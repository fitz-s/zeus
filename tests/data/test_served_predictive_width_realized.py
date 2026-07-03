# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: frontier consult REQ-20260629-131502 + measured over-dispersion (served sigma ~3.0
#   vs realized RMSE 1.35, PIT mound chi2=218, 50%CI covers 82%) + src/forecast/sigma_authority.py
#   rebuilt semantics (serve realized width, NOT max(RSS, floor)). The live materializer's served POINT
#   width double-counts center uncertainty: predictive_sigma_c = max(1.0, sqrt(fused.sd^2 + sigma_resid^2))
#   adds fused.sd on top of sigma_resid, which is ALREADY the realized fused-center error. The served
#   point width must be the realized width alone; fused.sd belongs only in the q_lcb/q_ucb center-
#   uncertainty bootstrap (carried separately as anchor_sigma_c).
"""Served point predictive width = realized walk-forward error, NOT realized (+) center-uncertainty.

Pins the double-count fix: the point predictive sigma that feeds bin_probability_settlement must be the
realized fused-center residual width (floored), and must NOT add the center posterior sd (fused.sd).
"""
from __future__ import annotations

import math

from src.data.replacement_forecast_materializer import served_predictive_sigma_c


def test_serves_realized_width_floored():
    # Realized width above the floor is served as-is (no inflation).
    assert served_predictive_sigma_c(1.35) == 1.35


def test_floor_binds_below_one():
    assert served_predictive_sigma_c(0.4) == 1.0


def test_does_not_add_center_uncertainty():
    # OLD bug: max(1.0, sqrt(fused.sd^2 + sigma_resid^2)). With fused.sd=2.7, sigma_resid=1.35 the old
    # code served ~3.02. The fix serves the realized width 1.35 -- fused.sd is NOT a point-width input.
    sigma_resid = 1.35
    old_rss = math.sqrt(2.7 ** 2 + sigma_resid ** 2)  # ~3.02 (the double-count)
    served = served_predictive_sigma_c(sigma_resid)
    assert served == 1.35
    assert served < old_rss  # the inflation is gone
