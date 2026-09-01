"""RiskGuard: independent monitoring process. Spec §7.

Runs as a SEPARATE process with its own 60-second tick.
Reads authoritative settlement records from zeus.db, writes to risk_state.db,
and emits durable risk actions into zeus.db when the canonical table exists.
Graduated response: GREEN → YELLOW → ORANGE → RED.

# Created: (pre-audit)
# Last reused or audited: 2026-08-19
# Authority basis: connection-leak audit 2026-05-10 — 51 open zeus-world.db-wal
#   handles observed on PID 18538. Root cause: tick() and tick_with_portfolio()
#   opened zeus_conn / risk_conn without try/finally, so any exception in the
#   tick body left both connections dangling. Fixed by wrapping tick bodies in
#   try/finally to guarantee conn.close() on every exit path.
#   2026-05-17 live lock remediation: trade/world metric lock loss degrades to
#   a fresh DATA_DEGRADED risk_state row, not stale RED force-exit.
#   2026-08-19 Day0 revision admission: an established Day0 strategy whose
#   current probability semantics lacks causal capital proof is revision-gated
#   before BUY while its no-money shadow continues to drain evidence.
#   2026-06-08 thepath/audit-realign iron #4/#6 fix: (1) init_risk_db re-applies
#   busy_timeout after executescript (Fitz #5 strip-trap); (2) lock-attestation
#   FAILS CONSERVATIVE — max(previous_level, DATA_DEGRADED), never re-stamps a
#   fail-open GREEN, never weakens RED; (3) get_current_level() floors a degraded
#   row (riskguard_degraded_reason) to DATA_DEGRADED so the SINGLE authority never
#   surfaces a degraded GREEN as clean — kills the status-vs-gate split-brain.
#   2026-08-17 Brier strategy gates require independent target dates; correlated
#   city/metric cells from one forecast day remain visible but cannot fabricate
#   the minimum evidence count that blocks current positive-growth actions.
"""

import hashlib
import json
import logging
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import uuid

from src.config import settings, get_mode
from src.contracts.global_auction_receipt import (
    CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
)
from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    LIVE_CURRENT_EVIDENCE_SEMANTICS_REVISIONS,
    STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
    _current_evidence_shape,
    current_evidence_shape_semantics_mismatch,
)
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
    DECISION_LAW_IDS,
    RISK_DB_PATH,
    get_connection,
    get_forecasts_connection_read_only,
    get_trade_connection_with_world_required,
    _zeus_trade_db_path,
    query_authoritative_settlement_rows,
    query_portfolio_loader_view,
    query_strategy_health_snapshot,
    refresh_strategy_health,
    settlement_economic_ready,
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
RISKGUARD_REALIZED_TELEMETRY_WINDOW = timedelta(days=7)
from src.state.portfolio_loader_policy import choose_portfolio_truth_source
from src.state.strategy_tracker import load_tracker
from src.contracts.freshness_registry import FreshnessLevel, registry as _freshness_registry

logger = logging.getLogger(__name__)
# Stuck non-GREEN visibility (2026-08-24 reversal plan item 5b): the
# 2026-08-24 investigation found RiskGuard stuck non-GREEN explained 97.6h of
# August silence (10/11 gaps DATA_DEGRADED, one RED) with zero alerts. These
# thresholds turn a silent stuck level into a visible one — mirrors
# src/main.py's BOOTSTRAP_ALERT_AFTER_SECONDS (commit d1aeeeb52, item 5a)
# without touching the gate itself (get_current_level/tick are unchanged).
STUCK_ALERT_AFTER_SECONDS = float(
    os.environ.get("ZEUS_RISKGUARD_STUCK_ALERT_AFTER_SECONDS", "1800")
)
STUCK_ALERT_REPEAT_SECONDS = 1800.0
# risk_state has no index on checked_at, so scanning backward to the last
# GREEN row on a long-lived DB risks an unbounded table scan. Bound the scan
# by row count against the indexed `id` PK instead: at the daemon's fixed
# 60s tick cadence, 2880 rows == 48h. A run older than the cap is reported
# with lookback_capped=True (a conservative underestimate of duration) rather
# than paying an unbounded scan.
STUCK_ALERT_LOOKBACK_ROWS = 2880
STUCK_ALERT_BREADCRUMB_FILENAME = "riskguard_stuck_alert.json"
_riskguard_stuck_alert_run_started_at: str | None = None
_riskguard_stuck_alert_last_alert_monotonic: float | None = None
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

_STORAGE_ENTRY_MIN_FREE_BYTES_DEFAULT = 64 * 1024**3
_STORAGE_ENTRY_MIN_FREE_RATIO_DEFAULT = 0.10
_disk_usage = shutil.disk_usage

_POWER_RUNWAY_YELLOW_MINUTES_DEFAULT = 60.0
_POWER_RUNWAY_ORANGE_MINUTES_DEFAULT = 30.0
_POWER_RUNWAY_RED_MINUTES_DEFAULT = 15.0
_POWER_PERCENT_YELLOW_DEFAULT = 20
_POWER_PERCENT_ORANGE_DEFAULT = 10
_POWER_PERCENT_RED_DEFAULT = 5


def _pmset_battery_status() -> str:
    completed = subprocess.run(
        ["pmset", "-g", "batt"],
        check=True,
        capture_output=True,
        text=True,
        timeout=2.0,
    )
    return completed.stdout


def host_power_runway_snapshot(raw_status: str | None = None) -> dict[str, object]:
    """Classify whether the live host can retain execution authority.

    SCOPE: YELLOW blocks new entries, ORANGE permits only favorable reduction,
    and RED activates the existing portfolio-wide reduce-only sweep. DRAIN:
    RiskGuard re-reads macOS power truth every 60 seconds while the host is
    awake. RESET: AC power, or Battery Power above every configured runway and
    percentage watermark, returns GREEN on the next tick.
    """

    if raw_status is None and (get_mode() != "live" or sys.platform != "darwin"):
        return {
            "level": RiskLevel.GREEN.value,
            "status": "NOT_APPLICABLE",
            "reason": None,
            "source": "non_live_or_non_darwin_host",
        }

    power_config = settings["riskguard"]
    try:
        yellow_minutes = float(
            power_config.get(
                "power_runway_yellow_minutes",
                _POWER_RUNWAY_YELLOW_MINUTES_DEFAULT,
            )
        )
        orange_minutes = float(
            power_config.get(
                "power_runway_orange_minutes",
                _POWER_RUNWAY_ORANGE_MINUTES_DEFAULT,
            )
        )
        red_minutes = float(
            power_config.get(
                "power_runway_red_minutes",
                _POWER_RUNWAY_RED_MINUTES_DEFAULT,
            )
        )
        yellow_percent = int(
            power_config.get(
                "power_percent_yellow",
                _POWER_PERCENT_YELLOW_DEFAULT,
            )
        )
        orange_percent = int(
            power_config.get(
                "power_percent_orange",
                _POWER_PERCENT_ORANGE_DEFAULT,
            )
        )
        red_percent = int(
            power_config.get("power_percent_red", _POWER_PERCENT_RED_DEFAULT)
        )
        if not (
            math.isfinite(yellow_minutes)
            and math.isfinite(orange_minutes)
            and math.isfinite(red_minutes)
            and yellow_minutes > orange_minutes > red_minutes > 0.0
            and 100 >= yellow_percent > orange_percent > red_percent >= 0
        ):
            raise ValueError("power runway watermarks are not strictly ordered")
    except (TypeError, ValueError) as exc:
        return {
            "level": RiskLevel.DATA_DEGRADED.value,
            "status": "CONFIG_INVALID",
            "reason": f"{type(exc).__name__}:{exc}",
            "source": "pmset",
        }

    try:
        status = raw_status if raw_status is not None else _pmset_battery_status()
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "level": RiskLevel.DATA_DEGRADED.value,
            "status": "POWER_TRUTH_UNAVAILABLE",
            "reason": f"{type(exc).__name__}:{exc}",
            "source": "pmset",
        }

    lines = [line.strip() for line in str(status).splitlines() if line.strip()]
    source = ""
    if lines and "'" in lines[0]:
        source = lines[0].split("'", 2)[1].strip()
    battery_line = next((line for line in lines[1:] if "%" in line), "")
    try:
        percent_text = next(
            part.strip().removesuffix("%")
            for part in battery_line.split(";")
            if part.strip().endswith("%")
        )
        percent = int(percent_text.rsplit(None, 1)[-1])
    except (StopIteration, TypeError, ValueError):
        percent = None

    remaining_minutes: float | None = None
    for part in battery_line.split(";"):
        token = part.strip()
        if " remaining" not in token:
            continue
        clock = token.split(" remaining", 1)[0].strip()
        if ":" not in clock:
            continue
        hours_text, minutes_text = clock.split(":", 1)
        try:
            remaining_minutes = int(hours_text) * 60.0 + int(minutes_text)
        except ValueError:
            remaining_minutes = None
        break

    if source == "AC Power":
        return {
            "level": RiskLevel.GREEN.value,
            "status": "AC_POWER",
            "reason": None,
            "source": "pmset",
            "power_source": source,
            "battery_percent": percent,
            "remaining_minutes": remaining_minutes,
        }
    if source != "Battery Power" or percent is None:
        return {
            "level": RiskLevel.DATA_DEGRADED.value,
            "status": "POWER_TRUTH_INVALID",
            "reason": "POWER_SOURCE_OR_PERCENT_UNREADABLE",
            "source": "pmset",
            "power_source": source or None,
            "battery_percent": percent,
            "remaining_minutes": remaining_minutes,
        }

    level = RiskLevel.GREEN
    reason = None
    if percent <= red_percent or (
        remaining_minutes is not None and remaining_minutes <= red_minutes
    ):
        level = RiskLevel.RED
        reason = "HOST_EXECUTION_RUNWAY_CRITICAL"
    elif percent <= orange_percent or (
        remaining_minutes is not None and remaining_minutes <= orange_minutes
    ):
        level = RiskLevel.ORANGE
        reason = "HOST_EXECUTION_RUNWAY_SEVERE"
    elif percent <= yellow_percent or (
        remaining_minutes is not None and remaining_minutes <= yellow_minutes
    ):
        level = RiskLevel.YELLOW
        reason = "HOST_EXECUTION_RUNWAY_LOW"

    return {
        "level": level.value,
        "status": "BATTERY_POWER",
        "reason": reason,
        "source": "pmset",
        "power_source": source,
        "battery_percent": percent,
        "remaining_minutes": remaining_minutes,
        "yellow_minutes": yellow_minutes,
        "orange_minutes": orange_minutes,
        "red_minutes": red_minutes,
        "yellow_percent": yellow_percent,
        "orange_percent": orange_percent,
        "red_percent": red_percent,
    }


def storage_capacity_snapshot(path=None) -> dict[str, object]:
    """Return the live volume's entry-preserving capacity verdict.

    SCOPE: DATA_DEGRADED blocks new entries only; held monitoring, cancel,
    reduce-only SELL, reconciliation, and settlement keep running. DRAIN: an
    operator or retention job frees the volume while the 60-second RiskGuard
    tick keeps re-reading the same filesystem. RESET: the next successful read
    at or above both configured watermarks returns GREEN.
    """

    capacity_config = settings["riskguard"]
    try:
        min_free_bytes = int(
            capacity_config.get(
                "storage_entry_min_free_bytes",
                _STORAGE_ENTRY_MIN_FREE_BYTES_DEFAULT,
            )
        )
        min_free_ratio = float(
            capacity_config.get(
                "storage_entry_min_free_ratio",
                _STORAGE_ENTRY_MIN_FREE_RATIO_DEFAULT,
            )
        )
        if min_free_bytes < 0 or not 0.0 < min_free_ratio < 1.0:
            raise ValueError("storage entry watermarks are outside valid bounds")
    except (TypeError, ValueError) as exc:
        return {
            "level": RiskLevel.DATA_DEGRADED.value,
            "status": "CONFIG_INVALID",
            "reason": f"{type(exc).__name__}:{exc}",
            "path": str(path or RISK_DB_PATH.parent),
        }

    target = path or RISK_DB_PATH.parent
    try:
        usage = _disk_usage(target)
    except OSError as exc:
        return {
            "level": RiskLevel.DATA_DEGRADED.value,
            "status": "CAPACITY_UNAVAILABLE",
            "reason": f"{type(exc).__name__}:{exc}",
            "path": str(target),
            "min_free_bytes": min_free_bytes,
            "min_free_ratio": min_free_ratio,
        }

    ratio_required_bytes = int(usage.total * min_free_ratio)
    required_free_bytes = max(min_free_bytes, ratio_required_bytes)
    level = (
        RiskLevel.GREEN
        if usage.free >= required_free_bytes
        else RiskLevel.DATA_DEGRADED
    )
    return {
        "level": level.value,
        "status": "READY" if level == RiskLevel.GREEN else "LOW_DISK",
        "reason": None if level == RiskLevel.GREEN else "ENTRY_RESERVE_BREACHED",
        "path": str(target),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "free_ratio": float(usage.free / usage.total) if usage.total else 0.0,
        "required_free_bytes": required_free_bytes,
        "min_free_bytes": min_free_bytes,
        "min_free_ratio": min_free_ratio,
    }


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
        configure_global_ledger(
            CollateralLedger(
                db_path=_zeus_trade_db_path(),
                initialize_schema=False,
            )
        )
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
        entry_source = str(row.get("entry_economics_source") or "")
        if entry_source not in {"execution_fact", "position_current_chain_corrected"}:
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
    # different economic objects and trigger false RED.
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


