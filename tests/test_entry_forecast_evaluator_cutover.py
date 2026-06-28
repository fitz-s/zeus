# Created: 2026-05-03
# Last reused/audited: 2026-05-20
# Authority basis: docs/archive/2026-Q2/task_2026-05-14_data_daemon_live_efficiency/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md
#   Phase 3 evaluator data-daemon cutover without hot-path entry-readiness writes.
"""Evaluator live-entry forecast cutover guard tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from src.config import City, EntryForecastRolloutMode, entry_forecast_config
from src.engine import evaluator as evaluator_module
from src.engine.discovery_mode import DiscoveryMode

UTC = timezone.utc


def _city() -> City:
    return City(
        name="London",
        lat=51.4775,
        lon=-0.4614,
        timezone="Europe/London",
        settlement_unit="C",
        cluster="London",
        wu_station="EGLL",
    )


def _candidate() -> evaluator_module.MarketCandidate:
    return evaluator_module.MarketCandidate(
        city=_city(),
        target_date="2026-05-08",
        outcomes=[],
        hours_since_open=1.0,
        temperature_metric="high",
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )


def _candidate_with_outcomes() -> evaluator_module.MarketCandidate:
    candidate = _candidate()
    candidate.outcomes.extend(
        [
            {
                "title": "< 15",
                "range_low": None,
                "range_high": 14.0,
                "token_id": "yes-0",
                "no_token_id": "no-0",
                "market_id": "condition-123",
                "condition_id": "condition-123",
            },
            {
                "title": "15",
                "range_low": 15.0,
                "range_high": 15.0,
                "token_id": "yes-1",
                "no_token_id": "no-1",
                "market_id": "condition-123",
                "condition_id": "condition-123",
            },
            {
                "title": "> 15",
                "range_low": 16.0,
                "range_high": None,
                "token_id": "yes-2",
                "no_token_id": "no-2",
                "market_id": "condition-123",
                "condition_id": "condition-123",
            },
        ]
    )
    return candidate


def test_period_extrema_spread_value_does_not_touch_missing_legacy_ensemble() -> None:
    spread = evaluator_module.TemperatureDelta(2.5, "C")

    assert evaluator_module._ensemble_spread_value(spread, None) == 2.5


def test_market_phase_source_threads_to_polymarket_end_anchor_source() -> None:
    verified = _candidate()
    verified.market_phase_source = "verified_gamma"
    fallback = _candidate()
    fallback.market_phase_source = "fallback_f1"

    assert evaluator_module._polymarket_end_anchor_source_for_candidate(verified) == "gamma_explicit"
    assert evaluator_module._polymarket_end_anchor_source_for_candidate(fallback) == "f1_12z_fallback"


def test_live_mode_blocked_rollout_no_longer_short_circuits_with_rollout_blocker(monkeypatch) -> None:
    blocked_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.BLOCKED)
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "live")
    monkeypatch.setattr(evaluator_module, "entry_forecast_config", lambda: blocked_cfg)

    def forbidden_fetch(*args, **kwargs):
        raise AssertionError("legacy fetch_ensemble should not be called")

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", forbidden_fetch)

    decisions = evaluator_module.evaluate_candidate(
        _candidate(),
        conn=None,
        portfolio=object(),
        clob=object(),
        limits=object(),
        decision_time=datetime(2026, 5, 3, tzinfo=UTC),
    )

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.should_trade is False
    assert decision.rejection_reasons != ["ENTRY_FORECAST_ROLLOUT_BLOCKED"]


def test_rollout_promotion_gate_is_not_in_live_evaluator_execution_path() -> None:
    """The canary/live promotion gate is control-plane tooling, not live evaluator authority."""

    assert not hasattr(evaluator_module, "_live_entry_forecast_rollout_blocker")
    assert not hasattr(evaluator_module, "_entry_forecast_rollout_gate_flag_on")


def test_live_mode_invalid_entry_forecast_config_has_own_rejection(monkeypatch) -> None:
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "live")

    def broken_config():
        raise ValueError("bad entry forecast config")

    monkeypatch.setattr(evaluator_module, "entry_forecast_config", broken_config)

    decisions = evaluator_module.evaluate_candidate(
        _candidate_with_outcomes(),
        conn=None,
        portfolio=object(),
        clob=object(),
        limits=object(),
        decision_time=datetime(2026, 5, 3, tzinfo=UTC),
    )

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.rejection_reasons == ["entry_forecast_reader_rejected"]
    assert decision.rejection_reason_detail.startswith("ENTRY_FORECAST_CONFIG_INVALID:")
    assert decision.applied_validations == [
        "entry_forecast_config",
        "legacy_entry_primary_fetch_blocked",
    ]


def test_phase_c6_day0_mode_falls_through_to_legacy_fetch(monkeypatch) -> None:
    """Phase C-6: a Day0 candidate with ``entry_forecast_cfg`` set must
    NOT be hard-rejected with ``ENTRY_FORECAST_DAY0_EXECUTABLE_PATH_NOT_WIRED``.
    It must fall through to the legacy ``fetch_ensemble`` path so the
    existing Day0 signal pipeline can run.

    Pre-Phase-C-6 the rejection silently killed Day0 trading whenever
    PR47 entry_forecast_cfg was loaded (i.e., every live cycle). The
    fix relies on the cutover-guard expression
    ``entry_forecast_cfg is not None and not is_day0_mode``.

    The retired rollout promotion gate is not part of this execution path; this
    test isolates the Day0 cutover behavior directly.
    """

    cfg_live = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "live")
    monkeypatch.setattr(evaluator_module, "entry_forecast_config", lambda: cfg_live)

    fetch_calls = []

    def stub_fetch(city, *, forecast_days, model, role):
        fetch_calls.append((city.name, forecast_days, model, role))
        # Raise SourceNotEnabled to short-circuit downstream processing
        # without a full fixture; we only need to prove the legacy
        # fetch was reached, not that it produced a usable result.
        from src.contracts import SourceNotEnabled
        raise SourceNotEnabled("test stub")

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", stub_fetch)

    def forbidden_reader(*args, **kwargs):
        raise AssertionError("Day0 mode must not use executable forecast cutover")

    monkeypatch.setattr(evaluator_module, "read_executable_forecast", forbidden_reader)

    from src.data.observation_client import Day0ObservationContext

    candidate = _candidate_with_outcomes()
    candidate.discovery_mode = DiscoveryMode.DAY0_CAPTURE.value
    # Day0 candidates carry a Day0ObservationContext whose ``source``
    # must match the city's settlement_source_type policy (London is
    # wu_icao → allowed=['wu_api']). Required fields for Day0 quality
    # gates: high_so_far, low_so_far, current_temp, source,
    # observation_time, unit. Without these, pre-cutover Day0 quality
    # checks at evaluator.py:~1390 short-circuit before reaching the
    # Phase-C-6 cutover-guard at evaluator.py:1618.
    candidate.observation = Day0ObservationContext(
        high_so_far=70.0,
        low_so_far=50.0,
        current_temp=60.0,
        source="wu_api",
        observation_time=datetime(2026, 5, 3, 11, tzinfo=UTC).isoformat(),
        unit="C",
    )

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=object(),
        clob=object(),
        limits=object(),
        decision_time=datetime(2026, 5, 3, 12, tzinfo=UTC),
    )

    assert len(decisions) == 1
    decision = decisions[0]
    # The rejection must NOT be the Phase-C-6-removed code; control
    # reached the legacy fetch (which our stub raised SourceNotEnabled
    # from), so the rejection comes from there instead.
    assert "ENTRY_FORECAST_DAY0_EXECUTABLE_PATH_NOT_WIRED" not in (decision.rejection_reasons or [])


def test_live_mode_live_rollout_uses_executable_reader_before_legacy_fetch(monkeypatch) -> None:
    """Live non-Day0 candidates use executable forecast rows before legacy fetch."""

    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "live")
    monkeypatch.setattr(evaluator_module, "entry_forecast_config", lambda: cfg)

    def forbidden_fetch(*args, **kwargs):
        raise AssertionError("legacy fetch_ensemble should not be called")

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", forbidden_fetch)

    decisions = evaluator_module.evaluate_candidate(
        _candidate_with_outcomes(),
        conn=None,
        portfolio=object(),
        clob=object(),
        limits=object(),
        decision_time=datetime(2026, 5, 3, tzinfo=UTC),
    )

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.should_trade is False
    assert decision.rejection_reasons == ["entry_forecast_reader_db_unavailable"]
    assert decision.applied_validations == [
        "entry_forecast_reader",
        "legacy_entry_primary_fetch_blocked",
    ]


def test_live_mode_reader_cutover_does_not_write_entry_readiness_in_evaluator(monkeypatch) -> None:
    """Live evaluator consumes producer readiness and must not produce entry readiness inline."""

    from types import SimpleNamespace

    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "live")
    monkeypatch.setattr(evaluator_module, "entry_forecast_config", lambda: cfg)

    def forbidden_fetch(*args, **kwargs):
        raise AssertionError("legacy fetch_ensemble should not be called")

    reader_calls: list[dict] = []

    def stub_reader(*args, **kwargs):
        reader_calls.append(kwargs)
        return SimpleNamespace(ok=False, bundle=None, reason_code="PRODUCER_READINESS_MISSING")

    class FakeConn:
        def execute(self, *args, **kwargs):  # pragma: no cover - reader is stubbed
            raise AssertionError("stub reader should avoid DB access")

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", forbidden_fetch)
    monkeypatch.setattr(evaluator_module, "read_executable_forecast", stub_reader)

    decisions = evaluator_module.evaluate_candidate(
        _candidate_with_outcomes(),
        conn=FakeConn(),
        portfolio=object(),
        clob=object(),
        limits=object(),
        decision_time=datetime(2026, 5, 3, tzinfo=UTC),
    )

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.should_trade is False
    assert decision.rejection_reasons == ["entry_forecast_reader_rejected"]
    assert decision.rejection_reason_detail == "PRODUCER_READINESS_MISSING"
    assert reader_calls
    assert reader_calls[0]["require_entry_readiness"] is False


def test_live_mode_actual_reader_consumes_daemon_readiness_before_signal(monkeypatch) -> None:
    """Live evaluator reaches signal processing through real DB readiness rows.

    This is the no-network E2E smoke for the data-daemon handoff:
    source_run, coverage, producer readiness, and snapshot rows are seeded in an
    in-memory DB; the evaluator must consume them through the executable reader
    without legacy direct fetch or evaluator-side readiness writes.
    """

    from tests.test_executable_forecast_reader import _conn, _insert_full_reader_fixture

    conn = _conn()
    _insert_full_reader_fixture(conn)

    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "live")
    monkeypatch.setattr(evaluator_module, "entry_forecast_config", lambda: cfg)

    def forbidden_p_raw_writer(*args, **kwargs):
        raise AssertionError("executable reader hot path must not write snapshot p_raw")

    def forbidden_fetch(*args, **kwargs):
        raise AssertionError("legacy fetch_ensemble should not be called")

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", forbidden_fetch)
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", forbidden_p_raw_writer)

    decisions = evaluator_module.evaluate_candidate(
        _candidate_with_outcomes(),
        conn=conn,
        portfolio=object(),
        clob=object(),
        limits=object(),
        decision_time=datetime(2026, 5, 3, 10, tzinfo=UTC),
    )

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.should_trade is False
    assert decision.rejection_stage == "CALIBRATION_IMMATURE"
    assert "entry_forecast_reader" in decision.applied_validations
    assert "entry_readiness" in decision.applied_validations
    assert "period_extrema_members_adapter" in decision.applied_validations
    assert "PRODUCER_READINESS_MISSING" not in (decision.rejection_reasons or [])
