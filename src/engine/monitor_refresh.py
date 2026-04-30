"""Monitor refresh: recompute fresh probability for held positions.

Blueprint v2 §7 Layer 1: Recompute probability with SAME METHOD as entry.
Uses full p_raw_vector with MC instrument noise (not simplified _estimate_bin_p_raw).
"""

import logging
from dataclasses import replace
from datetime import date, datetime, timezone

import numpy as np

from src.calibration.manager import get_calibrator, season_from_date
from src.calibration.platt import calibrate_and_normalize
from src.config import (
    cities_by_name,
    day0_n_mc,
    edge_n_bootstrap,
    ensemble_n_mc,
    ensemble_primary_model,
)
from src.contracts import (
    EntryMethod,
    recompute_native_probability,
    SettlementSemantics,
)
from src.contracts.settlement_semantics import round_wmo_half_up_value
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.market_scanner import _parse_temp_range, get_sibling_outcomes
from src.data.observation_client import get_current_observation
from src.data.polymarket_client import PolymarketClient
from src.engine.time_context import lead_days_to_date_start
from src.signal.day0_router import Day0Router, Day0SignalInputs
from src.signal.day0_window import remaining_member_extrema_for_day0
from src.signal.ensemble_signal import EnsembleSignal
from src.state.chain_reconciliation import resolve_position_metric
from src.state.portfolio import Position
from src.strategy.market_fusion import compute_alpha, vwmp
from src.types import Bin
from src.types.metric_identity import MetricIdentity
from src.types.temperature import TemperatureDelta

logger = logging.getLogger(__name__)
_MONITOR_PROBABILITY_FRESH_ATTR = "_monitor_probability_is_fresh"


def _set_monitor_probability_fresh(position: Position, is_fresh: bool) -> None:
    setattr(position, _MONITOR_PROBABILITY_FRESH_ATTR, is_fresh)


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


