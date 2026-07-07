# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D1 fill-up "re-decide held exposure". The same-token "never increase
#   exposure" invariant in src/strategy/family_exclusive_dedup.py guards the
#   2026-06-16 double-rest defect (a family double-resting a SECOND concurrent
#   entry). The consult's SAFE lift relaxes that invariant ONLY for a RESIDUAL
#   resize of the EXISTING held same-token position — never a second full entry.
"""Family-rebalance (fill-up) admission — the critical safety primitive for D1.

``decide_fill_up`` is the pure money-path predicate. It answers: given a held
position whose belief has STRENGTHENED, may we add to it, and by how much?

The single load-bearing safety property: the submitted amount is the RESIDUAL to
the fresh target, NOT a fresh full-target stake —

    delta_entry_usd = target_total_exposure_usd
                      - current_live_exposure_usd
                      - same_token_pending_entry_usd

submit ONLY if delta_entry_usd clears the venue minimum. The final live stake
kernel emits a family-TOTAL single-leg Kelly stake (not a residual), so fractional
Kelly does NOT by itself prevent a same-token re-entry from submitting another full
target — this residualizer MUST run before any order is emitted (consult
[HIGH] fill-up sizing). Fill-up also fails CLOSED on any unowned pending/unknown
entry in the family (the double-submit hazard) and is gated on a same-token+bin+
direction selection with a certified entry q_lcb that the fresh q_lcb exceeds.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from typing import Optional

_LEASE_SCHEMA_VERSION = 1
_TERMINAL_LEASE_STATUSES = frozenset({"COMPLETE", "ABORTED", "EXIT_ONLY_COMPLETE"})


@dataclass(frozen=True)
class FillUpDecision:
    """Whether a held same-token position may be topped up, and by how much."""

    allow: bool
    delta_entry_usd: float
    reason: str


@dataclass(frozen=True)
class ShiftBinDecision:
    """The close-before-open verdict for a sibling-different-bin redecision.

    ``phase`` is the state-machine verdict, NOT a single submit/no-submit boolean —
    shift-bin is inherently multi-cycle (the old-leg exit on cycle N, the counter-
    entry on a later cycle once closure is proven):

      - "NOT_SHIFT_BIN": not a sibling shift (not a redecision, no held exposure,
        same token, or same bin = fill-up / fresh-entry territory). Leave the entry
        path untouched.
      - "BLOCKED": an unowned pending/unknown/partial family command exists — fail
        closed. NO exit, NO entry (the 2026-06-16 double-rest hazard).
      - "EXIT_OLD_LEG": the old held leg still carries live exposure (>= the
        dust/min-order floor). Submit the reduce-only exit for the OLD leg; emit NO
        counter-entry this cycle.
      - "ENTER_NEW_BIN": the old leg residual is proven ZERO or below dust/min-order.
        The counter-entry is admitted (the reactor's own fresh recompute on current
        books decides the final stake — close-before-open is satisfied).
    """

    phase: str
    allow_entry: bool
    reason: str


@dataclass(frozen=True)
class ActiveRebalanceLease:
    """One active family-rebalance lease read from durable state."""

    intent_id: str
    event_id: str
    family_key: str
    operation: str
    status: str
    held_position_id: str
    held_token_id: str
    held_bin_id: str
    selected_token_id: str
    selected_bin_id: str


def decide_shift_bin(
    *,
    is_redecision_event: bool,
    selected_token_id: str,
    selected_bin_id: str,
    selected_direction: str,
    held_token_id: str | None,
    held_bin_id: str | None,
    held_position_id: str | None,
    old_leg_residual_usd: float,
    has_unowned_pending_or_unknown_entry: bool,
    old_leg_dust_floor_usd: float = 0.0,
    old_leg_q_current_lcb: float | None = None,
    old_leg_q_entry_lcb: float | None = None,
    shift_belief_weakening_floor: float = 0.0,
) -> ShiftBinDecision:
    """Pure close-before-open predicate for a sibling-different-bin redecision.

    Mirrors ``decide_fill_up``'s fail-closed posture. The ordering is load-bearing:

      1. NOT_SHIFT_BIN  — not a redecision / no held exposure / SAME token+bin
         (that is fill-up or fresh entry, never a sibling shift).
      2. BLOCKED        — ANY unowned pending/unknown/partial family command. Fail
         closed BEFORE any exit/entry decision (dominates the entry path even when
         the old leg looks closed).
      3. NOT_SHIFT_BIN (VALUE/BELIEF GATE) — the old leg still has live exposure but
         its belief has NOT genuinely weakened relative to its entry certification
         (mirrors ``decide_fill_up``'s strengthening check, inverted). A fresh
         redecision naming a different sibling bin is not, by itself, grounds to
         dump a still-strongly-believed leg — HOLD it instead (no churn). Fails
         CLOSED (HOLD) when either belief is unavailable.
      4. EXIT_OLD_LEG   — the old held leg still has live exposure at/above the dust
         floor AND its belief has genuinely weakened. Close it FIRST; no
         counter-entry this cycle.
      5. ENTER_NEW_BIN  — the old leg residual is proven ZERO or below the dust/min-
         order floor. The counter-entry may proceed (the reactor's fresh selection on
         current books is the recompute that decides the actual stake). Not a churn
         sell, so the belief gate does not apply here.

    The dust floor models "economically closed" (a sub-min-order remainder that can
    never be sold and is not live tradable exposure). At/above the floor is treated
    as still-live exposure (exit first) — the conservative direction.
    """
    deny = lambda phase, reason: ShiftBinDecision(phase=phase, allow_entry=False, reason=reason)

    if not is_redecision_event:
        return deny("NOT_SHIFT_BIN", "NOT_REDECISION_EVENT")
    if not held_token_id or not held_position_id:
        return deny("NOT_SHIFT_BIN", "NO_HELD_EXPOSURE")  # fresh entry, not a shift
    # SAME token+bin is fill-up territory (handled by decide_fill_up), not a sibling
    # shift to a DIFFERENT bin.
    if selected_token_id == held_token_id or selected_bin_id == held_bin_id:
        return deny("NOT_SHIFT_BIN", "SAME_TOKEN_OR_BIN_NOT_SIBLING_SHIFT")
    # Fail closed on the double-submit / over-exposure hazard BEFORE deciding exit vs
    # entry — an ambiguous family must never be acted on.
    if has_unowned_pending_or_unknown_entry:
        return deny("BLOCKED", "SHIFT_ABORT_BLOCKING_EXPOSURE")
    # Close-before-open: the old leg must be proven zero/dust before any counter-entry.
    if float(old_leg_residual_usd) >= max(float(old_leg_dust_floor_usd), 0.0) and (
        float(old_leg_residual_usd) > 0.0
    ):
        # VALUE/BELIEF GATE: never churn-sell a still-strongly-believed leg just
        # because a fresh redecision named a different sibling bin. Only a
        # genuinely WEAKENED old-leg belief may exit it. Fail closed (HOLD) when
        # either belief is unavailable — matches decide_fill_up's ENTRY_Q_LCB_MISSING
        # fail-closed posture.
        if old_leg_q_entry_lcb is None or old_leg_q_current_lcb is None:
            return deny("NOT_SHIFT_BIN", "SHIFT_OLD_LEG_BELIEF_UNKNOWN")
        if old_leg_q_current_lcb >= old_leg_q_entry_lcb - float(shift_belief_weakening_floor):
            return deny("NOT_SHIFT_BIN", "SHIFT_OLD_LEG_BELIEF_NOT_WEAKENED")
        return deny("EXIT_OLD_LEG", "SHIFT_EXIT_OLD_LEG_RESIDUAL_LIVE")
    return ShiftBinDecision(
        phase="ENTER_NEW_BIN",
        allow_entry=True,
        reason="SHIFT_OLD_LEG_CLOSED_ENTER_NEW_BIN",
    )


def decide_fill_up(
    *,
    is_redecision_event: bool,
    selected_token_id: str,
    selected_bin_id: str,
    selected_direction: str,
    held_token_id: str | None,
    held_bin_id: str | None,
    held_direction: str | None,
    q_current_lcb: float | None,
    q_entry_lcb: float | None,
    target_total_exposure_usd: float,
    current_live_exposure_usd: float,
    same_token_pending_entry_usd: float,
    venue_min_increment_usd: float,
    has_unowned_pending_or_unknown_entry: bool,
    q_strengthening_floor: float = 0.0,
) -> FillUpDecision:
    """Decide whether to fill up (top up) an existing same-token held position.

    Returns a residual ``delta_entry_usd`` to submit only when ALL hold:
    redecision event; a single held same-token+bin+direction exposure exists; no
    unowned pending/unknown entry in the family; the held side carries a certified
    entry q_lcb that the fresh q_lcb exceeds (by an optional hysteresis floor); and
    the residual to the fresh target clears the venue minimum. Fails CLOSED.
    """

    deny = lambda reason: FillUpDecision(allow=False, delta_entry_usd=0.0, reason=reason)

    if not is_redecision_event:
        return deny("NOT_REDECISION_EVENT")
    if not held_token_id:
        return deny("NO_HELD_EXPOSURE")  # fresh entry, not a fill-up
    # Fail closed on the double-submit hazard.
    if has_unowned_pending_or_unknown_entry:
        return deny("UNOWNED_PENDING_OR_UNKNOWN_ENTRY")
    # A different bin/token/direction is SHIFT-BIN, not fill-up.
    if (
        selected_token_id != held_token_id
        or selected_bin_id != held_bin_id
        or selected_direction != held_direction
    ):
        return deny("NOT_SAME_TOKEN_SIBLING_IS_SHIFT_BIN")
    # v1: do not fill-up a held position lacking a certified entry q_lcb authority.
    if q_entry_lcb is None or q_current_lcb is None:
        return deny("ENTRY_Q_LCB_MISSING")
    if q_current_lcb <= q_entry_lcb + float(q_strengthening_floor):
        return deny("BELIEF_NOT_STRENGTHENED")

    # THE safety lift: residual to the fresh target, never a fresh full stake.
    delta_entry_usd = (
        float(target_total_exposure_usd)
        - float(current_live_exposure_usd)
        - float(same_token_pending_entry_usd)
    )
    if delta_entry_usd <= 0.0:
        return FillUpDecision(False, delta_entry_usd, "NO_RESIDUAL_AT_OR_OVER_TARGET")
    if delta_entry_usd < float(venue_min_increment_usd):
        return FillUpDecision(False, delta_entry_usd, "RESIDUAL_BELOW_VENUE_MIN")

    return FillUpDecision(
        allow=True,
        delta_entry_usd=delta_entry_usd,
        reason="FILL_UP_RESIDUAL",
    )


# ---------------------------------------------------------------------------
# Family-rebalance LEASE manager — the concurrency guard for D1/D2.
# ---------------------------------------------------------------------------
#
# The lease makes the eventual money-path wiring safe against the duplicate-EDLI /
# SUBMIT_UNKNOWN double-submit race (the 2026-06-16 double-rest class): a family may
# have AT MOST ONE active rebalance. acquire is atomic — a second concurrent acquire
# on the same active family violates the partial-unique index and raises
# IntegrityError, which is caught here and returned as None (caller MUST no-op, never
# emit a second order). Released only on a terminal status.


def acquire_rebalance_lease(
    conn: sqlite3.Connection,
    *,
    family_key: str,
    operation: str,
    now_iso: str,
    held_position_id: Optional[str] = None,
    held_token_id: Optional[str] = None,
    held_bin_id: Optional[str] = None,
    selected_token_id: Optional[str] = None,
    selected_bin_id: Optional[str] = None,
    event_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    q_entry_lcb: Optional[float] = None,
    q_current_lcb: Optional[float] = None,
    target_total_exposure_usd: Optional[float] = None,
    current_exposure_usd: Optional[float] = None,
    pending_exposure_usd: Optional[float] = None,
    delta_entry_usd: Optional[float] = None,
) -> Optional[str]:
    """Atomically acquire the rebalance lease for a family. Returns the intent_id,
    or None when an ACTIVE lease already holds the family (the partial-unique index
    collision — caller MUST no-op, never emit a second order)."""

    _release_stale_planned_fill_up_without_command(
        conn,
        family_key=family_key,
        now_iso=now_iso,
    )
    _release_shift_unknown_without_durable_command(
        conn,
        family_key=family_key,
        now_iso=now_iso,
    )
    intent_id = str(uuid.uuid4())
    try:
        conn.execute(
            """
            INSERT INTO family_rebalance_intents (
                intent_id, event_id, family_key, operation, held_position_id,
                held_token_id, held_bin_id, selected_token_id, selected_bin_id,
                q_entry_lcb, q_current_lcb, target_total_exposure_usd,
                current_exposure_usd, pending_exposure_usd, delta_entry_usd,
                status, generation, idempotency_key, created_at, updated_at,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED', 1, ?, ?, ?, ?)
            """,
            (
                intent_id, event_id, family_key, operation, held_position_id,
                held_token_id, held_bin_id, selected_token_id, selected_bin_id,
                q_entry_lcb, q_current_lcb, target_total_exposure_usd,
                current_exposure_usd, pending_exposure_usd, delta_entry_usd,
                idempotency_key, now_iso, now_iso, _LEASE_SCHEMA_VERSION,
            ),
        )
    except sqlite3.IntegrityError:
        # An ACTIVE lease already holds this family — fail closed (no second order).
        return None
    return intent_id


def active_rebalance_lease_for_family(
    conn: sqlite3.Connection,
    *,
    family_key: str,
    operation: str | None = None,
) -> ActiveRebalanceLease | None:
    """Return the newest active lease for a family.

    Re-decision is multi-step: a later event may need to continue an existing
    SHIFT_BIN lease instead of trying to acquire a fresh FILL_UP lease and
    deadlocking against itself.
    """

    params: list[object] = [family_key, *sorted(_TERMINAL_LEASE_STATUSES)]
    operation_sql = ""
    if operation is not None:
        operation_sql = " AND operation = ?"
        params.append(str(operation))
    row = conn.execute(
        f"""
        SELECT intent_id, COALESCE(event_id, '') AS event_id, family_key, operation,
               status, COALESCE(held_position_id, '') AS held_position_id,
               COALESCE(held_token_id, '') AS held_token_id,
               COALESCE(held_bin_id, '') AS held_bin_id,
               COALESCE(selected_token_id, '') AS selected_token_id,
               COALESCE(selected_bin_id, '') AS selected_bin_id
          FROM family_rebalance_intents
         WHERE family_key = ?
           AND status NOT IN ({", ".join("?" for _ in _TERMINAL_LEASE_STATUSES)})
           {operation_sql}
         ORDER BY updated_at DESC, created_at DESC
         LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if row is None:
        return None

    def get(key: str, idx: int) -> object:
        try:
            return row[key]
        except (TypeError, IndexError, KeyError):
            return row[idx]

    return ActiveRebalanceLease(
        intent_id=str(get("intent_id", 0)),
        event_id=str(get("event_id", 1)),
        family_key=str(get("family_key", 2)),
        operation=str(get("operation", 3)),
        status=str(get("status", 4)),
        held_position_id=str(get("held_position_id", 5)),
        held_token_id=str(get("held_token_id", 6)),
        held_bin_id=str(get("held_bin_id", 7)),
        selected_token_id=str(get("selected_token_id", 8)),
        selected_bin_id=str(get("selected_bin_id", 9)),
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _shift_exit_command_exists(conn: sqlite3.Connection, held_position_id: str | None) -> bool | None:
    """Return None when command truth is unavailable, else whether a command exists."""

    if not str(held_position_id or "").strip():
        return False
    try:
        if not _table_exists(conn, "venue_commands"):
            return None
        row = conn.execute(
            """
            SELECT 1
              FROM venue_commands
             WHERE decision_id = ?
               AND intent_kind = 'EXIT'
             LIMIT 1
            """,
            (f"shift_bin_exit:{str(held_position_id).strip()}",),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return None


def _release_shift_unknown_without_durable_command(
    conn: sqlite3.Connection,
    *,
    family_key: str,
    now_iso: str,
) -> int:
    """Release stale SHIFT_BIN EXIT_UNKNOWN leases only when command truth proves no side effect."""

    try:
        rows = conn.execute(
            """
            SELECT intent_id, held_position_id
              FROM family_rebalance_intents
             WHERE family_key = ?
               AND operation = 'SHIFT_BIN'
               AND status = 'EXIT_UNKNOWN'
               AND COALESCE(old_exit_command_id, '') = ''
             ORDER BY updated_at DESC
            """,
            (family_key,),
        ).fetchall()
    except sqlite3.Error:
        return 0
    released = 0
    for row in rows:
        intent_id = row["intent_id"] if isinstance(row, sqlite3.Row) else row[0]
        held_position_id = row["held_position_id"] if isinstance(row, sqlite3.Row) else row[1]
        command_exists = _shift_exit_command_exists(conn, held_position_id)
        if command_exists is not False:
            continue
        advance_rebalance_lease(
            conn,
            str(intent_id),
            status="ABORTED",
            now_iso=now_iso,
            abort_reason="SHIFT_BIN_EXIT_UNKNOWN_NO_DURABLE_COMMAND_RECOVERED",
        )
        released += 1
    return released


def _release_stale_planned_fill_up_without_command(
    conn: sqlite3.Connection,
    *,
    family_key: str,
    now_iso: str,
    min_age_seconds: int = 1200,
) -> int:
    """Release stale pre-venue FILL_UP leases that never created a command.

    A FILL_UP lease in PLANNED with no ``new_entry_command_id`` is pre-submit
    state. If it survives beyond the build/retry window, it is not protecting a
    venue side effect; it is blocking future redecision for that family.
    """

    try:
        cur = conn.execute(
            """
            UPDATE family_rebalance_intents
               SET status = 'ABORTED',
                   updated_at = ?,
                   abort_reason = COALESCE(
                       abort_reason,
                       'FILL_UP_PLANNED_STALE_NO_DURABLE_COMMAND_RECOVERED'
                   )
             WHERE family_key = ?
               AND operation = 'FILL_UP'
               AND status = 'PLANNED'
               AND COALESCE(new_entry_command_id, '') = ''
               AND datetime(updated_at) <= datetime(?, ?)
            """,
            (
                now_iso,
                family_key,
                now_iso,
                f"-{int(min_age_seconds)} seconds",
            ),
        )
        return int(cur.rowcount or 0)
    except sqlite3.Error:
        return 0


def advance_rebalance_lease(
    conn: sqlite3.Connection,
    intent_id: str,
    *,
    status: str,
    now_iso: str,
    abort_reason: Optional[str] = None,
    new_entry_command_id: Optional[str] = None,
    old_exit_command_id: Optional[str] = None,
) -> None:
    """Transition a lease to a new status (intermediate or terminal). Terminal
    statuses (COMPLETE / ABORTED / EXIT_ONLY_COMPLETE) release the family so the next
    legitimate rebalance can acquire (the partial-unique index only covers active)."""

    conn.execute(
        """
        UPDATE family_rebalance_intents
        SET status = ?, updated_at = ?,
            abort_reason = COALESCE(?, abort_reason),
            new_entry_command_id = COALESCE(?, new_entry_command_id),
            old_exit_command_id = COALESCE(?, old_exit_command_id)
        WHERE intent_id = ?
        """,
        (status, now_iso, abort_reason, new_entry_command_id, old_exit_command_id, intent_id),
    )


def active_lease_for_family(
    conn: sqlite3.Connection, family_key: str
) -> Optional[str]:
    """Return the intent_id of the family's active lease, or None."""
    row = conn.execute(
        f"""
        SELECT intent_id FROM family_rebalance_intents
        WHERE family_key = ? AND status NOT IN (
            {", ".join("?" for _ in _TERMINAL_LEASE_STATUSES)}
        )
        ORDER BY created_at DESC LIMIT 1
        """,
        (family_key, *sorted(_TERMINAL_LEASE_STATUSES)),
    ).fetchone()
    return None if row is None else str(row[0])
