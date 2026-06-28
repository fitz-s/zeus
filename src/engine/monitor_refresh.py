# Created: prior
# Last audited: 2026-06-03
# Authority basis: D2 bias-family unify / wiring verdict 2026-06-03
#   D2 (2026-06-03): _resolve_unified_exit_bias_native + flag-gated bias-shift +
#   identity-Platt on the exit/monitor p_raw sites so EXIT belief matches ENTRY belief.
"""Monitor refresh: recompute fresh probability for held positions.

Blueprint v2 §7 Layer 1: recompute the held-side probability.

PRIMARY AUTHORITY (corrected 2026-06-17): Day0 absorbing hard facts dominate
model belief when qualified; otherwise ``monitor_probability_refresh`` reads the
multi-model fused posterior ``forecast_posteriors`` (via
``position_belief.load_replacement_belief``, sourced from ``raw_model_forecasts``)
as the SAME source family as the entry decision. The legacy ENS member-counting
path is retained as diagnostic telemetry only and must not substitute for a
stale/missing replacement belief on non-day0 positions. The day0 observation
lane remains a separate settlement-day authority.
Uses full p_raw_vector with MC instrument noise (not simplified _estimate_bin_p_raw).
"""

import logging
import sqlite3
import copy
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from src.calibration.manager import get_calibrator, season_from_date
from src.calibration.platt import calibrate_and_normalize
from src.config import (
    cities_by_name,
    day0_n_mc,
    edge_n_bootstrap,
    ensemble_member_count,
    ensemble_n_mc,
    ensemble_primary_model,
    entry_forecast_config,
    settings,
)
from src.contracts import (
    EntryMethod,
    recompute_native_probability,
    SettlementSemantics,
)
from src.contracts.day0_observation_context import BoundClassification, classify_bound
from src.contracts.exceptions import ObservationUnavailableError
from src.contracts.probability_arithmetic import one_minus
from src.contracts.settlement_semantics import round_wmo_half_up_value
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.executable_forecast_reader import read_executable_forecast
from src.data.forecast_fetch_plan import data_version_for_track, track_for_metric
from src.data.forecast_source_registry import calibration_source_id_for_lookup
from src.data.market_scanner import _parse_temp_range, get_last_scan_authority, get_sibling_outcomes
from src.data.observation_client import Day0ObservationContext, get_current_observation
from src.data.polymarket_client import PolymarketClient
from src.engine.evaluator import (
    DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE,
    _day0_observation_field,
    _day0_observation_quality_rejection_reason,
    _day0_observation_source_rejection_reason,
    _finite_day0_observation_float,
)
from src.engine.time_context import lead_days_to_date_start
from src.signal.day0_router import Day0Router, Day0SignalInputs
from src.signal.day0_window import remaining_member_extrema_for_day0
from src.signal.ensemble_signal import EnsembleSignal, p_raw_vector_from_maxes
from src.observability.counters import increment as _cnt_inc
from src.state.chain_reconciliation import resolve_position_metric
from src.state.portfolio import Position
from src.strategy.market_fusion import (
    MODEL_ONLY_POSTERIOR_MODE,
    compute_alpha,
    compute_posterior,
    vwmp,
)
from src.types import Bin
from src.types.market import BinTopologyError, validate_bin_topology
from src.types.metric_identity import MetricIdentity
from src.types.temperature import TemperatureDelta
from src.calibration.ens_bias_repo import read_bias_model

logger = logging.getLogger(__name__)
_MONITOR_PROBABILITY_FRESH_ATTR = "_monitor_probability_is_fresh"
_WHALE_TOXICITY_PRICE_MARGIN = 0.05
_WHALE_TOXICITY_SEVERE_PRICE_MARGIN = 0.15
_WHALE_TOXICITY_LOOKBACK_HOURS = 1.0
_WHALE_TOXICITY_MIN_NOTIONAL_USD = 25.0
_NOWCAST_PERSISTENT_FAILURE_THRESHOLD = 3
_DAY0_LOW_EXTREME_AUTHORITY_HOURS = 6.0
SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT = "day0_absorbing_hard_fact"
SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW = "day0_observation_remaining_window"
_DAY0_STALE_OBSERVATION_REJECTION_PREFIX = (
    "Day0 observation is stale for executable probability generation:"
)
_nowcast_consecutive_write_failures = 0


@dataclass(frozen=True)
class HeldTokenMonitorQuote:
    """Held-token executable quote surface for monitor/exit economics."""

    token_id: str
    best_bid: float
    best_ask: float | None
    bid_size: float
    ask_size: float
    diagnostic_market_price: float
    source_timestamp: str


def _compute_divergence_score(p_posterior: float, p_market: float, *, available: bool) -> float:
    """Adverse-only divergence: positive edge is entry signal, not exit signal.

    Non-finite inputs propagate as NaN so stale or missing quotes surface loudly
    rather than recording a spurious 0.0 (max() would silently swallow NaN).
    """
    if not available:
        return float("nan")
    if not (np.isfinite(p_posterior) and np.isfinite(p_market)):
        return float("nan")
    return max(0.0, p_market - p_posterior)


def _model_only_native_posterior(p_native: float) -> float:
    """Return held-side payoff belief without using executable quote as prior."""
    p = float(p_native)
    if not np.isfinite(p) or not 0.0 <= p <= 1.0:
        raise ValueError(f"native monitor probability must be in [0, 1], got {p!r}")
    return p


def _held_side_probability_from_yes_bin_probability(p_yes_bin: float, direction: str) -> float:
    """Convert a YES-bin point probability into the held-side outcome space."""
    p_yes = _model_only_native_posterior(p_yes_bin)
    direction_value = getattr(direction, "value", direction)
    if str(direction_value) == "buy_no":
        return _model_only_native_posterior(one_minus(p_yes))
    return p_yes


def _day0_remaining_window_belief_validations(metric: str | None = None) -> list[str]:
    metric_part = f";metric={metric}" if metric else ""
    return [
        "day0_observation_remaining_window",
        (
            "belief_source=day0_observation_remaining_window"
            f";kind=probabilistic_remaining_window{metric_part}"
            ";posterior_mode=model_only_v1"
        ),
        "market_quote_prior_excluded:day0_observation_remaining_window",
        "alpha_blend_inapplicable:day0_observation_remaining_window",
    ]


def _stamp_day0_remaining_window_belief(
    position: Position,
    *,
    metric: str | None = None,
) -> None:
    setattr(position, "selected_method", SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW)
    for validation in _day0_remaining_window_belief_validations(metric):
        _append_monitor_validation(position, validation)


@dataclass(frozen=True)
class MonitorOneCalibratorQ:
    q_vector: np.ndarray
    q_source: str
    bootstrap_probability_sampler: object | None
    # PARITY PROVENANCE (P1 review finding 2026-06-09): settlement sigma-floor coherence.
    # settlement_sigma_floor_applied — True when the empirical settlement σ-floor was
    #   looked up and found for this (city, season, metric) cell AND actually widened sigma.
    #   False when floor_enabled=False, or floor cell absent, or not applied (model σ already
    #   >= floor).
    # settlement_sigma_floor_required — mirror of the edli_settlement_sigma_floor_required
    #   config flag at monitor time; recorded for audit.
    # floor_missing_reason — non-None when floor_enabled=True but the cell lookup failed.
    #   The caller uses this to detect entry-parity violations (entry had floor, monitor does
    #   not → mark NOT FRESH; same fail-closed semantics as the day0 panic-sell hold fix).
    settlement_sigma_floor_applied: bool = False
    settlement_sigma_floor_required: bool = False
    floor_missing_reason: str | None = None


def _monitor_emos_regime_enabled() -> bool:
    try:
        return bool(settings["edli"].get("edli_emos_sole_calibrator_enabled", False))
    except Exception:
        return False


def _monitor_emos_season(target_d: date) -> str:
    month = int(target_d.month)
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def _monitor_normal_bootstrap_sampler(mu_native: float, sigma_native: float):
    def _sampler(analysis, n_members):
        draws = analysis._rng.normal(float(mu_native), float(sigma_native), int(n_members))
        measured = analysis._settle(draws)
        vec = np.array(
            [analysis._bin_probability(measured, bb) for bb in analysis.bins],
            dtype=float,
        )
        if not np.all(np.isfinite(vec)):
            return np.asarray(analysis.p_cal, dtype=float)
        total = float(vec.sum())
        if total <= 0.0:
            return np.asarray(analysis.p_cal, dtype=float)
        return vec / total

    return _sampler


def _probe_monitor_settlement_floor(
    city_name: str,
    season: str,
    metric: str,
) -> tuple[bool, str | None]:
    """Probe the settlement sigma-floor table for (city, season, metric) without raising.

    Returns (floor_found, floor_missing_reason):
      floor_found=True  — a positive floor value exists for this cell.
      floor_found=False — cell absent or table unavailable; floor_missing_reason explains why.

    PARITY RULE (P1 review finding 2026-06-09): called only when apply_settlement_floor=True so
    callers can determine whether the entry q's floor was obtainable at monitor time.
    """
    try:
        from src.calibration.emos import settlement_sigma_floor  # noqa: PLC0415
        floor_val = settlement_sigma_floor(city_name, season, str(metric).lower(), required=False)
        if floor_val is not None and float(floor_val) > 0.0:
            return True, None
        return False, f"floor_cell_absent_or_non_positive:{city_name}|{season}|{str(metric).lower()}"
    except Exception as exc:  # fail-closed: treat as missing, never crash the monitor path
        return False, f"floor_probe_error:{type(exc).__name__}:{exc}"


def _build_monitor_one_calibrator_q(
    *,
    city,
    target_d: date,
    metric: str,
    lead_days: float,
    member_extrema: np.ndarray,
    semantics: SettlementSemantics,
    all_bins: list,
) -> MonitorOneCalibratorQ:
    """Mirror the live entry EMOS/honest-raw q seam for non-Day0 monitor refresh.

    PARITY PROVENANCE (P1 review finding 2026-06-09): when the settlement sigma-floor flag is
    enabled, this function probes the floor table and records floor provenance on the returned
    MonitorOneCalibratorQ. The caller uses floor_missing_reason to detect parity violations:
    if the entry q had the floor applied (same flag on, same cell) but the monitor cannot
    obtain it → the caller marks the monitor probability NOT FRESH so exit decisions do not
    fire on the degraded (narrower) probability.
    """

    from src.calibration.emos import SettlementSigmaFloorError
    from src.calibration.emos_q_builder import build_emos_q, build_honest_raw_q

    season = _monitor_emos_season(target_d)
    unit = str(city.settlement_unit)
    # Wave-2 item 6 (2026-06-12): the settlement σ-floor is applied by PER-CELL DATA
    # AVAILABILITY (no flag — edli_settlement_sigma_floor_enabled / _required deleted),
    # in PARITY with the entry path. apply=True, required=False ⇒ floor when the fitted
    # cell exists, no-op (never blocks) when absent.
    apply_settlement_floor = True
    require_settlement_floor = False

    # PARITY: probe floor availability and share to both the emos and honest-raw branches
    # below so the probe cost is minimal (used for entry/monitor parity provenance).
    _floor_found, _floor_missing_reason = _probe_monitor_settlement_floor(
        city.name, season, metric
    )

    q_result = None
    try:
        q_result = build_emos_q(
            city=city.name,
            season=season,
            metric=metric,
            lead_days=float(lead_days),
            members_native=member_extrema,
            unit=unit,
            bins=all_bins,
            apply_settlement_floor=apply_settlement_floor,
            require_settlement_floor=require_settlement_floor,
        )
    except SettlementSigmaFloorError:
        raise
    except Exception as exc:
        logger.warning(
            "MONITOR_EMOS_SERVE_FAILED cell=%s|%s|%s unit=%s exc=%s: %s",
            city.name,
            season,
            metric,
            unit,
            type(exc).__name__,
            exc,
        )
        q_result = None
    if q_result is not None:
        q_vector, mu_native, sigma_native = q_result
        return MonitorOneCalibratorQ(
            q_vector=np.asarray(q_vector, dtype=float),
            q_source="emos",
            bootstrap_probability_sampler=_monitor_normal_bootstrap_sampler(
                mu_native,
                sigma_native,
            ),
            settlement_sigma_floor_applied=_floor_found,
            settlement_sigma_floor_required=require_settlement_floor,
            floor_missing_reason=_floor_missing_reason,
        )

    honest_raw = None
    try:
        honest_raw = build_honest_raw_q(
            city=city.name,
            season=season,
            metric=metric,
            lead_days=float(lead_days),
            members_native=member_extrema,
            unit=unit,
            bins=all_bins,
            apply_settlement_floor=apply_settlement_floor,
            require_settlement_floor=require_settlement_floor,
        )
    except SettlementSigmaFloorError:
        raise
    except Exception as exc:
        logger.warning(
            "MONITOR_HONEST_RAW_FLOOR_FAILED cell=%s|%s|%s unit=%s exc=%s: %s",
            city.name,
            season,
            metric,
            unit,
            type(exc).__name__,
            exc,
        )
        honest_raw = None
    if honest_raw is not None:
        q_vector, mu_native, sigma_native = honest_raw
        return MonitorOneCalibratorQ(
            q_vector=np.asarray(q_vector, dtype=float),
            q_source="raw_honest",
            bootstrap_probability_sampler=_monitor_normal_bootstrap_sampler(
                mu_native,
                sigma_native,
            ),
            settlement_sigma_floor_applied=_floor_found,
            settlement_sigma_floor_required=require_settlement_floor,
            floor_missing_reason=_floor_missing_reason,
        )

    raw_q = p_raw_vector_from_maxes(
        member_extrema,
        city,
        semantics,
        all_bins,
        n_mc=ensemble_n_mc(),
    )
    return MonitorOneCalibratorQ(
        q_vector=np.asarray(raw_q, dtype=float),
        q_source="raw_honest",
        bootstrap_probability_sampler=None,
        settlement_sigma_floor_applied=False,
        settlement_sigma_floor_required=require_settlement_floor,
        floor_missing_reason=_floor_missing_reason,
    )


def _set_monitor_probability_fresh(position: Position, is_fresh: bool) -> None:
    setattr(position, _MONITOR_PROBABILITY_FRESH_ATTR, is_fresh)


# K6 stage-1 belief-dead watchdog (2026-06-12). A fail-closed hold on missing
# probability authority is correct for one cycle and a silent catastrophe for
# 719 (the Karachi position was monitored its whole life with stale belief and
# nothing escalated). Track consecutive stale-belief cycles per position WHILE
# the market price stays fresh; at the threshold, brand the monitor event and
# log at ERROR so the condition is loud in both the event payload and the log.
_BELIEF_STALE_FAULT_THRESHOLD = 3
_belief_stale_cycles: dict[str, int] = {}

# LAYER 2 belief-debt ledger (2026-06-21 held-belief freeze fix). When the
# synchronous same-authority read-through CANNOT honestly recompute a held
# family's belief (no current single_runs / no on-disk anchor artifact), the
# fail-closed HOLD is correct but MUST be durably recorded so it is never a silent
# permanent freeze. We track (family -> first_failed_at, attempts) in process and
# stamp the structured marker onto the position's applied_validations, which
# cycle_runtime persists to position_events (TRADES state, INV-37 — the monitor
# writes only order-lifecycle state). The existing same-family reseed enqueue is
# the repair lane; the read-through retries every cycle, so the debt is RETRYABLE.
_belief_debt_first_failed_at: dict[str, str] = {}
_belief_debt_attempts: dict[str, int] = {}


def _record_belief_debt(pos: "Position", *, city: str, target_date: str, metric: str, reason: str) -> str:
    """Stamp a durable, retryable belief-debt marker on the position.

    Returns the marker string (also appended to applied_validations). The marker
    carries family + reason + first_failed_at + attempt count so a held position
    can never be silently frozen — the operator/audit can query position_events
    for ``belief_debt`` and the read-through retries it next cycle.
    """
    from datetime import datetime, timezone

    key = f"{city}|{target_date}|{metric}|{getattr(pos, 'trade_id', '') or id(pos)}"
    now_iso = datetime.now(timezone.utc).isoformat()
    first = _belief_debt_first_failed_at.setdefault(key, now_iso)
    attempts = _belief_debt_attempts.get(key, 0) + 1
    _belief_debt_attempts[key] = attempts
    marker = (
        f"belief_debt;city={city};target_date={target_date};metric={metric};"
        f"reason={reason};first_failed_at={first};attempts={attempts}"
    )
    _append_monitor_validation(pos, marker)
    return marker


def _clear_belief_debt(*, city: str, target_date: str, metric: str, pos: "Position") -> None:
    """A successful read-through clears the family's belief-debt counters."""
    key = f"{city}|{target_date}|{metric}|{getattr(pos, 'trade_id', '') or id(pos)}"
    _belief_debt_first_failed_at.pop(key, None)
    _belief_debt_attempts.pop(key, None)


def _track_belief_staleness(pos: Position) -> None:
    key = str(getattr(pos, "trade_id", "") or id(pos))
    if getattr(pos, "last_monitor_prob_is_fresh", False):
        _belief_stale_cycles.pop(key, None)
        return
    if not getattr(pos, "last_monitor_market_price_is_fresh", False):
        return
    count = _belief_stale_cycles.get(key, 0) + 1
    _belief_stale_cycles[key] = count
    _append_monitor_validation(pos, f"belief_stale_cycles={count}")
    if count >= _BELIEF_STALE_FAULT_THRESHOLD:
        _append_monitor_validation(pos, "BELIEF_AUTHORITY_FAULT")
        logger.error(
            "BELIEF_AUTHORITY_FAULT: position %s (%s %s %s) has had stale belief "
            "for %d consecutive monitor cycles while the market price is fresh — "
            "the exit organ is blind on a live position",
            getattr(pos, "trade_id", "?"),
            getattr(pos, "city", "?"),
            getattr(pos, "target_date", "?"),
            getattr(pos, "direction", "?"),
            count,
        )


def _is_position_target_local_day(pos: Position, city, target_d) -> bool:
    if target_d is None:
        return False
    try:
        target_date_value = target_d if isinstance(target_d, date) else date.fromisoformat(str(target_d))
    except Exception:
        return False
    timezone_name = str(getattr(city, "timezone", "") or "").strip()
    try:
        local_today = datetime.now(ZoneInfo(timezone_name)).date()
    except Exception:
        local_today = datetime.now(timezone.utc).date()
    return target_date_value == local_today


