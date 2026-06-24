# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D1 fill-up "re-decide held exposure". This module is the ADDITIVE
#   orchestration that connects the committed safety primitives
#   (src/strategy/family_rebalance.decide_fill_up + the family-rebalance lease) to
#   the live money path in src/engine/event_reactor_adapter.py. It owns NO new
#   sizing math, NO new exposure calc, NO new gate — it composes the primitives and
#   reads canonical position truth. The single load-bearing safety property it
#   enforces at the call site is the RESIDUAL stake override: an approved fill-up
#   emits ONLY `delta_entry_usd`, never a second full Kelly stake.
"""Fill-up wiring — orchestration helpers for D1 same-token fill-up.

The live decision body (``_build_event_bound_no_submit_receipt_core``) calls this in
ONE fully-gated block, entered only when ``allow_same_family_monitor_owned`` is true
(an EDLI_REDECISION_PENDING event) AND the freshly-selected winning candidate is the
SAME token as an existing held position. For EVERY other event/candidate the block is
a complete no-op and the fresh-entry path runs byte-identical.

Flow at the call site:

  1. ``read_held_same_token_exposure`` — is the selected token already held? Returns
     the held position truth (position_id, bin/direction, entry q_lcb, current_live)
     or None (fresh entry → leave the entry path untouched).
  2. ``plan_fill_up`` — acquire the family-rebalance lease (None on a concurrent
     same-family collision → ABORT, no order) then run ``decide_fill_up``. Returns:
       - APPLY(residual_stake_usd, lease_intent_id): override the emitted stake to
         the residual and submit through the existing executor/pre-submit path.
       - ABORT: a lease was taken-then-aborted (predicate denied) OR a concurrent
         lease holds the family (never acquired). Emit NO order.
       - NOOP: not a fill-up (no held same token / not a redecision). Leave the
         entry path untouched; no lease taken.
  3. ``presubmit_reread_aborts`` — final pre-submit exposure reread: abort if a new
     unowned/unknown same-family entry appeared between admission and submit.
  4. ``complete_fill_up_lease`` / ``abort_fill_up_lease`` — terminal lease advance.

INV-37: the lease table (family_rebalance_intents) lives in world.db. The reactor's
``trade_conn`` has world ATTACHed (get_trade_connection_with_world_required), so the
bare table name resolves on that single connection — no independent connection.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Optional

from src.strategy.family_rebalance import (
    FillUpDecision,
    acquire_rebalance_lease,
    advance_rebalance_lease,
    decide_fill_up,
)

# The held-position phases that count as live committed/in-flight family exposure.
# Mirrors the reactor's _ENTRY_HELD_POSITION_BLOCKING_PHASES intent (open/pending/
# active/in-flight); a settled/closed/admin-closed row is NOT live exposure.
_FILL_UP_BLOCKING_PHASES: frozenset[str] = frozenset(
    {
        "",
        "open",
        "pending",
        "pending_entry",
        "pending_tracked",
        "active",
        "entered",
        "holding",
        "day0_window",
        "pending_exit",
        "acked",
        "live",
        "partial",
        "partially_filled",
        "filled",
        "submitted",
        "submit_unknown_side_effect",
        "unknown",
        "review_required",
    }
)


@dataclass(frozen=True)
class HeldSameTokenExposure:
    """Canonical held same-token position truth read for a fill-up decision."""

    position_id: str
    bin_label: str
    direction: str
    entry_q_lcb: Optional[float]
    current_live_usd: float


@dataclass(frozen=True)
class FillUpPlan:
    """The orchestration outcome the live decision body acts on.

    kind:
      - "APPLY": override the emitted stake to ``residual_stake_usd``; the lease is
        held (``lease_intent_id``) and MUST be advanced to a terminal status.
      - "ABORT": emit NO order. ``lease_intent_id`` is set when a lease was acquired
        then advanced ABORTED (predicate denied); None when the family was already
        leased (concurrent rebalance — never acquired a second lease).
      - "NOOP": not a fill-up; leave the entry path untouched. No lease taken.
    """

    kind: Literal["APPLY", "ABORT", "NOOP"]
    residual_stake_usd: Optional[float] = None
    lease_intent_id: Optional[str] = None
    reason: str = ""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    if row is not None:
        return True
    # position_current may live in an ATTACHed schema (world). Probe all schemas.
    try:
        for schema_row in conn.execute("PRAGMA database_list").fetchall():
            schema = schema_row[1] if not isinstance(schema_row, sqlite3.Row) else schema_row["name"]
            if schema in ("main", "temp"):
                continue
            probe = conn.execute(
                f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (name,),
            ).fetchone()
            if probe is not None:
                return True
    except sqlite3.Error:
        return False
    return False


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1] if not isinstance(row, sqlite3.Row) else row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def read_held_same_token_exposure(
    conn: Optional[sqlite3.Connection],
    *,
    token_id: str,
) -> Optional[HeldSameTokenExposure]:
    """Return the held same-token position truth, or None for a fresh-entry candidate.

    Reads ``position_current`` (canonical open-position truth), matching the SAME
    token the fresh selection chose, restricted to live/in-flight phases. The held
    entry q_lcb is the entry CI lower bound (``p_posterior - entry_ci_width/2``),
    the same authority ``Position.entry_ci`` carries. Current-live USD prefers the
    chain-observed cost basis over the projected cost basis.

    Returns None (the fresh-entry signal) when no such held row exists. Fails CLOSED
    on a malformed schema by returning None — the caller then treats the candidate as
    a fresh entry (the safe default: a fill-up is only ever ADDITIVELY admitted, so a
    missed fill-up degrades to the existing fresh-entry no-op selection, never to an
    over-size).
    """
    token = str(token_id or "").strip()
    if conn is None or not token:
        return None
    try:
        if not _table_exists(conn, "position_current"):
            return None
        cols = _columns(conn, "position_current")
    except sqlite3.Error:
        return None
    if "token_id" not in cols:
        return None

    token_cols = [c for c in ("token_id", "no_token_id") if c in cols]
    phase_sql = (
        "phase IN ({})".format(",".join("?" for _ in _FILL_UP_BLOCKING_PHASES))
        if "phase" in cols
        else "1=1"
    )
    token_sql = " OR ".join(f"NULLIF({c}, '') = ?" for c in token_cols)
    cost_terms = [c for c in ("chain_cost_basis_usd", "cost_basis_usd", "size_usd") if c in cols]
    if not cost_terms:
        return None
    positive_sql = " AND (" + " OR ".join(f"COALESCE({c},0) > 0" for c in cost_terms) + ")"

    select_cols = []
    for name in ("position_id", "bin_label", "direction", "p_posterior",
                 "entry_ci_width", "chain_cost_basis_usd", "cost_basis_usd", "size_usd"):
        select_cols.append(name if name in cols else f"NULL AS {name}")
    order_sql = "ORDER BY updated_at DESC" if "updated_at" in cols else ""
    sql = (
        f"SELECT {', '.join(select_cols)} FROM position_current "
        f"WHERE {phase_sql} AND ({token_sql}){positive_sql} {order_sql} LIMIT 1"
    )
    params: list[object] = []
    if "phase" in cols:
        params.extend(sorted(_FILL_UP_BLOCKING_PHASES))
    params.extend(token for _ in token_cols)
    try:
        row = conn.execute(sql, tuple(params)).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None

    def _g(name: str):
        try:
            return row[name] if isinstance(row, sqlite3.Row) else None
        except (IndexError, KeyError):
            return None

    p_posterior = _g("p_posterior")
    entry_ci_width = _g("entry_ci_width")
    entry_q_lcb: Optional[float]
    if p_posterior is None or entry_ci_width is None:
        entry_q_lcb = None
    else:
        try:
            entry_q_lcb = float(p_posterior) - float(entry_ci_width) / 2.0
        except (TypeError, ValueError):
            entry_q_lcb = None

    chain_cb = _g("chain_cost_basis_usd")
    cb = _g("cost_basis_usd")
    size = _g("size_usd")
    current_live = 0.0
    for value in (chain_cb, cb, size):
        try:
            v = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            v = 0.0
        if v > 0.0:
            current_live = v
            break

    return HeldSameTokenExposure(
        position_id=str(_g("position_id") or ""),
        bin_label=str(_g("bin_label") or ""),
        direction=str(_g("direction") or ""),
        entry_q_lcb=entry_q_lcb,
        current_live_usd=current_live,
    )


def plan_fill_up(
    conn: sqlite3.Connection,
    *,
    is_redecision_event: bool,
    family_key: str,
    event_id: Optional[str],
    selected_token_id: str,
    selected_bin_id: str,
    selected_direction: str,
    held: Optional[HeldSameTokenExposure],
    q_current_lcb: Optional[float],
    target_total_exposure_usd: float,
    same_token_pending_entry_usd: float,
    venue_min_increment_usd: float,
    now_iso: str,
    has_unowned_pending_or_unknown_entry: bool = False,
    q_strengthening_floor: float = 0.0,
) -> FillUpPlan:
    """Orchestrate the lease acquire + ``decide_fill_up`` for a same-token candidate.

    Returns NOOP (leave the entry path untouched, no lease) when this is not a
    fill-up (not a redecision, or no held same-token exposure). Otherwise acquires
    the family-rebalance lease FIRST (the concurrency guard): a concurrent same-family
    lease => acquire returns None => ABORT with no order and no second lease. With the
    lease held, runs ``decide_fill_up``; on allow returns APPLY(residual, lease); on
    deny advances the lease ABORTED and returns ABORT (no order).
    """
    # Not a fill-up: leave the fresh-entry path completely untouched.
    if not is_redecision_event or held is None or not str(held.position_id or ""):
        return FillUpPlan(kind="NOOP")

    # Lease FIRST so a concurrent same-family redecision cannot race past us into a
    # second order (the 2026-06-16 double-rest class). None == family already leased.
    lease_intent_id = acquire_rebalance_lease(
        conn,
        family_key=family_key,
        operation="FILL_UP",
        now_iso=now_iso,
        held_position_id=held.position_id,
        held_token_id=selected_token_id,
        held_bin_id=selected_bin_id,
        selected_token_id=selected_token_id,
        selected_bin_id=selected_bin_id,
        event_id=event_id,
        q_entry_lcb=held.entry_q_lcb,
        q_current_lcb=q_current_lcb,
        target_total_exposure_usd=float(target_total_exposure_usd),
        current_exposure_usd=float(held.current_live_usd),
        pending_exposure_usd=float(same_token_pending_entry_usd),
    )
    if lease_intent_id is None:
        return FillUpPlan(
            kind="ABORT",
            reason="FILL_UP_CONCURRENT_FAMILY_LEASE",
        )

    decision: FillUpDecision = decide_fill_up(
        is_redecision_event=is_redecision_event,
        selected_token_id=selected_token_id,
        selected_bin_id=selected_bin_id,
        selected_direction=selected_direction,
        held_token_id=selected_token_id,  # same-token by construction (held was matched on it)
        held_bin_id=selected_bin_id,
        held_direction=selected_direction,
        q_current_lcb=q_current_lcb,
        q_entry_lcb=held.entry_q_lcb,
        target_total_exposure_usd=float(target_total_exposure_usd),
        current_live_exposure_usd=float(held.current_live_usd),
        same_token_pending_entry_usd=float(same_token_pending_entry_usd),
        venue_min_increment_usd=float(venue_min_increment_usd),
        has_unowned_pending_or_unknown_entry=bool(has_unowned_pending_or_unknown_entry),
        q_strengthening_floor=float(q_strengthening_floor),
    )
    if not decision.allow:
        advance_rebalance_lease(
            conn,
            lease_intent_id,
            status="ABORTED",
            now_iso=now_iso,
            abort_reason=decision.reason,
        )
        return FillUpPlan(
            kind="ABORT",
            lease_intent_id=lease_intent_id,
            reason=decision.reason,
        )

    # Record the bound residual on the lease for audit, then APPLY.
    advance_rebalance_lease(
        conn,
        lease_intent_id,
        status="PLANNED",
        now_iso=now_iso,
    )
    conn.execute(
        "UPDATE family_rebalance_intents SET delta_entry_usd = ?, updated_at = ? WHERE intent_id = ?",
        (float(decision.delta_entry_usd), now_iso, lease_intent_id),
    )
    return FillUpPlan(
        kind="APPLY",
        residual_stake_usd=float(decision.delta_entry_usd),
        lease_intent_id=lease_intent_id,
        reason=decision.reason,
    )


def presubmit_reread_aborts(
    conn: Optional[sqlite3.Connection],
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    owned_position_id: str,
    owned_token_id: str,
) -> Optional[str]:
    """Final pre-submit family-exposure reread. Returns an abort reason when a NEW
    blocking/unowned same-family entry (a position the fill-up does not own) appeared
    between admission and submit; None when clean.

    The fill-up owns exactly the held same-token position it tops up. Any OTHER live
    same-family position that is not that owned position is a new/concurrent exposure
    that re-opens the over-exposure hazard, so the fill-up aborts (fail closed). Reads
    canonical ``position_current``; a schema/read failure fails CLOSED (returns an
    abort reason) — never silently proceed on ambiguous exposure truth.
    """
    if conn is None:
        return "PRESUBMIT_REREAD_NO_EXPOSURE_CONN"
    owned_pid = str(owned_position_id or "").strip()
    owned_tok = str(owned_token_id or "").strip()
    try:
        if not _table_exists(conn, "position_current"):
            # No position truth table to reread — fail closed.
            return "PRESUBMIT_REREAD_POSITION_TRUTH_UNAVAILABLE"
        cols = _columns(conn, "position_current")
    except sqlite3.Error as exc:
        return f"PRESUBMIT_REREAD_POSITION_TRUTH_UNAVAILABLE:{type(exc).__name__}"
    needed = {"city", "target_date", "position_id"}
    if not needed.issubset(cols):
        return "PRESUBMIT_REREAD_POSITION_TRUTH_SCHEMA_INCOMPLETE"
    metric_col = (
        "temperature_metric" if "temperature_metric" in cols
        else "metric" if "metric" in cols
        else ""
    )
    phase_sql = (
        "phase IN ({})".format(",".join("?" for _ in _FILL_UP_BLOCKING_PHASES))
        if "phase" in cols
        else "1=1"
    )
    cost_terms = [c for c in ("chain_cost_basis_usd", "cost_basis_usd", "size_usd") if c in cols]
    positive_sql = (
        " AND (" + " OR ".join(f"COALESCE({c},0) > 0" for c in cost_terms) + ")"
        if cost_terms else ""
    )
    select_cols = ["position_id"]
    select_cols.append("token_id" if "token_id" in cols else "NULL AS token_id")
    select_cols.append("bin_label" if "bin_label" in cols else "NULL AS bin_label")
    if metric_col:
        select_cols.append(f"{metric_col} AS metric")
    sql = (
        f"SELECT {', '.join(select_cols)} FROM position_current "
        f"WHERE {phase_sql} AND city = ? AND target_date = ?{positive_sql}"
    )
    params: list[object] = []
    if "phase" in cols:
        params.extend(sorted(_FILL_UP_BLOCKING_PHASES))
    params.extend([str(city), str(target_date)])
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.Error as exc:
        return f"PRESUBMIT_REREAD_POSITION_TRUTH_UNAVAILABLE:{type(exc).__name__}"

    metric_norm = _norm_metric(temperature_metric)
    for row in rows:
        def _g(name: str):
            try:
                return row[name] if isinstance(row, sqlite3.Row) else None
            except (IndexError, KeyError):
                return None

        if metric_col and _norm_metric(_g("metric")) != metric_norm:
            continue
        pid = str(_g("position_id") or "")
        tok = str(_g("token_id") or "")
        # The position this fill-up owns is fine; same-token rows are the held
        # exposure being topped up (not a new independent entry).
        if pid and pid == owned_pid:
            continue
        if owned_tok and tok == owned_tok:
            continue
        # Any OTHER live same-family position is a new/unowned exposure → abort.
        return (
            "PRESUBMIT_REREAD_NEW_UNOWNED_SAME_FAMILY_ENTRY:"
            f"position_id={pid or 'unknown'}:bin_label={str(_g('bin_label') or 'unknown')}"
        )
    return None


def complete_fill_up_lease(
    conn: sqlite3.Connection,
    intent_id: Optional[str],
    *,
    now_iso: str,
    new_entry_command_id: Optional[str] = None,
) -> None:
    """Advance the fill-up lease to the COMPLETE terminal status on submit ack."""
    if not intent_id:
        return
    advance_rebalance_lease(
        conn,
        intent_id,
        status="COMPLETE",
        now_iso=now_iso,
        new_entry_command_id=new_entry_command_id,
    )


def abort_fill_up_lease(
    conn: sqlite3.Connection,
    intent_id: Optional[str],
    *,
    now_iso: str,
    reason: str,
) -> None:
    """Advance the fill-up lease to the ABORTED terminal status (release the family)."""
    if not intent_id:
        return
    advance_rebalance_lease(
        conn,
        intent_id,
        status="ABORTED",
        now_iso=now_iso,
        abort_reason=reason,
    )


def _norm_metric(value: object) -> str:
    metric = str(value or "").strip().lower()
    if metric in {"high", "tmax", "max", "maximum", "highest"}:
        return "high"
    if metric in {"low", "tmin", "min", "minimum", "lowest"}:
        return "low"
    return metric
