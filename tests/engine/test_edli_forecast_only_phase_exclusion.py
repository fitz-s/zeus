# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: DAY0_OBSERVATION_WRONGSIDE_ROOT_2026-06-01.md §5 + DESIGN_CRITIC_2026-06-01.md
#   (#98 forecast_only phase-exclusion; critic MAJOR-4 hardening: admit ONLY PRE_SETTLEMENT_DAY).
"""RED relationship test: market-phase clock × EDLI forecast_only admission.

The cross-module invariant (Fitz relationship law): forecast_only is BLIND to
observation. The instant a market's target *local day* begins, the daily
extremum starts realizing and a forecast-only decision can land on the
already-observed (losing) side — the Paris June-1 buy_no-on-observed-low=14°C
incident. Therefore forecast_only must admit a family ONLY while the entire
target local day is still in the future: ``MarketPhase.PRE_SETTLEMENT_DAY``.
SETTLEMENT_DAY (local day begun), POST_TRADING (market closed), RESOLVED, and
unknown/None must all be rejected fail-closed.

This is STRONGER than the design doc §4.1 (which admitted SETTLEMENT_DAY); the
critic MAJOR-4 showed SETTLEMENT_DAY still carries already-observed-extremum
wrong-side exposure, so the category-killing rule excludes it too. Same-day edge
is the (disjoint) day0 observation-aware scope's job, never forecast_only's.

TIER 1 tests the pure admission rule against the authoritative phase clock with
DISTINCT keys per case (per the selection-fix memory lesson). TIER 2 proves the
gate is actually wired into the single receipt chokepoint
(``build_event_bound_no_submit_receipt``) and short-circuits before scoring.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.strategy.market_phase import MarketPhase

# NOTE: these two symbols do not exist on pre-#98 HEAD — the import error IS the
# RED signal (feature missing), per TDD. They land with the phase-gate fix.
from src.engine.event_reactor_adapter import (
    _edli_forecast_only_phase_evidence,
    _forecast_only_phase_admits,
)


def _utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# TIER 1 — pure admission rule (no DB). Distinct city/date/time per case.
# --------------------------------------------------------------------------- #


def test_future_date_pre_settlement_day_is_admitted():
    """Whole target local day still in the future -> PRE_SETTLEMENT_DAY -> admit.
    (The legitimate multi-day-ahead forecast edge forecast_only exists to trade.)"""
    evidence = _edli_forecast_only_phase_evidence(
        city="Chicago",
        target_date="2026-06-04",
        decision_time=_utc(2026, 6, 1, 12),
        selected_market_row={},  # no explicit endDate -> F1 12:00Z fallback
    )
    assert evidence.phase == MarketPhase.PRE_SETTLEMENT_DAY
    assert _forecast_only_phase_admits(evidence) is True


def test_same_day_post_trading_is_rejected():
    """Decision past the market's 12:00 UTC close (Paris-like, 4h post-close) ->
    POST_TRADING -> reject. Timezone-independent (decision >= F1 end)."""
    evidence = _edli_forecast_only_phase_evidence(
        city="Chicago",
        target_date="2026-06-02",
        decision_time=_utc(2026, 6, 2, 16),  # > 12:00Z end of target_date
        selected_market_row={},
    )
    assert evidence.phase == MarketPhase.POST_TRADING
    assert _forecast_only_phase_admits(evidence) is False


def test_same_day_settlement_day_pre_close_is_rejected():
    """Local target day has BEGUN but market not yet closed (pre-12:00Z) ->
    SETTLEMENT_DAY. The critic MAJOR-4 wrong-side case: an overnight low may
    already be realized while forecast_only is blind. Must reject."""
    # Chicago is UTC-5 (CDT) in June; local 00:00 of 2026-06-03 == 2026-06-03T05:00Z.
    # Decision 2026-06-03T09:00Z is past local midnight, before 12:00Z close.
    evidence = _edli_forecast_only_phase_evidence(
        city="Chicago",
        target_date="2026-06-03",
        decision_time=_utc(2026, 6, 3, 9),
        selected_market_row={},
    )
    assert evidence.phase == MarketPhase.SETTLEMENT_DAY
    assert _forecast_only_phase_admits(evidence) is False


def test_unknown_city_timezone_fails_closed():
    """A city with no resolvable timezone -> phase undeterminable -> fail-closed
    reject (never silently admit)."""
    evidence = _edli_forecast_only_phase_evidence(
        city="NoSuchCity__zzz",
        target_date="2026-06-05",
        decision_time=_utc(2026, 6, 1, 12),
        selected_market_row={},
    )
    assert evidence.phase is None
    assert _forecast_only_phase_admits(evidence) is False


def test_explicit_future_enddate_is_honored_over_f1_fallback():
    """When the market row carries an explicit endDate, it is used (verified_gamma)
    rather than the F1 fallback — admit when that end is still future."""
    evidence = _edli_forecast_only_phase_evidence(
        city="Chicago",
        target_date="2026-06-04",
        decision_time=_utc(2026, 6, 1, 12),
        selected_market_row={"market_end_at": "2026-06-04T12:00:00+00:00"},
    )
    assert evidence.phase_source == "verified_gamma"
    assert evidence.phase == MarketPhase.PRE_SETTLEMENT_DAY
    assert _forecast_only_phase_admits(evidence) is True


# --------------------------------------------------------------------------- #
# TIER 2 — wiring: the gate fires inside the single receipt chokepoint.
# Reuses the heavy COMPLETE-forecast fixture from the sibling suite.
# --------------------------------------------------------------------------- #

from tests.engine.test_event_reactor_no_bypass import (  # noqa: E402
    _bound_forecast_event,
    _trade_conn_with_snapshot,
    _receipt,
)


def test_same_day_post_trading_family_yields_no_candidate(monkeypatch):
    """RELATIONSHIP (phase clock -> receipt chokepoint): a FORECAST_SNAPSHOT_READY
    family decided AFTER its target_date 12:00Z close produces NO candidate — the
    receipt is rejected with EVENT_BOUND_MARKET_PHASE_CLOSED before scoring.

    The reader is forced OK so the pipeline reaches family binding; the ONLY thing
    that rejects the otherwise-valid same-day family is the phase gate. RED on
    pre-#98 HEAD: the receipt is accepted (phase-blind)."""
    from types import SimpleNamespace
    from src.data import executable_forecast_reader

    event = _bound_forecast_event()  # target_date 2026-05-25
    conn = _trade_conn_with_snapshot()
    # Decision at 2026-05-25T16:00Z — 4h past the F1 12:00Z close of target_date.
    decision_time = _utc(2026, 5, 25, 16)

    evidence = SimpleNamespace(
        forecast_source_id="ecmwf_open_data",
        forecast_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        source_transport="ensemble_snapshots_db_reader",
        source_cycle_time="2026-05-24T00:00:00+00:00",
        source_issue_time="2026-05-24T00:00:00+00:00",
        source_run_id="run-1",
        coverage_id="coverage-1",
        producer_readiness_id="producer_readiness:coverage-1",
        entry_readiness_id=None,
        input_snapshot_ids=(1,),
        raw_payload_hash="hash-raw",
        manifest_hash="hash-manifest",
        required_steps=(0, 3, 6),
        observed_steps=(0, 3, 6),
        expected_members=51,
        observed_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
        applied_validations=(
            "source_run_completeness_status",
            "coverage_completeness_status",
            "coverage_readiness_status",
            "required_steps_observed",
            "expected_members_observed",
            "causality_status_ok",
            "authority_verified",
            "available_at_not_future",
        ),
        source_available_at="2026-05-24T08:10:00+00:00",
        fetch_started_at="2026-05-24T07:10:00+00:00",
        fetch_finished_at="2026-05-24T08:05:00+00:00",
        captured_at="2026-05-24T08:10:00+00:00",
    )
    monkeypatch.setattr(
        executable_forecast_reader,
        "read_executable_forecast",
        lambda *_a, **_k: SimpleNamespace(
            ok=True,
            status="LIVE_ELIGIBLE",
            bundle=SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="1"), evidence=evidence),
            reason_code="OK",
        ),
    )

    receipt = _receipt(event, conn, decision_time=decision_time)

    assert receipt.proof_accepted is not True
    assert receipt.reason is not None and receipt.reason.startswith("EVENT_BOUND_MARKET_PHASE_CLOSED")
    # Observability: city/target_date/metric populated so the regret ledger row is meaningful.
    assert receipt.city == "Chicago"
    assert receipt.target_date == "2026-05-25"
    assert receipt.metric == "high"


def test_future_date_family_still_yields_candidate():
    """CONTROL: the SAME fixture at the normal future-date decision (target 05-25
    decided 05-24 = PRE_SETTLEMENT_DAY) must STILL produce an accepted candidate —
    the gate must not over-fire on legitimate forward markets."""
    event = _bound_forecast_event()
    receipt = _receipt(event, _trade_conn_with_snapshot())  # default decision 2026-05-24T08:11Z

    assert receipt.proof_accepted is True
    assert receipt.reason != "EVENT_BOUND_MARKET_PHASE_CLOSED"