def _enqueue_single_family_belief_reseed_failsoft(
    *, city: str, target_date: str, metric: str
) -> dict[str, object] | None:
    """Fail-soft single-family replacement-posterior re-materialization trigger.

    Called when a non-day0 held position finds its replacement belief
    stale/missing (BELIEF_AUTHORITY_FAULT): re-materialize THAT family's
    posterior onto the freshest materializable cycle so the exit organ regains a
    fresh same-authority belief next cycle, instead of papering over the fault
    with a cross-era legacy substitution (regime law U1/U2, 2026-06-12).

    Reuses the SAME live materialization lane the reactor/poll uses
    (forecast_db/seed_dir/raw_manifest_dir from the live queue config + the
    shared idempotency marker),
    so a family already enqueued elsewhere never double-enqueues. NEVER raises into
    the monitor: any error (config missing, DB lock, import failure) is logged and
    a status dict (or None) is returned.
    """
    try:
        from pathlib import Path

        from src.data.replacement_forecast_production import (
            _replacement_forecast_live_materialization_queue_config,
        )
        from src.data.replacement_cycle_advance_trigger import (
            enqueue_single_family_cycle_advance_reseed,
        )

        cfg = _replacement_forecast_live_materialization_queue_config()
        forecast_db = cfg.get("forecast_db")
        seed_dir = cfg.get("seed_dir")
        raw_manifest_dir = cfg.get("raw_manifest_dir")
        if forecast_db is None or seed_dir is None or raw_manifest_dir is None:
            logger.info(
                "monitor belief reseed skipped (lane not configured): %s/%s/%s",
                city, target_date, metric,
            )
            return None
        day0_payload = _day0_observed_extreme_reseed_payload(
            city=city,
            target_date=target_date,
            metric=metric,
        )
        report = enqueue_single_family_cycle_advance_reseed(
            forecast_db=Path(str(forecast_db)),
            seed_dir=Path(str(seed_dir)),
            raw_manifest_dir=Path(str(raw_manifest_dir)),
            city=city,
            target_date=target_date,
            metric=metric,
            held_position=True,
            **day0_payload,
        )
        logger.info(
            "monitor belief reseed enqueued city=%s target_date=%s metric=%s status=%s "
            "enqueued=%s day0_observed_extreme=%s",
            city, target_date, metric,
            report.get("status") if isinstance(report, dict) else None,
            report.get("enqueued") if isinstance(report, dict) else None,
            day0_payload.get("day0_observed_extreme_c") if day0_payload else None,
        )
        return report
    except Exception as exc:  # noqa: BLE001 — reseed MUST NOT crash the monitor
        logger.warning(
            "monitor belief reseed FAILED (fail-soft) city=%s target_date=%s metric=%s exc=%s",
            city, target_date, metric, exc,
        )
        return None


def _freshest_family_seed_on_disk(*, city: str, target_date: str, metric: str):
    """Return (Path, payload) of the freshest on-disk materialization seed for the
    family, or None. Reads ONLY already-written seed files (pending + processed) —
    NO seed is written, NO network fetch, NO DB write. The seed carries the
    Open-Meteo anchor payload + precision metadata paths the read-through needs.

    The seed name is ``{city}.{target_date}.{metric}.{stamp}.json``; we pick the
    lexicographically-latest stamp (ISO-ordered) across the configured seed dirs.
    """
    import json as _json
    from pathlib import Path

    try:
        from src.data.replacement_forecast_production import (
            _replacement_forecast_live_materialization_queue_config,
        )

        cfg = _replacement_forecast_live_materialization_queue_config()
    except Exception:  # noqa: BLE001 — lane not configured / import failure -> not eligible
        return None
    # Normalize the family file-name segments the seed builder uses (spaces -> '_').
    city_seg = str(city).replace(" ", "_")
    prefix = f"{city_seg}.{target_date}.{metric}."
    candidate_dirs = [
        cfg.get("seed_dir"),
        cfg.get("seed_processed_dir"),
        cfg.get("processed_dir"),
    ]
    candidates: list[tuple[str, Path]] = []
    for d in candidate_dirs:
        if not d:
            continue
        base = Path(str(d))
        if not base.exists():
            continue
        try:
            for path in base.glob(f"{prefix}*.json"):
                name = path.name
                if name.endswith(".receipt.json"):
                    continue
                # Compare by the trailing stamp portion (ISO timestamps sort lexically).
                stamp = name[len(prefix):]
                candidates.append((stamp, path))
        except OSError:
            continue
    for _stamp, path in sorted(candidates, reverse=True):
        try:
            payload = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if not _seed_payload_covers_target_local_day(seed_path=path, payload=payload):
            continue
        return path, payload
    return None


def _seed_payload_covers_target_local_day(*, seed_path, payload: dict) -> bool:
    """True iff a held-belief seed can extract its requested local day."""
    from pathlib import Path

    try:
        target = date.fromisoformat(str(payload.get("target_date") or "").strip())
        city_timezone = str(payload.get("city_timezone") or "").strip()
        payload_text = str(payload.get("openmeteo_payload_json") or "").strip()
        if not city_timezone or not payload_text:
            return False
        openmeteo_payload_path = Path(payload_text)
        if not openmeteo_payload_path.is_absolute():
            openmeteo_payload_path = Path(seed_path).parent / openmeteo_payload_path
        openmeteo_payload = json.loads(openmeteo_payload_path.read_text(encoding="utf-8"))
        from src.data.openmeteo_ecmwf_ifs9_anchor import (
            extract_openmeteo_ecmwf_ifs9_localday_anchor,
        )

        extract_openmeteo_ecmwf_ifs9_localday_anchor(
            openmeteo_payload,
            city_timezone=city_timezone,
            target_local_date=target,
            min_hourly_samples=1,
            require_full_localday=False,
        )
    except Exception:
        return False
    return True


def _attempt_held_belief_readthrough(
    pos: "Position", *, city, target_d, metric: str,
    decision_now: datetime | None = None,
) -> float | None:
    """LAYER 2 — synchronous single-family read-through recompute (held-belief freeze fix).

    Recompute THIS held family's replacement posterior via the SAME canonical
    Bayes-precision fusion the live write path uses, against whatever single_runs
    are CURRENTLY persisted, WITHOUT writing forecast_posteriors. Returns the
    fresh HELD-SIDE probability for the position's bin, or None when the family
    cannot be honestly recomputed (no on-disk anchor seed / no current single_runs
    / not live-eligible). Fewer providers ⇒ honestly wider fusion CI (correct).

    ``decision_now`` is the CURRENT monitor cycle instant (the decision time for
    this recompute).  The arrival guard inside the Bayes-precision fusion admits
    only single_runs whose ``source_available_at <= decision_now``, so it MUST be
    the live clock — not the seed's original ``computed_at`` (which could be hours
    earlier, causing every recently-arrived single_run to be excluded and the
    recompute to collapse to STALE_HISTORY_ONLY / live_eligible=False).  The seed's
    ``source_cycle_time`` (the forecast cycle hour, e.g. "06:00 UTC") is kept
    verbatim: it identifies WHICH cycle's single_runs to fuse, not a wall-clock.

    Testability: ``decision_now`` defaults to ``None`` (→ ``datetime.now(UTC)``).
    Pass an explicit value in tests to make the arrival-guard behaviour deterministic.

    INV-37: reads forecasts via a dedicated READ-ONLY forecasts-MAIN connection
    (``get_forecasts_connection_read_only``) — the SAME pattern this module already
    uses for bare ``raw_model_forecasts`` reads — NEVER the trades lifecycle conn
    (whose MAIN is zeus_trades, where the fusion's bare forecast-table names would
    not resolve) and NEVER an independent WRITE connection. Writes nothing.

    Fail-soft: ANY error / missing input returns None so the caller fail-closes to
    HOLD + belief_debt (never a fabricated belief, never a monitor crash).
    """
    try:
        target_date = str(getattr(pos, "target_date", "") or "")
        if not target_date:
            return None
        seed = _freshest_family_seed_on_disk(
            city=str(pos.city), target_date=target_date, metric=metric
        )
        if seed is None:
            return None
        seed_path, seed_payload = seed

        # The on-disk seed is a source/anchor envelope, not the monitor decision
        # instant. Re-use its source-cycle identity, but stamp the read-only
        # request with the current monitor clock so arrival/freshness guards do
        # not compare a new decision time to an expired seed TTL.
        _now = decision_now if decision_now is not None else datetime.now(timezone.utc)
        from src.engine.position_belief import monitor_belief_max_age_hours

        readthrough_ttl_h = max(0.01, float(monitor_belief_max_age_hours()))
        readthrough_payload = dict(seed_payload)
        readthrough_payload["computed_at"] = _now.isoformat()
        readthrough_payload["expires_at"] = (_now + timedelta(hours=readthrough_ttl_h)).isoformat()

        from src.data.replacement_forecast_materialization_request_builder import (
            build_materialize_request_dataclass,
            build_replacement_forecast_materialization_request,
        )

        build = build_replacement_forecast_materialization_request(
            readthrough_payload, base_dir=seed_path.parent
        )
        if not build.ok or build.request is None:
            return None
        request = build_materialize_request_dataclass(
            build.request, base_dir=seed_path.parent
        )

        # ARRIVAL-GUARD DECISION INSTANT FIX (real-chain verified 2026-06-21):
        # The seed's ``computed_at`` is the seed's BUILD time (e.g. 12:09:08 for
        # Panama City's 12Z seed).  The Bayes-precision fusion's arrival guard
        # excludes single_runs whose ``source_available_at > computed_at``; for
        # frozen families the relevant single_runs arrived AFTER the seed's build
        # time (e.g. 06:00-cycle at 14:10) — so using the seed's stale computed_at
        # fuses ZERO multi-model extras → STALE_HISTORY_ONLY → live_eligible=False
        # → read-through returns None → the freeze is reproduced, not cured.
        # Fix: the DECISION INSTANT for this read-through recompute is NOW (the
        # live monitor cycle), so all single_runs available at that instant are
        # admitted.  source_cycle_time (the forecast cycle, "06:00 UTC") is kept
        # verbatim — it is NOT a wall-clock and must NOT be advanced.
        request = replace(
            request,
            computed_at=_now,
            expires_at=_now + timedelta(hours=readthrough_ttl_h),
        )

        from src.data.replacement_forecast_materializer import (
            compute_replacement_posterior_readonly,
        )
        from src.state.db import get_forecasts_connection_read_only

        fc_conn = get_forecasts_connection_read_only()
        try:
            fc_conn.row_factory = sqlite3.Row
            # Enforce the read-only contract at the SQLite level, not just by the
            # factory's name (critic 2026-06-21, MEDIUM-1): query_only turns the
            # no-write guarantee from convention into enforcement. Any inadvertent
            # write through this connection — e.g. a future edit to a reader deep in
            # the fusion call tree — raises instead of silently corrupting forecast
            # truth during the live monitor loop. The compute path is provably
            # write-free today; this is defense-in-depth on a live 51GB forecasts DB.
            fc_conn.execute("PRAGMA query_only=ON")
            result = compute_replacement_posterior_readonly(fc_conn, request)
        finally:
            fc_conn.close()
        if result is None or not result.live_eligible:
            return None
        # Index the held bin by its venue range-label, exactly like load_replacement_belief.
        from src.engine.position_belief import _match_bin  # noqa: PLC0415

        matched = _match_bin(result.q, str(pos.bin_label))
        if matched is None:
            return None
        _bin_key, q_yes = matched
        if not (0.0 <= float(q_yes) <= 1.0):
            return None
        held = _held_side_probability_from_yes_bin_probability(
            float(q_yes), str(getattr(pos.direction, "value", pos.direction))
        )
        if not (0.0 <= float(held) <= 1.0):
            return None
        logger.info(
            "monitor held-belief READ-THROUGH recompute OK city=%s target_date=%s metric=%s "
            "providers=%d/%d q_held=%.4f (exit organ regains fresh same-authority belief)",
            pos.city, target_date, metric,
            result.decorrelated_providers_served, result.decorrelated_providers_expected,
            float(held),
        )
        return float(held)
    except Exception as exc:  # noqa: BLE001 — read-through MUST NOT crash the monitor
        logger.warning(
            "monitor held-belief read-through FAILED (fail-soft -> fail-close) "
            "city=%s target_date=%s metric=%s exc=%s",
            getattr(pos, "city", "?"), getattr(pos, "target_date", "?"), metric, exc,
        )
        return None


def _record_nowcast_write_success() -> None:
    global _nowcast_consecutive_write_failures
    _nowcast_consecutive_write_failures = 0


def _record_nowcast_write_failure(*, market_slug: str, trade_id: str) -> int:
    global _nowcast_consecutive_write_failures
    _nowcast_consecutive_write_failures += 1
    _cnt_inc(
        "monitor_day0_nowcast_write_failed_total",
        labels={"market_slug": str(market_slug or "unknown")},
    )
    if _nowcast_consecutive_write_failures >= _NOWCAST_PERSISTENT_FAILURE_THRESHOLD:
        logger.error(
            "[MONITOR_NOWCAST_WRITE_PERSISTENT_FAILURE] consecutive_failures=%s "
            "trade_id=%s market_slug=%s",
            _nowcast_consecutive_write_failures,
            trade_id,
            market_slug,
        )
    return _nowcast_consecutive_write_failures


def _ens_result_phase2_keys(ens_result: dict) -> tuple[
    str | None, str | None, str | None
]:
    """Extract (cycle, source_id, horizon_profile) from a live ens_result.

    Phase 2.6 hardening (2026-05-04, critic-opus MAJOR 4): monitor exit
    lanes were silently loading the schema-default Platt bucket because
    get_calibrator was called WITHOUT cycle/source_id/horizon_profile
    args. This helper mirrors the evaluator's extraction logic so both
    entry and exit paths route through the same stratified bucket.

    Copilot review #5 + Codex P1 #7 (2026-05-04): delegated to the shared
    forecast_calibration_domain.derive_phase2_keys_from_ens_result helper
    so datetime issue_time and horizon_profile derivation behave the same
    way in monitor and evaluator paths.
    """
    from src.calibration.forecast_calibration_domain import (
        derive_phase2_keys_from_ens_result,
    )
    cycle, source_id, horizon_profile = derive_phase2_keys_from_ens_result(ens_result)
    return cycle, calibration_source_id_for_lookup(source_id), horizon_profile


def _monitor_calibrator_for_ens_result(
    *,
    conn,
    city,
    target_date: str,
    temperature_metric: str,
    ens_result: dict,
):
    """Load monitor calibrator only when source identity has bucket authority."""

    _cycle, _source_id, _horizon = _ens_result_phase2_keys(ens_result)
    raw_source_id = ens_result.get("source_id") if isinstance(ens_result, dict) else None
    if raw_source_id and _source_id is None:
        logger.warning(
            "Monitor forecast source %r has no calibration bucket authority; "
            "skipping Platt recalibration",
            raw_source_id,
        )
        return None, 4
    return get_calibrator(
        conn,
        city,
        target_date,
        temperature_metric=temperature_metric,
        cycle=_cycle,
        source_id=_source_id,
        horizon_profile=_horizon,
    )


def _monitor_forecast_source_validations(ens_result: dict) -> list[str]:
    """Expose degraded forecast authority in monitor/exit evidence."""

    validations: list[str] = []
    source_id = ens_result.get("source_id")
    if source_id:
        validations.append(f"forecast_source_id:{source_id}")
    source_role = ens_result.get("forecast_source_role")
    if source_role:
        validations.append(f"forecast_source_role:{source_role}")
    degradation_level = ens_result.get("degradation_level")
    if degradation_level:
        validations.append(f"forecast_degradation:{degradation_level}")
    return validations


