"""Settlement harvester: detects settlements, generates calibration pairs, logs P&L.

Spec §8.1: Hourly cycle:
1. Poll Gamma API for recently settled weather markets
2. Determine which bin won
3. Generate calibration pairs (1 per bin per settlement)
4. Log P&L for held positions that settled
5. Remove settled positions from portfolio
"""

import json
import logging
import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.calibration.manager import maybe_refit_bucket, season_from_date
from src.calibration.effective_sample_size import build_decision_group_for_key, write_decision_groups
from src.calibration.decision_group import compute_id
from src.calibration.store import add_calibration_pair_v2
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity
from src.config import City, cities_by_name, get_mode
from src.contracts.settlement_semantics import SettlementSemantics
from src.contracts.exceptions import SettlementPrecisionError
from src.data.market_scanner import _match_city, _parse_temp_range, infer_temperature_metric, GAMMA_BASE
from src.state.chronicler import log_event
from src.state.decision_chain import (
    SettlementRecord,
    query_legacy_settlement_records,
    store_settlement_records,
)
from src.state.db import (
    get_world_connection,
    get_trade_connection,
    log_market_event_outcomes_v2,
    log_settlement_event,
    log_settlement_v2,
    query_authoritative_settlement_rows,
    query_settlement_events,
    record_token_suppression,
)
from src.architecture.decorators import capability, protects
from src.state.canonical_write import commit_then_export
from src.state.portfolio import (
    ENTRY_ECONOMICS_CORRECTED_COST_BASIS,
    ENTRY_ECONOMICS_MODEL_EDGE_PRICE,
    ENTRY_ECONOMICS_SUBMITTED_LIMIT,
    PortfolioState,
    compute_settlement_close,
    load_portfolio,
    save_portfolio,
    void_position,
)
from src.state.strategy_tracker import get_tracker, save_tracker
from src.riskguard.discord_alerts import alert_redeem
from src.observability.counters import increment as _cnt_inc

logger = logging.getLogger(__name__)

_NON_FILL_ENTRY_ECONOMICS_AUTHORITIES = frozenset({
    ENTRY_ECONOMICS_CORRECTED_COST_BASIS,
    ENTRY_ECONOMICS_MODEL_EDGE_PRICE,
    ENTRY_ECONOMICS_SUBMITTED_LIMIT,
})


def _settlement_economics_for_position(pos) -> tuple[float, float]:
    if getattr(pos, "has_fill_economics_authority", False):
        return float(pos.effective_shares), float(pos.effective_cost_basis_usd)
    authority = str(getattr(pos, "entry_economics_authority", "") or "")
    if authority in _NON_FILL_ENTRY_ECONOMICS_AUTHORITIES:
        raise ValueError(
            "settlement P&L requires fill-derived economics; "
            f"entry_economics_authority={authority!r} "
            f"fill_authority={getattr(pos, 'fill_authority', '')!r}"
        )
    shares = pos.size_usd / pos.entry_price if pos.entry_price > 0 else 0.0
    cost_basis = float(getattr(pos, "cost_basis_usd", 0.0) or getattr(pos, "size_usd", 0.0) or 0.0)
    return float(shares), cost_basis


def _get_canonical_exit_flag() -> bool:
    """Read CANONICAL_EXIT_PATH feature flag from settings.

    B043: typed error taxonomy (SD-B). A broad ``except Exception``
    would silently disable the canonical exit path on any fault
    (TypeError/RuntimeError from a regression in ``feature_flags``),
    indistinguishable from the flag being legitimately False. Narrow
    to the two legitimate "settings surface missing" cases only;
    anything else is a code defect and must propagate.
    """
    try:
        from src.config import settings
        flags = settings.feature_flags
    except (ImportError, AttributeError):
        return False
    return flags.get("CANONICAL_EXIT_PATH", False)