def _build_all_bins(position: Position, city) -> tuple[list, int]:
    """Build full bin vector for a position's market.

    S6: Uses sibling outcomes from market scanner to reconstruct the
    complete bin set, matching the entry path's calibrate_and_normalize().
    Falls back to single held bin if siblings are unavailable.

    Returns (all_bins, held_bin_index).
    """
    held_low, held_high = _parse_temp_range(position.bin_label)
    held_bin = Bin(low=held_low, high=held_high, label=position.bin_label, unit=position.unit)

    if not position.market_id:
        return [held_bin], 0

    siblings = get_sibling_outcomes(position.market_id)
    if len(siblings) < 2:
        return [held_bin], 0

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
        return [held_bin], 0

    # Guard: if held market_id was never matched, fall back to single bin
    matched = any(o.get("market_id") == position.market_id for o in siblings
                  if not (o.get("range_low") is None and o.get("range_high") is None))
    if not matched:
        logger.warning("S6: held market_id %s not found in siblings, falling back to single bin",
                       position.market_id)
        return [held_bin], 0

    return all_bins, held_idx


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

    ens_result = fetch_ensemble(
        city,
        forecast_days=int(requested_lead_days) + 2,
        model=ensemble_primary_model(),
        role="monitor_fallback",
    )
    if ens_result is None or not validate_ensemble(ens_result):
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["fresh_ens_fetch"]
    forecast_source_validations = _monitor_forecast_source_validations(ens_result)
    lead_days = max(0.0, lead_days_to_date_start(target_d, city.timezone, ens_result.get("fetch_time")))

    semantics = SettlementSemantics.for_city(city)
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
    # Fall back to single-bin predict_for_bin if sibling outcomes unavailable.
    all_bins, held_idx = _build_all_bins(position, city)

    p_raw_vector = ens.p_raw_vector(all_bins, n_mc=ensemble_n_mc())

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
    cal, cal_level = get_calibrator(
        conn, city, position.target_date,
        temperature_metric=_position_metric_str,  # hoisted (P2-fix5)
    )
    if cal is not None and len(all_bins) > 1:
        p_cal_vector = calibrate_and_normalize(
            p_raw_vector,
            cal,
            float(lead_days),
            bin_widths=[b.width for b in all_bins],
        )
        p_cal_yes = float(p_cal_vector[held_idx])
        applied = [
            "fresh_ens_fetch",
            *forecast_source_validations,
            "mc_instrument_noise",
            "platt_recalibration",
            "vector_normalization",
        ]
    elif cal is not None:
        p_cal_yes = cal.predict_for_bin(
            float(p_raw_vector[0]),
            float(lead_days),
            bin_width=all_bins[0].width,
        )
        applied = [
            "fresh_ens_fetch",
            *forecast_source_validations,
            "mc_instrument_noise",
            "platt_recalibration",
        ]
    else:
        p_cal_yes = float(p_raw_vector[held_idx])
        applied = ["fresh_ens_fetch", *forecast_source_validations, "mc_instrument_noise"]

    # Compute actual hours since position was entered (not hardcoded 48h)
    hours_since_open = 48.0
    if position.entered_at:
        try:
            entered = datetime.fromisoformat(position.entered_at)
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=timezone.utc)
            hours_since_open = (datetime.now(timezone.utc) - entered).total_seconds() / 3600.0
        except Exception:
            pass  # Malformed timestamp → fall back to 48h

    # K1/#68: verify calibration authority before computing alpha.
    # Same gate as evaluator.py — check for UNVERIFIED calibration rows.
    # Slice P2-A2 (PR #19 phase 2, 2026-04-26): scope to active metric so
    # cross-metric noise doesn't trigger false-positive stale-probability
    # warnings. Resolver from P2-C1 already determined position metric
    # for this monitor cycle (post-P2-C2 routing); reuse it here.
    _authority_verified = False
    if conn is not None and hasattr(conn, 'execute'):
        from src.calibration.store import get_pairs_for_bucket as _get_pairs
        _cal_season = season_from_date(target_d, lat=city.lat)
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

    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ens.spread(),
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
        conn, city.name, target_d, float(np.mean(ens.member_maxes)),
        temperature_metric=_position_metric_str,
    )
    if anomaly_discount < 1.0:
        alpha *= anomaly_discount
        applied.append("persistence_anomaly_discount")
        logger.info(
            "Persistence anomaly for %s: α discounted by %.0f%%",
            city.name, (1.0 - anomaly_discount) * 100,
        )

    if position.direction == "buy_no":
        p_cal_native = 1.0 - p_cal_yes
    else:
        p_cal_native = p_cal_yes

    current_p_posterior = alpha * p_cal_native + (1.0 - alpha) * current_p_market

    # A1: Stash bootstrap-relevant data for fresh CI computation in refresh_position
    p_cal_full = p_cal_vector if (cal is not None and len(all_bins) > 1) else np.array([p_cal_yes])
    setattr(position, "_bootstrap_context", {
        "p_raw": p_raw_vector,
        "p_cal": p_cal_full,
        "alpha": alpha,
        "bins": all_bins,
        "held_idx": held_idx,
        "member_extrema": ens.member_maxes,
        "calibrator": cal,
        "lead_days": float(lead_days),
        "unit": city.settlement_unit,
    })

    _set_monitor_probability_fresh(position, True)
    return current_p_posterior, [*applied, "alpha_posterior"]


