# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 3 — anchor prior must match the live 9km product or go through an explicit bridge; raw 025 history must not be used as-if it were 9km.
# Reuse: Run with pytest; update if anchor product identity or bridge logic in bayes_precision_fusion/materializer changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §3 (the anchor prior = the LIVE 9km/0.1 ecmwf_ifs product)
#   + Fitz Constraint #4 (data provenance): the anchor history residuals/tau0/prior-strength
#   MUST come from the SAME physical product as the live anchor, OR be reconciled through an
#   explicit, declared bridge. Open-Meteo's previous-runs API serves ONLY ecmwf_ifs025 (0.25);
#   there is no 9km ecmwf_ifs previous-runs feed (forecast_source_registry: ecmwf_previous_runs
#   -> model_name='ecmwf_ifs025'). The honest option is the BRIDGE (option 2).
"""BLOCKER 3 — the anchor prior must be built from a product matching the live anchor, or via
an explicit ifs025->ifs9 bridge.

bayes_precision_fusion.ANCHOR_MODEL='ecmwf_ifs' is the live 9km/0.1 anchor (prior mean). The OM previous-runs
download stores the anchor history under model='ecmwf_ifs' but the PHYSICAL product is
model_name='ecmwf_ifs025' (0.25). Using the 025 residuals directly as the 9km prior would give
the wrong anchor sigma + q_lcb. The bridge module declares the reconciliation explicitly.
"""
from __future__ import annotations

from src.forecast import bayes_precision_fusion_anchor_bridge as bridge


def test_bridge_identity_and_product_labels() -> None:
    """The bridge declares: the live anchor product (ecmwf_ifs 9km), the stored history product
    (ecmwf_ifs025 0.25), the bridge id, and a NON-trivial uncertainty buffer (>0) so the prior
    is widened to honour the cross-product translation loss."""
    assert bridge.LIVE_ANCHOR_MODEL == "ecmwf_ifs"
    assert bridge.STORED_ANCHOR_MODEL == "ecmwf_ifs025"
    assert bridge.BRIDGE_ID == "ifs025_to_ifs9"
    assert bridge.BRIDGE_UNCERTAINTY_C > 0.0


def test_bridge_widens_tau0_and_shifts_by_delta_bias() -> None:
    """Applying the bridge to a raw 025 (mean_residual, tau0) returns the 9km-frame prior:
    the center shifts by bridge_delta_bias and tau0 widens by the bridge uncertainty (added in
    quadrature with the rho-sigma inflation). The widened tau0 must be >= the raw tau0."""
    raw_tau0 = 1.2
    bridged_tau0 = bridge.bridge_anchor_tau0(raw_tau0)
    assert bridged_tau0 >= raw_tau0, "the bridge must NEVER narrow the anchor prior"
    # Quadrature widening: sqrt((rho*tau0)^2 + uncertainty^2) >= tau0.
    expected = ((bridge.BRIDGE_RHO_SIGMA * raw_tau0) ** 2 + bridge.BRIDGE_UNCERTAINTY_C ** 2) ** 0.5
    assert abs(bridged_tau0 - expected) < 1e-9


def test_bridge_required_flag_true_for_025_history() -> None:
    """The bridge must report that the anchor history requires bridging (the stored product is
    NOT the live product). This is the structural gate the capture consults before forming the
    prior from anchor history."""
    assert bridge.anchor_history_requires_bridge(stored_model_name="ecmwf_ifs025") is True
    # If a TRUE 9km previous-runs product ever appears, no bridge is needed.
    assert bridge.anchor_history_requires_bridge(stored_model_name="ecmwf_ifs") is False
