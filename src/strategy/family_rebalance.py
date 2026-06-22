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