def _realized_window_loss_telemetry(
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

    The telemetry remains settlement-based, not mark-to-market. The retired
    delta calculation conflated three economically-distinct moves into "loss":
      (a) capital deployment            wallet cash -> open-position equity,
      (b) projection-pipeline reshuffle unprojected entry fill -> projected,
      (c) mark-to-market swings         of open prediction-market positions.
    This function therefore returns no RiskLevel. Missing history degrades the
    record_only; it cannot block a current-evidence decision.
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
            # strategy outcome, so exclude it from the strategy telemetry.
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
    "probability_semantics",
    "storage_capacity",
    "host_power",
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
        if not settlement_economic_ready(row):
            continue
        pnl = row.get("pnl")
        if pnl is None:
            continue
        strategy = str(row.get("strategy") or row.get("strategy_key") or "")
        # PR-1 (COLLISION.md §唯一决策律): loss-eligibility is an ORIGIN predicate,
        # not a label predicate. A position counts against the daily-loss gates iff
        # Zeus's own decision opened it. It is NOT loss-eligible when a non-Zeus
        # origin opened it (operator_cotrade / external_wallet — foreign capital),
        # OR for the legacy chain_only_reconciliation label kept for continuity.
        # position_origin is NULL for historical rows, which stay label-scoped.
        position_origin = row.get("position_origin")
        loss_eligible = not (
            (position_origin is not None and str(position_origin) != "zeus_decision")
            or strategy == "chain_only_reconciliation"
        )
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
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    cutoff = now_dt.astimezone(timezone.utc) - _ENTRY_EXECUTION_LOOKBACK
    # ``datetime(occurred_at)`` on both sides made SQLite convert and temp-sort
    # every historical position_event on each 60-second RiskGuard tick.  Read a
    # timezone-safe one-day superset with a cheap textual date floor, then do
    # exact UTC filtering/order/limit in Python.
    broad_cutoff = (cutoff - timedelta(days=1)).date().isoformat()
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
              AND occurred_at >= ?
              AND occurred_at LIKE '____-__-__T%'
            """,
            (broad_cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    recent_rows: list[tuple[datetime, sqlite3.Row]] = []
    for row in rows:
        try:
            occurred_at = datetime.fromisoformat(
                str(row["occurred_at"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        occurred_at = occurred_at.astimezone(timezone.utc)
        if occurred_at >= cutoff:
            recent_rows.append((occurred_at, row))
    recent_rows.sort(key=lambda item: item[0], reverse=True)
    rows = [row for _, row in recent_rows[:limit]]

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
            # Exact UTC timestamps were sorted newest-first above, so the first
            # terminal event seen is newest globally and for its strategy.
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

    Held-token payout truth and physical settlement truth are different
    surfaces. An exact venue resolution can grade the frozen held-side q
    immediately even before the source publishes the final temperature; it
    remains ineligible for physical calibration through ``metric_ready=False``.
    A settlement row without its decision snapshot must not displace a
    learning-ready row in the Brier sample.

    A frozen probability value without its ``venue_commands.q_version`` is also
    not learning lineage. It cannot prove which q authorized the order, so it is
    non_actuating and may not convict the currently executing probability
    system. ``_bind_brier_probability_identities`` establishes that proof before
    this filter runs.
    """

    metric_rows: list[dict] = []
    for row in rows:
        if not row.get("learning_snapshot_ready", False):
            continue
        probability_outcome_ready = row.get("probability_outcome_ready")
        if probability_outcome_ready is None:
            probability_outcome_ready = row.get("metric_ready", True)
        if not probability_outcome_ready:
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
    probability system only when the actual filled ENTRY commands carry
    reproducible q identities. Multiple fills into one position are one
    settlement observation, not independent samples: their submit-time
    ``q_live`` values are share-weighted into the probability of the actual
    acquired payoff. Rejected/unfilled commands contribute neither probability
    nor weight.

    Actuation additionally requires the persisted
    ``position_current.decision_law_id``; q content identity must not be misread
    as proof of which decision law produced an old position. Missing and
    ambiguous identities remain visible and are excluded.
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
    composites = _filled_entry_probability_composites(conn, unresolved)
    composite_blocked: set[str] = set()
    for row in output:
        trade_id = str(row.get("trade_id") or "").strip()
        composite = composites.get(trade_id)
        if composite is None:
            continue
        blocked_reason = str(composite.get("blocked_reason") or "").strip()
        if blocked_reason:
            row["probability_identity_ready"] = False
            row["entry_q_version"] = ""
            row["probability_identity_source"] = (
                "filled_entry_commands.q_version+submit_q_live+fill_shares"
            )
            row["probability_identity_blocked_reason"] = blocked_reason
            composite_blocked.add(trade_id)
            continue
        row["p_posterior"] = composite["p_posterior"]
        row["probability_identity_ready"] = True
        row["entry_q_version"] = composite["entry_q_version"]
        row["entry_q_versions"] = composite["entry_q_versions"]
        row["probability_identity_source"] = (
            "filled_entry_commands.q_version+submit_q_live+fill_shares"
        )
        row["probability_identity_blocked_reason"] = ""

    unresolved = {
        str(row.get("trade_id") or "")
        for row in output
        if not (
            row.get("probability_identity_ready") is True
            and str(row.get("entry_q_version") or "").strip()
        )
        and str(row.get("trade_id") or "").strip()
        and str(row.get("trade_id") or "").strip() not in composite_blocked
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
        trade_id = str(row.get("trade_id") or "").strip()
        if trade_id in composite_blocked:
            continue
        if (
            row.get("probability_identity_ready") is True
            and str(row.get("entry_q_version") or "").strip()
        ):
            continue
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

    unresolved_law = {
        str(row.get("trade_id") or "")
        for row in output
        if str(row.get("trade_id") or "").strip()
    }
    law_bindings: dict[str, str | None] = {
        trade_id: None for trade_id in unresolved_law
    }
    law_schema_ready = False
    if unresolved_law and _table_exists(conn, "position_current"):
        try:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
            }
            law_schema_ready = {"position_id", "decision_law_id"}.issubset(columns)
        except sqlite3.Error:
            law_schema_ready = False
    if law_schema_ready:
        trade_ids = sorted(unresolved_law)
        for start in range(0, len(trade_ids), 500):
            chunk = trade_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            try:
                law_rows = conn.execute(
                    "SELECT position_id,decision_law_id FROM position_current "
                    f"WHERE position_id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall()
            except sqlite3.Error:
                law_schema_ready = False
                break
            for law_row in law_rows:
                position_id = str(law_row[0] or "")
                if position_id in law_bindings:
                    law_bindings[position_id] = (
                        str(law_row[1] or "").strip() or None
                    )

    for row in output:
        trade_id = str(row.get("trade_id") or "").strip()
        bound_law = law_bindings.get(trade_id)
        row["decision_law_id"] = bound_law or ""
        row["decision_law_identity_ready"] = bound_law in DECISION_LAW_IDS
        row["decision_law_identity_source"] = "position_current.decision_law_id"
        if not law_schema_ready:
            reason = "decision_law_schema_unavailable"
        elif bound_law is None:
            reason = "decision_law_id_missing"
        else:
            reason = "decision_law_id_unknown"
        row["decision_law_identity_blocked_reason"] = (
            "" if row["decision_law_identity_ready"] else reason
        )
        if (
            row.get("probability_identity_ready") is True
            and not row.get("entry_q_versions")
        ):
            q_version = str(row.get("entry_q_version") or "").strip()
            if q_version and not q_version.startswith("filled-entry-composite:"):
                row["entry_q_versions"] = (q_version,)
    return output


def _bind_qkernel_probability_semantics(
    rows: list[dict],
    *,
    forecasts_connection_factory=get_forecasts_connection_read_only,
) -> tuple[list[dict], dict[str, object]]:
    """Bind qkernel settlement rows to the probability law that emitted them.

    Strategy is the governance identity; posterior semantics are metric
    lineage. A settlement grades only the probability mechanism that produced
    its filled entries. Superseded current-evidence shapes remain telemetry but
    cannot convict the replacement chain now eligible to trade.

    SCOPE: only ``forecast_qkernel_entry`` Brier actuation.
    DRAIN: every 60-second RiskGuard tick re-reads immutable posterior
    provenance for the bounded settlement sample.
    RESET: a successful read classifies each q_version; the DATA_DEGRADED
    fallback clears on the first successful lookup.
    """

    output = [dict(row) for row in rows]
    accepted_revisions = set(LIVE_CURRENT_EVIDENCE_SEMANTICS_REVISIONS)
    qkernel_rows = [
        row
        for row in output
        if str(row.get("strategy") or "").strip() == "forecast_qkernel_entry"
    ]
    candidates = [
        row
        for row in qkernel_rows
        if not isinstance(row.get("probability_semantics_ready"), bool)
    ]
    status: dict[str, object] = {
        "status": "not_applicable",
        "licensed_revisions": sorted(accepted_revisions),
        "strategy_candidate_count": len(qkernel_rows),
        "current_count": sum(
            row.get("probability_semantics_ready") is True for row in qkernel_rows
        ),
        "superseded_count": sum(
            row.get("probability_semantics_ready") is False for row in qkernel_rows
        ),
        "missing_count": 0,
        "mixed_count": 0,
    }
    if not qkernel_rows:
        return output, status
    if not candidates:
        status["status"] = "ok"
        return output, status

    versions = sorted(
        {
            str(version).strip()
            for row in candidates
            for version in (row.get("entry_q_versions") or ())
            if str(version).strip()
        }
    )
    if not versions:
        for row in candidates:
            row["probability_semantics_ready"] = False
            row["probability_semantics_revisions"] = ()
            row["probability_semantics_blocked_reason"] = (
                "entry_q_version_lineage_missing"
            )
        status.update(status="ok", missing_count=len(candidates))
        return output, status

    provenance_by_version: dict[str, object] = {}
    conn: sqlite3.Connection | None = None
    try:
        conn = forecasts_connection_factory()
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=250")
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(forecast_posteriors)").fetchall()
        }
        if not {"posterior_identity_hash", "provenance_json"}.issubset(columns):
            raise sqlite3.OperationalError(
                "forecast_posteriors probability provenance schema unavailable"
            )
        for start in range(0, len(versions), 500):
            chunk = versions[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            for q_version, provenance_json in conn.execute(
                "SELECT posterior_identity_hash,provenance_json "
                "FROM forecast_posteriors "
                f"WHERE posterior_identity_hash IN ({placeholders})",
                tuple(chunk),
            ).fetchall():
                provenance_by_version[str(q_version)] = provenance_json
    except (OSError, sqlite3.Error) as exc:
        for row in candidates:
            row["probability_semantics_ready"] = False
            row["probability_semantics_revisions"] = ()
            row["probability_semantics_blocked_reason"] = (
                "probability_semantics_authority_unavailable"
            )
        status.update(
            status="unavailable",
            missing_count=len(candidates),
            error=type(exc).__name__,
        )
        return output, status
    finally:
        if conn is not None:
            conn.close()

    version_lineage: dict[str, tuple[str, str]] = {}
    for q_version in versions:
        provenance = provenance_by_version.get(q_version)
        shape = _current_evidence_shape(provenance)
        if shape is None:
            version_lineage[q_version] = ("missing", "")
            continue
        revision = str(shape.get("semantics_revision") or "").strip()
        current = (
            revision in accepted_revisions
            and shape.get("translation_applied") is False
            and not current_evidence_shape_semantics_mismatch(provenance)
        )
        version_lineage[q_version] = (
            "current" if current else "superseded",
            revision,
        )

    for row in candidates:
        row_versions = tuple(
            str(version).strip()
            for version in (row.get("entry_q_versions") or ())
            if str(version).strip()
        )
        lineages = [version_lineage.get(version, ("missing", "")) for version in row_versions]
        kinds = {kind for kind, _revision in lineages}
        revisions = tuple(sorted({revision for _kind, revision in lineages if revision}))
        row["probability_semantics_revisions"] = revisions
        if row_versions and kinds == {"current"}:
            classification = "current"
            row["probability_semantics_ready"] = True
            row["probability_semantics_blocked_reason"] = ""
        elif kinds == {"superseded"}:
            classification = "superseded"
            row["probability_semantics_ready"] = False
            row["probability_semantics_blocked_reason"] = (
                "superseded_probability_semantics"
            )
        elif not row_versions:
            classification = "missing"
            row["probability_semantics_ready"] = False
            row["probability_semantics_blocked_reason"] = (
                "entry_q_version_lineage_missing"
            )
        elif kinds == {"missing"}:
            classification = "missing"
            row["probability_semantics_ready"] = False
            row["probability_semantics_blocked_reason"] = (
                "probability_semantics_provenance_missing"
            )
        else:
            classification = "mixed"
            row["probability_semantics_ready"] = False
            row["probability_semantics_blocked_reason"] = (
                "mixed_probability_semantics"
            )
        count_key = f"{classification}_count"
        status[count_key] = int(status[count_key]) + 1
    status["status"] = "ok"
    return output, status


def _bind_day0_probability_semantics(
    rows: list[dict],
) -> tuple[list[dict], dict[str, object]]:
    """Classify Day0 settlements by the mechanism stamped at ENTRY fill.

    Old hashes remain valid historical evidence, but they cannot convict the
    current mechanism.  Mixed-version fills are non-actuating because no one
    probability mechanism owns their share-weighted q.

    SCOPE: current-law ``day0_nowcast_entry`` settlement learning only.
    DRAIN: each RiskGuard tick rebinds immutable filled ENTRY q_versions.
    RESET: a settled fill stamped with the current revision enters the cohort;
    superseded rows remain telemetry and age naturally with the bounded scan.
    """

    from src.events.day0_authority import (
        DAY0_PROBABILITY_SEMANTICS_REVISION,
        day0_probability_semantics_revision,
    )

    output = [dict(row) for row in rows]
    candidates = [
        row
        for row in output
        if str(row.get("strategy") or "").strip() == "day0_nowcast_entry"
    ]
    counts = {"current": 0, "superseded": 0, "missing": 0, "mixed": 0}
    for row in candidates:
        versions = tuple(
            str(version).strip()
            for version in (row.get("entry_q_versions") or ())
            if str(version).strip()
        )
        parsed_revisions = tuple(
            day0_probability_semantics_revision(version) for version in versions
        )
        revisions = tuple(
            sorted({revision for revision in parsed_revisions if revision})
        )
        row["probability_semantics_revisions"] = revisions
        if not versions:
            classification = "missing"
            reason = "entry_q_version_lineage_missing"
        elif not revisions:
            classification = "superseded"
            reason = "superseded_probability_semantics"
        elif all(
            revision == DAY0_PROBABILITY_SEMANTICS_REVISION
            for revision in parsed_revisions
        ):
            classification = "current"
            reason = ""
        elif DAY0_PROBABILITY_SEMANTICS_REVISION in parsed_revisions:
            classification = "mixed"
            reason = "mixed_probability_semantics"
        else:
            classification = "superseded"
            reason = "superseded_probability_semantics"
        counts[classification] += 1
        row["probability_semantics_ready"] = classification == "current"
        row["probability_semantics_blocked_reason"] = reason
    return output, {
        "status": "ok" if candidates else "not_applicable",
        "strategy_candidate_count": len(candidates),
        "current_count": counts["current"],
        "superseded_count": counts["superseded"],
        "missing_count": counts["missing"],
        "mixed_count": counts["mixed"],
        "current_revision": DAY0_PROBABILITY_SEMANTICS_REVISION,
    }


def _filled_entry_probability_composites(
    conn: sqlite3.Connection,
    position_ids: set[str],
) -> dict[str, dict[str, object]]:
    """Rebuild one payoff probability from every economically filled entry.

    The submit provenance is the immutable decision-time q witness; execution
    facts provide the shares that actually became exposure. A position is
    returned only when every filled entry has a finite q/version/weight. An
    explicit FILLED command with incomplete execution/provenance evidence is
    returned as blocked and may not be laundered through the q-version fallback.
    """

    required_columns = {
        "venue_commands": {
            "position_id",
            "command_id",
            "intent_kind",
            "q_version",
            "state",
        },
        "execution_fact": {
            "command_id",
            "order_role",
            "filled_at",
            "terminal_exec_status",
            "shares",
        },
        "provenance_envelope_events": {
            "subject_type",
            "subject_id",
            "event_type",
            "local_sequence",
            "payload_json",
        },
    }
    if not position_ids or any(
        not _table_exists(conn, table) for table in required_columns
    ):
        return {}
    try:
        for table, required in required_columns.items():
            columns = {
                str(row[1])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not required.issubset(columns):
                return {}
    except sqlite3.Error:
        return {}

    parts: dict[str, list[dict[str, object]]] = {
        position_id: [] for position_id in position_ids
    }
    invalid: set[str] = set()
    ids = sorted(position_ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        try:
            command_rows = conn.execute(
                """
                SELECT vc.position_id,vc.command_id,vc.q_version,vc.state,
                       ef.filled_at,ef.terminal_exec_status,ef.shares,
                       ef.min_shares,ef.max_shares,
                       (
                         SELECT pee.payload_json
                         FROM provenance_envelope_events AS pee
                         WHERE pee.subject_type='command'
                           AND pee.subject_id=vc.command_id
                           AND pee.event_type='SUBMIT_REQUESTED'
                         ORDER BY pee.local_sequence DESC
                         LIMIT 1
                       ) AS submit_payload_json
                FROM venue_commands AS vc
                LEFT JOIN (
                    SELECT command_id,
                           MAX(
                               CASE
                                   WHEN filled_at IS NOT NULL
                                    AND lower(COALESCE(terminal_exec_status,''))
                                        IN ('filled','confirmed','partial')
                                   THEN filled_at
                               END
                           ) AS filled_at,
                           CASE
                               WHEN SUM(
                                   CASE
                                       WHEN filled_at IS NOT NULL
                                        AND lower(COALESCE(terminal_exec_status,''))
                                            IN ('filled','confirmed','partial')
                                       THEN 1 ELSE 0
                                   END
                               ) > 0
                               THEN 'filled' ELSE ''
                           END AS terminal_exec_status,
                           MAX(
                               CASE
                                   WHEN filled_at IS NOT NULL
                                    AND lower(COALESCE(terminal_exec_status,''))
                                        IN ('filled','confirmed','partial')
                                   THEN shares
                               END
                           ) AS shares,
                           MIN(
                               CASE
                                   WHEN filled_at IS NOT NULL
                                    AND lower(COALESCE(terminal_exec_status,''))
                                        IN ('filled','confirmed','partial')
                                   THEN shares
                               END
                           ) AS min_shares,
                           MAX(
                               CASE
                                   WHEN filled_at IS NOT NULL
                                    AND lower(COALESCE(terminal_exec_status,''))
                                        IN ('filled','confirmed','partial')
                                   THEN shares
                               END
                           ) AS max_shares
                    FROM execution_fact
                    WHERE order_role='entry'
                    GROUP BY command_id
                ) AS ef ON ef.command_id=vc.command_id
                WHERE vc.intent_kind='ENTRY'
                  AND vc.position_id IN ("""
                + placeholders
                + ")",
                tuple(chunk),
            ).fetchall()
        except sqlite3.Error:
            return {}

        for command_row in command_rows:
            position_id = str(command_row[0] or "").strip()
            command_id = str(command_row[1] or "").strip()
            q_version = str(command_row[2] or "").strip()
            venue_filled = str(command_row[3] or "").strip().lower() == "filled"
            fact_filled = (
                command_row[4] is not None
                and str(command_row[5] or "").strip().lower() == "filled"
            )
            if not venue_filled and not fact_filled:
                continue
            try:
                shares = float(command_row[6])
                min_shares = float(command_row[7])
                max_shares = float(command_row[8])
                payload = json.loads(command_row[9])
                components = (
                    payload.get("payload", {})
                    .get("execution_capability", {})
                    .get("components", [])
                )
                economics = next(
                    component
                    for component in components
                    if component.get("component") == "entry_economics"
                )
                q_live = float(economics["details"]["q_live"])
            except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError):
                invalid.add(position_id)
                continue
            if (
                not position_id
                or not command_id
                or not q_version
                or not shares > 0.0
                or abs(max_shares - min_shares)
                > max(1e-9, abs(max_shares) * 1e-9)
                or not 0.0 <= q_live <= 1.0
            ):
                invalid.add(position_id)
                continue
            parts[position_id].append(
                {
                    "command_id": command_id,
                    "q_version": q_version,
                    "q_live": q_live,
                    "shares": shares,
                }
            )

    composites: dict[str, dict[str, object]] = {}
    for position_id, entries in parts.items():
        if position_id in invalid:
            composites[position_id] = {
                "blocked_reason": "filled_entry_evidence_incomplete",
            }
            continue
        if not entries:
            continue
        total_shares = sum(float(entry["shares"]) for entry in entries)
        p_posterior = sum(
            float(entry["shares"]) * float(entry["q_live"]) for entry in entries
        ) / total_shares
        identity_payload = sorted(entries, key=lambda entry: str(entry["command_id"]))
        identity = hashlib.sha256(
            json.dumps(
                identity_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        composites[position_id] = {
            "p_posterior": p_posterior,
            "entry_q_version": f"filled-entry-composite:{identity}",
            "entry_q_versions": tuple(
                sorted({str(entry["q_version"]) for entry in entries})
            ),
        }
    return composites


def _riskguard_brier_actuating_rows(
    rows: list[dict],
    *,
    limit: int = RISKGUARD_SETTLEMENT_LIMIT,
) -> list[dict]:
    """Return Brier rows proven to belong to a current executable law.

    Legacy rows without a persisted law identity remain useful telemetry. They
    cannot convict a newer law merely because both laws emitted a q_version or
    reused the same decision-snapshot namespace.
    """

    actuating: list[dict] = []
    for row in rows:
        if not row.get("probability_identity_ready", False):
            continue
        if not row.get("decision_law_identity_ready", False):
            continue
        if str(row.get("decision_law_id") or "").strip() not in DECISION_LAW_IDS:
            continue
        if str(row.get("strategy") or "").strip() in {
            "forecast_qkernel_entry",
            "day0_nowcast_entry",
        } and row.get("probability_semantics_ready") is not True:
            continue
        actuating.append(row)
        if len(actuating) >= limit:
            break
    return actuating


def _bind_entry_market_benchmarks(
    conn: sqlite3.Connection,
    rows: list[dict],
) -> list[dict]:
    """Bind each settled probability claim to its canonical entry fills."""

    output = [dict(row) for row in rows]
    trade_ids = sorted(
        {
            str(row.get("trade_id") or "").strip()
            for row in output
            if str(row.get("trade_id") or "").strip()
        }
    )
    bindings: dict[str, tuple[float, str, str, str]] = {}
    required_columns = {
        "position_current": {
            "position_id",
            "city",
            "target_date",
            "temperature_metric",
        },
        "execution_fact": {
            "position_id",
            "order_role",
            "filled_at",
            "terminal_exec_status",
            "fill_price",
            "shares",
        },
    }
    schema_ready = bool(trade_ids) and all(
        _table_exists(conn, table)
        and required.issubset(
            {
                str(column[1])
                for column in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
        )
        for table, required in required_columns.items()
    )
    if schema_ready:
        fill_parts: dict[str, list[tuple[float, float]]] = {
            trade_id: [] for trade_id in trade_ids
        }
        identities: dict[str, tuple[str, str, str]] = {}
        invalid: set[str] = set()
        for start in range(0, len(trade_ids), 500):
            chunk = trade_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            for bound in conn.execute(
                "SELECT pc.position_id,ef.fill_price,ef.shares,"
                "pc.city,pc.target_date,pc.temperature_metric "
                "FROM position_current AS pc "
                "LEFT JOIN execution_fact AS ef ON ef.position_id=pc.position_id "
                "AND ef.order_role='entry' AND ef.filled_at IS NOT NULL "
                "AND lower(COALESCE(ef.terminal_exec_status,'')) "
                "IN ('filled','confirmed','partial') "
                f"WHERE pc.position_id IN ({placeholders})",
                tuple(chunk),
            ).fetchall():
                trade_id = str(bound[0] or "").strip()
                identities[trade_id] = (
                    str(bound[3] or "").strip(),
                    str(bound[4] or "").strip(),
                    str(bound[5] or "").strip(),
                )
                try:
                    price = float(bound[1])
                    shares = float(bound[2])
                except (TypeError, ValueError):
                    invalid.add(trade_id)
                    continue
                if (
                    not math.isfinite(price)
                    or not math.isfinite(shares)
                    or not 0.0 < price < 1.0
                    or shares <= 0.0
                ):
                    invalid.add(trade_id)
                    continue
                fill_parts[trade_id].append((price, shares))

        for trade_id, parts in fill_parts.items():
            identity = identities.get(trade_id)
            if trade_id in invalid or not parts or identity is None or not all(identity):
                continue
            total_shares = sum(shares for _price, shares in parts)
            price = sum(price * shares for price, shares in parts) / total_shares
            city, target_date, metric = identity
            bindings[trade_id] = (price, city, target_date, metric)

    for row in output:
        binding = bindings.get(str(row.get("trade_id") or "").strip())
        row["entry_market_benchmark_ready"] = binding is not None
        if binding is None:
            row["entry_market_benchmark"] = None
            row["entry_market_benchmark_family"] = ()
            continue
        price, city, target_date, metric = binding
        row["entry_market_benchmark"] = price
        row["entry_market_benchmark_family"] = (city, target_date, metric)
    return output


def _settled_market_relative_alpha_shadow_rows(
    conn: sqlite3.Connection,
    *,
    strategy_key: str,
    window_days: float,
    as_of: datetime | None = None,
    forecasts_connection_factory=get_forecasts_connection_read_only,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Bind frozen no-money decisions to later verified venue outcomes."""

    from src.events.day0_authority import (
        DAY0_PROBABILITY_SEMANTICS_REVISION,
        day0_probability_semantics_revision,
    )

    if strategy_key == "day0_nowcast_entry":
        expected_revisions = {DAY0_PROBABILITY_SEMANTICS_REVISION}
        expected_source_status = "current_day0_probability_authority"
    elif strategy_key == "forecast_qkernel_entry":
        expected_revisions = set(LIVE_CURRENT_EVIDENCE_SEMANTICS_REVISIONS)
        expected_source_status = "current_qkernel_probability_authority"
    else:
        raise ValueError("market-relative alpha shadow strategy is not canonical")
    expected_reason = f"MARKET_RELATIVE_ALPHA_SHADOW:{strategy_key}"

    evaluated_at = as_of or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    database_names = {
        str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()
    }
    schema = "world" if "world" in database_names else "main"
    table_ready = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master "
        "WHERE type='table' AND name='no_trade_regret_events' LIMIT 1"
    ).fetchone()
    status: dict[str, object] = {
        "status": "no_shadow_evidence",
        "strategy_key": strategy_key,
        "source_schema": schema,
        "shadow_candidate_count": 0,
        "certificate_ready_count": 0,
        "settlement_ready_count": 0,
        "blocked_reasons": {},
    }
    if table_ready is None:
        status["status"] = "shadow_table_unavailable"
        return [], status

    cutoff = evaluated_at - timedelta(days=window_days + 2.0)
    column_names = (
        "regret_event_id",
        "event_id",
        "rejection_stage",
        "rejection_reason",
        "condition_id",
        "token_id",
        "decision_time",
        "city",
        "target_date",
        "metric",
        "family_id",
        "bin_label",
        "direction",
        "q_live",
        "c_fee_adjusted",
        "native_quote_available",
        "source_status",
        "family_complete",
        "hypothetical_order_type",
        "hypothetical_fill_status",
        "hypothetical_fill_price",
        "causal_snapshot_id",
        "executable_snapshot_id",
        "envelope_json",
        "created_at",
    )
    raw_rows = conn.execute(
        f"SELECT {','.join(column_names)} "
        f"FROM {schema}.no_trade_regret_events "
        "INDEXED BY idx_no_trade_regret_stage "
        "WHERE rejection_stage='RISK_GUARD' "
        "AND rejection_reason=? AND created_at>=? "
        "ORDER BY created_at,regret_event_id",
        (
            expected_reason,
            cutoff.isoformat(),
        ),
    ).fetchall()
    status["shadow_candidate_count"] = len(raw_rows)
    blocked: dict[str, int] = {}

    def block(reason: str) -> None:
        blocked[reason] = blocked.get(reason, 0) + 1

    certificates: list[dict[str, object]] = []
    for raw in raw_rows:
        row = dict(zip(column_names, raw))
        try:
            envelope = json.loads(str(row["envelope_json"] or ""))
        except (TypeError, ValueError):
            block("envelope_invalid")
            continue
        if not isinstance(envelope, Mapping):
            block("envelope_invalid")
            continue
        revision = str(
            envelope.get("probability_semantics_revision") or ""
        )
        q_version = str(envelope.get("q_version") or "")
        posterior_identity_hash = str(
            envelope.get("posterior_identity_hash") or ""
        )
        side = str(envelope.get("side") or "").upper()
        expected_fields = {
            "family_key": row["family_id"],
            "city": row["city"],
            "target_date": row["target_date"],
            "metric": row["metric"],
            "bin_id": row["bin_label"],
            "condition_id": row["condition_id"],
            "token_id": row["token_id"],
        }
        if envelope.get("decision_law_id") != "executable_min_order_capital_gain_v2":
            block("superseded_decision_law")
            continue
        if (
            envelope.get("global_selection_revision")
            != CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        ):
            block("global_selection_revision_mismatch")
            continue
        revision_identity_ready = (
            day0_probability_semantics_revision(q_version) == revision
            if strategy_key == "day0_nowcast_entry"
            else bool(q_version and posterior_identity_hash)
        )
        if (
            envelope.get("schema_version") != 3
            or envelope.get("strategy_key") != strategy_key
            or envelope.get("selection_rule")
            != (
                "earliest_complete_global_cut_exact_global_posterior_mean_"
                "expected_growth_winner_v3"
            )
            or revision not in expected_revisions
            or not revision_identity_ready
            or side not in {"YES", "NO"}
            or any(
                str(envelope.get(key) or "") != str(value or "")
                for key, value in expected_fields.items()
            )
            or str(row["direction"] or "") != f"buy_{side.lower()}"
            or str(row["causal_snapshot_id"] or "")
            != str(envelope.get("probability_witness_identity") or "")
            or str(row["executable_snapshot_id"] or "")
            != str(envelope.get("book_snapshot_id") or "")
            or int(row["native_quote_available"] or 0) != 1
            or int(row["family_complete"] or 0) != 1
            or row["source_status"] != expected_source_status
            or row["hypothetical_order_type"] != "MARKETABLE_LIMIT"
            or row["hypothetical_fill_status"] != "EXECUTABLE_AT_DECISION"
        ):
            block("certificate_identity_mismatch")
            continue
        try:
            q = float(row["q_live"])
            market = float(row["hypothetical_fill_price"])
            envelope_q = float(envelope["q"])
            envelope_market = float(envelope["raw_min_order_vwap"])
            fee_adjusted_cost = float(envelope["fee_adjusted_min_order_cost"])
            row_fee_adjusted_cost = float(row["c_fee_adjusted"])
            min_order_size = float(envelope["min_order_size"])
            expected_net_edge = float(envelope["expected_net_edge_per_share"])
            proof_candidate_id = str(envelope["global_proof_candidate_id"])
            proof_execution_mode = str(
                envelope["global_proof_execution_mode"]
            )
            proof_shares = float(envelope["global_proof_shares"])
            proof_cost = float(envelope["global_proof_cost_usd"])
            proof_delta_log_wealth = float(
                envelope["global_proof_expected_delta_log_wealth"]
            )
            proof_ev_usd = float(envelope["global_proof_expected_ev_usd"])
            decision_time = datetime.fromisoformat(
                str(row["decision_time"] or "").replace("Z", "+00:00")
            )
            created_at = datetime.fromisoformat(
                str(row["created_at"] or "").replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            block("certificate_value_invalid")
            continue
        if decision_time.tzinfo is None:
            decision_time = decision_time.replace(tzinfo=timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if (
            not all(
                math.isfinite(value)
                for value in (
                    q,
                    market,
                    fee_adjusted_cost,
                    min_order_size,
                    expected_net_edge,
                    proof_shares,
                    proof_cost,
                    proof_delta_log_wealth,
                    proof_ev_usd,
                )
            )
            or not 0.0 <= q <= 1.0
            or not 0.0 < market < 1.0
            or not 0.0 < fee_adjusted_cost < 1.0
            or min_order_size <= 0.0
            or expected_net_edge <= 0.0
            or envelope.get("global_proof_winner") is not True
            or not proof_candidate_id
            or proof_execution_mode != "TAKER_LIMIT"
            or proof_shares <= 0.0
            or proof_cost <= 0.0
            or not 0.0 < proof_cost / proof_shares < 1.0
            or proof_delta_log_wealth <= 0.0
            or proof_ev_usd <= 0.0
            or not math.isclose(
                q - fee_adjusted_cost,
                expected_net_edge,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                row_fee_adjusted_cost,
                fee_adjusted_cost,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(q, envelope_q, rel_tol=0.0, abs_tol=1e-12)
            or not math.isclose(
                market, envelope_market, rel_tol=0.0, abs_tol=1e-12
            )
            or str(envelope.get("decision_at_utc") or "")
            != str(row["decision_time"] or "")
            or decision_time > evaluated_at
            or created_at > evaluated_at
        ):
            block("certificate_value_mismatch")
            continue
        certificates.append(
            {
                **row,
                "envelope": envelope,
                "q": q,
                "market": market,
                "fee_adjusted_cost": fee_adjusted_cost,
                "min_order_size": min_order_size,
                "expected_net_edge": expected_net_edge,
                "side": side,
                "decision_time_parsed": decision_time,
                "created_at_parsed": created_at,
            }
        )
    if not certificates:
        status["certificate_ready_count"] = 0
        status["blocked_reasons"] = blocked
        return [], status
    if strategy_key == "forecast_qkernel_entry":
        probes = [
            {
                "trade_id": str(row["regret_event_id"]),
                "strategy": strategy_key,
                "entry_q_versions": (
                    str(row["envelope"]["posterior_identity_hash"]),
                ),
            }
            for row in certificates
        ]
        classified, semantics_binding = _bind_qkernel_probability_semantics(
            probes,
            forecasts_connection_factory=forecasts_connection_factory,
        )
        status["probability_semantics_binding"] = semantics_binding
        current_revisions = {
            str(row["trade_id"]): tuple(
                str(revision)
                for revision in (row.get("probability_semantics_revisions") or ())
            )
            for row in classified
            if row.get("probability_semantics_ready") is True
        }
        current_certificates = []
        for row in certificates:
            revisions = current_revisions.get(str(row["regret_event_id"]))
            certificate_revision = str(
                row["envelope"].get("probability_semantics_revision") or ""
            )
            if revisions != (certificate_revision,):
                block("probability_semantics_not_current")
                continue
            row["probability_semantics_revisions"] = revisions
            current_certificates.append(row)
        certificates = current_certificates
    else:
        for row in certificates:
            row["probability_semantics_revisions"] = (
                str(row["envelope"]["probability_semantics_revision"]),
            )
    status["certificate_ready_count"] = len(certificates)
    if not certificates:
        status["blocked_reasons"] = blocked
        return [], status

    condition_ids = sorted(
        {str(row["condition_id"]) for row in certificates}
    )
    settlement_by_condition: dict[str, list[tuple[object, ...]]] = {}
    forecasts_conn: sqlite3.Connection | None = None
    try:
        forecasts_conn = forecasts_connection_factory()
        forecasts_conn.execute("PRAGMA query_only=ON")
        forecasts_conn.execute("PRAGMA busy_timeout=250")
        for start in range(0, len(condition_ids), 500):
            chunk = condition_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = forecasts_conn.execute(
                "SELECT me.condition_id,me.city,me.target_date,"
                "me.temperature_metric,me.outcome,so.settled_at "
                "FROM market_events me JOIN settlement_outcomes so "
                "ON so.city=me.city AND so.target_date=me.target_date "
                "AND so.temperature_metric=me.temperature_metric "
                f"WHERE me.condition_id IN ({placeholders}) "
                "AND me.outcome IN ('YES','NO') "
                "AND so.authority='VERIFIED'",
                tuple(chunk),
            ).fetchall()
            for outcome_row in rows:
                settlement_by_condition.setdefault(
                    str(outcome_row[0]), []
                ).append(tuple(outcome_row))
    except (OSError, sqlite3.Error) as exc:
        status.update(
            status="settlement_authority_unavailable",
            blocked_reasons={
                **blocked,
                f"settlement_authority_{type(exc).__name__}": len(certificates),
            },
        )
        return [], status
    finally:
        if forecasts_conn is not None:
            forecasts_conn.close()

    output: list[dict[str, object]] = []
    for row in certificates:
        matches = settlement_by_condition.get(str(row["condition_id"]), [])
        exact = [
            match
            for match in matches
            if (
                str(match[1]) == str(row["city"])
                and str(match[2]) == str(row["target_date"])
                and str(match[3]).lower() == str(row["metric"]).lower()
            )
        ]
        if len(exact) != 1:
            block("settlement_missing_or_ambiguous")
            continue
        _condition_id, _city, _date, _metric, venue_outcome, settled_at_raw = exact[0]
        try:
            settled_at = datetime.fromisoformat(
                str(settled_at_raw or "").replace("Z", "+00:00")
            )
        except ValueError:
            block("settlement_time_invalid")
            continue
        if settled_at.tzinfo is None:
            settled_at = settled_at.replace(tzinfo=timezone.utc)
        if (
            settled_at <= row["decision_time_parsed"]
            or settled_at <= row["created_at_parsed"]
            or settled_at > evaluated_at
        ):
            block("settlement_not_strictly_later")
            continue
        output.append(
            {
                "trade_id": str(row["regret_event_id"]),
                "strategy": strategy_key,
                "probability_semantics_ready": True,
                "probability_semantics_revisions": row[
                    "probability_semantics_revisions"
                ],
                "decision_law_id": "executable_min_order_capital_gain_v2",
                "global_selection_revision": (
                    CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                ),
                "settled_at": settled_at.isoformat(),
                "entry_market_benchmark_ready": True,
                "entry_market_benchmark": row["market"],
                "entry_market_benchmark_family": (
                    str(row["city"]),
                    str(row["target_date"]),
                    str(row["metric"]),
                ),
                "p_posterior": row["q"],
                "outcome": int(str(venue_outcome).upper() == row["side"]),
                "capital_gain_proof_ready": True,
                "hypothetical_min_order_size": row["min_order_size"],
                "hypothetical_capital_committed_usd": (
                    row["fee_adjusted_cost"] * row["min_order_size"]
                ),
                "hypothetical_settlement_payout_usd": (
                    int(str(venue_outcome).upper() == row["side"])
                    * row["min_order_size"]
                ),
                "hypothetical_realized_pnl_usd": (
                    (
                        int(str(venue_outcome).upper() == row["side"])
                        - row["fee_adjusted_cost"]
                    )
                    * row["min_order_size"]
                ),
                "evidence_source": (
                    "no_trade_regret_events_day0_shadow_v3"
                    if strategy_key == "day0_nowcast_entry"
                    else "no_trade_regret_events_qkernel_shadow_v3"
                ),
            }
        )
    status.update(
        status="ok" if output else "awaiting_verified_settlement",
        settlement_ready_count=len(output),
        blocked_reasons=blocked,
    )
    return output, status


def _settled_day0_market_relative_alpha_shadow_rows(
    conn: sqlite3.Connection,
    *,
    window_days: float,
    as_of: datetime | None = None,
    forecasts_connection_factory=get_forecasts_connection_read_only,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    return _settled_market_relative_alpha_shadow_rows(
        conn,
        strategy_key="day0_nowcast_entry",
        window_days=window_days,
        as_of=as_of,
        forecasts_connection_factory=forecasts_connection_factory,
    )


def _settled_qkernel_market_relative_alpha_shadow_rows(
    conn: sqlite3.Connection,
    *,
    window_days: float,
    as_of: datetime | None = None,
    forecasts_connection_factory=get_forecasts_connection_read_only,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    return _settled_market_relative_alpha_shadow_rows(
        conn,
        strategy_key="forecast_qkernel_entry",
        window_days=window_days,
        as_of=as_of,
        forecasts_connection_factory=forecasts_connection_factory,
    )


def _submission_schedule_fee_usd(
    *,
    post_only: object,
    fee_details_json: object,
    fill_price: object,
    shares: object,
) -> float | None:
    """Apply an immutable submit-time fee schedule to an actual fill.

    Venue trade facts do not consistently carry ``fee_paid_micro``. A missing
    observation is not permission to call the fee zero, so the forward capital
    curve uses the schedule frozen in the submission envelope and the actual
    fill price/size. Maker fills conservatively receive no rebate.
    """

    try:
        price = float(fill_price)
        quantity = float(shares)
        maker = int(post_only) == 1
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(price)
        or not math.isfinite(quantity)
        or not 0.0 < price < 1.0
        or quantity <= 0.0
    ):
        return None
    if maker:
        return 0.0
    try:
        details = json.loads(str(fee_details_json or ""))
        rate = float(details["fee_rate_fraction"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        return None
    return rate * price * (1.0 - price) * quantity


def _live_realized_capital_curve(
    conn: sqlite3.Connection,
    *,
    strategy_key: str,
    window_days: float,
    as_of: datetime | None = None,
) -> dict[str, object]:
    """Build walk-forward realized-capital attribution for observability only.

    A profitable early exit is capital truth but not a binary-outcome grade;
    probability accuracy is not capital gain. Only exact entry fills plus an
    EXIT_ORDER_FILLED/SETTLED event enter this curve. A position that exits
    before resolution and later settles only a residual is reported as a
    hybrid close, never as if the full entry were held to settlement. Gross
    canonical P&L is reduced by the frozen fee schedule because venue facts
    may omit fees.
    This retrospective curve never licenses entry for either strategy. Their
    revision-scoped probation guards may consume it only to bound an unproven
    revision to one sequential in-flight probe; current causal alpha and the
    normal executable economics/risk stack remain the positive authority.
    """

    if strategy_key not in {"day0_nowcast_entry", "forecast_qkernel_entry"}:
        raise ValueError("live capital strategy is not canonical")

    from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

    evaluated_at = as_of or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    cutoff = evaluated_at - timedelta(days=window_days)
    status: dict[str, object] = {
        "status": "awaiting_current_law_fills",
        "strategy_key": strategy_key,
        "decision_law_id": "predicted_bin_ev_v1",
        "probability_semantics_revision": (
            DAY0_PROBABILITY_SEMANTICS_REVISION
            if strategy_key == "day0_nowcast_entry"
            else CURRENT_EVIDENCE_SEMANTICS_REVISION
        ),
        "global_selection_revision": CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
        # This raw curve spans every selector that used the current probability
        # semantics. Exact current-selector binding is performed separately from
        # immutable entry certificates by _bind_actual_global_capital_evidence.
        "selection_revision_bound": False,
        "window_days": window_days,
        "evaluated_at": evaluated_at.isoformat(),
        "filled_position_count": 0,
        "open_position_count": 0,
        "realized_position_count": 0,
        "excluded_superseded_position_count": 0,
        "blocked_position_count": 0,
        "settled_entry_projection_reconstruction_count": 0,
        "terminal_projection_pnl_mismatch_count": 0,
        "capital_committed_usd": 0.0,
        "realized_capital_committed_usd": 0.0,
        "gross_realized_pnl_usd": 0.0,
        "fee_bound_usd": 0.0,
        "net_realized_pnl_usd": 0.0,
        "return_on_realized_capital": None,
        "curve": [],
        "blocked_reasons": {},
        "source": (
            "venue_commands+venue_submission_envelopes+execution_fact+"
            "position_events+position_current"
        ),
        "fee_basis": "submission_schedule_at_actual_fill_no_maker_rebate",
    }
    required_columns = {
        "position_current": {
            "position_id", "phase", "city", "target_date",
            "temperature_metric", "strategy_key", "decision_law_id",
            "shares", "cost_basis_usd", "realized_pnl_usd",
        },
        "venue_commands": {
            "command_id", "position_id", "intent_kind", "q_version",
            "envelope_id",
        },
        "venue_submission_envelopes": {
            "envelope_id", "post_only", "fee_details_json",
        },
        "execution_fact": {
            "command_id", "position_id", "order_role", "filled_at",
            "terminal_exec_status", "fill_price", "shares",
        },
        "position_events": {
            "position_id", "sequence_no", "event_type", "occurred_at",
            "payload_json",
        },
    }
    try:
        for table, required in required_columns.items():
            if not _table_exists(conn, table):
                status.update(status="capital_truth_unavailable", missing_table=table)
                return status
            columns = {
                str(row[1])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing = sorted(required.difference(columns))
            if missing:
                status.update(
                    status="capital_truth_unavailable",
                    missing_columns={table: missing},
                )
                return status
    except sqlite3.Error as exc:
        status.update(status="capital_truth_unavailable", error=type(exc).__name__)
        return status

    execution_fact_has_intent_id = "intent_id" in {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(execution_fact)").fetchall()
    }
    execution_intent_select = (
        "ef.intent_id" if execution_fact_has_intent_id else "NULL"
    )

    entry_rows = conn.execute(
        "SELECT pc.position_id,pc.phase,pc.city,pc.target_date,"
        "pc.temperature_metric,pc.cost_basis_usd,pc.realized_pnl_usd,"
        f"vc.command_id,{execution_intent_select},vc.q_version,"
        "ef.fill_price,ef.shares,ef.filled_at,"
        "vse.post_only,vse.fee_details_json,pc.shares,"
        "EXISTS(SELECT 1 FROM venue_commands AS exit_vc "
        "JOIN execution_fact AS exit_ef ON exit_ef.command_id=exit_vc.command_id "
        "WHERE exit_vc.position_id=pc.position_id "
        "AND exit_vc.intent_kind='EXIT' AND exit_ef.order_role='exit' "
        "AND exit_ef.filled_at IS NOT NULL AND exit_ef.shares>0 "
        "AND lower(COALESCE(exit_ef.terminal_exec_status,'')) "
        "IN ('filled','confirmed','partial')) "
        "FROM position_current AS pc "
        "JOIN venue_commands AS vc ON vc.position_id=pc.position_id "
        "JOIN execution_fact AS ef ON ef.command_id=vc.command_id "
        "JOIN venue_submission_envelopes AS vse ON vse.envelope_id=vc.envelope_id "
        "WHERE pc.strategy_key=? "
        "AND pc.decision_law_id='predicted_bin_ev_v1' "
        "AND vc.intent_kind='ENTRY' AND ef.order_role='entry' "
        "AND ef.filled_at IS NOT NULL "
        "AND lower(COALESCE(ef.terminal_exec_status,'')) "
        "IN ('filled','confirmed','partial') "
        "AND pc.position_id IN ("
        "SELECT position_id FROM execution_fact "
        "WHERE order_role='entry' AND filled_at>=? "
        "AND lower(COALESCE(terminal_exec_status,'')) "
        "IN ('filled','confirmed','partial')) "
        "ORDER BY pc.position_id,ef.filled_at,vc.command_id",
        (strategy_key, cutoff.isoformat()),
    ).fetchall()
    if not entry_rows:
        return status

    positions: dict[str, dict[str, object]] = {}
    for raw in entry_rows:
        position_id = str(raw[0] or "").strip()
        position = positions.setdefault(
            position_id,
            {
                "position_id": position_id,
                "phase": str(raw[1] or ""),
                "city": str(raw[2] or ""),
                "target_date": str(raw[3] or ""),
                "metric": str(raw[4] or ""),
                "projection_cost_basis_usd": raw[5],
                "projection_realized_pnl_usd": raw[6],
                "projection_shares": raw[15],
                "has_filled_exit": bool(raw[16]),
                "entries": [],
            },
        )
        position["entries"].append(
            {
                "command_id": str(raw[7] or ""),
                "intent_id": str(raw[8] or ""),
                "q_version": str(raw[9] or ""),
                "fill_price": raw[10],
                "shares": raw[11],
                "filled_at": str(raw[12] or ""),
                "post_only": raw[13],
                "fee_details_json": raw[14],
            }
        )

    for position in positions.values():
        position_id = str(position["position_id"])
        baseline_id = f"{position_id}:entry"

        def intent_priority(entry: Mapping[str, object]) -> int:
            command_id = str(entry["command_id"] or "")
            intent_id = str(entry["intent_id"] or "")
            if intent_id == baseline_id:
                return 0
            if intent_id == f"{baseline_id}:{command_id}":
                return 1
            return 2

        canonical_priority_by_command: dict[str, int] = {}
        for entry in position["entries"]:
            command_id = str(entry["command_id"] or "")
            priority = intent_priority(entry)
            canonical_priority_by_command[command_id] = min(
                priority,
                canonical_priority_by_command.get(command_id, priority),
            )
        deduped: dict[str, dict[str, object]] = {}
        conflict = False
        for entry in position["entries"]:
            command_id = str(entry["command_id"] or "")
            incumbent = deduped.get(command_id)
            if not command_id:
                conflict = True
                continue
            priority = intent_priority(entry)
            if priority != canonical_priority_by_command[command_id]:
                continue
            if incumbent is None:
                deduped[command_id] = entry
                continue
            try:
                same_economics = (
                    str(incumbent["q_version"]) == str(entry["q_version"])
                    and math.isclose(
                        float(incumbent["fill_price"]),
                        float(entry["fill_price"]),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    and math.isclose(
                        float(incumbent["shares"]),
                        float(entry["shares"]),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                )
            except (TypeError, ValueError):
                same_economics = False
            if not same_economics:
                conflict = True
                continue
            if str(entry["filled_at"]) > str(incumbent["filled_at"]):
                deduped[command_id] = entry
        position["entries"] = list(deduped.values())
        position["entry_identity_conflict"] = conflict

    blocked_reasons: dict[str, int] = status["blocked_reasons"]  # type: ignore[assignment]

    def block(reason: str) -> None:
        blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1

    if strategy_key == "day0_nowcast_entry":
        from src.events.day0_authority import day0_probability_semantics_revision

        current_position_ids = {
            position_id
            for position_id, position in positions.items()
            if {
                day0_probability_semantics_revision(str(entry["q_version"]))
                for entry in position["entries"]
            }
            == {DAY0_PROBABILITY_SEMANTICS_REVISION}
        }
        semantics_binding: dict[str, object] = {
            "status": "ok",
            "current_revision": DAY0_PROBABILITY_SEMANTICS_REVISION,
        }
    else:
        probes = [
            {
                "trade_id": position_id,
                "strategy": strategy_key,
                "entry_q_versions": tuple(
                    str(entry["q_version"])
                    for entry in position["entries"]
                ),
            }
            for position_id, position in positions.items()
        ]
        classified, semantics_binding = _bind_qkernel_probability_semantics(probes)
        current_position_ids = {
            str(row["trade_id"])
            for row in classified
            if row.get("probability_semantics_ready") is True
        }
    status["probability_semantics_binding"] = semantics_binding

    current_positions: dict[str, dict[str, object]] = {}
    for position_id, position in positions.items():
        entries: list[dict[str, object]] = position["entries"]  # type: ignore[assignment]
        if position_id not in current_position_ids:
            status["excluded_superseded_position_count"] = (
                int(status["excluded_superseded_position_count"]) + 1
            )
            continue
        if position["entry_identity_conflict"]:
            block("entry_command_economics_conflict")
            continue
        entry_notional = 0.0
        entry_shares = 0.0
        entry_fee = 0.0
        entry_times: list[datetime] = []
        valid = True
        for entry in entries:
            try:
                fill_price = float(entry["fill_price"])
                shares = float(entry["shares"])
                filled_at = datetime.fromisoformat(
                    str(entry["filled_at"]).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                valid = False
                break
            if filled_at.tzinfo is None:
                filled_at = filled_at.replace(tzinfo=timezone.utc)
            fee = _submission_schedule_fee_usd(
                post_only=entry["post_only"],
                fee_details_json=entry["fee_details_json"],
                fill_price=fill_price,
                shares=shares,
            )
            if fee is None:
                valid = False
                break
            entry_notional += fill_price * shares
            entry_shares += shares
            entry_fee += fee
            entry_times.append(filled_at)
        try:
            projected_cost = float(position["projection_cost_basis_usd"])
            projected_shares = float(position["projection_shares"])
        except (TypeError, ValueError):
            projected_cost = math.nan
            projected_shares = math.nan
        original_cost_matches = math.isclose(
            projected_cost, entry_notional, rel_tol=0.0, abs_tol=0.011
        )
        residual_cost_matches = (
            bool(position["has_filled_exit"])
            and math.isfinite(projected_shares)
            and entry_shares > 0.0
            and 0.0 <= projected_shares < entry_shares
            and math.isclose(
                projected_cost,
                entry_notional * projected_shares / entry_shares,
                rel_tol=0.0,
                abs_tol=0.011,
            )
        )
        settled_entry_projection_subset = (
            str(position["phase"]) == "settled"
            and not bool(position["has_filled_exit"])
            and math.isfinite(projected_shares)
            and entry_shares > 0.0
            and 0.0 < projected_shares < entry_shares
            and math.isclose(
                projected_cost,
                entry_notional * projected_shares / entry_shares,
                rel_tol=0.0,
                abs_tol=0.011,
            )
        )
        if (
            not valid
            or not entry_times
            or not math.isfinite(projected_cost)
            or projected_cost <= 0.0
            or not (
                original_cost_matches
                or residual_cost_matches
                or settled_entry_projection_subset
            )
        ):
            block("entry_capital_identity_incomplete")
            continue
        position.update(
            entry_notional_usd=entry_notional,
            entry_filled_shares=entry_shares,
            entry_fee_bound_usd=entry_fee,
            capital_committed_usd=entry_notional + entry_fee,
            entered_at=min(entry_times),
            settled_entry_projection_subset=settled_entry_projection_subset,
        )
        current_positions[position_id] = position

    status["filled_position_count"] = len(current_positions)
    status["blocked_position_count"] = sum(blocked_reasons.values())
    status["capital_committed_usd"] = round(
        sum(
            float(position["capital_committed_usd"])
            for position in current_positions.values()
        ),
        6,
    )
    if not current_positions:
        if blocked_reasons:
            status["status"] = "capital_truth_degraded"
        return status

    position_ids = sorted(current_positions)
    placeholders = ",".join("?" for _ in position_ids)
    exit_rows = conn.execute(
        "SELECT vc.position_id,ef.fill_price,ef.shares,ef.filled_at,"
        "vse.post_only,vse.fee_details_json "
        "FROM venue_commands AS vc "
        "JOIN execution_fact AS ef ON ef.command_id=vc.command_id "
        "JOIN venue_submission_envelopes AS vse ON vse.envelope_id=vc.envelope_id "
        "WHERE vc.intent_kind='EXIT' AND ef.order_role='exit' "
        "AND ef.filled_at IS NOT NULL "
        "AND lower(COALESCE(ef.terminal_exec_status,'')) "
        "IN ('filled','confirmed','partial') "
        f"AND vc.position_id IN ({placeholders})",
        tuple(position_ids),
    ).fetchall()
    exit_fees: dict[str, float | None] = {
        position_id: 0.0 for position_id in position_ids
    }
    exit_summaries: dict[str, dict[str, object]] = {
        position_id: {
            "filled_shares": 0.0,
            "gross_proceeds_usd": 0.0,
            "first_filled_at": None,
            "last_filled_at": None,
        }
        for position_id in position_ids
    }
    for raw in exit_rows:
        position_id = str(raw[0] or "")
        fee = _submission_schedule_fee_usd(
            post_only=raw[4],
            fee_details_json=raw[5],
            fill_price=raw[1],
            shares=raw[2],
        )
        if fee is None:
            exit_fees[position_id] = None
        elif exit_fees[position_id] is not None:
            exit_fees[position_id] = float(exit_fees[position_id]) + fee
        try:
            filled_shares = float(raw[2])
            filled_at = datetime.fromisoformat(
                str(raw[3] or "").replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            exit_fees[position_id] = None
            continue
        if filled_at.tzinfo is None:
            filled_at = filled_at.replace(tzinfo=timezone.utc)
        summary = exit_summaries[position_id]
        summary["filled_shares"] = float(summary["filled_shares"]) + filled_shares
        summary["gross_proceeds_usd"] = (
            float(summary["gross_proceeds_usd"])
            + float(raw[1]) * filled_shares
        )
        first_filled_at = summary["first_filled_at"]
        last_filled_at = summary["last_filled_at"]
        if first_filled_at is None or filled_at < first_filled_at:
            summary["first_filled_at"] = filled_at
        if last_filled_at is None or filled_at > last_filled_at:
            summary["last_filled_at"] = filled_at

    event_rows = conn.execute(
        "SELECT position_id,event_type,occurred_at,payload_json "
        "FROM position_events "
        "WHERE event_type IN ('EXIT_ORDER_FILLED','SETTLED') "
        f"AND position_id IN ({placeholders}) "
        "ORDER BY position_id,occurred_at DESC,sequence_no DESC",
        tuple(position_ids),
    ).fetchall()
    latest_event: dict[str, tuple[object, ...]] = {}
    for raw in event_rows:
        latest_event.setdefault(str(raw[0] or ""), tuple(raw))

    realized: list[dict[str, object]] = []
    open_count = 0
    for position_id, position in current_positions.items():
        phase = str(position["phase"])
        if phase not in {"economically_closed", "settled"}:
            open_count += 1
            continue
        event = latest_event.get(position_id)
        expected_event = "SETTLED" if phase == "settled" else "EXIT_ORDER_FILLED"
        if event is None or str(event[1]) != expected_event:
            block("terminal_event_missing_or_mismatched")
            continue
        try:
            payload = json.loads(str(event[3] or ""))
            event_pnl = float(payload["pnl"])
            projected_pnl = float(position["projection_realized_pnl_usd"])
            realized_at = datetime.fromisoformat(
                str(event[2] or "").replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            block("terminal_economics_invalid")
            continue
        if realized_at.tzinfo is None:
            realized_at = realized_at.replace(tzinfo=timezone.utc)
        exit_summary = exit_summaries[position_id]
        entry_filled_shares = float(position["entry_filled_shares"])
        exit_filled_shares = float(exit_summary["filled_shares"])
        remaining_after_exit_shares = max(
            0.0,
            entry_filled_shares - exit_filled_shares,
        )
        terminal_economics_source = "position_event_projection"
        if position.get("settled_entry_projection_subset") is True:
            settlement_price: float | None = None
            if isinstance(payload.get("position_won"), bool):
                settlement_price = 1.0 if payload["position_won"] else 0.0
            else:
                try:
                    outcome = float(payload["outcome"])
                except (KeyError, TypeError, ValueError):
                    outcome = math.nan
                if outcome in {0.0, 1.0}:
                    settlement_price = outcome
            if settlement_price is None:
                block("settled_entry_projection_payout_incomplete")
                continue
            event_pnl = (
                float(exit_summary["gross_proceeds_usd"])
                + remaining_after_exit_shares * settlement_price
                - float(position["entry_notional_usd"])
            )
            terminal_economics_source = (
                "exact_execution_plus_settlement_payout"
            )
            status["settled_entry_projection_reconstruction_count"] = (
                int(status["settled_entry_projection_reconstruction_count"])
                + 1
            )
            if not math.isclose(
                event_pnl,
                projected_pnl,
                rel_tol=0.0,
                abs_tol=0.011,
            ):
                status["terminal_projection_pnl_mismatch_count"] = (
                    int(status["terminal_projection_pnl_mismatch_count"]) + 1
                )
        if (
            not math.isfinite(event_pnl)
            or not math.isfinite(projected_pnl)
            or (
                terminal_economics_source == "position_event_projection"
                and not math.isclose(
                    event_pnl,
                    projected_pnl,
                    rel_tol=0.0,
                    abs_tol=0.011,
                )
            )
            or realized_at < position["entered_at"]
            or realized_at > evaluated_at
        ):
            block("terminal_economics_identity_mismatch")
            continue
        exit_fee = exit_fees.get(position_id)
        if exit_fee is None:
            block("exit_fee_identity_incomplete")
            continue
        fee_bound = float(position["entry_fee_bound_usd"]) + float(exit_fee)
        close_type = expected_event
        if phase == "settled" and exit_filled_shares > 0.0:
            close_type = "EXIT_ORDER_FILLED_WITH_RESIDUAL_SETTLEMENT"
        realized.append(
            {
                "position_id": position_id,
                "city": position["city"],
                "target_date": position["target_date"],
                "metric": position["metric"],
                "close_type": close_type,
                "terminal_event_type": expected_event,
                "terminal_economics_source": terminal_economics_source,
                "entry_filled_shares": entry_filled_shares,
                "exit_filled_shares": exit_filled_shares,
                "exit_fill_fraction": (
                    min(1.0, exit_filled_shares / entry_filled_shares)
                    if entry_filled_shares > 0.0
                    else 0.0
                ),
                "remaining_after_exit_shares": remaining_after_exit_shares,
                "first_exit_filled_at": exit_summary["first_filled_at"],
                "last_exit_filled_at": exit_summary["last_filled_at"],
                "realized_at": realized_at,
                "capital_committed_usd": float(position["capital_committed_usd"]),
                "gross_realized_pnl_usd": event_pnl,
                "fee_bound_usd": fee_bound,
                "net_realized_pnl_usd": event_pnl - fee_bound,
            }
        )

    realized.sort(key=lambda row: (row["realized_at"], row["position_id"]))
    cumulative = 0.0
    curve: list[dict[str, object]] = []
    for row in realized:
        cumulative += float(row["net_realized_pnl_usd"])
        curve.append(
            {
                **row,
                "realized_at": row["realized_at"].isoformat(),
                "first_exit_filled_at": (
                    row["first_exit_filled_at"].isoformat()
                    if row["first_exit_filled_at"] is not None
                    else None
                ),
                "last_exit_filled_at": (
                    row["last_exit_filled_at"].isoformat()
                    if row["last_exit_filled_at"] is not None
                    else None
                ),
                "entry_filled_shares": round(float(row["entry_filled_shares"]), 6),
                "exit_filled_shares": round(float(row["exit_filled_shares"]), 6),
                "exit_fill_fraction": round(float(row["exit_fill_fraction"]), 6),
                "remaining_after_exit_shares": round(
                    float(row["remaining_after_exit_shares"]),
                    6,
                ),
                "capital_committed_usd": round(float(row["capital_committed_usd"]), 6),
                "gross_realized_pnl_usd": round(float(row["gross_realized_pnl_usd"]), 6),
                "fee_bound_usd": round(float(row["fee_bound_usd"]), 6),
                "net_realized_pnl_usd": round(float(row["net_realized_pnl_usd"]), 6),
                "cumulative_net_realized_pnl_usd": round(cumulative, 6),
            }
        )
    realized_capital = sum(float(row["capital_committed_usd"]) for row in realized)
    gross_pnl = sum(float(row["gross_realized_pnl_usd"]) for row in realized)
    fee_bound = sum(float(row["fee_bound_usd"]) for row in realized)
    net_pnl = gross_pnl - fee_bound
    status.update(
        open_position_count=open_count,
        realized_position_count=len(realized),
        blocked_position_count=sum(blocked_reasons.values()),
        realized_capital_committed_usd=round(realized_capital, 6),
        gross_realized_pnl_usd=round(gross_pnl, 6),
        fee_bound_usd=round(fee_bound, 6),
        net_realized_pnl_usd=round(net_pnl, 6),
        return_on_realized_capital=(
            round(net_pnl / realized_capital, 6)
            if realized_capital > 0.0
            else None
        ),
        curve=curve,
    )
    if status["blocked_position_count"]:
        status["status"] = "capital_truth_degraded"
    elif not realized:
        status["status"] = "probation_in_flight"
    elif net_pnl > 0.0:
        status["status"] = "positive"
    else:
        status["status"] = "nonpositive"
    return status


def _day0_live_realized_capital_curve(
    conn: sqlite3.Connection,
    *,
    window_days: float,
    as_of: datetime | None = None,
) -> dict[str, object]:
    return _live_realized_capital_curve(
        conn,
        strategy_key="day0_nowcast_entry",
        window_days=window_days,
        as_of=as_of,
    )


def _qkernel_live_realized_capital_curve(
    conn: sqlite3.Connection,
    *,
    window_days: float,
    as_of: datetime | None = None,
) -> dict[str, object]:
    return _live_realized_capital_curve(
        conn,
        strategy_key="forecast_qkernel_entry",
        window_days=window_days,
        as_of=as_of,
    )


_GLOBAL_CAPITAL_EVIDENCE_LAW = "executable_min_order_capital_gain_v2"


def _global_winner_certificate_q(
    payload_json: object,
    *,
    strategy_key: str,
) -> tuple[float, float, float, str] | None:
    """Return frozen q, size, spend, and selection law for one winner."""

    try:
        payload = json.loads(str(payload_json or ""))
        economics = payload["qkernel_execution_economics"]
        receipt = economics["global_auction_receipt"]
        q = float(economics["global_cut_time_win_probability_mean"])
        target_shares = float(economics["global_target_shares"])
        max_spend = float(economics["global_max_spend_usd"])
        expected_growth = float(economics["global_expected_delta_log_wealth"])
        expected_ev = float(economics["global_expected_ev_usd"])
        selection_revision = str(
            economics["global_selection_revision"]
        ).strip()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping) or not isinstance(economics, Mapping):
        return None
    if not isinstance(receipt, Mapping):
        return None
    candidate_id = str(economics.get("global_candidate_id") or "").strip()
    actuation_id = str(economics.get("global_actuation_identity") or "").strip()
    epoch_id = str(economics.get("global_selection_epoch_identity") or "").strip()
    winner_event_id = str(economics.get("global_winner_event_id") or "").strip()
    if (
        str(payload.get("strategy_key") or "").strip() != strategy_key
        or str(economics.get("global_optimum_semantics") or "")
        != "CUT_TIME_GLOBAL_OPTIMUM"
        or str(economics.get("global_probability_functional") or "")
        != "POSTERIOR_PREDICTIVE_MEAN"
        or str(economics.get("global_execution_mode") or "")
        not in {"TAKER_LIMIT", "MAKER_REST"}
        or not candidate_id
        or not actuation_id
        or not epoch_id
        or not winner_event_id
        or not selection_revision
        or str(receipt.get("winner_candidate_id") or "") != candidate_id
        or str(receipt.get("winner_actuation_identity") or "") != actuation_id
        or str(receipt.get("selection_epoch_identity") or "") != epoch_id
        or str(receipt.get("winner_event_id") or "") != winner_event_id
        or not all(
            math.isfinite(value)
            for value in (q, target_shares, max_spend, expected_growth, expected_ev)
        )
        or not 0.0 <= q <= 1.0
        or target_shares <= 0.0
        or max_spend <= 0.0
        or expected_growth <= 0.0
        or expected_ev <= 0.0
    ):
        return None
    return q, target_shares, max_spend, selection_revision


def _bind_actual_global_capital_evidence(
    conn: sqlite3.Connection,
    rows: list[dict],
    *,
    strategy_key: str,
    capital_curve: Mapping[str, object],
) -> tuple[list[dict], dict[str, object]]:
    """Bind settled actual fills to their exact global-winner capital law.

    ``position_current.decision_law_id`` names the unified probability/EV law,
    not the global-auction admission certificate.  The latter lives on each
    filled ENTRY's immutable ``ActionableTradeCertificate``.  Only when every
    economically filled command carries a matching LIVE VERIFIED winner and
    its share-weighted q reproduces the frozen settlement row may actual fills
    enter the revision-scoped capital-alpha cohort.
    """

    if strategy_key not in {"day0_nowcast_entry", "forecast_qkernel_entry"}:
        raise ValueError("actual global capital strategy is not canonical")
    output = [dict(row) for row in rows]
    candidates = {
        str(row.get("trade_id") or "").strip()
        for row in output
        if str(row.get("strategy") or "").strip() == strategy_key
        and str(row.get("trade_id") or "").strip()
        and row.get("probability_identity_ready") is True
        and row.get("probability_semantics_ready") is True
        and str(row.get("decision_law_id") or "").strip() in DECISION_LAW_IDS
    }
    status: dict[str, object] = {
        "status": "no_actual_candidates",
        "strategy_key": strategy_key,
        "candidate_count": len(candidates),
        "capital_law_ready_count": 0,
        "capital_gain_proof_ready_count": 0,
        "blocked_reasons": {},
        "source": (
            "execution_fact+position_decision_attribution+"
            "world.decision_certificates+live_realized_capital_curve"
        ),
    }
    if not candidates:
        return output, status

    blocked: dict[str, int] = status["blocked_reasons"]  # type: ignore[assignment]

    def block(reason: str) -> None:
        blocked[reason] = blocked.get(reason, 0) + 1

    database_names = {
        str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()
    }
    required_columns = {
        "execution_fact": {
            "position_id", "command_id", "order_role", "filled_at",
            "terminal_exec_status", "fill_price", "shares",
        },
        "position_decision_attribution": {
            "position_id", "command_id", "decision_certificate_hash",
            "resolution", "intent_kind",
        },
    }
    try:
        schema_ready = "world" in database_names and all(
            _table_exists(conn, table)
            and required.issubset(
                {
                    str(column[1])
                    for column in conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
            )
            for table, required in required_columns.items()
        )
        world_columns = {
            str(column[1])
            for column in conn.execute(
                "PRAGMA world.table_info(decision_certificates)"
            ).fetchall()
        }
        schema_ready = schema_ready and {
            "certificate_hash", "certificate_type", "mode",
            "verifier_status", "payload_json",
        }.issubset(world_columns)
    except sqlite3.Error:
        schema_ready = False
    if not schema_ready:
        status["status"] = "certificate_authority_unavailable"
        block("certificate_schema_unavailable")
        return output, status

    command_parts: dict[str, list[tuple[float, float]]] = {
        position_id: [] for position_id in candidates
    }
    invalid: set[str] = set()
    explicitly_blocked: set[str] = set()
    ids = sorted(candidates)
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        facts = conn.execute(
            "SELECT ef.position_id,ef.command_id,MIN(ef.shares),MAX(ef.shares),"
            "MIN(ef.fill_price),MAX(ef.fill_price),"
            "pda.decision_certificate_hash,dc.payload_json "
            "FROM execution_fact AS ef "
            "LEFT JOIN position_decision_attribution AS pda "
            "ON pda.position_id=ef.position_id AND pda.command_id=ef.command_id "
            "AND pda.intent_kind='ENTRY' AND pda.resolution='ATTRIBUTED' "
            "LEFT JOIN world.decision_certificates AS dc "
            "ON dc.certificate_hash=pda.decision_certificate_hash "
            "AND dc.certificate_type='ActionableTradeCertificate' "
            "AND dc.mode='LIVE' AND dc.verifier_status='VERIFIED' "
            "WHERE ef.order_role='entry' AND ef.filled_at IS NOT NULL "
            "AND lower(COALESCE(ef.terminal_exec_status,'')) "
            "IN ('filled','confirmed','partial') "
            f"AND ef.position_id IN ({placeholders}) "
            "GROUP BY ef.position_id,ef.command_id,pda.decision_certificate_hash,"
            "dc.payload_json",
            tuple(chunk),
        ).fetchall()
        for raw in facts:
            position_id = str(raw[0] or "").strip()
            command_id = str(raw[1] or "").strip()
            try:
                min_shares = float(raw[2])
                max_shares = float(raw[3])
                min_price = float(raw[4])
                max_price = float(raw[5])
            except (TypeError, ValueError):
                invalid.add(position_id)
                continue
            certificate = _global_winner_certificate_q(
                raw[7],
                strategy_key=strategy_key,
            )
            if (
                not command_id
                or not str(raw[6] or "").strip()
                or certificate is None
                or not math.isfinite(min_shares)
                or min_shares <= 0.0
                or not math.isfinite(min_price)
                or not 0.0 < min_price < 1.0
                or not math.isclose(
                    min_shares,
                    max_shares,
                    rel_tol=0.0,
                    abs_tol=max(1e-9, abs(max_shares) * 1e-9),
                )
                or not math.isclose(
                    min_price,
                    max_price,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            ):
                invalid.add(position_id)
                continue
            (
                q,
                _certified_target_shares,
                certified_max_spend,
                selection_revision,
            ) = certificate
            if selection_revision != CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION:
                block("global_selection_revision_mismatch")
                invalid.add(position_id)
                explicitly_blocked.add(position_id)
                continue
            if min_price * min_shares > certified_max_spend + 0.011:
                invalid.add(position_id)
                continue
            command_parts[position_id].append((q, min_shares))

    capital_by_position = {
        str(point.get("position_id") or "").strip(): point
        for point in (capital_curve.get("curve") or [])
        if isinstance(point, Mapping)
        and str(point.get("position_id") or "").strip()
    }
    bindings: dict[str, dict[str, object]] = {}
    for position_id in sorted(candidates):
        parts = command_parts.get(position_id, [])
        if position_id in invalid or not parts:
            if position_id not in explicitly_blocked:
                block("global_certificate_identity_incomplete")
            continue
        total_shares = sum(shares for _q, shares in parts)
        composite_q = sum(q * shares for q, shares in parts) / total_shares
        capital = capital_by_position.get(position_id)
        binding: dict[str, object] = {
            "p_posterior": composite_q,
            "global_selection_revision": (
                CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            ),
            "capital_gain_proof_ready": False,
            "capital_evidence_source": "actual_global_winner_fill",
        }
        if capital is not None:
            try:
                committed = float(capital["capital_committed_usd"])
                realized_pnl = float(capital["net_realized_pnl_usd"])
            except (KeyError, TypeError, ValueError):
                committed = math.nan
                realized_pnl = math.nan
            if (
                math.isfinite(committed)
                and committed > 0.0
                and math.isfinite(realized_pnl)
            ):
                binding.update(
                    capital_gain_proof_ready=True,
                    hypothetical_capital_committed_usd=committed,
                    hypothetical_realized_pnl_usd=realized_pnl,
                )
        bindings[position_id] = binding

    for row in output:
        position_id = str(row.get("trade_id") or "").strip()
        binding = bindings.get(position_id)
        if binding is None:
            continue
        try:
            frozen_q = float(row["p_posterior"])
            certificate_q = float(binding["p_posterior"])
        except (KeyError, TypeError, ValueError):
            block("global_certificate_probability_mismatch")
            continue
        if not math.isclose(
            frozen_q,
            certificate_q,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            block("global_certificate_probability_mismatch")
            continue
        row["persisted_decision_law_id"] = row.get("decision_law_id")
        row["decision_law_id"] = _GLOBAL_CAPITAL_EVIDENCE_LAW
        row["decision_law_evidence_source"] = (
            "filled_entry_actionable_global_winner_certificate"
        )
        row.update(binding)
        status["capital_law_ready_count"] = (
            int(status["capital_law_ready_count"]) + 1
        )
        if binding["capital_gain_proof_ready"] is True:
            status["capital_gain_proof_ready_count"] = (
                int(status["capital_gain_proof_ready_count"]) + 1
            )
    status["status"] = (
        "ok" if status["capital_law_ready_count"] else "no_verified_winners"
    )
    return output, status


def _market_relative_alpha_evidence(
    rows: list[dict],
    *,
    strategy_key: str,
    rejection_evalue: float,
    window_days: float = 7.0,
    as_of: datetime | None = None,
) -> dict[str, object]:
    """Test one current probability law against its executable entry market.

    For one binary claim, ``market/model`` likelihood is a valid sequential
    e-value because both probabilities were fixed before the outcome. Sibling
    bins and HIGH/LOW observations within one city-date family are correlated,
    so each city-date cluster contributes only its largest ex-ante claimed edge.
    This is a
    capital-alpha test, not a stop-loss: model/market evidence proves admission;
    market/model evidence rejects it. Both probabilities are immutable decision-
    time witnesses, never reconstructed after settlement.
    """

    if strategy_key not in {"forecast_qkernel_entry", "day0_nowcast_entry"}:
        raise ValueError("market-relative alpha strategy is not canonical")
    if not math.isfinite(rejection_evalue) or rejection_evalue <= 1.0:
        raise ValueError("market-relative alpha rejection_evalue must exceed 1")
    if not math.isfinite(window_days) or window_days <= 0.0 or window_days > 7.0:
        raise ValueError("market-relative alpha window_days must be in (0, 7]")
    evaluated_at = as_of or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    not_before = evaluated_at - timedelta(days=window_days)

    cohorts: dict[
        tuple[str, str, tuple[str, ...]],
        dict[tuple[str, str], dict],
    ] = {}
    missing_benchmark_count = 0
    for row in rows:
        if str(row.get("strategy") or "").strip() != strategy_key:
            continue
        decision_law_id = str(row.get("decision_law_id") or "").strip()
        if (
            strategy_key == "day0_nowcast_entry"
            and decision_law_id != "executable_min_order_capital_gain_v2"
        ):
            continue
        if row.get("probability_semantics_ready") is not True:
            continue
        try:
            settled_at = datetime.fromisoformat(
                str(row.get("settled_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if settled_at.tzinfo is None:
            settled_at = settled_at.replace(tzinfo=timezone.utc)
        if settled_at < not_before or settled_at > evaluated_at:
            continue
        if not row.get("entry_market_benchmark_ready", False):
            missing_benchmark_count += 1
            continue
        try:
            q = float(row["p_posterior"])
            market = float(row["entry_market_benchmark"])
            outcome = int(row["outcome"])
        except (KeyError, TypeError, ValueError):
            continue
        family = tuple(row.get("entry_market_benchmark_family") or ())
        if (
            len(family) != 3
            or not all(str(value).strip() for value in family)
            or outcome not in {0, 1}
            or not math.isfinite(q)
            or not math.isfinite(market)
            or not 0.0 <= q <= 1.0
            or not 0.0 < market < 1.0
        ):
            continue
        revisions = tuple(
            sorted(
                str(revision).strip()
                for revision in (row.get("probability_semantics_revisions") or ())
                if str(revision).strip()
            )
        )
        global_selection_revision = str(
            row.get("global_selection_revision") or ""
        ).strip()
        cohort_key = (
            decision_law_id,
            global_selection_revision,
            revisions,
        )
        # Sibling bins and HIGH/LOW from one city-date share weather,
        # observation, and market-information shocks.  Different cities are
        # distinct settlement claims; collapsing them by calendar date alone
        # discards executable evidence from unrelated market families.
        evidence_cluster = (
            str(family[0]).strip(),
            str(family[1]).strip(),
        )
        try:
            capital_committed = float(
                row.get("hypothetical_capital_committed_usd")
            )
            capital_pnl = float(row.get("hypothetical_realized_pnl_usd"))
        except (TypeError, ValueError):
            capital_committed = 0.0
            capital_pnl = 0.0
        capital_gain_proof_ready = bool(
            row.get("capital_gain_proof_ready") is True
            and math.isfinite(capital_committed)
            and capital_committed > 0.0
            and math.isfinite(capital_pnl)
        )
        candidate = {
            "trade_id": str(row.get("trade_id") or ""),
            "q": q,
            "market": market,
            "outcome": outcome,
            "claimed_edge": abs(q - market),
            "capital_gain_proof_ready": capital_gain_proof_ready,
            "hypothetical_capital_committed_usd": capital_committed,
            "hypothetical_realized_pnl_usd": capital_pnl,
        }
        cluster_rows = cohorts.setdefault(cohort_key, {})
        incumbent = cluster_rows.get(evidence_cluster)
        if incumbent is None or (
            candidate["claimed_edge"], candidate["trade_id"]
        ) > (incumbent["claimed_edge"], incumbent["trade_id"]):
            cluster_rows[evidence_cluster] = candidate

    cohort_evidence: list[dict[str, object]] = []
    for (
        decision_law_id,
        global_selection_revision,
        revisions,
    ), cluster_rows in sorted(cohorts.items()):
        log_model_over_market = 0.0
        for row in cluster_rows.values():
            q = min(max(float(row["q"]), 1e-12), 1.0 - 1e-12)
            market = min(max(float(row["market"]), 1e-12), 1.0 - 1e-12)
            outcome = int(row["outcome"])
            model_probability = q if outcome else 1.0 - q
            market_probability = market if outcome else 1.0 - market
            log_model_over_market += math.log(model_probability / market_probability)
        market_over_model_evalue = math.exp(min(700.0, -log_model_over_market))
        model_over_market_evalue = math.exp(min(700.0, log_model_over_market))
        capital_rows = [
            row
            for row in cluster_rows.values()
            if row["capital_gain_proof_ready"]
        ]
        capital_committed = sum(
            float(row["hypothetical_capital_committed_usd"])
            for row in capital_rows
        )
        capital_pnl = sum(
            float(row["hypothetical_realized_pnl_usd"])
            for row in capital_rows
        )
        capital_proof_ready = bool(capital_rows) and len(capital_rows) == len(
            cluster_rows
        )
        capital_gain_validated = (
            capital_proof_ready
            and math.isfinite(capital_committed)
            and math.isfinite(capital_pnl)
            and capital_committed > 0.0
            and capital_pnl > 0.0
        )
        statistical_validation = model_over_market_evalue >= rejection_evalue
        # A probability system can beat the market on log score while still
        # losing money at the executable prices and minimum sizes that were
        # available at decision time.  Both entry strategies therefore require
        # the same positive forward-capital proof; likelihood evidence alone is
        # diagnostic, never validation for re-opening capital.
        validated = statistical_validation and capital_gain_validated
        cohort_evidence.append(
            {
                "decision_law_id": decision_law_id,
                "global_selection_revision": global_selection_revision,
                "probability_semantics_revisions": list(revisions),
                "independent_cluster_count": len(cluster_rows),
                "candidate_count": sum(
                    1
                    for row in rows
                    if str(row.get("strategy") or "").strip() == strategy_key
                    and str(row.get("decision_law_id") or "").strip()
                    == decision_law_id
                    and str(row.get("global_selection_revision") or "").strip()
                    == global_selection_revision
                    and tuple(sorted(row.get("probability_semantics_revisions") or ()))
                    == revisions
                    and row.get("entry_market_benchmark_ready", False)
                ),
                "log_model_over_market": round(log_model_over_market, 6),
                "market_over_model_evalue": round(market_over_model_evalue, 6),
                "model_over_market_evalue": round(model_over_market_evalue, 6),
                "capital_gain_proof_ready": capital_proof_ready,
                "hypothetical_capital_committed_usd": round(capital_committed, 6),
                "hypothetical_realized_pnl_usd": round(capital_pnl, 6),
                "hypothetical_return_on_capital": (
                    round(capital_pnl / capital_committed, 6)
                    if capital_committed > 0.0
                    else None
                ),
                "capital_gain_validated": capital_gain_validated,
                "rejected": market_over_model_evalue >= rejection_evalue,
                "validated": validated,
            }
        )

    current_cohorts = [
        cohort
        for cohort in cohort_evidence
        if cohort["global_selection_revision"]
        == CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
    ]
    rejected = [cohort for cohort in current_cohorts if cohort["rejected"]]
    validated = [cohort for cohort in current_cohorts if cohort["validated"]]
    return {
        "strategy_key": strategy_key,
        "global_selection_revision": (
            CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        ),
        "status": (
            "rejected"
            if rejected
            else (
                "validated"
                if validated
                else ("inconclusive" if current_cohorts else "no_evidence")
            )
        ),
        "rejection_evalue": rejection_evalue,
        "window_days": window_days,
        "evaluated_at": evaluated_at.isoformat(),
        "rejected": bool(rejected),
        "validated": bool(validated) and not bool(rejected),
        "missing_benchmark_count": missing_benchmark_count,
        "cohorts": cohort_evidence,
    }


def _qkernel_market_relative_alpha_evidence(
    rows: list[dict],
    *,
    rejection_evalue: float,
    window_days: float = 7.0,
    as_of: datetime | None = None,
) -> dict[str, object]:
    """Compatibility projection for the rejection-only forecast entry gate."""

    evidence = _market_relative_alpha_evidence(
        rows,
        strategy_key="forecast_qkernel_entry",
        rejection_evalue=rejection_evalue,
        window_days=window_days,
        as_of=as_of,
    )
    cohorts = []
    for cohort in evidence["cohorts"]:
        if (
            cohort.get("global_selection_revision")
            != CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        ):
            continue
        projected = dict(cohort)
        projected.pop("model_over_market_evalue", None)
        projected.pop("validated", None)
        cohorts.append(projected)
    return {
        "status": (
            "rejected"
            if evidence["rejected"]
            else ("ok" if cohorts else "no_evidence")
        ),
        "rejection_evalue": evidence["rejection_evalue"],
        "global_selection_revision": evidence["global_selection_revision"],
        "window_days": evidence["window_days"],
        "evaluated_at": evidence["evaluated_at"],
        "rejected": evidence["rejected"],
        "missing_benchmark_count": evidence["missing_benchmark_count"],
        "cohorts": cohorts,
    }


def _market_relative_alpha_gate_reason(
    semantics_binding: Mapping[str, object],
    causal_alpha_evidence: Mapping[str, object],
    *,
    required_evalue: float,
) -> str | None:
    """Return the licensed revisions' missing capital-proof gate reason.

    SCOPE: only the strategy, exact global-selection revision, and exact
    probability-semantics revisions named by ``semantics_binding``. DRAIN:
    settled, walk-forward model-vs-market capital evidence is refreshed every
    RiskGuard tick. RESET: a revision disappears from the reason when it attains
    the required e-value and positive realized-capital proof under the current
    selector; either revision change starts its own cohort.
    """

    if semantics_binding.get("status") != "ok":
        return None
    current_revision = str(
        semantics_binding.get("current_revision") or ""
    ).strip()
    licensed_revisions = tuple(
        sorted(
            {
                str(revision).strip()
                for revision in (
                    semantics_binding.get("licensed_revisions") or ()
                )
                if str(revision).strip()
            }
        )
    )
    target_revisions = (
        (current_revision,) if current_revision else licensed_revisions
    )
    cohorts = [
        cohort
        for cohort in (causal_alpha_evidence.get("cohorts") or [])
        if isinstance(cohort, Mapping)
        and cohort.get("decision_law_id")
        == "executable_min_order_capital_gain_v2"
        and cohort.get("global_selection_revision")
        == CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        and (
            not target_revisions
            or bool(
                set(target_revisions).intersection(
                    {
                        str(revision).strip()
                        for revision in cohort.get(
                            "probability_semantics_revisions", []
                        )
                        if str(revision).strip()
                    }
                )
            )
        )
    ]
    validated_revisions = {
        str(revision).strip()
        for cohort in cohorts
        if cohort.get("validated") is True
        for revision in cohort.get("probability_semantics_revisions", [])
        if str(revision).strip()
    }
    unproven_revisions = tuple(
        revision
        for revision in target_revisions
        if revision not in validated_revisions
    )
    if target_revisions and not unproven_revisions:
        return None
    if not target_revisions and any(
        cohort.get("validated") is True for cohort in cohorts
    ):
        return None
    relevant_revisions = set(unproven_revisions)
    relevant_cohorts = [
        cohort
        for cohort in cohorts
        if not relevant_revisions
        or relevant_revisions.intersection(
            {
                str(revision).strip()
                for revision in cohort.get(
                    "probability_semantics_revisions", []
                )
                if str(revision).strip()
            }
        )
    ]
    strongest = (
        max(
            relevant_cohorts,
            key=lambda cohort: float(cohort["model_over_market_evalue"]),
        )
        if relevant_cohorts
        else None
    )
    model_evalue = (
        float(strongest["model_over_market_evalue"])
        if strongest is not None
        else 0.0
    )
    clusters = (
        int(strongest["independent_cluster_count"])
        if strongest is not None
        else 0
    )
    current_status = (
        "rejected"
        if any(cohort.get("rejected") is True for cohort in relevant_cohorts)
        else ("inconclusive" if relevant_cohorts else "no_evidence")
    )
    revision_label = (
        ",".join(unproven_revisions)
        if unproven_revisions
        else semantics_binding.get("current_revision")
    )
    return (
        "market_relative_alpha_unproven("
        f"status={current_status},"
        f"model_evalue={model_evalue},"
        f"required={required_evalue},"
        f"clusters={clusters},"
        "law=executable_min_order_capital_gain_v2,"
        f"selection_revision={CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION},"
        f"revision={revision_label}"
        ")"
    )


def _market_relative_alpha_unproven_revisions(
    semantics_binding: Mapping[str, object],
    causal_alpha_evidence: Mapping[str, object],
) -> tuple[str, ...]:
    """Return only licensed probability revisions still lacking capital proof."""

    current_revision = str(
        semantics_binding.get("current_revision") or ""
    ).strip()
    revisions = (
        (current_revision,)
        if current_revision
        else tuple(
            sorted(
                {
                    str(revision).strip()
                    for revision in (
                        semantics_binding.get("licensed_revisions") or ()
                    )
                    if str(revision).strip()
                }
            )
        )
    )
    validated = {
        str(revision).strip()
        for cohort in (causal_alpha_evidence.get("cohorts") or [])
        if isinstance(cohort, Mapping)
        and cohort.get("decision_law_id")
        == "executable_min_order_capital_gain_v2"
        and cohort.get("global_selection_revision")
        == CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        and cohort.get("validated") is True
        for revision in cohort.get("probability_semantics_revisions", [])
        if str(revision).strip()
    }
    return tuple(revision for revision in revisions if revision not in validated)


def _market_relative_alpha_rejected_revisions(
    semantics_binding: Mapping[str, object],
    causal_alpha_evidence: Mapping[str, object],
) -> tuple[str, ...]:
    """Return licensed revisions with direct capital-law rejection evidence."""

    licensed = set(
        _market_relative_alpha_unproven_revisions(
            semantics_binding,
            {"cohorts": []},
        )
    )
    rejected = {
        str(revision).strip()
        for cohort in (causal_alpha_evidence.get("cohorts") or [])
        if isinstance(cohort, Mapping)
        and cohort.get("decision_law_id")
        == "executable_min_order_capital_gain_v2"
        and cohort.get("global_selection_revision")
        == CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        and cohort.get("rejected") is True
        for revision in cohort.get("probability_semantics_revisions", [])
        if str(revision).strip()
    }
    return tuple(sorted(licensed.intersection(rejected)))


def _market_relative_alpha_rejection_gate_reason(
    semantics_binding: Mapping[str, object],
    causal_alpha_evidence: Mapping[str, object],
    *,
    required_evalue: float,
) -> tuple[str | None, tuple[str, ...]]:
    """Gate only an explicitly rejected capital law, never missing history."""

    revisions = _market_relative_alpha_rejected_revisions(
        semantics_binding,
        causal_alpha_evidence,
    )
    if not revisions:
        return None, ()
    reason = _market_relative_alpha_gate_reason(
        {
            "status": semantics_binding.get("status"),
            "licensed_revisions": revisions,
        },
        causal_alpha_evidence,
        required_evalue=required_evalue,
    )
    return reason, revisions


# Minimum realized same-revision closes before a net-nonpositive cohort may
# latch the entry gate. Below this, one bad close is noise, and — because the
# latch blocks the very probes that would grow the sample — a permanent lock.
_PROBATION_MIN_REALIZED_SAMPLE = 3


def _revision_probation_gate_reason(
    semantics_binding: Mapping[str, object],
    causal_alpha_evidence: Mapping[str, object],
    capital_curve: Mapping[str, object],
    *,
    reason_prefix: str,
) -> tuple[str | None, tuple[str, ...]]:
    """Bound an unproven probability revision by realized capital truth.

    SCOPE: only the exact current strategy probability revision under the exact
    current global selection revision. DRAIN: existing monitor/exit/settlement
    lanes close realized positions while the no-money counterfactual lane keeps
    accumulating exact-selector evidence. RESET: validated same-revision
    capital evidence removes the bound; before validation, entry stays open
    unless selection-revision-bound realized truth is degraded or a
    minimum-sample cohort (>= _PROBATION_MIN_REALIZED_SAMPLE realized closes)
    is net-nonpositive. A new probability or global selection revision starts
    its own empty probation cohort.

    2026-08-28 operator directive (continuous decision throughput): the former
    in-flight arm counted every pre-existing open position as "the one
    sequential probe", so open>0 froze entry unconditionally — a fifth
    concurrency throttle on top of the four pinned sizing levers and the
    drawdown kill, and one that could never drain while the strategy kept any
    book. It is removed: concurrency is bounded by risk_policy.yaml levers,
    not by this gate. The nonpositive latch now needs a minimum realized
    sample — a single losing close is not statistical evidence and, because
    the latch itself blocks the next probe, n=1 was a permanent lock.
    """

    if semantics_binding.get("status") != "ok":
        return None, ()
    curve_revision = str(
        capital_curve.get("probability_semantics_revision") or ""
    ).strip()
    unproven_revisions = _market_relative_alpha_unproven_revisions(
        semantics_binding,
        causal_alpha_evidence,
    )
    if not curve_revision or curve_revision not in unproven_revisions:
        return None, ()
    revision = curve_revision

    # A probability revision can span several materially different selection
    # laws.  Old selector losses and an unbound open-position accounting dispute
    # are observability facts, but neither is evidence about the current global
    # expected-growth selector.  SCOPE: only a curve explicitly bound to the
    # current selection revision may gate this revision. DRAIN: actual fills and
    # proof receipts bind on their immutable entry certificates. RESET: the
    # first exact-bound curve re-enables ordinary degraded/nonpositive probation
    # checks below. Explicit current-law alpha rejection remains independently
    # enforced by _market_relative_alpha_rejection_gate_reason.
    if (
        capital_curve.get("selection_revision_bound") is not True
        or str(capital_curve.get("global_selection_revision") or "").strip()
        != CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
    ):
        return None, ()

    status = str(capital_curve.get("status") or "").strip()
    try:
        open_positions = int(capital_curve.get("open_position_count") or 0)
        realized_positions = int(
            capital_curve.get("realized_position_count") or 0
        )
        blocked_positions = int(
            capital_curve.get("blocked_position_count") or 0
        )
        net_pnl = float(capital_curve.get("net_realized_pnl_usd") or 0.0)
    except (TypeError, ValueError):
        status = "capital_truth_degraded"
        open_positions = realized_positions = blocked_positions = 0
        net_pnl = 0.0

    if (
        curve_revision != revision
        or blocked_positions > 0
        or status in {"capital_truth_degraded", "capital_truth_unavailable"}
    ):
        reason = (
            f"{reason_prefix}_revision_probation_truth_degraded("
            f"status={status or 'missing'},blocked={blocked_positions},"
            f"revision={revision})"
        )
        return reason, (revision,)
    # open_positions intentionally does NOT gate: concurrency is owned by the
    # pinned sizing levers (risk_policy.yaml) and the drawdown kill. This gate
    # only answers "has this revision's realized capital truth disproven it?"
    del open_positions
    if realized_positions >= _PROBATION_MIN_REALIZED_SAMPLE and (
        not math.isfinite(net_pnl) or net_pnl <= 0.0
    ):
        reason = (
            f"{reason_prefix}_revision_probation_nonpositive("
            f"realized={realized_positions},net_pnl_usd={net_pnl:.6f},"
            f"revision={revision})"
        )
        return reason, (revision,)
    return None, ()


def _day0_revision_probation_gate_reason(
    semantics_binding: Mapping[str, object],
    causal_alpha_evidence: Mapping[str, object],
    capital_curve: Mapping[str, object],
) -> tuple[str | None, tuple[str, ...]]:
    return _revision_probation_gate_reason(
        semantics_binding,
        causal_alpha_evidence,
        capital_curve,
        reason_prefix="day0",
    )


def _qkernel_revision_probation_gate_reason(
    semantics_binding: Mapping[str, object],
    causal_alpha_evidence: Mapping[str, object],
    capital_curve: Mapping[str, object],
) -> tuple[str | None, tuple[str, ...]]:
    return _revision_probation_gate_reason(
        semantics_binding,
        causal_alpha_evidence,
        capital_curve,
        reason_prefix="qkernel",
    )


# Below this many settled observations a per-strategy Brier score is noise,
# not a verdict (a single loss at p=0.6 scores 0.36 > brier_red). Thin
# Thin rows remain visible in raw portfolio telemetry and the loss gates. They
# cannot combine across unrelated probability cohorts to manufacture a verdict.
_STRATEGY_BRIER_MIN_SAMPLE = 10

_PROBABILITY_MECHANISM_SNAPSHOT_PREFIXES = frozenset({
    "metar_fast",
    "observation_instants",
})


def _probability_mechanism_key(row: dict) -> str | None:
    """Return a recorded probability mechanism, never infer one from outcome."""

    decision_law_id = str(row.get("decision_law_id") or "").strip()
    if (
        not row.get("decision_law_identity_ready", False)
        or decision_law_id not in DECISION_LAW_IDS
    ):
        return None
    snapshot_id = str(row.get("decision_snapshot_id") or "").strip()
    namespace, separator, _ = snapshot_id.partition(":")
    if not separator or namespace not in _PROBABILITY_MECHANISM_SNAPSHOT_PREFIXES:
        return None
    return f"law:{decision_law_id}:decision_snapshot:{namespace}"


def _brier_probability_cohort_keys(row: dict) -> tuple[str, ...]:
    """Return outcome-independent identities that may share Brier evidence."""

    strategy = str(row.get("strategy") or "").strip()
    decision_law_id = str(row.get("decision_law_id") or "").strip()
    if strategy in CANONICAL_STRATEGY_KEYS:
        owner = f"strategy:{strategy}"
    elif decision_law_id:
        owner = f"law:{decision_law_id}"
    else:
        return ()
    revisions = _probability_semantics_revisions(row)
    revision_identity = ",".join(revisions) if revisions else "unstamped"
    keys = [f"{owner}:probability_semantics:{revision_identity}"]
    mechanism = _probability_mechanism_key(row)
    if mechanism is not None:
        keys.append(f"mechanism:{mechanism}")
    return tuple(keys)


def _brier_independent_target_date(row: Mapping[str, object]) -> str | None:
    """Return the canonical independent evidence unit for a weather outcome."""

    raw = str(row.get("target_date") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date().isoformat()
    except ValueError:
        return None


def _probability_semantics_revisions(row: Mapping[str, object]) -> tuple[str, ...]:
    """Return the exact immutable probability-law revisions on one fill."""

    return tuple(
        sorted(
            str(revision).strip()
            for revision in (row.get("probability_semantics_revisions") or ())
            if str(revision).strip()
        )
    )


def _brier_evidence_ready_rows(rows: list[dict]) -> list[dict]:
    """Keep rows in at least one homogeneous evidence-complete cohort.

    The action law is not the probability law. Two thin strategies using
    different probability semantics cannot acquire statistical authority by
    being pooled merely because both actions used the same EV decision law.
    An explicitly recorded shared probability mechanism may still pool its
    member strategies. The minimum evidence unit is a distinct target date;
    correlated city/metric cells remain in the score but cannot inflate the
    admission sample count.
    """

    keyed_rows = [(row, _brier_probability_cohort_keys(row)) for row in rows]
    target_dates: dict[str, set[str]] = {}
    for row, keys in keyed_rows:
        target_date = _brier_independent_target_date(row)
        if target_date is None:
            continue
        for key in keys:
            target_dates.setdefault(key, set()).add(target_date)
    ready = {
        key
        for key, dates in target_dates.items()
        if len(dates) >= _STRATEGY_BRIER_MIN_SAMPLE
    }
    return [row for row, keys in keyed_rows if any(key in ready for key in keys)]


def _strategy_brier_breakdown(rows: list[dict], thresholds: dict) -> dict[str, object]:
    """Per-strategy probability-quality attribution for localized protection.

    Portfolio-level Brier still protects the system. When the breach is only
    YELLOW and every learning-ready row carries a canonical strategy key, the
    bad strategy or recorded probability mechanism can be halted through
    durable ``risk_actions`` instead of freezing every other strategy. A
    mechanism cohort may supply the minimum evidence when its consumer
    strategies are individually thin. Unknown/unclassified rows keep the
    global YELLOW because there is no safe strategy-local enforcement target.
    """

    buckets: dict[str, dict[str, dict[str, object]]] = {}
    mechanism_buckets: dict[str, dict[str, object]] = {}
    unclassified_count = 0
    for row in rows:
        strategy = str(row.get("strategy") or "unclassified")
        # PR-1 (COLLISION.md §唯一决策律): a new-law row carries strategy_key NULL/
        # absent but a decision_law_id, so it is classified by its law identity, not
        # the legacy registry. Bucket it under the law id; legacy rows still gate on
        # CANONICAL_STRATEGY_KEYS exactly as before (no weakening).
        decision_law_id = str(row.get("decision_law_id") or "").strip()
        if strategy in CANONICAL_STRATEGY_KEYS:
            bucket_key = strategy
        elif decision_law_id:
            bucket_key = f"law:{decision_law_id}"
        else:
            unclassified_count += 1
            continue
        revisions = _probability_semantics_revisions(row)
        revision_identity = ",".join(revisions) if revisions else "unstamped"
        cohort_key = (
            f"strategy:{bucket_key}:probability_semantics:{revision_identity}"
        )
        bucket = buckets.setdefault(bucket_key, {}).setdefault(
            cohort_key,
            {
                "p": [],
                "o": [],
                "revisions": revisions,
                "target_dates": set(),
                "missing_target_date_count": 0,
            },
        )
        bucket["p"].append(float(row["p_posterior"]))  # type: ignore[index, union-attr]
        bucket["o"].append(int(row["outcome"]))  # type: ignore[index, union-attr]
        target_date = _brier_independent_target_date(row)
        if target_date is None:
            bucket["missing_target_date_count"] = int(
                bucket["missing_target_date_count"]
            ) + 1
        else:
            bucket["target_dates"].add(target_date)  # type: ignore[union-attr]
        mechanism_key = _probability_mechanism_key(row)
        if mechanism_key is not None and strategy in CANONICAL_STRATEGY_KEYS:
            mechanism_bucket = mechanism_buckets.setdefault(
                mechanism_key,
                {
                    "p": [],
                    "o": [],
                    "strategy_counts": {},
                    "target_dates": set(),
                    "missing_target_date_count": 0,
                },
            )
            mechanism_bucket["p"].append(float(row["p_posterior"]))  # type: ignore[index, union-attr]
            mechanism_bucket["o"].append(int(row["outcome"]))  # type: ignore[index, union-attr]
            if target_date is None:
                mechanism_bucket["missing_target_date_count"] = int(
                    mechanism_bucket["missing_target_date_count"]
                ) + 1
            else:
                mechanism_bucket["target_dates"].add(target_date)  # type: ignore[union-attr]
            strategy_counts = mechanism_bucket["strategy_counts"]  # type: ignore[index]
            strategy_counts[strategy] = int(strategy_counts.get(strategy, 0)) + 1

    by_strategy: dict[str, dict[str, object]] = {}
    degraded: dict[str, dict[str, object]] = {}
    for strategy, cohort_buckets in sorted(buckets.items()):
        cohort_payloads: dict[str, dict[str, object]] = {}
        all_p: list[float] = []
        all_o: list[int] = []
        all_target_dates: set[str] = set()
        missing_target_date_count = 0
        degraded_cohorts: list[dict[str, object]] = []
        for cohort_key, bucket in sorted(cohort_buckets.items()):
            p_values = list(bucket["p"])  # type: ignore[index]
            outcomes = list(bucket["o"])  # type: ignore[index]
            cohort_target_dates = set(bucket["target_dates"])  # type: ignore[arg-type]
            cohort_missing_target_dates = int(
                bucket["missing_target_date_count"]
            )
            all_p.extend(p_values)
            all_o.extend(outcomes)
            all_target_dates.update(cohort_target_dates)
            missing_target_date_count += cohort_missing_target_dates
            score = brier_score(p_values, outcomes)
            level = evaluate_brier(score, thresholds)
            sample_size = len(p_values)
            independent_target_date_count = len(cohort_target_dates)
            cohort_payload: dict[str, object] = {
                "sample_size": sample_size,
                "independent_target_date_count": independent_target_date_count,
                "missing_target_date_count": cohort_missing_target_dates,
                "brier": round(float(score), 6),
                "level": level.value,
                "cohort": cohort_key,
            }
            revisions = tuple(bucket.get("revisions") or ())
            if revisions:
                cohort_payload["probability_semantics_revisions"] = list(revisions)
            if independent_target_date_count < _STRATEGY_BRIER_MIN_SAMPLE:
                cohort_payload["level"] = RiskLevel.GREEN.value
                cohort_payload["thin_sample_no_verdict"] = True
            elif level != RiskLevel.GREEN:
                degraded_cohorts.append(cohort_payload)
            cohort_payloads[cohort_key] = cohort_payload

        aggregate_score = brier_score(all_p, all_o) if all_p else 0.0
        aggregate_payload: dict[str, object] = {
            "sample_size": len(all_p),
            "independent_target_date_count": len(all_target_dates),
            "missing_target_date_count": missing_target_date_count,
            "brier": round(float(aggregate_score), 6),
            "level": RiskLevel.GREEN.value,
            "cohorts": cohort_payloads,
        }
        if degraded_cohorts:
            degraded_cohort_keys = {
                str(payload["cohort"]) for payload in degraded_cohorts
            }
            degraded_levels = [
                RiskLevel(str(payload["level"])) for payload in degraded_cohorts
            ]
            degraded_level = overall_level(*degraded_levels)
            degraded_p = [
                value
                for cohort_key, bucket in cohort_buckets.items()
                if cohort_key in degraded_cohort_keys
                for value in bucket["p"]  # type: ignore[index]
            ]
            degraded_o = [
                value
                for cohort_key, bucket in cohort_buckets.items()
                if cohort_key in degraded_cohort_keys
                for value in bucket["o"]  # type: ignore[index]
            ]
            revisions = sorted(
                {
                    str(revision)
                    for payload in degraded_cohorts
                    for revision in payload.get(
                        "probability_semantics_revisions", []
                    )
                }
            )
            degraded_payload: dict[str, object] = {
                "sample_size": len(degraded_p),
                "independent_target_date_count": len(
                    {
                        target_date
                        for cohort_key, bucket in cohort_buckets.items()
                        if cohort_key in degraded_cohort_keys
                        for target_date in bucket["target_dates"]  # type: ignore[union-attr]
                    }
                ),
                "brier": round(float(brier_score(degraded_p, degraded_o)), 6),
                "level": degraded_level.value,
                "cohorts": [str(payload["cohort"]) for payload in degraded_cohorts],
            }
            if len(degraded_cohorts) == 1:
                degraded_payload["cohort"] = degraded_cohorts[0]["cohort"]
            if revisions:
                degraded_payload["probability_semantics_revisions"] = revisions
            degraded[strategy] = degraded_payload
            aggregate_payload["level"] = degraded_level.value
        elif all_p and all(
            bool(payload.get("thin_sample_no_verdict"))
            for payload in cohort_payloads.values()
        ):
            aggregate_payload["thin_sample_no_verdict"] = True
        by_strategy[strategy] = aggregate_payload

    by_mechanism: dict[str, dict[str, object]] = {}
    for mechanism, bucket in sorted(mechanism_buckets.items()):
        p_values = list(bucket["p"])  # type: ignore[index]
        outcomes = list(bucket["o"])  # type: ignore[index]
        strategy_counts = dict(bucket["strategy_counts"])  # type: ignore[index]
        score = brier_score(p_values, outcomes)
        level = evaluate_brier(score, thresholds)
        sample_size = len(p_values)
        independent_target_date_count = len(bucket["target_dates"])  # type: ignore[arg-type]
        payload = {
            "sample_size": sample_size,
            "independent_target_date_count": independent_target_date_count,
            "missing_target_date_count": int(bucket["missing_target_date_count"]),
            "brier": round(float(score), 6),
            "level": level.value,
            "strategy_counts": strategy_counts,
        }
        if independent_target_date_count < _STRATEGY_BRIER_MIN_SAMPLE:
            payload["level"] = RiskLevel.GREEN.value
            payload["thin_sample_no_verdict"] = True
            by_mechanism[mechanism] = payload
            continue
        by_mechanism[mechanism] = payload
        if level == RiskLevel.GREEN:
            continue
        for strategy, member_sample_size in sorted(strategy_counts.items()):
            if strategy in degraded:
                degraded[strategy]["corroborating_mechanism"] = mechanism
                continue
            degraded[strategy] = {
                "sample_size": sample_size,
                "independent_target_date_count": independent_target_date_count,
                "brier": round(float(score), 6),
                "level": level.value,
                "cohort": mechanism,
                "member_sample_size": member_sample_size,
            }

    return {
        "by_strategy": by_strategy,
        "by_mechanism": by_mechanism,
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
    probability_semantics_scopes: Mapping[str, set[str]] | None = None,
    issued_at: str,
) -> dict[str, int | str]:
    if not _table_exists(conn, "risk_actions"):
        logger.info("RiskGuard durable risk_actions table unavailable; skipping action emission")
        return {
            "status": "skipped_missing_table",
            "emitted_count": 0,
            "expired_count": 0,
        }

    def _scope_covers_reason(reason: str, revisions: set[str]) -> bool:
        if reason.startswith("brier_degraded("):
            return True
        if reason.startswith(
            (
                "market_relative_alpha_unproven(",
                "day0_revision_probation_",
                "qkernel_revision_probation_",
            )
        ):
            marker = ",revision="
            if marker not in reason or not reason.endswith(")"):
                return False
            reason_revisions = {
                revision.strip()
                for revision in reason.rsplit(marker, 1)[1][:-1].split(",")
                if revision.strip()
            }
            return bool(reason_revisions) and reason_revisions.issubset(revisions)
        return False

    recommended = {
        strategy: (
            "|".join(sorted(reasons)),
            json.dumps(
                {
                    "gate": True,
                    "probability_semantics_revisions": sorted(
                        (probability_semantics_scopes or {}).get(strategy, set())
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if (probability_semantics_scopes or {}).get(strategy)
            and all(
                _scope_covers_reason(
                    reason,
                    (probability_semantics_scopes or {}).get(
                        strategy, set()
                    ),
                )
                for reason in reasons
            )
            else "true",
        )
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

    for strategy, (reason, value) in recommended.items():
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
            ) VALUES (?, ?, 'gate', ?, ?, NULL, ?, 'riskguard', 50, 'active')
            ON CONFLICT(action_id) DO UPDATE SET
                strategy_key = excluded.strategy_key,
                value = excluded.value,
                issued_at = excluded.issued_at,
                effective_until = NULL,
                reason = excluded.reason,
                precedence = excluded.precedence,
                status = 'active'
            """,
            (action_id, strategy, value, issued_at, reason),
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
    recommended_strategy_gate_scopes: Mapping[str, set[str]] | None = None,
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
            probability_semantics_scopes=recommended_strategy_gate_scopes,
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
    from src.state.db import ensure_single_live_cutover_generation_table

    ensure_single_live_cutover_generation_table(conn)
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


_RISK_DETAILS_CONTRACT_KEYS = (
    "execution_quality_level",
    "strategy_signal_level",
    "recommended_controls",
    "recommended_strategy_gates",
)


def _risk_details_from_row(row: sqlite3.Row) -> dict:
    try:
        details = json.loads(row["details_json"]) if row["details_json"] else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    return details if isinstance(details, dict) else {}


def _risk_details_contract_from_full_row(row: sqlite3.Row) -> dict:
    details = _risk_details_from_row(row)
    return {key: details[key] for key in _RISK_DETAILS_CONTRACT_KEYS}


def _degraded_risk_details_contract() -> dict:
    return {
        "execution_quality_level": RiskLevel.DATA_DEGRADED.value,
        "strategy_signal_level": RiskLevel.DATA_DEGRADED.value,
        "recommended_controls": [],
        "recommended_strategy_gates": [],
    }


def _full_risk_row_is_fresh(row: sqlite3.Row, *, now: datetime) -> bool:
    details = _risk_details_from_row(row)
    if details.get("riskguard_degraded_reason"):
        return False
    if any(key not in details for key in _RISK_DETAILS_CONTRACT_KEYS):
        return False
    checked_at = datetime.fromisoformat(str(row["checked_at"]).replace("Z", "+00:00"))
    return (now - checked_at).total_seconds() <= 300


def _latest_fresh_full_risk_row(conn: sqlite3.Connection, *, now: datetime) -> sqlite3.Row | None:
    rows = conn.execute(
        """
        SELECT level, checked_at, details_json
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
    host_power = host_power_runway_snapshot()
    host_power_level = RiskLevel(str(host_power["level"]))
    now = datetime.now(timezone.utc)
    now_ts = now.isoformat()
    risk_conn = get_connection(RISK_DB_PATH, write_class="live")
    try:
        init_risk_db(risk_conn)
        previous_full = _latest_fresh_full_risk_row(risk_conn, now=now)
        if previous_full is None:
            level = RiskLevel.DATA_DEGRADED
            details = {
                **_degraded_risk_details_contract(),
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
            details = {
                **_risk_details_contract_from_full_row(previous_full),
                "status": "dependency_db_locked_previous_risk_level_preserved",
                "riskguard_degraded_reason": "dependency_db_locked",
                "bankroll_truth_source": "polymarket_wallet",
                "dependency_db_lock_error": str(exc),
                "full_metrics_status": "locked_previous_fresh_level_preserved",
                "previous_full_risk_level": previous_full["level"],
                "previous_full_risk_checked_at": previous_full["checked_at"],
                "conservative_floor_applied": False,
            }
        stored_level = level
        level = overall_level(level, host_power_level)
        details["host_power_level"] = host_power_level.value
        details["host_power"] = host_power
        details["host_power_floor_applied"] = level is not stored_level
        risk_conn.execute(
            """
            INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
            VALUES (?, NULL, NULL, NULL, ?, ?)
            """,
            (
                level.value,
                json.dumps(details),
                now_ts,
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
    host_power = host_power_runway_snapshot()
    host_power_level = RiskLevel(str(host_power["level"]))
    now = datetime.now(timezone.utc)
    risk_conn = get_connection(RISK_DB_PATH, write_class="live")
    try:
        init_risk_db(risk_conn)
        previous_full = _latest_fresh_full_risk_row(risk_conn, now=now)
        if previous_full is None:
            return
        previous_level = RiskLevel(str(previous_full["level"]))
        level = overall_level(previous_level, host_power_level)
        details = {
            **_risk_details_contract_from_full_row(previous_full),
            "status": "metrics_in_progress_previous_risk_level_preserved",
            "riskguard_degraded_reason": "metrics_refresh_in_progress",
            "full_metrics_status": "in_progress_previous_fresh_level_preserved",
            "previous_full_risk_level": previous_full["level"],
            "previous_full_risk_checked_at": previous_full["checked_at"],
            "host_power_level": host_power_level.value,
            "host_power": host_power,
            "host_power_floor_applied": level is not previous_level,
        }
        risk_conn.execute(
            """
            INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
            VALUES (?, NULL, NULL, NULL, ?, ?)
            """,
            (
                level.value,
                json.dumps(details),
                now.isoformat(),
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
    host_power = host_power_runway_snapshot()
    host_power_level = RiskLevel(str(host_power["level"]))
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
            now_dt = datetime.now(timezone.utc)
            now_ts = now_dt.isoformat()
            previous_full = _latest_fresh_full_risk_row(risk_conn, now=now_dt)
            contract = _degraded_risk_details_contract()
            if previous_full is not None:
                previous_contract = _risk_details_contract_from_full_row(previous_full)
                contract["recommended_controls"] = previous_contract["recommended_controls"]
                contract["recommended_strategy_gates"] = previous_contract[
                    "recommended_strategy_gates"
                ]
            details = {
                **contract,
                "status": "bankroll_provider_unavailable",
                "riskguard_degraded_reason": "bankroll_provider_unavailable",
                "full_metrics_status": (
                    "bankroll_unavailable_previous_fresh_contract_preserved"
                    if previous_full is not None
                    else "bankroll_unavailable_no_fresh_full_risk_row"
                ),
                "bankroll_truth": {
                    "source": "polymarket_wallet",
                    "value_usd": None,
                    "fetched_at": None,
                    "staleness_seconds": None,
                    "cached": False,
                    "reason": "collateral snapshot and direct wallet query both unavailable",
                },
            }
            if previous_full is not None:
                details["previous_full_risk_level"] = previous_full["level"]
                details["previous_full_risk_checked_at"] = previous_full["checked_at"]
            level = overall_level(RiskLevel.DATA_DEGRADED, host_power_level)
            details["host_power_level"] = host_power_level.value
            details["host_power"] = host_power
            risk_conn.execute(
                """
                INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
                VALUES (?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    level.value,
                    json.dumps(details),
                    now_ts,
                ),
            )
            risk_conn.commit()
            logger.error(
                "RiskGuard tick fail-closed: bankroll truth unavailable "
                "(no fresh collateral snapshot and no direct wallet value)",
            )
            return level

        current_bankroll_usd = float(bankroll_of_record.value_usd)
        settlement_scan_rows = query_authoritative_settlement_rows(
            zeus_conn,
            limit=max(RISKGUARD_SETTLEMENT_LIMIT, RISKGUARD_BRIER_SCAN_LIMIT),
        )
        realized_settlement_rows = query_authoritative_settlement_rows(
            zeus_conn,
            limit=None,
            not_before=(
                datetime.now(timezone.utc) - RISKGUARD_REALIZED_TELEMETRY_WINDOW
            ).isoformat(),
        )
        settlement_scan_rows = _bind_brier_probability_identities(
            zeus_conn,
            settlement_scan_rows,
        )
        (
            settlement_scan_rows,
            probability_semantics_binding,
        ) = _bind_qkernel_probability_semantics(settlement_scan_rows)
        (
            settlement_scan_rows,
            day0_probability_semantics_binding,
        ) = _bind_day0_probability_semantics(settlement_scan_rows)
        settlement_scan_rows = _bind_entry_market_benchmarks(
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
            economic_ready = settlement_economic_ready(row)
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
            settlement_rows=realized_settlement_rows,
        )
        portfolio = replace(portfolio, recent_exits=realized_exits)

        brier_metric_rows = _riskguard_brier_metric_rows(brier_candidate_rows)
        brier_actuating_rows = _riskguard_brier_actuating_rows(brier_metric_rows)
        market_relative_alpha_evalue = float(
            thresholds.get("market_relative_alpha_rejection_evalue", 10.0)
        )
        market_relative_alpha_window_days = float(
            thresholds.get("market_relative_alpha_window_days", 7.0)
        )
        market_relative_alpha_as_of = datetime.now(timezone.utc)
        (
            qkernel_market_relative_alpha_shadow_rows,
            qkernel_market_relative_alpha_shadow_status,
        ) = _settled_qkernel_market_relative_alpha_shadow_rows(
            zeus_conn,
            window_days=market_relative_alpha_window_days,
            as_of=market_relative_alpha_as_of,
        )
        qkernel_live_realized_capital_curve = (
            _qkernel_live_realized_capital_curve(
                zeus_conn,
                window_days=market_relative_alpha_window_days,
                as_of=market_relative_alpha_as_of,
            )
        )
        (
            qkernel_actual_global_capital_rows,
            qkernel_actual_global_capital_binding,
        ) = _bind_actual_global_capital_evidence(
            zeus_conn,
            brier_actuating_rows,
            strategy_key="forecast_qkernel_entry",
            capital_curve=qkernel_live_realized_capital_curve,
        )
        market_relative_alpha_evidence = _qkernel_market_relative_alpha_evidence(
            qkernel_actual_global_capital_rows
            + qkernel_market_relative_alpha_shadow_rows,
            rejection_evalue=market_relative_alpha_evalue,
            window_days=market_relative_alpha_window_days,
            as_of=market_relative_alpha_as_of,
        )
        qkernel_market_relative_alpha_gate_evidence = (
            _market_relative_alpha_evidence(
                qkernel_actual_global_capital_rows
                + qkernel_market_relative_alpha_shadow_rows,
                strategy_key="forecast_qkernel_entry",
                rejection_evalue=market_relative_alpha_evalue,
                window_days=market_relative_alpha_window_days,
                as_of=market_relative_alpha_as_of,
            )
        )
        qkernel_market_relative_alpha_observation = (
            _market_relative_alpha_gate_reason(
                probability_semantics_binding,
                qkernel_market_relative_alpha_gate_evidence,
                required_evalue=market_relative_alpha_evalue,
            )
        )
        (
            qkernel_market_relative_alpha_gate_reason,
            qkernel_market_relative_alpha_gate_revisions,
        ) = _market_relative_alpha_rejection_gate_reason(
            probability_semantics_binding,
            qkernel_market_relative_alpha_gate_evidence,
            required_evalue=market_relative_alpha_evalue,
        )
        qkernel_market_relative_alpha_unproven_revisions = (
            _market_relative_alpha_unproven_revisions(
                probability_semantics_binding,
                qkernel_market_relative_alpha_gate_evidence,
            )
        )
        (
            qkernel_revision_probation_gate_reason,
            qkernel_revision_probation_gate_revisions,
        ) = _qkernel_revision_probation_gate_reason(
            probability_semantics_binding,
            qkernel_market_relative_alpha_gate_evidence,
            qkernel_live_realized_capital_curve,
        )
        qkernel_revision_probation_gate_required = (
            qkernel_revision_probation_gate_reason is not None
        )
        (
            day0_market_relative_alpha_shadow_rows,
            day0_market_relative_alpha_shadow_status,
        ) = _settled_day0_market_relative_alpha_shadow_rows(
            zeus_conn,
            window_days=market_relative_alpha_window_days,
            as_of=market_relative_alpha_as_of,
        )
        day0_live_realized_capital_curve = _day0_live_realized_capital_curve(
            zeus_conn,
            window_days=market_relative_alpha_window_days,
            as_of=market_relative_alpha_as_of,
        )
        (
            day0_actual_global_capital_rows,
            day0_actual_global_capital_binding,
        ) = _bind_actual_global_capital_evidence(
            zeus_conn,
            brier_actuating_rows,
            strategy_key="day0_nowcast_entry",
            capital_curve=day0_live_realized_capital_curve,
        )
        day0_market_relative_alpha_evidence = _market_relative_alpha_evidence(
            day0_actual_global_capital_rows
            + day0_market_relative_alpha_shadow_rows,
            strategy_key="day0_nowcast_entry",
            rejection_evalue=market_relative_alpha_evalue,
            window_days=market_relative_alpha_window_days,
            as_of=market_relative_alpha_as_of,
        )
        day0_market_relative_alpha_observation = (
            _market_relative_alpha_gate_reason(
                day0_probability_semantics_binding,
                day0_market_relative_alpha_evidence,
                required_evalue=market_relative_alpha_evalue,
            )
        )
        (
            day0_market_relative_alpha_gate_reason,
            day0_market_relative_alpha_gate_revisions,
        ) = _market_relative_alpha_rejection_gate_reason(
            day0_probability_semantics_binding,
            day0_market_relative_alpha_evidence,
            required_evalue=market_relative_alpha_evalue,
        )
        day0_market_relative_alpha_gate_required = (
            day0_market_relative_alpha_gate_reason is not None
        )
        (
            day0_revision_probation_gate_reason,
            day0_revision_probation_gate_revisions,
        ) = _day0_revision_probation_gate_reason(
            day0_probability_semantics_binding,
            day0_market_relative_alpha_evidence,
            day0_live_realized_capital_curve,
        )
        day0_revision_probation_gate_required = (
            day0_revision_probation_gate_reason is not None
        )
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
        decision_law_identity_ready_count = sum(
            bool(row.get("decision_law_identity_ready", False))
            for row in brier_metric_rows
        )
        decision_law_identity_block_reasons: dict[str, int] = {}
        for row in brier_metric_rows:
            if row.get("decision_law_identity_ready", False):
                continue
            reason = str(
                row.get("decision_law_identity_blocked_reason") or "unbound"
            )
            decision_law_identity_block_reasons[reason] = (
                decision_law_identity_block_reasons.get(reason, 0) + 1
            )
        observed_p_forecasts = [
            float(r["p_posterior"]) for r in brier_metric_rows
        ]
        observed_outcomes = [int(r["outcome"]) for r in brier_metric_rows]
        p_forecasts = [float(r["p_posterior"]) for r in brier_actuating_rows]
        outcomes = [int(r["outcome"]) for r in brier_actuating_rows]
        brier_evidence_ready_rows = _brier_evidence_ready_rows(
            brier_actuating_rows
        )
        evidence_p_forecasts = [
            float(r["p_posterior"]) for r in brier_evidence_ready_rows
        ]
        evidence_outcomes = [
            int(r["outcome"]) for r in brier_evidence_ready_rows
        ]
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
        observed_b_score = (
            brier_score(observed_p_forecasts, observed_outcomes)
            if observed_p_forecasts
            else 0.0
        )
        b_score = brier_score(p_forecasts, outcomes) if p_forecasts else 0.0
        evidence_b_score = (
            brier_score(evidence_p_forecasts, evidence_outcomes)
            if evidence_p_forecasts
            else 0.0
        )
        d_accuracy = directional_accuracy(p_forecasts, outcomes) if p_forecasts else 0.5

        # Evaluate levels. Portfolio Brier is the headline quality metric, but
        # a breach that is fully attributable to canonical strategies can be
        # enforced through durable strategy gates. ORANGE/RED additionally
        # require read-after-write gate confirmation and a GREEN residual
        # portfolio; otherwise they remain global fail-closed. Brier measures
        # an entry probability law, so even a severe but exactly attributed
        # breach does not authorize price-insensitive liquidation of holdings.
        portfolio_brier_raw_level = (
            evaluate_brier(b_score, thresholds) if p_forecasts else RiskLevel.GREEN
        )
        portfolio_brier_thin_sample = bool(p_forecasts) and not evidence_p_forecasts
        empty_brier_breakdown = {
            "by_strategy": {},
            "by_mechanism": {},
            "degraded_strategies": {},
            "unclassified_count": 0,
            "classified_count": 0,
        }
        brier_strategy_breakdown = (
            _strategy_brier_breakdown(brier_actuating_rows, thresholds)
            if p_forecasts
            else empty_brier_breakdown
        )
        brier_verdict_breakdown = (
            _strategy_brier_breakdown(brier_evidence_ready_rows, thresholds)
            if evidence_p_forecasts
            else empty_brier_breakdown
        )
        risk_level_values = {level.value for level in RiskLevel}
        degraded_brier_levels = [
            RiskLevel(str(payload["level"]))
            for payload in brier_verdict_breakdown.get(
                "degraded_strategies", {}
            ).values()
            if isinstance(payload, dict)
            and str(payload.get("level") or "") in risk_level_values
        ]
        # Only a homogeneous probability cohort with enough evidence may
        # actuate. The shared EV action law is not a probability identity.
        # Raw pooled Brier remains telemetry; current-law cohorts retain the
        # settlement -> learning -> admission feedback loop independently.
        #
        # Brier governs entry admission only. It cannot authorize a
        # price-insensitive held-position liquidation; all unlocalized Brier
        # breaches are capped to YELLOW below. Current-state RED inputs retain
        # their normal sweep authority.
        #
        # SCOPE: evidence-complete current-law probability cohorts.
        # DRAIN: every 60-second tick rebinds immutable fill q identities and
        # recomputes the bounded settlement sample.
        # RESET: a strategy gate expires when its current-law verdict clears;
        # superseded/unstamped laws are excluded before this point.
        portfolio_brier_level = (
            overall_level(*degraded_brier_levels)
            if degraded_brier_levels
            else RiskLevel.GREEN
        )
        brier_level = portfolio_brier_level
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
        recommended_strategy_gate_scopes: dict[str, set[str]] = {}
        # Each strategy bootstraps one exact current q/book/wealth probe per
        # revision. While that probe is unresolved, or after its realized
        # capital is nonpositive, the same revision is gated until exact-selector
        # counterfactual evidence validates it. Every ordinary source, price,
        # Brier, Kelly, global-ranking, and submit-time JIT boundary stays
        # cumulative; held monitoring and exits remain outside this entry policy.
        probability_semantics_level = RiskLevel.GREEN
        if probability_semantics_binding.get("status") == "unavailable":
            probability_semantics_level = RiskLevel.DATA_DEGRADED
            _append_reason(
                recommended_strategy_gate_reasons,
                "forecast_qkernel_entry",
                "probability_semantics_authority_unavailable",
            )
        for strategy, binding, reason, alpha_gate_revisions in (
            (
                "forecast_qkernel_entry",
                probability_semantics_binding,
                qkernel_market_relative_alpha_gate_reason,
                qkernel_market_relative_alpha_gate_revisions,
            ),
            (
                "day0_nowcast_entry",
                day0_probability_semantics_binding,
                day0_market_relative_alpha_gate_reason,
                day0_market_relative_alpha_gate_revisions,
            ),
        ):
            if reason is None:
                continue
            _append_reason(
                recommended_strategy_gate_reasons,
                strategy,
                reason,
            )
            revisions = {
                str(revision).strip()
                for revision in alpha_gate_revisions
                if str(revision).strip()
            }
            if revisions:
                recommended_strategy_gate_scopes.setdefault(
                    strategy, set()
                ).update(revisions)
        if qkernel_revision_probation_gate_reason is not None:
            _append_reason(
                recommended_strategy_gate_reasons,
                "forecast_qkernel_entry",
                qkernel_revision_probation_gate_reason,
            )
            recommended_strategy_gate_scopes.setdefault(
                "forecast_qkernel_entry", set()
            ).update(qkernel_revision_probation_gate_revisions)
        if day0_revision_probation_gate_reason is not None:
            _append_reason(
                recommended_strategy_gate_reasons,
                "day0_nowcast_entry",
                day0_revision_probation_gate_reason,
            )
            recommended_strategy_gate_scopes.setdefault(
                "day0_nowcast_entry", set()
            ).update(day0_revision_probation_gate_revisions)
        degraded_brier_strategies = brier_verdict_breakdown.get(
            "degraded_strategies", {}
        )
        clean_brier_attribution = (
            isinstance(degraded_brier_strategies, dict)
            and bool(degraded_brier_strategies)
            and int(brier_verdict_breakdown.get("unclassified_count", 0) or 0) == 0
            and all(
                str(strategy) in CANONICAL_STRATEGY_KEYS
                for strategy in degraded_brier_strategies
            )
        )

        def _append_brier_degraded_gate_reasons() -> None:
            for strategy, payload in sorted(degraded_brier_strategies.items()):
                if not isinstance(payload, dict):
                    continue
                revisions = {
                    str(revision).strip()
                    for revision in payload.get(
                        "probability_semantics_revisions", []
                    )
                    if str(revision).strip()
                }
                if revisions:
                    recommended_strategy_gate_scopes.setdefault(
                        str(strategy), set()
                    ).update(revisions)
                cohort = payload.get("cohort") if revisions else None
                cohort_suffix = f",cohort={cohort}" if cohort else ""
                _append_reason(
                    recommended_strategy_gate_reasons,
                    str(strategy),
                    (
                        "brier_degraded("
                        f"level={payload.get('level')},"
                        f"brier={payload.get('brier')},"
                        f"sample={payload.get('sample_size')}"
                        f"{cohort_suffix}"
                        ")"
                    ),
                )

        if portfolio_brier_level == RiskLevel.YELLOW and clean_brier_attribution:
            brier_strategy_localization = {
                "status": "pending_durable_strategy_gate",
                "gated_strategies": sorted(
                    str(strategy) for strategy in degraded_brier_strategies
                ),
            }
            _append_brier_degraded_gate_reasons()
        elif (
            portfolio_brier_level in {RiskLevel.ORANGE, RiskLevel.RED}
            and clean_brier_attribution
        ):
            strength = portfolio_brier_level.value.lower()
            brier_strategy_localization = {
                "status": f"pending_durable_strategy_gate_{strength}",
                "gated_strategies": sorted(
                    str(strategy) for strategy in degraded_brier_strategies
                ),
            }
            _append_brier_degraded_gate_reasons()
        elif portfolio_brier_level != RiskLevel.GREEN:
            # Historical probability quality can stop new exposure, but it
            # cannot create price-insensitive SELL authority.
            brier_level = RiskLevel.YELLOW
            brier_strategy_localization = {
                "status": "not_localized",
                "reason": "portfolio_brier_requires_global_level",
                "portfolio_brier_level": portfolio_brier_level.value,
                "unclassified_count": int(
                    brier_verdict_breakdown.get("unclassified_count", 0) or 0
                ),
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
        # Tracker edge compression summarizes past decisions and stays learning
        # telemetry. Current executable edge is recomputed inside the auction.
        strategy_signal_level = RiskLevel.GREEN
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
        #   3. Current probability/source authority and executable economics
        #      already fail closed; execution_decay measured fills, not either.
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
            recommended_strategy_gate_scopes=recommended_strategy_gate_scopes,
            now=now,
            position_view=portfolio_truth.get("_strategy_health_position_view"),
        )
        market_relative_alpha_gate_confirmation: dict[str, bool] = {}
        day0_market_relative_alpha_gate_confirmation: dict[str, bool] = {}
        if (
            qkernel_market_relative_alpha_gate_reason is not None
            or qkernel_revision_probation_gate_required
        ):
            market_relative_alpha_gate_confirmation = (
                _confirm_active_durable_strategy_gates(
                    zeus_conn,
                    ["forecast_qkernel_entry"],
                )
            )
        if (
            day0_market_relative_alpha_gate_required
            or day0_revision_probation_gate_required
        ):
            day0_market_relative_alpha_gate_confirmation = (
                _confirm_active_durable_strategy_gates(
                    zeus_conn,
                    ["day0_nowcast_entry"],
                )
            )
        required_alpha_gates_confirmed = all(
            confirmation.get(strategy, False)
            for strategy, confirmation, required in (
                (
                    "forecast_qkernel_entry",
                    market_relative_alpha_gate_confirmation,
                    (
                        qkernel_market_relative_alpha_gate_reason is not None
                        or qkernel_revision_probation_gate_required
                    ),
                ),
                (
                    "day0_nowcast_entry",
                    day0_market_relative_alpha_gate_confirmation,
                    (
                        day0_market_relative_alpha_gate_required
                        or day0_revision_probation_gate_required
                    ),
                ),
            )
            if required
        )
        if (
            qkernel_market_relative_alpha_gate_reason is not None
            or qkernel_revision_probation_gate_required
            or day0_market_relative_alpha_gate_required
            or day0_revision_probation_gate_required
        ) and not required_alpha_gates_confirmed:
            # A missing durable localized gate must not turn missing capital
            # authority into permission. DATA_DEGRADED blocks new entries while
            # preserving monitor/exit lanes.
            probability_semantics_level = RiskLevel.DATA_DEGRADED
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
        elif brier_strategy_localization.get("status") in {
            "pending_durable_strategy_gate_orange",
            "pending_durable_strategy_gate_red",
        }:
            strong_scope = portfolio_brier_level.value.lower()
            strong_gated_strategies = list(
                brier_strategy_localization.get("gated_strategies", [])
            )
            if durable_action_status.get("status") == "emitted":
                gate_confirmation = _confirm_active_durable_strategy_gates(
                    zeus_conn,
                    strong_gated_strategies,
                )
                all_gates_confirmed = bool(strong_gated_strategies) and all(
                    gate_confirmation.values()
                )
            else:
                gate_confirmation = {
                    strategy: False for strategy in strong_gated_strategies
                }
                all_gates_confirmed = False

            if all_gates_confirmed:
                (
                    residual_level,
                    residual_score,
                    residual_sample_size,
                    residual_thin_excluded,
                ) = _residual_active_portfolio_brier_level(
                    brier_actuating_rows,
                    thresholds,
                    set(strong_gated_strategies),
                )
                if residual_level == RiskLevel.GREEN:
                    brier_level = RiskLevel.GREEN
                    brier_strategy_localization = {
                        **brier_strategy_localization,
                        "status": f"localized_{strong_scope}_scope",
                        "durable_risk_action_status": durable_action_status.get("status"),
                        "gate_confirmation": gate_confirmation,
                        "residual_brier_level": residual_level.value,
                        "residual_brier_score": round(float(residual_score), 6),
                        "residual_sample_size": residual_sample_size,
                        "thin_sample_excluded_strategies": residual_thin_excluded,
                    }
                else:
                    # A Brier failure governs probability-law admission. If
                    # strong localization cannot prove a GREEN residual, block
                    # every new entry (YELLOW) but do not convert historical
                    # scoring error into a price-insensitive RED liquidation.
                    brier_level = RiskLevel.YELLOW
                    brier_strategy_localization = {
                        **brier_strategy_localization,
                        "status": f"{strong_scope}_residual_portfolio_not_green",
                        "durable_risk_action_status": durable_action_status.get("status"),
                        "gate_confirmation": gate_confirmation,
                        "residual_brier_level": residual_level.value,
                        "residual_brier_score": round(float(residual_score), 6),
                        "residual_sample_size": residual_sample_size,
                        "thin_sample_excluded_strategies": residual_thin_excluded,
                    }
            else:
                # Missing durable scope enforcement falls back to the global
                # entry block. It does not create SELL authority.
                brier_level = RiskLevel.YELLOW
                brier_strategy_localization = {
                    **brier_strategy_localization,
                    "status": (
                        "durable_strategy_gate_unconfirmed_"
                        + (
                            "global_entry_block"
                            if portfolio_brier_level == RiskLevel.RED
                            else f"global_{strong_scope}"
                        )
                    ),
                    "durable_risk_action_status": durable_action_status.get("status"),
                    "gate_confirmation": gate_confirmation,
                }

        localized_orange_scope = brier_strategy_localization.get("status") == "localized_orange_scope"
        localized_red_scope = (
            brier_strategy_localization.get("status") == "localized_red_scope"
        )

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
        daily_loss_snapshot = _realized_window_loss_telemetry(
            realized_exits,
            now=now,
            lookback=timedelta(hours=24),
            degraded=realized_degraded,
            source=loss_source,
        )
        weekly_loss_snapshot = _realized_window_loss_telemetry(
            realized_exits,
            now=now,
            lookback=RISKGUARD_REALIZED_TELEMETRY_WINDOW,
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
        storage_capacity = storage_capacity_snapshot()
        storage_capacity_level = RiskLevel(str(storage_capacity["level"]))

        level = overall_level(
            brier_level,
            settlement_quality_level,
            execution_quality_level,
            strategy_signal_level,
            collateral_identity_level,
            portfolio_consistency_level,
            unresolved_exposure_level,
            probability_semantics_level,
            storage_capacity_level,
            host_power_level,
        )

        risk_conn.execute("""
            INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            level.value, b_score, d_accuracy, None,
            json.dumps({
                "brier_level": brier_level.value,
                "portfolio_brier_level": portfolio_brier_level.value,
                "portfolio_brier_raw_level": portfolio_brier_raw_level.value,
                "portfolio_brier_thin_sample_no_verdict": portfolio_brier_thin_sample,
                "brier_observed_all_lineage_score": round(float(observed_b_score), 6),
                "brier_observed_all_lineage_sample_size": len(brier_metric_rows),
                "brier_actuating_sample_size": len(brier_actuating_rows),
                "brier_evidence_ready_score": round(float(evidence_b_score), 6),
                "brier_evidence_ready_sample_size": len(
                    brier_evidence_ready_rows
                ),
                # ORANGE-localization audit surface (2026-07-04): the raw,
                # unfiltered portfolio view (all strategies pooled) vs. the
                # view that actually DRIVES admission after any localization
                # (YELLOW-to-durable-gate or ORANGE-scope) is applied.
                # Kept as an explicit alias of portfolio_brier_level/brier_level
                # so downstream consumers see a coherent, self-describing pair
                # regardless of which localization branch (if any) fired.
                "brier_all_strategies_level": portfolio_brier_raw_level.value,
                "brier_active_portfolio_level": brier_level.value,
                "localized_orange_scope": localized_orange_scope,
                "localized_red_scope": localized_red_scope,
                "brier_strategy_breakdown": brier_strategy_breakdown,
                "brier_verdict_breakdown": brier_verdict_breakdown,
                "brier_strategy_localization": brier_strategy_localization,
                "probability_semantics_level": probability_semantics_level.value,
                "probability_semantics_binding": probability_semantics_binding,
                "day0_probability_semantics_binding": (
                    day0_probability_semantics_binding
                ),
                "market_relative_alpha_evidence": market_relative_alpha_evidence,
                "market_relative_alpha_gate_evidence": (
                    qkernel_market_relative_alpha_gate_evidence
                ),
                "market_relative_alpha_gate_reason": (
                    qkernel_market_relative_alpha_gate_reason
                ),
                "market_relative_alpha_observation": (
                    qkernel_market_relative_alpha_observation
                ),
                "market_relative_alpha_unproven_revisions": (
                    qkernel_market_relative_alpha_unproven_revisions
                ),
                "qkernel_revision_probation_gate_required": (
                    qkernel_revision_probation_gate_required
                ),
                "qkernel_revision_probation_gate_reason": (
                    qkernel_revision_probation_gate_reason
                ),
                "market_relative_alpha_admission_role": (
                    "revision_scoped_rejection_gate"
                ),
                "qkernel_market_relative_alpha_shadow": (
                    qkernel_market_relative_alpha_shadow_status
                ),
                "qkernel_actual_global_capital_binding": (
                    qkernel_actual_global_capital_binding
                ),
                "market_relative_alpha_gate_confirmation": (
                    market_relative_alpha_gate_confirmation
                ),
                "qkernel_live_realized_capital_curve": (
                    qkernel_live_realized_capital_curve
                ),
                "day0_market_relative_alpha_evidence": (
                    day0_market_relative_alpha_evidence
                ),
                "day0_market_relative_alpha_admission_role": (
                    "revision_scoped_rejection_gate"
                ),
                "day0_market_relative_alpha_gate_reason": (
                    day0_market_relative_alpha_gate_reason
                ),
                "day0_revision_probation_gate_required": (
                    day0_revision_probation_gate_required
                ),
                "day0_revision_probation_gate_reason": (
                    day0_revision_probation_gate_reason
                ),
                "day0_market_relative_alpha_observation": (
                    day0_market_relative_alpha_observation
                ),
                "day0_market_relative_alpha_shadow": (
                    day0_market_relative_alpha_shadow_status
                ),
                "day0_actual_global_capital_binding": (
                    day0_actual_global_capital_binding
                ),
                "day0_live_realized_capital_curve": (
                    day0_live_realized_capital_curve
                ),
                "day0_market_relative_alpha_gate_required": (
                    day0_market_relative_alpha_gate_required
                ),
                "day0_market_relative_alpha_gate_confirmation": (
                    day0_market_relative_alpha_gate_confirmation
                ),
                "settlement_quality_level": settlement_quality_level.value,
                "execution_quality_level": execution_quality_level.value,
                "strategy_signal_level": strategy_signal_level.value,
                # T2 (quarantine excision, BLOCKER-1): unbounded obligation or
                # unmapped-family ChainOnlyFact -> DATA_DEGRADED leg.
                "unresolved_exposure_level": unresolved_exposure_level.value,
                "storage_capacity_level": storage_capacity_level.value,
                "storage_capacity": storage_capacity,
                "host_power_level": host_power_level.value,
                "host_power": host_power,
                "daily_loss_level": daily_loss_level.value,
                "weekly_loss_level": weekly_loss_level.value,
                "trailing_loss_decision_role": "record_only",
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
                "settlement_sample_size": len(observed_p_forecasts),
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
                "settlement_brier_actuating_count": len(brier_actuating_rows),
                "settlement_probability_identity_ready_count": probability_identity_ready_count,
                "settlement_probability_identity_unready_count": (
                    len(brier_candidate_rows) - probability_identity_ready_count
                ),
                "settlement_probability_identity_block_reasons": probability_identity_block_reasons,
                "settlement_decision_law_identity_ready_count": (
                    decision_law_identity_ready_count
                ),
                "settlement_decision_law_identity_unready_count": (
                    len(brier_metric_rows) - decision_law_identity_ready_count
                ),
                "settlement_decision_law_identity_block_reasons": (
                    decision_law_identity_block_reasons
                ),
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
            "probability_semantics": probability_semantics_level,
            "storage_capacity": storage_capacity_level,
            "host_power": host_power_level,
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
            "probability_semantics": (
                f"status={probability_semantics_binding.get('status')} "
                f"current={probability_semantics_binding.get('current_count')} "
                f"superseded={probability_semantics_binding.get('superseded_count')} "
                f"missing={probability_semantics_binding.get('missing_count')} "
                f"mixed={probability_semantics_binding.get('mixed_count')}"
            ),
            "storage_capacity": (
                f"status={storage_capacity.get('status')} "
                f"reason={storage_capacity.get('reason')} "
                f"free_bytes={storage_capacity.get('free_bytes')} "
                f"free_ratio={storage_capacity.get('free_ratio')}"
            ),
            "host_power": (
                f"status={host_power.get('status')} "
                f"reason={host_power.get('reason')} "
                f"power_source={host_power.get('power_source')} "
                f"battery_percent={host_power.get('battery_percent')} "
                f"remaining_minutes={host_power.get('remaining_minutes')}"
            ),
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
    host_power = host_power_runway_snapshot()
    host_power_level = RiskLevel(str(host_power["level"]))
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
        storage_capacity = storage_capacity_snapshot()
        storage_capacity_level = RiskLevel(str(storage_capacity["level"]))

        level = overall_level(
            RiskLevel.DATA_DEGRADED if portfolio.portfolio_loader_degraded else RiskLevel.GREEN,
            RiskLevel.GREEN,
            RiskLevel.GREEN,
            RiskLevel.GREEN,
            collateral_identity_level,
            storage_capacity_level,
            host_power_level,
        )

        return level
    finally:
        zeus_conn.close()
        risk_conn.close()


@dataclass(frozen=True)
class RiskAttestation:
    """Typed single read of the five-level RiskGuard authority."""

    level: RiskLevel
    attestation_id: str
    read_at: str
    monotonic_ns: int
    outcome: str = "READ_OK"
    error: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.level, RiskLevel):
            object.__setattr__(self, "level", RiskLevel(str(self.level)))
        if not self.attestation_id or not self.read_at or self.monotonic_ns < 0:
            raise ValueError("risk attestation identity invalid")
        if self.outcome not in {"READ_OK", "READ_ERROR_FAIL_CLOSED"}:
            raise ValueError("risk attestation outcome invalid")
        if self.outcome == "READ_ERROR_FAIL_CLOSED" and self.level is not RiskLevel.RED:
            raise ValueError("risk read errors must remain RED")

    @property
    def observed_red(self) -> bool:
        return self.level is RiskLevel.RED

    def as_payload(self) -> dict[str, object]:
        return {
            "attestation_id": self.attestation_id,
            "level": self.level.value,
            "read_at": self.read_at,
            "monotonic_ns": self.monotonic_ns,
            "outcome": self.outcome,
            "error": self.error,
        }


def read_risk_attestation(*, now: datetime | None = None) -> RiskAttestation:
    """Read RiskGuard exactly once; DB/error surfaces fail closed as RED."""
    try:
        level = get_current_level()
        outcome = "READ_OK"
        error = ""
    except Exception as exc:  # noqa: BLE001 - authority edge is fail closed.
        level = RiskLevel.RED
        outcome = "READ_ERROR_FAIL_CLOSED"
        error = f"{type(exc).__name__}:{str(exc)[:400]}"
    return RiskAttestation(
        level=level,
        attestation_id=uuid.uuid4().hex,
        read_at=(now or datetime.now(timezone.utc)).isoformat(),
        monotonic_ns=time.monotonic_ns(),
        outcome=outcome,
        error=error,
    )


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


# Component `<name>_level` keys persisted into a FULL tick's details_json (see
# the `overall_level(...)` call above `INSERT INTO risk_state` in `_tick_once`).
# NOTE (cause-detail availability finding, item 5b): `collateral_identity_level`
# drives `level` and drives the RED `alert_halt` failed_rules payload, but is
# NOT persisted as a details_json key anywhere in `_tick_once` — a RED driven
# purely by collateral_identity therefore has no queryable per-row cause here.
# `_riskguard_row_causes` falls back to `riskguard_degraded_reason`/`status`,
# and to an explicit "cause_unavailable" marker so that gap is visible in the
# alert/breadcrumb rather than silently reporting an empty cause list.
_RISK_STATE_COMPONENT_LEVEL_KEYS = (
    "brier_level",
    "settlement_quality_level",
    "execution_quality_level",
    "strategy_signal_level",
    "portfolio_consistency_level",
    "unresolved_exposure_level",
    "probability_semantics_level",
    "storage_capacity_level",
    "host_power_level",
)


def _riskguard_row_causes(details: dict) -> list[str]:
    """Extract the non-GREEN component names (or degraded reason) from a risk_state row.

    Full ticks persist one `<component>_level` key per driving component (see
    `_RISK_STATE_COMPONENT_LEVEL_KEYS` above); a non-GREEN value there names the
    check that contributed to the row's overall level. Degraded attestation rows
    (`_persist_dependency_db_locked_attestation`, `_persist_tick_in_progress_attestation`)
    carry only the reduced `_RISK_DETAILS_CONTRACT_KEYS` subset plus
    `riskguard_degraded_reason` — when no component key is present, that reason
    (or `status`) is the only cause available. If neither is present the row's
    cause is genuinely unrecoverable from details_json (see the
    collateral_identity gap noted above); that is reported explicitly rather
    than as a silent empty list.
    """
    if not isinstance(details, dict):
        return ["cause_unavailable"]
    causes = [
        key[: -len("_level")]
        for key in _RISK_STATE_COMPONENT_LEVEL_KEYS
        if details.get(key) not in (None, RiskLevel.GREEN.value)
    ]
    if causes:
        return causes
    reason = details.get("riskguard_degraded_reason") or details.get("status")
    return [str(reason)] if reason else ["cause_unavailable"]


def _riskguard_stuck_non_green_run(conn: sqlite3.Connection, *, now: datetime) -> dict:
    """Find where the current non-GREEN risk_state run started.

    Scans backward from the latest row (ORDER BY id DESC, the indexed PK)
    bounded by STUCK_ALERT_LOOKBACK_ROWS so a long-lived DB never pays an
    unbounded scan for a GREEN row that may not exist within any reasonable
    window. If no GREEN row is found within the cap, the run is reported with
    `lookback_capped=True` and its start is the oldest row seen — a
    conservative underestimate of the true duration.

    Caller must ensure the latest row is non-GREEN; called with an empty or
    all-GREEN table this degenerately reports a zero-duration run.
    """
    rows = conn.execute(
        "SELECT level, checked_at, details_json FROM risk_state "
        "ORDER BY id DESC LIMIT ?",
        (STUCK_ALERT_LOOKBACK_ROWS,),
    ).fetchall()
    if not rows:
        return {
            "run_started_at": now.isoformat(),
            "elapsed_seconds": 0.0,
            "lookback_capped": False,
            "first_causes": [],
            "current_causes": [],
        }

    current_causes = _riskguard_row_causes(_risk_details_from_row(rows[0]))
    run_started_row = rows[0]
    lookback_capped = True
    for row in rows:
        if RiskLevel(row["level"]) == RiskLevel.GREEN:
            lookback_capped = False
            break
        run_started_row = row

    run_started_at = str(run_started_row["checked_at"])
    started_dt = datetime.fromisoformat(run_started_at.replace("Z", "+00:00"))
    elapsed = max(0.0, (now - started_dt).total_seconds())
    first_causes = _riskguard_row_causes(_risk_details_from_row(run_started_row))
    return {
        "run_started_at": run_started_at,
        "elapsed_seconds": elapsed,
        "lookback_capped": lookback_capped,
        "first_causes": first_causes,
        "current_causes": current_causes,
    }


def _write_riskguard_stuck_breadcrumb_atomic(payload: dict) -> None:
    try:
        from src.config import state_path

        out_path = state_path(STUCK_ALERT_BREADCRUMB_FILENAME)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(out_path)
    except Exception as exc:  # noqa: BLE001 - breadcrumb is best-effort.
        logger.warning("RiskGuard stuck-state breadcrumb write failed: %s", exc)


def maybe_alert_riskguard_stuck_non_green(level: RiskLevel) -> None:
    """Escalate a stuck non-GREEN risk_state level from silent to visible.

    SCOPE: logging + a best-effort state/riskguard_stuck_alert.json breadcrumb
    only. This never writes a risk_state row, never changes `level`, and never
    touches get_current_level()/tick() — it is called AFTER a tick already
    persisted the row driving `level`. Mirrors
    `_maybe_alert_held_position_monitor_bootstrap_stall` (src/main.py, commit
    d1aeeeb52, item 5a).

    The 2026-08-24 investigation found RiskGuard stuck non-GREEN explained
    97.6h of August silence (10/11 gaps DATA_DEGRADED, one RED) with zero
    alerts — one 25.6h window was 99.9% non-GREEN. This makes that state
    visible without changing what it does.

    DRAIN: below STUCK_ALERT_AFTER_SECONDS continuous duration, no-op. Past
    it, logs `logger.error` and writes the breadcrumb at most once per
    STUCK_ALERT_REPEAT_SECONDS. Duration is recomputed from risk_state on
    every call (bounded backward scan, `_riskguard_stuck_non_green_run`), not
    tracked only in memory, so a process restart mid-stall still reports the
    correct age. RESET: recovery to GREEN clears the breadcrumb (writes a
    `recovered_at` marker over it) so the next stuck episode starts a fresh
    clock — the in-memory run marker changing to a different `run_started_at`
    also resets the repeat-alert throttle immediately.
    """
    global _riskguard_stuck_alert_run_started_at
    global _riskguard_stuck_alert_last_alert_monotonic

    if level == RiskLevel.GREEN:
        if _riskguard_stuck_alert_run_started_at is not None:
            _write_riskguard_stuck_breadcrumb_atomic({
                "recovered_at": datetime.now(timezone.utc).isoformat(),
                "previous_run_started_at": _riskguard_stuck_alert_run_started_at,
            })
        _riskguard_stuck_alert_run_started_at = None
        _riskguard_stuck_alert_last_alert_monotonic = None
        return

    now = datetime.now(timezone.utc)
    try:
        conn = get_connection(RISK_DB_PATH, write_class=None)
        try:
            run = _riskguard_stuck_non_green_run(conn, now=now)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - alert path is observability-only.
        logger.warning("RiskGuard stuck-state scan failed: %s", exc)
        return

    if run["run_started_at"] != _riskguard_stuck_alert_run_started_at:
        # New episode (first non-GREEN tick after GREEN, or first check since
        # process start) -- fresh clock, repeat-alert throttle resets.
        _riskguard_stuck_alert_run_started_at = run["run_started_at"]
        _riskguard_stuck_alert_last_alert_monotonic = None

    elapsed = run["elapsed_seconds"]
    if elapsed < STUCK_ALERT_AFTER_SECONDS:
        return

    now_monotonic = time.monotonic()
    last_alert = _riskguard_stuck_alert_last_alert_monotonic
    if last_alert is not None and now_monotonic - last_alert < STUCK_ALERT_REPEAT_SECONDS:
        return
    _riskguard_stuck_alert_last_alert_monotonic = now_monotonic

    capped_note = (
        f" [lookback capped at {STUCK_ALERT_LOOKBACK_ROWS // 60}h, true start may be earlier]"
        if run["lookback_capped"]
        else ""
    )
    logger.error(
        "RiskGuard stuck non-GREEN %.0fs (alert threshold %.0fs): level=%s "
        "first_causes=%s current_causes=%s run_started_at=%s%s",
        elapsed,
        STUCK_ALERT_AFTER_SECONDS,
        level.value,
        run["first_causes"],
        run["current_causes"],
        run["run_started_at"],
        capped_note,
    )
    _write_riskguard_stuck_breadcrumb_atomic({
        "level": level.value,
        "run_started_at": run["run_started_at"],
        "elapsed_seconds": elapsed,
        "alert_after_seconds": STUCK_ALERT_AFTER_SECONDS,
        "first_causes": run["first_causes"],
        "current_causes": run["current_causes"],
        "lookback_capped": run["lookback_capped"],
        "lookback_rows": STUCK_ALERT_LOOKBACK_ROWS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


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
            maybe_alert_riskguard_stuck_non_green(level)
        except Exception as e:
            logger.error("RiskGuard tick failed: %s", e)
        time.sleep(60)
