# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D2 shift-bin "close-before-open". The ADDITIVE orchestration that
#   connects the committed primitives (decide_shift_bin + the family-rebalance lease,
#   operation=SHIFT_BIN) to the live money path in
#   src/engine/event_reactor_adapter.py. It owns NO new sizing math, NO new exposure
#   calc, NO new gate — it composes the primitives and reads canonical position
#   truth. The single load-bearing safety property it enforces at the call site:
#   NO new-bin entry while the OLD leg has live/partial/unknown exposure. The lease
#   carries the close-before-open state across reactor cycles (EXIT_SUBMITTED on the
#   cycle that detects the shift → the counter-entry on a LATER cycle once the old
#   residual is proven zero/dust).
"""Shift-bin wiring — orchestration helpers for D2 close-before-open sibling shift.

The live decision body (``_build_event_bound_no_submit_receipt_core``) calls this in
ONE fully-gated block, entered only when ``allow_same_family_monitor_owned`` is true
(an EDLI_REDECISION_PENDING event) AND the freshly-selected winning candidate is a
SIBLING (different token AND different bin, same family) of an existing held position.
For EVERY other event/candidate the block is a complete no-op and the fresh-entry +
D1 fill-up paths run byte-identical.

Flow at the call site (the multi-cycle state machine, driven by the lease + truth):

  CYCLE N (sibling detected, old leg live):
    1. ``read_held_sibling_exposure`` — is there a held DIFFERENT-bin position in the
       fresh selection's family? Returns the OLD-leg truth (position_id, token, bin)
       or None (not a shift → leave the entry/fill-up paths untouched).
    2. ``read_old_leg_residual_usd`` — the OLD leg's current live committed USD from
       canonical position_current (chain cost basis preferred). 0.0 == proven closed.
    3. ``plan_shift_bin`` — acquire the SHIFT_BIN lease (None on a concurrent
       same-family collision → ABORT, no order) then run ``decide_shift_bin``:
         - EXIT_OLD_LEG: lease advanced EXIT_SUBMITTED, the old-leg identity recorded;
           the reactor submits the reduce-only exit for the OLD token via the existing
           exit path and emits NO counter-entry this cycle.
         - ABORT: blocking unowned exposure (lease ABORTED) OR a concurrent lease holds
           the family (never acquired). NO exit, NO order.
         - NOOP: not a shift-bin.
  LATER CYCLE (a fresh redecision after the old leg closed):
         - ENTER_NEW_BIN: the OLD residual is proven zero/dust → admit the counter-
           entry under the SAME lease (advanced ENTRY_SUBMITTED → COMPLETE on ack).
           The reactor's own fresh selection on current books IS the recompute.

INV-37: the lease table (family_rebalance_intents) lives in world.db. The reactor's
``trade_conn`` has world ATTACHed, so the bare table name resolves on that single
connection — no independent connection.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Optional

from src.strategy.family_rebalance import (
    ShiftBinDecision,
    acquire_rebalance_lease,
    advance_rebalance_lease,
    decide_shift_bin,
)

# Reuse the EXACT same live-committed/in-flight phase set + schema helpers the D1
# fill-up wiring uses (no parallel exposure-phase truth).
from src.strategy.fill_up_wiring import (
    _FILL_UP_BLOCKING_PHASES as _BLOCKING_PHASES,
    _columns,
    _norm_metric,
    _table_exists,
)


@dataclass(frozen=True)
class HeldSiblingExposure:
    """The OLD held leg (same family, DIFFERENT bin/token than the fresh selection)."""

    position_id: str
    token_id: str
    bin_label: str
    direction: str
    current_live_usd: float


@dataclass(frozen=True)
class ShiftBinPlan:
    """The orchestration outcome the live decision body acts on.

    kind:
      - "EXIT_OLD_LEG": submit the reduce-only exit for ``old_token_id`` via the
        existing exit path; emit NO counter-entry. The lease (``lease_intent_id``) is
        held in EXIT_SUBMITTED and MUST be advanced terminally on the exit outcome.
      - "ENTER_NEW_BIN": the old leg is proven closed; admit the counter-entry. The
        lease is held in ENTRY_SUBMITTED and MUST reach COMPLETE on ack.
      - "ABORT": emit NO order (no exit, no entry). ``lease_intent_id`` is set when a
        lease was acquired then advanced ABORTED (blocking exposure); None when the
        family was already leased (concurrent rebalance).
      - "NOOP": not a shift-bin; leave the entry/fill-up paths untouched. No lease.
    """

    kind: Literal["EXIT_OLD_LEG", "ENTER_NEW_BIN", "ABORT", "NOOP"]
    allow_entry: bool = False
    lease_intent_id: Optional[str] = None
    old_position_id: Optional[str] = None
    old_token_id: Optional[str] = None
    reason: str = ""


def read_held_sibling_exposure(
    conn: Optional[sqlite3.Connection],
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    selected_token_id: str,
    selected_bin_label: str,
) -> Optional[HeldSiblingExposure]:
    """Return the OLD held leg for a sibling shift, or None.

    A held position in the SAME (city, target_date, metric) family whose token AND
    bin_label DIFFER from the fresh selection is the OLD leg to close. Returns None
    when the fresh selection is the SAME token (that is fill-up, not a shift) or when
    no different-bin family position is held. Reads canonical ``position_current``,
    restricted to live/in-flight phases with positive committed cost. Fails CLOSED on
    a malformed schema (returns None → the caller leaves the entry path untouched; a
    missed shift degrades to the existing fresh-entry selection, never to an unsafe
    double-open because the family-exclusive admission still gates a true fresh entry).
    """
    sel_token = str(selected_token_id or "").strip()
    sel_bin = str(selected_bin_label or "").strip()
    if conn is None or not sel_token:
        return None
    try:
        if not _table_exists(conn, "position_current"):
            return None
        cols = _columns(conn, "position_current")
    except sqlite3.Error:
        return None
    if not {"city", "target_date", "token_id"}.issubset(cols):
        return None
    metric_col = (
        "temperature_metric" if "temperature_metric" in cols
        else "metric" if "metric" in cols
        else ""
    )
    if not metric_col:
        return None

    phase_sql = (
        "phase IN ({})".format(",".join("?" for _ in _BLOCKING_PHASES))
        if "phase" in cols
        else "1=1"
    )
    cost_terms = [c for c in ("chain_cost_basis_usd", "cost_basis_usd", "size_usd") if c in cols]
    if not cost_terms:
        return None
    positive_sql = " AND (" + " OR ".join(f"COALESCE({c},0) > 0" for c in cost_terms) + ")"

    select_cols = []
    for name in ("position_id", "token_id", "bin_label", "direction",
                 "chain_cost_basis_usd", "cost_basis_usd", "size_usd", metric_col):
        select_cols.append(name if name in cols else f"NULL AS {name}")
    order_sql = "ORDER BY updated_at DESC" if "updated_at" in cols else ""
    sql = (
        f"SELECT {', '.join(select_cols)} FROM position_current "
        f"WHERE {phase_sql} AND city = ? AND target_date = ?{positive_sql} {order_sql}"
    )
    params: list[object] = []
    if "phase" in cols:
        params.extend(sorted(_BLOCKING_PHASES))
    params.extend([str(city), str(target_date)])
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.Error:
        return None

    metric_norm = _norm_metric(temperature_metric)
    for row in rows:
        def _g(name: str):
            try:
                return row[name] if isinstance(row, sqlite3.Row) else None
            except (IndexError, KeyError):
                return None

        if _norm_metric(_g(metric_col)) != metric_norm:
            continue
        tok = str(_g("token_id") or "")
        bin_label = str(_g("bin_label") or "")
        # Same token == fill-up territory, not a sibling shift.
        if tok and tok == sel_token:
            continue
        # Same bin (the two SIDES of one bin) is not a shift to a DIFFERENT bin.
        if sel_bin and bin_label and bin_label == sel_bin:
            continue
        current_live = 0.0
        for value in (_g("chain_cost_basis_usd"), _g("cost_basis_usd"), _g("size_usd")):
            try:
                v = float(value) if value is not None else 0.0
            except (TypeError, ValueError):
                v = 0.0
            if v > 0.0:
                current_live = v
                break
        return HeldSiblingExposure(
            position_id=str(_g("position_id") or ""),
            token_id=tok,
            bin_label=bin_label,
            direction=str(_g("direction") or ""),
            current_live_usd=current_live,
        )
    return None


def read_old_leg_residual_usd(
    conn: Optional[sqlite3.Connection],
    *,
    token_id: str,
) -> float:
    """Return the OLD leg's current live committed USD, or 0.0 when no longer held.

    The CLOSE proof for close-before-open: when the old leg has been exited/voided to
    zero (or dust below min-order), no live ``position_current`` row remains for the
    old token, so this returns 0.0 (== proven closed from canonical truth). A row with
    positive committed cost in a blocking phase returns that USD (still live → exit
    first). Chain cost basis is preferred over the projected cost basis (chain truth).
    Fails CLOSED conservatively: a read/schema error returns +inf so the caller treats
    the old leg as STILL LIVE (exit first, never falsely enter) rather than 0.
    """
    token = str(token_id or "").strip()
    if conn is None or not token:
        return float("inf")  # ambiguous truth → treat as live, never falsely enter
    try:
        if not _table_exists(conn, "position_current"):
            return float("inf")
        cols = _columns(conn, "position_current")
    except sqlite3.Error:
        return float("inf")
    if "token_id" not in cols:
        return float("inf")
    token_cols = [c for c in ("token_id", "no_token_id") if c in cols]
    phase_sql = (
        "phase IN ({})".format(",".join("?" for _ in _BLOCKING_PHASES))
        if "phase" in cols
        else "1=1"
    )
    token_sql = " OR ".join(f"NULLIF({c}, '') = ?" for c in token_cols)
    cost_terms = [c for c in ("chain_cost_basis_usd", "cost_basis_usd", "size_usd") if c in cols]
    if not cost_terms:
        return float("inf")
    positive_sql = " AND (" + " OR ".join(f"COALESCE({c},0) > 0" for c in cost_terms) + ")"
    select_cols = [c if c in cols else f"NULL AS {c}"
                   for c in ("chain_cost_basis_usd", "cost_basis_usd", "size_usd")]
    order_sql = "ORDER BY updated_at DESC" if "updated_at" in cols else ""
    sql = (
        f"SELECT {', '.join(select_cols)} FROM position_current "
        f"WHERE {phase_sql} AND ({token_sql}){positive_sql} {order_sql} LIMIT 1"
    )
    params: list[object] = []
    if "phase" in cols:
        params.extend(sorted(_BLOCKING_PHASES))
    params.extend(token for _ in token_cols)
    try:
        row = conn.execute(sql, tuple(params)).fetchone()
    except sqlite3.Error:
        return float("inf")
    if row is None:
        return 0.0  # no live row for the old token → proven closed

    def _g(name: str):
        try:
            return row[name] if isinstance(row, sqlite3.Row) else None
        except (IndexError, KeyError):
            return None

    for value in (_g("chain_cost_basis_usd"), _g("cost_basis_usd"), _g("size_usd")):
        try:
            v = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            v = 0.0
        if v > 0.0:
            return v
    return 0.0


def plan_shift_bin(
    conn: sqlite3.Connection,
    *,
    is_redecision_event: bool,
    family_key: str,
    event_id: Optional[str],
    selected_token_id: str,
    selected_bin_id: str,
    selected_direction: str,
    held: Optional[HeldSiblingExposure],
    old_leg_residual_usd: float,
    has_unowned_pending_or_unknown_entry: bool,
    now_iso: str,
    old_leg_dust_floor_usd: float = 0.0,
) -> ShiftBinPlan:
    """Orchestrate the SHIFT_BIN lease acquire + ``decide_shift_bin``.

    Returns NOOP (leave the entry/fill-up paths untouched, no lease) when this is not
    a sibling shift. Otherwise acquires the family-rebalance lease FIRST (the
    concurrency guard): a concurrent same-family lease => acquire returns None => ABORT
    with no order and no second lease. With the lease held, runs ``decide_shift_bin``:
    EXIT_OLD_LEG advances the lease EXIT_SUBMITTED and returns the old-leg identity;
    ENTER_NEW_BIN advances ENTRY_SUBMITTED and admits the counter-entry; BLOCKED
    advances the lease ABORTED and returns ABORT (no exit, no order).
    """
    # Not a shift-bin: leave the fresh-entry + D1 fill-up paths completely untouched.
    if not is_redecision_event or held is None or not str(held.position_id or ""):
        return ShiftBinPlan(kind="NOOP")

    # Lease FIRST so a concurrent same-family redecision cannot race past us into a
    # second exit/entry (the 2026-06-16 double-rest class). None == family leased.
    lease_intent_id = acquire_rebalance_lease(
        conn,
        family_key=family_key,
        operation="SHIFT_BIN",
        now_iso=now_iso,
        held_position_id=held.position_id,
        held_token_id=held.token_id,
        held_bin_id=held.bin_label,
        selected_token_id=selected_token_id,
        selected_bin_id=selected_bin_id,
        event_id=event_id,
        current_exposure_usd=float(held.current_live_usd),
    )
    if lease_intent_id is None:
        return ShiftBinPlan(kind="ABORT", reason="SHIFT_BIN_CONCURRENT_FAMILY_LEASE")

    decision: ShiftBinDecision = decide_shift_bin(
        is_redecision_event=is_redecision_event,
        selected_token_id=selected_token_id,
        selected_bin_id=selected_bin_id,
        selected_direction=selected_direction,
        held_token_id=held.token_id,
        held_bin_id=held.bin_label,
        held_position_id=held.position_id,
        old_leg_residual_usd=float(old_leg_residual_usd),
        has_unowned_pending_or_unknown_entry=bool(has_unowned_pending_or_unknown_entry),
        old_leg_dust_floor_usd=float(old_leg_dust_floor_usd),
    )

    if decision.phase == "BLOCKED":
        advance_rebalance_lease(
            conn, lease_intent_id, status="ABORTED", now_iso=now_iso,
            abort_reason=decision.reason,
        )
        return ShiftBinPlan(kind="ABORT", lease_intent_id=lease_intent_id, reason=decision.reason)

    if decision.phase == "NOT_SHIFT_BIN":
        # Defensive: decide_shift_bin disagreed with read_held_sibling_exposure (e.g.
        # a same-token row slipped through). Release the lease, no order.
        advance_rebalance_lease(
            conn, lease_intent_id, status="ABORTED", now_iso=now_iso,
            abort_reason=decision.reason,
        )
        return ShiftBinPlan(kind="ABORT", lease_intent_id=lease_intent_id, reason=decision.reason)

    if decision.phase == "EXIT_OLD_LEG":
        advance_rebalance_lease(
            conn, lease_intent_id, status="EXIT_SUBMITTED", now_iso=now_iso,
        )
        return ShiftBinPlan(
            kind="EXIT_OLD_LEG",
            allow_entry=False,
            lease_intent_id=lease_intent_id,
            old_position_id=held.position_id,
            old_token_id=held.token_id,
            reason=decision.reason,
        )

    # ENTER_NEW_BIN: the old leg is proven closed; admit the counter-entry.
    advance_rebalance_lease(
        conn, lease_intent_id, status="ENTRY_SUBMITTED", now_iso=now_iso,
    )
    return ShiftBinPlan(
        kind="ENTER_NEW_BIN",
        allow_entry=True,
        lease_intent_id=lease_intent_id,
        old_position_id=held.position_id,
        old_token_id=held.token_id,
        reason=decision.reason,
    )


def record_exit_submitted(
    conn: sqlite3.Connection,
    intent_id: Optional[str],
    *,
    now_iso: str,
    old_exit_command_id: Optional[str] = None,
    status: str = "EXIT_SUBMITTED",
    reason: Optional[str] = None,
) -> None:
    """Record the old-leg exit command id on the lease and set the EXIT_* status.

    ``status`` is one of EXIT_SUBMITTED / EXIT_PARTIAL / EXIT_UNKNOWN — all keep the
    family LOCKED (no counter-entry) until the old residual is proven zero/dust.
    """
    if not intent_id:
        return
    advance_rebalance_lease(
        conn, intent_id, status=status, now_iso=now_iso,
        old_exit_command_id=old_exit_command_id,
        abort_reason=reason,
    )


def complete_shift_bin_lease(
    conn: sqlite3.Connection,
    intent_id: Optional[str],
    *,
    now_iso: str,
    new_entry_command_id: Optional[str] = None,
) -> None:
    """Advance the shift-bin lease to COMPLETE on the counter-entry submit ack."""
    if not intent_id:
        return
    advance_rebalance_lease(
        conn, intent_id, status="COMPLETE", now_iso=now_iso,
        new_entry_command_id=new_entry_command_id,
    )


def exit_only_complete(
    conn: sqlite3.Connection,
    intent_id: Optional[str],
    *,
    now_iso: str,
    reason: str,
) -> None:
    """End the rebalance EXIT_ONLY_COMPLETE: the old leg closed but the fresh recompute
    no longer selects the sibling. NOT a false exit (the exit was independently
    justified) — a market-moved / no-counter-entry outcome. Releases the family."""
    if not intent_id:
        return
    advance_rebalance_lease(
        conn, intent_id, status="EXIT_ONLY_COMPLETE", now_iso=now_iso,
        abort_reason=reason,
    )


def abort_shift_bin_lease(
    conn: sqlite3.Connection,
    intent_id: Optional[str],
    *,
    now_iso: str,
    reason: str,
) -> None:
    """Advance the shift-bin lease to ABORTED (release the family). Used on an exit
    venue boundary unknown, a pre-submit failure, or a presubmit-reread block."""
    if not intent_id:
        return
    advance_rebalance_lease(
        conn, intent_id, status="ABORTED", now_iso=now_iso, abort_reason=reason,
    )
