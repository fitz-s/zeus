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


def test_phase_c1_kill_switch_zero_preserves_legacy_rollout_blocker(monkeypatch) -> None:
    """Phase C-1 post-2026-05-04 default-ON activation: setting
    ``ZEUS_ENTRY_FORECAST_ROLLOUT_GATE=0`` is the operator's emergency
    kill-switch — it restores the legacy rollout-mode-only check
    (byte-equal to pre-Phase-C behavior). Used during incident
    recovery only.
    """

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "0")
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


def test_phase_c3_kill_switch_zero_disables_writer(monkeypatch, tmp_path) -> None:
    """Phase C-3 post-2026-05-04 default-ON activation: setting
    ``ZEUS_ENTRY_FORECAST_READINESS_WRITER=0`` is the operator's
    emergency kill-switch — predicate returns False so the call site
    at ``evaluator.py:1639`` skips the writer invocation and no
    ``readiness_state`` row with ``strategy_key='entry_forecast'``
    lands. Used during incident recovery only.
    """

    from src.engine.evaluator import _entry_forecast_readiness_writer_flag_on

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "0")
    assert _entry_forecast_readiness_writer_flag_on() is False

    monkeypatch.delenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", raising=False)
    assert _entry_forecast_readiness_writer_flag_on() is True  # default-ON


def test_phase_c3_writer_flag_on_writes_blocked_row_when_evidence_missing(monkeypatch, tmp_path) -> None:
    """Phase C-3: with the flag ON and no evidence file, the helper
    writes a BLOCKED entry_readiness row whose reason includes
    ``ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING``. The reader will
    consume this row and emit a typed blocker rather than silently
    finding no row at all.
    """

    import sqlite3
    from datetime import date, datetime, timezone
    from src.config import EntryForecastRolloutMode, entry_forecast_config
    from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
    from src.control import entry_forecast_promotion_evidence_io as evidence_io
    from src.data.entry_readiness_writer import ENTRY_FORECAST_STRATEGY_KEY
    from src.engine import evaluator as evaluator_module
    from src.engine.evaluator import _write_entry_readiness_for_candidate
    from src.state.db import init_schema
    from src.state.schema.v2_schema import apply_v2_schema
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "1")
    monkeypatch.setattr(
        evidence_io,
        "DEFAULT_PROMOTION_EVIDENCE_PATH",
        tmp_path / "absent.json",
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)

    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    _write_entry_readiness_for_candidate(
        conn,
        cfg=cfg,
        city=_city(),
        target_local_date=date(2026, 5, 8),
        temperature_metric=HIGH_LOCALDAY_MAX,
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        decision_time=datetime(2026, 5, 3, 12, tzinfo=UTC),
    )

    row = conn.execute(
        "SELECT status, reason_codes_json, market_family, condition_id "
        "FROM readiness_state WHERE strategy_key = ?",
        (ENTRY_FORECAST_STRATEGY_KEY,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "BLOCKED"
    assert row["market_family"] == "POLY_TEMP_LONDON"
    assert row["condition_id"] == "condition-123"
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in row["reason_codes_json"]


def test_phase_c3_writer_flag_on_writes_live_eligible_when_all_gates_align(monkeypatch, tmp_path) -> None:
    """Phase C-3: complete promotion evidence + LIVE rollout + approved
    calibration ⇒ helper writes a LIVE_ELIGIBLE entry_readiness row.
    """

    import sqlite3
    from datetime import date, datetime, timezone
    from src.config import EntryForecastRolloutMode, entry_forecast_config
    from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
    from src.control import entry_forecast_promotion_evidence_io as evidence_io
    from src.control.entry_forecast_promotion_evidence_io import write_promotion_evidence
    from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
    from src.data.entry_readiness_writer import ENTRY_FORECAST_STRATEGY_KEY
    from src.data.live_entry_status import LiveEntryForecastStatus
    from src.engine.evaluator import _write_entry_readiness_for_candidate
    from src.state.db import init_schema
    from src.state.schema.v2_schema import apply_v2_schema
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    target = tmp_path / "evidence.json"
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "1")

    write_promotion_evidence(
        EntryForecastPromotionEvidence(
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
            canary_success_evidence_id="canary-1",
        ),
        path=target,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)

    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    _write_entry_readiness_for_candidate(
        conn,
        cfg=cfg,
        city=_city(),
        target_local_date=date(2026, 5, 8),
        temperature_metric=HIGH_LOCALDAY_MAX,
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        decision_time=datetime(2026, 5, 3, 12, tzinfo=UTC),
    )

    row = conn.execute(
        "SELECT status, expires_at FROM readiness_state WHERE strategy_key = ?",
        (ENTRY_FORECAST_STRATEGY_KEY,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "LIVE_ELIGIBLE"
    assert row["expires_at"] is not None


def test_phase_c6_day0_mode_falls_through_to_legacy_fetch(monkeypatch) -> None:
    """Phase C-6: a Day0 candidate with ``entry_forecast_cfg`` set must
    NOT be hard-rejected with ``ENTRY_FORECAST_DAY0_EXECUTABLE_PATH_NOT_WIRED``.
    It must fall through to the legacy ``fetch_ensemble`` path so the
    existing Day0 signal pipeline can run.

    Pre-Phase-C-6 the rejection silently killed Day0 trading whenever
    PR47 entry_forecast_cfg was loaded (i.e., every live cycle). The
    fix relies on the cutover-guard expression
    ``entry_forecast_cfg is not None and not is_day0_mode``.

    Post-2026-05-04 default-ON activation: the rollout gate fires
    BEFORE the Day0 cutover guard at evaluator.py:1467, so reaching
    the Day0 fall-through requires either populated promotion
    evidence or the gate kill-switch. This test uses the kill-switch
    to isolate the §Phase-C-6 behavior under test.
    """

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "0")

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
    assert fetch_calls, "legacy fetch_ensemble should have been called for Day0"
    assert fetch_calls[0][0] == "London"
    assert fetch_calls[0][3] == "entry_primary"


def test_live_mode_live_rollout_uses_executable_reader_before_legacy_fetch(monkeypatch) -> None:
    """Post-2026-05-04 default-ON gate: rollout-blocker fires BEFORE
    the executable-reader path. With no on-disk evidence, the gate
    short-circuits with EVIDENCE_MISSING; legacy fetch is never
    consulted (which is the property this test originally asserted).

    Kill-switch=0 here would expose the legacy path's
    ``ENTRY_FORECAST_READER_DB_UNAVAILABLE``; we keep the gate
    default-ON to pin the new dominant path.
    """

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
    assert decision.rejection_reasons == ["ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING"]
    assert decision.applied_validations == [
        "entry_forecast_rollout",
        "legacy_entry_primary_fetch_blocked",
    ]
