# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R_BAYES_SPEC.md §3 (anchor prior = the LIVE 9km/0.1 ecmwf_ifs product);
#   Fitz Constraint #4 (data provenance: the anchor history product must match the live anchor
#   OR be reconciled through an explicit declared bridge). BLOCKER 3 (PR#400 review): the
#   chosen honest option for what Open-Meteo actually serves.
"""BLOCKER 3 — the explicit ifs025 -> ifs9 anchor bridge.

THE PROVENANCE PROBLEM. u0r_bayes.ANCHOR_MODEL = 'ecmwf_ifs' is the LIVE anchor: the
Open-Meteo ECMWF IFS 9km / 0.1-degree deterministic product (the prior mean of the U0R fusion).
But the anchor's WALK-FORWARD history can only come from Open-Meteo's previous-runs API, and
that API serves the ECMWF deterministic feed ONLY as 'ecmwf_ifs025' — the 0.25-degree product
(see src/data/forecast_source_registry.py: ecmwf_previous_runs -> model_name='ecmwf_ifs025';
the previous-runs endpoint has no 9km ECMWF feed). So the anchor residuals / tau0 / prior
strength would be estimated from a DIFFERENT physical product (0.25) than the live 9km anchor.

THE THREE OPTIONS (brief):
  (1) use real ecmwf_ifs 9km previous-runs — NOT AVAILABLE (OM serves no such feed).
  (2) store the 025 history with explicit bridge metadata and consume it ONLY via the bridge.
  (3) no bridge -> do NOT use 025 as the 9km prior; use a conservative wider fallback.

CHOSEN: option (2), the explicit bridge — it is the honest description of what OM serves and it
keeps the (valuable) ECMWF history while declaring the cross-product reconciliation. The bridge
is conservative: it NEVER narrows the anchor prior. We have no empirically-calibrated systematic
025->9km bias in this PR, so the bias delta is 0 (we do not invent a correction we cannot
justify), the 025 residual spread is treated as a LOWER BOUND on the 9km anchor's own spread
(rho = 1.0), and an additive uncertainty buffer accounts for the translation loss between the
0.25 and 9km products. The result: anchor_tau0 is the 025 stdev WIDENED in quadrature, so the
soft-anchor sigma + q_lcb honour the fact that the prior was sourced from a coarser product.

If a native 9km ECMWF previous-runs product later appears, anchor_history_requires_bridge()
returns False for it and the raw history is used directly (no widening).
"""
from __future__ import annotations

import math

# The live anchor product (prior mean) vs the stored history product (OM previous-runs).
LIVE_ANCHOR_MODEL = "ecmwf_ifs"        # 9km / 0.1-degree deterministic (the live anchor).
STORED_ANCHOR_MODEL = "ecmwf_ifs025"   # 0.25-degree deterministic (the only OM prev-runs ECMWF).
BRIDGE_ID = "ifs025_to_ifs9"

# Bridge reconciliation parameters (conservative, documented):
#   delta_bias: claimed systematic mean shift 025->9km. ZERO — no empirically-calibrated bias in
#     this PR; we do not invent a correction we cannot justify (widen-only honesty).
#   rho_sigma:  scale on the 025 residual std carried into the 9km frame. 1.0 — the 025 spread is
#     a LOWER BOUND on the 9km anchor's own spread (a coarser grid is not MORE certain).
#   uncertainty_c: additive degC buffer (in quadrature) for the cross-product translation loss.
BRIDGE_DELTA_BIAS_C = 0.0
BRIDGE_RHO_SIGMA = 1.0
BRIDGE_UNCERTAINTY_C = 0.5


def anchor_history_requires_bridge(*, stored_model_name: str | None) -> bool:
    """True iff the anchor walk-forward history's PHYSICAL product is not the live anchor product.

    The download stamps the OM model id actually addressed into raw_model_forecasts.model_name
    (BLOCKER 4). When that is the 0.25 product ('ecmwf_ifs025') the history must be bridged before
    it becomes the 9km prior. A native 9km history ('ecmwf_ifs') needs no bridge.
    """
    if stored_model_name is None:
        # Unknown product provenance -> be conservative and bridge (widen). Provenance-first.
        return True
    return stored_model_name != LIVE_ANCHOR_MODEL


def bridge_anchor_center(raw_center_c: float) -> float:
    """Shift the 025-frame anchor center into the 9km frame by the (declared) bridge bias."""
    return float(raw_center_c) + BRIDGE_DELTA_BIAS_C


def bridge_anchor_tau0(raw_tau0_c: float) -> float:
    """Widen the 025-frame anchor std into the 9km-frame prior std (NEVER narrows).

    bridged_tau0 = sqrt((rho_sigma * raw_tau0)^2 + uncertainty_c^2). With rho=1 and a positive
    uncertainty buffer this is strictly >= raw_tau0, so a 025-sourced prior is more uncertain
    than if it had been the native 9km product.
    """
    raw = max(0.0, float(raw_tau0_c))
    return math.sqrt((BRIDGE_RHO_SIGMA * raw) ** 2 + BRIDGE_UNCERTAINTY_C ** 2)


def bridge_metadata(*, stored_model_name: str | None) -> dict[str, object]:
    """The provenance block recorded on the posterior when the anchor prior is bridged."""
    return {
        "bridge_id": BRIDGE_ID,
        "stored_model": STORED_ANCHOR_MODEL,
        "live_anchor_model": LIVE_ANCHOR_MODEL,
        "stored_model_name_observed": stored_model_name,
        "bridge_delta_bias_c": BRIDGE_DELTA_BIAS_C,
        "bridge_rho_sigma": BRIDGE_RHO_SIGMA,
        "bridge_uncertainty_c": BRIDGE_UNCERTAINTY_C,
        "applied": anchor_history_requires_bridge(stored_model_name=stored_model_name),
    }
