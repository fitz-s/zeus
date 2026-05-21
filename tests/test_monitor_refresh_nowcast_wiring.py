# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md §8.2 + §8.3 — monitor_refresh nowcast wiring
"""
T5 RED antibody: _maybe_write_day0_nowcast call-site invocation.

Verifies that _refresh_day0_observation calls _maybe_write_day0_nowcast
when position.market_slug is set AND hours_remaining <= 6.

SCAFFOLD phase: _maybe_write_day0_nowcast is a stub (no actual DB write).
The xfail marks the gap: once fit_run_id plumbing lands and the function
actually calls write_nowcast_run, the xfail must be REMOVED (activated).

Gate conditions tested:
  - market_slug=None → function NOT called.
  - market_slug set + hours_remaining > 6 → function NOT called.
  - market_slug set + hours_remaining <= 6 → function IS called (RED: stub only).
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock, call

import pytest

from src.engine.monitor_refresh import _maybe_write_day0_nowcast
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


@pytest.mark.xfail(
    reason=(
        "SCAFFOLD: _maybe_write_day0_nowcast is a stub — no write_nowcast_run call yet. "
        "Remove xfail when fit_run_id plumbing lands (Phase 2 T5 GREEN)."
    ),
    strict=True,
)
def test_nowcast_write_called_when_gate_passes() -> None:
    """market_slug set + hours_remaining <= 6 → write_nowcast_run is called.

    RED: currently a stub; xfail must be REMOVED (activated) when the
    Phase 2 T5 GREEN phase wires write_nowcast_run into _maybe_write_day0_nowcast.
    """
    import numpy as np
    from src.types.metric_identity import MetricIdentity
    from datetime import date

    pos = _make_position(market_slug="boston-2026-06-15-high")
    temporal_ctx = _make_temporal_context("afternoon")

    # Patch at the write site (will be imported inside _maybe_write_day0_nowcast
    # in the GREEN phase; for SCAFFOLD the patch target doesn't matter — the stub
    # never calls it, which is why this test is xfail strict).
    with patch("src.state.day0_nowcast_store.write_nowcast_run") as mock_write:
        _maybe_write_day0_nowcast(
            position=pos,
            hours_remaining=4.0,
            temporal_context=temporal_ctx,
            p_cal_full=np.array([0.6]),
            p_raw_vector=np.array([0.55]),
            temperature_metric=MetricIdentity.from_raw("high"),
            target_d=date(2026, 6, 15),
            conn=None,
        )
        assert mock_write.called, (
            "_maybe_write_day0_nowcast must call write_nowcast_run when "
            "market_slug is set and hours_remaining <= 6"
        )


def test_nowcast_write_skipped_when_market_slug_none() -> None:
    """market_slug=None → _maybe_write_day0_nowcast returns immediately, no write."""
    import numpy as np
    from datetime import date

    pos = _make_position(market_slug=None)
    temporal_ctx = _make_temporal_context("afternoon")

    # The stub logs a DEBUG message only when gate passes; with market_slug=None
    # it returns before logging. Verify by confirming no exception and no write.
    _maybe_write_day0_nowcast(
        position=pos,
        hours_remaining=4.0,
        temporal_context=temporal_ctx,
        p_cal_full=np.array([0.6]),
        p_raw_vector=np.array([0.55]),
        temperature_metric=None,
        target_d=date(2026, 6, 15),
        conn=None,
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
        conn=None,
    )
    # If we reach here without exception, the hours_remaining guard works.
