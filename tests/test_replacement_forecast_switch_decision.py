# Created: 2026-06-06
# Last reused/audited: 2026-06-17
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-17; last_reused=2026-06-17
# Purpose: Prove replacement forecast switch admission is live-only and cannot admit diagnostic middle states.
# Reuse: Run before wiring replacement forecast switch decisions into daemon or event reactor.
# Authority basis: Operator directive 2026-06-17: already-live systems cannot retain non-live coupling.
"""Replacement forecast runtime switch decision tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.data.replacement_forecast_live_switch_surface import (
    REQUIRED_EVIDENCE_GATES,
    REQUIRED_FORECAST_TABLES,
    REQUIRED_LIVE_READ_FILES,
    REQUIRED_TRADE_TABLES,
    REQUIRED_WORLD_TABLES,
    ReplacementForecastLiveSwitchInput,
    build_replacement_forecast_live_switch_report,
)
from src.data.replacement_forecast_readiness import (
    HIGH_DATA_VERSION,
    PRODUCT_ID,
    SOURCE_ID,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    # Renamed from TRADE_AUTHORITY_FLAG by a1c2163e4 ("unify replacement
    # forecast runtime semantics" — drops the trade-authority middle state
    # in favor of a direct live flag).
    LIVE_FLAG as TRADE_AUTHORITY_FLAG,
    resolve_replacement_forecast_runtime_policy,
)
from src.data.replacement_forecast_switch_decision import (
    ReplacementForecastSwitchDecisionInput,
    evaluate_replacement_forecast_switch_decision,
)


UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _flags(*, trade: bool = False, kelly: bool = False, flip: bool = False) -> dict[str, bool]:
    return {
        TRADE_AUTHORITY_FLAG: trade,
        KELLY_INCREASE_FLAG: kelly,
        DIRECTION_FLIP_FLAG: flip,
    }


def _policy(*, trade: bool = True, kelly: bool = False, flip: bool = False):
    return resolve_replacement_forecast_runtime_policy(
        _flags(trade=trade, kelly=kelly, flip=flip)
    )


def _live_switch(policy=None, *, current: bool = True):
    policy = policy or _policy()
    return build_replacement_forecast_live_switch_report(
        ReplacementForecastLiveSwitchInput(
            runtime_policy=policy,
            available_files=tuple(REQUIRED_LIVE_READ_FILES),
            forecast_tables=tuple(REQUIRED_FORECAST_TABLES),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            trade_tables=tuple(REQUIRED_TRADE_TABLES),
            enabled_evidence_gates=tuple(REQUIRED_EVIDENCE_GATES),
            source_fact_status="CURRENT_FOR_LIVE" if current else "STALE_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE" if current else "STALE_FOR_LIVE",
        )
    )


def _readiness(*, ready: bool = True):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id="b0-run",
            source_available_at=_dt(2),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id="aifs-run",
            source_available_at=_dt(2, 30),
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="om9-run",
            source_available_at=_dt(3),
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            # a1c2163e4 dropped AIFS wiring; the data_version naming no longer
            # carries "aifs_sampled_2t" (was
            # openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1).
            data_version=HIGH_DATA_VERSION,
            source_run_id="posterior-run",
            source_available_at=_dt(3, 5) if ready else _dt(5),
            posterior_id=77,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        computed_at=_dt(4, 1),
        expires_at=_dt(6),
        dependencies=dependencies,
    )


_DEFAULT = object()


def _decision(policy=None, live_switch=None, readiness=_DEFAULT):
    policy = policy or _policy()
    return evaluate_replacement_forecast_switch_decision(
        ReplacementForecastSwitchDecisionInput(
            runtime_policy=policy,
            live_switch_report=live_switch or _live_switch(policy),
            readiness=_readiness() if readiness is _DEFAULT else readiness,
        )
    )


def test_switch_decision_disabled_is_safe_noop() -> None:
    policy = _policy(trade=False)
    decision = _decision(policy=policy, live_switch=_live_switch(policy), readiness=None)

    assert decision.status == "DISABLED"
    assert decision.can_read_live_posterior is False
    assert decision.can_apply_reactor_hook is False
    assert decision.can_initiate_trade is False


def test_switch_decision_admits_live_authority_when_inputs_are_ready() -> None:
    decision = _decision()

    # "LIVE_AUTHORITY" / REPLACEMENT_SWITCH_LIVE_AUTHORITY_ADMITTED renamed to
    # "live" / REPLACEMENT_SWITCH_LIVE_ADMITTED by a1c2163e4 (drops the
    # trade-authority middle-state terminology).
    assert decision.status == "live"
    assert decision.reason_codes == ("REPLACEMENT_SWITCH_LIVE_ADMITTED",)
    assert decision.can_read_live_posterior is True
    assert decision.can_apply_reactor_hook is True
    assert decision.can_initiate_trade is True
    assert decision.can_increase_kelly is False
    assert decision.can_flip_direction is False
    assert decision.readiness_id is not None


def test_switch_decision_blocks_stale_live_switch_or_missing_readiness() -> None:
    stale = _decision(live_switch=_live_switch(current=False))
    missing = _decision(readiness=None)

    assert stale.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_SOURCE_FACTS_STALE" in stale.reason_codes
    assert missing.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_READINESS_MISSING" in missing.reason_codes


def test_switch_decision_blocks_not_ready_dependencies() -> None:
    decision = _decision(readiness=_readiness(ready=False))

    assert decision.status == "BLOCKED"
    assert "REPLACEMENT_DEPENDENCY_AFTER_DECISION_TIME" in decision.reason_codes


def test_switch_decision_blocks_non_live_policy() -> None:
    blocked_policy = _policy(trade=False, kelly=True)
    decision = _decision(policy=blocked_policy, live_switch=_live_switch(blocked_policy))

    assert blocked_policy.status == "BLOCKED"
    assert decision.status == "BLOCKED"
    assert decision.can_initiate_trade is False
    assert decision.can_read_live_posterior is False


def test_switch_decision_payload_is_json_ready_and_live_only() -> None:
    payload = _decision().as_dict()

    assert payload["status"] == "live"
    assert payload["can_read_live_posterior"] is True
    assert payload["can_initiate_trade"] is True
    assert "can_read_blocked_posterior" not in payload
    assert "can_apply_veto" not in payload