def _fetch_day0_observation(city: Position | object, target_d: date):
    reference_time = datetime.now(timezone.utc)
    try:
        return get_current_observation(city, target_date=target_d, reference_time=reference_time)
    except TypeError:
        return get_current_observation(city)


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
    if not obs.observation_time:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation", "missing_observation_timestamp"]

    ens_result = fetch_ensemble(
        city,
        forecast_days=2,
        model=ensemble_primary_model(),
        role="monitor_fallback",
    )
    if ens_result is None or not validate_ensemble(ens_result):
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch"]
    forecast_source_validations = _monitor_forecast_source_validations(ens_result)

    low, high = _parse_temp_range(position.bin_label)
    if low is None and high is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch"]

    try:
        from src.signal.diurnal import build_day0_temporal_context
        temporal_context = build_day0_temporal_context(
            city.name,
            target_d,
            city.timezone,
            observation_time=obs.observation_time,
            observation_source=obs.source,
        )
    except Exception:
        temporal_context = None

    if temporal_context is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch", "missing_solar_context"]

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

    extrema, hours_remaining = remaining_member_extrema_for_day0(
        ens_result["members_hourly"],
        ens_result["times"],
        city.timezone,
        target_d,
        now=temporal_context.current_utc_timestamp,
        temperature_metric=temperature_metric,
    )
    if extrema is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch"]

    semantics = SettlementSemantics.for_city(city)
    day0 = Day0Router.route(Day0SignalInputs(
        temperature_metric=temperature_metric,
        observed_high_so_far=float(obs.high_so_far) if obs.high_so_far is not None else None,
        observed_low_so_far=float(obs.low_so_far) if obs.low_so_far is not None else None,
        current_temp=float(obs.current_temp),
        hours_remaining=hours_remaining,
        member_maxes_remaining=extrema.maxes,
        member_mins_remaining=extrema.mins,
        unit=city.settlement_unit,
        observation_source=str(obs.source),
        observation_time=obs.observation_time,
        temporal_context=temporal_context,
        round_fn=semantics.round_values,
        precision=semantics.precision,
    ))
    # S6: Build full bin vector for calibrate_and_normalize (same path as entry)
    all_bins, held_idx = _build_all_bins(position, city)

    p_raw_vector = day0.p_vector(all_bins, n_mc=day0_n_mc())

    # L3 Phase 9C metric-aware Platt read (Day0 exit lane)
    # Slice P2-C2 + P2-fix5: use hoisted _position_metric_str (resolver
    # already fired audit log at function entry).
    cal, cal_level = get_calibrator(
        conn, city, position.target_date,
        temperature_metric=_position_metric_str,
    )
    if cal is not None and len(all_bins) > 1:
        p_cal_vector = calibrate_and_normalize(
            p_raw_vector,
            cal,
            0.0,
            bin_widths=[b.width for b in all_bins],
        )
        p_cal_yes = float(p_cal_vector[held_idx])
        applied = [
            "day0_observation",
            "fresh_ens_fetch",
            *forecast_source_validations,
            "mc_instrument_noise",
            "platt_recalibration",
            "vector_normalization",
        ]
    elif cal is not None:
        p_cal_yes = cal.predict_for_bin(
            float(p_raw_vector[0]),
            0.0,
            bin_width=all_bins[0].width,
        )
        applied = [
            "day0_observation",
            "fresh_ens_fetch",
            *forecast_source_validations,
            "mc_instrument_noise",
            "platt_recalibration",
        ]
    else:
        p_cal_yes = float(p_raw_vector[held_idx])
        applied = [
            "day0_observation",
            "fresh_ens_fetch",
            *forecast_source_validations,
            "mc_instrument_noise",
        ]

    member_extrema = extrema.mins if temperature_metric.is_low() else extrema.maxes
    if member_extrema is None:
        _set_monitor_probability_fresh(position, False)
        return position.p_posterior, ["day0_observation", "fresh_ens_fetch", "metric_extrema_missing"]
    ensemble_spread = TemperatureDelta(float(np.std(member_extrema)), city.settlement_unit)

    hours_since_open = 48.0
    if position.entered_at:
        try:
            entered = datetime.fromisoformat(position.entered_at)
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=timezone.utc)
            hours_since_open = (datetime.now(timezone.utc) - entered).total_seconds() / 3600.0
        except Exception:
            pass

    # K1/#68: verify calibration authority before computing alpha.
    # Slice P2-A2 (PR #19 phase 2, 2026-04-26): twin of the gate above —
    # scope to active metric for the same false-positive-suppression reason.
    _authority_verified = False
    if conn is not None and hasattr(conn, 'execute'):
        from src.calibration.store import get_pairs_for_bucket as _get_pairs
        _cal_season = season_from_date(target_d, lat=city.lat)
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

    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ensemble_spread,
        model_agreement=getattr(position, "entry_model_agreement", "NOT_CHECKED"),
        lead_days=0.0,
        hours_since_open=hours_since_open,
        authority_verified=_authority_verified,
    ).value_for_consumer("ev")
    p_cal_native = 1.0 - p_cal_yes if position.direction == "buy_no" else p_cal_yes
    current_p_posterior = alpha * p_cal_native + (1.0 - alpha) * current_p_market

    # A1: Stash bootstrap-relevant data for fresh CI computation in refresh_position
    p_cal_full = p_cal_vector if (cal is not None and len(all_bins) > 1) else np.array([p_cal_yes])
    setattr(position, "_bootstrap_context", {
        "p_raw": p_raw_vector,
        "p_cal": p_cal_full,
        "alpha": alpha,
        "bins": all_bins,
        "held_idx": held_idx,
        "member_extrema": extrema.maxes if extrema.maxes is not None else extrema.mins,
        "calibrator": cal,
        "lead_days": 0.0,
        "unit": city.settlement_unit,
    })

    _set_monitor_probability_fresh(position, True)
    return current_p_posterior, [*applied, "alpha_posterior"]


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
    when settlements_v2 populated (P10D).
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
                "SELECT settlement_value FROM settlements "
                "WHERE city = ? AND target_date = ? "
                "AND temperature_metric = 'high' LIMIT 1",
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
                "PERSISTENCE_CHECK_DISABLED: all 3 recent settlement days NULL for %s/%s — returning 1.0 (no discount)",
                city_name, target_date,
            )
            return 1.0

        delta = sum(deltas) / len(deltas)
        bucket = _delta_bucket(delta)

        freq_row = conn.execute(
            "SELECT frequency, n_samples FROM temp_persistence "
            "WHERE city = ? AND season = ? AND delta_bucket = ?",
            (city_name, season, bucket),
        ).fetchone()

        if freq_row and freq_row["frequency"] < 0.05:
            n = freq_row["n_samples"]
            if n < 30:
                return 1.0  # Too few samples to trust the frequency estimate
            # Scale discount: 10% at n=30, grows linearly to 30% at n>=100
            discount_magnitude = min(0.30, 0.10 + 0.20 * (n - 30) / 70.0)
            return 1.0 - discount_magnitude

    except Exception as e:
        logger.debug("Persistence anomaly check failed for %s: %s", city_name, e)

    return 1.0


