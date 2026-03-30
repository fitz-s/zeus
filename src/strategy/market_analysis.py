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
from src.signal.ensemble_signal import SIGMA_INSTRUMENT
from src.strategy.market_fusion import compute_posterior
from src.types import Bin, BinEdge

logger = logging.getLogger(__name__)

# Default bootstrap iterations for edge CI
DEFAULT_EDGE_BOOTSTRAP = 500


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
    ):
        self.bins = bins
        self.p_raw = p_raw
        self.p_cal = p_cal
        self.p_market = p_market
        self.p_posterior = compute_posterior(p_cal, p_market, alpha)
        self.vig = float(p_market.sum())
        self._member_maxes = member_maxes
        self._calibrator = calibrator
        self._alpha = alpha
        self._lead_days = lead_days

    def find_edges(
        self, n_bootstrap: int = DEFAULT_EDGE_BOOTSTRAP
    ) -> list[BinEdge]:
        """Scan all bins for edges. Returns edges with positive CI lower bound.

        For each bin, considers both directions (buy_yes, buy_no).
        Uses double bootstrap to compute CI and p-value.
        """
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
                    ))

        return edges

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
            noised = sample + rng.normal(0, SIGMA_INSTRUMENT, n_members)
            measured = np.round(noised).astype(int)

            # Compute p_raw for this bin from resampled members
            p_raw_boot = self._bin_probability(measured, b)

            # Layer 2: sample Platt parameterization
            if platt_params:
                params = platt_params[rng.integers(len(platt_params))]
                A, B, C = params[0], params[1], params[2]
                p_clamped = np.clip(p_raw_boot, P_CLAMP_LOW, P_CLAMP_HIGH)
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

        return ci_lo, ci_hi, p_value

    def _bootstrap_bin_no(
        self, bin_idx: int, n: int
    ) -> tuple[float, float, float]:
        """Double bootstrap CI for buy_no direction. Same procedure, inverted."""
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
            noised = sample + rng.normal(0, SIGMA_INSTRUMENT, n_members)
            measured = np.round(noised).astype(int)

            p_raw_boot = self._bin_probability(measured, b)

            if platt_params:
                params = platt_params[rng.integers(len(platt_params))]
                A, B, C = params[0], params[1], params[2]
                p_clamped = np.clip(p_raw_boot, P_CLAMP_LOW, P_CLAMP_HIGH)
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

        return ci_lo, ci_hi, p_value

    @staticmethod
    def _bin_probability(measured: np.ndarray, b: Bin) -> float:
        """Compute fraction of measured values falling in bin."""
        if b.is_open_low:
            return float(np.mean(measured <= b.high))
        elif b.is_open_high:
            return float(np.mean(measured >= b.low))
        else:
            return float(np.mean((measured >= b.low) & (measured <= b.high)))
