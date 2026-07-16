"""RiskGuard: independent monitoring process. Spec §7.

Runs as a SEPARATE process with its own 60-second tick.
Reads authoritative settlement records from zeus.db, writes to risk_state.db,
and emits durable risk actions into zeus.db when the canonical table exists.
Graduated response: GREEN → YELLOW → ORANGE → RED.

# Created: (pre-audit)
# Last reused or audited: 2026-07-08
# Authority basis: connection-leak audit 2026-05-10 — 51 open zeus-world.db-wal
#   handles observed on PID 18538. Root cause: tick() and tick_with_portfolio()
#   opened zeus_conn / risk_conn without try/finally, so any exception in the
#   tick body left both connections dangling. Fixed by wrapping tick bodies in
#   try/finally to guarantee conn.close() on every exit path.
#   2026-05-17 live lock remediation: trade/world metric lock loss degrades to
#   a fresh DATA_DEGRADED risk_state row, not stale RED force-exit.
#   2026-06-08 thepath/audit-realign iron #4/#6 fix: (1) init_risk_db re-applies
#   busy_timeout after executescript (Fitz #5 strip-trap); (2) lock-attestation
#   FAILS CONSERVATIVE — max(previous_level, DATA_DEGRADED), never re-stamps a
#   fail-open GREEN, never weakens RED; (3) get_current_level() floors a degraded
#   row (riskguard_degraded_reason) to DATA_DEGRADED so the SINGLE authority never
#   surfaces a degraded GREEN as clean — kills the status-vs-gate split-brain.
"""

import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from src.config import settings, get_mode
from src.riskguard.discord_alerts import alert_halt, alert_resume, alert_warning
from src.riskguard.metrics import (
    brier_score,
    directional_accuracy,
    evaluate_brier,
)
from src.riskguard.risk_level import RiskLevel, overall_level
from src.runtime import bankroll_provider
from src.runtime.bankroll_provider import BankrollOfRecord
from src.state.db import (
    CANONICAL_STRATEGY_KEYS,
    RISK_DB_PATH,
    get_connection,
    get_trade_connection_with_world_required,
    _zeus_trade_db_path,
    query_authoritative_settlement_rows,
    query_portfolio_loader_view,
    query_strategy_health_snapshot,
    refresh_strategy_health,
)
from src.state.fill_dedup import canonical_trade_fact_cte
from src.state.portfolio import (
    ENTRY_ECONOMICS_LEGACY_UNKNOWN,
    FILL_GRADE_FILL_AUTHORITIES,
    FILL_AUTHORITY_NONE,
    PortfolioState,
    Position,
    has_verified_trade_fill,
    load_portfolio,
)

RISKGUARD_SETTLEMENT_LIMIT = 50
RISKGUARD_BRIER_SCAN_LIMIT = 200
from src.state.portfolio_loader_policy import choose_portfolio_truth_source
from src.state.strategy_tracker import load_tracker
from src.contracts.freshness_registry import FreshnessLevel, registry as _freshness_registry

logger = logging.getLogger(__name__)
TRAILING_LOSS_ROW_TOLERANCE_USD = 0.01
TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE = timedelta(hours=2)
TRAILING_LOSS_SOURCE_OK = "risk_state_history"
TRAILING_LOSS_SOURCE_DEGRADED = "no_trustworthy_reference_row"
TRAILING_LOSS_STATUSES = {
    "ok",
    "stale_reference",
    "insufficient_history",
    "inconsistent_history",
    "no_reference_row",
}
_BANKROLL_TRUTH_SOURCES_OF_RECORD = frozenset({
    "polymarket_wallet",
    "collateral_ledger_snapshot",
})
_RISKGUARD_OPEN_RUNTIME_STATES = frozenset({
    "pending_tracked",
    "entered",
    "day0_window",
    "pending_exit",
    "unknown",
})


def _collateral_identity_level(zeus_conn: sqlite3.Connection) -> RiskLevel:
    """SCH-W1.1-CAS-LEDGER 7th risk component.

    RED iff any unresolved collateral_identity_mismatch finding exists,
    GREEN otherwise. Routes through the existing RED sweep (INV-05 — risk
    must gate, not advise); no new kill-switch.
    """
    try:
        from src.execution.exchange_reconcile import list_unresolved_findings

        findings = list_unresolved_findings(zeus_conn, kind="collateral_identity_mismatch")
    except sqlite3.OperationalError:
        return RiskLevel.GREEN
    return RiskLevel.RED if findings else RiskLevel.GREEN


def _portfolio_consistency_level(consistency_lock: str) -> RiskLevel:
    """Route the RiskGuard loader's row-exclusion verdict into the risk lane.

    consistency_lock == "pass" (zero excluded rows, counts reconcile) is the
    only GREEN case. "degraded" (a known, reconciled row exclusion — B052) and
    "mismatched" (counts don't reconcile) both mean real exposure may be
    missing from the risk view: an excluded/unaccounted position row is
    missing truth input, so it is DATA_DEGRADED (YELLOW-equivalent: no new
    entries, monitor/exit continue) — never RED, since crash-the-tick / fail-
    closed-RED-on-one-bad-row was the original B052 bug this loader fixed.
    """
    return RiskLevel.GREEN if consistency_lock == "pass" else RiskLevel.DATA_DEGRADED


def _unresolved_exposure_data_degraded_level(zeus_conn: sqlite3.Connection, portfolio) -> RiskLevel:
    """T2 (quarantine excision, BLOCKER-1 "unbounded obligation -> DATA_DEGRADED"
    leg): DATA_DEGRADED iff any OPEN EntryExposureObligation carries unknown
    (unbounded) exposure — a command that may have caused venue/chain exposure
    with no usable size/cost figure yet.

    DATA_DEGRADED (YELLOW-equivalent: no new entries, monitor/exit/
    reconciliation continue), never RED — an unknown-exposure fact is missing
    truth input, not a confirmed loss event.

    Scope note: the sibling "unmapped ChainOnlyFact family identity ->
    DATA_DEGRADED" leg (canonical-asset dedup reducer, T2 item 1) is NOT
    folded in here — ``portfolio`` on this call site comes from
    ``_load_riskguard_portfolio_truth``, a Position-only loader view that
    never populates ``chain_only_facts`` (a 60-second hot-tick perf
    tradeoff documented on that loader; see its docstring). That leg IS
    wired at src.engine.cycle_runner.run_cycle, which loads the full
    ``PortfolioState`` via ``load_portfolio()`` including ``chain_only_facts``.
    ``portfolio`` is accepted here for call-site symmetry / a future wire once
    chain-only facts are cheaply available on this loader path.
    """
    try:
        from src.state.entry_exposure_obligation import has_unbounded_obligation

        if has_unbounded_obligation(zeus_conn):
            return RiskLevel.DATA_DEGRADED
    except sqlite3.Error:
        # Fail-soft on a transient/degraded read of this specific signal —
        # every OTHER risk component above still evaluates on its own merits;
        # a read error here must not crash the whole tick.
        return RiskLevel.GREEN
    return RiskLevel.GREEN


def _finite_float_or_none(value):
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _get_runtime_trade_connection() -> sqlite3.Connection:
    # v4 plan §AX3: riskguard runtime = LIVE class.
    if get_connection.__module__ != "src.state.db":
        return get_connection()
    return get_trade_connection_with_world_required(write_class="live")


