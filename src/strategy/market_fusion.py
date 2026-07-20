"""Market fusion: posterior belief construction and legacy VWMP blending.

Legacy Spec §4.5 blended p_cal with p_market. Corrected pricing semantics
separate executable quotes from posterior belief, so raw VWMP inputs are now
accepted only through the explicit legacy mode.
"""

from typing import Literal

import numpy as np

from src.config import settings
from src.contracts.alpha_decision import AlphaDecision
from src.contracts.tail_treatment import TailTreatment
from src.contracts.vig_treatment import VigTreatment
from src.types.temperature import TemperatureDelta


class AuthorityViolation(ValueError):
    """Raised when the computation chain receives UNVERIFIED calibration data.

    K4 hard gate: market_fusion refuses to compute alpha on data whose
    provenance has not been verified. This is not a soft warning -- it is
    a hard raise that prevents UNVERIFIED data from entering the edge
    computation chain.
    """

# Spread thresholds defined in °F, auto-converted via .to() for any unit.
# This prevents the legacy-predecessor bug where 2.0 was used for both °F and °C cities.
# HARDCODED(setting_key="edge.spread_tight_f", note_key="edge._spread_tight_f_note",
#           tier=2, replace_after="1000+ ENS snapshots per city",
#           data_needed="per-city spread distribution percentiles")
SPREAD_TIGHT = TemperatureDelta(settings["edge"]["spread_tight_f"], "F")
# HARDCODED(setting_key="edge.spread_wide_f", note_key="edge._spread_wide_f_note",
#           tier=2, replace_after="1000+ ENS snapshots per city",
#           data_needed="per-city spread distribution percentiles")
SPREAD_WIDE = TemperatureDelta(settings["edge"]["spread_wide_f"], "F")

# HARDCODED(setting_key="edge.base_alpha", note_key="edge._base_alpha_note",
#           tier=1, replace_after="100+ settlements",
#           data_needed="Model Brier vs Market Brier per calibration level")
BASE_ALPHA_BY_LEVEL = {
    1: settings["edge"]["base_alpha"]["level1"],
    2: settings["edge"]["base_alpha"]["level2"],
    3: settings["edge"]["base_alpha"]["level3"],
    4: settings["edge"]["base_alpha"]["level4"],
}
TAIL_ALPHA_SCALE = 0.5  # Validated: sweep [0.5, 0.6, ..., 1.0], 0.5 is Brier-optimal
DEFAULT_TAIL_TREATMENT = TailTreatment(
    scale_factor=TAIL_ALPHA_SCALE,
    serves="calibration_accuracy",
    validated_against=(
        "D3 sweep 2026-03-31 tail bins, Brier improvement -0.042; "
        "not validated against buy_no P&L"
    ),
)
COMPLETE_MARKET_VIG_MIN = 0.90
COMPLETE_MARKET_VIG_MAX = 1.10
LEGACY_POSTERIOR_MODE = "legacy_vwmp_prior_v0"
MODEL_ONLY_POSTERIOR_MODE = "model_only_v1"
PosteriorMode = Literal[
    "legacy_vwmp_prior_v0",
    "model_only_v1",
]


def vwmp(best_bid: float, best_ask: float,
         bid_size: float, ask_size: float) -> float:
    """Volume-Weighted Micro-Price. Spec §4.1.

    If total_size <= 0: raise ValueError("Illiquid market: VWMP total size is 0.")
    Per CLAUDE.md: never use mid-price for edge calculations (VWMP required).
    """
    total = bid_size + ask_size
    if total <= 0:
        raise ValueError("Illiquid market: VWMP total size is 0, cannot fall back to mid-price")
    return (best_bid * ask_size + best_ask * bid_size) / total


def compute_alpha(
    calibration_level: int,
    ensemble_spread: TemperatureDelta,
    model_agreement: str,
    lead_days: float,
    hours_since_open: float,
    city_name: str = "",
    season: str = "",
    *,
    authority_verified: bool,
) -> AlphaDecision:
    """Compute α for model-market blending. Spec §4.5.

    Higher α → trust model more. Lower α → trust market more.
    Clamped to [0.20, 0.85].

    α is adjusted by PER-DECISION signals (validated 2026-03-31):
    - D4: ENS spread (tight → +0.10, wide → -0.15)
    - D3: tail bin scaling (applied in compute_posterior, not here)
    - Lead days (short → +0.05, long → -0.05)

    ensemble_spread must be a TemperatureDelta. This is a hard rule:
    spread thresholds are unit-aware and must not silently fall back to bare floats.
    """
    # K4 authority hard gate: refuse UNVERIFIED calibration data.
    # The evaluator already gates via get_pairs_for_bucket(authority_filter='VERIFIED');
    # this is a second line of defense at the market_fusion boundary.
    if not authority_verified:
        raise AuthorityViolation(
            f"market_fusion refused UNVERIFIED calibration for "
            f"{city_name!r}/{season!r} "
            f"(calibration_level={calibration_level})"
        )

    if not isinstance(ensemble_spread, TemperatureDelta):
        raise TypeError(
            "compute_alpha requires ensemble_spread to be TemperatureDelta. "
            "Wrap raw spreads with the city settlement unit first."
        )

    # ALPHA IS DORMANT — serve the calibration-level base weight, nothing added. The returned value is
    # DISCARDED on every live and replay path: all call sites build MarketAnalysis with
    # posterior_mode=MODEL_ONLY_POSTERIOR_MODE, and compute_posterior's MODEL_ONLY branch returns the
    # normalized calibrated belief WITHOUT ever reading alpha, so alpha never blends into a live
    # decision. The eight per-decision "adjustments" that used to shape it (a += 0.10 / a -= 0.15 /
    # a -= 0.10 / a -= 0.20 / a += 0.05 / a -= 0.05 / a += 0.10 / a += 0.05) were HARDCODED constants
    # stapled onto a continuous weight via threshold branches on continuously-varying signals
    # (ensemble_spread, lead_days, hours_since_open) — the forbidden "fixed constant added to a
    # continuously-varying value" pattern (a static offset cannot correct a varying quantity). Deleted:
    # byte-identical live (the value is discarded). If market-blending is ever revived, alpha must be a
    # DATA-DRIVEN function of measured model-vs-market skill, never these magic offsets. The
    # authority_verified gate and the ensemble_spread type check above are untouched.
    base = BASE_ALPHA_BY_LEVEL[calibration_level]
    return AlphaDecision(
        value=max(0.20, min(0.85, base)),
        optimization_target="risk_cap",
        evidence_basis="D1 resolution: conservative blending weight, not pure Brier minimizer",
        ci_bound=0.05,
    )


