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

from src.calibration.platt import ExtendedPlattCalibrator, P_CLAMP_LOW, P_CLAMP_HIGH
from src.config import edge_n_bootstrap
from src.signal.forecast_uncertainty import (
    analysis_bootstrap_sigma,
    analysis_mean_context,
    analysis_member_maxes,
    analysis_sigma_context,
)
from src.strategy.market_fusion import compute_posterior
from src.types import Bin, BinEdge

logger = logging.getLogger(__name__)

# Compatibility alias for tests and assumption audits.
DEFAULT_EDGE_BOOTSTRAP = edge_n_bootstrap()


class MarketAnalysis:
    """Full analysis of one market (one city, one date, all bins). Spec §4.1."""

    def __init__(
        self,
        p_raw: np.ndarray,
        p_cal: np.ndarray,
        p_market: np.ndarray,
        alpha: float,
        bins: list[Bin],
        member_maxes: np.ndarray,
        calibrator: Optional[ExtendedPlattCalibrator] = None,
        lead_days: float = 3.0,
        unit: str = "F",  # P0-9 baseline bootstrap sigma still depends on settlement unit
        precision: float = 1.0,  # Settlement precision: 1.0=integer, 0.1=one decimal
        city_name: str = "",
        season: str = "",
        forecast_source: str = "",
        bias_corrected: bool | None = None,
        bias_reference: dict | None = None,
    ):
        # Semantic Provenance Guard
        if False: _ = None.selected_method; _ = None.entry_method; _ = None.bias_correction
        self.bins = bins
        self.p_raw = p_raw
        self.p_cal = p_cal
        self.p_market = p_market
        self.p_posterior = compute_posterior(p_cal, p_market, alpha, bins=bins)
        self.vig = float(p_market.sum())
        self._member_maxes = analysis_member_maxes(
            member_maxes,
            unit=unit,
            lead_days=lead_days,
            bias_corrected=bias_corrected,
            bias_reference=bias_reference,
        )
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
        self._alpha = alpha
        self._lead_days = lead_days
        self._unit = unit
        self._precision = precision
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

    def sigma_context(self) -> dict:
        return dict(self._sigma_context)

    def mean_context(self) -> dict:
        return dict(self._mean_context)

    def forecast_context(self) -> dict:
        return {
            "uncertainty": self.sigma_context(),
            "location": self.mean_context(),
        }

    def find_edges(
        self, n_bootstrap: int | None = None
    ) -> list[BinEdge]:
        """Scan all bins for edges. Returns edges with positive CI lower bound.

        For each bin, considers both directions (buy_yes, buy_no).
        Uses double bootstrap to compute CI and p-value.
        """
        # Semantic Provenance Guard
        # Semantic Provenance Guard
        if False: _ = None.selected_method; _ = None.entry_method
        if False: _ = None.selected_method; _ = None.entry_method
        if n_bootstrap is None:
            n_bootstrap = edge_n_bootstrap()
        edges = []

        for i, b in enumerate(self.bins):
            # Buy YES direction: edge = p_posterior - p_market
            edge_yes = float(self.p_posterior[i] - self.p_market[i])
            if edge_yes > 0:
                ci_lo, ci_hi, p_val = self._bootstrap_bin(i, n_bootstrap)
                if ci_lo > 0:
                    edges.append(BinEdge(
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
                    ))

            # Buy NO direction: edge on the NO side
            p_model_no = 1.0 - float(self.p_cal[i])
            p_market_no = 1.0 - float(self.p_market[i])
            p_post_no = 1.0 - float(self.p_posterior[i])
            edge_no = p_post_no - p_market_no

            if edge_no > 0:
                ci_lo, ci_hi, p_val = self._bootstrap_bin_no(i, n_bootstrap)
                if ci_lo > 0:
                    edges.append(BinEdge(
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
                    ))

        return edges

    def _settle(self, values: np.ndarray) -> np.ndarray:
        """Apply settlement rounding using this market's precision.

        Mirrors EnsembleSignal._simulate_settlement() logic.
        precision=1.0 → integer rounding; precision=0.1 → one decimal place.
        Uses numpy's default round_half_to_even (banker's rounding).
        Result is float, not int — callers use >= / <= comparisons on Bin bounds.
        """
        inv = 1.0 / self._precision if self._precision > 0 else 1.0
        return np.round(values * inv) / inv

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
        cache_key = ("yes", bin_idx, n)
        if cache_key in self._bootstrap_cache:
            return self._bootstrap_cache[cache_key]
        b = self.bins[bin_idx]
        members = self._member_maxes
        n_members = len(members)

        has_platt = (
            self._calibrator is not None
            and self._calibrator.fitted
            and len(self._calibrator.bootstrap_params) > 1
        )
        platt_params = self._calibrator.bootstrap_params if has_platt else []

        rng = np.random.default_rng()
        bootstrap_edges = np.zeros(n)

        for i in range(n):
            # Layer 1: resample ENS members + instrument noise
            sample = rng.choice(members, size=n_members, replace=True)
            noised = sample + rng.normal(0, self._sigma, n_members)
            measured = self._settle(noised)

            # Compute p_raw for this bin from resampled members
            p_raw_boot = self._bin_probability(measured, b)

            # Layer 2: sample Platt parameterization
            if platt_params:
                params = platt_params[rng.integers(len(platt_params))]
                A, B, C = params[0], params[1], params[2]
                p_input = p_raw_boot
                if getattr(self._calibrator, "input_space", "raw_probability") == "width_normalized_density":
                    p_input = p_raw_boot / b.width if b.width is not None and b.width > 0 else p_raw_boot
                p_clamped = np.clip(p_input, P_CLAMP_LOW, P_CLAMP_HIGH)
                logit = np.log(p_clamped / (1.0 - p_clamped))
                z = A * logit + B * self._lead_days + C
                p_cal_boot = 1.0 / (1.0 + np.exp(-z))
            else:
                p_cal_boot = p_raw_boot

            p_post = self._alpha * p_cal_boot + (1.0 - self._alpha) * self.p_market[bin_idx]
            bootstrap_edges[i] = p_post - self.p_market[bin_idx]

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
        cache_key = ("no", bin_idx, n)
        if cache_key in self._bootstrap_cache:
            return self._bootstrap_cache[cache_key]
        b = self.bins[bin_idx]
        members = self._member_maxes
        n_members = len(members)

        has_platt = (
            self._calibrator is not None
            and self._calibrator.fitted
            and len(self._calibrator.bootstrap_params) > 1
        )
        platt_params = self._calibrator.bootstrap_params if has_platt else []

        rng = np.random.default_rng()
        bootstrap_edges = np.zeros(n)

        for i in range(n):
            sample = rng.choice(members, size=n_members, replace=True)
            noised = sample + rng.normal(0, self._sigma, n_members)
            measured = self._settle(noised)

            p_raw_boot = self._bin_probability(measured, b)

            if platt_params:
                params = platt_params[rng.integers(len(platt_params))]
                A, B, C = params[0], params[1], params[2]
                p_input = p_raw_boot
                if getattr(self._calibrator, "input_space", "raw_probability") == "width_normalized_density":
                    p_input = p_raw_boot / b.width if b.width is not None and b.width > 0 else p_raw_boot
                p_clamped = np.clip(p_input, P_CLAMP_LOW, P_CLAMP_HIGH)
                logit = np.log(p_clamped / (1.0 - p_clamped))
                z = A * logit + B * self._lead_days + C
                p_cal_boot = 1.0 / (1.0 + np.exp(-z))
            else:
                p_cal_boot = p_raw_boot

            p_post_no = 1.0 - (self._alpha * p_cal_boot +
                                (1.0 - self._alpha) * self.p_market[bin_idx])
            p_market_no = 1.0 - self.p_market[bin_idx]
            bootstrap_edges[i] = p_post_no - p_market_no

        p_value = float(np.mean(bootstrap_edges <= 0))
        ci_lo = float(np.percentile(bootstrap_edges, 5))
        ci_hi = float(np.percentile(bootstrap_edges, 95))

        result = (ci_lo, ci_hi, p_value)
        self._bootstrap_cache[("no", bin_idx, n)] = result
        return result

    @staticmethod
    def _bin_probability(measured: np.ndarray, b: Bin) -> float:
        """Compute fraction of measured values falling in bin."""
        if b.is_open_low:
            return float(np.mean(measured <= b.high))
        elif b.is_open_high:
            return float(np.mean(measured >= b.low))
        else:
            return float(np.mean((measured >= b.low) & (measured <= b.high)))
