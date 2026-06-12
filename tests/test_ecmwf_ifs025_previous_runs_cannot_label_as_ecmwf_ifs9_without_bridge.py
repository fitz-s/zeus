# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 3 — 025 previous-runs history must be bridged (ifs025->ifs9 widening) before becoming the 9km anchor prior; un-bridged use is forbidden.
# Reuse: Run with pytest; update if ifs025->ifs9 bridge logic or anchor_tau0 computation changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §3 anchor + Fitz Constraint #4. The capture forms the
#   anchor prior (anchor_z/anchor_tau0) from the anchor walk-forward history. That history's
#   physical product is ecmwf_ifs025 (0.25), NOT the live 9km ecmwf_ifs. The capture must NOT
#   pass the raw 025 tau0 through as if it were a 9km prior — it must apply the declared
#   ifs025->ifs9 bridge (widen tau0), so the anchor sigma honours the cross-product gap.
"""BLOCKER 3 — 025 previous-runs history must be bridged before it becomes the 9km anchor prior.

The capture builds anchor_tau0 from statistics.stdev(anchor history residuals). Those residuals
are ecmwf_ifs025 (the only ECMWF previous-runs product OM serves). Without the bridge the
produced anchor_tau0 would equal the raw 025 stdev — silently labelling a 0.25 product's
uncertainty as the 9km anchor's. This test pins that the capture's anchor_tau0 is the BRIDGED
(>= raw) value, proving the 025 history is not mislabelled as 9km.
"""
from __future__ import annotations

import statistics
from datetime import date

from src.data.bayes_precision_fusion_capture import ModelHistory, capture_bayes_precision_instruments
from src.forecast import bayes_precision_fusion_anchor_bridge as bridge
from src.forecast.model_selection import ANCHOR_MODEL
from src.forecast.bayes_precision_fusion import MIN_TRAIN


def _history_provider(histories):
    def _provider(*, city, metric, lead_days, target_date, models):
        return {m: histories[m] for m in models if m in histories}
    return _provider


def _make_anchor_history(n: int) -> ModelHistory:
    # Forecasts + settlements with a known residual spread so the raw 025 tau0 is well-defined.
    dates = [f"2026-04-{(i % 28) + 1:02d}" for i in range(n)]
    # ensure unique dates so the date-aligned path has a full window
    dates = [(date(2026, 4, 1).toordinal() + i) for i in range(n)]
    iso = [date.fromordinal(d).isoformat() for d in dates]
    fcs = tuple(20.0 + (0.5 if i % 2 else -0.5) for i in range(n))
    settles = tuple(20.0 for _ in range(n))
    return ModelHistory(
        model=ANCHOR_MODEL, forecast_values=fcs, settlement_values=settles,
        target_dates=tuple(iso),
    )


def test_capture_anchor_tau0_is_bridged_from_025_not_raw() -> None:
    n = MIN_TRAIN + 5
    anchor_hist = _make_anchor_history(n)
    histories = {ANCHOR_MODEL: anchor_hist}

    # No live extras -> only the anchor prior is formed (likelihood empty is fine for this test).
    result = capture_bayes_precision_instruments(
        city="Paris", metric="high", latitude=48.967, longitude=2.428,
        timezone_name="Europe/Paris", run="2026-06-06T00:00:00+00:00",
        target_local_date=date(2026, 6, 7), lead_days=1,
        anchor_z_corrected=20.0,
        history_provider=_history_provider(histories),
        live_fetch=lambda **k: None,  # no extras survive
    )

    raw_tau0 = statistics.stdev(anchor_hist.residuals)
    expected_bridged = bridge.bridge_anchor_tau0(raw_tau0)

    assert result.anchor_tau0 is not None
    assert abs(result.anchor_tau0 - expected_bridged) < 1e-9, (
        f"anchor_tau0 must be the BRIDGED 025->9km value {expected_bridged}, not the raw 025 "
        f"stdev {raw_tau0}; got {result.anchor_tau0}"
    )
    assert result.anchor_tau0 >= raw_tau0, "the bridge must never narrow the anchor prior"


def test_bridge_constants_are_documented_and_conservative() -> None:
    """The bridge uncertainty must be a real, positive widening (not 0), so a 025-sourced prior
    is strictly more uncertain than if it were the native 9km product."""
    assert bridge.BRIDGE_UNCERTAINTY_C > 0.0
    assert bridge.bridge_anchor_tau0(1.0) > 1.0
