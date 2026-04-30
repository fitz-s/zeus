"""Market fusion: posterior belief construction and legacy VWMP blending.

Legacy Spec §4.5 blended p_cal with p_market. Corrected pricing semantics
separate executable quotes from posterior belief, so raw VWMP inputs are now
accepted only through the explicit legacy mode.
"""

from dataclasses import dataclass
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
YES_FAMILY_DEVIG_SHADOW_MODE = "yes_family_devig_v1_shadow"
PosteriorMode = Literal[
    "legacy_vwmp_prior_v0",
    "model_only_v1",
    "yes_family_devig_v1_shadow",
]
_CORRECTED_PRIOR_MODES = {
    YES_FAMILY_DEVIG_SHADOW_MODE,
}


@dataclass(frozen=True)
class MarketPriorDistribution:
    """Named epistemic market prior, not an executable quote.

    This transitional contract lives next to ``compute_posterior`` until the
    wider contracts packet is admitted. It deliberately requires a complete,
    normalized distribution plus lineage; raw token quotes/VWMP vectors are
    still allowed only through the explicitly named legacy mode.
    """

    probabilities: tuple[float, ...]
    bin_labels: tuple[str, ...]
    prior_id: str
    estimator_version: str
    source_quote_hashes: tuple[str, ...]
    family_complete: bool
    side_convention: Literal["YES_FAMILY"]
    vig_treatment: str
    freshness_status: Literal["FRESH", "UNKNOWN"]
    liquidity_filter_status: Literal["PASS", "UNKNOWN"]
    neg_risk_policy: str
    validated_for_live: bool
    source: str = "market_prior_distribution"
    validation_evidence_id: str | None = None

    def __post_init__(self) -> None:
        values = tuple(float(v) for v in self.probabilities)
        if not values:
            raise ValueError("MarketPriorDistribution.probabilities must be non-empty")
        labels = tuple(str(label).strip() for label in self.bin_labels)
        if len(labels) != len(values) or any(not label for label in labels):
            raise ValueError(
                "MarketPriorDistribution.bin_labels must be non-empty and match probabilities"
            )
        arr = np.asarray(values, dtype=float)
        if not np.all(np.isfinite(arr)):
            raise ValueError("MarketPriorDistribution.probabilities must be finite")
        if np.any(arr < 0.0):
            raise ValueError("MarketPriorDistribution.probabilities must be non-negative")
        if np.any(arr > 1.0):
            raise ValueError("MarketPriorDistribution.probabilities components must be <= 1")
        total = float(arr.sum())
        if not np.isclose(total, 1.0, rtol=1e-6, atol=1e-6):
            raise ValueError(
                "MarketPriorDistribution.probabilities must sum to 1.0; "
                f"got {total}"
            )
        if not str(self.prior_id).strip():
            raise ValueError("MarketPriorDistribution.prior_id must be non-empty")
        if not str(self.estimator_version).strip():
            raise ValueError("MarketPriorDistribution.estimator_version must be non-empty")
        quote_hashes = tuple(str(value).strip() for value in self.source_quote_hashes)
        if not quote_hashes or any(not value for value in quote_hashes):
            raise ValueError("MarketPriorDistribution.source_quote_hashes must be non-empty")
        if not self.family_complete:
            raise ValueError("MarketPriorDistribution requires complete YES-family prior")
        if self.side_convention != "YES_FAMILY":
            raise ValueError("MarketPriorDistribution side_convention must be YES_FAMILY")
        if not str(self.vig_treatment).strip():
            raise ValueError("MarketPriorDistribution.vig_treatment must be non-empty")
        if self.freshness_status not in {"FRESH", "UNKNOWN"}:
            raise ValueError("MarketPriorDistribution.freshness_status must be FRESH or UNKNOWN")
        if self.liquidity_filter_status not in {"PASS", "UNKNOWN"}:
            raise ValueError("MarketPriorDistribution.liquidity_filter_status must be PASS or UNKNOWN")
        if not str(self.neg_risk_policy).strip():
            raise ValueError("MarketPriorDistribution.neg_risk_policy must be non-empty")
        object.__setattr__(self, "probabilities", values)
        object.__setattr__(self, "bin_labels", labels)
        object.__setattr__(self, "prior_id", str(self.prior_id).strip())
        object.__setattr__(self, "estimator_version", str(self.estimator_version).strip())
        object.__setattr__(self, "source_quote_hashes", quote_hashes)
        object.__setattr__(self, "vig_treatment", str(self.vig_treatment).strip())
        object.__setattr__(self, "neg_risk_policy", str(self.neg_risk_policy).strip())
        object.__setattr__(self, "source", str(self.source).strip() or "market_prior_distribution")
        evidence_id = None if self.validation_evidence_id is None else str(self.validation_evidence_id).strip()
        object.__setattr__(self, "validation_evidence_id", evidence_id or None)

    def as_array(self, expected_len: int | None = None, bins: list | None = None) -> np.ndarray:
        arr = np.asarray(self.probabilities, dtype=float)
        if expected_len is not None and arr.shape != (expected_len,):
            raise ValueError(
                "MarketPriorDistribution length mismatch: "
                f"expected {expected_len}, got {arr.shape[0]}"
            )
        if bins is not None:
            labels = tuple(str(getattr(bin, "label", "")).strip() for bin in bins)
            if labels != self.bin_labels:
                raise ValueError(
                    "MarketPriorDistribution bin label mismatch: "
                    f"expected {labels}, got {self.bin_labels}"
                )
        return arr


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

    # K1/#5: deprecated alpha override removed — alpha_overrides table had 0 rows
    # and per-decision adjustments (below) are the correct alpha mechanism.
    base = BASE_ALPHA_BY_LEVEL[calibration_level]
    a = base

    # Ensemble spread adjustments — typed thresholds prevent °C/°F confusion
    # D4 analysis (2026-03-31): spread IS predictive of per-decision accuracy
    # (r=+0.214, tight Brier 0.114 vs wide 0.269). Sweep showed bonus=0.10
    # gives -0.00825 Brier improvement vs -0.00460 at the old bonus=0.05.
    tight = SPREAD_TIGHT.to(ensemble_spread.unit)
    wide = SPREAD_WIDE.to(ensemble_spread.unit)
    if ensemble_spread < tight:
        a += 0.10  # was 0.05, increased per D4
    if ensemble_spread > wide:
        a -= 0.15  # was 0.10, increased per D4

    # Model agreement adjustments
    if model_agreement == "SOFT_DISAGREE":
        a -= 0.10
    if model_agreement == "CONFLICT":
        a -= 0.20

    # Lead time adjustments
    if lead_days <= 1:
        a += 0.05
    if lead_days >= 5:
        a -= 0.05

    # Market freshness: recently-opened markets have unreliable prices
    if hours_since_open < 12:
        a += 0.10
    if hours_since_open < 6:
        a += 0.05  # Cumulative with above

    return AlphaDecision(
        value=max(0.20, min(0.85, a)),
        optimization_target="risk_cap",
        evidence_basis="D1 resolution: conservative blending weight, not pure Brier minimizer",
        ci_bound=0.05,
    )


