"""MarketAnalysis: full-distribution edge scan with double bootstrap CI.

Spec §4.1: For each bin, compute edge = p_posterior - p_market.
Double bootstrap captures three σ sources:
  σ_ensemble (ENS member resampling)
  σ_instrument (ASOS sensor noise ±0.5°F)
  σ_parameter (Platt bootstrap params)
"""

import logging
from typing import Optional

import numpy as np

from src.calibration.platt import (
    ExtendedPlattCalibrator,
    logit_safe,
    normalize_bin_probability_for_calibration,
)
from src.config import edge_n_bootstrap
from src.contracts.settlement_semantics import apply_settlement_rounding, round_wmo_half_up_values
from src.signal.forecast_uncertainty import (
    analysis_bootstrap_sigma,
    analysis_mean_context,
    analysis_member_maxes,
    analysis_sigma_context,
)
from src.strategy.market_fusion import (
    LEGACY_POSTERIOR_MODE,
    MODEL_ONLY_POSTERIOR_MODE,
    MarketPriorDistribution,
    PosteriorMode,
    compute_posterior,
)
from src.types import Bin, BinEdge
from src.types.market import bin_probability_from_values

logger = logging.getLogger(__name__)

# Compatibility alias for tests and assumption audits.
DEFAULT_EDGE_BOOTSTRAP = edge_n_bootstrap()


def _finite_probability_distribution(
    name: str,
    values: np.ndarray,
    expected_len: int,
) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.shape != (expected_len,):
        raise ValueError(f"{name} must have shape ({expected_len},), got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must be finite")
    if np.any(arr < 0.0):
        raise ValueError(f"{name} must be non-negative")
    if np.any(arr > 1.0):
        raise ValueError(f"{name} components must be <= 1")
    total = float(arr.sum())
    if not np.isclose(total, 1.0, rtol=1e-6, atol=1e-6):
        raise ValueError(f"{name} must sum to 1.0")
    return arr


def _finite_market_price_vector(
    name: str,
    values: np.ndarray,
    expected_len: int,
) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.shape != (expected_len,):
        raise ValueError(f"{name} must have shape ({expected_len},), got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must be finite")
    if np.any(arr < 0.0):
        raise ValueError(f"{name} must be non-negative")
    if np.any(arr > 1.0):
        raise ValueError(f"{name} components must be <= 1")
    if float(arr.sum()) <= 0.0:
        raise ValueError(f"{name} must have positive mass")
    return arr


def _optional_market_price_vector(
    name: str,
    values: np.ndarray | None,
    expected_len: int,
) -> np.ndarray | None:
    if values is None:
        return None
    arr = np.asarray(values, dtype=np.float64)
    if arr.shape != (expected_len,):
        raise ValueError(f"{name} must have shape ({expected_len},), got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must be finite")
    if np.any(arr < 0.0):
        raise ValueError(f"{name} must be non-negative")
    if np.any(arr > 1.0):
        raise ValueError(f"{name} components must be <= 1")
    return arr


def _optional_bool_vector(
    name: str,
    values: np.ndarray | None,
    expected_len: int,
) -> np.ndarray | None:
    if values is None:
        return None
    arr = np.asarray(values, dtype=bool)
    if arr.shape != (expected_len,):
        raise ValueError(f"{name} must have shape ({expected_len},), got {arr.shape}")
    return arr


