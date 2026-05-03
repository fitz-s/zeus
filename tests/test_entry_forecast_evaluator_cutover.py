# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md evaluator live cutover fail-closed guard.
"""Evaluator live-entry forecast cutover guard tests."""

from __future__ import annotations

from datetime import datetime, timezone

from src.config import City
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


def test_live_mode_blocked_entry_forecast_stops_before_legacy_entry_primary_fetch(monkeypatch) -> None:
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "live")

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