def _install_riskguard_collateral_ledger() -> bool:
    """Install the P4-produced collateral ledger reader in this process.

    RiskGuard runs in its own launchd process, so it cannot rely on
    ``src.main`` having installed the process-local global ledger singleton.
    The ledger is path-backed and opens short-lived DB connections only; it
    consumes post-trade-capital's durable CHAIN snapshots and performs no venue
    I/O.
    """

    from src.state.collateral_ledger import CollateralLedger, configure_global_ledger, get_global_ledger

    if get_global_ledger() is not None:
        return True
    try:
        configure_global_ledger(CollateralLedger(db_path=_zeus_trade_db_path()))
        logger.info(
            "RiskGuard CollateralLedger reader installed (db=%s)",
            _zeus_trade_db_path(),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - tick fail-closed handles missing truth.
        logger.warning("RiskGuard CollateralLedger reader install failed: %s", exc)
        return False


def _bankroll_of_record_for_riskguard() -> BankrollOfRecord | None:
    """Return current live bankroll truth for RiskGuard.

    Source order is deliberate:
    1. Fresh durable collateral snapshot from the post-trade-capital sidecar.
       This is live CHAIN collateral truth already used by submit preflight and
       avoids duplicating a fragile wallet/positions API read in RiskGuard.
    2. Direct bankroll provider current() for compatibility when the sidecar
       snapshot is unavailable.

    If neither live truth source is available, callers fail closed at
    DATA_DEGRADED.
    """

    try:
        snapshot_record = bankroll_provider.warm_from_collateral_snapshot()
    except Exception as exc:  # noqa: BLE001 - direct wallet path still has a chance.
        snapshot_record = None
        logger.warning("RiskGuard collateral snapshot bankroll read failed: %s", exc)
    if snapshot_record is not None:
        return snapshot_record

    try:
        return bankroll_provider.current()
    except Exception as exc:  # noqa: BLE001 - caller writes the fail-closed row.
        logger.warning("RiskGuard direct bankroll read failed: %s", exc)
        return None


def _portfolio_position_from_loader_row(row: dict) -> Position:
    # B052: Enforce strict canonical fields rather than filling defaults
    required = ["trade_id", "market_id", "city", "target_date", "direction", "unit", "env", "size_usd"]
    for req in required:
        if row.get(req) is None or str(row.get(req)) == "":
            raise ValueError(f"Canonical loader row missing critical field {req!r}")

    entry_authority = str(row.get("entry_economics_authority") or ENTRY_ECONOMICS_LEGACY_UNKNOWN)
    fill_authority = str(row.get("fill_authority") or FILL_AUTHORITY_NONE)
    if fill_authority in FILL_GRADE_FILL_AUTHORITIES:
        if str(row.get("entry_economics_source") or "") != "execution_fact":
            raise ValueError("fill-grade loader row missing execution_fact source provenance")
        if not str(row.get("execution_fact_intent_id") or ""):
            raise ValueError("fill-grade loader row missing execution_fact_intent_id provenance")
        if not str(row.get("execution_fact_filled_at") or ""):
            raise ValueError("fill-grade loader row missing execution_fact_filled_at provenance")

    return Position(
        trade_id=str(row["trade_id"]),
        market_id=str(row["market_id"]),
        city=str(row["city"]),
        cluster=str(row.get("cluster") or ""),
        target_date=str(row["target_date"]),
        bin_label=str(row.get("bin_label") or ""),
        direction=str(row["direction"]),
        unit=str(row["unit"]),
        temperature_metric=str(row.get("temperature_metric") or "high"),
        env=str(row["env"]),
        size_usd=float(row["size_usd"]),
        shares=float(row.get("shares") or 0.0),
        cost_basis_usd=float(row.get("cost_basis_usd") or 0.0),
        entry_price=float(row.get("entry_price") or 0.0),
        submitted_notional_usd=float(row.get("submitted_size_usd") or 0.0),
        filled_cost_basis_usd=float(row.get("filled_cost_basis_usd") or 0.0),
        entry_price_avg_fill=float(row.get("entry_price_avg_fill") or 0.0),
        shares_filled=float(row.get("shares_filled") or 0.0),
        entry_economics_authority=entry_authority,
        fill_authority=fill_authority,
        p_posterior=float(row.get("p_posterior") or 0.0),
        entered_at=str(row.get("entered_at") or ""),
        day0_entered_at=str(row.get("day0_entered_at") or ""),
        decision_snapshot_id=str(row.get("decision_snapshot_id") or ""),
        entry_method=str(row.get("entry_method") or ""),
        strategy_key=str(row.get("strategy_key") or ""),
        strategy=str(row.get("strategy") or row.get("strategy_key") or ""),
        edge_source=str(row.get("edge_source") or ""),
        discovery_mode=str(row.get("discovery_mode") or ""),
        state=str(row.get("state") or "entered"),
        order_id=str(row.get("order_id") or ""),
        order_status=str(row.get("order_status") or ""),
        chain_state=str(row.get("chain_state") or ""),
        token_id=str(row.get("token_id") or ""),
        no_token_id=str(row.get("no_token_id") or ""),
        condition_id=str(row.get("condition_id") or ""),
        exit_state=str(row.get("exit_state") or ""),
        last_monitor_prob=_finite_float_or_none(row.get("last_monitor_prob")),
        last_monitor_edge=_finite_float_or_none(row.get("last_monitor_edge")),
        last_monitor_market_price=row.get("last_monitor_market_price"),
        admin_exit_reason=str(row.get("admin_exit_reason") or ""),
        entry_fill_verified=bool(row.get("entry_fill_verified", False)),
    )


def _riskguard_unloadable_row_is_excluded_duplicate(
    row: dict, loaded_positions: list[Position]
) -> bool:
    """Conservative proof that an unloadable row's exposure is already
    accounted for by a successfully loaded canonical position (the B052
    dual-id recovered-fill DUPLICATE case, riskguard.py comment above) —
    NOT a genuine missing-exposure gap.

    Returns True only when the excluded row carries a non-empty on-chain
    token_id AND a loaded position for that SAME token_id already covers
    at least as many shares as the excluded row claims — i.e. the excluded
    row cannot add unaccounted exposure even in the worst case. Any row this
    cannot positively prove (no token_id, no matching loaded position, or a
    loaded match that covers fewer shares) is NOT classified as a duplicate
    and must degrade the verdict — proof of safety is required, absence of
    proof of danger is not enough on a money-risk view.
    """
    token_id = str(row.get("token_id") or "")
    if not token_id:
        return False
    excluded_shares = _finite_float_or_none(row.get("shares")) or 0.0
    for position in loaded_positions:
        if str(getattr(position, "token_id", "") or "") != token_id:
            continue
        loaded_shares = _finite_float_or_none(getattr(position, "shares", None)) or 0.0
        if loaded_shares >= excluded_shares:
            return True
    return False


def _riskguard_position_status_view_from_loader_rows(
    rows: list[dict],
    *,
    excluded_trade_ids: set[str] | None = None,
) -> dict:
    excluded = excluded_trade_ids or set()
    positions: list[dict] = []
    strategy_open_counts: dict[str, int] = {}
    chain_state_counts: dict[str, int] = {}
    exit_state_counts: dict[str, int] = {}
    total_exposure_usd = 0.0
    total_unrealized_pnl = 0.0
    unverified_entries = 0
    day0_positions = 0

    for row in rows:
        trade_id = str(row.get("trade_id") or "")
        if trade_id and trade_id in excluded:
            continue
        state = str(row.get("state") or "")
        if state not in _RISKGUARD_OPEN_RUNTIME_STATES:
            continue

        strategy_key = str(row.get("strategy") or row.get("strategy_key") or "")
        chain_state = str(row.get("chain_state") or "unknown")
        exit_state = str(row.get("exit_state") or "none")
        if state != "pending_exit":
            exit_state = "none"
        shares = _finite_float_or_none(row.get("shares")) or 0.0
        mark_price = _finite_float_or_none(row.get("last_monitor_market_price"))
        cost_basis_usd = _finite_float_or_none(row.get("cost_basis_usd"))
        effective_cost_basis_usd = (
            _finite_float_or_none(row.get("effective_cost_basis_usd"))
            if row.get("effective_cost_basis_usd") is not None
            else _finite_float_or_none(row.get("size_usd"))
        ) or 0.0
        unrealized_pnl = 0.0
        if shares and mark_price is not None and cost_basis_usd is not None:
            unrealized_pnl = round((shares * mark_price) - cost_basis_usd, 2)

        positions.append({
            "trade_id": trade_id,
            "city": str(row.get("city") or ""),
            "direction": str(row.get("direction") or ""),
            "strategy": strategy_key,
            "state": state,
            "chain_state": chain_state,
            "exit_state": exit_state,
            "entry_fill_verified": bool(row.get("entry_fill_verified", False)),
            "admin_exit_reason": str(row.get("admin_exit_reason") or ""),
            "size_usd": effective_cost_basis_usd,
            "submitted_size_usd": float(_finite_float_or_none(row.get("submitted_size_usd")) or 0.0),
            "effective_cost_basis_usd": effective_cost_basis_usd,
            "entry_economics_authority": str(row.get("entry_economics_authority") or ""),
            "fill_authority": str(row.get("fill_authority") or ""),
            "entry_economics_source": str(row.get("entry_economics_source") or ""),
            "entry_price_avg_fill": float(_finite_float_or_none(row.get("entry_price_avg_fill")) or 0.0),
            "shares_filled": float(_finite_float_or_none(row.get("shares_filled")) or 0.0),
            "filled_cost_basis_usd": float(_finite_float_or_none(row.get("filled_cost_basis_usd")) or 0.0),
            "execution_fact_intent_id": str(row.get("execution_fact_intent_id") or ""),
            "execution_fact_filled_at": str(row.get("execution_fact_filled_at") or ""),
            "shares": shares,
            "entry_price": float(_finite_float_or_none(row.get("entry_price")) or 0.0),
            "edge": None,
            "bin_label": str(row.get("bin_label") or ""),
            "decision_snapshot_id": str(row.get("decision_snapshot_id") or ""),
            "token_id": str(row.get("token_id") or ""),
            "no_token_id": str(row.get("no_token_id") or ""),
            "condition_id": str(row.get("condition_id") or ""),
            "day0_entered_at": str(row.get("day0_entered_at") or ""),
            "mark_price": mark_price,
            "unrealized_pnl": unrealized_pnl,
        })

        strategy_open_counts[strategy_key or "unclassified"] = (
            strategy_open_counts.get(strategy_key or "unclassified", 0) + 1
        )
        chain_state_counts[chain_state] = chain_state_counts.get(chain_state, 0) + 1
        exit_state_counts[exit_state] = exit_state_counts.get(exit_state, 0) + 1
        total_exposure_usd += effective_cost_basis_usd
        total_unrealized_pnl += unrealized_pnl
        if not has_verified_trade_fill({"fill_authority": str(row.get("fill_authority") or "")}):
            unverified_entries += 1
        if state == "day0_window":
            day0_positions += 1

    return {
        "status": "ok",
        "table": "position_current",
        "positions": positions,
        "strategy_open_counts": strategy_open_counts,
        "open_positions": len(positions),
        "total_exposure_usd": round(total_exposure_usd, 2),
        "unrealized_pnl": round(total_unrealized_pnl, 2),
        "chain_state_counts": chain_state_counts,
        "exit_state_counts": exit_state_counts,
        "unverified_entries": unverified_entries,
        "day0_positions": day0_positions,
    }


def _load_riskguard_portfolio_truth(zeus_conn: sqlite3.Connection) -> tuple[PortfolioState, dict]:
    # RiskGuard protects current capital. Loading terminal position history here
    # makes every 60-second tick scan and sort the full projection table even
    # though settlements have their own canonical read below. The runtime view
    # retains every current-money-risk phase plus unresolved unloadable rows
    # while using the phase index on the live hot path.
    loader_view = query_portfolio_loader_view(
        zeus_conn,
        runtime_exposure_only=True,
    )
    policy = choose_portfolio_truth_source(loader_view.get("status"))
    if policy.source != "canonical_db":
        raise RuntimeError(
            f"riskguard requires canonical truth source, got {policy.source!r}: {policy.reason}"
        )
    loader_rows = list(loader_view.get("positions", []))
    positions = []
    unloadable_raw: list[tuple[dict, str]] = []
    for row in loader_rows:
        try:
            positions.append(_portfolio_position_from_loader_row(row))
        except ValueError as exc:
            # B052 (2026-06-16 incident fix): EXCLUDE the un-loadable row and CONTINUE
            # the tick — do NOT re-raise. The prior `raise` turned ONE un-loadable canonical
            # row into a failed tick -> RiskGuard STALE -> trader fail-closed RED -> ALL
            # trading blocked. Disabling the entire risk system because of a single bad row
            # is strictly WORSE for risk than excluding that row. The trigger here was a
            # dual-id recovered-fill DUPLICATE (its on-chain exposure already accounted via
            # the canonical position, so excluding it neither double- nor under-counts), but
            # the resilience is general. "Avoid silent masking" (the original B052 intent) is
            # preserved by a LOUD, COUNTED, VERDICT-DEGRADING exclusion (ERROR log +
            # unloadable_count in the returned truth dict, consistency_lock forced off
            # "pass" unless PROVEN accounted for — see
            # `_riskguard_unloadable_row_is_excluded_duplicate` and the classification
            # pass below) — not by crashing the whole tick and not by reporting a
            # healthy verdict while real exposure is missing from the risk view.
            unloadable_raw.append((row, str(exc)))
            continue

    # Classification pass (runs after ALL rows are loaded, since a dual-id duplicate's
    # canonical counterpart may appear anywhere in loader_rows, not necessarily before
    # the bad row). Two evidentiary tiers, per operator directive (2026-07-11 critic
    # amendment M-2): a blanket "any exclusion degrades" over-blocks the documented
    # benign B052 trigger (a dual-id recovered-fill DUPLICATE whose exposure is already
    # counted via the canonical position) with a false YELLOW halt.
    #   - "excluded_duplicate": PROVEN accounted for by a loaded position (same
    #     token_id, loaded shares >= excluded shares) — pass-eligible, still
    #     counted + logged, never silently dropped from the truth dict.
    #   - anything else ("excluded_unaccounted"): cannot be proven safe — degrades.
    unloadable: list[dict] = []
    unloadable_reason_counts: dict[str, int] = {}
    excluded_duplicate_count = 0
    for row, reason in unloadable_raw:
        unloadable_reason_counts[reason] = unloadable_reason_counts.get(reason, 0) + 1
        is_duplicate = _riskguard_unloadable_row_is_excluded_duplicate(row, positions)
        if is_duplicate:
            excluded_duplicate_count += 1
        unloadable.append({
            "trade_id": row.get("trade_id"),
            "state": row.get("state"),
            "reason": reason,
            "classification": "excluded_duplicate" if is_duplicate else "excluded_unaccounted",
        })
    if unloadable:
        logger.error(
            "RiskGuard excluded %d un-loadable canonical portfolio rows "
            "(excluded_duplicate=%d proven-accounted, excluded_unaccounted=%d; "
            "excluded from risk view; tick CONTINUES): reasons=%s sample=%s",
            len(unloadable),
            excluded_duplicate_count,
            len(unloadable) - excluded_duplicate_count,
            unloadable_reason_counts,
            unloadable[:5],
        )

    # B053 count lock, reduced to a single authoritative snapshot. RiskGuard used
    # to call load_portfolio() here as "capital metadata", but that function reads
    # the same canonical loader view again. Count the current loader rows instead:
    # loaded + unloadable must account for every canonical row in this tick.
    loader_position_count = len(loader_rows)
    if (len(positions) + len(unloadable)) != loader_position_count:
        logger.error(
            "B053 Consistency Mismatch: canonical_db loaded %d positions (+%d unloadable) "
            "from %d loader rows. RiskGuard blending MUST NOT proceed without caller-side "
            "consistency_lock check.",
            len(positions), len(unloadable), loader_position_count
        )

    # Bankroll truth comes from the live bankroll path upstream. Keep PortfolioState
    # capital fields uninitialized here so analytics cannot promote loader metadata
    # into bankroll authority.
    bankroll = 0.0
    portfolio = PortfolioState(
        positions=positions,
        bankroll=bankroll,
        updated_at="",
        audit_logging_enabled=True,
        daily_baseline_total=bankroll,
        weekly_baseline_total=bankroll,
        recent_exits=[],
        ignored_tokens=[],
    )
    # B053 count reconciliation accounts for unloadable rows: a row excluded by the
    # loader above is a KNOWN exclusion, not silent drift, so the canonical/metadata
    # comparison adds them back to check the counts reconcile. This does NOT make the
    # verdict "pass" by itself — an unloadable row means real exposure MIGHT be missing
    # from the risk view, UNLESS it is proven "excluded_duplicate" (see classification
    # pass above): a row whose exposure a loaded position already covers cannot add
    # unaccounted risk, so it does not need to block new entries. consistency_lock is
    # therefore: "pass" with zero exclusions, OR with exclusions that are ALL proven
    # excluded_duplicate; "degraded" when at least one exclusion is NOT proven accounted
    # for (an excluded_unaccounted row — the general, conservative case); "mismatched"
    # when counts don't reconcile at all — still not RED (crash-the-tick was the
    # original B052 bug), but strictly less trustworthy than either pass path.
    canonical_known_count = len(positions) + len(unloadable)
    unaccounted_unloadable = [
        row for row in unloadable if row["classification"] != "excluded_duplicate"
    ]
    if canonical_known_count != loader_position_count:
        consistency_lock = "mismatched"
    elif not unaccounted_unloadable:
        consistency_lock = "pass"
    else:
        consistency_lock = "degraded"
    strategy_health_position_view = _riskguard_position_status_view_from_loader_rows(
        loader_rows,
        excluded_trade_ids={
            str(row.get("trade_id") or "")
            for row in unloadable
            if str(row.get("trade_id") or "")
        },
    )
    return portfolio, {
        "source": "position_current",
        "loader_status": str(loader_view.get("status") or "unknown"),
        "fallback_active": False,
        "fallback_reason": "",
        "position_count": len(positions),
        "unloadable_count": len(unloadable),
        "unloadable_rows": unloadable,
        "excluded_duplicate_count": excluded_duplicate_count,
        "capital_source": "canonical_loader_view",
        "consistency_lock": consistency_lock,
        # Preserve the legacy key while it now means the single loader snapshot
        # count, not a second load_portfolio() pass.
        "metadata_position_count": loader_position_count,
        "_strategy_health_position_view": strategy_health_position_view,
    }


def _coerce_finite_float(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _position_value_usd(position: Position) -> float:
    """Conservative account-equity value for an open position."""

    shares = _coerce_finite_float(getattr(position, "shares", None)) or 0.0
    if shares > 0:
        for price_field in ("last_monitor_market_price", "entry_price_avg_fill", "entry_price"):
            price = _coerce_finite_float(getattr(position, price_field, None))
            if price is not None and price > 0:
                return max(0.0, shares * price)

    for value_field in ("filled_cost_basis_usd", "cost_basis_usd", "size_usd"):
        value = _coerce_finite_float(getattr(position, value_field, None))
        if value is not None and value > 0:
            return value
    return 0.0


def _active_position_equity_usd(conn: sqlite3.Connection, portfolio: PortfolioState) -> float:
    value_columns = (
        "shares",
        "last_monitor_market_price",
        "entry_price",
        "chain_avg_price",
        "filled_cost_basis_usd",
        "cost_basis_usd",
        "size_usd",
        "chain_cost_basis_usd",
    )
    try:
        available = {
            str(row["name"] if hasattr(row, "keys") else row[1])
            for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
        }
        selected = [column for column in value_columns if column in available]
        if not selected:
            return 0.0
        rows = conn.execute(
            f"""
            SELECT {', '.join(selected)}
            FROM position_current
            WHERE phase IN ('active', 'day0_window', 'pending_exit')
            """
        ).fetchall()
    except sqlite3.Error:
        logger.exception("RiskGuard failed to compute active position equity from position_current")
        total = 0.0
        for position in getattr(portfolio, "positions", []) or []:
            phase = str(getattr(position, "state", "") or "").lower()
            exit_state = str(getattr(position, "exit_state", "") or "").lower()
            if phase in {"settled", "voided", "admin_closed"}:
                continue
            if exit_state in {"settled", "voided", "admin_closed"}:
                continue
            total += _position_value_usd(position)
        return round(total, 2)

    total = 0.0
    for row in rows:
        row_map = row if isinstance(row, dict) else {key: row[key] for key in row.keys()}
        shares = _coerce_finite_float(row_map.get("shares")) or 0.0
        if shares > 0:
            for price_field in ("last_monitor_market_price", "entry_price", "chain_avg_price"):
                price = _coerce_finite_float(row_map.get(price_field))
                if price is not None and price > 0:
                    total += shares * price
                    break
            else:
                for value_field in ("filled_cost_basis_usd", "cost_basis_usd", "size_usd", "chain_cost_basis_usd"):
                    value = _coerce_finite_float(row_map.get(value_field))
                    if value is not None and value > 0:
                        total += value
                        break
        else:
            for value_field in ("filled_cost_basis_usd", "cost_basis_usd", "size_usd", "chain_cost_basis_usd"):
                value = _coerce_finite_float(row_map.get(value_field))
                if value is not None and value > 0:
                    total += value
                    break
    return round(total, 2)


def _unprojected_entry_fill_equity_usd(conn: sqlite3.Connection) -> float:
    """Value confirmed entry fills that have not reached position projections yet.

    A live BUY converts cash into conditional tokens. Treating the cash drop as
    realized loss trips RiskGuard after the first successful fill. Until the
    position projection catches up, the venue-confirmed fill fact is the
    conservative account-equity authority for that just-acquired asset.
    """

    try:
        rows = conn.execute(
            "WITH " + canonical_trade_fact_cte() + """
            SELECT canonical_trade_fact.filled_size, canonical_trade_fact.fill_price
            FROM canonical_trade_fact
            JOIN venue_commands cmd
              ON cmd.command_id = canonical_trade_fact.command_id
            WHERE canonical_trade_fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
              AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
              AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
              AND cmd.state = 'FILLED'
              AND NOT EXISTS (
                SELECT 1
                FROM position_lots lot
                WHERE lot.source_command_id = cmd.command_id
              )
              AND NOT EXISTS (
                SELECT 1
                FROM position_current pc
                WHERE pc.position_id = cmd.position_id
                   OR (
                        cmd.venue_order_id IS NOT NULL
                    AND pc.order_id = cmd.venue_order_id
                   )
              )
            """
        ).fetchall()
    except sqlite3.Error:
        logger.exception("RiskGuard failed to compute unprojected entry fill equity")
        return 0.0

    total = 0.0
    for row in rows:
        row_map = row if isinstance(row, dict) else {key: row[key] for key in row.keys()}
        shares = _coerce_finite_float(row_map.get("filled_size")) or 0.0
        price = _coerce_finite_float(row_map.get("fill_price")) or 0.0
        if shares > 0 and price > 0:
            total += shares * price
    return round(total, 2)


def _riskguard_account_equity(
    conn: sqlite3.Connection,
    *,
    wallet_cash_usd: float,
    portfolio: PortfolioState,
) -> dict:
    open_position_equity_usd = _active_position_equity_usd(conn, portfolio)
    unprojected_entry_fill_equity_usd = _unprojected_entry_fill_equity_usd(conn)
    effective_equity_usd = round(
        float(wallet_cash_usd) + open_position_equity_usd + unprojected_entry_fill_equity_usd,
        2,
    )
    return {
        "wallet_cash_usd": round(float(wallet_cash_usd), 2),
        "open_position_equity_usd": open_position_equity_usd,
        "unprojected_entry_fill_equity_usd": unprojected_entry_fill_equity_usd,
        "effective_equity_usd": effective_equity_usd,
    }


def _risk_state_reference_from_row(row: sqlite3.Row) -> dict | None:
    try:
        details = json.loads(row["details_json"] or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(details, dict):
        return None

    # P0-A cutover-day guard (followup_design.md §6.2, §7 hazard #3):
    # Pre-cutover risk_state rows could store config-literal capital plus PnL as
    # `effective_bankroll`. After cutover, `effective_bankroll` is the real
    # on-chain wallet. Without this guard, trailing-loss math could compare
    # different economic objects and trigger false RED → force_exit_review.
    # Only rows tagged with a live bankroll truth source are eligible
    # references. Old rows (no field, or any other value) are filtered out.
    if str(details.get("bankroll_truth_source") or "") not in _BANKROLL_TRUTH_SOURCES_OF_RECORD:
        return None

    initial_bankroll = _coerce_finite_float(details.get("initial_bankroll"))
    effective_bankroll = _coerce_finite_float(details.get("effective_bankroll"))
    if initial_bankroll is None or effective_bankroll is None:
        return None

    # `total_pnl` may still be present in details_json for analytics, but it is
    # NOT the equity formula. Effective bankroll is account equity: wallet cash
    # plus authoritative open-position value. Older rows had wallet-only equity
    # and no component fields, so they remain internally consistent only when
    # initial_bankroll == effective_bankroll.
    total_pnl = _coerce_finite_float(details.get("total_pnl")) or 0.0
    components = details.get("account_equity_components")
    if not isinstance(components, dict) and abs(initial_bankroll - effective_bankroll) > TRAILING_LOSS_ROW_TOLERANCE_USD:
        return None
    return {
        "row_id": int(row["id"]),
        "checked_at": str(row["checked_at"] or ""),
        "initial_bankroll": round(initial_bankroll, 2),
        "total_pnl": round(total_pnl, 2),
        "effective_bankroll": round(effective_bankroll, 2),
    }


def _trailing_loss_reference(
    risk_conn: sqlite3.Connection,
    *,
    now: str,
    lookback: timedelta,
) -> dict:
    cutoff_dt = datetime.fromisoformat(now.replace("Z", "+00:00")) - lookback
    cutoff = cutoff_dt.isoformat()
    total_rows = int(
        risk_conn.execute("SELECT COUNT(*) FROM risk_state").fetchone()[0] or 0
    )
    if total_rows == 0:
        return {
            "status": "no_reference_row",
            "source": TRAILING_LOSS_SOURCE_DEGRADED,
            "reference": None,
        }

    # SF7 fix (2026-05-04): pre-filter to post-cutover rows at the SQL layer.
    # Without this, the LIMIT-100 window can be dominated by rows that lack the
    # top-level `bankroll_truth_source` field (transient writer regressions, or
    # error-state rows like `bankroll_provider_unavailable`). All such rows fail
    # `_risk_state_reference_from_row` line 196, so the for-loop falls through to
    # `inconsistent_history` and the daemon stays DATA_DEGRADED indefinitely —
    # even when 918 post-cutover rows exist further back in history. Filtering at
    # the SQL layer means: if no post-cutover row is old enough we get the proper
    # `insufficient_history` (already bootstrap-allowlisted to GREEN), and only
    # rows that COULD pass trustworthiness reach the for-loop. Architectural
    # `inconsistent_history` signal is preserved for genuine post-cutover
    # disagreement (initial != effective), which is what the lines 302-304
    # comment intends to gate.
    candidate_rows = risk_conn.execute(
        """
        SELECT id, checked_at, details_json
        FROM risk_state
        WHERE checked_at <= ?
          AND json_extract(details_json, '$.bankroll_truth_source') IN (
              'polymarket_wallet',
              'collateral_ledger_snapshot'
          )
        ORDER BY checked_at DESC, id DESC
        LIMIT 100
        """,
        (cutoff,),
    ).fetchall()
    if not candidate_rows:
        return {
            "status": "insufficient_history",
            "source": TRAILING_LOSS_SOURCE_DEGRADED,
            "reference": None,
        }

    for row in candidate_rows:
        if reference := _risk_state_reference_from_row(row):
            ref_dt = datetime.fromisoformat(reference["checked_at"].replace("Z", "+00:00"))
            staleness = cutoff_dt - ref_dt
            if staleness > TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE:
                status = "stale_reference"
            else:
                status = "ok"
            
            return {
                "status": status,
                "source": TRAILING_LOSS_SOURCE_OK,
                "reference": reference,
            }

    return {
        "status": "inconsistent_history",
        "source": TRAILING_LOSS_SOURCE_DEGRADED,
        "reference": None,
    }


def _trailing_loss_snapshot(
    risk_conn: sqlite3.Connection,
    *,
    now: str,
    lookback: timedelta,
    current_equity: float,
    initial_bankroll: float,
    threshold_pct: float,
) -> dict:
    reference_info = _trailing_loss_reference(risk_conn, now=now, lookback=lookback)
    status = str(reference_info["status"])
    if status not in TRAILING_LOSS_STATUSES:
        raise RuntimeError(f"unexpected trailing loss status: {status}")
    reference = reference_info.get("reference")

    # Cold-start handling (operator directive 2026-05-01 + architecture review):
    # `_trailing_loss_reference` returns "no_reference_row" / "insufficient_history"
    # on a fresh deploy — risk_state has no rows older than the lookback window
    # (e.g., 24h). The previous behaviour mapped both states to DATA_DEGRADED,
    # which the cycle reads as "block all entries" — making every fresh deploy
    # permanently undeployable until someone manually seeds risk_state. That
    # was a deadlock by design, not the structural intent: when there is no
    # history yet, no loss can have occurred against it. The right level is
    # GREEN with an explicit `bootstrap_no_history` annotation that downstream
    # observability can show. `inconsistent_history` is a different beast — it
    # means rows exist but disagree, which IS a data integrity signal worth
    # gating on, so it stays DATA_DEGRADED.
    if status in ("no_reference_row", "insufficient_history"):
        return {
            "loss": 0.0,
            "level": RiskLevel.GREEN,
            "degraded": False,
            "status": f"bootstrap_no_history:{status}",
            "source": str(reference_info["source"]),
            "reference": None,
        }
    if status not in ("ok", "stale_reference") or reference is None:
        return {
            "loss": 0.0,
            "level": RiskLevel.DATA_DEGRADED,
            "degraded": True,
            "status": f"degraded:{status}",
            "source": str(reference_info["source"]),
            "reference": None,
        }
    reference_equity = float(reference["effective_bankroll"])
    loss = round(max(0.0, reference_equity - current_equity), 2)
    level_from_loss = (
        RiskLevel.RED
        if loss > float(initial_bankroll) * float(threshold_pct)
        else RiskLevel.GREEN
    )
    
    # Staleness handling (operator directive 2026-05-01 + cold-start follow-up):
    # `stale_reference` = we have a reference row but it's older than the
    # staleness tolerance (default 2h beyond the lookback cutoff). The previous
    # behaviour mapped this to DATA_DEGRADED whenever loss didn't already trip
    # RED — meaning every fresh restart after a long unload window saw the
    # 17-hour-old reference, flagged stale, and blocked entries. This is
    # symmetric to the `no_reference_row` cold-start: the reference is from
    # before the latest deploy and doesn't reflect current state. If there's
    # no demonstrable loss against it (level_from_loss == GREEN), treat as
    # bootstrap and unblock. RED stays RED — a stale reference showing a real
    # loss is still a loss signal worth honouring.
    if status == "stale_reference":
        if level_from_loss == RiskLevel.RED:
            level = RiskLevel.RED
            is_degraded = True
        else:
            level = RiskLevel.GREEN
            is_degraded = False
            status = "bootstrap_stale_reference"
    else:
        level = level_from_loss
        is_degraded = False
    return {
        "loss": loss,
        "level": level,
        "degraded": is_degraded,
        "status": status,
        "source": str(reference_info["source"]),
        "reference": reference,
    }


def _realized_window_loss_diagnostic(
    realized_exits: list[dict] | None,
    *,
    now: str,
    lookback: timedelta,
    degraded: bool,
    source: str,
) -> dict:
    """Describe trailing realized PnL without granting it actuation authority.

    Current cash, current positions, current executable prices, and unresolved
    side effects fully describe the capital available to the next decision.
    A settled loss is already embedded in that state. Applying a second
    trailing-window veto double-counts sunk outcomes and can reject a positive
    current delta-log-wealth action solely because of when an earlier outcome
    settled.

    The diagnostic remains settlement-based, not mark-to-market. The retired
    delta calculation conflated three economically-distinct moves into "loss":
      (a) capital deployment            wallet cash -> open-position equity,
      (b) projection-pipeline reshuffle unprojected entry fill -> projected,
      (c) mark-to-market swings         of open prediction-market positions.
    This function therefore returns no RiskLevel. Missing history degrades the
    diagnostic only; it cannot block a current-evidence decision.
    """
    if degraded:
        return {
            "loss": None,
            "degraded": True,
            "status": "degraded:realized_settlement_unavailable",
            "source": source,
            "reference": None,
        }

    now_dt = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    cutoff_dt = now_dt - lookback

    windowed_pnl = 0.0
    counted = 0
    skipped_unparseable = 0
    excluded_unowned = 0
    excluded_unowned_pnl = 0.0
    for exit_row in realized_exits or []:
        ts = str(exit_row.get("exited_at") or "")
        if not ts:
            skipped_unparseable += 1
            continue
        try:
            exit_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            skipped_unparseable += 1
            continue
        if exit_dt.tzinfo is None:
            exit_dt = exit_dt.replace(tzinfo=timezone.utc)
        if cutoff_dt <= exit_dt <= now_dt:
            # Balance-only chain recovery proves inventory, not a Zeus-authored
            # strategy outcome, so exclude it from the strategy diagnostic.
            if exit_row.get("loss_eligible") is False:
                pnl = _coerce_finite_float(exit_row.get("pnl"))
                if pnl is not None:
                    excluded_unowned_pnl += float(pnl)
                excluded_unowned += 1
                continue
            pnl = _coerce_finite_float(exit_row.get("pnl"))
            if pnl is None:
                skipped_unparseable += 1
                continue
            windowed_pnl += float(pnl)
            counted += 1

    loss = round(max(0.0, -windowed_pnl), 2)
    return {
        "loss": loss,
        "degraded": False,
        "status": "ok" if counted else "no_settlements_in_window",
        "source": source,
        "reference": {
            "basis": "realized_settled_pnl",
            "window_start": cutoff_dt.isoformat(),
            "window_end": now_dt.isoformat(),
            "settlement_count": counted,
            "realized_pnl_window": round(windowed_pnl, 2),
            "skipped_unparseable": skipped_unparseable,
            "excluded_unowned_settlement_count": excluded_unowned,
            "excluded_unowned_realized_pnl": round(excluded_unowned_pnl, 2),
        },
    }


def _append_reason(bucket: dict[str, list[str]], key: str, reason: str) -> None:
    reasons = bucket.setdefault(key, [])
    if reason not in reasons:
        reasons.append(reason)


# Canonical component order for the per-tick breakdown. Pinning this list in ONE
# place (and asserting it in the test) is the structural half of the
# anti-silent-verdict antibody: a future component added to `overall_level` that
# is NOT added here would change the overall level WITHOUT appearing in the log —
# re-creating the exact "RED with no printed reason" failure. The test asserts
# that the breakdown enumerates every component fed to `overall_level`.
RISK_COMPONENT_ORDER: tuple[str, ...] = (
    "brier",
    "settlement_quality",
    "execution_quality",
    "strategy_signal",
    "collateral_identity",
    "portfolio_consistency",
    "unresolved_exposure",
)


def _component_breakdown(
    overall: RiskLevel,
    component_levels: dict[str, RiskLevel],
    component_detail: dict[str, str],
) -> tuple[str, str]:
    """Build (driven_by, breakdown_str) for the per-tick component log.

    `driven_by` is the comma-joined set of components whose level equals the
    overall level (the load-bearing component(s)) — empty string when GREEN.
    `breakdown_str` lists EVERY component's level, annotating non-GREEN ones with
    their driving number so the daemon log alone answers "why is this tick RED?".

    Pure function (no DB / no logging) so the anti-silent-verdict antibody is
    unit-testable and the component enumeration is asserted against
    RISK_COMPONENT_ORDER.
    """
    driving = sorted(
        name
        for name in RISK_COMPONENT_ORDER
        if component_levels.get(name) == overall and overall != RiskLevel.GREEN
    )
    parts = []
    for name in RISK_COMPONENT_ORDER:
        lvl = component_levels[name]
        if lvl != RiskLevel.GREEN:
            parts.append(f"{name}={lvl.value}[{component_detail.get(name, '')}]")
        else:
            parts.append(f"{name}={lvl.value}")
    return ",".join(driving) or "none", " | ".join(parts)


def _canonical_recent_exits_from_settlement_rows(rows: list[dict]) -> list[dict]:
    exits: list[dict] = []
    for row in rows:
        if not row.get("metric_ready", False):
            continue
        pnl = row.get("pnl")
        if pnl is None:
            continue
        strategy = str(row.get("strategy") or row.get("strategy_key") or "")
        loss_eligible = strategy != "chain_only_reconciliation"
        exits.append(
            {
                "city": str(row.get("city") or ""),
                "bin_label": str(row.get("range_label") or row.get("winning_bin") or ""),
                "target_date": str(row.get("target_date") or ""),
                "direction": str(row.get("direction") or ""),
                "token_id": "",
                "no_token_id": "",
                "exit_reason": str(row.get("exit_reason") or "SETTLEMENT"),
                "exited_at": str(row.get("exited_at") or row.get("settled_at") or ""),
                "pnl": float(pnl),
                "strategy_key": strategy,
                "loss_eligible": loss_eligible,
                "loss_exclusion_reason": (
                    "balance_only_chain_recovery_has_no_entry_authority"
                    if not loss_eligible
                    else ""
                ),
            }
        )
    return exits


def _current_mode_realized_exits(
    conn: sqlite3.Connection,
    *,
    settlement_rows: list[dict] | None = None,
    env: str | None = None,
) -> tuple[list[dict], str, bool]:
    """Returns (exits, source_name, degraded)."""
    if conn is None:
        return [], "none", False
    if settlement_rows is not None:
        exits = _canonical_recent_exits_from_settlement_rows(settlement_rows)
        degraded = any(bool(row.get("is_degraded", False)) for row in settlement_rows)
        return exits, "authoritative_settlement_rows", degraded and not exits

    outcome_fact_available = True
    try:
        rows = conn.execute(
            """
            SELECT strategy_key, city, target_date, position_id, exit_reason, settled_at, pnl
            FROM outcome_fact
            WHERE pnl IS NOT NULL
            ORDER BY settled_at DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        outcome_fact_available = False
        rows = []
    if rows:
        return (
            [
                {
                    "city": str(row["city"] or ""),
                    "bin_label": str(row["position_id"] or ""),
                    "target_date": str(row["target_date"] or ""),
                    "direction": "",
                    "token_id": "",
                    "no_token_id": "",
                    "exit_reason": str(row["exit_reason"] or "SETTLEMENT"),
                    "exited_at": str(row["settled_at"] or ""),
                    "pnl": float(row["pnl"]),
                    "strategy_key": str(row["strategy_key"] or ""),
                }
                for row in rows
            ],
            "outcome_fact",
            False,
        )
    if outcome_fact_available:
        # Table exists but is empty — valid empty result, not degradation
        return [], "outcome_fact", False

    # Degradation: outcome_fact unavailable, falling back to chronicle
    logger.warning("outcome_fact unavailable — degrading realized exits to chronicle")
    chronicle_env = str(env or get_mode()).strip()
    try:
        rows = conn.execute(
            """
            SELECT json_extract(details_json, '$.city') AS city,
                   json_extract(details_json, '$.range_label') AS range_label,
                   json_extract(details_json, '$.target_date') AS target_date,
                   json_extract(details_json, '$.direction') AS direction,
                   json_extract(details_json, '$.exit_reason') AS exit_reason,
                   timestamp AS exited_at,
                   json_extract(details_json, '$.pnl') AS pnl
            FROM chronicle
            WHERE event_type = 'SETTLEMENT'
              AND env = ?
              AND trade_id IS NOT NULL
              AND id IN (
                SELECT MAX(id)
                FROM chronicle
                WHERE event_type = 'SETTLEMENT'
                  AND env = ?
                  AND trade_id IS NOT NULL
                GROUP BY trade_id
              )
            ORDER BY timestamp DESC
            """,
            (chronicle_env, chronicle_env),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if rows:
        return (
            [
                {
                    "city": str(row["city"] or ""),
                    "bin_label": str(row["range_label"] or ""),
                    "target_date": str(row["target_date"] or ""),
                    "direction": str(row["direction"] or ""),
                    "token_id": "",
                    "no_token_id": "",
                    "exit_reason": str(row["exit_reason"] or "SETTLEMENT"),
                    "exited_at": str(row["exited_at"] or ""),
                    "pnl": float(row["pnl"]),
                }
                for row in rows
                if row["pnl"] is not None
            ],
            "chronicle_dedup",
            True,
        )

    return [], "none", False


def _strategy_settlement_summary(rows: list[dict]) -> dict[str, dict]:
    """Aggregate settlement rows into per-strategy counts and PnL.

    K1 invariant (bug #1/#2): this aggregation MUST be deduped by
    trade_id. Settlement rows can come from multiple upstream sources
    (canonical position_events and historical decision_log artifacts), and
    the same underlying trade may appear in more than one source or in
    multiple batches of the same source. Prior
    to dedup, opening_inertia would show 19 settlements on
    2026-04-11 while the canonical truth was 6 unique positions, because
    two decision_log settlement batches (19:43 and 20:43) each recorded
    the same 6 positions. The two bugs are now fixed at the writer layer
    but historical decision_log rows from before the fix still contain
    duplicates, so the reader must dedup defensively.

    Dedup policy: for each trade_id, keep the FIRST row encountered in
    iteration order. Callers should pass rows ordered by occurred_at ASC
    if they want the earliest settlement record; the current caller
    passes most-recent-first order from query_settlement_events, which
    means the last recorded settlement wins. That is fine as long as
    settlement is idempotent at the writer layer (bug #9 fix).
    """
    summary: dict[str, dict] = {}
    seen_trade_ids: set[str] = set()
    for row in rows:
        trade_id = str(row.get("trade_id") or row.get("runtime_trade_id") or "")
        if not trade_id:
            # Rows without a trade_id cannot be deduped; fall back to
            # including them so we do not silently drop data. This should
            # be rare after the settlement writer fixes land.
            pass
        elif trade_id in seen_trade_ids:
            continue
        else:
            seen_trade_ids.add(trade_id)

        strategy = str(row.get("strategy") or "unclassified")
        bucket = summary.setdefault(
            strategy,
            {
                "count": 0,
                "pnl": 0.0,
                "wins": 0,
                # K2 rename (bug #3): this is trade profitability (wins/count),
                # distinct from probability_directional_accuracy at the
                # risk.details top level. The old shared 'accuracy' key name
                # caused LLM reporters to conflate the two metrics.
                "trade_profitability_rate": None,
            },
        )
        bucket["count"] += 1
        pnl = row.get("pnl")
        if pnl is not None:
            bucket["pnl"] += float(pnl)
        outcome = row.get("outcome")
        if outcome == 1:
            bucket["wins"] += 1

    for strategy, bucket in summary.items():
        count = bucket["count"]
        bucket["pnl"] = round(bucket["pnl"], 2)
        bucket["trade_profitability_rate"] = (
            round(bucket["wins"] / count, 4) if count else None
        )
    return summary


_ENTRY_EXECUTION_LOOKBACK = timedelta(hours=48)

# Entry events whose presence proves the order actually reached a terminal
# outcome (as opposed to POSITION_OPEN_INTENT, which only proves we tried).
_TERMINAL_ENTRY_COUNTERS = frozenset({"filled", "rejected", "voided"})

# Freshness horizon for the per-strategy execution_decay gate (2026-07-05).
# The 48h _ENTRY_EXECUTION_LOOKBACK decides which events COUNT toward a
# strategy's fill-rate; this shorter horizon decides whether that count is a
# CURRENT verdict. RiskGuard ticks every few minutes and a strategy in live
# execution produces terminal events far more often than every two hours, so a
# strategy whose newest terminal event is already older than this has stopped
# executing — which is exactly what happens the moment the gate itself blocks
# the lane. Without this bound the gate is self-perpetuating: once a strategy
# is STRATEGY_POLICY_GATED it emits no new terminal events, its fill-rate
# window freezes at the tripping ratio, and the gate re-fires every tick for
# the full 48h lookback, forbidding the very fills that would clear it (live
# incident: forecast_qkernel_entry, fill_rate=0.1667/observed=12, zero new
# POSITION_OPEN_INTENT since issuance, effective_until=NULL). Two hours is long
# enough to ride out a brief quiet spell and short enough that a gated-then-
# quiet strategy ages out, clears, and re-earns a verdict from fresh evidence.
# Same current-evidence / walk-forward principle as _STRATEGY_BRIER_MIN_SAMPLE
# and the 48h lookback itself.
_EXECUTION_DECAY_FRESH_HORIZON = timedelta(hours=2)


def _entry_execution_summary(
    conn: sqlite3.Connection,
    *,
    limit: int = 200,
    now: str | None = None,
) -> dict:
    """Entry execution summary from canonical position_events.

    Time-bounded (2026-07-05): execution quality measures the CURRENT
    execution machinery, so evidence older than the lookback is excluded.
    Without the bound, LIMIT-200 reached back across deploy regimes and a
    dead pipeline's fill rate (0.14 from 07-01..07-03 legacy rests) kept
    gating strategies days after the machinery it measured was replaced —
    the same stale-evidence failure the walk-forward law forbids.
    """
    now_dt = (
        datetime.fromisoformat(now.replace("Z", "+00:00"))
        if now
        else datetime.now(timezone.utc)
    )
    cutoff = (now_dt - _ENTRY_EXECUTION_LOOKBACK).isoformat()
    try:
        rows = conn.execute(
            """
            SELECT event_type, strategy_key, occurred_at
            FROM position_events
            WHERE event_type IN (
                'POSITION_OPEN_INTENT',
                'ENTRY_ORDER_FILLED',
                'ENTRY_ORDER_REJECTED',
                'ENTRY_ORDER_VOIDED'
            )
              AND datetime(occurred_at) >= datetime(?)
            ORDER BY datetime(occurred_at) DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    overall = {
        "attempted": 0,
        "filled": 0,
        "rejected": 0,
        "voided": 0,
        "terminal_observed": 0,
        "fill_rate": None,
        "newest_terminal_at": None,
    }
    by_strategy: dict[str, dict] = {}
    mapping = {
        "POSITION_OPEN_INTENT": "attempted",
        "ENTRY_ORDER_FILLED": "filled",
        "ENTRY_ORDER_REJECTED": "rejected",
        "ENTRY_ORDER_VOIDED": "voided",
    }
    for row in rows:
        event_type = str(row["event_type"])
        counter_key = mapping.get(event_type)
        if counter_key is None:
            continue
        strategy = str(row["strategy_key"] or "unclassified")
        bucket = by_strategy.setdefault(
            strategy,
            {
                "attempted": 0,
                "filled": 0,
                "rejected": 0,
                "voided": 0,
                "terminal_observed": 0,
                "fill_rate": None,
                "newest_terminal_at": None,
            },
        )
        overall[counter_key] += 1
        bucket[counter_key] += 1
        if counter_key in _TERMINAL_ENTRY_COUNTERS:
            # Rows arrive newest-first (ORDER BY datetime(occurred_at) DESC), so
            # the first terminal event seen is the newest — for the global
            # overall and for each strategy bucket.
            occurred_at = str(row["occurred_at"])
            if overall["newest_terminal_at"] is None:
                overall["newest_terminal_at"] = occurred_at
            if bucket["newest_terminal_at"] is None:
                bucket["newest_terminal_at"] = occurred_at

    def _finalize(bucket: dict) -> None:
        terminal_observed = bucket["filled"] + bucket["rejected"] + bucket["voided"]
        bucket["terminal_observed"] = terminal_observed
        bucket["fill_rate"] = (
            round(bucket["filled"] / terminal_observed, 4) if terminal_observed else None
        )

    _finalize(overall)
    for bucket in by_strategy.values():
        _finalize(bucket)
    return {"overall": overall, "by_strategy": by_strategy}


def _execution_decay_verdict_is_current(
    newest_terminal_at: str | None, *, now: datetime
) -> bool:
    """Whether a strategy's fill-rate window is fresh enough to gate on.

    The per-strategy execution_decay gate must reflect the CURRENT execution
    machinery, not a window frozen by the gate itself. Returns True only when
    the strategy's newest terminal entry event is within
    ``_EXECUTION_DECAY_FRESH_HORIZON`` of ``now``. A stale window (no terminal
    event in the horizon — the state a gated strategy is trapped in, because it
    can no longer place the orders that would produce terminal events) yields
    no current verdict, so the gate does not re-fire and the strategy can age
    out, clear, and re-earn admission from fresh evidence. See
    ``_EXECUTION_DECAY_FRESH_HORIZON`` for the live self-perpetuation incident.
    """
    if not newest_terminal_at:
        return False
    try:
        parsed = datetime.fromisoformat(str(newest_terminal_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed) <= _EXECUTION_DECAY_FRESH_HORIZON


def _riskguard_brier_metric_rows(rows: list[dict], *, limit: int = RISKGUARD_SETTLEMENT_LIMIT) -> list[dict]:
    """Return learning-ready settlement rows for probability quality metrics.

    Settlement truth quality and probability learning lineage are different
    surfaces. A SETTLED event with complete canonical settlement payload is
    valid settlement truth, but if it lacks the decision snapshot it must not
    displace a learning-ready row in the Brier sample. Settlement backfills can
    be newest by occurred_at while carrying no decision snapshot; using them as
    the latest Brier rows turns a data repair into a false reduce-only halt.

    A frozen probability value without its ``venue_commands.q_version`` is also
    not learning lineage. It cannot prove which q authorized the order, so it is
    diagnostic-only and may not convict the currently executing probability
    system. ``_bind_brier_probability_identities`` establishes that proof before
    this filter runs.
    """

    metric_rows: list[dict] = []
    for row in rows:
        if not row.get("learning_snapshot_ready", False):
            continue
        if not row.get("metric_ready", True):
            continue
        if not row.get("probability_identity_ready", False):
            continue
        if row.get("p_posterior") is None or row.get("outcome") is None:
            continue
        metric_rows.append(row)
        if len(metric_rows) >= limit:
            break
    return metric_rows


def _bind_brier_probability_identities(
    conn: sqlite3.Connection,
    rows: list[dict],
) -> list[dict]:
    """Bind settled forecasts to one unambiguous entry-time q identity.

    ``p_posterior`` is only a number. A Brier verdict becomes evidence about a
    probability system only when the actual ENTRY command carries exactly one
    non-empty, non-conflicting ``q_version``. Missing and ambiguous identities
    remain visible on the settlement rows but are excluded from the risk verdict.
    """

    output = [dict(row) for row in rows]
    unresolved = {
        str(row.get("trade_id") or "")
        for row in output
        if not (
            row.get("probability_identity_ready") is True
            and str(row.get("entry_q_version") or "").strip()
        )
        and str(row.get("trade_id") or "").strip()
    }
    bindings: dict[str, list[str | None]] = {trade_id: [] for trade_id in unresolved}
    schema_ready = False
    if unresolved and _table_exists(conn, "venue_commands"):
        try:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(venue_commands)").fetchall()
            }
            schema_ready = {"position_id", "intent_kind", "q_version"}.issubset(columns)
        except sqlite3.Error:
            schema_ready = False
    if schema_ready:
        trade_ids = sorted(unresolved)
        for start in range(0, len(trade_ids), 500):
            chunk = trade_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            try:
                command_rows = conn.execute(
                    "SELECT position_id,q_version FROM venue_commands "
                    "WHERE intent_kind='ENTRY' AND position_id IN ("
                    f"{placeholders})",
                    tuple(chunk),
                ).fetchall()
            except sqlite3.Error:
                schema_ready = False
                break
            for command_row in command_rows:
                position_id = str(command_row[0] or "")
                q_version = str(command_row[1] or "").strip() or None
                if position_id in bindings:
                    bindings[position_id].append(q_version)

    for row in output:
        if (
            row.get("probability_identity_ready") is True
            and str(row.get("entry_q_version") or "").strip()
        ):
            continue
        trade_id = str(row.get("trade_id") or "").strip()
        versions = bindings.get(trade_id, [])
        nonempty = {version for version in versions if version is not None}
        missing_count = sum(version is None for version in versions)
        if not schema_ready:
            reason = "venue_q_version_schema_unavailable"
        elif not versions:
            reason = "entry_command_missing"
        elif missing_count:
            reason = "entry_q_version_missing"
        elif len(nonempty) != 1:
            reason = "entry_q_version_conflicting"
        else:
            row["probability_identity_ready"] = True
            row["entry_q_version"] = next(iter(nonempty))
            row["probability_identity_source"] = "venue_commands.q_version"
            row["probability_identity_blocked_reason"] = ""
            continue
        row["probability_identity_ready"] = False
        row["entry_q_version"] = next(iter(nonempty)) if len(nonempty) == 1 else ""
        row["probability_identity_source"] = "venue_commands.q_version"
        row["probability_identity_blocked_reason"] = reason
    return output


# Below this many settled observations a per-strategy Brier score is noise,
# not a verdict (a single loss at p=0.6 scores 0.36 > brier_red). Thin
# strategies are still counted in the portfolio pool and the loss gates.
_STRATEGY_BRIER_MIN_SAMPLE = 10


def _strategy_brier_breakdown(rows: list[dict], thresholds: dict) -> dict[str, object]:
    """Per-strategy probability-quality attribution for localized protection.

    Portfolio-level Brier still protects the system. When the breach is only
    YELLOW and every learning-ready row carries a canonical strategy key, the
    bad strategy can be halted through durable ``risk_actions`` instead of
    freezing every other strategy. Unknown/unclassified rows keep the global
    YELLOW because there is no safe strategy-local enforcement target.
    """

    buckets: dict[str, dict[str, object]] = {}
    unclassified_count = 0
    for row in rows:
        strategy = str(row.get("strategy") or "unclassified")
        if strategy not in CANONICAL_STRATEGY_KEYS:
            unclassified_count += 1
            continue
        bucket = buckets.setdefault(strategy, {"p": [], "o": []})
        bucket["p"].append(float(row["p_posterior"]))  # type: ignore[index, union-attr]
        bucket["o"].append(int(row["outcome"]))  # type: ignore[index, union-attr]

    by_strategy: dict[str, dict[str, object]] = {}
    degraded: dict[str, dict[str, object]] = {}
    for strategy, bucket in sorted(buckets.items()):
        p_values = list(bucket["p"])  # type: ignore[index]
        outcomes = list(bucket["o"])  # type: ignore[index]
        score = brier_score(p_values, outcomes)
        level = evaluate_brier(score, thresholds)
        sample_size = len(p_values)
        payload = {
            "sample_size": sample_size,
            "brier": round(float(score), 6),
            "level": level.value,
        }
        # Minimum-evidence floor (2026-07-05): a per-strategy Brier verdict
        # below n=10 is statistically empty — one confident settled loss
        # scores far above any threshold (p=0.79 loss -> (0.79-0)^2 = 0.6241)
        # and would gate a whole lane on a single coin flip (live
        # incident: forecast_qkernel_entry gated RED on n=1 while its
        # candidates showed the book's best positive edges). Thin strategies
        # stay in by_strategy for observability but never enter
        # degraded_strategies; portfolio-level Brier (which pools them) and
        # the loss gates still bind. Same attribute-don't-convict principle
        # as ORANGE/execution localization; K3's coverage min_n=30 is the
        # calibration-lane analogue.
        if sample_size < _STRATEGY_BRIER_MIN_SAMPLE:
            payload["level"] = RiskLevel.GREEN.value
            payload["thin_sample_no_verdict"] = True
            by_strategy[strategy] = payload
            continue
        by_strategy[strategy] = payload
        if level != RiskLevel.GREEN:
            degraded[strategy] = payload

    return {
        "by_strategy": by_strategy,
        "degraded_strategies": degraded,
        "unclassified_count": unclassified_count,
        "classified_count": sum(int(row["sample_size"]) for row in by_strategy.values()),
    }


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _sync_riskguard_strategy_gate_actions(
    conn: sqlite3.Connection,
    recommended_strategy_gate_reasons: dict[str, list[str]],
    *,
    issued_at: str,
) -> dict[str, int | str]:
    if not _table_exists(conn, "risk_actions"):
        logger.info("RiskGuard durable risk_actions table unavailable; skipping action emission")
        return {
            "status": "skipped_missing_table",
            "emitted_count": 0,
            "expired_count": 0,
        }

    recommended = {
        strategy: "|".join(sorted(reasons))
        for strategy, reasons in sorted(recommended_strategy_gate_reasons.items())
    }

    existing_rows = conn.execute(
        """
        SELECT action_id, strategy_key
        FROM risk_actions
        WHERE source = 'riskguard'
          AND action_type = 'gate'
          AND status = 'active'
        """
    ).fetchall()
    existing_by_strategy = {str(row["strategy_key"]): str(row["action_id"]) for row in existing_rows}
    expired_count = 0

    for strategy, reason in recommended.items():
        action_id = existing_by_strategy.get(strategy, f"riskguard:gate:{strategy}")
        conn.execute(
            """
            INSERT INTO risk_actions (
                action_id,
                strategy_key,
                action_type,
                value,
                issued_at,
                effective_until,
                reason,
                source,
                precedence,
                status
            ) VALUES (?, ?, 'gate', 'true', ?, NULL, ?, 'riskguard', 50, 'active')
            ON CONFLICT(action_id) DO UPDATE SET
                strategy_key = excluded.strategy_key,
                value = excluded.value,
                issued_at = excluded.issued_at,
                effective_until = NULL,
                reason = excluded.reason,
                precedence = excluded.precedence,
                status = 'active'
            """,
            (action_id, strategy, issued_at, reason),
        )

    for strategy, action_id in existing_by_strategy.items():
        if strategy in recommended:
            continue
        conn.execute(
            """
            UPDATE risk_actions
            SET effective_until = ?,
                status = 'expired'
            WHERE action_id = ?
            """,
            (issued_at, action_id),
        )
        expired_count += 1

    return {
        "status": "emitted",
        "emitted_count": len(recommended),
        "expired_count": expired_count,
    }


def _confirm_active_durable_strategy_gates(
    conn: sqlite3.Connection,
    strategies: list[str],
) -> dict[str, bool]:
    """Read-after-write confirmation that each strategy holds an ACTIVE gate.

    ORANGE localization (unlike the pre-existing YELLOW localization) treats
    the durable ``risk_actions`` gate as a SAFETY PRECONDITION rather than
    lock-tolerant auxiliary bookkeeping: a write that CLAIMS emission but did
    not actually land an active row for a degraded strategy must NOT be
    trusted. This queries the SAME connection the write used (uncommitted
    writes are visible to later reads on that same connection), so this is a
    true same-cycle read-after-write check, not a check against stale/committed
    state from a prior tick.
    """
    if not strategies:
        return {}
    if not _table_exists(conn, "risk_actions"):
        return {strategy: False for strategy in strategies}
    confirmed: dict[str, bool] = {}
    for strategy in strategies:
        row = conn.execute(
            """
            SELECT 1 FROM risk_actions
            WHERE source = 'riskguard'
              AND action_type = 'gate'
              AND status = 'active'
              AND strategy_key = ?
            LIMIT 1
            """,
            (strategy,),
        ).fetchone()
        confirmed[strategy] = row is not None
    return confirmed


def _residual_active_portfolio_brier_level(
    brier_metric_rows: list[dict],
    thresholds: dict,
    excluded_strategies: set[str],
) -> tuple[RiskLevel, float, int]:
    """Recompute portfolio Brier EXCLUDING durably-gated strategies' rows.

    This is the ORANGE-localization residual check (condition #3): the
    strategies already scoped-out behind a confirmed durable gate are removed
    from the sample, and the REMAINING ("active") portfolio must itself land
    GREEN before admission may be relaxed from the global ORANGE. An empty
    residual sample (no remaining rows) is treated as GREEN, matching the
    existing convention that an empty Brier sample is not itself a breach.
    """
    residual_rows = [
        row for row in brier_metric_rows
        if str(row.get("strategy") or "unclassified") not in excluded_strategies
    ]
    # Minimum-evidence floor, pool edition (2026-07-05): a strategy below
    # _STRATEGY_BRIER_MIN_SAMPLE carries NO verdict (same doctrine as the
    # per-strategy breakdown) — its rows must not vote in the residual
    # either. Live incident: two n=1 settled losses (Brier 0.92 / 0.79)
    # dragged an otherwise-GREEN residual to YELLOW, defeating ORANGE
    # localization and freezing the whole book on two coin flips — the
    # exact failure the per-strategy floor fixed, one level up. Thin
    # strategies remain visible via thin_sample_excluded_strategies; the
    # daily/weekly realized-loss gates still bind on their outcomes.
    rows_by_strategy: dict[str, list[dict]] = {}
    for row in residual_rows:
        rows_by_strategy.setdefault(str(row.get("strategy") or "unclassified"), []).append(row)
    thin_excluded = sorted(
        strategy
        for strategy, rows in rows_by_strategy.items()
        if len(rows) < _STRATEGY_BRIER_MIN_SAMPLE
    )
    scored_rows = [
        row
        for strategy, rows in rows_by_strategy.items()
        if strategy not in thin_excluded
        for row in rows
    ]
    residual_p = [float(row["p_posterior"]) for row in scored_rows]
    residual_o = [int(row["outcome"]) for row in scored_rows]
    residual_score = brier_score(residual_p, residual_o) if residual_p else 0.0
    residual_level = evaluate_brier(residual_score, thresholds) if residual_p else RiskLevel.GREEN
    return residual_level, residual_score, len(residual_p), thin_excluded


def _refresh_riskguard_auxiliary_bookkeeping(
    zeus_conn: sqlite3.Connection,
    *,
    recommended_strategy_gate_reasons: dict[str, list[str]],
    now: str,
    position_view: dict | None = None,
) -> tuple[dict, dict, dict]:
    """Run the RiskGuard AUXILIARY bookkeeping writes/reads, lock-tolerantly.

    Root cause (live 2026-06-13, docs/evidence/no_order_root_2026-06-13/diagnosis.md):
    the RiskGuard tick computes its risk LEVEL purely from READS (settlement /
    realized-exit / Brier / loss snapshots, already gathered before this call).
    These two bookkeeping operations — ``_sync_riskguard_strategy_gate_actions``
    (DELETE/INSERT into ``risk_actions``) and ``refresh_strategy_health``
    (DELETE+INSERT into ``strategy_health``) — are WRITE transactions on the
    zeus_trades write lock. When a concurrent writer (reactor / ingest, in
    another process) holds that WAL write lock, these AUXILIARY writes raise
    ``"database is locked"``. The pre-fix code let that bubble to the top-level
    tick handler, which RETRIED then DEGRADED to DATA_DEGRADED — vetoing every
    post-Kelly tradeable bet on the GREEN-only entry gate even though risk was
    perfectly KNOWABLE (the level reads had all succeeded). This is the
    no-conn-across-IO / writer-contention storm class (9f70e9c581).

    THE LEVEL MUST NOT DEGRADE because a bookkeeping write lost the WAL write
    lock. So a ``"database is locked"`` here is caught, the zeus_conn write txn is
    rolled back (so the locked/partial bookkeeping txn never carries into the
    tick's final ``zeus_conn.commit()``), and the tick proceeds to compute and
    persist a FRESH FULL risk_state row from the reads it already has.

    FAIL-CLOSED IS PRESERVED (AGENTS.md risk-levels law): only the SPURIOUS
    writer-contention lock on these two bookkeeping operations is absorbed — a
    bookkeeping write losing the WAL lock is NOT a "missing or stale truth input".
    A lock (or any other failure) on the genuine truth READS happens EARLIER in
    ``_tick_once`` and still propagates to the top-level handler → retry →
    DATA_DEGRADED. A NON-lock OperationalError here (e.g. a genuine schema fault)
    is re-raised loudly — never swallowed.

    Returns ``(durable_action_status, strategy_health_refresh,
    strategy_health_snapshot)``. On a caught lock the three carry a
    ``skipped_dependency_lock`` status so the tick's observability fields record
    that the bookkeeping was skipped this cycle (the LEVEL is unaffected).
    """
    try:
        durable_action_status = _sync_riskguard_strategy_gate_actions(
            zeus_conn,
            recommended_strategy_gate_reasons,
            issued_at=now,
        )
        strategy_health_refresh = refresh_strategy_health(
            zeus_conn,
            as_of=now,
            position_view=position_view,
        )
        strategy_health_snapshot = query_strategy_health_snapshot(
            zeus_conn,
            now=now,
        )
        return durable_action_status, strategy_health_refresh, strategy_health_snapshot
    except sqlite3.OperationalError as exc:
        if not _is_sqlite_database_locked(exc):
            # A genuine bookkeeping fault (e.g. schema corruption) must NOT be
            # masked as a lock — propagate so the top-level handler surfaces it.
            raise
        # The bookkeeping write lost the WAL write lock to a concurrent writer.
        # Roll back the (locked/partial) zeus_conn write txn so it cannot poison
        # the tick's final zeus_conn.commit(); the risk LEVEL is computed from the
        # reads we ALREADY have, so we still write a fresh full risk_state row.
        try:
            zeus_conn.rollback()
        except Exception:  # noqa: BLE001 — best-effort; rollback of a stub/locked conn
            pass
        logger.warning(
            "RiskGuard auxiliary bookkeeping (risk_actions / strategy_health) lost the "
            "zeus_trades write lock to a concurrent writer (database is locked); SKIPPING "
            "the bookkeeping refresh this cycle and proceeding with the level computed from "
            "the metric reads. The risk LEVEL is NOT degraded by a bookkeeping write lock. "
            "error=%s",
            exc,
        )
        skipped = {
            "status": "skipped_dependency_lock",
            "emitted_count": 0,
            "expired_count": 0,
        }
        skipped_refresh = {
            "status": "skipped_dependency_lock",
            "table": "strategy_health",
            "rows_written": 0,
            "as_of": now,
            "settlement_authority_missing_tables": [],
        }
        skipped_snapshot = {
            "status": "skipped_dependency_lock",
            "table": "strategy_health",
            "by_strategy": {},
            "stale_strategy_keys": [],
        }
        return skipped, skipped_refresh, skipped_snapshot


def init_risk_db(conn: sqlite3.Connection) -> None:
    """Create risk_state tables."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS risk_state (
            id INTEGER PRIMARY KEY,
            level TEXT NOT NULL,
            brier REAL,
            accuracy REAL,
            win_rate REAL,
            details_json TEXT,
            checked_at TEXT NOT NULL
        );
    """)
    # CATEGORY ANTIBODY (Fitz #5): executescript() can NULL the C-level busy
    # handler on some Python/SQLite builds, leaving this risk_state.db handle at a
    # 0 ms wait budget so the immediately-following reads/writes (every tick(),
    # get_current_level(), lock-attestation) raise "database is locked" instead of
    # waiting. Re-apply the SQL-level busy_timeout here so the factory's wait
    # budget survives the schema-ensure. Best-effort: a stub conn in tests may not
    # implement execute(), so failure is swallowed (the factory already set it).
    try:
        from src.state.db import _apply_busy_timeout as _apply_db_busy_timeout
        _apply_db_busy_timeout(conn)
    except Exception:  # noqa: BLE001 - never let timeout re-apply break schema init
        pass
    # B5: Add force_exit_review column if missing (code-level migration, no raw ALTER)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(risk_state)").fetchall()}
    if "force_exit_review" not in cols:
        try:
            conn.execute("ALTER TABLE risk_state ADD COLUMN force_exit_review INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # concurrent process already added it


def _is_sqlite_database_locked(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower()


def _riskguard_dependency_lock_retries() -> int:
    """Within-tick retry budget for a transient dependency-DB lock.

    Fitz #5 lock-CATEGORY kill (2026-06-08): the metrics read on zeus_trades +
    ATTACHed world/forecasts (all WAL, written concurrently by live-trading and
    the forecast/data-ingest daemons) loses a transient WAL/checkpoint window on
    ~half of ticks even with the 30s busy_timeout (a bulk forecast write can hold
    the single WAL write lock past the wait). Giving up on the FIRST lock made the
    daemon fail ~half its ticks, so genuine fresh full_risk rows aged past the
    5-min freshness window and get_current_level flapped to DATA_DEGRADED — the
    GREEN-only entry gate then blocked ALL new entries (operator zero-trade
    2026-06-08). Retrying the read within the same tick recovers a genuine fresh
    row on nearly every tick. Default 3 (4 attempts); 0 restores the pre-fix
    single-attempt behavior.
    """
    try:
        return max(0, int(os.environ.get("ZEUS_RISKGUARD_DEP_LOCK_RETRIES", "3")))
    except ValueError:
        return 3


def _riskguard_dependency_lock_backoff_seconds(attempt: int) -> float:
    """Backoff before re-attempting a lock-failed tick read (attempt is 0-based).

    Linear 1.5s, 3.0s, 4.5s ... capped at 8s. Total worst-case wait across the
    default 3 retries is ~9s — well inside the 60s tick cadence — so a contended
    tick still completes long before the next one is due.
    """
    try:
        base = float(os.environ.get("ZEUS_RISKGUARD_DEP_LOCK_BACKOFF_BASE_S", "1.5"))
    except ValueError:
        base = 1.5
    if base < 0.0:
        base = 1.5
    return min(base * (attempt + 1), 8.0)


def _riskguard_dependency_busy_timeout_ms() -> int:
    """Short per-attempt busy_timeout for the metrics dependency read.

    Fitz #5 follow-up (2026-06-08): the within-tick retry fixed the lock-DEGRADE
    flap, but combined with the global 30s busy_timeout a locked tick waited up to
    ~2 min (30s x retries) before producing ANY risk_state row. That pushed the
    inter-row gap past the 5-min get_current_level staleness floor and created a
    SECOND flap (stale row -> RISK_GUARD_BLOCKED on the entry gate). A SHORT
    per-attempt wait makes a contended attempt FAIL FAST so the retry loop — or the
    fast preserve-GREEN attestation — completes in seconds, keeping risk_state rows
    well inside the freshness window. A genuine read between WAL spikes needs only a
    brief uncontended lock window (sub-second when uncontended), so genuine GREEN
    rows still land; sustained spikes fall to the preserve-GREEN attestation, which
    is the correct conservative behaviour. Default 4000ms; floored at 500ms.
    """
    try:
        return max(500, int(os.environ.get("ZEUS_RISKGUARD_DEP_BUSY_TIMEOUT_MS", "4000")))
    except ValueError:
        return 4000


def _close_conn(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _rollback_and_close(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass
    _close_conn(conn)


def _full_risk_row_is_fresh(row: sqlite3.Row, *, now: datetime) -> bool:
    try:
        details = json.loads(row["details_json"]) if row["details_json"] else {}
    except (json.JSONDecodeError, TypeError):
        details = {}
    if isinstance(details, dict) and details.get("riskguard_degraded_reason"):
        return False
    checked_at = datetime.fromisoformat(str(row["checked_at"]).replace("Z", "+00:00"))
    return (now - checked_at).total_seconds() <= 300


def _latest_fresh_full_risk_row(conn: sqlite3.Connection, *, now: datetime) -> sqlite3.Row | None:
    rows = conn.execute(
        """
        SELECT level, checked_at, details_json, force_exit_review
        FROM risk_state
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()
    for row in rows:
        if _full_risk_row_is_fresh(row, now=now):
            return row
    return None


def _persist_dependency_db_locked_attestation(exc: sqlite3.OperationalError) -> RiskLevel:
    """Persist a fresh degraded row when a RiskGuard dependency DB is locked.

    A locked dependency surface means RiskGuard cannot run full metrics. If a
    previous full risk attestation is still fresh, preserve that level and mark
    only the metrics refresh degraded. If no full attestation is fresh, degrade
    to DATA_DEGRADED.
    """
    now = datetime.now(timezone.utc)
    now_ts = now.isoformat()
    risk_conn = get_connection(RISK_DB_PATH, write_class="live")
    try:
        init_risk_db(risk_conn)
        previous_full = _latest_fresh_full_risk_row(risk_conn, now=now)
        if previous_full is None:
            level = RiskLevel.DATA_DEGRADED
            force_exit_review = 0
            details = {
                "status": "dependency_db_locked",
                "riskguard_degraded_reason": "dependency_db_locked",
                "bankroll_truth_source": "polymarket_wallet",
                "dependency_db_lock_error": str(exc),
                "full_metrics_status": "unavailable_no_fresh_full_risk_row",
            }
        else:
            # A TRANSIENT dependency lock does NOT mean risk is unknowable. The
            # branch is reached ONLY when a FULL risk attestation exists within the
            # freshness window (_full_risk_row_is_fresh = 5 min); daily-loss,
            # settlement-quality and Brier are slow-moving and do not change in that
            # window, so that fresh level is still valid. Preserve it VERBATIM so a
            # momentary lock cannot block the GREEN-only entry gate — this is the
            # weeks-stable behavior; the prior max(previous_level, DATA_DEGRADED)
            # floor downgraded a fresh GREEN to DATA_DEGRADED on EVERY transient lock
            # and blocked all entries (operator-reported regression 2026-06-08).
            # Safety is preserved by the freshness window itself: once the last full
            # row ages past 5 min (persistent lock / genuine truth gap), previous_full
            # is None above and this path degrades to DATA_DEGRADED. RED/ORANGE/YELLOW
            # are unaffected (they are >= DATA_DEGRADED; only GREEN was downgraded).
            previous_level = RiskLevel(previous_full["level"])
            level = previous_level
            # force_exit_review is a halt signal — carry it forward (never clear it
            # under degraded truth); a previous RED keeps its force-exit posture.
            force_exit_review = int(previous_full["force_exit_review"] or 0)
            details = {
                "status": "dependency_db_locked_previous_risk_level_preserved",
                "riskguard_degraded_reason": "dependency_db_locked",
                "bankroll_truth_source": "polymarket_wallet",
                "dependency_db_lock_error": str(exc),
                "full_metrics_status": "locked_previous_fresh_level_preserved",
                "previous_full_risk_level": previous_full["level"],
                "previous_full_risk_checked_at": previous_full["checked_at"],
                "conservative_floor_applied": False,
            }
        risk_conn.execute(
            """
            INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)
            VALUES (?, NULL, NULL, NULL, ?, ?, ?)
            """,
            (
                level.value,
                json.dumps(details),
                now_ts,
                force_exit_review,
            ),
        )
        risk_conn.commit()
    finally:
        _close_conn(risk_conn)
    logger.error(
        "RiskGuard tick metrics degraded: dependency DB locked; persisted fresh risk_state level=%s. error=%s",
        level.value,
        exc,
    )
    return level


def _persist_tick_in_progress_attestation() -> None:
    """Keep the entry gate continuous while a full RiskGuard tick is running.

    RiskGuard's full metric pass can occasionally exceed the 5-minute reader
    freshness window under DB I/O pressure. If the previous full row is still
    fresh at tick start, persist a short-lived attestation carrying that proven
    level so live trading does not fail RED in the middle of a still-running
    tick. Rows written here are not full metrics and are never accepted by
    _latest_fresh_full_risk_row; they expire through the normal freshness floor.
    """
    now = datetime.now(timezone.utc)
    risk_conn = get_connection(RISK_DB_PATH, write_class="live")
    try:
        init_risk_db(risk_conn)
        previous_full = _latest_fresh_full_risk_row(risk_conn, now=now)
        if previous_full is None:
            return
        details = {
            "status": "metrics_in_progress_previous_risk_level_preserved",
            "riskguard_degraded_reason": "metrics_refresh_in_progress",
            "full_metrics_status": "in_progress_previous_fresh_level_preserved",
            "previous_full_risk_level": previous_full["level"],
            "previous_full_risk_checked_at": previous_full["checked_at"],
        }
        risk_conn.execute(
            """
            INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)
            VALUES (?, NULL, NULL, NULL, ?, ?, ?)
            """,
            (
                previous_full["level"],
                json.dumps(details),
                now.isoformat(),
                int(previous_full["force_exit_review"] or 0),
            ),
        )
        risk_conn.commit()
    except sqlite3.OperationalError as exc:
        if not _is_sqlite_database_locked(exc):
            raise
        logger.warning(
            "RiskGuard tick-start attestation skipped because risk_state.db is locked: %s",
            exc,
        )
    finally:
        _close_conn(risk_conn)


def _tick_once() -> RiskLevel:
    """Run one RiskGuard evaluation attempt. Spec §7: 60-second cycle.

    Reads recent trade data from zeus.db, computes metrics,
    determines risk level, writes to risk_state.db.

    RAISES ``sqlite3.OperationalError('database is locked')`` on a transient
    dependency-DB lock instead of degrading inline — the ``tick()`` wrapper
    retries this within the same tick (see ``_riskguard_dependency_lock_retries``)
    and only persists the lock-attestation after the retries exhaust. This keeps
    the lock-degrade decision in ONE place while letting a momentary lock be
    waited out rather than immediately flipping the GREEN-only entry gate.

    Connection discipline (2026-05-10 leak fix): zeus_conn and risk_conn are
    opened once and closed in a finally block. Prior to this fix, any
    exception mid-tick left both handles open; with a 60s tick and recurring
    errors this produced 51+ accumulated zeus-world.db-wal reader handles
    (observed on PID 18538), blocking all WAL writers (data-ingest, live-trading).
    """
    zeus_conn: sqlite3.Connection | None = None
    risk_conn: sqlite3.Connection | None = None

    # P0-A bankroll truth chain (architect memo §7): trailing-loss math must
    # use live chain/collateral truth, NOT the config constant routed through
    # PortfolioState.bankroll. When the wallet is unreachable AND no fresh
    # collateral snapshot/cache exists, fail-closed at DATA_DEGRADED rather
    # than silently falling back to retired config-literal capital.
    #
    # CONN-ACROSS-IO INVARIANT (T0-1, dimension-#4): this fetch is hoisted ABOVE
    # the zeus_conn/risk_conn opens. The primary path consumes the post-trade
    # sidecar's durable collateral snapshot (no venue I/O); compatibility direct
    # wallet reads may still perform network I/O. Fetching before any conn opens
    # guarantees NO network I/O ever happens while a write-class conn is held.
    # The fail-closed-to-DATA_DEGRADED contract (the `bankroll_of_record is None`
    # branch below, which still runs after risk_conn opens so the DATA_DEGRADED
    # attestation row can be written), the short busy_timeout, and the WAL-leak
    # fix are all preserved.
    # Relationship test: tests/riskguard/test_no_network_io_under_conn.py.
    bankroll_of_record = _bankroll_of_record_for_riskguard()

    try:
        zeus_conn = _get_runtime_trade_connection()
        # Short per-attempt wait so a contended metrics read FAILS FAST and the
        # tick() retry loop (or the fast preserve-GREEN attestation) keeps risk_state
        # rows inside the 5-min staleness floor — see _riskguard_dependency_busy_timeout_ms.
        try:
            zeus_conn.execute("PRAGMA busy_timeout = %d" % _riskguard_dependency_busy_timeout_ms())
        except Exception:  # noqa: BLE001 — best-effort PRAGMA; a stub conn in tests may lack it
            pass
        risk_conn = get_connection(RISK_DB_PATH, write_class="live")
        init_risk_db(risk_conn)

        previous_row = risk_conn.execute(
            "SELECT level FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        previous_level = RiskLevel(previous_row["level"]) if previous_row else None

        thresholds = settings["riskguard"]
        portfolio, portfolio_truth = _load_riskguard_portfolio_truth(zeus_conn)

        # Bankroll truth was fetched BEFORE the conns opened (see the hoisted
        # `_bankroll_of_record_for_riskguard()` above — conn-across-IO invariant T0-1).
        # The fail-closed write below needs risk_conn, so the None-handling stays
        # here; direct venue I/O itself never runs under a held conn.
        if bankroll_of_record is None:
            now_ts = datetime.now(timezone.utc).isoformat()
            risk_conn.execute(
                """
                INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)
                VALUES (?, NULL, NULL, NULL, ?, ?, 0)
                """,
                (
                    RiskLevel.DATA_DEGRADED.value,
                    json.dumps({
                        "status": "bankroll_provider_unavailable",
                        "bankroll_truth": {
                            "source": "polymarket_wallet",
                            "value_usd": None,
                            "fetched_at": None,
                            "staleness_seconds": None,
                            "cached": False,
                            "reason": "collateral snapshot and direct wallet query both unavailable",
                        },
                    }),
                    now_ts,
                ),
            )
            risk_conn.commit()
            logger.error(
                "RiskGuard tick fail-closed: bankroll truth unavailable "
                "(no fresh collateral snapshot and no direct wallet value)",
            )
            return RiskLevel.DATA_DEGRADED

        current_bankroll_usd = float(bankroll_of_record.value_usd)
        settlement_scan_rows = query_authoritative_settlement_rows(
            zeus_conn,
            limit=max(RISKGUARD_SETTLEMENT_LIMIT, RISKGUARD_BRIER_SCAN_LIMIT),
        )
        settlement_scan_rows = _bind_brier_probability_identities(
            zeus_conn,
            settlement_scan_rows,
        )
        settlement_rows = settlement_scan_rows[:RISKGUARD_SETTLEMENT_LIMIT]
        brier_candidate_rows = settlement_scan_rows[:RISKGUARD_BRIER_SCAN_LIMIT]
        settlement_row_storage_sources = sorted({str(r.get("source", "unknown")) for r in settlement_rows})
        settlement_storage_source = (
            settlement_row_storage_sources[0]
            if len(settlement_row_storage_sources) == 1
            else ("mixed" if settlement_row_storage_sources else "none")
        )
        settlement_authority_levels: dict[str, int] = {}
        degraded_rows = 0
        settlement_economic_ready_rows = []
        settlement_contract_incomplete_count = 0
        learning_snapshot_ready_count = 0
        canonical_payload_complete_count = 0
        settlement_metric_ready_rows = []
        for row in settlement_rows:
            authority_level = str(row.get("authority_level", "unknown"))
            settlement_authority_levels[authority_level] = settlement_authority_levels.get(authority_level, 0) + 1
            # Economic payout truth and physical settlement truth are distinct.
            # Gamma can prove which token paid without proving the exact observed
            # temperature. That row is complete for P&L/risk, but remains excluded
            # from physical calibration through metric_ready=False. Only a malformed
            # economic row may actuate settlement_quality and freeze new entries.
            required_missing = tuple(row.get("required_missing_fields") or ())
            economic_ready = (
                authority_level != "durable_event_malformed"
                and not required_missing
            )
            if economic_ready:
                settlement_economic_ready_rows.append(row)
            else:
                degraded_rows += 1
            if not row.get("canonical_payload_complete", False):
                settlement_contract_incomplete_count += 1
            if row.get("learning_snapshot_ready", False):
                learning_snapshot_ready_count += 1
            if row.get("canonical_payload_complete", False):
                canonical_payload_complete_count += 1
            if row.get("metric_ready", True) and row.get("p_posterior") is not None and row.get("outcome") is not None:
                settlement_metric_ready_rows.append(row)

        realized_exits, realized_truth_source, realized_degraded = _current_mode_realized_exits(
            zeus_conn,
            settlement_rows=settlement_rows,
        )
        portfolio = replace(portfolio, recent_exits=realized_exits)

        brier_metric_rows = _riskguard_brier_metric_rows(brier_candidate_rows)
        probability_identity_ready_count = sum(
            bool(row.get("probability_identity_ready", False))
            for row in brier_candidate_rows
        )
        probability_identity_block_reasons: dict[str, int] = {}
        for row in brier_candidate_rows:
            if row.get("probability_identity_ready", False):
                continue
            reason = str(
                row.get("probability_identity_blocked_reason") or "unbound"
            )
            probability_identity_block_reasons[reason] = (
                probability_identity_block_reasons.get(reason, 0) + 1
            )
        p_forecasts = [float(r["p_posterior"]) for r in brier_metric_rows]
        outcomes = [int(r["outcome"]) for r in brier_metric_rows]
        strategy_settlement_summary = _strategy_settlement_summary(settlement_metric_ready_rows)
        entry_execution_summary = _entry_execution_summary(zeus_conn)
        try:
            tracker = load_tracker()
            tracker_summary = tracker.summary()
            edge_compression_alerts = tracker.edge_compression_check()
            tracker_accounting = dict(getattr(tracker, "accounting", {}))
            strategy_tracker_error = ""
        except Exception as exc:
            tracker_summary = {}
            edge_compression_alerts = []
            tracker_accounting = {}
            strategy_tracker_error = str(exc)

        # Compute metrics from authoritative settlement rows only.
        b_score = brier_score(p_forecasts, outcomes) if p_forecasts else 0.0
        d_accuracy = directional_accuracy(p_forecasts, outcomes) if p_forecasts else 0.5

        # Evaluate levels. Portfolio Brier is the headline quality metric, but
        # a YELLOW breach that is fully attributable to canonical strategies can
        # be enforced through durable strategy gates. Stronger ORANGE/RED
        # breaches remain global fail-closed.
        portfolio_brier_raw_level = (
            evaluate_brier(b_score, thresholds) if p_forecasts else RiskLevel.GREEN
        )
        portfolio_brier_thin_sample = (
            0 < len(p_forecasts) < _STRATEGY_BRIER_MIN_SAMPLE
        )
        # A pooled probability score has no more authority than its evidence.
        # One confident loss can exceed the RED threshold, but it cannot prove
        # that every current candidate is unsafe.  Keep the raw score/level for
        # learning and operator telemetry; only let it actuate RiskGuard once
        # the same minimum evidence floor used by strategy attribution exists.
        portfolio_brier_level = (
            RiskLevel.GREEN
            if portfolio_brier_thin_sample
            else portfolio_brier_raw_level
        )
        brier_level = portfolio_brier_level
        brier_strategy_breakdown = _strategy_brier_breakdown(brier_metric_rows, thresholds) if p_forecasts else {
            "by_strategy": {},
            "degraded_strategies": {},
            "unclassified_count": 0,
            "classified_count": 0,
        }
        brier_strategy_localization: dict[str, object] = {
            "status": "not_applicable",
            "reason": (
                "portfolio_brier_thin_sample_no_verdict"
                if portfolio_brier_thin_sample
                else "portfolio_brier_green"
            ),
        }
        settlement_quality_level = RiskLevel.GREEN
        if settlement_rows and not settlement_economic_ready_rows:
            settlement_quality_level = RiskLevel.RED
        elif degraded_rows > 0:
            settlement_quality_level = RiskLevel.YELLOW
        execution_quality_level = RiskLevel.GREEN
        execution_overall = entry_execution_summary["overall"]
        execution_observed = int(execution_overall.get("terminal_observed", 0) or 0)
        recommended_control_reasons: dict[str, list[str]] = {}
        recommended_strategy_gate_reasons: dict[str, list[str]] = {}
        degraded_brier_strategies = brier_strategy_breakdown.get("degraded_strategies", {})
        clean_brier_attribution = (
            isinstance(degraded_brier_strategies, dict)
            and bool(degraded_brier_strategies)
            and int(brier_strategy_breakdown.get("unclassified_count", 0) or 0) == 0
        )

        def _append_brier_degraded_gate_reasons() -> None:
            for strategy, payload in sorted(degraded_brier_strategies.items()):
                if not isinstance(payload, dict):
                    continue
                _append_reason(
                    recommended_strategy_gate_reasons,
                    str(strategy),
                    (
                        "brier_degraded("
                        f"level={payload.get('level')},"
                        f"brier={payload.get('brier')},"
                        f"sample={payload.get('sample_size')}"
                        ")"
                    ),
                )

        if portfolio_brier_level == RiskLevel.YELLOW and clean_brier_attribution:
            brier_strategy_localization = {
                "status": "pending_durable_strategy_gate",
                "gated_strategies": sorted(str(strategy) for strategy in degraded_brier_strategies),
            }
            _append_brier_degraded_gate_reasons()
        elif portfolio_brier_level == RiskLevel.ORANGE and clean_brier_attribution:
            # ORANGE localization (live incident 2026-07-04, opening_inertia
            # trailing-30d Brier 0.322 froze healthy strategies for ~30 trailing
            # days). Unlike YELLOW, ORANGE localization additionally requires
            # (checked after the durable bookkeeping write below): a
            # read-after-write CONFIRMED active gate per degraded strategy, and
            # the residual (non-gated) portfolio itself recomputing to GREEN.
            # Until both are confirmed this stays "pending" and the level below
            # remains the global portfolio_brier_level (fail closed).
            brier_strategy_localization = {
                "status": "pending_durable_strategy_gate_orange",
                "gated_strategies": sorted(str(strategy) for strategy in degraded_brier_strategies),
            }
            _append_brier_degraded_gate_reasons()
        elif portfolio_brier_level != RiskLevel.GREEN:
            brier_strategy_localization = {
                "status": "not_localized",
                "reason": "portfolio_brier_requires_global_level",
                "portfolio_brier_level": portfolio_brier_level.value,
                "unclassified_count": int(brier_strategy_breakdown.get("unclassified_count", 0) or 0),
                "degraded_strategy_count": (
                    len(degraded_brier_strategies)
                    if isinstance(degraded_brier_strategies, dict)
                    else 0
                ),
            }
        # execution_quality_level stays GREEN: a low maker fill-rate is NOT a
        # risk condition (2026-07-05, INV-05). REMOVED the assignment that set
        # execution_quality_level=YELLOW + recommended tighten_risk when
        # overall fill_rate < 0.3. Why: non-fills / voided rests cost $0, and
        # fill_rate counts deliberate maker-patience pulls as "decay" (see the
        # per-strategy removal above). Leaving it drove the portfolio to a
        # STUCK YELLOW -> auto-safe tighten_risk -> DOUBLED edge thresholds
        # (control_plane), throttling the very entries the loop needs. fill_rate
        # remains in entry_execution_summary for observability only.
        # The downstream `if execution_quality_level == RiskLevel.YELLOW`
        # branches (tighten_risk control append; execution-quality localization;
        # the YELLOW alert) are now inert — execution_quality_level can no longer
        # be YELLOW. Collapsing that dead apparatus is tracked as a follow-up.
        strategy_signal_level = RiskLevel.YELLOW if (edge_compression_alerts or strategy_tracker_error) else RiskLevel.GREEN
        for alert in edge_compression_alerts:
            if not alert.startswith("EDGE_COMPRESSION: "):
                continue
            strategy = alert.split(": ", 1)[1].split(" edge", 1)[0]
            _append_reason(recommended_strategy_gate_reasons, strategy, "edge_compression")
        # execution_decay is NOT a per-strategy selection gate (2026-07-05,
        # INV-05 advisory-risk-forbidden). REMOVED: the fill-rate loop that
        # appended execution_decay(...) to recommended_strategy_gate_reasons and
        # became a risk_action:gate removing candidates before ranking. Why:
        #   1. Non-fills and voided maker rests cost $0. A fill-rate heuristic is
        #      not capital protection, so it must not HARD-gate entries — risk
        #      sweeps (RED) or does not act; it is never advisory (INV-05).
        #   2. fill_rate = filled / (filled + rejected + voided) counts our own
        #      DELIBERATE maker-patience pulls (winner's-curse rests we decline
        #      to overpay; re-decision pulls on book drift) as "decay". It
        #      penalizes correct behavior — low maker-fill is EXPECTED for a
        #      maker-patient strategy, not a defect.
        #   3. Calibration failure (the real risk) is caught by brier_degraded
        #      (settled Brier) and edge_compression, which STILL gate above.
        #      execution_decay measured fills, not calibration — orthogonal.
        #   4. It self-perpetuated: gate -> strategy quiet -> no terminals ->
        #      frozen window -> re-gate, blocking the only fat-edge strategy
        #      (forecast_qkernel_entry) every cycle and starving the
        #      settle->grade->recalibrate loop of the fills that validate q.
        # fill_rate stays computed in _entry_execution_summary for observability
        # only; it never gates a strategy nor raises a risk level.
        # The _execution_decay_verdict_is_current freshness helper (the earlier,
        # weaker mitigation) is now unwired; removal tracked separately.
        recommended_strategy_gates = sorted(recommended_strategy_gate_reasons)
        recommended_controls = []
        if execution_quality_level == RiskLevel.YELLOW:
            recommended_controls.append("tighten_risk")
        if recommended_strategy_gates:
            recommended_controls.append("review_strategy_gates")
            review_gate_reasons = [
                f"{strategy}:{'|'.join(sorted(recommended_strategy_gate_reasons.get(strategy, [])))}"
                for strategy in recommended_strategy_gates
            ]
            recommended_control_reasons["review_strategy_gates"] = review_gate_reasons

        # Refresh and query strategy health FIRST to compute canonical PnL.
        # These are AUXILIARY bookkeeping writes/reads (risk_actions +
        # strategy_health). They run lock-tolerantly: a writer-contention
        # "database is locked" on these bookkeeping WRITES must NOT degrade the
        # risk LEVEL, which is computed entirely from the metric READS already
        # gathered above. See _refresh_riskguard_auxiliary_bookkeeping +
        # docs/evidence/no_order_root_2026-06-13/diagnosis.md. Fail-closed is
        # preserved — a lock on the genuine truth READS earlier still degrades.
        now = datetime.now(timezone.utc).isoformat()
        (
            durable_action_status,
            strategy_health_refresh,
            strategy_health_snapshot,
        ) = _refresh_riskguard_auxiliary_bookkeeping(
            zeus_conn,
            recommended_strategy_gate_reasons=recommended_strategy_gate_reasons,
            now=now,
            position_view=portfolio_truth.get("_strategy_health_position_view"),
        )
        if brier_strategy_localization.get("status") == "pending_durable_strategy_gate":
            if durable_action_status.get("status") == "emitted":
                brier_level = RiskLevel.GREEN
                brier_strategy_localization = {
                    **brier_strategy_localization,
                    "status": "localized_to_durable_strategy_gates",
                    "durable_risk_action_status": durable_action_status.get("status"),
                }
            else:
                brier_level = portfolio_brier_level
                brier_strategy_localization = {
                    **brier_strategy_localization,
                    "status": "durable_strategy_gate_unavailable_global_yellow",
                    "durable_risk_action_status": durable_action_status.get("status"),
                }
        elif brier_strategy_localization.get("status") == "pending_durable_strategy_gate_orange":
            orange_gated_strategies = list(brier_strategy_localization.get("gated_strategies", []))
            if durable_action_status.get("status") == "emitted":
                gate_confirmation = _confirm_active_durable_strategy_gates(zeus_conn, orange_gated_strategies)
                all_gates_confirmed = bool(orange_gated_strategies) and all(gate_confirmation.values())
            else:
                gate_confirmation = {strategy: False for strategy in orange_gated_strategies}
                all_gates_confirmed = False

            if all_gates_confirmed:
                (
                    residual_level,
                    residual_score,
                    residual_sample_size,
                    residual_thin_excluded,
                ) = _residual_active_portfolio_brier_level(
                    brier_metric_rows, thresholds, set(orange_gated_strategies),
                )
                if residual_level == RiskLevel.GREEN:
                    brier_level = RiskLevel.GREEN
                    brier_strategy_localization = {
                        **brier_strategy_localization,
                        "status": "localized_orange_scope",
                        "durable_risk_action_status": durable_action_status.get("status"),
                        "gate_confirmation": gate_confirmation,
                        "residual_brier_level": residual_level.value,
                        "residual_brier_score": round(float(residual_score), 6),
                        "residual_sample_size": residual_sample_size,
                        "thin_sample_excluded_strategies": residual_thin_excluded,
                    }
                else:
                    brier_level = portfolio_brier_level
                    brier_strategy_localization = {
                        **brier_strategy_localization,
                        "status": "orange_residual_portfolio_not_green",
                        "durable_risk_action_status": durable_action_status.get("status"),
                        "gate_confirmation": gate_confirmation,
                        "residual_brier_level": residual_level.value,
                        "residual_brier_score": round(float(residual_score), 6),
                        "residual_sample_size": residual_sample_size,
                        "thin_sample_excluded_strategies": residual_thin_excluded,
                    }
            else:
                brier_level = portfolio_brier_level
                brier_strategy_localization = {
                    **brier_strategy_localization,
                    "status": "durable_strategy_gate_unconfirmed_global_orange",
                    "durable_risk_action_status": durable_action_status.get("status"),
                    "gate_confirmation": gate_confirmation,
                }

        localized_orange_scope = brier_strategy_localization.get("status") == "localized_orange_scope"

        # Execution-quality localization (same admissible-portfolio principle
        # as ORANGE Brier localization): a strategy already held behind a
        # CONFIRMED durable gate cannot place entries, so its historical
        # fill-rate must not freeze the strategies that CAN. Recompute the
        # fill-rate over non-gated strategies only; evidence is never aged
        # out or windowed away — it is attributed. Falls back to the global
        # verdict when nothing is gated. A thin residual sample (<10 terminal)
        # is not evidence of decay: the gate exists to catch DECAY, and a
        # residual book too new to have terminal outcomes is admitted on the
        # Brier/loss gates instead.
        if execution_quality_level == RiskLevel.YELLOW:
            gated_for_execution = sorted(
                strategy
                for strategy, held in _confirm_active_durable_strategy_gates(
                    zeus_conn,
                    sorted(entry_execution_summary.get("by_strategy", {})),
                ).items()
                if held
            )
            if gated_for_execution:
                residual_terminal = 0
                residual_filled = 0
                for strategy, bucket in entry_execution_summary.get("by_strategy", {}).items():
                    if strategy in gated_for_execution:
                        continue
                    residual_terminal += int(bucket.get("terminal_observed", 0) or 0)
                    residual_filled += int(bucket.get("filled", 0) or 0)
                residual_fill_rate = (
                    residual_filled / residual_terminal if residual_terminal else None
                )
                if residual_terminal < 10 or (
                    residual_fill_rate is not None and residual_fill_rate >= 0.3
                ):
                    execution_quality_level = RiskLevel.GREEN
                    recommended_control_reasons.pop("tighten_risk", None)
                    if "tighten_risk" in recommended_controls:
                        recommended_controls.remove("tighten_risk")
                    brier_strategy_localization = {
                        **brier_strategy_localization,
                        "execution_quality_localized": True,
                        "execution_gated_strategies": gated_for_execution,
                        "execution_residual_fill_rate": residual_fill_rate,
                        "execution_residual_terminal_observed": residual_terminal,
                    }

        total_realized_pnl = sum(bucket.get("realized_pnl_30d", 0.0) for bucket in strategy_health_snapshot.get("by_strategy", {}).values())
        total_unrealized_pnl = sum(bucket.get("unrealized_pnl", 0.0) for bucket in strategy_health_snapshot.get("by_strategy", {}).values())

        if total_unrealized_pnl == 0.0 and strategy_health_snapshot.get("status") in (
            "missing_table", "empty", "fresh", "stale", "skipped_dependency_lock"
        ):
            # Fallback for unrealized PnL — also covers the cycle where the
            # strategy_health bookkeeping was SKIPPED because the auxiliary write
            # lost the zeus_trades WAL write lock (skipped_dependency_lock): the
            # observability PnL still reads from in-memory portfolio positions so
            # a writer-contention skip never silently zeroes unrealized PnL.
            total_unrealized_pnl = sum(float(getattr(p, "unrealized_pnl", 0.0)) for p in getattr(portfolio, "positions", []))

        total_pnl = total_realized_pnl + total_unrealized_pnl
        settlement_authority_missing_tables = list(
            strategy_health_refresh.get("settlement_authority_missing_tables", [])
        )
        if settlement_authority_missing_tables:
            realized_degraded = True

        # Account equity = wallet cash plus authoritative open-position value.
        # Realized PnL is already in wallet cash and must not be added again.
        # Open entry fills are different: a BUY converts cash into conditional
        # tokens, and treating that conversion as loss false-REDs live after the
        # first successful fill.
        account_equity = _riskguard_account_equity(
            zeus_conn,
            wallet_cash_usd=current_bankroll_usd,
            portfolio=portfolio,
        )
        current_total_value = account_equity["effective_equity_usd"]
        # Trailing realized loss is observability only. Settled outcomes are
        # already embedded in current cash and positions; using the same loss a
        # second time as an admission veto would reject current positive-growth
        # actions based on sunk outcomes.
        loss_source = f"realized_settlement_window:{realized_truth_source}"
        daily_loss_snapshot = _realized_window_loss_diagnostic(
            realized_exits,
            now=now,
            lookback=timedelta(hours=24),
            degraded=realized_degraded,
            source=loss_source,
        )
        weekly_loss_snapshot = _realized_window_loss_diagnostic(
            realized_exits,
            now=now,
            lookback=timedelta(days=7),
            degraded=realized_degraded,
            source=loss_source,
        )
        daily_loss = daily_loss_snapshot["loss"]
        weekly_loss = weekly_loss_snapshot["loss"]
        daily_loss_level = RiskLevel.GREEN
        weekly_loss_level = RiskLevel.GREEN
        collateral_identity_level = _collateral_identity_level(zeus_conn)
        portfolio_consistency_level = _portfolio_consistency_level(
            portfolio_truth.get("consistency_lock", "pass")
        )
        # T2 (quarantine excision, BLOCKER-1 "unbounded obligation -> DATA_DEGRADED"
        # leg + "unmappable family identity... never silent skip"): an OPEN
        # unbounded EntryExposureObligation, or a blocking ChainOnlyFact whose
        # family identity cannot be resolved, is missing risk-input truth —
        # existing DATA_DEGRADED lane (blocks NEW entries via
        # _risk_allows_new_entries/riskguard_allows_new_entries requiring
        # GREEN; monitor/exit/reconciliation lanes are untouched by risk_level).
        # This replaces the portfolio-wide quarantine gate's global freeze with
        # the SAME risk lane every other "missing truth input" condition
        # already uses, single-seam.
        unresolved_exposure_level = _unresolved_exposure_data_degraded_level(zeus_conn, portfolio)

        level = overall_level(
            brier_level,
            settlement_quality_level,
            execution_quality_level,
            strategy_signal_level,
            collateral_identity_level,
            portfolio_consistency_level,
            unresolved_exposure_level,
        )

        # Legacy column retained for schema compatibility. Historical outcomes
        # no longer create exit intent or block current positive-growth entries.
        force_exit_review = 0

        risk_conn.execute("""
            INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            level.value, b_score, d_accuracy, None,
            json.dumps({
                "brier_level": brier_level.value,
                "portfolio_brier_level": portfolio_brier_level.value,
                "portfolio_brier_raw_level": portfolio_brier_raw_level.value,
                "portfolio_brier_thin_sample_no_verdict": portfolio_brier_thin_sample,
                # ORANGE-localization audit surface (2026-07-04): the raw,
                # unfiltered portfolio view (all strategies pooled) vs. the
                # view that actually DRIVES admission after any localization
                # (YELLOW-to-durable-gate or ORANGE-scope) is applied.
                # Kept as an explicit alias of portfolio_brier_level/brier_level
                # so downstream consumers see a coherent, self-describing pair
                # regardless of which localization branch (if any) fired.
                "brier_all_strategies_level": portfolio_brier_level.value,
                "brier_active_portfolio_level": brier_level.value,
                "localized_orange_scope": localized_orange_scope,
                "brier_strategy_breakdown": brier_strategy_breakdown,
                "brier_strategy_localization": brier_strategy_localization,
                "settlement_quality_level": settlement_quality_level.value,
                "execution_quality_level": execution_quality_level.value,
                "strategy_signal_level": strategy_signal_level.value,
                # T2 (quarantine excision, BLOCKER-1): unbounded obligation or
                # unmapped-family ChainOnlyFact -> DATA_DEGRADED leg.
                "unresolved_exposure_level": unresolved_exposure_level.value,
                "daily_loss_level": daily_loss_level.value,
                "weekly_loss_level": weekly_loss_level.value,
                "trailing_loss_decision_role": "diagnostic_only",
                "daily_loss": None if daily_loss is None else round(float(daily_loss), 2),
                "weekly_loss": None if weekly_loss is None else round(float(weekly_loss), 2),
                "daily_loss_status": daily_loss_snapshot["status"],
                "weekly_loss_status": weekly_loss_snapshot["status"],
                "daily_loss_source": daily_loss_snapshot["source"],
                "weekly_loss_source": weekly_loss_snapshot["source"],
                "daily_loss_reference": daily_loss_snapshot["reference"],
                "weekly_loss_reference": weekly_loss_snapshot["reference"],
                "initial_bankroll": round(current_bankroll_usd, 2),
                # Preserve concrete live bankroll provenance for current-state
                # sizing and for compatibility with older risk rows.
                "bankroll_truth_source": bankroll_of_record.source,
                "bankroll_truth": {
                    "value_usd": round(current_bankroll_usd, 2),
                    "source": bankroll_of_record.source,
                    "authority": bankroll_of_record.authority,
                    "fetched_at": bankroll_of_record.fetched_at,
                    "staleness_seconds": round(float(bankroll_of_record.staleness_seconds), 3),
                    "cached": bool(bankroll_of_record.cached),
                    # Positions-blip guard provenance (2026-06-09): "blip_held"
                    # means the equity base is defending against an empty
                    # /positions read that contradicted recent verified holdings.
                    "positions_read_verdict": str(
                        getattr(bankroll_of_record, "positions_read_verdict", "unknown")
                    ),
                    # Conservative NEW-ENTRY sizing base. Under blip_held it
                    # excludes phantom equity so Kelly cannot size from it.
                    "equity_for_new_entry_sizing_usd": (
                        None
                        if getattr(bankroll_of_record, "equity_for_new_entry_sizing_usd", None) is None
                        else round(float(bankroll_of_record.equity_for_new_entry_sizing_usd), 2)
                    ),
                },
                "daily_baseline_total": round(portfolio.daily_baseline_total, 2),
                "weekly_baseline_total": round(portfolio.weekly_baseline_total, 2),
                "realized_pnl": round(total_realized_pnl, 2),
                "realized_pnl_source": "strategy_health.realized_pnl_30d",
                "realized_pnl_window_days": 30,
                "unrealized_pnl": round(total_unrealized_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "effective_bankroll": round(current_total_value, 2),
                "account_equity_components": account_equity,
                "portfolio_truth_source": portfolio_truth["source"],
                "portfolio_loader_status": portfolio_truth["loader_status"],
                "portfolio_fallback_active": portfolio_truth["fallback_active"],
                "portfolio_fallback_reason": portfolio_truth["fallback_reason"],
                "portfolio_position_count": portfolio_truth["position_count"],
                "portfolio_capital_source": portfolio_truth.get("capital_source", "unknown"),
                "portfolio_consistency_lock": portfolio_truth.get("consistency_lock", "pass"),
                "portfolio_consistency_level": portfolio_consistency_level.value,
                "portfolio_unloadable_count": portfolio_truth.get("unloadable_count", 0),
                "portfolio_excluded_duplicate_count": portfolio_truth.get("excluded_duplicate_count", 0),
                "realized_truth_source": realized_truth_source,
                "realized_degraded": realized_degraded,
                "settlement_sample_size": len(p_forecasts),
                "settlement_brier_scan_limit": RISKGUARD_BRIER_SCAN_LIMIT,
                "settlement_brier_candidate_count": len(brier_candidate_rows),
                "settlement_storage_source": settlement_storage_source,
                "settlement_row_storage_sources": settlement_row_storage_sources,
                "settlement_authority_levels": settlement_authority_levels,
                "settlement_degraded_row_count": degraded_rows,
                "settlement_economic_ready_count": len(settlement_economic_ready_rows),
                "settlement_contract_incomplete_count": settlement_contract_incomplete_count,
                "settlement_learning_snapshot_ready_count": learning_snapshot_ready_count,
                "settlement_canonical_payload_complete_count": canonical_payload_complete_count,
                "settlement_metric_ready_count": len(settlement_metric_ready_rows),
                "settlement_brier_learning_ready_count": len(brier_metric_rows),
                "settlement_probability_identity_ready_count": probability_identity_ready_count,
                "settlement_probability_identity_unready_count": (
                    len(brier_candidate_rows) - probability_identity_ready_count
                ),
                "settlement_probability_identity_block_reasons": probability_identity_block_reasons,
                # K2 rename (bug #3): this field is the PROBABILITY-SIDE directional
                # hit rate computed from brier forecasts (did p>0.5 match the
                # outcome?). It is NOT the same as trade profitability rate, which
                # lives inside strategy_settlement_summary as per-strategy
                # 'trade_profitability_rate'. The previous bare 'accuracy' key
                # collided in name with the per-strategy rate and caused LLM
                # reporters to copy 0.8947 as 'win rate'.
                "probability_directional_accuracy": round(d_accuracy, 4),
                "strategy_settlement_summary": strategy_settlement_summary,
                "entry_execution_summary": entry_execution_summary,
                "strategy_tracker_summary": tracker_summary,
                "strategy_edge_compression_alerts": edge_compression_alerts,
                "strategy_tracker_accounting": tracker_accounting,
                "strategy_tracker_error": strategy_tracker_error,
                "recommended_strategy_gates": recommended_strategy_gates,
                "recommended_strategy_gate_reasons": {
                    strategy: sorted(reasons)
                    for strategy, reasons in sorted(recommended_strategy_gate_reasons.items())
                },
                "recommended_controls": recommended_controls,
                "recommended_control_reasons": {
                    control: list(reasons)
                    for control, reasons in sorted(recommended_control_reasons.items())
                },
                "durable_risk_action_emission_status": durable_action_status["status"],
                "durable_risk_action_emitted_count": durable_action_status["emitted_count"],
                "durable_risk_action_expired_count": durable_action_status["expired_count"],
                "strategy_health_refresh_status": strategy_health_refresh["status"],
                "strategy_health_rows_written": strategy_health_refresh.get("rows_written", 0),
                "strategy_health_missing_required_tables": list(strategy_health_refresh.get("missing_required_tables", [])),
                "strategy_health_missing_optional_tables": list(strategy_health_refresh.get("missing_optional_tables", [])),
                "strategy_health_settlement_authority_missing_tables": settlement_authority_missing_tables,
                "strategy_health_omitted_fields": list(strategy_health_refresh.get("omitted_fields", [])),
                "strategy_health_snapshot_status": strategy_health_snapshot["status"],
                "strategy_health_stale_strategy_keys": list(strategy_health_snapshot.get("stale_strategy_keys", [])),
            }),
            now,
            force_exit_review,
        ))
        zeus_conn.commit()
        risk_conn.commit()

        try:
            if level == RiskLevel.RED:
                failed_rules = []
                if brier_level == RiskLevel.RED:
                    failed_rules.append({
                        "name": "brier",
                        "value": round(b_score, 4),
                        "threshold": thresholds["brier_red"],
                        "detail": f"accuracy={d_accuracy:.4f}",
                    })
                if settlement_quality_level == RiskLevel.RED:
                    failed_rules.append({
                        "name": "settlement_quality",
                        "value": 0,
                        "threshold": 1,
                        "detail": f"storage_source={settlement_storage_source}",
                    })
                if collateral_identity_level == RiskLevel.RED:
                    failed_rules.append({
                        "name": "collateral_identity",
                        "value": 1,
                        "threshold": 0,
                        "detail": "unresolved collateral_identity_mismatch finding(s)",
                    })
                alert_halt(failed_rules or [{
                    "name": "riskguard",
                    "value": 1,
                    "threshold": 0,
                    "detail": f"level={level.value}",
                }])
            elif previous_level == RiskLevel.RED and level == RiskLevel.GREEN:
                alert_resume("rules cleared")
            elif level == RiskLevel.YELLOW:
                if brier_level == RiskLevel.YELLOW:
                    alert_warning("Brier score", round(b_score, 4), thresholds["brier_yellow"], detail=f"accuracy={d_accuracy:.4f}")
                if execution_quality_level == RiskLevel.YELLOW:
                    alert_warning(
                        "Execution fill rate",
                        round(execution_overall.get("fill_rate", 0.0), 4) if execution_overall.get("fill_rate") is not None else 0.0,
                        0.3,
                        detail=f"observed={execution_observed}",
                    )
                if settlement_quality_level == RiskLevel.YELLOW:
                    alert_warning("Settlement quality", float(degraded_rows), 1.0, detail=f"storage_source={settlement_storage_source}")
                if strategy_signal_level == RiskLevel.YELLOW:
                    alert_warning("Strategy signal", float(len(edge_compression_alerts)), 1.0, detail=strategy_tracker_error or "edge_compression_alerts_present")
            elif level == RiskLevel.DATA_DEGRADED:
                if portfolio_consistency_level == RiskLevel.DATA_DEGRADED:
                    alert_warning(
                        "Portfolio Consistency",
                        float(portfolio_truth.get("unloadable_count", 0)),
                        0.0,
                        detail=(
                            f"DATA_DEGRADED: consistency_lock="
                            f"{portfolio_truth.get('consistency_lock', 'pass')}"
                        ),
                    )
        except Exception as exc:
            logger.warning("Discord alert emission failed: %s", exc)

        # Per-component tick breakdown (anti-silent-verdict antibody, 2026-06-09):
        # overall = max(components), and the daemon's `Tick complete: <LEVEL>` line
        # prints ONLY that max. When the daemon sat RED for >24h (operator
        # zero-trade), the single printed word gave no way to tell WHICH component
        # drove it — a RED could be a Brier corpse, a settlement-quality gap, OR a
        # genuine realized-loss breach, and they demand opposite responses. The
        # diagnosis required a manual risk_state.db dive. This log makes every
        # tick self-explaining: each component's level plus the load-bearing number
        # for any non-GREEN component, so the log alone answers "why RED?".
        component_levels = {
            "brier": brier_level,
            "settlement_quality": settlement_quality_level,
            "execution_quality": execution_quality_level,
            "strategy_signal": strategy_signal_level,
            "collateral_identity": collateral_identity_level,
            "portfolio_consistency": portfolio_consistency_level,
            "unresolved_exposure": unresolved_exposure_level,
        }
        component_detail = {
            "brier": f"score={b_score:.4f} (n={len(p_forecasts)}, red>={thresholds['brier_red']})",
            "settlement_quality": (
                f"economic_ready={len(settlement_economic_ready_rows)}/{len(settlement_rows)} "
                f"metric_ready={len(settlement_metric_ready_rows)}/{len(settlement_rows)} "
                f"brier_learning_ready={len(brier_metric_rows)}/{len(brier_candidate_rows)} "
                f"q_identity_ready={probability_identity_ready_count}/{len(brier_candidate_rows)} "
                f"degraded={degraded_rows} storage={settlement_storage_source}"
            ),
            "execution_quality": (
                f"fill_rate={execution_overall['fill_rate']} observed={execution_observed}"
            ),
            "strategy_signal": (
                f"edge_compression_alerts={len(edge_compression_alerts)} "
                f"tracker_error={'yes' if strategy_tracker_error else 'no'}"
            ),
            "collateral_identity": "unresolved_collateral_identity_mismatch_finding",
            "portfolio_consistency": (
                f"consistency_lock={portfolio_truth.get('consistency_lock', 'pass')} "
                f"unloadable={portfolio_truth.get('unloadable_count', 0)} "
                f"excluded_duplicate={portfolio_truth.get('excluded_duplicate_count', 0)}"
            ),
            "unresolved_exposure": "unbounded current exposure truth",
        }
        driving, breakdown = _component_breakdown(level, component_levels, component_detail)
        log_fn = logger.warning if level != RiskLevel.GREEN else logger.info
        log_fn(
            "RiskGuard tick components: overall=%s driven_by=%s :: %s",
            level.value,
            driving,
            breakdown,
        )

        # Dual-bankroll posture visibility (2026-06-09 P1). Under a blip_held
        # /positions read the loss-threshold base value_usd HOLDS a phantom
        # position value (correct — it prevents a false catastrophic RED), but
        # NEW-ENTRY sizing must NOT arm Kelly off that phantom. The sizing
        # consumers (event_reactor `_runtime_bankroll_usd`, replay) already use
        # the conservative `equity_for_new_entry_sizing_usd` base; this WARN makes
        # the degraded posture explicit in the tick log so an operator reading the
        # daemon log sees "loss-threshold defended, sizing shrunk" at a glance.
        _bankroll_verdict = getattr(bankroll_of_record, "positions_read_verdict", "verified")
        if _bankroll_verdict == "blip_held":
            _sizing_base = getattr(
                bankroll_of_record, "equity_for_new_entry_sizing_usd", None
            )
            logger.warning(
                "RiskGuard posture DEGRADED (bankroll blip_held): loss-threshold base "
                "HELD at $%.2f (defends against false RED) but NEW-ENTRY sizing base is "
                "conservative $%s (phantom held position value EXCLUDED) — Kelly will "
                "size off free/corroborated cash only until the /positions read recovers "
                "or the hold bound elapses.",
                current_bankroll_usd,
                "unknown" if _sizing_base is None else f"{float(_sizing_base):.2f}",
            )

        return level
    except sqlite3.OperationalError as exc:
        if not _is_sqlite_database_locked(exc):
            raise
        # Roll back + close BEFORE re-raising so a failed attempt never leaves an
        # open read txn / dangling WAL reader handle across the tick() retry sleep
        # (the 2026-05-10 leak that accumulated 51+ reader handles). The tick()
        # wrapper owns the retry/lock-attestation decision.
        _rollback_and_close(risk_conn)
        risk_conn = None
        _rollback_and_close(zeus_conn)
        zeus_conn = None
        raise
    finally:
        _close_conn(zeus_conn)
        _close_conn(risk_conn)


def tick() -> RiskLevel:
    """Run one RiskGuard tick, retrying a transient dependency-DB lock.

    Wrapper around ``_tick_once`` (the actual evaluation). A locked dependency
    surface (zeus_trades + ATTACHed world/forecasts, all WAL) is RETRIED within
    this same tick before the daemon gives up: ~half of single reads lose a
    transient WAL/checkpoint window, so retrying recovers a GENUINE fresh
    full_risk row on nearly every tick and the 5-min freshness window that
    get_current_level depends on never lapses. Only after the retries exhaust
    does ``_persist_dependency_db_locked_attestation`` run (preserve a fresh
    <5min level, else DATA_DEGRADED) — so a PERSISTENT lock still degrades and no
    safety boundary is weakened. ``tick()`` is the public daemon entry; its API
    is unchanged.
    """
    _persist_tick_in_progress_attestation()
    retries = _riskguard_dependency_lock_retries()
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(retries + 1):
        try:
            return _tick_once()
        except sqlite3.OperationalError as exc:
            if not _is_sqlite_database_locked(exc):
                raise
            last_exc = exc
            if attempt >= retries:
                break
            logger.warning(
                "RiskGuard tick dependency lock (attempt %d/%d); retrying read after backoff",
                attempt + 1,
                retries + 1,
            )
            time.sleep(_riskguard_dependency_lock_backoff_seconds(attempt))
    assert last_exc is not None  # only reached via the locked break above
    return _persist_dependency_db_locked_attestation(last_exc)


def tick_with_portfolio(portfolio: PortfolioState) -> RiskLevel:
    """DT#6 graceful-degradation entry: run one tick with a pre-loaded PortfolioState.

    Callers that have already checked portfolio.authority can pass the degraded
    state here. If authority != 'canonical_db', new-entry paths are suppressed
    but monitor / exit / reconciliation lanes run read-only.

    Connection discipline: both connections closed in finally so exceptions
    never leave dangling handles (same leak fix as tick(), 2026-05-10).
    """
    risk_conn = get_connection(RISK_DB_PATH, write_class="live")
    zeus_conn = _get_runtime_trade_connection()
    try:
        init_risk_db(risk_conn)

        if portfolio.authority != "canonical_db":
            logger.warning(
                "tick_with_portfolio: portfolio authority=%r (degraded) — new-entry paths suppressed",
                portfolio.authority,
            )

        # Current wallet truth remains required. Historical loss windows do not:
        # a settled loss is already reflected in this balance.
        bankroll_of_record = _bankroll_of_record_for_riskguard()
        if bankroll_of_record is None:
            logger.error(
                "RiskGuard tick_with_portfolio fail-closed: bankroll_provider unavailable",
            )
            return RiskLevel.DATA_DEGRADED

        collateral_identity_level = _collateral_identity_level(zeus_conn)

        level = overall_level(
            RiskLevel.DATA_DEGRADED if portfolio.portfolio_loader_degraded else RiskLevel.GREEN,
            RiskLevel.GREEN,
            RiskLevel.GREEN,
            RiskLevel.GREEN,
            collateral_identity_level,
        )

        return level
    finally:
        zeus_conn.close()
        risk_conn.close()


def get_current_level() -> RiskLevel:
    """Read current risk level from risk_state.db.

    R4: Fail-closed — if DB error or stale (>5 min), return RED.

    SINGLE AUTHORITY (AGENTS.md iron #4): this is the ONE level both the daemon
    entry gate (riskguard_allows_new_entries) and the status risk block consume.
    A risk_state row that carries a ``riskguard_degraded_reason`` is a degraded
    attestation — RiskGuard could NOT compute fresh full metrics when it was
    written. Surfacing such a row's stored level verbatim would let a degraded
    GREEN read as a clean GREEN and admit entries (the split-brain / read-side
    fail-open). Apply the conservative floor max(level, DATA_DEGRADED) to any
    degraded row so the authority NEVER reports clean GREEN when truth is
    degraded, while never weakening a stronger halt (RED/ORANGE/YELLOW survive).
    """
    try:
        conn = get_connection(RISK_DB_PATH, write_class=None)
        row = conn.execute(
            "SELECT level, checked_at, details_json "
            "FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if row is None:
            logger.warning("RiskGuard has no persisted state row. Fail-closed → RED.")
            return RiskLevel.RED

        # R4: Staleness check — if last check > 5 min ago, RiskGuard may have crashed
        from datetime import datetime as dt
        last_check = dt.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - last_check).total_seconds()
        if _freshness_registry.evaluate("riskguard_last_check", age_seconds) >= FreshnessLevel.STALE:
            logger.warning("RiskGuard STALE: last check was %ds ago. Fail-closed → RED.",
                           int(age_seconds))
            return RiskLevel.RED

        stored_level = RiskLevel(row["level"])

        # Conservative floor for degraded attestations (read-side split-brain kill).
        try:
            details = json.loads(row["details_json"]) if row["details_json"] else {}
        except (json.JSONDecodeError, TypeError):
            details = {}
        if isinstance(details, dict) and details.get("riskguard_degraded_reason"):
            # Transient attestations already carry the CORRECT bounded level:
            # dependency_db_locked preserves a FRESH (<5 min) full level, while
            # metrics_refresh_in_progress is stamped only at tick start when the
            # previous full row is still fresh. Re-flooring either here would
            # re-block the GREEN-only entry gate during a still-running risk pass.
            # Keep the conservative split-brain floor for ALL OTHER degraded
            # reasons (genuine metric/truth degradation).
            if details.get("riskguard_degraded_reason") in {
                "dependency_db_locked",
                "metrics_refresh_in_progress",
            }:
                return stored_level
            floored = overall_level(stored_level, RiskLevel.DATA_DEGRADED)
            if floored != stored_level:
                logger.warning(
                    "RiskGuard latest row is degraded (reason=%s) with level=%s; "
                    "surfacing conservative floor %s to the entry gate / status.",
                    details.get("riskguard_degraded_reason"),
                    stored_level.value,
                    floored.value,
                )
            return floored

        return stored_level

    except Exception as e:
        # R4: DB error = fail closed → RED
        logger.error("RiskGuard DB error: %s. Fail-closed → RED.", e)
        return RiskLevel.RED


def get_force_exit_review() -> bool:
    """Read the legacy force_exit_review compatibility field.

    New full ticks write zero because trailing realized loss is diagnostic-only.
    Reading remains fail-closed so an unreadable control surface cannot silently
    weaken a row written by an older loaded process during deployment.
    """
    conn = None
    try:
        conn = get_connection(RISK_DB_PATH, write_class=None)
        row = conn.execute(
            "SELECT force_exit_review FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        return bool(row["force_exit_review"])
    except Exception:
        return True  # fail-closed: assume exit review needed
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    """Run RiskGuard as standalone process."""
    import signal
    import time
    _start = time.monotonic()  # F86: process start time for SIGTERM elapsed log
    # F85: route INFO/DEBUG to stdout (.log) and WARNING+ to stderr (.err).
    _fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    _stdout_h = logging.StreamHandler(sys.stdout)
    _stdout_h.setLevel(logging.INFO)
    _stdout_h.setFormatter(_fmt)
    _stdout_h.addFilter(lambda r: r.levelno < logging.WARNING)
    _stderr_h = logging.StreamHandler(sys.stderr)
    _stderr_h.setLevel(logging.WARNING)
    _stderr_h.setFormatter(_fmt)
    _root = logging.getLogger()
    _root.handlers.clear()
    _root.setLevel(logging.INFO)
    _root.addHandler(_stdout_h)
    _root.addHandler(_stderr_h)
    # F86: forensic SIGTERM trail.
    signal.signal(
        signal.SIGTERM,
        lambda s, f: (
            logger.error(
                "SIGTERM_RECEIVED pid=%s ppid=%s elapsed=%ss",
                os.getpid(), os.getppid(), int(time.monotonic() - _start),
            ),
            sys.exit(0),
        ),
    )
    logger.info("RiskGuard starting (60s tick)")

    from src.data.proxy_health import bypass_dead_proxy_env_vars
    bypass_dead_proxy_env_vars()
    _install_riskguard_collateral_ledger()

    while True:
        try:
            level = tick()
            logger.info("Tick complete: %s", level.value)
        except Exception as e:
            logger.error("RiskGuard tick failed: %s", e)
        time.sleep(60)
