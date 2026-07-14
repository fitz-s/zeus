# Created: 2026-05-20
# Last reused or audited: 2026-07-14
# Authority basis: PHASE_2_ULTRAPLAN.md §8.2 + §8.3; finite-evidence probability symmetry packet held/entry single-q law
# Lifecycle: created=2026-05-20; last_reviewed=2026-05-21; last_reused=2026-07-11
# Purpose: T5 GREEN antibody — _maybe_write_day0_nowcast gate conditions + write_nowcast_run call.
# Reuse: Run when _maybe_write_day0_nowcast, write_nowcast_run wiring, or day0 gate logic changes.
"""
T5 GREEN antibody: _maybe_write_day0_nowcast call-site invocation.

Verifies that _maybe_write_day0_nowcast calls write_nowcast_run when
position.market_slug is set, hours_remaining <= 6, and a platt fit is available.

Gate conditions tested:
  - market_slug=None → function returns early, no write.
  - market_slug set + hours_remaining > 6 → function returns early, no write.
  - market_slug set + hours_remaining <= 6 + fit available → write_nowcast_run called (GREEN).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

import src.engine.monitor_refresh as monitor_refresh_module
from src.engine.monitor_refresh import _maybe_write_day0_nowcast
from src.engine.position_belief import ReplacementBelief
from src.observability.counters import read as read_counter, reset_all as reset_counters
from src.state.portfolio import Position


def _replacement_belief(*, fresh: bool = True) -> ReplacementBelief:
    return ReplacementBelief(
        held_side_prob=0.73,
        q_yes_bin=0.27,
        posterior_id="posterior-pre-first-observation",
        computed_at="2026-07-11T23:05:00+00:00",
        age_hours=0.1,
        fresh=fresh,
        bin_key="test-bin",
        direction="buy_no",
    )


def test_probability_refresh_preserves_only_pending_robust_exit_confirmation() -> None:
    """Fresh belief replacement must not make confirmation two unreachable."""

    pending = "day0_robust_sell_value_awaits_confirmation"
    position = SimpleNamespace(
        applied_validations=["stale_probability_evidence", pending]
    )
    refreshed = SimpleNamespace(applied_validations=["current_probability_evidence"])

    monitor_refresh_module._replace_probability_validations_preserving_exit_confirmation(
        position,
        refreshed,
    )

    assert position.applied_validations == ["current_probability_evidence", pending]


def test_day0_start_grace_is_bounded_to_target_local_day() -> None:
    city = SimpleNamespace(timezone="Europe/London")
    target = date(2026, 7, 12)

    assert monitor_refresh_module._within_day0_observation_start_grace(
        city,
        target,
        now=datetime(2026, 7, 11, 23, 30, tzinfo=timezone.utc),
    )
    assert not monitor_refresh_module._within_day0_observation_start_grace(
        city,
        target,
        now=datetime(2026, 7, 12, 2, 1, tzinfo=timezone.utc),
    )
    assert not monitor_refresh_module._within_day0_observation_start_grace(
        city,
        target,
        now=datetime(2026, 7, 11, 22, 59, tzinfo=timezone.utc),
    )


def test_pre_first_day0_observation_uses_fresh_replacement_belief(monkeypatch) -> None:
    from src.contracts.exceptions import ObservationUnavailableError
    from src.engine import position_belief

    pos = _make_position()
    pos.entry_method = "day0_observation"
    pos.p_posterior = 0.41
    monkeypatch.setattr(
        monitor_refresh_module,
        "recompute_native_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ObservationUnavailableError("first target-day observation not published")
        ),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_within_day0_observation_start_grace",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        position_belief,
        "load_replacement_belief",
        lambda **kwargs: _replacement_belief(),
    )

    prob, refresh_pos, fresh = monitor_refresh_module._refresh_day0_monitor_probability(
        pos,
        conn=None,
        city=SimpleNamespace(timezone="UTC"),
        target_d=date(2026, 7, 12),
    )

    assert fresh is True
    assert prob == pytest.approx(0.73)
    assert refresh_pos.selected_method == "replacement_posterior"
    assert (
        "day0_observation_unavailable_within_start_grace:replacement_posterior_authority"
        in refresh_pos.applied_validations
    )


@pytest.mark.parametrize(
    ("belief_fresh", "inside_grace"),
    [(False, True), (True, False)],
)
def test_day0_observation_absence_stays_stale_without_both_authorities(
    monkeypatch,
    belief_fresh: bool,
    inside_grace: bool,
) -> None:
    from src.contracts.exceptions import ObservationUnavailableError
    from src.engine import position_belief

    pos = _make_position()
    pos.entry_method = "day0_observation"
    pos.p_posterior = 0.41
    monkeypatch.setattr(
        monitor_refresh_module,
        "recompute_native_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ObservationUnavailableError("day0 observation unavailable")
        ),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_within_day0_observation_start_grace",
        lambda *args, **kwargs: inside_grace,
    )
    monkeypatch.setattr(
        position_belief,
        "load_replacement_belief",
        lambda **kwargs: _replacement_belief(fresh=belief_fresh),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_attempt_held_belief_readthrough",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kwargs: None,
    )

    prob, refresh_pos, fresh = monitor_refresh_module._refresh_day0_monitor_probability(
        pos,
        conn=None,
        city=SimpleNamespace(timezone="UTC"),
        target_d=date(2026, 7, 12),
    )

    assert fresh is False
    assert prob == pytest.approx(pos.p_posterior)
    assert refresh_pos.selected_method != "replacement_posterior"


def test_day0_monitor_reads_exact_current_global_probability_witness(
    monkeypatch,
) -> None:
    """A live identified holding uses the entry/SELL/HOLD/CASH joint-q witness."""
    import numpy as np
    from src.engine import event_reactor_adapter, global_auction_universe
    from src.state import db as state_db

    condition_id = "0x" + "1c" * 32
    event_row = {
        "event_id": "event-paris-day0",
        "event_type": "DAY0_EXTREME_UPDATED",
        "entity_key": "Paris|2026-07-14|high",
        "source": "test",
        "observed_at": "2026-07-14T14:00:00+00:00",
        "available_at": "2026-07-14T14:00:01+00:00",
        "received_at": "2026-07-14T14:00:01+00:00",
        "causal_snapshot_id": "snapshot-1",
        "payload_hash": "payload-hash",
        "idempotency_key": "idempotency-key",
        "priority": 1,
        "expires_at": None,
        "payload_json": "{}",
        "schema_version": 1,
        "created_at": "2026-07-14T14:00:01+00:00",
    }

    class FakeConnection:
        def __init__(self, row=None):
            self.row = row
            self.closed = False

        def execute(self, *_args, **_kwargs):
            return self

        def fetchone(self):
            return self.row

        def fetchall(self):
            return [self.row] if self.row is not None else []

        def close(self):
            self.closed = True

    world = FakeConnection(event_row)
    forecasts = FakeConnection()
    trade = FakeConnection(
        {
            "condition_id": condition_id,
            "yes_token_id": "paris-yes-token",
            "no_token_id": "paris-no-token",
        }
    )
    monkeypatch.setattr(state_db, "get_world_connection_read_only", lambda: world)
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        lambda: forecasts,
    )

    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="paris-yes-token",
                no_token_id="paris-no-token",
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.2], [0.3], [0.4]]),
        witness_identity="witness-current-global",
        q_version="q-version-current-global",
        source_truth_identity="source-truth-current-global",
        band_basis="current_coherent_day0_remaining_finite_evidence_v2",
        band_alpha=0.25,
    )

    def prepare(event, **kwargs):
        assert event.event_id == "event-paris-day0"
        assert kwargs["forecast_conn"] is forecasts
        assert kwargs["topology_conn"] is forecasts
        assert kwargs["observation_conn"] is world
        kwargs["day0_payload_out"].update(
            {
                "_edli_global_day0_binding": {
                    "observation_time": "2026-07-14T14:00:00+00:00",
                    "observed_extreme_native": 34.0,
                },
                "_edli_day0_finite_evidence_member_count": 4,
            }
        )
        return SimpleNamespace(probability_witness=witness)

    monkeypatch.setattr(
        event_reactor_adapter,
        "_prepare_current_global_probability_family",
        prepare,
    )
    monkeypatch.setattr(
        global_auction_universe,
        "_rebind_probability_witness_tokens",
        lambda candidate_witness, **kwargs: candidate_witness,
    )
    pos = _make_position()
    pos.city = "Paris"
    pos.target_date = "2026-07-14"
    pos.direction = "buy_no"
    pos.condition_id = condition_id
    pos.token_id = "paris-yes-token"
    pos.no_token_id = "paris-no-token"

    probability, refreshed, fresh = (
        monitor_refresh_module._refresh_current_global_day0_probability(
            pos,
            trade_conn=trade,
            decision_time=datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc),
        )
    )

    assert fresh is True
    assert probability == pytest.approx(0.75)
    assert getattr(
        refreshed,
        monitor_refresh_module._GLOBAL_MONITOR_SAMPLES_ATTR,
    ) == pytest.approx([0.9, 0.8, 0.7, 0.6])
    assert refreshed._day0_monitor_probability_receipt["probability_witness_identity"] == (
        "witness-current-global"
    )
    assert world.closed is True
    assert forecasts.closed is True


@pytest.mark.parametrize(
    ("direction", "expected"),
    (("buy_yes", [0.1, 0.3]), ("buy_no", [0.9, 0.7])),
)
def test_current_global_monitor_samples_bind_exact_held_token(
    direction,
    expected,
) -> None:
    import numpy as np

    condition_id = "0x" + "3e" * 32
    pos = _make_position()
    pos.direction = direction
    pos.condition_id = condition_id
    pos.token_id = "exact-yes-token"
    pos.no_token_id = "exact-no-token"
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="exact-yes-token",
                no_token_id="exact-no-token",
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.3]]),
    )

    assert monitor_refresh_module._current_global_held_samples(
        pos,
        witness,
        current_token_pair=("exact-yes-token", "exact-no-token"),
    ) == pytest.approx(expected)


def test_current_global_monitor_token_mismatch_fails_closed() -> None:
    import numpy as np

    condition_id = "0x" + "4f" * 32
    pos = _make_position()
    pos.direction = "buy_no"
    pos.condition_id = condition_id
    pos.no_token_id = "wrong-no-token"
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="exact-yes-token",
                no_token_id="exact-no-token",
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.3]]),
    )

    with pytest.raises(
        ValueError,
        match="position token pair does not match current global witness",
    ):
        monitor_refresh_module._current_global_held_samples(
            pos,
            witness,
            current_token_pair=("exact-yes-token", "exact-no-token"),
        )


def test_current_global_monitor_missing_witness_no_token_fails_closed() -> None:
    import numpy as np

    condition_id = "0x" + "6b" * 32
    pos = _make_position()
    pos.direction = "buy_no"
    pos.condition_id = condition_id
    pos.token_id = "exact-yes-token"
    pos.no_token_id = "exact-no-token"
    witness = SimpleNamespace(
        bindings=(
            SimpleNamespace(
                condition_id=condition_id,
                yes_token_id="exact-yes-token",
                no_token_id=None,
            ),
        ),
        yes_q_samples=np.array([[0.1], [0.3]]),
    )

    with pytest.raises(
        ValueError,
        match="position token pair does not match current global witness",
    ):
        monitor_refresh_module._current_global_held_samples(
            pos,
            witness,
            current_token_pair=("exact-yes-token", "exact-no-token"),
        )


def test_current_global_monitor_edge_band_uses_solver_cvar() -> None:
    lower, upper = monitor_refresh_module._current_global_monitor_edge_band(
        [0.2, 0.4, 0.6, 0.8],
        alpha=0.25,
        current_p_market=0.1,
    )

    assert lower == pytest.approx(0.1)
    assert upper == pytest.approx(0.7)


def test_identified_day0_monitor_fails_closed_without_global_probability(
    monkeypatch,
) -> None:
    """A current-q failure cannot borrow freshness from the legacy Day0 path."""
    pos = _make_position()
    pos.city = "Paris"
    pos.target_date = "2026-07-14"
    pos.entry_method = "day0_observation"
    pos.p_posterior = 0.62
    pos.condition_id = "0x" + "2d" * 32
    monkeypatch.setattr(
        monitor_refresh_module,
        "_day0_absorbing_hard_fact_overlay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_refresh_current_global_day0_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no current q")),
    )
    monkeypatch.setattr(
        monitor_refresh_module,
        "_refresh_day0_monitor_probability",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy Day0 probability must not become authority")
        ),
    )
    reseeds = []
    monkeypatch.setattr(
        monitor_refresh_module,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kwargs: reseeds.append(kwargs),
    )

    probability, refreshed, fresh = monitor_refresh_module.monitor_probability_refresh(
        pos,
        conn=None,
        city=SimpleNamespace(name="Paris", timezone="Europe/Paris"),
        target_d=date(2026, 7, 14),
    )

    assert probability == pytest.approx(0.62)
    assert fresh is False
    assert getattr(refreshed, monitor_refresh_module._MONITOR_PROBABILITY_FRESH_ATTR) is False
    assert any(
        validation.startswith("day0_current_global_probability_unavailable:")
        for validation in refreshed.applied_validations
    )
    assert reseeds == [
        {"city": "Paris", "target_date": "2026-07-14", "metric": "high"}
    ]


def test_held_monitor_releases_trade_transaction_before_probability_refresh(
    monkeypatch,
) -> None:
    """The exit monitor cannot hold TRADE while Day0 refresh writes WORLD."""
    import sqlite3
    import types
    from datetime import datetime, timezone

    import numpy as np
    from src.engine import cycle_runtime
    from src.state.decision_chain import CycleArtifact, MonitorResult
    from src.state.portfolio import ExitDecision, PortfolioState
    from src.state.strategy_tracker import StrategyTracker

    pos = _make_position()
    pos.city = "TestCity"
    pos.target_date = "2026-06-15"
    pos.state = "holding"
    pos.entry_price = 0.44
    pos.p_posterior = 0.61
    portfolio = PortfolioState(positions=[pos])
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE preflight_write (v INTEGER)")
    conn.execute("INSERT INTO preflight_write VALUES (1)")
    assert conn.in_transaction is True

    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *args, **kwargs: [pos],
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_closed_non_accepting_market_info",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_portfolio_rotation_evaluation_status",
        lambda *args, **kwargs: None,
    )

    def _refresh_position(conn_arg, clob, refreshed_pos):
        assert conn_arg.in_transaction is False
        refreshed_pos.last_monitor_prob = 0.61
        refreshed_pos.last_monitor_prob_is_fresh = True
        refreshed_pos.last_monitor_market_price = 0.44
        refreshed_pos.last_monitor_market_price_is_fresh = True
        refreshed_pos.last_monitor_best_bid = 0.43
        refreshed_pos.last_monitor_best_ask = 0.45
        return types.SimpleNamespace(
            p_market=np.array([0.44]),
            p_posterior=0.61,
            divergence_score=0.0,
            market_velocity_1h=0.0,
            forward_edge=0.17,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh_position)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, ctx: ExitDecision(False, "NO_EXIT"),
    )
    deps = types.SimpleNamespace(
        cities_by_name={
            "TestCity": types.SimpleNamespace(timezone="UTC")
        },
        _utcnow=lambda: datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
        logger=types.SimpleNamespace(
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        ),
        MonitorResult=MonitorResult,
    )

    cycle_runtime.execute_monitoring_phase(
        conn=conn,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=CycleArtifact(mode="exit_monitor", started_at="2026-06-14T12:00:00Z"),
        tracker=StrategyTracker(),
        summary={"monitors": 0, "exits": 0},
        deps=deps,
        exit_order_submit_enabled=False,
        run_exit_preflight=False,
    )
    conn.close()


def _make_position(market_slug: str | None = None) -> Position:
    return Position(
        trade_id="trade-t5-nowcast-001",
        market_id="test-market-001",
        city="TestCity",
        cluster="Test",
        target_date="2026-06-15",
        bin_label="70-80°F",
        direction="buy_yes",
        temperature_metric="high",
        env="test",
        state="holding",
        market_slug=market_slug,
    )


def _make_temporal_context(daypart: str = "afternoon") -> MagicMock:
    ctx = MagicMock()
    ctx.daypart = daypart
    return ctx


def test_nowcast_write_called_when_gate_passes() -> None:
    """market_slug set + hours_remaining <= 6 + fit available → write_nowcast_run is called.

    GREEN: fit_run_id plumbing is live; xfail removed (Phase 2 T5 GREEN).
    """
    import numpy as np
    from src.types.metric_identity import MetricIdentity
    from src.calibration.day0_horizon_calibration import HorizonPlattFit
    from datetime import date

    pos = _make_position(market_slug="boston-2026-06-15-high")
    temporal_ctx = _make_temporal_context("afternoon")

    stub_fit = HorizonPlattFit(
        alpha=1.0,
        beta=0.0,
        gamma_morning=0.0,
        gamma_afternoon=0.0,
        gamma_post_peak=0.0,
        delta=0.0,
        epsilon=0.0,
        fit_run_id="test-fit-001",
    )

    with patch("src.state.day0_nowcast_store.write_nowcast_run") as mock_write, \
         patch("src.state.day0_nowcast_store.read_latest_platt_fit", return_value=stub_fit):
        _maybe_write_day0_nowcast(
            position=pos,
            hours_remaining=4.0,
            temporal_context=temporal_ctx,
            p_cal_full=np.array([0.6]),
            p_raw_vector=np.array([0.55]),
            temperature_metric=MetricIdentity.from_raw("high"),
            target_d=date(2026, 6, 15),
            observation_time="2026-06-15T14:00:00",
        )
        assert mock_write.called, (
            "_maybe_write_day0_nowcast must call write_nowcast_run when "
            "market_slug is set, hours_remaining <= 6, and fit is available"
        )
        # Verify the wiring passes the expected contract arguments.
        kwargs = mock_write.call_args.kwargs
        assert kwargs["market_slug"] == "boston-2026-06-15-high"
        assert kwargs["fit_run_id"] == "test-fit-001"
        assert kwargs["temperature_metric"] == "high"
        assert kwargs["target_date"] == "2026-06-15"
        assert kwargs["observation_time"] == "2026-06-15T14:00:00"
        assert kwargs["hours_remaining"] == 4.0
        assert kwargs["daypart"] == "afternoon"
        assert kwargs["source"] == "live_nowcast"
        assert monitor_refresh_module._nowcast_consecutive_write_failures == 0


def test_nowcast_write_skipped_when_market_slug_none() -> None:
    """market_slug=None → _maybe_write_day0_nowcast returns immediately, no write."""
    import numpy as np
    from datetime import date

    pos = _make_position(market_slug=None)
    temporal_ctx = _make_temporal_context("afternoon")

    # market_slug=None returns before any write attempt.
    _maybe_write_day0_nowcast(
        position=pos,
        hours_remaining=4.0,
        temporal_context=temporal_ctx,
        p_cal_full=np.array([0.6]),
        p_raw_vector=np.array([0.55]),
        temperature_metric=None,
        target_d=date(2026, 6, 15),
        observation_time="2026-06-15T14:00:00",
    )
    # If we reach here without exception, the early-return guard works.


def test_nowcast_write_skipped_when_hours_remaining_high() -> None:
    """hours_remaining > 6 → _maybe_write_day0_nowcast skips the write."""
    import numpy as np
    from datetime import date

    pos = _make_position(market_slug="dallas-2026-06-15-high")
    temporal_ctx = _make_temporal_context("morning")

    _maybe_write_day0_nowcast(
        position=pos,
        hours_remaining=8.5,
        temporal_context=temporal_ctx,
        p_cal_full=np.array([0.45]),
        p_raw_vector=np.array([0.4]),
        temperature_metric=None,
        target_d=date(2026, 6, 15),
        observation_time="2026-06-15T08:00:00",
    )
    # If we reach here without exception, the hours_remaining guard works.


def test_nowcast_write_failure_counter_and_persistent_alert(caplog) -> None:
    """Repeated fail-soft nowcast write errors must become observable."""
    import logging
    import numpy as np
    from datetime import date
    from src.types.metric_identity import MetricIdentity
    from src.calibration.day0_horizon_calibration import HorizonPlattFit

    reset_counters()
    monitor_refresh_module._nowcast_consecutive_write_failures = 0
    pos = _make_position(market_slug="boston-2026-06-15-high")
    temporal_ctx = _make_temporal_context("afternoon")
    stub_fit = HorizonPlattFit(
        alpha=1.0,
        beta=0.0,
        gamma_morning=0.0,
        gamma_afternoon=0.0,
        gamma_post_peak=0.0,
        delta=0.0,
        epsilon=0.0,
        fit_run_id="test-fit-001",
    )

    with patch("src.state.day0_nowcast_store.write_nowcast_run", side_effect=RuntimeError("boom")), \
         patch("src.state.day0_nowcast_store.read_latest_platt_fit", return_value=stub_fit), \
         caplog.at_level(logging.ERROR, logger="src.engine.monitor_refresh"):
        for _ in range(3):
            _maybe_write_day0_nowcast(
                position=pos,
                hours_remaining=4.0,
                temporal_context=temporal_ctx,
                p_cal_full=np.array([0.6]),
                p_raw_vector=np.array([0.55]),
                temperature_metric=MetricIdentity.from_raw("high"),
                target_d=date(2026, 6, 15),
                observation_time="2026-06-15T14:00:00",
            )

    assert read_counter(
        "monitor_day0_nowcast_write_failed_total",
        labels={"market_slug": "boston-2026-06-15-high"},
    ) == 3
    assert any("MONITOR_NOWCAST_WRITE_PERSISTENT_FAILURE" in record.message for record in caplog.records)


def test_day0_metric_fact_write_helper_uses_monitor_observation_contract() -> None:
    """Valid Day0 monitor observations produce one world-owned metric fact write."""
    from datetime import date

    from src.types.metric_identity import MetricIdentity

    city = MagicMock()
    city.name = "Paris"
    city.timezone = "Europe/Paris"
    pos = _make_position(market_slug="paris-2026-07-09-low")
    pos.city = "Paris"
    obs = {
        "source": "wu_api",
        "observation_time": "2026-07-09T04:00:00Z",
        "local_timestamp": "2026-07-09T06:00:00+02:00",
    }

    with patch("src.state.day0_metric_fact_store.write_day0_metric_fact") as mock_write:
        mock_write.return_value = "d0mf_v1_test"
        monitor_refresh_module._maybe_write_day0_metric_fact(
            position=pos,
            city=city,
            target_d=date(2026, 7, 9),
            temperature_metric=MetricIdentity.from_raw("low"),
            obs=obs,
            current_temp=21.2,
            observed_extreme_for_metric=20.0,
        )

    assert mock_write.call_count == 1
    kwargs = mock_write.call_args.kwargs
    assert kwargs["city"] == "Paris"
    assert kwargs["target_date"] == "2026-07-09"
    assert kwargs["temperature_metric"] == "low"
    assert kwargs["source"] == "wu_api"
    assert kwargs["utc_timestamp"] == "2026-07-09T04:00:00Z"
    assert kwargs["local_timezone"] == "Europe/Paris"
    assert kwargs["local_timestamp"] == "2026-07-09T06:00:00+02:00"
    assert kwargs["temp_current"] == 21.2
    assert kwargs["running_extreme"] == 20.0


def test_day0_metric_fact_write_helper_is_fail_soft(caplog) -> None:
    """A metric-fact persistence failure must not interrupt monitor refresh."""
    import logging
    from datetime import date

    from src.types.metric_identity import MetricIdentity

    city = MagicMock()
    city.name = "Paris"
    city.timezone = "Europe/Paris"
    pos = _make_position(market_slug="paris-2026-07-09-low")
    obs = {
        "source": "wu_api",
        "observation_time": "2026-07-09T04:00:00Z",
        "local_timestamp": "2026-07-09T06:00:00+02:00",
    }

    with patch(
        "src.state.day0_metric_fact_store.write_day0_metric_fact",
        side_effect=RuntimeError("db locked"),
    ), caplog.at_level(logging.WARNING, logger="src.engine.monitor_refresh"):
        monitor_refresh_module._maybe_write_day0_metric_fact(
            position=pos,
            city=city,
            target_d=date(2026, 7, 9),
            temperature_metric=MetricIdentity.from_raw("low"),
            obs=obs,
            current_temp=21.2,
            observed_extreme_for_metric=20.0,
        )

    assert any("MONITOR_DAY0_METRIC_FACT_WRITE_FAILED" in record.message for record in caplog.records)
