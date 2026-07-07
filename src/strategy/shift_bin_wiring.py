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

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from src.contracts.position_truth import (
    CURRENT_MONEY_RISK_CHAIN_STATES,
    has_current_money_risk_chain_state,
)
from src.strategy.family_rebalance import (
    ActiveRebalanceLease,
    ShiftBinDecision,
    acquire_rebalance_lease,
    active_rebalance_lease_for_family,
    advance_rebalance_lease,
    decide_shift_bin,
)

# Reuse the EXACT same live-committed/in-flight phase set + schema helpers the D1
# fill-up wiring uses (no parallel exposure-phase truth).
from src.strategy.fill_up_wiring import (
    _LIVE_CHAIN_SHARE_EPSILON,
    _columns,
    _live_position_phase_sql,
    _norm_metric,
    _row_get,
    _table_exists,
)


_CHAIN_COLLATERAL_RESIDUAL_MAX_AGE_SECONDS = 180.0
_CURRENT_MONEY_RISK_CHAIN_STATES = tuple(sorted(CURRENT_MONEY_RISK_CHAIN_STATES))


@dataclass(frozen=True)
class HeldSiblingExposure:
    """The OLD held leg (same family, DIFFERENT bin/token than the fresh selection).

    ``token_id`` is the HELD-SIDE / sellable token. For buy_yes positions this is
    ``position_current.token_id``; for buy_no positions it is ``no_token_id``. The
    shift-bin exit path sells this token, so using the condition/YES token for buy_no
    rows creates a false collateral miss even when chain/local shares are synced.

    ``entry_q_lcb`` / ``current_q_lcb`` back the VALUE/BELIEF GATE in
    ``decide_shift_bin`` (do not churn-sell a still-strongly-believed leg). Sourced
    the SAME way ``fill_up_wiring.HeldSameTokenExposure.entry_q_lcb`` is: the entry CI
    lower bound (``p_posterior - entry_ci_width/2``) captured at position-open time.
    ``current_q_lcb`` is the position's own freshest monitored belief
    (``last_monitor_prob``, gated on ``last_monitor_prob_is_fresh`` — the same K1
    single-belief-authority field every held-position monitor refresh writes; see
    ``src/engine/position_belief.py`` and ``src/engine/monitor_refresh.py``). Both are
    None when unavailable — the gate fails CLOSED (HOLD) on either being None.
    """

    position_id: str
    token_id: str
    bin_label: str
    direction: str
    current_live_usd: float
    entry_q_lcb: Optional[float] = None
    current_q_lcb: Optional[float] = None


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


@dataclass(frozen=True)
class OldLegResidual:
    """Typed OLD-leg residual truth; never stores shares in the USD slot."""

    shares: float | None
    usd: float | None
    source: str


def old_leg_is_live(
    residual: OldLegResidual,
    *,
    min_order_shares: float,
    dust_floor_usd: float,
) -> bool:
    """Conservative close-before-open predicate shared by new and existing leases."""

    if residual.source == "ambiguous":
        return True
    try:
        shares = float(residual.shares) if residual.shares is not None else None
    except (TypeError, ValueError):
        shares = None
    try:
        usd = float(residual.usd) if residual.usd is not None else None
    except (TypeError, ValueError):
        usd = None
    try:
        min_shares = max(float(min_order_shares), 0.0)
    except (TypeError, ValueError):
        min_shares = 0.0
    try:
        dust_floor = max(float(dust_floor_usd), 0.0)
    except (TypeError, ValueError):
        dust_floor = 0.0

    if shares is not None and shares >= min_shares and shares > 0.0:
        return True
    if usd is not None and usd >= dust_floor and usd > 0.0:
        return True
    return False


