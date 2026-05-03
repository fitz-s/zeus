# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md evaluator live cutover fail-closed guard.
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


def test_live_mode_blocked_entry_forecast_stops_before_legacy_entry_primary_fetch(monkeypatch) -> None:
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
    assert decision.rejection_stage == "SIGNAL_QUALITY"
    assert decision.rejection_reasons == ["ENTRY_FORECAST_ROLLOUT_BLOCKED"]
    assert "legacy_entry_primary_fetch_blocked" in decision.applied_validations


def test_live_mode_live_rollout_uses_executable_reader_before_legacy_fetch(monkeypatch) -> None:
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
    assert decision.rejection_reasons == ["ENTRY_FORECAST_READER_DB_UNAVAILABLE"]
    assert decision.applied_validations == [
        "entry_forecast_reader",
        "legacy_entry_primary_fetch_blocked",
    ]