def _next_canonical_sequence_no(conn, position_id: str) -> int:
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
            (position_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 1
    return int(row[0] or 0) + 1


def _has_canonical_position_history(conn, position_id: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM position_events WHERE position_id = ? LIMIT 1",
            (position_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _canonical_phase_before_for_settlement(pos) -> str:
    from src.state.lifecycle_manager import LifecyclePhase, phase_for_runtime_position

    try:
        phase = phase_for_runtime_position(
            state=getattr(pos, "state", ""),
            exit_state=getattr(pos, "exit_state", ""),
            chain_state=getattr(pos, "chain_state", ""),
        )
    except ValueError:
        phase = None

    if phase in {
        LifecyclePhase.PENDING_EXIT,
        LifecyclePhase.ECONOMICALLY_CLOSED,
        LifecyclePhase.DAY0_WINDOW,
        LifecyclePhase.ACTIVE,
    }:
        return phase.value
    return "day0_window" if getattr(pos, "day0_entered_at", "") else "active"


_TERMINAL_PHASES = frozenset({"settled", "voided", "admin_closed", "quarantined"})
_HARVESTER_STAGE2_TRADE_TABLES = (
    "position_events",
    "position_current",
    "decision_log",
    "chronicle",
)
_HARVESTER_STAGE2_SHARED_TABLES = (
    "ensemble_snapshots",
    "calibration_pairs",
    "calibration_decision_group",
    "platt_models",
)

_TRAINING_FORECAST_SOURCES = frozenset({"tigge", "ecmwf_ens"})


def _metric_identity_for(temperature_metric: str | MetricIdentity) -> MetricIdentity:
    return MetricIdentity.from_raw(temperature_metric)


def _forecast_source_from_version(source_model_version: str | None) -> str:
    version = str(source_model_version or "").strip().lower()
    if not version:
        return ""
    if version.startswith("ecmwf_ens"):
        return "ecmwf_ens"
    if version.startswith("tigge"):
        return "tigge"
    if version.startswith("openmeteo"):
        return "openmeteo"
    return version.split("_", 1)[0]


def _is_training_forecast_source(source_model_version: str | None) -> bool:
    return _forecast_source_from_version(source_model_version) in _TRAINING_FORECAST_SOURCES


def _coerce_snapshot_id(snapshot_id: object) -> int | None:
    if snapshot_id in (None, ""):
        return None
    try:
        return int(str(snapshot_id))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ResolvedMarketOutcome:
    """Resolved Gamma child market identity for one binary temperature bin."""

    condition_id: str
    yes_token_id: str
    range_label: str
    range_low: Optional[float]
    range_high: Optional[float]
    yes_won: bool

    def as_v2_outcome_row(self) -> dict:
        return {
            "condition_id": self.condition_id,
            "token_id": self.yes_token_id,
            "outcome": "YES" if self.yes_won else "NO",
        }


def _missing_tables(conn, table_names: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for table_name in table_names:
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
                (table_name,),
            ).fetchone()
        except sqlite3.Error:
            missing.append(table_name)
            continue
        if row is None:
            missing.append(table_name)
    return missing


def _preflight_harvester_stage2_db_shape(trade_conn, shared_conn) -> dict:
    """Check whether Stage-2 calibration learning dependencies are installed."""
    missing_trade = _missing_tables(trade_conn, _HARVESTER_STAGE2_TRADE_TABLES)
    missing_shared = _missing_tables(shared_conn, _HARVESTER_STAGE2_SHARED_TABLES)
    if missing_trade or missing_shared:
        return {
            "stage2_status": "skipped_db_shape_preflight",
            "stage2_skip_reason": "missing_stage2_runtime_tables",
            "stage2_missing_trade_tables": missing_trade,
            "stage2_missing_shared_tables": missing_shared,
        }
    return {
        "stage2_status": "ready",
        "stage2_missing_trade_tables": [],
        "stage2_missing_shared_tables": [],
    }


def _current_phase_in_db(conn, trade_id: str) -> dict:
    """Read the authoritative phase from position_current for the given trade.

    Returns a structured status result: {"status": "ok", "phase": str},
    {"status": "missing"}, or {"status": "error", "reason": str}.
    This is the canonical dedup anchor — stale in-memory pos objects must
    never be used to decide whether a settlement has already been emitted.
    """
    if not trade_id:
        return {"status": "missing"}
    try:
        row = conn.execute(
            "SELECT phase FROM position_current WHERE trade_id = ? LIMIT 1",
            (trade_id,),
        ).fetchone()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    if row is None:
        return {"status": "missing"}
    phase_str = str(row["phase"]) if hasattr(row, "keys") else str(row[0])
    return {"status": "ok", "phase": phase_str}


def _dual_write_canonical_settlement_if_available(
    conn,
    pos,
    *,
    winning_bin: str,
    won: bool,
    outcome: int,
    phase_before: str | None = None,
) -> bool:
    from src.engine.lifecycle_events import build_settlement_canonical_write
    from src.state.db import append_many_and_project

    trade_id = getattr(pos, "trade_id", "")

    if not _has_canonical_position_history(conn, trade_id):
        logger.debug(
            "Canonical settlement dual-write skipped for %s: no prior canonical position history",
            trade_id,
        )
        return False

    # Bug #9 dedup guard: the authoritative source for "is this position already
    # in a terminal phase?" is position_current in the DB, NOT the in-memory pos
    # object. If load_portfolio fell back to the JSON cache (bug #7 path), the
    # pos object may show economically_closed while the DB already reflects
    # settled from an earlier cycle. Refusing re-entry at this layer makes
    # settlement idempotent regardless of the iterator's staleness.
    db_result = _current_phase_in_db(conn, trade_id)
    if db_result["status"] == "error":
        logger.error(
            "Canonical settlement aborted for %s: position_current.phase lookup failed: %s",
            trade_id, db_result.get("reason"),
        )
        return False
        
    db_phase = db_result.get("phase")
    if db_phase in _TERMINAL_PHASES:
        logger.info(
            "Canonical settlement dual-write skipped for %s: position_current.phase=%s already terminal",
            trade_id,
            db_phase,
        )
        return False

    # The terminal dedup above uses db_phase authoritatively. For phase_before
    # metadata, prefer the runtime pos state: db_phase reflects last canonical
    # write but pos may have advanced further (e.g. economically_closed or
    # pending_exit) without intermediate canonical writes.
    resolved_phase_before = (
        phase_before
        or _canonical_phase_before_for_settlement(pos)
        or db_phase
        or "active"
    )

    try:
        events, projection = build_settlement_canonical_write(
            pos,
            winning_bin=winning_bin,
            won=won,
            outcome=outcome,
            sequence_no=_next_canonical_sequence_no(conn, trade_id),
            phase_before=resolved_phase_before,
            source_module="src.execution.harvester",
        )
        append_many_and_project(conn, events, projection)
    except Exception as exc:
        raise RuntimeError(
            f"canonical settlement dual-write failed for {trade_id}: {exc}"
        ) from exc

    return True


def _table_column_names(conn, table_name: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.Error:
        return []
    return [str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in rows]


def _table_columns(conn, table_name: str) -> set[str]:
    return set(_table_column_names(conn, table_name))


def _row_value(row, key: str):
    if isinstance(row, sqlite3.Row):
        return row[key] if key in row.keys() else None
    if isinstance(row, dict):
        return row.get(key)
    return None


def _source_matches_settlement_family(source: str, source_type: str) -> bool:
    src = str(source or "").strip().lower()
    if source_type == "wu_icao":
        return src == "wu_icao_history" or src.startswith("wu_icao_history_")
    if source_type == "noaa":
        return src.startswith("ogimet_metar_")
    if source_type == "hko":
        return src == "hko_daily_api" or src.startswith("hko_daily_api_")
    return False


def _expected_settlement_station_id(city: City) -> str:
    if city.settlement_source_type == "hko":
        return "HKO"
    return str(city.wu_station or "").strip().upper()


def _station_matches_city(row_station: object, city: City) -> bool:
    expected = _expected_settlement_station_id(city)
    if not expected:
        return city.settlement_source_type == "hko"
    station = str(row_station or "").strip().upper()
    if not station:
        return False
    return station == expected or station.startswith(f"{expected}:")


def _lookup_settlement_obs(
    conn,
    city: City,
    target_date: str,
    *,
    temperature_metric: str = "high",
) -> Optional[dict]:
    """Look up source-family-correct observation for the harvester write path.

    Routes per city.settlement_source_type (P-C routing rules, DR-33 plan §3.3):
      - wu_icao   → observations.source='wu_icao_history'
      - noaa      → observations.source LIKE 'ogimet_metar_%'
      - hko       → observations.source='hko_daily_api'
      - cwa_station → no accepted proxy (returns None; row will quarantine)
    """
    metric_identity = _metric_identity_for(temperature_metric)
    st = city.settlement_source_type
    if st == "cwa_station":
        return None
    column_names = _table_column_names(conn, "observations")
    columns = set(column_names)
    if not columns:
        return None
    metric_field = metric_identity.observation_field
    if metric_field not in columns:
        return None
    rows = conn.execute(
        """SELECT *
           FROM observations
           WHERE city = ? AND target_date = ?""",
        (city.name, target_date),
    ).fetchall()
    for r in rows:
        if not isinstance(r, (sqlite3.Row, dict)):
            r = dict(zip(column_names, r))
        src = str(_row_value(r, "source") or "")
        if not _source_matches_settlement_family(src, st):
            continue
        if "authority" in columns and str(_row_value(r, "authority") or "").upper() != "VERIFIED":
            continue
        if "station_id" in columns and not _station_matches_city(_row_value(r, "station_id"), city):
            continue
        observed_temp = _row_value(r, metric_field)
        if observed_temp is None:
            continue
        return {
            "id": _row_value(r, "id"),
            "source": src,
            "high_temp": _row_value(r, "high_temp"),
            "low_temp": _row_value(r, "low_temp"),
            "unit": _row_value(r, "unit"),
            "fetched_at": _row_value(r, "fetched_at"),
            "station_id": _row_value(r, "station_id"),
            "authority": _row_value(r, "authority"),
            "observation_field": metric_field,
            "observed_temp": observed_temp,
        }
    return None


# ---------------------------------------------------------------------------
# T1C extracted functions — settlement / redeem / learning-write separation
# ---------------------------------------------------------------------------

def record_settlement_result(
    trade_conn,
    settlement_records: "list[SettlementRecord]",
    stage2_preflight: dict,
) -> int:
    """Write settlement records to the decision_log table and return count written.

    T1C-SETTLEMENT-NOT-REDEEM: this function ONLY writes settlement facts.
    It does NOT invoke any redeem-state transition. Redeem transitions are
    the sole responsibility of enqueue_redeem_command().
    """
    if not settlement_records:
        return 0
    if "decision_log" in stage2_preflight.get("stage2_missing_trade_tables", []):
        legacy_skipped = len(settlement_records)
        logger.warning(
            "Legacy settlement record storage skipped: decision_log missing; records=%d",
            legacy_skipped,
        )
        return 0
    store_settlement_records(trade_conn, settlement_records, source="harvester")
    return len(settlement_records)


def enqueue_redeem_command(
    conn,
    *,
    condition_id: str,
    payout_asset: str,
    market_id: Optional[str] = None,
    pusd_amount_micro: Optional[int] = None,
    token_amounts: Optional[dict] = None,
    trade_id: str = "",
) -> dict:
    """Enqueue a durable redeem-intent command in the settlement_commands ledger.

    T1C-SETTLEMENT-NOT-REDEEM: this function is the ONLY entry point for
    redeem-state transitions. It uses SettlementState.REDEEM_INTENT_CREATED
    from src/execution/settlement_commands.py (read-only import; that module
    is NOT modified by T1C).

    Returns dict with keys: status ("queued" | "already_exists" | "error"),
    command_id (str | None), reason (str | None).
    """
    from src.execution.settlement_commands import request_redeem, SettlementState  # noqa: F401 — verify import only
    try:
        command_id = request_redeem(
            condition_id,
            payout_asset,
            market_id=market_id or condition_id,
            pusd_amount_micro=pusd_amount_micro,
            token_amounts=token_amounts or {},
            conn=conn,
        )
        logger.info(
            "pUSD redemption for %s (condition=%s) recorded in R1 settlement command ledger: %s",
            trade_id,
            condition_id,
            command_id,
        )
        return {"status": "queued", "command_id": command_id, "reason": None}
    except Exception as exc:
        logger.warning("Redeem deferred for %s: %s (pUSD still claimable later)", trade_id, exc)
        return {"status": "error", "command_id": None, "reason": str(exc)}


def maybe_write_learning_pair(
    conn,
    city: "City",
    target_date: str,
    winning_label: str,
    all_labels: list,
    context: dict,
    temperature_metric: str,
) -> int:
    """Authority-gated wrapper for harvest_settlement().

    T1C-LEARNING-AUTHORITY-GATE: refuses to write calibration pairs unless:
      - context provides a non-empty source_model_version, AND
      - context provides snapshot_training_allowed=True (or snapshot_learning_ready=True)

    T1C-LIVE-PRAW-NOT-TRAINING-DATA: also refuses if the snapshot's source is
    not in the explicit training-source allowlist (_is_training_forecast_source).

    Emits harvester_learning_write_blocked_total{reason} on each block.
    Returns the number of pairs written (0 on any block).
    """
    source_model_version = context.get("source_model_version") or ""
    snapshot_training_allowed = bool(
        context.get("snapshot_training_allowed")
        or context.get("snapshot_learning_ready", False)
    )

    # Pre-screen: missing authority — harvest_settlement will also check, but
    # we emit the counter here so the caller's log captures the rejection.
    if not str(source_model_version).strip() or not snapshot_training_allowed:
        _cnt_inc(
            "harvester_learning_write_blocked_total",
            labels={"reason": "missing_source_model_version_or_lineage"},
        )
        logger.warning(
            "telemetry_counter event=harvester_learning_write_blocked_total "
            "reason=missing_source_model_version_or_lineage"
        )
        return 0

    # Pre-screen: live/non-training source.
    if not _is_training_forecast_source(source_model_version):
        _cnt_inc(
            "harvester_learning_write_blocked_total",
            labels={"reason": "live_praw_no_training_lineage"},
        )
        logger.warning(
            "telemetry_counter event=harvester_learning_write_blocked_total "
            "reason=live_praw_no_training_lineage"
        )
        return 0

    # Delegate to harvest_settlement which performs the same guards again
    # (defence-in-depth) and the actual DB write.
    return harvest_settlement(
        conn,
        city,
        target_date,
        winning_label,
        all_labels,
        context["p_raw_vector"],
        lead_days=context["lead_days"],
        forecast_issue_time=context["issue_time"],
        forecast_available_at=context["available_at"],
        source_model_version=source_model_version,
        temperature_metric=temperature_metric,
        snapshot_id=context.get("decision_snapshot_id"),
        snapshot_training_allowed=snapshot_training_allowed,
        forecast_source=context.get("forecast_source", ""),
        pair_data_version=context.get("source_model_version"),
        causality_status=context.get("snapshot_causality_status") or "OK",
    )


def run_harvester() -> dict:
    """Run one harvester cycle. Polls for settled markets.

    Returns: harvester counts plus stage2_status / stage2 preflight details.

    Feature flag: ``ZEUS_HARVESTER_LIVE_ENABLED`` must equal ``"1"`` for the
    cycle to actually fetch Gamma + write settlements. Default OFF (DR-33-A
    staged rollout per plan.md §3.1). OFF state short-circuits BEFORE any
    data-plane call; no DB connection is acquired, no HTTP request is made.
    """
    if os.environ.get("ZEUS_HARVESTER_LIVE_ENABLED", "0") != "1":
        logger.info(
            "harvester_live disabled by ZEUS_HARVESTER_LIVE_ENABLED flag (DR-33-A default-OFF); "
            "cycle skipped; no data-plane calls"
        )
        return {
            "status": "disabled_by_feature_flag",
            "disabled_by_flag": True,
            "settled_events": 0,
            "positions_settled": 0,
            "total_pairs": 0,
        }
    # Split connections: trade DB for position/settlement events, shared DB for
    # ensemble snapshots and calibration pairs.
    trade_conn = get_trade_connection()
    shared_conn = get_world_connection()
    portfolio = load_portfolio()

    settled_events = _fetch_settled_events()
    logger.info("Harvester: found %d settled events", len(settled_events))
    stage2_preflight = (
        _preflight_harvester_stage2_db_shape(trade_conn, shared_conn)
        if settled_events
        else {
            "stage2_status": "not_run_no_settled_events",
            "stage2_missing_trade_tables": [],
            "stage2_missing_shared_tables": [],
        }
    )
    stage2_ready = stage2_preflight.get("stage2_status") == "ready"
    if settled_events and not stage2_ready:
        logger.warning(
            "Harvester Stage-2 skipped by DB shape preflight: trade_missing=%s shared_missing=%s",
            stage2_preflight.get("stage2_missing_trade_tables", []),
            stage2_preflight.get("stage2_missing_shared_tables", []),
        )

    total_pairs = 0
    positions_settled = 0
    settlement_records: list[SettlementRecord] = []
    tracker = get_tracker()
    tracker_dirty = False

    for event in settled_events:
        try:
            city = _match_city(
                (event.get("title") or "").lower(),
                event.get("slug", ""),
            )
            if city is None:
                continue

            target_date = _extract_target_date(event)
            if target_date is None:
                continue
            temperature_metric = infer_temperature_metric(
                event.get("title", ""),
                event.get("slug", ""),
                *[
                    str(market.get("question") or market.get("groupItemTitle") or "")
                    for market in event.get("markets", []) or []
                ],
            )

            resolved_market_outcomes = _extract_resolved_market_outcomes(event)
            winning_market_outcomes = [
                outcome for outcome in resolved_market_outcomes if outcome.yes_won
            ]
            if len(winning_market_outcomes) != 1:
                # Exactly one YES-resolved child is required to avoid resolving
                # malformed Gamma payloads into multiple winners.
                if winning_market_outcomes:
                    logger.warning(
                        "harvester_live: skipping %s %s due ambiguous resolved winners=%d slug=%s",
                        city.name,
                        target_date,
                        len(winning_market_outcomes),
                        event.get("slug", ""),
                    )
                continue
            winning_market_outcome = winning_market_outcomes[0]
            pm_bin_lo, pm_bin_hi = (
                winning_market_outcome.range_low,
                winning_market_outcome.range_high,
            )

            # Derive the canonical text-form winning_bin label that downstream
            # learning + position-settlement pipelines (harvest_settlement,
            # _settle_positions) expect as `winning_label`. Without this the
            # broad except-handler below would silently swallow a NameError
            # under flag-ON and the learning pipeline would 100% no-op
            # (code-reviewer P0 finding, Phase 2 verification 2026-04-23).
            winning_label = _canonical_bin_label(pm_bin_lo, pm_bin_hi, city.settlement_unit)
            if winning_label is None:
                logger.warning(
                    "harvester_live: both pm_bin_lo and pm_bin_hi are None after _find_winning_bin; "
                    "skipping %s %s (degenerate bin; should be unreachable)",
                    city.name, target_date,
                )
                continue

            # Look up source-family-correct obs for SettlementSemantics gate.
            obs_row = _lookup_settlement_obs(
                shared_conn,
                city,
                target_date,
                temperature_metric=temperature_metric,
            )
            if obs_row is None:
                # No obs yet; don't write a quarantine row — retry next cycle when obs lands.
                # (Alternative: write QUARANTINED with harvester_live_no_obs; skip for DR-33-A
                # to avoid polluting the table with transient no-obs rows during obs-collector lag.)
                logger.debug(
                    "harvester_live: skipping %s %s — no source-correct obs yet",
                    city.name, target_date,
                )
                continue

            # Canonical-authority write: SettlementSemantics gate + INV-14 + provenance_json.
            _write_settlement_truth(
                shared_conn, city, target_date, pm_bin_lo, pm_bin_hi,
                event_slug=event.get("slug", ""),
                obs_row=obs_row,
                resolved_market_outcomes=resolved_market_outcomes,
                temperature_metric=temperature_metric,
            )

            # Extract all bin labels and use decision-time snapshots for calibration
            all_labels = _extract_all_bin_labels(event)
            learning_contexts = []
            if stage2_ready:
                # shared_conn: _snapshot_contexts_for_market reads ensemble_snapshots (shared)
                # and position_events via query_settlement_events — pass trade_conn for event
                # spine queries, shared_conn for snapshot lookups.
                snapshot_contexts, dropped_rows = _snapshot_contexts_for_market(
                    trade_conn, shared_conn, portfolio, city.name, target_date
                )
                _log_snapshot_context_resolution(
                    trade_conn,
                    city=city.name,
                    target_date=target_date,
                    snapshot_contexts=snapshot_contexts,
                    dropped_rows=dropped_rows,
                )
                learning_contexts = [
                    context
                    for context in snapshot_contexts
                    if context.get("learning_snapshot_ready", False)
                    and context.get("authority_level") != "working_state_fallback"
                ]
            event_pairs = 0
            for context in learning_contexts:
                if context.get("temperature_metric") != temperature_metric:
                    continue
                # T1C: route through maybe_write_learning_pair() which enforces
                # source/lineage authority before calling harvest_settlement().
                event_pairs += maybe_write_learning_pair(
                    shared_conn,
                    city,
                    target_date,
                    winning_label,
                    all_labels,
                    context,
                    temperature_metric,
                )
            total_pairs += event_pairs
            if event_pairs > 0:
                maybe_refit_bucket(shared_conn, city, target_date)

            # Settle held positions in this market
            n_settled = _settle_positions(
                trade_conn,
                portfolio,
                city.name,
                target_date,
                winning_label,
                settlement_records=settlement_records,
                strategy_tracker=tracker,
            )
            positions_settled += n_settled
            if n_settled > 0:
                tracker_dirty = True

        except Exception as e:
            logger.error("Harvester error for event %s: %s",
                         event.get("slug", "?"), e)

    # T1C: settlement record write is now isolated in record_settlement_result().
    # No redeem transitions here; those occur inside _settle_positions() via
    # enqueue_redeem_command() which wraps the settlement_commands.request_redeem call.
    n_written = record_settlement_result(trade_conn, settlement_records, stage2_preflight)
    legacy_settlement_records_skipped = (
        len(settlement_records) - n_written if settlement_records and n_written == 0 else 0
    )

    # DT#1 / INV-17: DB commits FIRST, then JSON exports.
    # harvester has no artifact row, so db_op returns None.
    _portfolio_settled = positions_settled > 0
    _tracker_dirty = tracker_dirty

    def _db_op_trade() -> None:
        trade_conn.commit()
        shared_conn.commit()

    def _export_portfolio_h() -> None:
        if _portfolio_settled:
            save_portfolio(portfolio, source="harvester_settlement")  # Phase 9C B3 audit tag

    def _export_tracker_h() -> None:
        if _tracker_dirty:
            save_tracker(tracker)

    commit_then_export(
        trade_conn,
        db_op=_db_op_trade,
        json_exports=[_export_portfolio_h, _export_tracker_h],
    )

    trade_conn.close()
    shared_conn.close()

    return {
        "settlements_found": len(settled_events),
        "pairs_created": total_pairs,
        "positions_settled": positions_settled,
        "legacy_settlement_records_skipped": legacy_settlement_records_skipped,
        **stage2_preflight,
    }


def _fetch_settled_events() -> list[dict]:
    """Poll Gamma API for recently settled weather markets.

    B045: mid-pagination HTTPError handling (SD-B). Previously any
    httpx.HTTPError broke out of the loop and returned the partial
    page batch as if it were the complete settled-event set.
    Downstream in run_harvester events not yet fetched look
    identical to "no settlement yet," so settlements on page 2+
    would be silently dropped for this cycle's portfolio close
    accounting.

    Contract:
      * first-page (offset == 0) HTTPError is tolerated with a
        warning and an empty return -- indistinguishable from a
        hand-off hour with no settled events, next cycle retries.
      * mid-pagination HTTPError (offset > 0) raises RuntimeError
        so the outer cron wrapper logs a real fault and we do NOT
        commit partial settlement state to the portfolio this cycle.
    """
    events: list[dict] = []
    offset = 0

    while True:
        try:
            resp = httpx.get(f"{GAMMA_BASE}/events", params={
                "closed": "true",
                "limit": 200,
                "offset": offset,
            }, timeout=15.0)
            resp.raise_for_status()
            batch = resp.json()
        except httpx.HTTPError as e:
            if offset == 0:
                logger.warning("Gamma API fetch failed on first page: %s", e)
                break
            raise RuntimeError(
                f"Gamma API pagination failed at offset={offset} after "
                f"{len(events)} events already fetched: {e}. Refusing "
                f"to return partial settled events as complete."
            ) from e

        if not batch:
            break

        # Filter to temperature events only
        for event in batch:
            title = (event.get("title") or "").lower()
            if any(kw in title for kw in ("temperature", "°f", "°c")):
                events.append(event)

        if len(batch) < 200:
            break
        offset += 200

    return events


def _json_list(value) -> Optional[list]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _resolution_price_is_one(value) -> bool:
    try:
        return float(value) == 1.0
    except (TypeError, ValueError):
        return False


def _resolution_price_is_zero(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _extract_resolved_market_outcomes(event: dict) -> list[ResolvedMarketOutcome]:
    """Extract resolved Gamma child identities without requiring tradability.

    Settled Gamma events are no longer tradable, so this intentionally does not
    call the active-market tradability filter from market_scanner. Each returned
    row is keyed by the YES token, because market_events_v2 stores one row per
    temperature bin with the YES token as `token_id`.
    """
    resolved: list[ResolvedMarketOutcome] = []
    for market in event.get("markets", []) or []:
        if market.get("umaResolutionStatus") != "resolved":
            continue

        prices = _json_list(market.get("outcomePrices"))
        outcomes = _json_list(market.get("outcomes"))
        tokens = _json_list(market.get("clobTokenIds"))
        if not (
            isinstance(prices, list)
            and isinstance(outcomes, list)
            and isinstance(tokens, list)
            and len(prices) >= 2
            and len(outcomes) >= 2
            and len(tokens) >= 2
        ):
            continue

        labels = [str(outcomes[0]).strip().lower(), str(outcomes[1]).strip().lower()]
        if labels == ["yes", "no"]:
            yes_index = 0
        elif labels == ["no", "yes"]:
            yes_index = 1
        else:
            continue

        yes_price = prices[yes_index]
        no_price = prices[1 - yes_index]
        if not (
            (_resolution_price_is_one(yes_price) and _resolution_price_is_zero(no_price))
            or (_resolution_price_is_zero(yes_price) and _resolution_price_is_one(no_price))
        ):
            continue

        condition_id = str(
            market.get("conditionId")
            or market.get("condition_id")
            or market.get("id")
            or ""
        ).strip()
        yes_token_id = str(tokens[yes_index]).strip()
        if not condition_id or not yes_token_id:
            continue

        label = market.get("question") or market.get("groupItemTitle", "")
        low, high = _parse_temp_range(label)
        resolved.append(
            ResolvedMarketOutcome(
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                range_label=str(label or ""),
                range_low=low,
                range_high=high,
                yes_won=_resolution_price_is_one(yes_price),
            )
        )
    return resolved


def _find_winning_market_outcome(event: dict) -> Optional[ResolvedMarketOutcome]:
    winners = [outcome for outcome in _extract_resolved_market_outcomes(event) if outcome.yes_won]
    if len(winners) != 1:
        return None
    return winners[0]


def _find_winning_bin(event: dict) -> tuple[Optional[float], Optional[float]]:
    """Determine which bin won from a UMA-resolved settled event.

    Returns: (pm_bin_lo, pm_bin_hi) of the YES-won market, or (None, None).

    Gate (P-D §6.1 + §5.3 non-reversal attestation against R3-09):
      - ``umaResolutionStatus == 'resolved'`` (terminal UMA DVM state)
      - ``outcomes`` map one token to Yes and one token to No (unexpected
        labels → fail closed)
      - the Yes-labeled token has resolution price 1.0 (YES-won per UMA's
        binary vote encoding)

    This is NOT the removed ``outcomePrices >= 0.95`` pre-resolution price
    fallback (R3-09). The removed pattern read prices as a live-trading
    signal on UN-resolved markets. This reads ONLY resolved markets where
    outcomePrices is the UMA oracle vote result encoded as
    ``("1","0")`` or ``("0","1")`` depending on outcome-label ordering.

    See:
      - docs/operations/task_2026-04-23_data_readiness_remediation/evidence/harvester_gamma_probe.md §6.1
      - docs/operations/task_2026-04-23_live_harvester_enablement_dr33/plan.md

    Precedent: existing production code at ``scripts/_build_pm_truth.py:137-139``
    already uses the same ``outcomePrices[0] == "1"`` pattern WITHOUT the
    umaResolutionStatus gate. This function is STRICTER than that precedent.
    """
    winning = _find_winning_market_outcome(event)
    if winning is not None:
        return winning.range_low, winning.range_high
    return None, None


# DR-33-A (2026-04-23): The pre-P-D `_format_range` function was removed; it
# produced sentinel-encoded strings (`-999-15` / `75-999`) that lost shoulder
# semantics and that P-E / DR-33 replaced with the canonical text form
# (`15°C or below` / `75°F or higher`). `_canonical_bin_label` below is the
# sole replacement. No remaining callers of `_format_range` exist — verified
# via `grep -rn "_format_range" src/ tests/ scripts/` returns zero matches.


def _canonical_bin_label(lo: Optional[float], hi: Optional[float], unit: str) -> Optional[str]:
    """Canonical winning_bin label matching P-E reconstruction convention.

    Shoulder cases use English text form (not unicode ≥/≤) because
    ``src/data/market_scanner.py::_parse_temp_range`` uses ``re.search``
    and would silently misparse ``'≥21°C'`` as the POINT bin ``(21.0, 21.0)``.
    Critic-opus C1 (P-E pre-review 2026-04-23) proved this empirically.
    """
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None:
        if lo == hi:
            return f"{int(lo)}°{unit}"
        return f"{int(lo)}-{int(hi)}°{unit}"
    if lo is None and hi is not None:
        return f"{int(hi)}°{unit} or below"
    return f"{int(lo)}°{unit} or higher"


_HARVESTER_LIVE_DATA_VERSION = {
    "wu_icao": "wu_icao_history_v1",
    "hko": "hko_daily_api_v1",
    "noaa": "ogimet_metar_v1",
    "cwa_station": "cwa_no_collector_v0",
}


def _extract_all_bin_labels(event: dict) -> list[str]:
    """Extract all bin labels from a settled event."""
    labels = []
    for market in event.get("markets", []):
        label = market.get("question") or market.get("groupItemTitle", "")
        if label:
            labels.append(label)
    return labels



@capability("settlement_write", lease=True)
@capability("settlement_rebuild", lease=True)
@protects("INV-02", "INV-14")
def _write_settlement_truth(
    conn,
    city: City,
    target_date: str,
    pm_bin_lo: Optional[float],
    pm_bin_hi: Optional[float],
    *,
    event_slug: str = "",
    obs_row: Optional[dict] = None,
    resolved_market_outcomes: Optional[list[ResolvedMarketOutcome]] = None,
    temperature_metric: str | MetricIdentity = "high",
) -> dict:
    """Write canonical-authority settlement truth to settlements table.

    Gate (DR-33-A / P-E canonical pattern):
      1. Look up source-family-correct obs (caller's responsibility; passed via obs_row)
      2. Apply SettlementSemantics.for_city(city).assert_settlement_value(obs.high_temp)
      3. Containment check: rounded value ∈ [pm_bin_lo, pm_bin_hi]?
         - Yes → authority='VERIFIED', settlement_value=rounded, winning_bin=canonical label
         - No → authority='QUARANTINED' with enumerable reason
      4. Populate all 4 INV-14 identity fields + provenance_json with decision_time_snapshot_id

    Does NOT call conn.commit() — caller owns the transaction boundary (P-H
    atomicity consideration; MEMORY L30 with-conn/savepoint collision).

    Returns a dict with {authority, settlement_value, winning_bin, reason}
    for caller to log / aggregate.
    """
    _SOURCE_TYPE_MAP = {"wu_icao": "WU", "hko": "HKO", "noaa": "NOAA", "cwa_station": "CWA"}
    db_source_type = _SOURCE_TYPE_MAP.get(city.settlement_source_type, city.settlement_source_type.upper())
    data_version = _HARVESTER_LIVE_DATA_VERSION.get(
        city.settlement_source_type, "unknown_v0"
    )
    metric_identity = _metric_identity_for(temperature_metric)
    settled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    authority = "QUARANTINED"
    settlement_value: Optional[float] = None
    winning_bin: Optional[str] = None
    reason: Optional[str] = None
    rounding_rule: str = "wmo_half_up"

    observation_value = (
        obs_row.get(metric_identity.observation_field)
        if obs_row is not None
        else None
    )
    if obs_row is None or observation_value is None:
        reason = "harvester_live_no_obs"
    else:
        try:
            sem = SettlementSemantics.for_city(city)
            rounding_rule = sem.rounding_rule
            rounded = sem.assert_settlement_value(
                float(observation_value),
                context=f"harvester_live/{city.name}/{target_date}",
            )
        except SettlementPrecisionError:
            reason = "harvester_live_settlement_precision_error"
            rounded = None

        if rounded is not None and math.isfinite(rounded):
            # Containment check (point/range/shoulder-aware)
            contained = False
            if pm_bin_lo is not None and pm_bin_hi is not None:
                contained = pm_bin_lo <= rounded <= pm_bin_hi
            elif pm_bin_lo is None and pm_bin_hi is not None:
                contained = rounded <= pm_bin_hi
            elif pm_bin_hi is None and pm_bin_lo is not None:
                contained = rounded >= pm_bin_lo
            if contained:
                authority = "VERIFIED"
                settlement_value = rounded
                winning_bin = _canonical_bin_label(pm_bin_lo, pm_bin_hi, city.settlement_unit)
                reason = None
            else:
                # Quarantined — preserve rounded as evidence
                settlement_value = rounded
                reason = "harvester_live_obs_outside_bin"

    provenance = {
        "writer": "harvester_live_dr33",
        "writer_script": "src/execution/harvester.py",
        "source_family": db_source_type,
        "obs_source": obs_row.get("source") if obs_row else None,
        "obs_id": obs_row.get("id") if obs_row else None,
        "decision_time_snapshot_id": obs_row.get("fetched_at") if obs_row else None,
        "rounding_rule": rounding_rule,
        "reconstruction_method": "harvester_live_uma_vote",
        "event_slug": event_slug or None,
        "pm_bin_lo": pm_bin_lo,
        "pm_bin_hi": pm_bin_hi,
        "unit": city.settlement_unit,
        "settlement_source_type": db_source_type,
        "temperature_metric": metric_identity.temperature_metric,
        "physical_quantity": metric_identity.physical_quantity,
        "observation_field": metric_identity.observation_field,
        "data_version": data_version,
        "reconstructed_at": settled_at,
        "audit_ref": "docs/operations/task_2026-04-23_live_harvester_enablement_dr33/plan.md",
    }
    if reason is not None:
        provenance["quarantine_reason"] = reason

    # INSERT OR REPLACE matches P-E's canonical DELETE+INSERT idempotency;
    # REOPEN-2 makes this an upsert per (city, target_date, temperature_metric).
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO settlements (
                city, target_date, market_slug, winning_bin, settlement_value,
                settlement_source, settled_at, authority,
                pm_bin_lo, pm_bin_hi, unit, settlement_source_type,
                temperature_metric, physical_quantity, observation_field,
                data_version, provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city.name, target_date, event_slug or None, winning_bin, settlement_value,
                city.settlement_source, settled_at, authority,
                pm_bin_lo, pm_bin_hi, city.settlement_unit, db_source_type,
                # C6 (2026-04-24): source canonical INV-14 identity from
                # HIGH_LOCALDAY_MAX so settlements align with ensemble/observation
                # rows on physical_quantity. Previously hardcoded
                # "daily_maximum_air_temperature" diverged from canonical
                # "mx2t6_local_calendar_day_max"; any future JOIN that filters on
                # canonical physical_quantity would have silently dropped 100%
                # of harvester-written rows.
                metric_identity.temperature_metric,
                metric_identity.physical_quantity,
                metric_identity.observation_field,
                data_version, json.dumps(provenance, sort_keys=True, default=str),
            ),
        )
        settlement_v2_result = log_settlement_v2(
            conn,
            city=city.name,
            target_date=target_date,
            temperature_metric=metric_identity.temperature_metric,
            market_slug=event_slug or None,
            winning_bin=winning_bin,
            settlement_value=settlement_value,
            settlement_source=city.settlement_source,
            settled_at=settled_at,
            authority=authority,
            provenance=provenance,
            recorded_at=settled_at,
        )
        if authority == "VERIFIED" and resolved_market_outcomes:
            market_events_v2_result = log_market_event_outcomes_v2(
                conn,
                market_slug=event_slug or None,
                city=city.name,
                target_date=target_date,
                temperature_metric=metric_identity.temperature_metric,
                outcomes=[
                    outcome.as_v2_outcome_row()
                    for outcome in resolved_market_outcomes
                ],
            )
        elif resolved_market_outcomes:
            market_events_v2_result = {
                "status": "skipped_unverified_settlement",
                "table": "market_events_v2",
                "authority": authority,
            }
        else:
            market_events_v2_result = {
                "status": "skipped_no_resolved_market_identity",
                "table": "market_events_v2",
            }
        logger.info(
            "harvester_live write: %s %s → authority=%s settlement_value=%s winning_bin=%s reason=%s settlements_v2=%s market_events_v2=%s",
            city.name, target_date, authority, settlement_value, winning_bin, reason,
            settlement_v2_result.get("status"), market_events_v2_result.get("status"),
        )
    except Exception as exc:
        logger.warning(
            "harvester_live write failed for %s %s: %s", city.name, target_date, exc,
        )
        raise

    return {
        "authority": authority,
        "settlement_value": settlement_value,
        "winning_bin": winning_bin,
        "reason": reason,
        "settlement_v2": settlement_v2_result,
        "market_events_v2": market_events_v2_result,
    }


def _extract_target_date(event: dict) -> Optional[str]:
    """Extract target date from event."""
    from src.data.market_scanner import _parse_target_date
    return _parse_target_date(event)


def _snapshot_table_exists(conn, schema: str, table: str) -> bool:
    schema_sql = "main" if schema == "" else schema
    try:
        return conn.execute(
            f"SELECT 1 FROM {schema_sql}.sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def _first_snapshot_table(conn, table: str) -> str:
    if _snapshot_table_exists(conn, "world", table):
        return f"world.{table}"
    if _snapshot_table_exists(conn, "", table):
        return table
    return ""


def _snapshot_table_columns(conn, table: str) -> set[str]:
    if not table:
        return set()
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = "main", table
    try:
        rows = conn.execute(f"PRAGMA {schema}.table_info({name})").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in rows}


def _snapshot_identity_predicates(
    columns: set[str],
    source: str,
    *,
    expected_city: Optional[str] = None,
    expected_target_date: Optional[str] = None,
    expected_temperature_metric: Optional[str] = None,
) -> tuple[str, tuple[str, ...], bool]:
    predicates: list[str] = []
    params: list[str] = []
    if expected_city and "city" in columns:
        predicates.append("city = ?")
        params.append(expected_city)
    if expected_target_date and "target_date" in columns:
        predicates.append("target_date = ?")
        params.append(expected_target_date)
    if expected_temperature_metric:
        if "temperature_metric" in columns:
            predicates.append("temperature_metric = ?")
            params.append(expected_temperature_metric)
        elif source == "ensemble_snapshots_v2":
            return "", (), False
    if not predicates:
        return "", (), True
    return " AND " + " AND ".join(predicates), tuple(params), True


def _snapshot_select_expr(columns: set[str], column: str, fallback_sql: str) -> str:
    return column if column in columns else f"{fallback_sql} AS {column}"


def _snapshot_row_by_id(
    conn,
    snapshot_id: str,
    *,
    expected_city: Optional[str] = None,
    expected_target_date: Optional[str] = None,
    expected_temperature_metric: Optional[str] = None,
):
    for table, source in (
        (_first_snapshot_table(conn, "ensemble_snapshots_v2"), "ensemble_snapshots_v2"),
        (_first_snapshot_table(conn, "ensemble_snapshots"), "ensemble_snapshots"),
    ):
        if not table:
            continue
        columns = _snapshot_table_columns(conn, table)
        identity_sql, identity_params, identity_supported = _snapshot_identity_predicates(
            columns,
            source,
            expected_city=expected_city,
            expected_target_date=expected_target_date,
            expected_temperature_metric=expected_temperature_metric,
        )
        if not identity_supported:
            continue
        training_expr = _snapshot_select_expr(columns, "training_allowed", "NULL")
        causality_expr = _snapshot_select_expr(columns, "causality_status", "NULL")
        metric_expr = _snapshot_select_expr(columns, "temperature_metric", "'high'")
        row = conn.execute(
            f"""
            SELECT p_raw_json, lead_hours, issue_time, available_at,
                   model_version, data_version, snapshot_id,
                   {training_expr},
                   {causality_expr},
                   {metric_expr},
                   ? AS snapshot_source
            FROM {table}
            WHERE snapshot_id = ?
              {identity_sql}
            LIMIT 1
            """,
            (source, snapshot_id, *identity_params),
        ).fetchone()
        if row is not None:
            return row
    return None


def _latest_snapshot_row(
    conn,
    city: str,
    target_date: str,
    *,
    temperature_metric: Optional[str] = None,
):
    for table, source in (
        (_first_snapshot_table(conn, "ensemble_snapshots_v2"), "ensemble_snapshots_v2"),
        (_first_snapshot_table(conn, "ensemble_snapshots"), "ensemble_snapshots"),
    ):
        if not table:
            continue
        columns = _snapshot_table_columns(conn, table)
        identity_sql, identity_params, identity_supported = _snapshot_identity_predicates(
            columns,
            source,
            expected_temperature_metric=temperature_metric,
        )
        if not identity_supported:
            continue
        training_expr = _snapshot_select_expr(columns, "training_allowed", "NULL")
        causality_expr = _snapshot_select_expr(columns, "causality_status", "NULL")
        metric_expr = _snapshot_select_expr(columns, "temperature_metric", "'high'")
        row = conn.execute(
            f"""
            SELECT p_raw_json, lead_hours, issue_time, available_at,
                   model_version, data_version, snapshot_id,
                   {training_expr},
                   {causality_expr},
                   {metric_expr},
                   ? AS snapshot_source
            FROM {table}
            WHERE city = ? AND target_date = ? AND p_raw_json IS NOT NULL
              {identity_sql}
            ORDER BY datetime(fetch_time) DESC
            LIMIT 1
            """,
            (source, city, target_date, *identity_params),
        ).fetchone()
        if row is not None:
            return row
    return None


def _get_stored_p_raw(
    conn,
    city: str,
    target_date: str,
    snapshot_id: Optional[str] = None,
    temperature_metric: Optional[str] = None,
) -> Optional[list[float]]:
    """Get stored P_raw vector from canonical v2 snapshot, then legacy compatibility."""
    row = (
        _snapshot_row_by_id(
            conn,
            snapshot_id,
            expected_city=city,
            expected_target_date=target_date,
            expected_temperature_metric=temperature_metric,
        )
        if snapshot_id
        else _latest_snapshot_row(
            conn,
            city,
            target_date,
            temperature_metric=temperature_metric,
        )
    )

    if row and row["p_raw_json"]:
        try:
            return json.loads(row["p_raw_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def get_snapshot_p_raw(
    conn,
    snapshot_id: str,
    *,
    expected_city: Optional[str] = None,
    expected_target_date: Optional[str] = None,
    expected_temperature_metric: Optional[str] = None,
) -> Optional[list[float]]:
    """Get the decision-time P_raw vector for a specific snapshot."""
    row = _snapshot_row_by_id(
        conn,
        snapshot_id,
        expected_city=expected_city,
        expected_target_date=expected_target_date,
        expected_temperature_metric=expected_temperature_metric,
    )

    if row and row["p_raw_json"]:
        try:
            return json.loads(row["p_raw_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def get_snapshot_context(
    conn,
    snapshot_id: str,
    *,
    expected_city: Optional[str] = None,
    expected_target_date: Optional[str] = None,
    expected_temperature_metric: Optional[str] = None,
) -> Optional[dict]:
    """Get the decision-time snapshot payload needed for calibration capture."""
    row = _snapshot_row_by_id(
        conn,
        snapshot_id,
        expected_city=expected_city,
        expected_target_date=expected_target_date,
        expected_temperature_metric=expected_temperature_metric,
    )
    if row is None or not row["p_raw_json"]:
        return None
    source_model_version = row["data_version"] or row["model_version"]
    if not source_model_version:
        return None
    issue_time = row["issue_time"]
    training_allowed = row["training_allowed"]
    learning_snapshot_ready = bool(issue_time) and training_allowed != 0
    if not issue_time:
        learning_blocked_reason = "missing_forecast_issue_time"
    elif training_allowed == 0:
        learning_blocked_reason = "snapshot_training_not_allowed"
    else:
        learning_blocked_reason = ""
    try:
        return {
            "p_raw_vector": json.loads(row["p_raw_json"]),
            "lead_days": float(row["lead_hours"]) / 24.0,
            "issue_time": issue_time,
            "available_at": row["available_at"],
            "source_model_version": source_model_version,
            "temperature_metric": str(row["temperature_metric"] or "high"),
            "forecast_source": _forecast_source_from_version(source_model_version),
            "snapshot_learning_ready": learning_snapshot_ready,
            "learning_blocked_reason": learning_blocked_reason,
            "snapshot_source": row["snapshot_source"],
            "snapshot_causality_status": row["causality_status"],
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _snapshot_contexts_for_market(
    trade_conn,
    shared_conn,
    portfolio: PortfolioState,
    city: str,
    target_date: str,
) -> tuple[list[dict], list[dict]]:
    """Resolve decision-time snapshots, preferring durable settlement truth over open portfolio.

    trade_conn: for event-spine queries (position_events, decision_log).
    shared_conn: for snapshot lookups (ensemble_snapshots).
    """
    stage_events = query_settlement_events(
        trade_conn,
        limit=200,
        city=city,
        target_date=target_date,
    )
    authoritative_rows = query_authoritative_settlement_rows(
        trade_conn,
        limit=200,
        city=city,
        target_date=target_date,
    )
    contexts, dropped_rows = _snapshot_contexts_from_rows(trade_conn, shared_conn, authoritative_rows)
    if contexts:
        for context in contexts:
            context["partial_context_resolution"] = bool(dropped_rows)
        return contexts, dropped_rows

    legacy_rows: list[dict] = []
    if authoritative_rows and authoritative_rows[0].get("source") != "decision_log":
        legacy_rows = query_legacy_settlement_records(
            trade_conn,
            limit=200,
            city=city,
            target_date=target_date,
        )
        contexts, dropped_rows = _snapshot_contexts_from_rows(trade_conn, shared_conn, legacy_rows)
        if contexts:
            for context in contexts:
                context["partial_context_resolution"] = bool(dropped_rows)
            return contexts, dropped_rows

    fallback_reason = "no_durable_settlement_snapshot"
    if stage_events and not authoritative_rows:
        fallback_reason = "durable_rows_malformed"
    elif authoritative_rows:
        fallback_reason = "authoritative_rows_missing_snapshot_context"
    elif legacy_rows:
        fallback_reason = "legacy_rows_missing_snapshot_context"

    snapshot_refs: list[tuple[str, str]] = []
    for pos in portfolio.positions:
        if pos.city == city and pos.target_date == target_date and pos.decision_snapshot_id:
            metric = str(getattr(pos, "temperature_metric", "") or "")
            ref = (pos.decision_snapshot_id, metric)
            if ref not in snapshot_refs:
                snapshot_refs.append(ref)

    fallback_contexts: list[dict] = []
    for snapshot_id, temperature_metric in snapshot_refs:
        context = get_snapshot_context(
            shared_conn,
            snapshot_id,
            expected_city=city,
            expected_target_date=target_date,
            expected_temperature_metric=temperature_metric or None,
        )
        if context is None:
            continue
        blocked_reason = str(context.get("learning_blocked_reason") or "")
        fallback_contexts.append({
            **context,
            "decision_snapshot_id": snapshot_id,
            "source": "portfolio_open_fallback",
            "authority_level": "working_state_fallback",
            "is_degraded": True,
            "degraded_reason": "; ".join(
                reason for reason in (fallback_reason, blocked_reason) if reason
            ),
            "learning_snapshot_ready": False,
        })
    return fallback_contexts, dropped_rows


def _snapshot_contexts_from_rows(trade_conn, shared_conn, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    contexts: list[dict] = []
    dropped_rows: list[dict] = []
    seen_snapshot_ids: set[str] = set()
    for row in rows:
        snapshot_id = str(row.get("decision_snapshot_id") or "")
        if not snapshot_id or snapshot_id in seen_snapshot_ids:
            if not snapshot_id:
                dropped_rows.append({
                    "source": str(row.get("source") or "unknown"),
                    "authority_level": str(row.get("authority_level") or "unknown"),
                    "reason": "missing_decision_snapshot_id",
                    "degraded_reason": str(row.get("degraded_reason") or ""),
                })
            continue
        context = get_snapshot_context(
            shared_conn,
            snapshot_id,
            expected_city=str(row.get("city") or "") or None,
            expected_target_date=str(row.get("target_date") or "") or None,
            expected_temperature_metric=str(row.get("temperature_metric") or "") or None,
        )
        if context is None:
            dropped_rows.append({
                "source": str(row.get("source") or "unknown"),
                "authority_level": str(row.get("authority_level") or "unknown"),
                "reason": "missing_snapshot_context",
                "decision_snapshot_id": snapshot_id,
                "degraded_reason": str(row.get("degraded_reason") or ""),
            })
            continue
        seen_snapshot_ids.add(snapshot_id)
        row_ready = bool(row.get("learning_snapshot_ready", bool(snapshot_id)))
        snapshot_ready = bool(context.get("snapshot_learning_ready", True))
        blocked_reason = str(context.get("learning_blocked_reason") or "")
        degraded_reason = str(row.get("degraded_reason") or "")
        if blocked_reason:
            degraded_reason = "; ".join(
                reason for reason in (degraded_reason, blocked_reason) if reason
            )
        contexts.append({
            **context,
            "decision_snapshot_id": snapshot_id,
            "temperature_metric": str(context.get("temperature_metric") or row.get("temperature_metric") or "high"),
            "source": str(row.get("source") or "unknown"),
            "authority_level": str(row.get("authority_level") or "unknown"),
            "is_degraded": bool(row.get("is_degraded", False)) or bool(blocked_reason),
            "degraded_reason": degraded_reason,
            "learning_snapshot_ready": row_ready and snapshot_ready,
        })
    return contexts, dropped_rows


def _log_snapshot_context_resolution(
    conn,
    *,
    city: str,
    target_date: str,
    snapshot_contexts: list[dict],
    dropped_rows: list[dict] | None = None,
) -> None:
    """Audit which truth surface fed settlement learning for a market."""
    log_event(
        conn,
        "SETTLEMENT_SNAPSHOT_SOURCE",
        None,
        {
            "city": city,
            "target_date": target_date,
            "context_count": len(snapshot_contexts),
            "partial_context_resolution": bool(dropped_rows),
            "dropped_context_count": len(dropped_rows or []),
            "contexts": [
                {
                    "decision_snapshot_id": context.get("decision_snapshot_id", ""),
                    "source": context.get("source", "unknown"),
                    "authority_level": context.get("authority_level", "unknown"),
                    "is_degraded": bool(context.get("is_degraded", False)),
                    "degraded_reason": context.get("degraded_reason", ""),
                    "learning_snapshot_ready": bool(context.get("learning_snapshot_ready", False)),
                }
                for context in snapshot_contexts
            ],
            "dropped_rows": list(dropped_rows or []),
        },
    )


def harvest_settlement(
    conn,
    city: City,
    target_date: str,
    winning_bin_label: str,
    bin_labels: list[str],
    p_raw_vector: Optional[list[float]] = None,
    lead_days: float = 3.0,
    forecast_issue_time: Optional[str] = None,
    forecast_available_at: Optional[str] = None,
    source_model_version: Optional[str] = None,
    settlement_value: Optional[float] = None,
    bias_corrected: Optional[bool] = None,
    temperature_metric: str = "high",
    snapshot_id: object = None,
    snapshot_training_allowed: Optional[bool] = None,
    forecast_source: Optional[str] = None,
    pair_data_version: Optional[str] = None,
    causality_status: str = "OK",
) -> int:
    """Generate calibration pairs from a settled market.

    Creates one pair per bin. Winning bin gets outcome=1, others get outcome=0.
    Returns: number of pairs created.
    """
    season = season_from_date(target_date, lat=city.lat)
    now = forecast_available_at or datetime.now(timezone.utc).isoformat()
    issue_time = str(forecast_issue_time or "").strip()
    # Guard: missing forecast_issue_time when p_raw is present — preserve existing
    # behaviour and emit counter (T1C adds the counter; the return-0 already existed).
    if p_raw_vector and not issue_time:
        logger.warning(
            "Skipping calibration harvest for %s %s: forecast_issue_time is missing",
            city.name,
            target_date,
        )
        _cnt_inc(
            "harvester_learning_write_blocked_total",
            labels={"reason": "missing_forecast_issue_time"},
        )
        logger.warning(
            "telemetry_counter event=harvester_learning_write_blocked_total "
            "reason=missing_forecast_issue_time"
        )
        return 0
    if bias_corrected is None:
        try:
            from src.config import settings
            bias_corrected = settings.bias_correction_enabled
        except Exception:
            bias_corrected = False
    if p_raw_vector and not source_model_version:
        raise ValueError(
            "source_model_version is required when harvesting calibration pairs"
        )
    metric_identity = _metric_identity_for(
        getattr(city, "temperature_metric", temperature_metric)
        if getattr(city, "temperature_metric", temperature_metric) == "low" or temperature_metric == "low"
        else temperature_metric
    )
    resolved_forecast_source = forecast_source or _forecast_source_from_version(source_model_version)
    resolved_pair_data_version = (
        str(pair_data_version).strip()
        if pair_data_version not in (None, "")
        else (
            metric_identity.data_version
            if _is_training_forecast_source(source_model_version)
            else str(source_model_version or "").strip()
        )
    )
    if not resolved_pair_data_version:
        resolved_pair_data_version = metric_identity.data_version
    training_requested = (
        bool(snapshot_training_allowed)
        if snapshot_training_allowed is not None
        else _is_training_forecast_source(source_model_version)
    )
    resolved_snapshot_id = _coerce_snapshot_id(snapshot_id)

    # Phase 2.6 (2026-05-04): derive cycle/source_id/horizon_profile from the
    # forecast issue_time + data_version so calibration_pairs_v2 rows land in
    # the correct stratified bucket. Falls back to None when issue_time is
    # missing or data_version doesn't resolve to a registered source_family —
    # the writer's schema-default branch handles that case.
    _phase2_cycle: Optional[str] = None
    _phase2_source_id_field: Optional[str] = None
    _phase2_horizon_profile: Optional[str] = None
    try:
        if isinstance(issue_time, str) and len(issue_time) >= 13:
            _phase2_cycle = issue_time[11:13]
        from src.calibration.forecast_calibration_domain import (
            derive_source_id_from_data_version,
        )
        _src_id = derive_source_id_from_data_version(resolved_pair_data_version)
        if _src_id is not None:
            _phase2_source_id_field = _src_id
        if _phase2_cycle is not None:
            _phase2_horizon_profile = (
                "full" if _phase2_cycle in ("00", "12") else "short"
            )
    except (ImportError, AttributeError, TypeError, ValueError) as _exc:
        # Phase 2.6 hardening (2026-05-04, critic-opus MINOR 10): explicit
        # exception list rather than bare Exception so a real bug doesn't
        # get swallowed silently. We log+continue (writer's schema-default
        # branch produces well-formed rows from None args). If a future
        # exception type needs to fall through here, add it explicitly so
        # the maintainer is forced to think about whether silent fallback
        # is right for that case.
        logger.warning(
            "Phase 2.6 stratification derivation failed for %s/%s; falling "
            "back to schema defaults: %s: %s",
            city.name, target_date, type(_exc).__name__, _exc,
        )
        _phase2_cycle = None
        _phase2_source_id_field = None
        _phase2_horizon_profile = None

    count = 0
    for i, label in enumerate(bin_labels):
        outcome = 1 if label == winning_bin_label else 0
        p_raw = p_raw_vector[i] if p_raw_vector and i < len(p_raw_vector) else None

        if p_raw is None:
            continue

        dgid = compute_id(
            city.name,
            target_date,
            issue_time,
            source_model_version or "",
        )
        # C5 routes both tracks through add_calibration_pair_v2. The row also
        # preserves forecast-source lineage so runtime/fallback p_raw cannot be
        # rebranded as canonical TIGGE training data.
        add_calibration_pair_v2(
            conn, city=city.name, target_date=target_date,
            range_label=label, p_raw=p_raw, outcome=outcome,
            lead_days=lead_days, season=season, cluster=city.cluster,
            forecast_available_at=now,
            settlement_value=settlement_value,
            decision_group_id=dgid,
            bias_corrected=bool(bias_corrected),
            city_obj=city,
            metric_identity=metric_identity,
            data_version=resolved_pair_data_version,
            source=resolved_forecast_source,
            training_allowed=training_requested,
            causality_status=causality_status or "OK",
            snapshot_id=resolved_snapshot_id,
            cycle=_phase2_cycle,
            source_id=_phase2_source_id_field,
            horizon_profile=_phase2_horizon_profile,
        )
        count += 1

    logger.info("Harvested %d pairs for %s %s (winner: %s)",
                count, city.name, target_date, winning_bin_label)
    if count:
        group = build_decision_group_for_key(
            conn,
            city=city.name,
            target_date=target_date,
            forecast_available_at=now,
            lead_days=lead_days,
        )
        if group is not None:
            write_decision_groups(
                conn,
                [group],
                recorded_at=datetime.now(timezone.utc).isoformat(),
                update_pair_rows=True,
            )
    return count


def _settle_positions(
    conn, portfolio: PortfolioState,
    city: str, target_date: str, winning_label: str,
    settlement_records: Optional[list[SettlementRecord]] = None,
    strategy_tracker=None,
) -> int:
    """Settle held positions that match this market. Log P&L."""
    settled = 0
    _canonical_exit = _get_canonical_exit_flag()
    settlement_records = settlement_records if settlement_records is not None else []

    # P6: Load the authoritative phase from position_current for each trade in
    # this market. Positions already in a terminal DB phase are excluded before
    # any other logic, making settlement idempotent even when the in-memory
    # portfolio snapshot is stale (e.g. loaded from a JSON fallback cache).
    # Positions without a position_current row (pre-canonical history) are NOT
    # excluded u2014 they fall through to the existing skip logic unchanged.
    try:
        pc_rows = conn.execute(
            "SELECT trade_id, phase FROM position_current WHERE city = ? AND target_date = ?",
            (city, target_date),
        ).fetchall()
        pc_phase_by_id: dict[str, str] | None = {
            (row["trade_id"] if hasattr(row, "keys") else row[0]):
            (row["phase"] if hasattr(row, "keys") else row[1])
            for row in pc_rows
        }
    except Exception as exc:
        logger.warning(
            "position_current query failed for %s %s, using portfolio-only skip logic: %s",
            city, target_date, exc,
        )
        pc_phase_by_id = None

    for pos in list(portfolio.positions):
        if pos.city != city or pos.target_date != target_date:
            continue
        try:
            entry_provenance = pos.entry_method or pos.selected_method or "unknown"
        except AttributeError:
            entry_provenance = "unknown"
        if entry_provenance == "unknown":
            logger.debug(
                "Settlement P&L for %s has unknown entry provenance",
                pos.trade_id,
            )

        # P6 iterator-level dedup: skip positions whose DB phase is already
        # terminal even when the in-memory snapshot shows otherwise.
        if pc_phase_by_id is not None:
            _db_phase = pc_phase_by_id.get(pos.trade_id)
            if _db_phase in _TERMINAL_PHASES:
                logger.info(
                    "Skipping settlement for %s: position_current.phase=%s already terminal",
                    pos.trade_id, _db_phase,
                )
                continue

        state_name = getattr(pos.state, "value", getattr(pos, "state", ""))
        exit_state = getattr(pos, "exit_state", "")
        chain_state = getattr(pos, "chain_state", "")
        pending_exit_at_settlement = state_name == "pending_exit"
        if (
            state_name in {"pending_tracked", "quarantined", "admin_closed", "voided", "settled"}
            or chain_state in {"quarantined", "quarantine_expired"}
            or (
                chain_state == "exit_pending_missing"
                and not pending_exit_at_settlement
                and exit_state != "backoff_exhausted"
            )
            or (
                not pending_exit_at_settlement
                and exit_state in {"exit_intent", "sell_placed", "sell_pending", "retry_pending"}
            )
        ):
            logger.info("Skipping settlement for %s: runtime state still non-terminal for settlement", pos.trade_id)
            continue
        if pos.direction not in {"buy_yes", "buy_no"}:
            logger.warning(
                "Skipping settlement P&L for %s: unknown direction %r",
                pos.trade_id,
                pos.direction,
            )
            closed = void_position(portfolio, pos.trade_id, "SETTLED_UNKNOWN_DIRECTION")
            if closed is not None and strategy_tracker is not None:
                strategy_tracker.record_exit(closed)
            settled += 1
            continue

        # Determine P&L — correct formula: shares × exit_price - cost_basis
        # Legacy-predecessor comparison found the old formula underestimated winning P&L
        won = pos.bin_label == winning_label
        try:
            shares, settlement_cost_basis = _settlement_economics_for_position(pos)
        except ValueError as exc:
            logger.warning("Skipping settlement P&L for %s: %s", pos.trade_id, exc)
            continue
        exited_at_before_settlement = getattr(pos, "last_exit_at", "")
        if pos.direction == "buy_yes":
            exit_price = 1.0 if won else 0.0
        else:
            exit_price = 1.0 if not won else 0.0
        phase_before = _canonical_phase_before_for_settlement(pos)
        settlement_price = exit_price
        if getattr(pos, "state", "") == "economically_closed":
            settlement_price = getattr(pos, "exit_price", exit_price)

        # F1: Route settlement close through exit_lifecycle when flag is on
        if _canonical_exit:
            from src.execution.exit_lifecycle import mark_settled
            closed = mark_settled(portfolio, pos.trade_id, settlement_price, "SETTLEMENT")
        else:
            closed = compute_settlement_close(portfolio, pos.trade_id, settlement_price, "SETTLEMENT")
        pnl = closed.pnl if closed is not None else round(shares * exit_price - settlement_cost_basis, 2)
        outcome = 1 if exit_price > 0 else 0

        if closed is not None:
            settlement_records.append(SettlementRecord(
                trade_id=closed.trade_id,
                city=city,
                target_date=target_date,
                range_label=closed.bin_label,
                direction=closed.direction,
                p_posterior=closed.p_posterior,
                outcome=outcome,
                pnl=round(pnl, 2),
                decision_snapshot_id=closed.decision_snapshot_id,
                edge_source=closed.edge_source,
                strategy=closed.strategy,
                settled_at=closed.last_exit_at,
            ))
            if strategy_tracker is not None:
                strategy_tracker.record_settlement(closed)

        # T2G-NO-INLINE-REQUEST-REDEEM: all redeem-state transitions route through
        # enqueue_redeem_command (the single auditable entry point per T1C +
        # T2G-REDEEM-STATE-TRANSITION-AUDITABLE). The prior inline
        # 'from src.execution.settlement_commands import request_redeem' block
        # is removed here; request_redeem is only called inside
        # enqueue_redeem_command's body (src/execution/harvester.py:~499).
        if exit_price > 0 and pos.condition_id:
            redeem_token_id = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
            enqueue_redeem_command(
                conn,
                condition_id=pos.condition_id,
                payout_asset="pUSD",
                market_id=getattr(pos, "market_id", "") or pos.condition_id,
                pusd_amount_micro=int(round(shares * 1_000_000)),
                token_amounts={redeem_token_id: shares} if redeem_token_id else {},
                trade_id=pos.trade_id,
            )

        # T2-C: Add settled token to ignored set (don't resurrect in reconciliation)
        token_id = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        if token_id and token_id not in portfolio.ignored_tokens:
            suppression_result = record_token_suppression(
                conn,
                token_id=token_id,
                condition_id=getattr(pos, "condition_id", ""),
                suppression_reason="settled_position",
                source_module="src.execution.harvester",
                evidence={"trade_id": pos.trade_id, "target_date": target_date},
            )
            if suppression_result.get("status") == "written":
                portfolio.ignored_tokens.append(token_id)
            else:
                logger.warning(
                    "Settlement token suppression was not persisted for %s: %s",
                    pos.trade_id,
                    suppression_result,
                )

        log_event(conn, "SETTLEMENT", pos.trade_id, {
            "city": city, "target_date": target_date,
            "winning_bin": winning_label, "position_bin": pos.bin_label,
            "direction": pos.direction, "won": won,
            "position_won": bool(exit_price > 0),
            "pnl": round(pnl, 2), "entry_price": pos.entry_price,
            "exit_price": getattr(closed or pos, "exit_price", settlement_price),
            "p_posterior": pos.p_posterior,
            "outcome": outcome,
            "exit_reason": getattr(closed or pos, "exit_reason", "SETTLEMENT"),
            "edge_source": pos.edge_source,
            "strategy": pos.strategy,
            "decision_snapshot_id": pos.decision_snapshot_id,
        })
        log_settlement_event(
            conn,
            pos,
            winning_bin=winning_label,
            won=won,
            outcome=outcome,
            exited_at_override=exited_at_before_settlement or None,
        )
        _dual_write_canonical_settlement_if_available(
            conn,
            closed or pos,
            winning_bin=winning_label,
            won=won,
            outcome=outcome,
            phase_before=phase_before,
        )

        # SD-1: write settlement outcome back to trade_decisions
        try:
            rtid = getattr(pos, 'trade_id', '')
            if rtid:
                conn.execute(
                    """UPDATE trade_decisions
                       SET settlement_edge_usd = ?,
                           exit_reason = COALESCE(exit_reason, 'SETTLEMENT'),
                           status = CASE WHEN status IN ('entered', 'day0_window') THEN 'settled' ELSE status END
                       WHERE runtime_trade_id = ?
                         AND status NOT IN ('exited', 'unresolved_ghost', 'settled')""",
                    (round(pnl, 4), rtid),
                )
                conn.commit()
        except Exception as exc:
            logger.warning('SD-1: failed to update trade_decisions for %s: %s', pos.trade_id, exc)

        settled += 1

        logger.info("SETTLED %s: %s %s %s — PnL=$%.2f",
                     pos.trade_id, "WON" if won else "LOST",
                     pos.direction, pos.bin_label, pnl)

    return settled