from src.contracts.edge_context import EdgeContext


def refresh_position(conn, clob: PolymarketClient, pos: Position) -> EdgeContext:
    """Fetch fresh market price and recompute P_posterior for a held position.

    Blueprint v2 §7 Layer 1: uses same method as entry (p_raw_vector with MC noise).
    Returns: EdgeContext wrapping both fresh market and semantic provenance.
    Falls back to stored values if refresh fails.
    """
    current_p_market = (
        pos.last_monitor_market_price
        if pos.last_monitor_market_price is not None
        else pos.entry_price
    )
    current_p_posterior = pos.p_posterior

    if pos.direction not in {"buy_yes", "buy_no"}:
        logger.warning("Skipping refresh for %s: unknown direction %r", pos.trade_id, pos.direction)
        raise ValueError(f"Unknown direction {pos.direction} for trade {pos.trade_id}")

    pos.last_monitor_best_bid = None
    pos.last_monitor_best_ask = None
    pos.last_monitor_market_vig = None
    pos.last_monitor_whale_toxicity = None
    pos.last_monitor_market_price_is_fresh = False
    pos.last_monitor_prob_is_fresh = False

    # 1. Refresh market price
    market_refreshed = False
    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    if tid:
        try:
            bid, ask, bid_sz, ask_sz = clob.get_best_bid_ask(tid)
            pos.last_monitor_best_bid = float(bid)
            pos.last_monitor_best_ask = float(ask)
            if pos.state == "day0_window":
                current_p_market = bid
            else:
                current_p_market = vwmp(bid, ask, bid_sz, ask_sz)
            market_refreshed = True

            # Injection Point 7: Data completeness - record microstructure snapshot
            from src.state.db import log_microstructure

            log_microstructure(
                conn,
                token_id=tid,
                city=pos.city,
                target_date=pos.target_date,
                range_label=pos.bin_label,
                price=float(current_p_market),
                volume=float(bid_sz + ask_sz),
                bid=float(bid),
                ask=float(ask),
                spread=round(float(ask - bid), 4) if ask >= bid else 0.0,
                source_timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            logger.debug("VWMP refresh failed for %s: %s", pos.trade_id, e)

    if market_refreshed:
        pos.last_monitor_market_price = current_p_market
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_at = datetime.now(timezone.utc).isoformat()

    # 2. Recompute P_posterior from fresh ENS
    city = cities_by_name.get(pos.city)
    if city is None:
        raise ValueError(f"Unknown city {pos.city} for trade {pos.trade_id}")

    try:
        target_d = date.fromisoformat(pos.target_date)
        registry = {
            EntryMethod.ENS_MEMBER_COUNTING.value: _refresh_ens_member_counting,
            EntryMethod.DAY0_OBSERVATION.value: _refresh_day0_observation,
        }
        refresh_pos = pos
        if pos.state == "day0_window" and pos.entry_method != EntryMethod.DAY0_OBSERVATION.value:
            refresh_pos = replace(pos, entry_method=EntryMethod.DAY0_OBSERVATION.value)
        setattr(refresh_pos, _MONITOR_PROBABILITY_FRESH_ATTR, None)
        current_p_posterior = recompute_native_probability(
            refresh_pos,
            current_p_market=current_p_market,
            registry=registry,
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
        prob_refresh_is_fresh = getattr(refresh_pos, _MONITOR_PROBABILITY_FRESH_ATTR, None)

        # Persist monitor state on Position
        pos.last_monitor_prob = current_p_posterior
        pos.last_monitor_prob_is_fresh = True if prob_refresh_is_fresh is None else bool(prob_refresh_is_fresh)
        pos.last_monitor_edge = current_p_posterior - current_p_market
        if not market_refreshed:
            pos.last_monitor_market_price = current_p_market

    except Exception as e:
        logger.debug("ENS refresh failed for %s: %s", pos.trade_id, e)

    divergence_score = abs(current_p_posterior - current_p_market)
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
    current_forward_edge = current_p_posterior - current_p_market

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
    if bootstrap_ctx is None or len(bootstrap_ctx.get("bins", []) if bootstrap_ctx else []) <= 1:
        logger.debug(
            "P3.2 fallback: no _bootstrap_context; using stale entry_ci_width "
            "for trade=%s entry_ci_width=%.6f",
            getattr(pos, "trade_id", "?"),
            getattr(pos, "entry_ci_width", 0.0),
        )
    if bootstrap_ctx is not None and len(bootstrap_ctx["bins"]) > 1:
        try:
            from src.strategy.market_analysis import MarketAnalysis
            held_idx = bootstrap_ctx["held_idx"]
            bins = bootstrap_ctx["bins"]
            if len(bootstrap_ctx["member_extrema"]) == 0:
                raise ValueError("Bootstrap context has no member_extrema")
            p_market_arr = np.zeros(len(bins))
            # A1: MarketAnalysis expects YES-side market prices (entry convention).
            # For buy_no, current_p_market is native NO-side — convert back to YES.
            p_market_yes = current_p_market if pos.direction == "buy_yes" else 1.0 - current_p_market
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