def compute_posterior(
    p_cal: np.ndarray,
    p_market: np.ndarray | MarketPriorDistribution | None = None,
    alpha: float = 1.0,
    bins: list = None,
    *,
    posterior_mode: PosteriorMode = MODEL_ONLY_POSTERIOR_MODE,
    allow_legacy_quote_prior: bool = False,
) -> np.ndarray:
    """Compute alpha-weighted posterior, normalized to sum=1.0. Spec §4.5.

    Corrected modes consume only calibrated belief plus either no prior
    (``model_only_v1``) or a named ``MarketPriorDistribution``. Raw executable
    quote/VWMP vectors remain supported only in ``legacy_vwmp_prior_v0`` so old
    call sites are explicit about the semantic debt they carry.

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

    if posterior_mode in _CORRECTED_PRIOR_MODES:
        if not isinstance(p_market, MarketPriorDistribution):
            raise TypeError(
                f"{posterior_mode} requires MarketPriorDistribution; "
                "raw quote/VWMP vectors are forbidden"
            )
        if p_market.estimator_version != posterior_mode:
            raise ValueError(
                "MarketPriorDistribution.estimator_version must match posterior_mode: "
                f"{p_market.estimator_version!r} != {posterior_mode!r}"
            )
        market = p_market.as_array(expected_len=len(p_cal_arr), bins=bins)
    elif posterior_mode == LEGACY_POSTERIOR_MODE:
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
    p_market: np.ndarray | MarketPriorDistribution | None,
    p_cal: np.ndarray,
) -> np.ndarray:
    if isinstance(p_market, MarketPriorDistribution):
        raise TypeError("legacy_vwmp_prior_v0 requires a raw market quote vector")
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