def _parse_utc_datetime(raw: object) -> datetime | None:
    try:
        text = str(raw or "").strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_day0_hourly_vectors(*, city, target_d: date, now: datetime | None = None) -> dict | None:
    """Read the live Day0 remaining-window hourly vectors for monitor belief.

    Day0 held-position redecision needs hourly trajectories. The daily
    ``raw_model_forecasts`` extrema are already the replacement posterior input,
    but they cannot tell the Day0 router which hours remain. The live hourly
    vector table is therefore the only admissible remaining-window source here.
    """

    from src.state.db import get_forecasts_connection_read_only

    city_name = str(getattr(city, "name", "") or "")
    if not city_name:
        return None
    target_date = target_d.isoformat()
    decision_time = now or datetime.now(timezone.utc)
    try:
        conn = get_forecasts_connection_read_only()
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    try:
        latest = conn.execute(
            """
            SELECT captured_at
            FROM day0_hourly_vectors
            WHERE city = ? AND target_date = ?
              AND datetime(captured_at) <= datetime(?)
            ORDER BY datetime(captured_at) DESC
            LIMIT 1
            """,
            (city_name, target_date, decision_time.isoformat()),
        ).fetchone()
        if latest is None:
            return None
        captured_at = str(latest["captured_at"] or "")
        rows = conn.execute(
            """
            SELECT model, timezone_name, times_json, temps_c_json
            FROM day0_hourly_vectors
            WHERE city = ? AND target_date = ? AND captured_at = ?
            ORDER BY model
            """,
            (city_name, target_date, captured_at),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not rows:
        return None

    times: list[str] | None = None
    member_rows: list[list[float]] = []
    for row in rows:
        try:
            row_times = json.loads(row["times_json"] or "null")
            temps_c = json.loads(row["temps_c_json"] or "null")
        except (TypeError, ValueError):
            return None
        if not isinstance(row_times, list) or not isinstance(temps_c, list):
            return None
        if len(row_times) != len(temps_c) or not row_times:
            return None
        row_times = [str(item) for item in row_times]
        if times is None:
            times = row_times
        elif row_times != times:
            return None
        try:
            values_c = [float(item) for item in temps_c]
        except (TypeError, ValueError):
            return None
        if not np.isfinite(np.asarray(values_c, dtype=float)).all():
            return None
        unit = str(getattr(city, "settlement_unit", "C") or "C").upper()
        if unit == "F":
            member_rows.append([(value * 9.0 / 5.0) + 32.0 for value in values_c])
        elif unit == "C":
            member_rows.append(values_c)
        else:
            return None
    if times is None or not member_rows:
        return None
    captured_dt = _parse_utc_datetime(captured_at)
    return {
        "members_hourly": np.asarray(member_rows, dtype=float),
        "times": times,
        "fetch_time": captured_dt,
        "source_id": "day0_hourly_vectors",
        "forecast_source_role": "day0_remaining_window_live",
    }


def _local_hours_remaining(city, target_d: date, *, now: datetime | None) -> float:
    try:
        tz = ZoneInfo(str(getattr(city, "timezone")))
    except Exception:
        return 0.0
    moment = (now or datetime.now(timezone.utc)).astimezone(tz)
    end_local = datetime.combine(target_d + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return max(0.0, (end_local - moment).total_seconds() / 3600.0)


def _read_day0_raw_model_extrema(
    *,
    city,
    target_d: date,
    metric: str,
    now: datetime | None = None,
) -> dict | None:
    """Read live same-day replacement raw extrema when no hourly vector exists."""

    from src.state.db import get_forecasts_connection_read_only

    city_name = str(getattr(city, "name", "") or "")
    if not city_name or metric not in {"high", "low"}:
        return None
    decision_time = now or datetime.now(timezone.utc)
    target_date = target_d.isoformat()
    try:
        conn = get_forecasts_connection_read_only()
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    try:
        latest = conn.execute(
            """
            SELECT source_cycle_time
            FROM raw_model_forecasts
            WHERE city = ? AND target_date = ? AND metric = ?
              AND endpoint = 'single_runs'
              AND datetime(source_cycle_time) <= datetime(?)
              AND (source_available_at IS NULL OR datetime(source_available_at) <= datetime(?))
              AND (coverage_status IS NULL OR coverage_status = 'COVERED')
            GROUP BY source_cycle_time
            ORDER BY datetime(source_cycle_time) DESC
            LIMIT 1
            """,
            (
                city_name,
                target_date,
                metric,
                decision_time.isoformat(),
                decision_time.isoformat(),
            ),
        ).fetchone()
        if latest is None:
            return None
        cycle = str(latest["source_cycle_time"] or "")
        rows = conn.execute(
            """
            SELECT model, forecast_value_c
            FROM raw_model_forecasts
            WHERE city = ? AND target_date = ? AND metric = ?
              AND endpoint = 'single_runs'
              AND source_cycle_time = ?
              AND (coverage_status IS NULL OR coverage_status = 'COVERED')
            ORDER BY model
            """,
            (city_name, target_date, metric, cycle),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    values_c: list[float] = []
    seen_models: set[str] = set()
    for row in rows:
        model = str(row["model"] or "")
        if not model or model in seen_models:
            continue
        seen_models.add(model)
        try:
            value_c = float(row["forecast_value_c"])
        except (TypeError, ValueError):
            return None
        if not np.isfinite(value_c):
            return None
        values_c.append(value_c)
    if not values_c:
        return None
    unit = str(getattr(city, "settlement_unit", "C") or "C").upper()
    if unit == "F":
        values = [(value * 9.0 / 5.0) + 32.0 for value in values_c]
    elif unit == "C":
        values = values_c
    else:
        return None
    return {
        "member_extrema": np.asarray(values, dtype=float),
        "source_id": "raw_model_forecasts.single_runs",
        "forecast_source_role": "day0_daily_extrema_live",
        "source_cycle_time": cycle,
    }


def _monitor_city_id(city) -> str:
    return str(city.name).upper().replace(" ", "_")


def _monitor_condition_id(position: Position) -> str:
    return str(
        getattr(position, "condition_id", "")
        or getattr(position, "market_id", "")
        or getattr(position, "trade_id", "")
        or ""
    )


def _monitor_market_family(position: Position, city, target_d, temperature_metric: MetricIdentity) -> str:
    market_ref = getattr(position, "market_id", "") or getattr(position, "condition_id", "")
    if market_ref:
        return str(market_ref)
    return f"{city.name}|{target_d.isoformat()}|{temperature_metric.temperature_metric}"


def _read_monitor_executable_forecast(
    *,
    conn,
    position: Position,
    city,
    target_d: date,
    temperature_metric: MetricIdentity,
) -> tuple[dict | None, str | None]:
    """Legacy/fallback ENSEMBLE executable-forecast read — NOT the live entry authority.

    PROVENANCE (corrected 2026-06-16, spine source-divergence fix): the live
    entry decision authority is the multi-model fused posterior
    ``forecast_posteriors`` (read via ``position_belief.load_replacement_belief``,
    sourced from ``raw_model_forecasts`` provider fusion) — NOT this reader. This
    function reads ``ensemble_snapshots`` (51 ``ecmwf_ens`` members of a single
    model) through the executable-forecast contract. It is a SUPPRESSED legacy
    fallback: for replacement-authority (edli) positions the belief-authority-fault
    guard in ``monitor_probability_refresh`` returns BEFORE the ensemble registry
    is dispatched (see the ``legacy_belief_substitution_suppressed`` early return),
    so this path is NOT used as the freshness authority for those positions. It is
    reached only as ``applied``-list telemetry and, for legacy non-edli positions
    not covered by a fresh ``forecast_posteriors`` row, as a last-resort center.

    A held-position monitor must not fall back to the legacy Open-Meteo
    ``fetch_ensemble`` adapter for that source, because that path cannot prove the
    executable forecast reader contract.  Non-real sqlite connections return
    ``(None, None)`` so legacy unit tests and diagnostic callsites keep their
    existing fallback behavior.
    """

    if not isinstance(conn, sqlite3.Connection):
        return None, None
    try:
        cfg = entry_forecast_config()
    except Exception as exc:
        return None, f"entry_forecast_config_error:{exc.__class__.__name__}"
    if cfg.source_id != "ecmwf_open_data":
        return None, None
    try:
        track = track_for_metric(cfg, temperature_metric.temperature_metric)
        reader_result = read_executable_forecast(
            conn,
            city_id=_monitor_city_id(city),
            city_name=city.name,
            city_timezone=city.timezone,
            target_local_date=target_d,
            temperature_metric=temperature_metric.temperature_metric,
            source_id=cfg.source_id,
            source_transport=cfg.source_transport.value,
            data_version=data_version_for_track(track),
            track=track,
            strategy_key="entry_forecast",
            market_family=_monitor_market_family(position, city, target_d, temperature_metric),
            condition_id=_monitor_condition_id(position),
            decision_time=datetime.now(timezone.utc),
            require_entry_readiness=False,
        )
    except Exception as exc:
        return None, f"executable_forecast_reader_error:{exc.__class__.__name__}"
    if reader_result.ok and reader_result.bundle is not None:
        return reader_result.bundle.to_ens_result(), None
    return None, f"executable_forecast_reader_blocked:{reader_result.reason_code}"


def _build_all_bins(position: Position, city) -> tuple[list, int]:
    """Build full bin vector for a position's market.

    S6: Uses sibling outcomes from market scanner to reconstruct the
    complete bin set, matching the entry path's calibrate_and_normalize().
    Missing or invalid support is a stale refresh, not a license to
    recalibrate against a single held bin.

    Returns (all_bins, held_bin_index).
    """
    held_low, held_high = _parse_temp_range(position.bin_label)
    if held_low is None and held_high is None:
        raise ValueError(f"held bin label is not parseable: {position.bin_label!r}")

    if not position.market_id:
        raise ValueError("support topology unavailable: missing held market_id")

    siblings = get_sibling_outcomes(position.market_id)
    scan_authority = str(get_last_scan_authority()).upper()
    if scan_authority != "VERIFIED":
        raise ValueError(f"support topology stale: market_scan_authority={scan_authority}")
    if len(siblings) < 2:
        raise ValueError(
            f"support topology incomplete: found {len(siblings)} sibling outcomes"
        )

    all_bins = []
    held_idx = 0
    for o in siblings:
        low, high = o.get("range_low"), o.get("range_high")
        if low is None and high is None:
            continue
        try:
            b = Bin(low=low, high=high, label=o["title"], unit=city.settlement_unit)
        except (ValueError, TypeError):
            continue
        if o.get("market_id") == position.market_id:
            held_idx = len(all_bins)
        all_bins.append(b)

    if not all_bins:
        raise ValueError("support topology has no parseable sibling bins")

    matched = any(o.get("market_id") == position.market_id for o in siblings
                  if not (o.get("range_low") is None and o.get("range_high") is None))
    if not matched:
        raise ValueError(f"held market_id {position.market_id!r} not found in support topology")

    try:
        validate_bin_topology(all_bins)
    except BinTopologyError as exc:
        raise ValueError(f"support topology invalid: {exc}") from exc

    return all_bins, held_idx


def _resolve_unified_exit_bias_native(
    conn: sqlite3.Connection,
    city,
    target_d: date,
    metric_str: str,
) -> "float | None":
    """D2 bias-family unify (2026-06-03): EXIT-side analogue of the LIVE EDLI reactor's
    entry bias correction (``event_reactor_adapter._maybe_apply_edli_bias_correction``).

    Returns the native-unit per-city bias SHIFT to subtract from the member extrema BEFORE
    p_raw on the exit/monitor path, so the exit belief matches the entry belief — or None.

    The live flag ``feature_flags.exit_bias_family_unify_enabled`` reads the SAME populated VERIFIED
    family the reactor entry uses (``event_reactor_adapter._EDLI_BIAS_FAMILY`` =
    'edli_per_city_v1', 71 rows) with the reactor's EXACT read shape:
    ``month=target_month, target_month=target_month, authority='VERIFIED', lead_bucket=None``.
    The legacy ft read shape (month=0, lead_bucket=computed) 0-row-missed these rows because
    the stored rows are ``lead_bucket='LEGACY_POOLED'`` and ``month`` ∈ {target months}.

    A4 lockstep (do not ship a half-fix): this is a bias-SHIFT only — NO residual widening
    (matches the entry's ``_maybe_apply_edli_bias_correction``, which residual-widening does
    NOT do). The caller MUST also apply identity-Platt on the corrected domain (skip
    ``calibrate_and_normalize``), because the Platt models were fit on the UNCORRECTED p_raw
    domain (same reasoning as ``event_reactor_adapter._snapshot_p_cal``). The legacy
    full_transport (FT) error-model path - removed 2026-06-14 (retired 0-row experiment) -
    residual-widened AND ran real Platt, which on a bias-shifted domain would be a
    DIFFERENT, NEW asymmetry; this helper is deliberately a separate bias-shift-only path.

    UNIT: ``effective_bias_c`` is degC; F-settled cities (members carry the city's settlement
    unit) need ×1.8 before subtraction — same conversion the reactor entry applies.

    FAIL-CLOSED: missing flag/config/row/field or any error -> None (no correction; the caller
    uses today's plain-p_raw + real-Platt path). Never breaks the exit decision.
    Authority: D2 bias-family unify / wiring verdict 2026-06-03.
    """
    try:
        if not bool(settings["feature_flags"].get("exit_bias_family_unify_enabled", False)):
            return None
        # Reuse the reactor's family constant — never a second 'edli_per_city_v1' literal.
        from src.engine.event_reactor_adapter import _EDLI_BIAS_FAMILY  # noqa: PLC0415
        try:
            cfg = entry_forecast_config()
            track = track_for_metric(cfg, metric_str)
            live_data_version = data_version_for_track(track)
        except Exception:
            return None
        season = season_from_date(target_d.isoformat(), lat=city.lat)
        _tmonth = int(target_d.isoformat()[5:7])
        row = read_bias_model(
            conn,
            city=city.name,
            season=season,
            metric=metric_str,
            live_data_version=live_data_version,
            month=_tmonth,
            target_month=_tmonth,
            authority="VERIFIED",
            error_model_family=_EDLI_BIAS_FAMILY,
            lead_bucket=None,
        )
        if row is None:
            logger.warning(
                "exit_bias_family_unify: flag ON but no VERIFIED %s row for "
                "city=%r season=%r metric=%r live_data_version=%r month=%r — plain p_raw (uncorrected exit)",
                _EDLI_BIAS_FAMILY, city.name, season, metric_str, live_data_version, _tmonth,
            )
            return None
        keys = set(row.keys())
        eff = row["effective_bias_c"] if "effective_bias_c" in keys else None
        wl = row["weight_live"] if "weight_live" in keys else 0.0
        if eff is None or float(wl or 0.0) <= 0.0:
            return None
        unit = getattr(city, "settlement_unit", "C")
        eff_native = float(eff) * 1.8 if unit == "F" else float(eff)
        logger.info(
            "exit_bias_family_unify applied city=%s season=%s metric=%s unit=%s eff_bias_c=%.3f eff_native=%.3f",
            city.name, season, metric_str, unit, float(eff), eff_native,
        )
        return eff_native
    except Exception as exc:  # fail-closed: never break the exit decision path
        try:
            logger.warning("exit_bias_family_unify skipped (fail-closed): %s", exc)
        except Exception:
            pass
        return None


def _hours_since_open_or_nan(position) -> float:
    """Hold age in hours from a REAL ``entered_at``; NaN when ``entered_at`` is
    missing or malformed. M2b (timing-semantics fix 2026-06-16): never the
    fabricated 48h — NaN routes the caller to an explicit refuse so a missing
    hold-age authority is treated as missing, not as "old enough to exit".
    Shared by both monitor-refresh paths so they grade hold age identically.
    """
    if not position.entered_at:
        return float("nan")
    try:
        entered = datetime.fromisoformat(position.entered_at)
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - entered).total_seconds() / 3600.0
    except Exception:
        return float("nan")


def _refresh_ens_member_counting(
    *,
    position: Position,
    current_p_market: float,
    conn,
    city,
    target_d,
) -> tuple[float, list[str]]:
    """Recompute fresh probability with the same ENS member-counting path as entry."""
    # Slice P2-fix5 (post-review MAJOR #5 from code-reviewer, 2026-04-26):
    # hoist resolver call to function entry. Pre-fix called
    # resolve_position_metric(position) at L149 + L192 + L224 (3 sites in
    # this function). The resolver result is invariant within a single
    # monitor cycle, so each redundant call wasted attribute lookups and
    # — for missing-metric positions — emitted 3 identical DEBUG log
    # lines per cycle, inflating the audit trail and confusing operator
    # review.
    _position_metric_str = resolve_position_metric(position)[0]
    temperature_metric = MetricIdentity.from_raw(_position_metric_str)
    try:
        entry_provenance = position.selected_method or position.entry_method
    except AttributeError:
        entry_provenance = ""
    if not entry_provenance:
        logger.debug("Monitor refresh missing entry provenance for %s", getattr(position, "trade_id", "?"))

    requested_lead_days = max(0.0, lead_days_to_date_start(target_d, city.timezone))
    if requested_lead_days < 0:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["fresh_ens_fetch"]

    ens_result, executable_forecast_block = _read_monitor_executable_forecast(
        conn=conn,
        position=position,
        city=city,
        target_d=target_d,
        temperature_metric=temperature_metric,
    )
    if ens_result is None and executable_forecast_block is not None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, [
            "fresh_ens_fetch",
            "entry_forecast_reader",
            executable_forecast_block,
            "legacy_monitor_fallback_blocked",
        ]
    if ens_result is None:
        ens_result = fetch_ensemble(
            city,
            forecast_days=int(requested_lead_days) + 2,
            model=ensemble_primary_model(),
            role="monitor_fallback",
            temperature_metric=temperature_metric.temperature_metric,
        )
    period_extrema_members = ens_result.get("period_extrema_members") if isinstance(ens_result, dict) else None
    using_period_extrema = period_extrema_members is not None
    if ens_result is None or (not using_period_extrema and not validate_ensemble(ens_result)):
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["fresh_ens_fetch"]
    forecast_source_validations = _monitor_forecast_source_validations(ens_result)
    lead_days = max(0.0, lead_days_to_date_start(target_d, city.timezone, ens_result.get("fetch_time")))

    semantics = SettlementSemantics.for_city(city)
    ens = None
    if not using_period_extrema:
        ens = EnsembleSignal(
            ens_result["members_hourly"],
            ens_result["times"],
            city,
            target_d,
            settlement_semantics=semantics,
            decision_time=ens_result.get("fetch_time"),
            temperature_metric=temperature_metric,
        )

    low, high = _parse_temp_range(position.bin_label)
    if low is None and high is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["fresh_ens_fetch"]

    # S6: Build full bin vector for calibrate_and_normalize (same path as entry).
    try:
        all_bins, held_idx = _build_all_bins(position, city)
    except ValueError as exc:
        logger.warning("Monitor support topology unavailable for %s: %s", position.market_id, exc)
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, [
            "fresh_ens_fetch",
            *forecast_source_validations,
            "support_topology_stale",
            str(exc),
        ]

    _monitor_emos_regime = _monitor_emos_regime_enabled()
    _monitor_q_source: str | None = None
    _bootstrap_probability_sampler = None
    # D2 bias-family unify (2026-06-03): legacy EXIT-side mirror. In the
    # EMOS sole regime this must be skipped entirely; otherwise monitor and
    # entry would use different probability builders for the same held market.
    _unified_exit_bias_native = None
    if not _monitor_emos_regime:
        _unified_exit_bias_native = _resolve_unified_exit_bias_native(
            conn, city, target_d, _position_metric_str,
        )

    if using_period_extrema:
        expected_members_unit = "degC" if city.settlement_unit == "C" else "degF"
        if ens_result.get("members_unit") != expected_members_unit:
            _set_monitor_probability_fresh(position, False)
            return position.p_posterior, [
                "fresh_ens_fetch",
                *forecast_source_validations,
                "entry_forecast_reader",
                "members_unit_mismatch",
            ]
        member_extrema = np.asarray(period_extrema_members, dtype=float)
        _extrema_floor = settings["ensemble"].get("min_members_floor", ensemble_member_count())
        if (
            member_extrema.ndim != 1
            or len(member_extrema) < _extrema_floor
            or not np.isfinite(member_extrema).all()
        ):
            _set_monitor_probability_fresh(position, False)
            return position.p_posterior, [
                "fresh_ens_fetch",
                *forecast_source_validations,
                "entry_forecast_reader",
                "period_extrema_members_invalid",
            ]
        _member_unit = expected_members_unit  # already validated above
        if _monitor_emos_regime:
            try:
                _monitor_q = _build_monitor_one_calibrator_q(
                    city=city,
                    target_d=target_d,
                    metric=_position_metric_str,
                    lead_days=float(lead_days),
                    member_extrema=member_extrema,
                    semantics=semantics,
                    all_bins=all_bins,
                )
            except Exception as exc:
                logger.warning(
                    "monitor_emos_sole_calibrator unavailable for %s %s %s: %s",
                    city.name,
                    target_d,
                    _position_metric_str,
                    exc,
                )
                _set_monitor_probability_fresh(position, False)
                return position.p_posterior, [
                    "fresh_ens_fetch",
                    *forecast_source_validations,
                    "entry_forecast_reader",
                    "period_extrema_members_adapter",
                    f"monitor_emos_sole_calibrator_failed:{type(exc).__name__}",
                ]
            # PARITY RULE (P1 review finding 2026-06-09; Wave-2 item 6 refinement 2026-06-12):
            # the floor is applied by PER-CELL DATA AVAILABILITY on BOTH entry and monitor (same
            # table, same cell). A genuinely ABSENT cell is SYMMETRIC — entry also applied no
            # floor — so it is NOT a parity violation and must NOT newly block exits for the
            # 44/54 cities that have no fitted floor cell. Only a TRANSIENT probe error
            # (table read failed at monitor while entry may have obtained the floor) is a true
            # asymmetry: mark NOT FRESH so exit decisions do not fire on a possibly-degraded
            # (narrower) posterior — same fail-closed semantics as the day0 panic-sell hold fix.
            _floor_probe_failed_transiently = (
                _monitor_q.floor_missing_reason is not None
                and str(_monitor_q.floor_missing_reason).startswith("floor_probe_error")
            )
            if _floor_probe_failed_transiently:
                logger.warning(
                    "MONITOR_FLOOR_PARITY_VIOLATION cell=%s|%s|%s "
                    "floor_applied=%s floor_missing_reason=%s — "
                    "monitor q narrower than entry q; marking NOT FRESH (no exit trigger)",
                    city.name,
                    _monitor_emos_season(target_d),
                    _position_metric_str,
                    _monitor_q.settlement_sigma_floor_applied,
                    _monitor_q.floor_missing_reason,
                )
                _set_monitor_probability_fresh(position, False)
                return position.p_posterior, [
                    "fresh_ens_fetch",
                    *forecast_source_validations,
                    "entry_forecast_reader",
                    "period_extrema_members_adapter",
                    "monitor_emos_sole_calibrator",
                    "monitor_floor_parity_violation",
                    f"floor_missing_reason:{_monitor_q.floor_missing_reason}",
                ]
            p_raw_vector = _monitor_q.q_vector
            p_cal_full = np.asarray(_monitor_q.q_vector, dtype=float)
            p_cal_yes = float(p_cal_full[held_idx])
            _monitor_q_source = _monitor_q.q_source
            _bootstrap_probability_sampler = _monitor_q.bootstrap_probability_sampler
            base_applied = [
                "fresh_ens_fetch",
                *forecast_source_validations,
                "entry_forecast_reader",
                "period_extrema_members_adapter",
                "monitor_emos_sole_calibrator",
                f"q_source:{_monitor_q_source}",
                f"settlement_sigma_floor_applied:{_monitor_q.settlement_sigma_floor_applied}",
            ]
        else:
            # _monitor_q absent: full_transport (FT) error-model path was retired
            # as a 0-row experiment. p_raw uses the unified-bias or plain branch below.
            pass
        if _monitor_q_source is not None:
            pass
        elif _unified_exit_bias_native is not None:
            # D2 unify: bias-SHIFT only (no residual widening), then plain p_raw — the
            # exact entry treatment. Calibration step uses identity-Platt (set below).
            member_extrema = member_extrema - float(_unified_exit_bias_native)
            p_raw_vector = p_raw_vector_from_maxes(
                member_extrema,
                city,
                semantics,
                all_bins,
                n_mc=ensemble_n_mc(),
            )
            base_applied = [
                "fresh_ens_fetch",
                *forecast_source_validations,
                "entry_forecast_reader",
                "period_extrema_members_adapter",
                "mc_instrument_noise",
                "exit_bias_family_unify",
            ]
        else:
            p_raw_vector = p_raw_vector_from_maxes(
                member_extrema,
                city,
                semantics,
                all_bins,
                n_mc=ensemble_n_mc(),
            )
            base_applied = [
                "fresh_ens_fetch",
                *forecast_source_validations,
                "entry_forecast_reader",
                "period_extrema_members_adapter",
                "mc_instrument_noise",
            ]
        ensemble_spread = TemperatureDelta(float(np.std(member_extrema)), city.settlement_unit)
        analysis_member_extrema = member_extrema
    else:
        assert ens is not None
        # Bug 3 fix (Zeus #64 PR #342): avoid eager evaluation of fallback getattr —
        # getattr(ens, "member_maxes") would raise AttributeError if member_maxes also absent.
        if hasattr(ens, "member_extrema"):
            _ens_member_extrema = ens.member_extrema
        else:
            _ens_member_extrema = ens.member_maxes
        _member_unit = "degC" if city.settlement_unit == "C" else "degF"
        if _monitor_emos_regime:
            try:
                _monitor_q = _build_monitor_one_calibrator_q(
                    city=city,
                    target_d=target_d,
                    metric=_position_metric_str,
                    lead_days=float(lead_days),
                    member_extrema=np.asarray(_ens_member_extrema, dtype=float),
                    semantics=ens.settlement_semantics,
                    all_bins=all_bins,
                )
            except Exception as exc:
                logger.warning(
                    "monitor_emos_sole_calibrator unavailable for %s %s %s: %s",
                    city.name,
                    target_d,
                    _position_metric_str,
                    exc,
                )
                _set_monitor_probability_fresh(position, False)
                return position.p_posterior, [
                    "fresh_ens_fetch",
                    *forecast_source_validations,
                    f"monitor_emos_sole_calibrator_failed:{type(exc).__name__}",
                ]
            # PARITY RULE (P1 review finding 2026-06-09): mirror of the period-extrema branch
            # above — floor enabled but cell absent → monitor q narrower than entry q → NOT FRESH.
            if _monitor_q.floor_missing_reason is not None:
                logger.warning(
                    "MONITOR_FLOOR_PARITY_VIOLATION cell=%s|%s|%s "
                    "floor_applied=%s floor_missing_reason=%s — "
                    "monitor q narrower than entry q; marking NOT FRESH (no exit trigger)",
                    city.name,
                    _monitor_emos_season(target_d),
                    _position_metric_str,
                    _monitor_q.settlement_sigma_floor_applied,
                    _monitor_q.floor_missing_reason,
                )
                _set_monitor_probability_fresh(position, False)
                return position.p_posterior, [
                    "fresh_ens_fetch",
                    *forecast_source_validations,
                    "monitor_emos_sole_calibrator",
                    "monitor_floor_parity_violation",
                    f"floor_missing_reason:{_monitor_q.floor_missing_reason}",
                ]
            p_raw_vector = _monitor_q.q_vector
            p_cal_full = np.asarray(_monitor_q.q_vector, dtype=float)
            p_cal_yes = float(p_cal_full[held_idx])
            _monitor_q_source = _monitor_q.q_source
            _bootstrap_probability_sampler = _monitor_q.bootstrap_probability_sampler
            base_applied = [
                "fresh_ens_fetch",
                *forecast_source_validations,
                "monitor_emos_sole_calibrator",
                f"q_source:{_monitor_q_source}",
                f"settlement_sigma_floor_applied:{_monitor_q.settlement_sigma_floor_applied}",
            ]
        else:
            # _monitor_q absent: full_transport (FT) error-model path was retired
            # as a 0-row experiment. p_raw uses the unified-bias or plain branch below.
            pass
        if _monitor_q_source is not None:
            pass
        elif _unified_exit_bias_native is not None:
            # D2 unify: bias-SHIFT only (no residual widening), then plain p_raw via the
            # shared p_raw_vector_from_maxes path (same path ens.p_raw_vector and the ft
            # branch use). Calibration step uses identity-Platt (set below).
            _ens_member_extrema = np.asarray(_ens_member_extrema, dtype=float) - float(_unified_exit_bias_native)
            p_raw_vector = p_raw_vector_from_maxes(
                _ens_member_extrema,
                city,
                ens.settlement_semantics,
                all_bins,
                n_mc=ensemble_n_mc(),
            )
            base_applied = [
                "fresh_ens_fetch",
                *forecast_source_validations,
                "mc_instrument_noise",
                "exit_bias_family_unify",
            ]
        else:
            p_raw_vector = ens.p_raw_vector(all_bins, n_mc=ensemble_n_mc())
            base_applied = [
                "fresh_ens_fetch",
                *forecast_source_validations,
                "mc_instrument_noise",
            ]
        ensemble_spread = ens.spread()
        analysis_member_extrema = _ens_member_extrema

    # DT#5 / L3 Phase 9C: thread temperature_metric so LOW position reads
    # its own Platt model (pre-P9C this was metric-blind and LOW silently
    # received HIGH calibration — critical blocker for LOW deployment).
    # Slice P2-C2 (PR #19 phase 2, 2026-04-26): route via canonical
    # resolver. Pre-fix, `getattr(position, "temperature_metric", "high")`
    # silently substituted HIGH for any position with missing metric,
    # directly undermining the L3 Phase 9C metric-aware gate at the entry
    # seam (a LOW position with no attribute received HIGH calibration
    # silently). Post-fix, the resolver still defaults to HIGH for
    # backward compat, but emits a DEBUG log identifying the position so
    # operators can audit silent-HIGH events.
    # Phase 2.6 (2026-05-04, critic-opus MAJOR 4): thread Phase 2 stratification
    # axes so monitor exit calibration uses the same bucket the entry side did.
    if _monitor_q_source is not None:
        cal = None
        cal_level = 1
        applied = [
            *base_applied,
            "identity_one_calibrator",
            "vector_normalization",
        ]
    else:
        cal, cal_level = _monitor_calibrator_for_ens_result(
            conn=conn,
            city=city,
            target_date=position.target_date,
            temperature_metric=_position_metric_str,  # hoisted (P2-fix5)
            ens_result=ens_result,
        )
    if _monitor_q_source is not None:
        pass
    elif _unified_exit_bias_native is not None:
        # D2 unify A4 lockstep: the member maxes were bias-SHIFTED above, so the existing
        # Platt models (fit on the UNCORRECTED p_raw domain) would mis-calibrate the shifted
        # domain. Use identity Platt (p_cal = normalized p_raw) — EXACT mirror of the entry
        # path's event_reactor_adapter._snapshot_p_cal lockstep. Keeps exit belief consistent
        # with entry belief (same bias shift AND same Platt treatment).
        _arr = np.asarray(p_raw_vector, dtype=float)
        _tot = float(_arr.sum())
        if _tot <= 0.0 or not np.isfinite(_arr).all():
            # Fail-closed: invalid corrected p_raw → keep stale probability (never trade on garbage).
            _set_monitor_probability_fresh(position, False)
            return position.p_posterior, [*base_applied, "exit_bias_family_unify_invalid_p_raw"]
        p_cal_full = _arr / _tot
        p_cal_yes = float(p_cal_full[held_idx]) if len(all_bins) > 1 else float(p_cal_full[0])
        # NOTE: cal_level (int 1-4) from _monitor_calibrator_for_ens_result is left
        # UNCHANGED — compute_alpha indexes BASE_ALPHA_BY_LEVEL[cal_level] and requires an
        # int. The identity-Platt substitution affects only the p_cal vector, not the
        # alpha calibration_level, mirroring how the entry reactor keeps its own bucket level.
        applied = [
            *base_applied,
            "identity_platt_bias_unify",
            "vector_normalization",
        ]
    elif cal is not None and len(all_bins) > 1:
        p_cal_vector = calibrate_and_normalize(
            p_raw_vector,
            cal,
            float(lead_days),
            bin_widths=[b.width for b in all_bins],
        )
        p_cal_yes = float(p_cal_vector[held_idx])
        p_cal_full = p_cal_vector
        applied = [
            *base_applied,
            "platt_recalibration",
            "vector_normalization",
        ]
    elif cal is not None:
        p_cal_yes = cal.predict_for_bin(
            float(p_raw_vector[0]),
            float(lead_days),
            bin_width=all_bins[0].width,
        )
        p_cal_full = np.array([p_cal_yes], dtype=float)
        applied = [
            *base_applied,
            "platt_recalibration",
        ]
    else:
        p_cal_yes = float(p_raw_vector[held_idx])
        p_cal_full = p_raw_vector if len(all_bins) > 1 else np.array([p_cal_yes], dtype=float)
        applied = [*base_applied]

    # M2b (timing-semantics fix 2026-06-16): hold age from a REAL entered_at;
    # NaN when missing/malformed -> explicit refuse below (never the fabricated
    # 48h). Shared helper so both refresh paths grade hold age identically.
    hours_since_open = _hours_since_open_or_nan(position)

    # K1/#68: verify calibration authority before computing alpha.
    # Same gate as evaluator.py — check for UNVERIFIED calibration rows.
    # Slice P2-A2 (PR #19 phase 2, 2026-04-26): scope to active metric so
    # cross-metric noise doesn't trigger false-positive stale-probability
    # warnings. Resolver from P2-C1 already determined position metric
    # for this monitor cycle (post-P2-C2 routing); reuse it here.
    _authority_verified = _monitor_q_source is not None
    if _monitor_q_source is None and conn is not None and hasattr(conn, 'execute'):
        from src.calibration.store import get_pairs_for_bucket as _get_pairs
        _cal_season = season_from_date(target_d.isoformat(), lat=city.lat)
        _gate_metric = "high" if _position_metric_str == "high" else None  # hoisted (P2-fix5)
        try:
            _unverified_pairs = _get_pairs(
                conn, city.cluster, _cal_season,
                authority_filter='UNVERIFIED',
                metric=_gate_metric,
            )
        except Exception:
            _unverified_pairs = []
        if _unverified_pairs:
            logger.warning(
                "Monitor authority gate: %d UNVERIFIED calibration rows for %s/%s — using stale probability",
                len(_unverified_pairs), city.name, _cal_season,
            )
            _set_monitor_probability_fresh(position, False)
            applied.append("authority_gate_blocked")
            return position.p_posterior, applied
        _authority_verified = True

    # M2b: missing/malformed entered_at -> hours_since_open is NaN -> REFUSE.
    # compute_alpha does not itself reject NaN (NaN < threshold is False, so it
    # would silently skip the freshness adjustment and return base alpha — the
    # same fabrication this fix removes). Refuse explicitly so the exit gate
    # treats missing hold-age authority as missing, not as "old enough to exit".
    if not np.isfinite(hours_since_open):
        _set_monitor_probability_fresh(position, False)
        applied.append("entered_at_missing_alpha_refused")
        return position.p_posterior, applied

    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ensemble_spread,
        model_agreement=getattr(position, "entry_model_agreement", "NOT_CHECKED"),
        lead_days=float(lead_days),
        hours_since_open=hours_since_open,
        authority_verified=_authority_verified,
    ).value_for_consumer("ev")

    # Persistence anomaly check: if ENS predicts a historically rare
    # day-to-day temperature change, discount model trust
    # Slice P2-fix5 (post-review MAJOR #6): route bare attribute access
    # through the same hoisted resolver result. Pre-fix would AttributeError
    # on a position with missing temperature_metric attr (now uses the
    # resolver default).
    anomaly_discount = _check_persistence_anomaly(
        conn, city.name, target_d, float(np.mean(analysis_member_extrema)),
        temperature_metric=_position_metric_str,
    )
    if anomaly_discount < 1.0:
        alpha *= anomaly_discount
        # Fraction of alpha removed is (1 - anomaly_discount); de-obfuscated from
        # the value-identical (1/x - 1) * x that 16c35e7445 wrote (§0.2 / FIX-5a).
        anomaly_removed = (
            1.0 if anomaly_discount <= 0.0
            else one_minus(anomaly_discount)
        )
        applied.append("persistence_anomaly_discount")
        logger.info(
            "Persistence anomaly for %s: α discounted by %.0f%%",
            city.name, anomaly_removed * 100,
        )

    p_cal_native = _held_side_probability_from_yes_bin_probability(
        p_cal_yes,
        position.direction,
    )

    current_p_posterior = _model_only_native_posterior(p_cal_native)

    # A1: Stash bootstrap-relevant data for fresh CI computation in refresh_position
    setattr(position, "_bootstrap_context", {
        "p_raw": p_raw_vector,
        "p_cal": p_cal_full,
        "alpha": alpha,
        "bins": all_bins,
        "held_idx": held_idx,
        "member_extrema": analysis_member_extrema,
        "calibrator": cal,
        "lead_days": float(lead_days),
        "unit": city.settlement_unit,
        "bootstrap_probability_sampler": _bootstrap_probability_sampler,
        "bootstrap_signal_type": (
            "monitor_emos_sole_calibrator"
            if _monitor_q_source is not None
            else "monitor_forecast"
        ),
    })

    _set_monitor_probability_fresh(position, True)
    return current_p_posterior, [*applied, "model_only_posterior", "alpha_posterior"]


def _position_state_value(pos: Position) -> str:
    return str(getattr(getattr(pos, "state", ""), "value", getattr(pos, "state", "")) or "")


def _city_supports_executable_day0_observation(city) -> bool:
    source_type = str(getattr(city, "settlement_source_type", "") or "").strip()
    return source_type in DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE


def _fetch_day0_observation(city: Position | object, target_d: date):
    reference_time = datetime.now(timezone.utc)
    if str(getattr(city, "settlement_source_type", "") or "").strip() == "noaa":
        canonical = _fetch_canonical_day0_observation_from_instants(
            city,
            target_d,
            reference_time=reference_time,
        )
        if canonical is not None:
            return canonical
        raise ObservationUnavailableError(
            f"Canonical Day0 observation unavailable for "
            f"{getattr(city, 'name', '?')}/noaa/{target_d.isoformat()}"
        )
    try:
        return get_current_observation(city, target_date=target_d, reference_time=reference_time)
    except TypeError:
        return get_current_observation(city)


def _fetch_canonical_day0_observation_from_instants(
    city: object,
    target_d: date,
    *,
    reference_time: datetime,
) -> Day0ObservationContext | None:
    """Build an executable Day0 observation from canonical observation_instants.

    NOAA-settled cities do not have an observation_client live fetcher, but their
    settlement-station METAR rows are already persisted in the same canonical
    surface used by hard-fact Day0 triggers. This adapter feeds that source into
    the normal Day0Router math instead of falling back to stale replacement
    posteriors.
    """

    try:
        from src.data.day0_observation_reader import (
            COVERAGE_NONE,
            read_day0_observed_extrema,
        )
        from src.state.db import get_world_connection_read_only
    except Exception:
        return None

    city_name = str(getattr(city, "name", "") or "")
    timezone_name = str(getattr(city, "timezone", "") or "")
    unit = str(getattr(city, "settlement_unit", "C") or "C")
    if not city_name or not timezone_name:
        return None
    conn = None
    try:
        conn = get_world_connection_read_only()
        result = read_day0_observed_extrema(
            conn,
            city=city_name,
            target_date=target_d.isoformat(),
            timezone_name=timezone_name,
            decision_time_utc=reference_time,
        )
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if result.coverage_status == COVERAGE_NONE or result.chosen_source is None:
        return None
    if result.high_so_far is None or result.low_so_far is None:
        return None
    observation_time = result.last_observation_time_utc
    if not observation_time:
        return None
    current_temp = (
        float(result.current_temp)
        if result.current_temp is not None
        else float("nan")
    )
    return Day0ObservationContext(
        current_temp=current_temp,
        high_so_far=float(result.high_so_far),
        low_so_far=float(result.low_so_far),
        source=str(result.chosen_source),
        observation_time=observation_time,
        unit=unit,
        station_id=str(result.provenance.get("chosen_source") or result.chosen_source),
        sample_count=int(result.row_count),
        last_sample_time=observation_time,
        coverage_status=str(result.coverage_status),
        observation_available_at=str(result.decision_time_utc),
        provider_reported_time="canonical_observation_instants",
    )


def _temperature_native_value_to_c(value: float, *, unit: str) -> float:
    normalized = str(unit or "").strip().upper()
    number = float(value)
    if normalized == "C":
        return number
    if normalized == "F":
        return (number - 32.0) * 5.0 / 9.0
    raise ValueError(f"unsupported Day0 observed-extreme unit: {unit!r}")


def _day0_observed_extreme_from_canonical_surface(
    *,
    city_name: str,
    target_date: str,
    metric_is_low: bool,
    now: datetime | None = None,
    world_conn: sqlite3.Connection | None = None,
) -> tuple[float, str, int] | None:
    """Observed running extreme + its observation version from the canonical settlement-grade
    ``world.observation_instants`` surface — the SAME source the day0 hard-fact lane
    (``day0_hard_fact_exit._durable_observation_instants_extremes``) and the
    ``day0_extreme_updated`` trigger already treat as authoritative.

    Same-day exit-blindness fix 2026-06-23: the monitor belief reseed previously sourced the
    observed extreme ONLY from a live-provider fetch (``get_current_observation``) that routinely
    fails on the settlement day ("All observation providers failed for <city>/<date>"), starving
    the day0 conditioning while this canonical WU-hourly surface already held the verified running
    extreme (Toronto NO@24 -98.94% incident). Returns ``(observed_native, observation_time_iso,
    sample_count)``, or None when no VERIFIED WU row is available up to ``now``. ``world_conn`` is
    injectable for tests; otherwise a private short-lived read-only world connection is opened and
    closed (the position_belief read posture). See
    docs/evidence/same_day_exit_blindness/2026-06-23_toronto_total_loss.md.
    """
    extreme_col = "running_min" if metric_is_low else "running_max"
    agg = "MIN" if metric_is_low else "MAX"
    now_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    owns_conn = world_conn is None
    if owns_conn:
        try:
            from src.state.db import get_world_connection_read_only

            world_conn = get_world_connection_read_only()
        except Exception:  # noqa: BLE001 — read posture is best-effort; reseed continues without it
            return None
    try:
        # Prefer an ATTACHed authoritative ``world`` schema when present (composite connections);
        # fall back to unqualified ``observation_instants`` for the production case where the world
        # DB itself is opened as main (consult REQ-20260623-184115 LOW: precedence must not read a
        # stale main table over an attached authoritative world one).
        for table_ref in ("world.observation_instants", "observation_instants"):
            try:
                row = world_conn.execute(
                    f"""
                    SELECT {agg}(CAST({extreme_col} AS REAL)) AS extreme,
                           MAX(utc_timestamp) AS obs_time,
                           COUNT(*) AS n_rows
                    FROM {table_ref}
                    WHERE city = ?
                      AND target_date = ?
                      AND substr(local_timestamp, 1, 10) = target_date
                      AND utc_timestamp <= ?
                      AND COALESCE(causality_status, 'OK') = 'OK'
                      AND (
                            (
                                UPPER(COALESCE(authority, '')) = 'VERIFIED'
                                AND COALESCE(source_role, '') = 'historical_hourly'
                                AND COALESCE(training_allowed, 0) = 1
                                AND (
                                    LOWER(COALESCE(source, '')) LIKE 'wu%'
                                    OR LOWER(COALESCE(source, '')) LIKE 'ogimet_metar_%'
                                )
                            )
                            OR (
                                city = 'Hong Kong'
                                AND LOWER(COALESCE(source, '')) = 'hko_hourly_accumulator'
                                AND UPPER(COALESCE(authority, '')) = 'ICAO_STATION_NATIVE'
                                AND COALESCE(source_role, '') = 'runtime_monitoring'
                                AND COALESCE(training_allowed, 0) = 0
                            )
                      )
                      AND {extreme_col} IS NOT NULL
                    """,
                    (city_name, target_date, now_iso),
                ).fetchone()
            except Exception:  # noqa: BLE001 — missing attachment/table fails soft to the next ref
                continue
            if row is None:
                continue
            extreme = row["extreme"] if hasattr(row, "keys") else row[0]
            obs_time = row["obs_time"] if hasattr(row, "keys") else row[1]
            n_rows = int((row["n_rows"] if hasattr(row, "keys") else row[2]) or 0)
            if extreme is None or n_rows <= 0 or not obs_time:
                continue
            return float(extreme), str(obs_time), n_rows
        return None
    finally:
        if owns_conn:
            try:
                world_conn.close()
            except Exception:  # noqa: BLE001
                pass


def _compose_day0_observed_extreme(
    *,
    live: tuple[float, str, str, int] | None,
    canonical: tuple[float, str, int] | None,
    metric_is_low: bool,
) -> tuple[float, str, str, int] | None:
    """Compose live + canonical observed extremes by the ABSORBING LAW (consult
    REQ-20260623-184115 BLOCKER): the canonical settlement-grade surface is a HARD bound; a live
    reading may only IMPROVE the absorbing extreme (raise the high / lower the low), never undercut
    it. Returns ``(observed_native, observation_time_iso, source, sample_count)`` for the dominant
    source, with the LATER observation version on a tie so a fresh plateau still advances the
    idempotency version. None when neither source is available. A stale/lower live value therefore
    can NEVER suppress a higher canonical running extreme and materialise a fresh-but-wrong belief
    (the 9h staleness guard cannot catch a semantically false but timestamp-fresh posterior).
    ``live`` = (native, observation_time, source, sample_count); ``canonical`` = (native, time, n).
    """
    from src.data.replacement_cycle_advance_trigger import normalize_observation_version

    candidates: list[tuple[float, str, str, int]] = []
    if live is not None:
        candidates.append((float(live[0]), str(live[1]), str(live[2]), int(live[3])))
    if canonical is not None:
        candidates.append(
            (float(canonical[0]), str(canonical[1]), "durable_observation_instants", int(canonical[2]))
        )
    if not candidates:
        return None
    extreme = min(c[0] for c in candidates) if metric_is_low else max(c[0] for c in candidates)
    dominant = [c for c in candidates if c[0] == extreme]
    best = max(dominant, key=lambda c: normalize_observation_version(c[1]) or "")
    return (extreme, best[1], best[2], best[3])


def _apply_absorbing_floor_to_observed_extreme(
    raw_live: float | None,
    canonical_native: float | None,
    *,
    metric_is_low: bool,
) -> float | None:
    """Monotonic absorbing floor for the BELIEF's observed extreme (REQ-20260623-184115).

    The day0 belief samples ``settle(max(observed_high_so_far, future_member_max))`` (high) /
    ``min(...)`` (low), so the observed extreme is a hard floor/ceiling on the day's settle value.
    A later, LOWER live high (an evening METAR/station revision) must NEVER undercut the canonical
    running max — once the running max has exceeded a bin upper bound the bin is WON, and a lower
    reading cannot re-open it. The hard-fact/reseed path already composes live+canonical by this
    absorbing law (`_compose_day0_observed_extreme`); the belief was the last consumer still reading
    the raw revisable live value (Chicago 2026-06-25: high revised down -> won 76-77 bin re-opened ->
    belief 1.0->0.65 -> false SETTLEMENT_IMMINENT sale). Returns the absorbing extreme:
    ``max(live, canonical)`` for high, ``min(live, canonical)`` for low; non-finite values are
    dropped; ``raw_live`` is returned unchanged when no canonical floor is available.
    """

    candidates = [
        v for v in (raw_live, canonical_native)
        if v is not None and np.isfinite(float(v))
    ]
    if not candidates:
        return raw_live
    return min(candidates) if metric_is_low else max(candidates)


def _day0_observed_extreme_reseed_payload(
    *, city: str, target_date: str, metric: str
) -> dict[str, object]:
    city_obj = cities_by_name.get(str(city))
    if city_obj is None or not _city_supports_executable_day0_observation(city_obj):
        return {}
    try:
        target_d = date.fromisoformat(str(target_date))
    except Exception:
        return {}
    if not _is_position_target_local_day(None, city_obj, target_d):
        return {}
    try:
        metric_id = MetricIdentity.from_raw(metric)
    except Exception:
        return {}
    metric_is_low = metric_id.is_low()
    unit = str(getattr(city_obj, "settlement_unit", "") or "").strip().upper()

    # LIVE candidate: a validated live-provider reading (lowest latency when providers serve).
    live: tuple[float, str, str, int] | None = None
    obs = None
    try:
        obs = _fetch_day0_observation(city_obj, target_d)
    except Exception as exc:
        logger.info(
            "monitor belief reseed Day0 live observation unavailable city=%s target_date=%s "
            "metric=%s exc=%s (composing with canonical surface)",
            city, target_date, metric, exc,
        )
    if obs is not None and _day0_observation_field(obs, "observation_time"):
        source_rejection = _day0_observation_source_rejection_reason(
            city_obj,
            obs,
            consumer_label="replacement belief reseed",
        )
        if source_rejection is not None:
            logger.info(
                "monitor belief reseed Day0 live observation rejected city=%s target_date=%s "
                "metric=%s reason=%s (composing with canonical surface)",
                city, target_date, metric, source_rejection,
            )
        else:
            live_native = _finite_day0_observation_float(
                obs, "low_so_far" if metric_is_low else "high_so_far"
            )
            if live_native is not None:
                try:
                    live_sample = int(_day0_observation_field(obs, "sample_count", 0) or 0)
                except Exception:
                    live_sample = 0
                live = (
                    float(live_native),
                    str(_day0_observation_field(obs, "observation_time", "") or ""),
                    str(_day0_observation_field(obs, "source", "") or "live"),
                    live_sample,
                )

    # CANONICAL candidate (ALWAYS read): the settlement-grade world.observation_instants surface
    # the day0 hard-fact lane treats as authoritative. The live reading may only IMPROVE the
    # absorbing extreme, never undercut this hard bound — a stale/lower live value cannot suppress
    # the canonical running extreme and materialise a fresh-but-wrong belief (consult
    # REQ-20260623-184115 BLOCKER). See docs/evidence/same_day_exit_blindness/.
    canonical = _day0_observed_extreme_from_canonical_surface(
        city_name=str(getattr(city_obj, "name", "") or city),
        target_date=str(target_date),
        metric_is_low=metric_is_low,
    )
    composed = _compose_day0_observed_extreme(
        live=live, canonical=canonical, metric_is_low=metric_is_low
    )
    if composed is None:
        return {}
    observed_native, observation_time, source_label, sample_count = composed
    try:
        observed_c = _temperature_native_value_to_c(observed_native, unit=unit)
    except Exception as exc:
        logger.info(
            "monitor belief reseed Day0 observed-extreme unit conversion failed "
            "city=%s target_date=%s metric=%s unit=%s exc=%s",
            city, target_date, metric, unit, exc,
        )
        return {}
    return {
        "day0_observed_extreme_c": float(observed_c),
        "day0_observed_extreme_source": source_label,
        "day0_observed_extreme_observation_time": observation_time,
        "day0_observed_extreme_sample_count": sample_count,
        "day0_observed_extreme_unit": unit,
    }


def _is_stale_day0_observation_quality_rejection(reason: str | None) -> bool:
    return str(reason or "").startswith(_DAY0_STALE_OBSERVATION_REJECTION_PREFIX)


def _stale_day0_observation_can_remain_monitor_authority(
    *,
    quality_rejection: str | None,
    temperature_metric: MetricIdentity,
    temporal_context,
) -> bool:
    """Allow stale-but-valid Day0 bounds to keep held-position monitor authority.

    This is deliberately monitor-only. Entry decisions still require a fresh
    observation tick. Held positions need the latest known running high/low plus
    remaining-window forecast so the exit/redecision loop does not go blind
    merely because the settlement station has not emitted another hourly sample.
    """

    if not _is_stale_day0_observation_quality_rejection(quality_rejection):
        return False
    if not (temperature_metric.is_high() or temperature_metric.is_low()):
        return False
    if temporal_context is None:
        return False
    return bool(str(getattr(temporal_context, "daypart", "") or ""))


def _decision_local_hour_for_target(city, target_d: date, decision_time: datetime) -> float | None:
    try:
        decision_utc = decision_time if decision_time.tzinfo is not None else decision_time.replace(tzinfo=timezone.utc)
        decision_local = decision_utc.astimezone(ZoneInfo(str(city.timezone)))
    except Exception:
        return None
    if decision_local.date() != target_d:
        return None
    return (
        float(decision_local.hour)
        + float(decision_local.minute) / 60.0
        + float(decision_local.second) / 3600.0
    )


def _is_position_day0_quote_eligible(pos: Position) -> bool:
    if _position_state_value(pos) == "day0_window":
        return True
    city = cities_by_name.get(str(getattr(pos, "city", "") or ""))
    if city is None:
        return False
    if not _city_supports_executable_day0_observation(city):
        return False
    try:
        target_d = date.fromisoformat(str(getattr(pos, "target_date", "") or ""))
    except Exception:
        return False
    return _is_position_target_local_day(pos, city, target_d)


def _day0_one_sided_monitor_quote(conn, clob: PolymarketClient, pos: Position, token_id: str) -> HeldTokenMonitorQuote | None:
    if not _is_position_day0_quote_eligible(pos) or not hasattr(clob, "get_orderbook"):
        return None
    try:
        from src.data.market_scanner import _top_book_level_decimal

        book = clob.get_orderbook(token_id)
        best_bid = bid_size = None
        best_ask = ask_size = None
        try:
            best_bid, bid_size = _top_book_level_decimal(book, "bids")
        except Exception:  # noqa: BLE001 - one-sided books are valid day0 evidence
            pass
        try:
            best_ask, ask_size = _top_book_level_decimal(book, "asks")
        except Exception:  # noqa: BLE001 - bid-only books are valid day0 evidence
            pass

        if best_bid is None and best_ask is None:
            return None
        bid_f = float(best_bid) if best_bid is not None else 0.0
        bid_sz_f = float(bid_size) if bid_size is not None else 0.0
        ask_f = float(best_ask) if best_ask is not None else None
        ask_sz_f = float(ask_size) if ask_size is not None else 0.0
        if ask_f is not None and (not np.isfinite(ask_f) or ask_f <= 0.0):
            ask_f = None
            ask_sz_f = 0.0
        if not np.isfinite(bid_f) or bid_f < 0.0 or not np.isfinite(bid_sz_f) or bid_sz_f < 0.0:
            return None
        if bid_f <= 0.0 and ask_f is None:
            return None
        source_timestamp = datetime.now(timezone.utc).isoformat()
        try:
            from src.state.db import log_microstructure

            log_microstructure(
                conn,
                token_id=token_id,
                city=pos.city,
                target_date=pos.target_date,
                range_label=pos.bin_label,
                price=bid_f,
                volume=bid_sz_f + ask_sz_f,
                bid=bid_f,
                ask=ask_f,
                spread=(round(float(ask_f - bid_f), 4) if ask_f is not None and ask_f >= bid_f else None),
                source_timestamp=source_timestamp,
            )
        except Exception as exc:
            logger.debug("Day0 one-sided microstructure log failed for %s: %s", pos.trade_id, exc)
        return HeldTokenMonitorQuote(
            token_id=token_id,
            best_bid=bid_f,
            best_ask=ask_f,
            bid_size=bid_sz_f,
            ask_size=ask_sz_f,
            diagnostic_market_price=bid_f,
            source_timestamp=source_timestamp,
        )
    except Exception as exc:
        logger.debug("Day0 one-sided quote refresh failed for %s: %s", pos.trade_id, exc)
        return None


def monitor_quote_refresh(conn, clob: PolymarketClient, pos: Position) -> HeldTokenMonitorQuote | None:
    """Refresh held-token executable quote without feeding posterior belief."""

    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    if not tid:
        return None

    try:
        bid, ask, bid_sz, ask_sz = clob.get_best_bid_ask(tid)
        bid_f = float(bid)
        ask_f = float(ask)
        bid_sz_f = float(bid_sz)
        ask_sz_f = float(ask_sz)
        diagnostic_market_price = (
            bid_f
            if pos.state == "day0_window"
            else float(vwmp(bid_f, ask_f, bid_sz_f, ask_sz_f))
        )
        source_timestamp = datetime.now(timezone.utc).isoformat()

        try:
            # Injection Point 7: Data completeness - record microstructure snapshot.
            from src.state.db import log_microstructure

            log_microstructure(
                conn,
                token_id=tid,
                city=pos.city,
                target_date=pos.target_date,
                range_label=pos.bin_label,
                price=float(diagnostic_market_price),
                volume=float(bid_sz_f + ask_sz_f),
                bid=float(bid_f),
                ask=float(ask_f),
                spread=round(float(ask_f - bid_f), 4) if ask_f >= bid_f else 0.0,
                source_timestamp=source_timestamp,
            )
        except Exception as exc:
            logger.debug("Microstructure log failed for %s: %s", pos.trade_id, exc)
        return HeldTokenMonitorQuote(
            token_id=tid,
            best_bid=bid_f,
            best_ask=ask_f,
            bid_size=bid_sz_f,
            ask_size=ask_sz_f,
            diagnostic_market_price=diagnostic_market_price,
            source_timestamp=source_timestamp,
        )
    except Exception as e:
        one_sided_quote = _day0_one_sided_monitor_quote(conn, clob, pos, tid)
        if one_sided_quote is not None:
            return one_sided_quote
        logger.debug("VWMP refresh failed for %s: %s", pos.trade_id, e)
        return None


def _refresh_day0_observation(
    *,
    position: Position,
    current_p_market: float,
    conn,
    city,
    target_d,
) -> tuple[float, list[str]]:
    # Slice P2-fix5 (post-review MAJOR #5 from code-reviewer, 2026-04-26):
    # hoist resolver call to function entry. Pre-fix called
    # resolve_position_metric(position) at L323 (audit), L376 (Day0 exit
    # calibrator), L417 (K4 gate) — 3 sites. Hoist eliminates redundant
    # attribute lookups + collapses 3 identical DEBUG log lines per cycle
    # into 1 for missing-metric positions.
    _position_metric_str = resolve_position_metric(position)[0]
    """Recompute fresh probability through the Day0 observation + ENS path."""
    try:
        entry_provenance = position.selected_method or position.entry_method
    except AttributeError:
        entry_provenance = ""
    if not entry_provenance:
        logger.debug("Day0 monitor refresh missing entry provenance for %s", getattr(position, "trade_id", "?"))
    obs = _fetch_day0_observation(city, target_d)
    if obs is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation"]
    if not _day0_observation_field(obs, "observation_time"):
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation", "missing_observation_timestamp"]

    # R4: wrap the str from Position (portfolio boundary) into MetricIdentity
    # so Day0Signal receives the typed object, not a bare str.
    # Slice P2-fix1 (post-review BLOCKER from code-reviewer + critic M1,
    # 2026-04-26): split audit (via resolver) from value construction (via
    # MetricIdentity.from_raw direct). Pre-fix1, routing the value through
    # resolver coerced garbage strings ("HIGH", " low ", etc.) silently to
    # HIGH, removing MetricIdentity.from_raw's loud antibody. Now: resolver
    # emits DEBUG audit log (preserves P2-C2 visibility), but the actual
    # MetricIdentity comes from the raw position attribute so garbage still
    # raises ValueError at the typed-atom boundary.
    # _position_metric_str already bound at function entry (P2-fix5 hoist);
    # the resolver fired its audit log there. Construct MetricIdentity from
    # raw position attribute so garbage strings still raise (P2-fix1 antibody).
    temperature_metric = MetricIdentity.from_raw(
        getattr(position, "temperature_metric", "high")
    )

    source_rejection = _day0_observation_source_rejection_reason(
        city,
        obs,
        consumer_label="held-position monitor refresh",
    )
    if source_rejection is not None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, [
            "day0_observation",
            "observation_source_policy",
            source_rejection,
        ]

    coverage_validations: list[str] = []
    obs_coverage_status = str(_day0_observation_field(obs, "coverage_status", "") or "").strip().upper()
    if obs_coverage_status == "WINDOW_INCOMPLETE":
        coverage_validations.append("day0_observation_bound_only:coverage_window_incomplete")

    temporal_context = None
    decision_time = datetime.now(timezone.utc)
    decision_local_hour = _decision_local_hour_for_target(city, target_d, decision_time)
    quality_rejection = _day0_observation_quality_rejection_reason(
        city,
        obs,
        temperature_metric,
        decision_time=decision_time,
        allow_incomplete_window_bound=True,
    )
    if quality_rejection is not None:
        if _is_stale_day0_observation_quality_rejection(quality_rejection):
            try:
                from src.signal.diurnal import build_day0_temporal_context
                temporal_context = build_day0_temporal_context(
                    city.name,
                    target_d,
                    city.timezone,
                    current_local_hour=decision_local_hour,
                    observation_time=(
                        None
                        if decision_local_hour is not None
                        else _day0_observation_field(obs, "observation_time")
                    ),
                    observation_source=_day0_observation_field(obs, "source", ""),
                )
            except Exception:
                temporal_context = None
        if _stale_day0_observation_can_remain_monitor_authority(
            quality_rejection=quality_rejection,
            temperature_metric=temperature_metric,
            temporal_context=temporal_context,
        ):
            coverage_validations.append("day0_observation_stale_monitor_bound")
            coverage_validations.append(quality_rejection)
        else:
            _set_monitor_probability_fresh(position, False)
            return position.p_posterior, [
                "day0_observation",
                *coverage_validations,
                "observation_quality_gate",
                quality_rejection,
            ]

    if temporal_context is None:
        try:
            from src.signal.diurnal import build_day0_temporal_context
            temporal_context = build_day0_temporal_context(
                city.name,
                target_d,
                city.timezone,
                current_local_hour=decision_local_hour,
                observation_time=(
                    None
                    if decision_local_hour is not None
                    else _day0_observation_field(obs, "observation_time")
                ),
                observation_source=_day0_observation_field(obs, "source", ""),
            )
        except Exception:
            temporal_context = None

    if temporal_context is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation", "day0_live_forecast", "missing_solar_context"]

    low, high = _parse_temp_range(position.bin_label)
    if low is None and high is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation", "day0_live_forecast"]

    ens_result = _read_day0_hourly_vectors(city=city, target_d=target_d)
    live_forecast_source = "day0_hourly_vectors"
    if ens_result is not None:
        forecast_source_validations = _monitor_forecast_source_validations(ens_result)
        extrema, hours_remaining = remaining_member_extrema_for_day0(
            ens_result["members_hourly"],
            ens_result["times"],
            city.timezone,
            target_d,
            now=temporal_context.current_utc_timestamp,
            temperature_metric=temperature_metric,
        )
        if extrema is None:
            ens_result = None
    if ens_result is None:
        raw_extrema = _read_day0_raw_model_extrema(
            city=city,
            target_d=target_d,
            metric=temperature_metric.temperature_metric,
            now=temporal_context.current_utc_timestamp,
        )
        if raw_extrema is None:
            _set_monitor_probability_fresh(position, False)
            return position.p_posterior, [
                "day0_observation",
                *coverage_validations,
                "day0_live_forecast_unavailable",
            ]
        from src.signal.day0_extrema import RemainingMemberExtrema

        extrema = RemainingMemberExtrema.for_metric(
            raw_extrema["member_extrema"],
            temperature_metric,
        )
        hours_remaining = _local_hours_remaining(
            city,
            target_d,
            now=temporal_context.current_utc_timestamp,
        )
        live_forecast_source = "day0_raw_model_extrema"
        forecast_source_validations = [
            f"forecast_source_id:{raw_extrema['source_id']}",
            f"forecast_source_role:{raw_extrema['forecast_source_role']}",
            f"forecast_source_cycle_time:{raw_extrema['source_cycle_time']}",
        ]

    semantics = SettlementSemantics.for_city(city)
    observed_high_so_far = _finite_day0_observation_float(obs, "high_so_far")
    observed_low_so_far = _finite_day0_observation_float(obs, "low_so_far")
    current_temp = _finite_day0_observation_float(obs, "current_temp")
    # ABSORBING FLOOR (2026-06-25 "wrong exit"): the BELIEF's observed extreme must be MONOTONIC.
    # day0_high_distribution samples max(observed_high_so_far, future_max), so a later LOWER live
    # high (evening METAR/station revision) would drop the floor back into an already-WON max-bin and
    # spuriously collapse the belief (Chicago 1.0->0.65 -> false SETTLEMENT_IMMINENT sale). The
    # hard-fact/reseed path already composes live+canonical by the absorbing law (REQ-20260623-184115);
    # the belief was the last consumer still on the raw revisable live value. Wire the SAME canonical
    # running-extreme floor here (only the position's own metric — avoids a second world read).
    _belief_metric_is_low = temperature_metric.is_low()
    _belief_canonical_extreme = _day0_observed_extreme_from_canonical_surface(
        city_name=str(getattr(city, "name", "") or ""),
        target_date=str(target_d),
        metric_is_low=_belief_metric_is_low,
    )
    if _belief_canonical_extreme is not None:
        if _belief_metric_is_low:
            observed_low_so_far = _apply_absorbing_floor_to_observed_extreme(
                observed_low_so_far, _belief_canonical_extreme[0], metric_is_low=True
            )
        else:
            observed_high_so_far = _apply_absorbing_floor_to_observed_extreme(
                observed_high_so_far, _belief_canonical_extreme[0], metric_is_low=False
            )
    observation_source_for_value = str(_day0_observation_field(obs, "source", "") or "")
    if current_temp is None and observation_source_for_value.startswith("ogimet_metar_"):
        current_temp = float("nan")
    if current_temp is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, [
            "day0_observation",
            live_forecast_source,
            "observation_quality_gate",
        ]
    member_extrema_for_metric = extrema.mins if temperature_metric.is_low() else extrema.maxes
    observed_extreme_for_metric = observed_low_so_far if temperature_metric.is_low() else observed_high_so_far
    maturity_rejection = _day0_extreme_authority_rejection_reason(
        temperature_metric=temperature_metric,
        temporal_context=temporal_context,
        hours_remaining=hours_remaining,
        observed_extreme_so_far=observed_extreme_for_metric,
        member_extrema_remaining=member_extrema_for_metric,
    )
    maturity_validations: list[str] = []
    if maturity_rejection is not None:
        # Non-absorbing Day0 observations are still valid probability evidence:
        # Day0Router combines the observed-so-far bound with remaining live hourly
        # vectors. The maturity gate only withholds hard-fact/absorbing authority;
        # it must not blind the held-position redecision loop.
        maturity_validations = [
            "day0_extreme_not_absorbing",
            maturity_rejection,
        ]
    day0 = Day0Router.route(Day0SignalInputs(
        temperature_metric=temperature_metric,
        observed_high_so_far=observed_high_so_far,
        observed_low_so_far=observed_low_so_far,
        current_temp=current_temp,
        hours_remaining=hours_remaining,
        member_maxes_remaining=extrema.maxes,
        member_mins_remaining=extrema.mins,
        unit=city.settlement_unit,
        observation_source=str(_day0_observation_field(obs, "source", "")),
        observation_time=_day0_observation_field(obs, "observation_time"),
        temporal_context=temporal_context,
        round_fn=semantics.round_values,
        precision=semantics.precision,
    ))
    # S6: Build full bin vector for calibrate_and_normalize (same path as entry)
    try:
        all_bins, held_idx = _build_all_bins(position, city)
    except ValueError as exc:
        logger.warning("Day0 monitor support topology unavailable for %s: %s", position.market_id, exc)
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, [
            "day0_observation",
            live_forecast_source,
            *forecast_source_validations,
            "support_topology_stale",
            str(exc),
        ]

    p_raw_vector = day0.p_vector(all_bins, n_mc=day0_n_mc())

    # U1/U2 regime-unification law: Day0 is observation authority plus the
    # remaining-window raw snapshot. Do not resurrect legacy ENS+Platt monitor
    # calibration here; normalize the raw vector honestly and mark it as such.
    p_cal_full = np.asarray(p_raw_vector, dtype=float)
    p_cal_sum = float(p_cal_full.sum())
    if p_cal_sum <= 0.0 or not np.isfinite(p_cal_full).all():
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, [
            "day0_observation",
            live_forecast_source,
            *forecast_source_validations,
            "day0_honest_raw_invalid_p_raw",
        ]
    p_cal_full = p_cal_full / p_cal_sum
    p_cal_yes = float(p_cal_full[held_idx])
    applied = [
        "day0_observation",
        *coverage_validations,
        live_forecast_source,
        *forecast_source_validations,
        *maturity_validations,
        "mc_instrument_noise",
        "day0_remaining_window_raw_vector_normalization",
    ]

    member_extrema = extrema.mins if temperature_metric.is_low() else extrema.maxes
    if member_extrema is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, [
            "day0_observation",
            live_forecast_source,
            "metric_extrema_missing",
        ]

    # Day0 observation remaining-window belief is not legacy alpha blending.
    # The probability authority is the observed-so-far bound plus remaining
    # hourly extrema, normalized in settlement-bin space. Market quotes and
    # hold-age alpha are therefore inapplicable to this belief.
    alpha = 1.0
    p_cal_native = _held_side_probability_from_yes_bin_probability(
        p_cal_yes,
        position.direction,
    )
    current_p_posterior = _model_only_native_posterior(p_cal_native)

    # A1: Stash bootstrap-relevant data for fresh CI computation in refresh_position
    setattr(position, "_bootstrap_context", {
        "p_raw": p_raw_vector,
        "p_cal": p_cal_full,
        "alpha": alpha,
        "bins": all_bins,
        "held_idx": held_idx,
        "member_extrema": extrema.maxes if extrema.maxes is not None else extrema.mins,
        "calibrator": None,
        "lead_days": 0.0,
        "unit": city.settlement_unit,
        "bootstrap_signal_type": SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW,
    })

    _stamp_day0_remaining_window_belief(
        position,
        metric=temperature_metric.temperature_metric,
    )
    _set_monitor_probability_fresh(position, True)

    # T5 nowcast wiring (Phase 2 T5): gate on market_slug + hours_remaining.
    # write_nowcast_run called when fit is available; fail-soft on any write error.
    _maybe_write_day0_nowcast(
        position=position,
        hours_remaining=hours_remaining,
        temporal_context=temporal_context,
        p_cal_full=p_cal_full,
        p_raw_vector=p_raw_vector,
        temperature_metric=temperature_metric,
        target_d=target_d,
        observation_time=_day0_observation_field(obs, "observation_time"),
        # ThePath P1 ITEM 1: honest obs-availability clock from the live obs ctx
        # (observation_client stamps it = now()-at-fetch). Read verbatim; the
        # helper records NULL + 'UNVERIFIED' when absent (never synthesizes now()).
        observation_available_at=_day0_observation_field(obs, "observation_available_at"),
    )

    return current_p_posterior, [
        *applied,
        *_day0_remaining_window_belief_validations(
            temperature_metric.temperature_metric,
        ),
    ]


def _day0_extreme_authority_rejection_reason(
    *,
    temperature_metric: MetricIdentity,
    temporal_context,
    hours_remaining: float,
    observed_extreme_so_far: float | None,
    member_extrema_remaining,
) -> str | None:
    """Reject Day0 observation authority before the daily extreme is causal.

    Running high/low observations are useful signal inputs, but they are not
    automatically exit authority. A non-deterministic HIGH running max before
    the peak and a non-terminal LOW running min near local midnight are both
    early-day bounds, not settlement-grade reversals.
    """
    try:
        classification = classify_bound(
            observed_extreme_so_far=observed_extreme_so_far,
            member_extremes_remaining=list(member_extrema_remaining)
            if member_extrema_remaining is not None
            else None,
            is_high_market=temperature_metric.is_high(),
        )
    except ValueError as exc:
        return f"day0_extreme_maturity_unavailable:{exc}"

    if classification == BoundClassification.UNBOUNDED_NO_OBS_YET:
        return "day0_extreme_maturity_unavailable:no_intraday_extreme"
    if classification == BoundClassification.DETERMINISTIC:
        return None

    if temperature_metric.is_high():
        daypart = str(getattr(temporal_context, "daypart", "") or "")
        post_peak_confidence = float(getattr(temporal_context, "post_peak_confidence", 0.0) or 0.0)
        if daypart != "post_peak" or post_peak_confidence < 0.5:
            return (
                "day0_high_extreme_not_mature:"
                f"daypart={daypart or 'unknown'},post_peak_confidence={post_peak_confidence:.3f}"
            )
        return None

    if float(hours_remaining) > _DAY0_LOW_EXTREME_AUTHORITY_HOURS:
        return f"day0_low_extreme_not_terminal:hours_remaining={float(hours_remaining):.1f}"
    return None


def _maybe_write_day0_nowcast(
    *,
    position: "Position",
    hours_remaining: float,
    temporal_context: object,
    p_cal_full: "np.ndarray",
    p_raw_vector: "np.ndarray",
    temperature_metric: "MetricIdentity",
    target_d: "date",
    observation_time: "str | None",
    observation_available_at: "str | None" = None,
) -> None:
    """Attempt a day0_nowcast_runs write for a canonical market slug when
    hours_remaining <= 6.  Fail-soft: any write error is logged as WARNING
    and swallowed so the monitor loop is never interrupted.

    observation_available_at: pre-extracted from the live Day0ObservationContext
        (observation_client.Day0ObservationContext.observation_available_at =
        now()-at-fetch). ThePath P1 ITEM 1 (2026-06-07): threaded to persist the
        honest obs-availability clock per nowcast run. When absent/empty, the
        write records NULL + provenance 'UNVERIFIED' — NEVER a synthesized now().
        Default None keeps every existing call signature valid.

    Guards:
      - position.market_slug must be present or uniquely resolvable from
        forecast-class market_events using exact persisted position identities.
      - hours_remaining must be <= 6 (G8c: within the terminal nowcast window).
      - temporal_context must be non-None (daypart requires it).
      - observation_time must be non-empty.
      - read_latest_platt_fit() must return a fit (skipped silently before
        first calibration run).

    Phase 2 T5 GREEN: calls write_nowcast_run with live fit_run_id from
    day0_horizon_platt_fits.
    """
    market_slug = str(getattr(position, "market_slug", None) or "").strip()
    if hours_remaining > 6:
        return
    if temporal_context is None:
        return
    if not observation_time:
        return
    _metric_str = (
        temperature_metric.temperature_metric
        if hasattr(temperature_metric, "temperature_metric")
        else str(temperature_metric)
    )

    try:
        from src.state.day0_nowcast_store import (  # noqa: PLC0415
            ensure_identity_platt_fit,
            read_latest_platt_fit,
            resolve_market_slug_for_position_identity,
            write_nowcast_run,
        )

        if not market_slug:
            market_slug = (
                resolve_market_slug_for_position_identity(
                    token_id=getattr(position, "token_id", None),
                    condition_id=getattr(position, "condition_id", None),
                    market_id=getattr(position, "market_id", None),
                    city=getattr(position, "city", None),
                    target_date=getattr(position, "target_date", None),
                    temperature_metric=_metric_str,
                    bin_label=getattr(position, "bin_label", None),
                )
                or ""
            )
            if not market_slug:
                logger.debug(
                    "T5 nowcast: no unique canonical market_slug for %s "
                    "token_id=%s condition_id=%s city=%s target_date=%s metric=%s",
                    getattr(position, "trade_id", "?"),
                    getattr(position, "token_id", None),
                    getattr(position, "condition_id", None),
                    getattr(position, "city", None),
                    getattr(position, "target_date", None),
                    _metric_str,
                )
                return

        fit = read_latest_platt_fit()
        if fit is None:
            fit = ensure_identity_platt_fit()
            if fit is None:
                logger.debug(
                    "T5 nowcast: no platt fit available yet for %s — skipping write",
                    market_slug,
                )
                return

        # ThePath P1 ITEM 1: thread the honest obs-availability clock. The live
        # observation_client stamps observation_available_at = now()-at-fetch.
        # Treat empty string (the contract default when no fetch produced one) as
        # absent -> NULL + 'UNVERIFIED'. NEVER synthesize now() here.
        _obs_avail = observation_available_at or None
        _obs_provenance = "live_fetch" if _obs_avail else "UNVERIFIED"

        write_nowcast_run(
            market_slug=market_slug,
            temperature_metric=_metric_str,
            target_date=target_d.isoformat(),
            observation_time=observation_time,
            fit_run_id=fit.fit_run_id,
            p_nowcast=p_cal_full,
            p_now_raw=p_raw_vector,
            hours_remaining=hours_remaining,
            daypart=temporal_context.daypart,
            source="live_nowcast",
            observation_available_at=_obs_avail,
            obs_availability_provenance=_obs_provenance,
        )
        logger.debug(
            "T5 nowcast write OK: %s market_slug=%s hours_remaining=%.1f daypart=%s fit_run_id=%s",
            getattr(position, "trade_id", "?"),
            market_slug,
            hours_remaining,
            temporal_context.daypart,
            fit.fit_run_id,
        )
        _record_nowcast_write_success()
    except Exception as exc:  # noqa: BLE001
        _record_nowcast_write_failure(
            market_slug=market_slug or str(getattr(position, "market_slug", "?") or "?"),
            trade_id=str(getattr(position, "trade_id", "?") or "?"),
        )
        logger.warning(
            "T5 nowcast write FAILED (non-fatal) for %s market_slug=%s: %s",
            getattr(position, "trade_id", "?"),
            market_slug or getattr(position, "market_slug", "?"),
            exc,
            exc_info=True,
        )


def _delta_bucket(delta: float) -> str:
    if abs(delta) <= 1:
        return "-1 to 1"
    elif -3 <= delta < -1:
        return "-3 to -1"
    elif -5 <= delta < -3:
        return "-5 to -3"
    elif -10 <= delta < -5:
        return "-10 to -5"
    elif delta < -10:
        return "<-10"
    elif 1 < delta <= 3:
        return "1 to 3"
    elif 3 < delta <= 5:
        return "3 to 5"
    elif 5 < delta <= 10:
        return "5 to 10"
    else:
        return ">10"


def _check_persistence_anomaly(
    conn, city_name: str, target_date, predicted_high: float,
    *, temperature_metric=None,
) -> float:
    """Check if ENS-predicted temp change from recent days is historically rare.

    Looks at the last 3 days of settlements and averages the delta to smooth out
    single-day noise. Discount is confidence-scaled by sample size:
    - n < 30: not enough data → no discount
    - n=30: 10% discount
    - n=100+: 30% max discount

    LOW metric gate: legacy settlements has no metric column; LOW lookups would
    cross-compare against HIGH historical values. Defer to metric-aware query
    when settlement_outcomes populated (P10D).
    """
    if temperature_metric is not None:
        is_low = (
            getattr(temperature_metric, "is_low", lambda: False)()
            or temperature_metric == "low"
        )
        if is_low:
            return 1.0  # no persistence discount for LOW

    from datetime import timedelta

    try:
        from src.calibration.manager import season_from_date, lat_for_city
        season = season_from_date(target_date.isoformat(), lat=lat_for_city(city_name))

        # Average delta over last 3 available settlement days
        deltas = []
        for days_back in range(1, 4):
            d = (target_date - timedelta(days=days_back)).isoformat()
            # H3 (2026-04-24): pin temperature_metric='high' explicitly.
            # LOW callers early-return at L453-459 before reaching this query,
            # so the HIGH filter is safe: any caller reaching this SELECT has
            # already committed to the HIGH axis (via explicit HIGH
            # temperature_metric kwarg, or the default pre-dual-track path).
            # Without the filter, a future LOW settlement row for the same
            # (city, target_date) would silently match and produce a cross-
            # metric delta anyway.
            row = conn.execute(
                "SELECT settlement_value FROM forecasts.settlement_outcomes "
                "WHERE city = ? AND target_date = ? "
                "AND temperature_metric = 'high' "
                "AND authority = 'VERIFIED' LIMIT 1",
                (city_name, d),
            ).fetchone()
            if row and row["settlement_value"] is not None:
                # Note: uses WMO half-up as generic directional delta.
                # oracle_truncate precision not critical here (±0.5 max).
                deltas.append(
                    predicted_high - round_wmo_half_up_value(float(row["settlement_value"]))
                )

        if not deltas:
            logger.warning(
                "PERSISTENCE_FALLBACK_TRIGGERED: all 3 recent settlement days NULL "
                "in forecasts.settlement_outcomes for %s/%s — returning 1.0 (no discount)",
                city_name, target_date,
            )
            return 1.0

        delta = sum(deltas) / len(deltas)
        bucket = _delta_bucket(delta)

        freq_row = conn.execute(
            "SELECT frequency, n_samples FROM world.temp_persistence "
            "WHERE city = ? AND season = ? AND delta_bucket = ?",
            (city_name, season, bucket),
        ).fetchone()

        if freq_row and freq_row["frequency"] < 0.05:
            n = freq_row["n_samples"]
            if n < 30:
                return 1.0  # Too few samples to trust the frequency estimate
            # Scale discount: 10% at n=30, grows linearly to 30% at n>=100
            discount_magnitude = min(0.30, 0.10 + 0.20 * (n - 30) / 70.0)
            # Remaining multiplier after the discount is (1 - discount_magnitude);
            # de-obfuscated from the value-identical (1/x - 1) * x (§0.2 / FIX-5a).
            return one_minus(discount_magnitude)
        else:
            logger.debug(
                "PERSISTENCE_NO_DATA: world.temp_persistence has no row for %s/%s/bucket=%s — returning 1.0 (no discount)",
                city_name, target_date, bucket,
            )

    except Exception as e:
        logger.debug("Persistence anomaly check failed for %s: %s", city_name, e)

    return 1.0


from src.contracts.edge_context import EdgeContext


def _append_monitor_validation(position: Position, validation: str) -> None:
    validations = list(getattr(position, "applied_validations", []) or [])
    if validation not in validations:
        validations.append(validation)
    position.applied_validations = validations


def _bin_sort_key(outcome: dict) -> tuple[int, float]:
    low = outcome.get("range_low")
    high = outcome.get("range_high")
    if low is None and high is None:
        return (1, float("inf"))
    if low is None:
        return (0, float(high))
    return (0, float(low))


def _adjacent_sibling_outcomes(position: Position, siblings: list[dict]) -> list[dict]:
    """Return tradable weather bins adjacent to the held bin within one event."""

    if not position.market_id:
        return []
    ordered = [
        outcome for outcome in sorted(siblings, key=_bin_sort_key)
        if outcome.get("range_low") is not None or outcome.get("range_high") is not None
    ]
    held_index = next(
        (idx for idx, outcome in enumerate(ordered) if outcome.get("market_id") == position.market_id),
        None,
    )
    if held_index is None:
        return []
    adjacent: list[dict] = []
    if held_index > 0:
        adjacent.append(ordered[held_index - 1])
    if held_index + 1 < len(ordered):
        adjacent.append(ordered[held_index + 1])
    return adjacent


def _recent_price_delta(
    conn,
    *,
    token_id: str,
    current_price: float,
    now: datetime,
    lookback_hours: float = _WHALE_TOXICITY_LOOKBACK_HOURS,
) -> float | None:
    if conn is None or not token_id:
        return None
    try:
        lookback = (now - timedelta(hours=lookback_hours)).isoformat()
        row = conn.execute(
            """
            SELECT price
            FROM token_price_log
            WHERE token_id = ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (token_id, lookback),
        ).fetchone()
        if row is None:
            return None
        return float(current_price) - float(row["price"])
    except Exception as exc:
        logger.debug("Whale-toxicity price delta unavailable for token=%s: %s", token_id, exc)
        return None


def _detect_whale_toxicity_from_orderbook(
    conn,
    clob,
    position: Position,
    *,
    held_best_bid: float | None,
    held_best_ask: float | None,
    now: datetime | None = None,
) -> bool | None:
    """Detect adjacent-bin orderbook pressure for held YES positions.

    This is deliberately narrower than a true market-wide trade-sweep detector:
    Zeus currently has no market-level trade stream producer.  A signal is
    raised only when VERIFIED sibling bins plus fresh CLOB top-book facts show a
    large adjacent YES bid with enough visible depth.  Missing facts stay
    unknown (`None`) so the exit evidence does not pretend the detector ran.
    """

    if position.direction != "buy_yes":
        _append_monitor_validation(position, "whale_toxicity_not_applicable:buy_no")
        return False
    if conn is None:
        return None
    if clob is None or not position.market_id or held_best_bid is None:
        _append_monitor_validation(position, "whale_toxicity_unavailable:missing_market_facts")
        return None

    try:
        siblings = get_sibling_outcomes(position.market_id)
        if str(get_last_scan_authority()).upper() != "VERIFIED":
            _append_monitor_validation(position, "whale_toxicity_unavailable:market_scan_not_verified")
            return None
    except Exception as exc:
        logger.debug("Whale-toxicity sibling scan failed for %s: %s", position.trade_id, exc)
        _append_monitor_validation(position, "whale_toxicity_unavailable:sibling_scan_failed")
        return None

    adjacent = _adjacent_sibling_outcomes(position, siblings)
    if not adjacent:
        _append_monitor_validation(position, "whale_toxicity_unavailable:no_adjacent_bins")
        return None

    observed = False
    basis_price = float(held_best_ask if held_best_ask is not None else held_best_bid)
    effective_cost_basis = float(getattr(position, "effective_cost_basis_usd", 0.0) or 0.0)
    if getattr(position, "has_fill_economics_authority", False):
        position_notional = max(_WHALE_TOXICITY_MIN_NOTIONAL_USD, effective_cost_basis)
    else:
        position_notional = max(
            _WHALE_TOXICITY_MIN_NOTIONAL_USD,
            effective_cost_basis,
            float(getattr(position, "size_usd", 0.0) or 0.0),
        )
    now_utc = now or datetime.now(timezone.utc)

    for outcome in adjacent:
        adjacent_token = str(outcome.get("token_id") or "").strip()
        if not adjacent_token:
            continue
        try:
            adj_bid, adj_ask, adj_bid_size, _adj_ask_size = clob.get_best_bid_ask(adjacent_token)
        except Exception as exc:
            logger.debug(
                "Whale-toxicity adjacent book unavailable for trade=%s token=%s: %s",
                position.trade_id,
                adjacent_token,
                exc,
            )
            continue

        observed = True
        adjacent_notional = float(adj_bid) * float(adj_bid_size)
        current_mid = (float(adj_bid) + float(adj_ask)) / 2.0
        prior_delta = _recent_price_delta(
            conn,
            token_id=adjacent_token,
            current_price=current_mid,
            now=now_utc,
        )
        has_sufficient_depth = adjacent_notional >= position_notional
        has_recent_surge = (
            prior_delta is not None
            and prior_delta >= _WHALE_TOXICITY_PRICE_MARGIN
            and float(adj_bid) >= basis_price + _WHALE_TOXICITY_PRICE_MARGIN
        )
        has_severe_static_pressure = (
            prior_delta is None
            and float(adj_bid) >= basis_price + _WHALE_TOXICITY_SEVERE_PRICE_MARGIN
            and adjacent_notional >= position_notional * 2.0
        )
        if has_sufficient_depth and (has_recent_surge or has_severe_static_pressure):
            _append_monitor_validation(
                position,
                "whale_toxicity_available:adjacent_orderbook_pressure",
            )
            return True

    if observed:
        _append_monitor_validation(position, "whale_toxicity_available:clear")
        return False

    _append_monitor_validation(position, "whale_toxicity_unavailable:adjacent_orderbook_missing")
    return None


def _day0_absorbing_hard_fact_overlay(
    *,
    pos: Position,
    conn,
    city,
    target_d,
) -> tuple[float, Position, bool] | None:
    """Return exact monitor belief when a qualified Day0 hard fact is absorbing."""

    if not _is_position_target_local_day(pos, city, target_d):
        return None
    metric = str(getattr(pos, "temperature_metric", "") or "").strip().lower()
    if metric not in {"high", "low"}:
        return None
    try:
        from src.execution.day0_hard_fact_exit import (
            evaluate_hard_fact_exit,
            hard_fact_monitor_belief,
        )

        verdict = evaluate_hard_fact_exit(
            position=pos,
            city=city,
            now=datetime.now(timezone.utc),
            world_conn=conn,
        )
        if verdict is None:
            return None
        belief = hard_fact_monitor_belief(
            verdict=verdict,
            direction=getattr(pos, "direction", ""),
        )
        if belief is None:
            return None
    except Exception as exc:  # noqa: BLE001 - hard-fact overlay must fail soft
        logger.warning(
            "monitor_probability_refresh: day0 hard-fact overlay failed for %s: %s",
            getattr(pos, "trade_id", "?"),
            exc,
        )
        return None

    hard_pos = replace(pos)
    setattr(hard_pos, "selected_method", SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
    _append_monitor_validation(hard_pos, SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
    _append_monitor_validation(
        hard_pos,
        (
            "belief_source=day0_absorbing_hard_fact;"
            "kind=deterministic_absorbing;"
            f"metric={verdict.metric};"
            f"yes_verdict={belief.yes_verdict};"
            f"held_verdict={belief.held_verdict};"
            f"yes_prob={belief.yes_prob:.6f};"
            f"held_prob={belief.held_side_prob:.6f};"
            f"effective_extreme={verdict.rounded_extreme:g};"
            f"source={verdict.source or 'unknown'}"
        ),
    )
    if belief.held_verdict == "STRUCTURAL_WIN":
        _append_monitor_validation(hard_pos, "day0_hard_fact_structural_win_hold")
    else:
        _append_monitor_validation(hard_pos, "day0_hard_fact_structural_loss")
    _append_monitor_validation(
        hard_pos,
        "model_divergence_panic_inapplicable:day0_absorbing_hard_fact",
    )
    _append_monitor_validation(
        hard_pos,
        "forecast_posteriors_dominated_by_day0_hard_fact",
    )
    _set_monitor_probability_fresh(hard_pos, True)
    return float(belief.held_side_prob), hard_pos, True


def _would_use_day0_monitor_lane(pos: Position, city, target_d) -> bool:
    """Whether same-day monitor probability must be observation-aware Day0 belief."""

    return (
        pos.entry_method == EntryMethod.DAY0_OBSERVATION.value
        or (
            _position_state_value(pos) == "day0_window"
            and _city_supports_executable_day0_observation(city)
        )
        or (
            _is_position_target_local_day(pos, city, target_d)
            and _city_supports_executable_day0_observation(city)
        )
    )


def _refresh_day0_monitor_probability(
    pos: Position,
    *,
    conn,
    city,
    target_d,
) -> tuple[float, Position, bool | None]:
    """Refresh same-day held probability from Day0 observation remaining-window."""

    registry = {
        EntryMethod.ENS_MEMBER_COUNTING.value: _refresh_ens_member_counting,
        EntryMethod.QKERNEL_SPINE.value: _refresh_ens_member_counting,
        EntryMethod.DAY0_OBSERVATION.value: _refresh_day0_observation,
    }
    refresh_pos = pos
    if pos.entry_method != EntryMethod.DAY0_OBSERVATION.value:
        refresh_pos = copy.copy(pos)
        refresh_pos.entry_method = EntryMethod.DAY0_OBSERVATION.value
    setattr(refresh_pos, _MONITOR_PROBABILITY_FRESH_ATTR, None)

    # recompute_native_probability still carries a legacy current_p_market
    # parameter for dispatch compatibility. Do not pass the just-refreshed
    # executable quote through this seam.
    probability_reference_price = float(getattr(pos, "entry_price", 0.0) or 0.0)
    try:
        current_p_posterior = recompute_native_probability(
            refresh_pos,
            current_p_market=probability_reference_price,
            registry=registry,
            conn=conn,
            city=city,
            target_d=target_d,
        )
    except ObservationUnavailableError:
        metric = resolve_position_metric(pos)[0]
        from src.engine.position_belief import (
            SELECTED_METHOD_REPLACEMENT_POSTERIOR,
            load_replacement_belief,
            monitor_belief_max_age_hours,
        )

        try:
            belief = load_replacement_belief(
                city=pos.city,
                target_date=pos.target_date,
                temperature_metric=metric,
                bin_label=pos.bin_label,
                direction=str(getattr(pos.direction, "value", pos.direction)),
                max_age_hours=monitor_belief_max_age_hours(),
            )
        except Exception as exc:  # noqa: BLE001 - fallback must stay fail-soft
            belief = None
            logger.debug(
                "day0 observation unavailable replacement fallback read failed for %s: %s",
                getattr(pos, "trade_id", "?"),
                exc,
            )
        if belief is not None and belief.fresh:
            fresh_pos = copy.copy(pos)
            setattr(fresh_pos, "selected_method", SELECTED_METHOD_REPLACEMENT_POSTERIOR)
            _append_monitor_validation(
                fresh_pos,
                "day0_observation_unavailable:replacement_posterior_fresh",
            )
            _append_monitor_validation(fresh_pos, belief.freshness_validation())
            _set_monitor_probability_fresh(fresh_pos, True)
            return float(belief.held_side_prob), fresh_pos, True

        readthrough_prob = _attempt_held_belief_readthrough(
            pos, city=city, target_d=target_d, metric=metric
        )
        if readthrough_prob is not None:
            fresh_pos = copy.copy(pos)
            setattr(fresh_pos, "selected_method", SELECTED_METHOD_REPLACEMENT_POSTERIOR)
            _append_monitor_validation(
                fresh_pos,
                "day0_observation_unavailable:replacement_belief_readthrough",
            )
            _append_monitor_validation(
                fresh_pos,
                "belief_source=forecast_posteriors_readthrough_recompute;basis=canonical_bayes_precision_fusion",
            )
            _set_monitor_probability_fresh(fresh_pos, True)
            return float(readthrough_prob), fresh_pos, True

        _set_monitor_probability_fresh(refresh_pos, False)
        _append_monitor_validation(
            refresh_pos,
            "day0_observation_unavailable:replacement_belief_reseed",
        )
        _enqueue_single_family_belief_reseed_failsoft(
            city=str(pos.city),
            target_date=str(pos.target_date),
            metric=metric,
        )
        return pos.p_posterior, refresh_pos, False

    if getattr(refresh_pos, _MONITOR_PROBABILITY_FRESH_ATTR, None) is True:
        try:
            _day0_metric = MetricIdentity.from_raw(
                getattr(refresh_pos, "temperature_metric", None)
            ).temperature_metric
        except Exception:
            _day0_metric = None
        _stamp_day0_remaining_window_belief(refresh_pos, metric=_day0_metric)
    else:
        _append_monitor_validation(
            refresh_pos,
            "day0_observation_unavailable:replacement_belief_reseed",
        )
        _enqueue_single_family_belief_reseed_failsoft(
            city=str(pos.city),
            target_date=str(pos.target_date),
            metric=resolve_position_metric(pos)[0],
        )
    return (
        current_p_posterior,
        refresh_pos,
        getattr(refresh_pos, _MONITOR_PROBABILITY_FRESH_ATTR, None),
    )


def monitor_probability_refresh(
    pos: Position,
    *,
    conn,
    city,
    target_d,
) -> tuple[float, Position, bool | None]:
    """Refresh held-side posterior without consuming the held-token quote.

    PRIMARY AUTHORITY: a qualified Day0 absorbing hard fact is exact and
    dominates model belief. When no absorbing hard fact exists, the K1 single
    belief authority is the replacement-chain posterior
    (``forecast_posteriors``), the SAME authority the entry decision used. The
    legacy ens/day0 refreshers below remain as explicit fallback telemetry only;
    they cannot be the freshness authority while a fresh replacement row exists.
    This removes the entry-belief vs exit-belief twin-authority failure mode
    without encoding any current live-position coverage claim in source comments.
    """
    hard_fact_overlay = _day0_absorbing_hard_fact_overlay(
        pos=pos,
        conn=conn,
        city=city,
        target_d=target_d,
    )
    if hard_fact_overlay is not None:
        return hard_fact_overlay

    if _would_use_day0_monitor_lane(pos, city, target_d):
        return _refresh_day0_monitor_probability(
            pos,
            conn=conn,
            city=city,
            target_d=target_d,
        )

    from src.engine.position_belief import (
        SELECTED_METHOD_REPLACEMENT_POSTERIOR,
        load_replacement_belief,
        monitor_belief_max_age_hours,
    )

    try:
        belief = load_replacement_belief(
            city=pos.city,
            target_date=pos.target_date,
            temperature_metric=str(getattr(pos, "temperature_metric", "high")),
            bin_label=pos.bin_label,
            direction=str(getattr(pos.direction, "value", pos.direction)),
            max_age_hours=monitor_belief_max_age_hours(),
        )
    except Exception as exc:  # noqa: BLE001 — belief read must not kill the monitor
        belief = None
        logger.warning(
            "monitor_probability_refresh: replacement belief read failed for %s: %s",
            pos.trade_id,
            exc,
        )
    if belief is not None and belief.fresh:
        fresh_pos = replace(pos)
        setattr(fresh_pos, "selected_method", SELECTED_METHOD_REPLACEMENT_POSTERIOR)
        _append_monitor_validation(fresh_pos, SELECTED_METHOD_REPLACEMENT_POSTERIOR)
        _append_monitor_validation(fresh_pos, belief.freshness_validation())
        _set_monitor_probability_fresh(fresh_pos, True)
        return float(belief.held_side_prob), fresh_pos, True
    if belief is not None:
        _append_monitor_validation(
            pos, f"replacement_posterior_stale;age_h={belief.age_hours:.2f}"
        )
    else:
        _append_monitor_validation(pos, "replacement_posterior_missing")

    # BELIEF-AUTHORITY FAULT (regime law U1/U2, 2026-06-12): a position whose
    # replacement belief is stale/missing must NOT have the gap papered over by
    # the legacy ENS forecast belief (the Denver 2026-06-12 incident: stale 0.79
    # masked as fresh while the market said 0.22). For these positions we (a) mark
    # belief NOT-fresh, (b) emit BELIEF_AUTHORITY_FAULT, (c) fire a fail-soft
    # single-family reseed so the SAME authority refreshes next cycle — and return
    # WITHOUT the cross-era substitution. The day0 nowcast lane is exempt (it is
    # settlement-day observation, not a forecast-belief substitution).
    #
    # SOURCE-PARITY WIDENING (2026-06-16, spine source-divergence fix, plan Option
    # A): the guard formerly fired only for replacement-authority (edli) positions,
    # leaving LEGACY non-edli positions to substitute the cold single-model
    # ``ensemble_snapshots`` EMOS center — the same cold-center divergence the entry
    # spine fix removed, re-introduced on the held side. The guard is now widened to
    # ALL non-day0 positions: a legacy position with a fresh ``forecast_posteriors``
    # row already returned fresh ABOVE (``load_replacement_belief`` is position-
    # agnostic); one with a stale/missing posterior is marked belief-unavailable
    # (fail-closed hold) rather than exiting off a cold ensemble center. VERIFIED
    # PREREQUISITE: all live legacy held positions (Houston aef7968f active;
    # Chengdu ad59da00 / Hong Kong day0_window) have ``forecast_posteriors`` coverage
    # for their family, so widening never strands a held position with NO belief
    # source. The same-family reseed is the only repair lane; the ensemble
    # registry below is retained ONLY as applied-list telemetry.
    _metric_for_family = resolve_position_metric(pos)[0]
    # LAYER 2 (2026-06-21 held-belief freeze fix): BEFORE fail-closing, attempt a
    # SYNCHRONOUS single-family read-through recompute of THIS family's replacement
    # posterior via the SAME canonical fusion authority, using whatever single_runs
    # are CURRENTLY persisted. This is NOT a loosening of the BELIEF_AUTHORITY_FAULT
    # guard: it makes the belief FRESH legitimately (canonical fusion, honestly wider
    # CI when fewer providers) instead of substituting the cold legacy ENS center.
    # If it yields a fresh posterior, the exit organ regains a fresh same-authority
    # belief THIS cycle (so CI_SEPARATED_REVERSAL can arm); if not, we fail-close as
    # before AND record a durable, retryable belief_debt marker (never a silent freeze).
    readthrough_prob = _attempt_held_belief_readthrough(
        pos, city=city, target_d=target_d, metric=_metric_for_family
    )
    if readthrough_prob is not None:
        fresh_pos = replace(pos)
        setattr(fresh_pos, "selected_method", SELECTED_METHOD_REPLACEMENT_POSTERIOR)
        _append_monitor_validation(fresh_pos, SELECTED_METHOD_REPLACEMENT_POSTERIOR)
        _append_monitor_validation(
            fresh_pos,
            "belief_source=forecast_posteriors_readthrough_recompute;basis=canonical_bayes_precision_fusion",
        )
        _set_monitor_probability_fresh(fresh_pos, True)
        _clear_belief_debt(
            city=str(pos.city), target_date=str(pos.target_date),
            metric=_metric_for_family, pos=fresh_pos,
        )
        return float(readthrough_prob), fresh_pos, True
    _set_monitor_probability_fresh(pos, False)
    _append_monitor_validation(pos, "BELIEF_AUTHORITY_FAULT")
    _append_monitor_validation(pos, "legacy_belief_substitution_suppressed")
    # Durable, retryable belief-debt: the read-through could not honestly recompute
    # (no current single_runs / no on-disk anchor) — record it so a held position is
    # never silently frozen. The reseed below is the repair lane.
    _record_belief_debt(
        pos, city=str(pos.city), target_date=str(pos.target_date),
        metric=_metric_for_family, reason="readthrough_inputs_insufficient",
    )
    _enqueue_single_family_belief_reseed_failsoft(
        city=str(pos.city),
        target_date=str(pos.target_date),
        metric=_metric_for_family,
    )
    # Return the stored entry-time posterior as the value carrier but with
    # is_fresh=False so refresh_position records NaN current_p_posterior and
    # the exit organ treats belief as unavailable (never a stale-as-fresh).
    _posterior_provenance = pos.selected_method or pos.entry_method
    if not _posterior_provenance:
        _append_monitor_validation(pos, "stored_entry_probability_provenance_missing")
        return float("nan"), pos, False
    return pos.p_posterior, pos, False


def refresh_position(conn, clob: PolymarketClient, pos: Position) -> EdgeContext:
    """Fetch fresh market price and recompute P_posterior for a held position.

    Blueprint v2 §7 Layer 1: uses same method as entry (p_raw_vector with MC noise).
    Returns: EdgeContext wrapping both fresh market and semantic provenance.
    Missing probability authority materializes as non-finite probability fields.
    """
    monitor_evaluated_at = datetime.now(timezone.utc).isoformat()
    pos.last_monitor_at = monitor_evaluated_at
    current_p_market = (
        pos.last_monitor_market_price
        if pos.last_monitor_market_price is not None
        else pos.entry_price
    )
    current_p_posterior = float("nan")
    if pos.direction not in {"buy_yes", "buy_no"}:
        logger.warning("Skipping refresh for %s: unknown direction %r", pos.trade_id, pos.direction)
        raise ValueError(f"Unknown direction {pos.direction} for trade {pos.trade_id}")

    pos.last_monitor_best_bid = None
    pos.last_monitor_best_ask = None
    pos.last_monitor_market_vig = None
    pos.last_monitor_whale_toxicity = None
    pos.last_monitor_market_price_is_fresh = False
    pos.last_monitor_prob_is_fresh = False

    # 1. Refresh held-token quote
    market_refreshed = False
    quote = monitor_quote_refresh(conn, clob, pos)
    if quote is not None:
        pos.last_monitor_best_bid = quote.best_bid
        pos.last_monitor_best_ask = quote.best_ask
        current_p_market = quote.diagnostic_market_price
        market_refreshed = True
        pos.last_monitor_market_price = current_p_market
        pos.last_monitor_market_price_is_fresh = True

    # 2. Recompute P_posterior from fresh ENS/Day0 evidence
    city = cities_by_name.get(pos.city)
    if city is None:
        raise ValueError(f"Unknown city {pos.city} for trade {pos.trade_id}")

    try:
        target_d = date.fromisoformat(pos.target_date)
        refreshed_p_posterior, refresh_pos, prob_refresh_is_fresh = monitor_probability_refresh(
            pos,
            conn=conn,
            city=city,
            target_d=target_d,
        )
        pos.selected_method = refresh_pos.selected_method
        pos.applied_validations = list(refresh_pos.applied_validations)
        # A1: Propagate bootstrap context from refresh_pos (may differ from pos for day0_window)
        _bootstrap_ctx = getattr(refresh_pos, "_bootstrap_context", None)
        if _bootstrap_ctx is not None:
            setattr(pos, "_bootstrap_context", _bootstrap_ctx)

        # Persist monitor state on Position only when the producer explicitly
        # attests freshness. Stored entry-time posterior is not a current
        # monitor probability and must not be relabeled as such.
        pos.last_monitor_prob_is_fresh = prob_refresh_is_fresh is True
        if pos.last_monitor_prob_is_fresh:
            current_p_posterior = float(refreshed_p_posterior)
            pos.last_monitor_prob = current_p_posterior
            pos.last_monitor_edge = current_p_posterior - current_p_market
        else:
            current_p_posterior = float("nan")
            pos.last_monitor_edge = float("nan")
            _append_monitor_validation(pos, "monitor_probability_stale")
            if prob_refresh_is_fresh is None:
                _append_monitor_validation(pos, "monitor_probability_authority_unknown")
        if not market_refreshed:
            pos.last_monitor_market_price = current_p_market

    except Exception as e:
        logger.debug("ENS refresh failed for %s: %s", pos.trade_id, e)
        pos.last_monitor_prob_is_fresh = False
        current_p_posterior = float("nan")
        pos.last_monitor_edge = float("nan")
        _append_monitor_validation(pos, "monitor_probability_refresh_failed")

    _track_belief_staleness(pos)

    probability_authority_available = (
        pos.last_monitor_prob_is_fresh
        and np.isfinite(current_p_posterior)
    )

    if pos.direction != "buy_yes":
        pos.last_monitor_whale_toxicity = False
        _append_monitor_validation(pos, "whale_toxicity_not_applicable:buy_no")
    elif probability_authority_available:
        pos.last_monitor_whale_toxicity = None
        _append_monitor_validation(pos, "whale_toxicity_deferred:fresh_probability_authority")
    else:
        pos.last_monitor_whale_toxicity = _detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=pos.last_monitor_best_bid,
            held_best_ask=pos.last_monitor_best_ask,
        )

    divergence_score = _compute_divergence_score(
        current_p_posterior, current_p_market, available=probability_authority_available
    )
    market_velocity_1h = 0.0

    # Try fetching 1h velocity if we know the token
    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    if tid:
        from datetime import timedelta
        try:
            one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            row = conn.execute(
                "SELECT price FROM token_price_log WHERE token_id = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
                (tid, one_hour_ago),
            ).fetchone()
            if row:
                old_native_p = row["price"]
                market_velocity_1h = current_p_market - old_native_p
        except Exception as e:
            logger.debug("Failed to calculate market velocity for %s: %s", pos.trade_id, e)

    # Wrap into verified EdgeContext
    current_forward_edge = (
        current_p_posterior - current_p_market
        if probability_authority_available
        else float("nan")
    )

    # A1: Recompute bootstrap CI from fresh data (symmetric with entry path).
    # Slice P3.2 + P3-fix3 (post-review critic Major #2, 2026-04-26): when
    # fresh bootstrap CI is unavailable (no cached _bootstrap_context — e.g.
    # position re-loaded from JSON fallback after process restart, or test
    # fixture without the cached context), fall back to entry's CI width
    # rather than the pre-P3.2 degenerate `ci_lower = ci_upper =
    # current_forward_edge` (zero width). With degenerate fallback,
    # conservative_forward_edge collapsed to point-estimate logic —
    # exit decisions reverted to raw-point edge, breaking the entry/exit
    # epistemic-symmetry contract that known_gaps.md says was fixed for
    # the bootstrap-present path.
    #
    # CAVEAT (critic Major #2): entry_ci_width is FROZEN at entry-time
    # (cycle_runtime.py:273; never updated post-entry). For positions held
    # past significant bin-distribution evolution, this fallback gives
    # STALE-but-defensive CI width — wider than current truth in late-
    # cycle scenarios. Operationally bounded to post-restart first-cycle
    # window since the recompute branch dominates steady-state. DEBUG
    # log emitted on fallback so operators can audit incidence.
    _entry_ci_half = max(0.0, getattr(pos, "entry_ci_width", 0.0)) / 2.0
    ci_lower = current_forward_edge - _entry_ci_half
    ci_upper = current_forward_edge + _entry_ci_half
    bootstrap_ctx = getattr(pos, "_bootstrap_context", None)
    if not probability_authority_available:
        ci_lower = float("nan")
        ci_upper = float("nan")
    elif bootstrap_ctx is None or len(bootstrap_ctx.get("bins", []) if bootstrap_ctx else []) <= 1:
        logger.debug(
            "P3.2 fallback: no _bootstrap_context; using stale entry_ci_width "
            "for trade=%s entry_ci_width=%.6f",
            getattr(pos, "trade_id", "?"),
            getattr(pos, "entry_ci_width", 0.0),
        )
    if not probability_authority_available:
        pass
    elif bootstrap_ctx is not None and len(bootstrap_ctx["bins"]) > 1:
        try:
            from src.strategy.market_analysis import MarketAnalysis
            from src.contracts.forecast_sharpness import ForecastSharpnessEvidence
            held_idx = bootstrap_ctx["held_idx"]
            bins = bootstrap_ctx["bins"]
            if len(bootstrap_ctx["member_extrema"]) == 0:
                raise ValueError("Bootstrap context has no member_extrema")
            p_market_arr = None
            if pos.direction == "buy_yes" or len(bins) <= 2:
                p_market_arr = np.zeros(len(bins))
                # Binary buy_no may still use complement price semantics. In
                # multi-bin buy_no, native NO quote below is the only executable
                # cost and model-only posterior never consumes this vector.
                p_market_yes = current_p_market if pos.direction == "buy_yes" else 0.0
                p_market_arr[held_idx] = p_market_yes
            p_market_no_arr = None
            buy_no_quote_available = None
            if pos.direction == "buy_no" and len(bins) > 2:
                p_market_no_arr = np.zeros(len(bins))
                p_market_no_arr[held_idx] = current_p_market
                buy_no_quote_available = np.zeros(len(bins), dtype=bool)
                buy_no_quote_available[held_idx] = True

            analysis = MarketAnalysis(
                p_raw=bootstrap_ctx["p_raw"],
                p_cal=bootstrap_ctx["p_cal"],
                p_market=p_market_arr,
                p_market_no=p_market_no_arr,
                buy_no_quote_available=buy_no_quote_available,
                alpha=bootstrap_ctx["alpha"],
                bins=bins,
                member_maxes=bootstrap_ctx["member_extrema"],
                calibrator=bootstrap_ctx["calibrator"],
                lead_days=bootstrap_ctx["lead_days"],
                unit=bootstrap_ctx["unit"],
                posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
                bootstrap_probability_sampler=bootstrap_ctx.get("bootstrap_probability_sampler"),
                bootstrap_signal_type=bootstrap_ctx.get("bootstrap_signal_type", "monitor_forecast"),
                # K1: this path recomputes CI for a HELD position via _bootstrap_bin
                # (never find_edges), so the sharpness gate is moot — exempt evidence
                # keeps the required ctor contract satisfied without affecting CI.
                forecast_sharpness=ForecastSharpnessEvidence.exempt(unit=bootstrap_ctx["unit"]),
            )
            # Call _bootstrap_bin directly (not find_edges) so CI is computed
            # regardless of edge sign — monitor needs CI even when edge is negative.
            if pos.direction == "buy_yes":
                ci_lower, ci_upper, _ = analysis._bootstrap_bin(held_idx, edge_n_bootstrap())
            else:
                ci_lower, ci_upper, _ = analysis._bootstrap_bin_no(held_idx, edge_n_bootstrap())
            # Guard against NaN from degenerate bootstrap (e.g., empty member_maxes)
            if np.isnan(ci_lower) or np.isnan(ci_upper):
                raise ValueError("Bootstrap produced NaN CI bounds")
        except Exception as e:
            logger.debug("A1: Bootstrap CI recomputation failed for %s: %s", pos.trade_id, e)
            ci_half_width = max(0.0, pos.entry_ci_width) / 2.0
            ci_lower = current_forward_edge - ci_half_width
            ci_upper = current_forward_edge + ci_half_width
    else:
        # Single-bin fallback or no bootstrap context — use stale CI width
        ci_half_width = max(0.0, pos.entry_ci_width) / 2.0
        ci_lower = current_forward_edge - ci_half_width
        ci_upper = current_forward_edge + ci_half_width

    return EdgeContext(
        p_raw=np.array([]),
        p_cal=np.array([]),
        p_market=np.array([current_p_market]),
        p_posterior=current_p_posterior,
        forward_edge=current_forward_edge,
        alpha=bootstrap_ctx["alpha"] if bootstrap_ctx else 0.0,
        confidence_band_upper=ci_upper,
        confidence_band_lower=ci_lower,
        entry_provenance=EntryMethod(pos.entry_method),
        decision_snapshot_id=pos.decision_snapshot_id,
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=market_velocity_1h,
        divergence_score=divergence_score,
    )
