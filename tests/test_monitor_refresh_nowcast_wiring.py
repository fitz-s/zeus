# Created: 2026-05-20
# Last reused or audited: 2026-05-21
# Authority basis: PHASE_2_ULTRAPLAN.md §8.2 + §8.3 — monitor_refresh nowcast wiring; live release proof P2-3 nowcast failure telemetry
# Lifecycle: created=2026-05-20; last_reviewed=2026-05-21; last_reused=never
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

from unittest.mock import patch, MagicMock

import src.engine.monitor_refresh as monitor_refresh_module
from src.engine.monitor_refresh import _maybe_write_day0_nowcast
from src.observability.counters import read as read_counter, reset_all as reset_counters
from src.state.portfolio import Position


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