def active_shift_lease_for_family(
    conn: sqlite3.Connection,
    *,
    family_key: str,
) -> ActiveRebalanceLease | None:
    """Return an active SHIFT_BIN lease for this family, if one exists."""

    return active_rebalance_lease_for_family(
        conn,
        family_key=family_key,
        operation="SHIFT_BIN",
    )


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
    restricted to live/in-flight phases or chain-proven current money risk. Schema/read
    ambiguity returns None, but the live adapter must pair that with independent
    same-family truth and fail closed rather than treating None as proof there is no
    old leg.
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

    phase_sql, phase_params = _live_position_phase_sql(cols)
    positive_terms = [
        (f"COALESCE({c},0) > 0", ())
        for c in ("chain_cost_basis_usd", "cost_basis_usd", "size_usd")
        if c in cols
    ]
    if "chain_shares" in cols:
        if "chain_state" in cols:
            chain_state_placeholders = ",".join("?" for _ in _CURRENT_MONEY_RISK_CHAIN_STATES)
            positive_terms.append(
                (
                    f"(COALESCE(chain_shares,0) > ? AND chain_state IN ({chain_state_placeholders}))",
                    (_LIVE_CHAIN_SHARE_EPSILON, *_CURRENT_MONEY_RISK_CHAIN_STATES),
                )
            )
        else:
            positive_terms.append(
                ("COALESCE(chain_shares,0) > ?", (_LIVE_CHAIN_SHARE_EPSILON,))
            )
    if not positive_terms:
        return None
    positive_sql = " AND (" + " OR ".join(term for term, _ in positive_terms) + ")"
    positive_params: list[object] = []
    for _, params_for_term in positive_terms:
        positive_params.extend(params_for_term)

    selected_names = (
        "position_id", "token_id", "no_token_id", "bin_label", "direction",
        "chain_cost_basis_usd", "cost_basis_usd", "size_usd", "chain_shares",
        "chain_state", metric_col, "p_posterior", "entry_ci_width",
        "last_monitor_prob", "last_monitor_prob_is_fresh",
    )
    select_cols = []
    for name in selected_names:
        select_cols.append(name if name in cols else f"NULL AS {name}")
    order_sql = "ORDER BY updated_at DESC" if "updated_at" in cols else ""
    sql = (
        f"SELECT {', '.join(select_cols)} FROM position_current "
        f"WHERE {phase_sql} AND city = ? AND target_date = ?{positive_sql} {order_sql}"
    )
    params: list[object] = []
    params.extend(phase_params)
    params.extend([str(city), str(target_date)])
    params.extend(positive_params)
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.Error:
        return None

    metric_norm = _norm_metric(temperature_metric)
    for row in rows:
        def _g(name: str):
            return _row_get(row, selected_names, name)

        if _norm_metric(_g(metric_col)) != metric_norm:
            continue
        direction = str(_g("direction") or "")
        yes_token = str(_g("token_id") or "")
        no_token = str(_g("no_token_id") or "")
        tok = no_token if direction == "buy_no" and no_token else yes_token
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
        if current_live <= 0.0:
            try:
                chain_shares = float(_g("chain_shares") or 0.0)
            except (TypeError, ValueError):
                chain_shares = 0.0
            if chain_shares > _LIVE_CHAIN_SHARE_EPSILON:
                current_live = chain_shares
        return HeldSiblingExposure(
            position_id=str(_g("position_id") or ""),
            token_id=tok,
            bin_label=bin_label,
            direction=direction,
            current_live_usd=current_live,
            entry_q_lcb=_entry_q_lcb_from_row(_g),
            current_q_lcb=_current_q_lcb_from_row(_g),
        )
    return None


def _entry_q_lcb_from_row(get) -> float | None:
    """Entry CI lower bound the same way ``fill_up_wiring`` computes it for the SAME
    row shape: ``p_posterior - entry_ci_width / 2``. None when either is missing."""

    p_posterior = get("p_posterior")
    entry_ci_width = get("entry_ci_width")
    if p_posterior is None or entry_ci_width is None:
        return None
    try:
        return float(p_posterior) - float(entry_ci_width) / 2.0
    except (TypeError, ValueError):
        return None