def compute_posterior(
    p_cal: np.ndarray,
    p_market: np.ndarray | None = None,
    alpha: float = 1.0,
    bins: list = None,
    *,
    posterior_mode: PosteriorMode = MODEL_ONLY_POSTERIOR_MODE,
    allow_legacy_quote_prior: bool = False,
) -> np.ndarray:
    """Compute alpha-weighted posterior, normalized to sum=1.0. Spec §4.5.

    Corrected mode consumes only calibrated belief. Raw executable quote/VWMP
    vectors remain supported only in ``legacy_vwmp_prior_v0`` so old call sites
    are explicit about the semantic debt they carry.

    D3 analysis (2026-03-31): tail bins are 5.3x harder for the model
    (Brier 0.67 vs 0.11). Per-bin α scaling at 0.5 for tails reduces
    overall Brier by 0.042. When bins are provided, tail bins get
    α_tail = α × TAIL_ALPHA_SCALE.

    Complete p_market vectors sum to plausible vig (~0.90-1.10), not 1.0, so vig is
    removed before blending. Sparse monitor vectors are not complete market
    families and stay in raw observed-price space. The final posterior is still
    normalized because per-bin tail alpha can make the blended vector drift.
    """
    p_cal_arr = np.asarray(p_cal, dtype=float)
    if not np.all(np.isfinite(p_cal_arr)):
        raise ValueError("p_cal must be finite")
    if np.any(p_cal_arr < 0.0):
        raise ValueError("p_cal must be non-negative")

    if posterior_mode == MODEL_ONLY_POSTERIOR_MODE:
        if p_market is not None:
            raise TypeError("model_only_v1 posterior cannot accept market quote/prior input")
        raw = p_cal_arr.copy()
        total = raw.sum()
        if total > 0:
            return raw / total
        return raw

    if posterior_mode == LEGACY_POSTERIOR_MODE:
        if not allow_legacy_quote_prior:
            raise ValueError("legacy VWMP market prior is disabled for this computation")
        market = _legacy_market_vector(p_market, p_cal_arr)
    else:
        raise ValueError(f"unknown posterior_mode: {posterior_mode!r}")

    if bins is not None and len(bins) == len(p_cal):
        alpha_vec = np.array([alpha_for_bin(alpha, b) for b in bins], dtype=float)
        raw = alpha_vec * p_cal_arr + (1.0 - alpha_vec) * market
    else:
        raw = alpha * p_cal_arr + (1.0 - alpha) * market

    total = raw.sum()
    if total > 0:
        return raw / total
    return raw


def _legacy_market_vector(
    p_market: np.ndarray | None,
    p_cal: np.ndarray,
) -> np.ndarray:
    if p_market is None:
        raise TypeError("legacy_vwmp_prior_v0 requires p_market")

    market_arr = np.asarray(p_market, dtype=float)
    if not np.all(np.isfinite(market_arr)):
        raise ValueError("p_market must be finite")
    if np.any(market_arr < 0.0):
        raise ValueError("p_market must be non-negative")
    market_total = float(np.sum(market_arr))
    if market_total <= 0.0:
        raise ValueError(f"Invalid market probability vector sum <= 0: {market_total}")

    positive_components = int(np.count_nonzero(market_arr > 0.0))
    looks_complete = positive_components >= min(len(market_arr), 2)
    has_zeros = bool(np.any(market_arr == 0.0))
    if looks_complete and COMPLETE_MARKET_VIG_MIN <= market_total <= COMPLETE_MARKET_VIG_MAX:
        return VigTreatment.from_raw(market_arr).clean_prices
    if has_zeros:
        # Legacy-only: sparse monitor vectors have zeros for non-held bins.
        # Corrected modes reject this shape instead of laundering held-token
        # quote data into a family prior.
        return VigTreatment.from_raw(
            market_arr,
            sibling_snapshot=p_cal,
            imputation_source="p_cal_fallback",
        ).clean_prices

    # Distorted-vig complete market (vig out of [0.90, 1.10] band, no zeros).
    # Pre-T6.3 behavior preserved in legacy mode only.
    return market_arr.copy()


def alpha_for_bin(alpha: float, bin) -> float:
    """Return the effective alpha for one bin, including tail scaling."""
    is_tail = bool(getattr(bin, "is_shoulder", False))
    if not is_tail:
        is_tail = (
            (hasattr(bin, 'low') and bin.low is None)
            or (hasattr(bin, 'high') and bin.high is None)
        )
    if not is_tail and hasattr(bin, 'label'):
        label = bin.label.lower()
        is_tail = 'or below' in label or 'or higher' in label or 'or above' in label
    if is_tail:
        return max(0.20, float(alpha) * DEFAULT_TAIL_TREATMENT.scale_factor)
    return float(alpha)
