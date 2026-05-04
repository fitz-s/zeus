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


def test_phase_c1_flag_off_preserves_legacy_rollout_blocker(monkeypatch) -> None:
    """Phase C-1: with ``ZEUS_ENTRY_FORECAST_ROLLOUT_GATE`` unset (default
    OFF), ``_live_entry_forecast_rollout_blocker`` continues to use the
    rollout-mode-only check so daemon behavior is byte-equal to pre-Phase-C.
    """

    monkeypatch.delenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", raising=False)
    blocked_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.BLOCKED)
    live_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    assert (
        evaluator_module._live_entry_forecast_rollout_blocker(blocked_cfg)
        == "ENTRY_FORECAST_ROLLOUT_BLOCKED"
    )
    assert evaluator_module._live_entry_forecast_rollout_blocker(live_cfg) is None


def test_phase_c1_flag_on_blocks_when_evidence_missing(monkeypatch, tmp_path) -> None:
    """Phase C-1: with the flag ON and no evidence file, the gate
    surfaces ``ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING`` rather than
    falling back to the rollout-mode-only check. This is the safety
    upgrade the gate is intended to provide.
    """

    from src.control import entry_forecast_promotion_evidence_io as evidence_io

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    monkeypatch.setattr(
        evidence_io,
        "DEFAULT_PROMOTION_EVIDENCE_PATH",
        tmp_path / "absent.json",
    )

    live_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)
    assert (
        evaluator_module._live_entry_forecast_rollout_blocker(live_cfg)
        == "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING"
    )


def test_phase_c1_flag_on_surfaces_corruption_as_explicit_blocker(monkeypatch, tmp_path) -> None:
    """Phase C-1: corrupt evidence file ⇒ explicit corruption blocker
    rather than uncaught exception that would crash the cycle.
    """

    from src.control import entry_forecast_promotion_evidence_io as evidence_io

    target = tmp_path / "corrupt.json"
    target.write_text("not valid json {{{")

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    live_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)
    blocker = evaluator_module._live_entry_forecast_rollout_blocker(live_cfg)
    assert blocker is not None
    assert blocker.startswith("ENTRY_FORECAST_PROMOTION_EVIDENCE_CORRUPT:")


def test_phase_c1_flag_on_passes_with_complete_evidence(monkeypatch, tmp_path) -> None:
    """Phase C-1: complete promotion evidence with all approvals ⇒ the
    gate returns ``None`` (no blocker) for live rollout."""

    from src.control.entry_forecast_promotion_evidence_io import (
        DEFAULT_PROMOTION_EVIDENCE_PATH,
        write_promotion_evidence,
    )
    from src.control import entry_forecast_promotion_evidence_io as evidence_io
    from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
    from src.data.live_entry_status import LiveEntryForecastStatus

    target = tmp_path / "evidence.json"
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    evidence = EntryForecastPromotionEvidence(
        operator_approval_id="op-2026-05-03",
        g1_evidence_id="g1-2026-05-03",
        status_snapshot=LiveEntryForecastStatus(
            status="LIVE_ELIGIBLE",
            blockers=(),
            executable_row_count=4,
            producer_readiness_count=4,
            producer_live_eligible_count=4,
        ),
        calibration_promotion_approved=True,
        canary_success_evidence_id="canary-1",
    )
    write_promotion_evidence(evidence, path=target)

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    live_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)
    assert evaluator_module._live_entry_forecast_rollout_blocker(live_cfg) is None


def test_phase_c1_flag_on_blocks_when_evidence_lacks_canary_success(monkeypatch, tmp_path) -> None:
    """Phase C-1: live rollout requires canary_success_evidence_id; a
    payload with operator + G1 + calibration approval but no canary
    success ⇒ gate emits ``ENTRY_FORECAST_CANARY_SUCCESS_MISSING``.
    """

    from src.control.entry_forecast_promotion_evidence_io import write_promotion_evidence
    from src.control import entry_forecast_promotion_evidence_io as evidence_io
    from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
    from src.data.live_entry_status import LiveEntryForecastStatus

    target = tmp_path / "evidence.json"
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    evidence = EntryForecastPromotionEvidence(
        operator_approval_id="op-1",
        g1_evidence_id="g1-1",
        status_snapshot=LiveEntryForecastStatus(
            status="LIVE_ELIGIBLE",
            blockers=(),
            executable_row_count=4,
            producer_readiness_count=4,
            producer_live_eligible_count=4,
        ),
        calibration_promotion_approved=True,
        canary_success_evidence_id=None,
    )
    write_promotion_evidence(evidence, path=target)

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    live_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)
    assert (
        evaluator_module._live_entry_forecast_rollout_blocker(live_cfg)
        == "ENTRY_FORECAST_CANARY_SUCCESS_MISSING"
    )


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