def _current_q_lcb_from_row(get) -> float | None:
    """The old leg's OWN freshest monitored belief (K1 single-belief-authority field),
    gated on its freshness flag. None (fail closed) when stale, missing, or unparsable
    — the caller (``decide_shift_bin``'s value/belief gate) must never treat a stale
    monitor snapshot as proof the belief is still strong."""

    if not bool(get("last_monitor_prob_is_fresh") or False):
        return None
    raw = get("last_monitor_prob")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _chain_collateral_available_shares(
    conn: sqlite3.Connection,
    *,
    token_id: str,
) -> float | None:
    """Return fresh CHAIN collateral token shares, or None when unavailable/stale."""

    token = str(token_id or "").strip()
    if not token:
        return None
    try:
        if not _table_exists(conn, "collateral_ledger_snapshots"):
            return None
        row = conn.execute(
            """
            SELECT ctf_token_balances_json, captured_at
              FROM collateral_ledger_snapshots
             WHERE authority_tier = 'CHAIN'
             ORDER BY captured_at DESC, id DESC
             LIMIT 1
            """
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None

    balances_raw = _row_get(row, ("ctf_token_balances_json", "captured_at"), "ctf_token_balances_json")
    captured_raw = _row_get(row, ("ctf_token_balances_json", "captured_at"), "captured_at")
    try:
        captured_at = datetime.fromisoformat(str(captured_raw).replace("Z", "+00:00"))
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - captured_at.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return None
    if age_seconds < 0.0 or age_seconds > _CHAIN_COLLATERAL_RESIDUAL_MAX_AGE_SECONDS:
        return None

    try:
        balances = json.loads(str(balances_raw or "{}"))
    except json.JSONDecodeError:
        return None
    if not isinstance(balances, dict):
        return None
    try:
        micro_shares = float(balances.get(token, 0) or 0)
    except (TypeError, ValueError):
        return None
    return max(0.0, micro_shares / 1_000_000.0)


def read_old_leg_residual_usd(
    conn: Optional[sqlite3.Connection],
    *,
    token_id: str,
) -> float:
    """Return the OLD leg's current live committed USD, or 0.0 when no USD remains.

    Compatibility wrapper for callers that still expect a numeric USD residual. It
    intentionally does not convert shares to USD; callers that need sellability must
    use ``read_old_leg_residual`` plus ``old_leg_is_live``.
    """

    residual = read_old_leg_residual(conn, token_id=token_id)
    if residual.usd is None:
        return float("inf") if residual.source == "ambiguous" else 0.0
    return float(residual.usd)


def read_old_leg_residual(
    conn: Optional[sqlite3.Connection],
    *,
    token_id: str,
) -> OldLegResidual:
    """Return typed OLD-leg residual truth, or ambiguous when it cannot be proven.

    The CLOSE proof for close-before-open: when the old leg has been exited/voided to
    zero (or dust below min-order), no live ``position_current`` row remains for the
    old token, or fresh CHAIN collateral proves the sellable token balance is zero, so
    this returns 0.0 (== proven closed from canonical truth). A row with positive
    committed cost in a blocking phase returns that USD (still live → exit first).
    Chain collateral/cost basis is preferred over the projected cost basis (chain
    truth).
    Fails CLOSED conservatively: a read/schema error returns +inf so the caller treats
    the old leg as STILL LIVE (exit first, never falsely enter) rather than 0.
    """
    token = str(token_id or "").strip()
    if conn is None or not token:
        return OldLegResidual(shares=None, usd=float("inf"), source="ambiguous")
    try:
        if not _table_exists(conn, "position_current"):
            return OldLegResidual(shares=None, usd=float("inf"), source="ambiguous")
        cols = _columns(conn, "position_current")
    except sqlite3.Error:
        return OldLegResidual(shares=None, usd=float("inf"), source="ambiguous")
    if "token_id" not in cols:
        return OldLegResidual(shares=None, usd=float("inf"), source="ambiguous")
    token_cols = [c for c in ("token_id", "no_token_id") if c in cols]
    phase_sql, phase_params = _live_position_phase_sql(cols)
    token_sql = " OR ".join(f"NULLIF({c}, '') = ?" for c in token_cols)
    positive_terms = [
        (f"COALESCE({c},0) > 0", ())
        for c in ("chain_cost_basis_usd", "cost_basis_usd", "size_usd")
        if c in cols
    ]
    if "chain_shares" in cols:
        if "chain_state" in cols:
            chain_state_placeholders = ",".join("?" for _ in _CURRENT_MONEY_RISK_CHAIN_STATES)
            positive_terms.append(
                (
                    f"(COALESCE(chain_shares,0) > ? AND chain_state IN ({chain_state_placeholders}))",
                    (_LIVE_CHAIN_SHARE_EPSILON, *_CURRENT_MONEY_RISK_CHAIN_STATES),
                )
            )
        else:
            positive_terms.append(
                ("COALESCE(chain_shares,0) > ?", (_LIVE_CHAIN_SHARE_EPSILON,))
            )
    if not positive_terms:
        return OldLegResidual(shares=None, usd=float("inf"), source="ambiguous")
    positive_sql = " AND (" + " OR ".join(term for term, _ in positive_terms) + ")"
    positive_params: list[object] = []
    for _, params_for_term in positive_terms:
        positive_params.extend(params_for_term)
    selected_names = (
        "chain_cost_basis_usd",
        "cost_basis_usd",
        "size_usd",
        "chain_shares",
        "chain_state",
    )
    select_cols = [c if c in cols else f"NULL AS {c}" for c in selected_names]
    order_sql = "ORDER BY updated_at DESC" if "updated_at" in cols else ""
    sql = (
        f"SELECT {', '.join(select_cols)} FROM position_current "
        f"WHERE {phase_sql} AND ({token_sql}){positive_sql} {order_sql} LIMIT 1"
    )
    params: list[object] = []
    params.extend(phase_params)
    params.extend(token for _ in token_cols)
    params.extend(positive_params)
    try:
        row = conn.execute(sql, tuple(params)).fetchone()
    except sqlite3.Error:
        return OldLegResidual(shares=None, usd=float("inf"), source="ambiguous")
    if row is None:
        return OldLegResidual(shares=0.0, usd=0.0, source="no_live_position")

    def _g(name: str):
        return _row_get(row, selected_names, name)

    row_chain_shares = None
    try:
        row_chain_shares = float(_g("chain_shares")) if _g("chain_shares") is not None else None
    except (TypeError, ValueError):
        row_chain_shares = None

    row_live_usd = 0.0
    for value in (_g("chain_cost_basis_usd"), _g("cost_basis_usd"), _g("size_usd")):
        try:
            v = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            v = 0.0
        if v > 0.0:
            row_live_usd = v
            break

    row_chain_state = _g("chain_state")
    row_asserts_current_chain_risk = (
        row_chain_shares is not None
        and row_chain_shares > _LIVE_CHAIN_SHARE_EPSILON
        and (
            "chain_state" not in cols
            or has_current_money_risk_chain_state(row_chain_state)
        )
    )
    if row_asserts_current_chain_risk:
        if row_live_usd > 0.0:
            return OldLegResidual(
                shares=row_chain_shares,
                usd=row_live_usd,
                source="position_current_chain_usd",
            )
        return OldLegResidual(
            shares=row_chain_shares,
            usd=None,
            source="position_current_chain_shares",
        )

    chain_available_shares = _chain_collateral_available_shares(conn, token_id=token)
    if chain_available_shares == 0.0:
        # A fresh CHAIN collateral snapshot is the venue-side sellability truth. A
        # stale local position projection must not keep re-opening EXIT_OLD_LEG after
        # the wallet has no old-leg collateral left. This may only override rows that
        # do NOT still assert current chain risk in position_current; a synced positive
        # chain_shares row is itself chain evidence and must stay live.
        return OldLegResidual(shares=0.0, usd=0.0, source="chain_collateral_zero")

    if row_live_usd > 0.0:
        return OldLegResidual(
            shares=row_chain_shares,
            usd=row_live_usd,
            source="position_current_usd",
        )
    return OldLegResidual(shares=row_chain_shares, usd=0.0, source="no_live_usd")


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
    shift_belief_weakening_floor: float = 0.0,
) -> ShiftBinPlan:
    """Orchestrate the SHIFT_BIN lease acquire + ``decide_shift_bin``.

    Returns NOOP (leave the entry/fill-up paths untouched, no lease) when this is not
    a sibling shift. Otherwise acquires the family-rebalance lease FIRST (the
    concurrency guard): a concurrent same-family lease => acquire returns None => ABORT
    with no order and no second lease. With the lease held, runs ``decide_shift_bin``:
    EXIT_OLD_LEG advances the lease EXIT_SUBMITTED and returns the old-leg identity;
    ENTER_NEW_BIN advances ENTRY_SUBMITTED and admits the counter-entry; BLOCKED and the
    VALUE/BELIEF GATE (NOT_SHIFT_BIN — old leg still live but belief not genuinely
    weakened, or belief unavailable) both advance the lease ABORTED and return ABORT
    (no exit, no order) — the same "release the family, no side effect" handling
    NOT_SHIFT_BIN already gets below. The old leg's entry/current belief come from
    ``held.entry_q_lcb`` / ``held.current_q_lcb`` (see ``HeldSiblingExposure``).
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
        old_leg_q_current_lcb=held.current_q_lcb,
        old_leg_q_entry_lcb=held.entry_q_lcb,
        shift_belief_weakening_floor=float(shift_belief_weakening_floor),
    )

    if decision.phase == "BLOCKED":
        advance_rebalance_lease(
            conn, lease_intent_id, status="ABORTED", now_iso=now_iso,
            abort_reason=decision.reason,
        )
        return ShiftBinPlan(kind="ABORT", lease_intent_id=lease_intent_id, reason=decision.reason)

    if decision.phase == "NOT_SHIFT_BIN":
        # Either a defensive mismatch (decide_shift_bin disagreed with
        # read_held_sibling_exposure, e.g. a same-token row slipped through) OR the
        # VALUE/BELIEF GATE denied a live old leg whose belief has not genuinely
        # weakened (SHIFT_OLD_LEG_BELIEF_NOT_WEAKENED / _UNKNOWN). Either way: release
        # the lease, no order, HOLD the leg for a later cycle to re-evaluate.
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


def record_entry_submitted(
    conn: sqlite3.Connection,
    intent_id: Optional[str],
    *,
    now_iso: str,
    reason: Optional[str] = None,
) -> None:
    """Record that the old leg is closed and the counter-entry may be submitted."""

    if not intent_id:
        return
    advance_rebalance_lease(
        conn,
        intent_id,
        status="ENTRY_SUBMITTED",
        now_iso=now_iso,
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


def record_entry_unknown(
    conn: sqlite3.Connection,
    intent_id: Optional[str],
    *,
    now_iso: str,
    new_entry_command_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Keep the shift-bin counter-entry lease active while submit reconciles."""
    if not intent_id:
        return
    advance_rebalance_lease(
        conn,
        intent_id,
        status="ENTRY_UNKNOWN",
        now_iso=now_iso,
        new_entry_command_id=new_entry_command_id,
        abort_reason=reason,
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