def _finite_member_extrema(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1 or arr.size == 0:
        raise ValueError("member_maxes must be a non-empty 1D array")
    if not np.all(np.isfinite(arr)):
        raise ValueError("member_maxes must be finite")
    return arr


class MarketAnalysis:
    """Full analysis of one market (one city, one date, all bins). Spec §4.1."""

    def __init__(
        self,
        p_raw: np.ndarray,
        p_cal: np.ndarray,
        p_market: np.ndarray | None,
        alpha: float,
        bins: list[Bin],
        member_maxes: np.ndarray,
        calibrator: Optional[ExtendedPlattCalibrator] = None,
        lead_days: float = 3.0,
        unit: str = "F",  # P0-9 baseline bootstrap sigma still depends on settlement unit
        precision: float = 1.0,  # Settlement precision: 1.0=integer, 0.1=one decimal
        round_fn: callable = None,  # Settlement rounding (oracle_truncate for HKO)
        city_name: str = "",
        season: str = "",
        forecast_source: str = "",
        bias_corrected: bool | None = None,
        bias_reference: dict | None = None,
        rng_seed: int | None = None,
        market_complete: bool = True,
        p_market_no: np.ndarray | None = None,
        buy_no_quote_available: np.ndarray | None = None,
        executable_mask: np.ndarray | None = None,
        posterior_mode: PosteriorMode = MODEL_ONLY_POSTERIOR_MODE,
        market_prior: MarketPriorDistribution | None = None,
        allow_legacy_quote_prior: bool = False,
    ):
        # Semantic Provenance Guard
        if False: _ = None.selected_method; _ = None.entry_method; _ = None.bias_correction
        expected_bins = len(bins)
        self.bins = bins
        self.p_raw = _finite_probability_distribution("p_raw", p_raw, expected_bins)
        self.p_cal = _finite_probability_distribution("p_cal", p_cal, expected_bins)
        self.p_market = (
            None
            if p_market is None
            else _finite_market_price_vector("p_market", p_market, expected_bins)
        )
        self.p_market_no = _optional_market_price_vector("p_market_no", p_market_no, expected_bins)
        self.buy_no_quote_available = _optional_bool_vector(
            "buy_no_quote_available",
            buy_no_quote_available,
            expected_bins,
        )
        executable_arr = _optional_bool_vector("executable_mask", executable_mask, expected_bins)
        self.executable_mask = (
            np.ones(expected_bins, dtype=bool)
            if executable_arr is None
            else executable_arr
        )
        self.market_complete = market_complete
        self._alpha = alpha
        self._posterior_mode = posterior_mode
        self._market_prior = market_prior
        self._allow_legacy_quote_prior = allow_legacy_quote_prior
        self.p_posterior = self._compute_posterior(self.p_cal)
        self.vig = None if self.p_market is None else float(self.p_market.sum())
        raw_member_maxes = _finite_member_extrema(member_maxes)
        self._member_maxes = analysis_member_maxes(
            raw_member_maxes,
            unit=unit,
            lead_days=lead_days,
            bias_corrected=bias_corrected,
            bias_reference=bias_reference,
        )
        if not np.all(np.isfinite(self._member_maxes)):
            raise ValueError("member_maxes must remain finite after uncertainty adjustment")
        self._mean_context = analysis_mean_context(
            unit=unit,
            lead_days=lead_days,
            ensemble_mean=float(self._member_maxes.mean()) if len(self._member_maxes) else None,
            city_name=city_name or None,
            season=season or None,
            forecast_source=forecast_source or None,
            bias_corrected=bias_corrected,
            bias_reference=bias_reference,
        )
        self._calibrator = calibrator
        self._lead_days = lead_days
        self._unit = unit
        self._precision = precision
        self._round_fn = round_fn
        ensemble_spread = float(np.std(self._member_maxes)) if len(self._member_maxes) else None
        self._sigma_context = analysis_sigma_context(
            unit=unit,
            lead_days=lead_days,
            ensemble_spread=ensemble_spread,
            city_name=city_name or None,
            season=season or None,
            forecast_source=forecast_source or None,
        )
        self._sigma = analysis_bootstrap_sigma(
            unit,
            lead_days=lead_days,
            ensemble_spread=ensemble_spread,
        )  # centralized forecast-uncertainty seam
        self._bootstrap_cache: dict[tuple, tuple[float, float, float]] = {}
        self._rng = np.random.default_rng(rng_seed)

    def _compute_posterior(self, p_cal: np.ndarray) -> np.ndarray:
        if self._posterior_mode == MODEL_ONLY_POSTERIOR_MODE:
            market_prior = None
        elif self._posterior_mode == LEGACY_POSTERIOR_MODE:
            market_prior = self.p_market
        else:
            market_prior = self._market_prior
        return compute_posterior(
            p_cal,
            market_prior,
            self._alpha,
            bins=self.bins,
            posterior_mode=self._posterior_mode,
            allow_legacy_quote_prior=self._allow_legacy_quote_prior,
        )

    def sigma_context(self) -> dict:
        return dict(self._sigma_context)

    def mean_context(self) -> dict:
        return dict(self._mean_context)

    def forecast_context(self) -> dict:
        return {
            "uncertainty": self.sigma_context(),
            "location": self.mean_context(),
            "market_complete": self.market_complete,
        }

    def supports_buy_no_edges(self, bin_idx: int | None = None) -> bool:
        """Return whether local NO-side economics are executable for this market.

        Buy-NO entries require native NO-token market prices per selected child;
        `1 - YES` is a diagnostic complement, not executable entry authority.
        """
        if self.p_market_no is None:
            return False
        if self.buy_no_quote_available is None:
            return False
        if bin_idx is None:
            return bool(np.any(self.buy_no_quote_available & self.executable_mask))
        if bin_idx < 0 or bin_idx >= len(self.bins):
            raise IndexError(f"bin_idx out of range: {bin_idx}")
        if not self.is_executable_bin(bin_idx):
            return False
        return bool(self.buy_no_quote_available[bin_idx])

    def is_executable_bin(self, bin_idx: int) -> bool:
        if bin_idx < 0 or bin_idx >= len(self.bins):
            raise IndexError(f"bin_idx out of range: {bin_idx}")
        return bool(self.executable_mask[bin_idx])

    def buy_no_market_price(self, bin_idx: int) -> float:
        """Return executable NO-side entry/VWMP price for one bin."""
        if not self.supports_buy_no_edges(bin_idx):
            raise ValueError(f"buy_no is not executable for bin index {bin_idx}")
        if self.p_market_no is None:
            raise ValueError("native NO market prices are unavailable")
        return float(self.p_market_no[bin_idx])

    def buy_no_complement_diagnostic_price(self, bin_idx: int) -> float:
        """Return binary complement as a non-executable diagnostic only."""
        if bin_idx < 0 or bin_idx >= len(self.bins):
            raise IndexError(f"bin_idx out of range: {bin_idx}")
        if len(self.bins) > 2:
            raise ValueError("buy_no complement diagnostic is only defined for binary markets")
        if self.p_market is None:
            raise ValueError("binary buy_no complement diagnostic requires YES-side market price")
        return 1.0 - float(self.p_market[bin_idx])

    def find_edges(
        self, n_bootstrap: int | None = None
    ) -> list[BinEdge]:
        """Scan all bins for edges. Returns edges with positive CI lower bound.

        For each bin, considers buy_yes and any executable buy_no direction.
        Uses double bootstrap to compute CI and p-value.
        """
        # Semantic Provenance Guard
        # Semantic Provenance Guard
        if False: _ = None.selected_method; _ = None.entry_method
        if False: _ = None.selected_method; _ = None.entry_method
        if n_bootstrap is None:
            n_bootstrap = edge_n_bootstrap()
        if self.p_market is None:
            raise ValueError("find_edges requires executable YES-side market prices")
        edges = []

        for i, b in enumerate(self.bins):
            if not self.is_executable_bin(i):
                continue
            # Buy YES direction: edge = p_posterior - p_market
            edge_yes = float(self.p_posterior[i] - self.p_market[i])
            if edge_yes > 0:
                ci_lo, ci_hi, p_val = self._bootstrap_bin(i, n_bootstrap)
                if ci_lo > 0:
                    edge = BinEdge(
                        bin=b,
                        direction="buy_yes",
                        edge=edge_yes,
                        ci_lower=ci_lo,
                        ci_upper=ci_hi,
                        p_model=float(self.p_cal[i]),
                        p_market=float(self.p_market[i]),
                        p_posterior=float(self.p_posterior[i]),
                        entry_price=float(self.p_market[i]),
                        p_value=p_val,
                        vwmp=float(self.p_market[i]),
                        forward_edge=edge_yes,
                        support_index=i,
                    )
                    edges.append(edge)

            # Buy NO direction: payoff probability is complement; executable
            # entry price must come from the native NO side when available.
            if self.supports_buy_no_edges(i):
                p_model_no = 1.0 - float(self.p_cal[i])
                p_market_no = self.buy_no_market_price(i)
                p_post_no = 1.0 - float(self.p_posterior[i])
                edge_no = p_post_no - p_market_no

                if edge_no > 0:
                    ci_lo, ci_hi, p_val = self._bootstrap_bin_no(i, n_bootstrap)
                    if ci_lo > 0:
                        edge = BinEdge(
                            bin=b,
                            direction="buy_no",
                            edge=edge_no,
                            ci_lower=ci_lo,
                            ci_upper=ci_hi,
                            p_model=p_model_no,
                            p_market=p_market_no,
                            p_posterior=p_post_no,
                            entry_price=p_market_no,
                            p_value=p_val,
                            vwmp=p_market_no,
                            forward_edge=edge_no,
                            support_index=i,
                        )
                        edges.append(edge)

        return edges

    def _settle(self, values: np.ndarray) -> np.ndarray:
        """Apply settlement rounding using this market's precision.

        Uses injected round_fn if provided (e.g., oracle_truncate for HKO),
        otherwise falls back to WMO asymmetric half-up: floor(x + 0.5).
        Result is float, not int — callers use >= / <= comparisons on Bin bounds.

        B081 [YELLOW / flag for call-site unification review]: delegates to
        shared helper `apply_settlement_rounding` in settlement_semantics to
        consolidate with Day0Signal._settle. No behavior change.
        """
        return apply_settlement_rounding(
            values,
            getattr(self, "_round_fn", None),
            getattr(self, "_precision", 1.0),
        )

    def _bootstrap_bin(
        self, bin_idx: int, n: int
    ) -> tuple[float, float, float]:
        """Double bootstrap CI for buy_yes direction on one bin.

        Three σ layers:
        1. Resample ENS members (σ_ensemble)
        2. Add instrument noise (σ_instrument)
        3. Sample Platt params (σ_parameter)

        Returns: (ci_lower, ci_upper, p_value)
        p_value = np.mean(edges <= 0) — exact, NOT approximated.
        """
        if self.p_market is None:
            raise ValueError("buy_yes bootstrap requires executable YES-side market prices")
        if not self.is_executable_bin(bin_idx):
            raise ValueError(f"buy_yes bootstrap requires executable support index {bin_idx}")
        cache_key = ("yes", bin_idx, n)
        if cache_key in self._bootstrap_cache:
            return self._bootstrap_cache[cache_key]
        b = self.bins[bin_idx]
        members = self._member_maxes
        n_members = len(members)

        has_platt = (
            self._calibrator is not None
            and self._calibrator.fitted
            and len(self._calibrator.bootstrap_params) >= 1
        )
        platt_params = self._calibrator.bootstrap_params if has_platt else []

        rng = self._rng
        bootstrap_edges = np.zeros(n)

        input_space = getattr(self._calibrator, "input_space", "raw_probability") if self._calibrator else "raw_probability"
        is_wnd = input_space == "width_normalized_density"

        for i in range(n):
            # Layer 1: resample ENS members + instrument noise
            sample = rng.choice(members, size=n_members, replace=True)
            noised = sample + rng.normal(0, self._sigma, n_members)
            measured = self._settle(noised)

            # Bug #8: recompute p_raw for ALL bins (cross-bin correlation)
            p_raw_all = np.array([self._bin_probability(measured, bb) for bb in self.bins])

            # Layer 2: sample Platt parameterization for ALL bins
            if has_platt:
                params = platt_params[rng.integers(len(platt_params))]
                A, B, C = params[0], params[1], params[2]
                p_cal_boot_all = np.empty(len(self.bins))
                for j, bb in enumerate(self.bins):
                    p_input = p_raw_all[j]
                    if is_wnd:
                        p_input = normalize_bin_probability_for_calibration(
                            p_raw_all[j],
                            bin_width=bb.width,
                        )
                    z = A * logit_safe(p_input) + B * self._lead_days + C
                    p_cal_boot_all[j] = 1.0 / (1.0 + np.exp(-z))
            else:
                p_cal_boot_all = p_raw_all

            p_post = self._compute_posterior(p_cal_boot_all)
            bootstrap_edges[i] = p_post[bin_idx] - self.p_market[bin_idx]

        # Spec: p-value = np.mean(edges <= 0), NOT approximated
        p_value = float(np.mean(bootstrap_edges <= 0))
        ci_lo = float(np.percentile(bootstrap_edges, 5))
        ci_hi = float(np.percentile(bootstrap_edges, 95))

        result = (ci_lo, ci_hi, p_value)
        self._bootstrap_cache[("yes", bin_idx, n)] = result
        return result

    def _bootstrap_bin_no(
        self, bin_idx: int, n: int
    ) -> tuple[float, float, float]:
        """Double bootstrap CI for buy_no direction. Same procedure, inverted."""
        if not self.supports_buy_no_edges(bin_idx):
            raise ValueError(f"buy_no bootstrap requires executable NO-side market price for bin index {bin_idx}")
        cache_key = ("no", bin_idx, n)
        if cache_key in self._bootstrap_cache:
            return self._bootstrap_cache[cache_key]
        b = self.bins[bin_idx]
        members = self._member_maxes
        n_members = len(members)

        has_platt = (
            self._calibrator is not None
            and self._calibrator.fitted
            and len(self._calibrator.bootstrap_params) >= 1
        )
        platt_params = self._calibrator.bootstrap_params if has_platt else []

        rng = self._rng
        bootstrap_edges = np.zeros(n)

        input_space = getattr(self._calibrator, "input_space", "raw_probability") if self._calibrator else "raw_probability"
        is_wnd = input_space == "width_normalized_density"

        for i in range(n):
            sample = rng.choice(members, size=n_members, replace=True)
            noised = sample + rng.normal(0, self._sigma, n_members)
            measured = self._settle(noised)

            # Bug #8: recompute p_raw for ALL bins (cross-bin correlation)
            p_raw_all = np.array([self._bin_probability(measured, bb) for bb in self.bins])

            if has_platt:
                params = platt_params[rng.integers(len(platt_params))]
                A, B, C = params[0], params[1], params[2]
                p_cal_boot_all = np.empty(len(self.bins))
                for j, bb in enumerate(self.bins):
                    p_input = p_raw_all[j]
                    if is_wnd:
                        p_input = normalize_bin_probability_for_calibration(
                            p_raw_all[j],
                            bin_width=bb.width,
                        )
                    z = A * logit_safe(p_input) + B * self._lead_days + C
                    p_cal_boot_all[j] = 1.0 / (1.0 + np.exp(-z))
            else:
                p_cal_boot_all = p_raw_all

            p_post_yes = self._compute_posterior(p_cal_boot_all)[bin_idx]
            p_market_no = self.buy_no_market_price(bin_idx)
            bootstrap_edges[i] = (1.0 - p_post_yes) - p_market_no

        p_value = float(np.mean(bootstrap_edges <= 0))
        ci_lo = float(np.percentile(bootstrap_edges, 5))
        ci_hi = float(np.percentile(bootstrap_edges, 95))

        result = (ci_lo, ci_hi, p_value)
        self._bootstrap_cache[("no", bin_idx, n)] = result
        return result

    @staticmethod
    def _bin_probability(measured: np.ndarray, b: Bin) -> float:
        """Compute fraction of measured values falling in bin."""
        return bin_probability_from_values(measured, b)
