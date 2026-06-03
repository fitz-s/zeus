"""MarketAnalysis: full-distribution edge scan with double bootstrap CI.

Spec §4.1: For each bin, compute edge = p_posterior - p_market.
Double bootstrap captures four σ sources:
  σ_ensemble (ENS member resampling)
  σ_instrument (ASOS sensor noise ±0.5°F)
  σ_parameter (Platt bootstrap params)
  σ_transfer (cross-domain Platt transfer uncertainty, additive in logit-space)
"""

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from src.calibration.platt import (
    ExtendedPlattCalibrator,
    P_CLAMP_HIGH,
    P_CLAMP_LOW,
    logit_safe,
    normalize_bin_probability_for_calibration,
)
from src.config import edge_n_bootstrap
from src.contracts.execution_price import ExecutionPrice
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


@dataclass(frozen=True)
class EdgeScanTrace:
    support_index: int
    bin_label: str
    executable: bool
    direction: str
    p_posterior: float | None
    p_market: float | None
    raw_edge: float | None
    ci_lower: float | None
    ci_upper: float | None
    p_value: float | None
    decision: str
    native_quote_available: bool | None = None


BootstrapProbabilitySampler = Callable[["MarketAnalysis", int], np.ndarray]


def compute_transfer_logit_sigma(brier_diff: float, scale: float = 4.0) -> float:
    """Map OOS Brier-diff to logit-space σ for cross-domain Platt transfer.

    brier_diff: float — excess Brier MSE attributable to source→target domain shift.
                negative or NaN values clamped to 0 (no inflation).
    scale: float — operator-tunable; default 4.0 ≈ chain-rule logit slope at p=0.5.
                   Configurable via config/settings.json::transfer_logit_sigma_scale.

    Returns: σ in logit-space, additive in bootstrap_bin's z computation.
    """
    if brier_diff is None or not (brier_diff == brier_diff):  # NaN check
        return 0.0
    return (max(0.0, float(brier_diff))) ** 0.5 * float(scale)


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
        *,
        transfer_logit_sigma: float = 0.0,
        bootstrap_probability_sampler: BootstrapProbabilitySampler | None = None,
        bootstrap_signal_type: str = "generic_ensemble",
        entry_quote_evidence_yes: list | None = None,
        entry_quote_evidence_no: list | None = None,
        representativeness_sigma: float = 0.0,
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
        # Preserve the RAW (pre-bias-correction) ensemble so the mainstream gate
        # (#135-B) can tell whether agreement with mainstream is independent or
        # only an artifact of a large bias correction. Read-only; adds no
        # correction — _member_maxes (below) remains the corrected array used by q.
        self._raw_member_maxes = raw_member_maxes
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
        # REPRESENTATIVENESS VARIANCE (2026-06-03, pre-arm blocker iron rule 6).
        # A mean-only EDLI bias correction shifts the member array but does NOT widen
        # spread, so the bootstrap CI is over-confident on corrected cities. σ_repr is
        # the per-city forecast-vs-settlement residual std (model_bias_ens.residual_sd_c,
        # converted to the members' NATIVE unit by the adapter) — the irreducible
        # representativeness uncertainty the ensemble spread does not capture. It is folded
        # into the MC resampling noise IN QUADRATURE with the instrument/bootstrap sigma so
        # q_lcb widens HONESTLY (only the LOWER bound; the POINT p_posterior is untouched).
        # σ_repr=0.0 (no correction applied) => hypot(σ, 0) == σ exactly => bit-identical
        # legacy behaviour. The adapter gates σ_repr>0 to fire ONLY when the bias correction
        # was applied (members_already_corrected / _edli_bias_corrected True).
        self._representativeness_sigma = float(representativeness_sigma)
        if self._representativeness_sigma < 0 or not np.isfinite(self._representativeness_sigma):
            raise ValueError("representativeness_sigma must be a finite, non-negative std dev")
        self._bootstrap_cache: dict[tuple, tuple[float, float, float]] = {}
        self._rng = np.random.default_rng(rng_seed)
        self._transfer_logit_sigma = float(transfer_logit_sigma)
        self._bootstrap_probability_sampler = bootstrap_probability_sampler
        self._bootstrap_signal_type = str(bootstrap_signal_type or "generic_ensemble")
        # Wave 5: dedicated independent RNG for σ_market cost-noise draws so
        # the forecast/Platt resampling stream (self._rng) is NOT disturbed
        # when EntryQuoteEvidence is provided. Without this split the same
        # rng_seed produces different forecast samples between legacy and
        # Wave-5 paths, defeating behaviour-preservation testing.
        # X3 fix (Copilot review of PR #348): use numpy.random.SeedSequence.spawn
        # — the canonical decorrelated-substream pattern — instead of a
        # close-spaced fixed-prime offset. spawn() guarantees the substream
        # is statistically independent of self._rng regardless of generator
        # family (PCG64, Philox, etc), where seed+constant only happens to
        # work for PCG64. self._rng is NOT re-seeded to preserve legacy
        # forecast-stream behaviour bit-identically (rng_seed callers depend
        # on the existing default_rng(rng_seed) stream).
        if rng_seed is None:
            self._cost_rng = np.random.default_rng()
        else:
            (cost_ss,) = np.random.SeedSequence(rng_seed).spawn(1)
            self._cost_rng = np.random.default_rng(cost_ss)
        # Wave 5 (2026-05-27, INV-40): per-bin EntryQuoteEvidence carries
        # cost_uncertainty (σ_market). When provided, _bootstrap_bin samples
        # c_b ~ N(eqe.all_in_entry_price, eqe.cost_uncertainty) instead of
        # subtracting the fixed p_market value — so edge_ci_lower reflects
        # market-cost uncertainty (R5 antibody) rather than only forecast
        # uncertainty. None preserves pre-Wave-5 behaviour bit-identically.
        self._entry_quote_evidence_yes: list | None = (
            None if entry_quote_evidence_yes is None
            else list(entry_quote_evidence_yes)
        )
        self._entry_quote_evidence_no: list | None = (
            None if entry_quote_evidence_no is None
            else list(entry_quote_evidence_no)
        )
        if self._entry_quote_evidence_yes is not None and len(self._entry_quote_evidence_yes) != expected_bins:
            raise ValueError(
                f"entry_quote_evidence_yes must have length {expected_bins}, "
                f"got {len(self._entry_quote_evidence_yes)}"
            )
        if self._entry_quote_evidence_no is not None and len(self._entry_quote_evidence_no) != expected_bins:
            raise ValueError(
                f"entry_quote_evidence_no must have length {expected_bins}, "
                f"got {len(self._entry_quote_evidence_no)}"
            )

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

    @property
    def member_maxes(self) -> "np.ndarray":
        """Public accessor for the (bias-corrected) ensemble member maxima array.

        Gate consumers (#135 mainstream-agreement) MUST use this accessor — the
        backing attribute is private (_member_maxes) so direct attribute access
        silently raises AttributeError inside the outer try/except, converting a
        hard bug into a fail-open gate (the antibody the gate is meant to be).
        """
        return self._member_maxes

    @property
    def raw_member_maxes(self) -> "np.ndarray":
        """Public accessor for the pre-mean-offset ensemble member maxima.

        Returns the _raw_member_maxes array as set at construction time — the
        per-member values BEFORE the analysis mean-offset is applied
        (analysis_member_maxes). Note: in the EDLI event-bound runtime path,
        bias/grid corrections are applied upstream before MarketAnalysis is
        constructed, so this array already reflects those upstream corrections.
        It is NOT guaranteed to be a genuinely pre-correction array; callers
        should treat it as provenance/informational only. Adds no further
        transformation — it merely exposes what was stored at construction.
        """
        return self._raw_member_maxes

    @property
    def unit(self) -> str:
        """Public accessor for the settlement unit ('C' or 'F')."""
        return self._unit

    @property
    def precision(self) -> float:
        """Public accessor for settlement precision (1.0 = integer, 0.1 = one decimal)."""
        return self._precision

    def forecast_context(self) -> dict:
        return {
            "uncertainty": self.sigma_context(),
            "location": self.mean_context(),
            "market_complete": self.market_complete,
            "bootstrap_signal_type": self._bootstrap_signal_type,
        }

    def _bootstrap_p_raw_all(self, n_members: int) -> np.ndarray:
        """Sample the same probability object used for edge confidence."""
        if self._bootstrap_probability_sampler is not None:
            sampled = np.asarray(self._bootstrap_probability_sampler(self, n_members), dtype=float)
            return _finite_probability_distribution(
                f"{self._bootstrap_signal_type}_bootstrap_probability_sample",
                sampled,
                len(self.bins),
            )
        sample = self._rng.choice(self._member_maxes, size=n_members, replace=True)
        # MC noise = instrument/bootstrap sigma combined with the representativeness
        # residual sigma IN QUADRATURE. Computed lazily here (not cached at construction)
        # so a post-construction self._sigma mutation is still honoured. σ_repr=0 =>
        # hypot(self._sigma, 0) == self._sigma exactly, preserving legacy behaviour
        # bit-for-bit (including self._sigma=0 -> mc_sigma=0).
        mc_sigma = float(np.hypot(self._sigma, self._representativeness_sigma))
        noised = sample + self._rng.normal(0, mc_sigma, n_members)
        measured = self._settle(noised)
        return np.array([self._bin_probability(measured, bb) for bb in self.bins])

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
        return self.find_edges_with_trace(n_bootstrap=n_bootstrap)[0]

    def find_edges_with_trace(
        self, n_bootstrap: int | None = None
    ) -> tuple[list[BinEdge], list[EdgeScanTrace]]:
        """Scan all bins and explain why each side did or did not emit an edge."""
        # Semantic Provenance Guard
        if False: _ = None.selected_method; _ = None.entry_method
        if n_bootstrap is None:
            n_bootstrap = edge_n_bootstrap()
        if self.p_market is None:
            raise ValueError("find_edges requires executable YES-side market prices")
        edges: list[BinEdge] = []
        trace: list[EdgeScanTrace] = []

        for i, b in enumerate(self.bins):
            if not self.is_executable_bin(i):
                trace.append(
                    EdgeScanTrace(
                        support_index=i,
                        bin_label=b.label,
                        executable=False,
                        direction="support",
                        p_posterior=None,
                        p_market=None,
                        raw_edge=None,
                        ci_lower=None,
                        ci_upper=None,
                        p_value=None,
                        decision="non_executable_bin",
                    )
                )
                continue
            # Buy YES direction.
            # K2 (PR #348 operator review, P0-3): hard-veto BEFORE edge
            # construction when EntryQuoteEvidence flags the orderbook as
            # not-executable. THIN_BOOK + CROSSED reliability cannot
            # produce a tradeable cost — there is no point computing edge
            # statistics over them, and downstream Kelly sizing would
            # silently use a degenerate cost.
            eqe_yes = (
                self._entry_quote_evidence_yes[i]
                if self._entry_quote_evidence_yes is not None
                else None
            )
            if eqe_yes is not None and eqe_yes.reliability_status in (
                "THIN_BOOK", "CROSSED"
            ):
                trace.append(
                    EdgeScanTrace(
                        support_index=i,
                        bin_label=b.label,
                        executable=True,
                        direction="buy_yes",
                        p_posterior=float(self.p_posterior[i]),
                        p_market=float(self.p_market[i]),
                        raw_edge=None,
                        ci_lower=None,
                        ci_upper=None,
                        p_value=None,
                        decision=f"market_cost_hard_veto:{eqe_yes.reliability_status.lower()}",
                        native_quote_available=True,
                    )
                )
                # Fall through to buy_no without producing a buy_yes edge.
                pass
            else:
                # K1 (PR #348, P0-2): compute edge off the cost-corrected
                # entry-cost mean. When EQE is present, this is the all-in
                # cost (depth-walked fill + fee). When absent, falls back
                # to legacy p_market so behaviour is preserved for callers
                # without EQE wiring.
                entry_cost_mean = (
                    float(eqe_yes.all_in_entry_price) if eqe_yes is not None
                    else float(self.p_market[i])
                )
                entry_cost_uncertainty = (
                    float(eqe_yes.cost_uncertainty) if eqe_yes is not None else 0.0
                )
                edge_yes = float(self.p_posterior[i]) - entry_cost_mean
                if edge_yes > 0:
                    ci_lo, ci_hi, p_val = self._bootstrap_bin(i, n_bootstrap)
                    if ci_lo > 0:
                        # Wave 2 (INV-38): construct typed ExecutionPrice at the
                        # edge-scan seam so VWMP provenance from
                        # _buy_entry_price_from_clob travels intact to the Kelly
                        # boundary. The Kelly seam (evaluator.py
                        # _size_at_execution_price_boundary) no longer fabricates
                        # price_type="implied_probability" over this object.
                        # Wave 5: prefer the EntryQuoteEvidence-derived all-in
                        # price + fee_adjusted ExecutionPrice when EQE is
                        # provided; otherwise stamp VWMP (Wave 2 default).
                        if eqe_yes is not None:
                            yes_entry_price = eqe_yes.to_execution_price()
                        else:
                            yes_entry_price = ExecutionPrice(
                                value=float(self.p_market[i]),
                                price_type="vwmp",
                                fee_deducted=False,
                                currency="probability_units",
                            )
                        edge = BinEdge(
                            bin=b,
                            direction="buy_yes",
                            edge=edge_yes,
                            ci_lower=ci_lo,
                            ci_upper=ci_hi,
                            p_model=float(self.p_cal[i]),
                            p_market=float(self.p_market[i]),
                            p_posterior=float(self.p_posterior[i]),
                            entry_price=yes_entry_price,
                            p_value=p_val,
                            vwmp=float(self.p_market[i]),
                            forward_edge=edge_yes,
                            support_index=i,
                            entry_quote_evidence=eqe_yes,
                            entry_cost_mean=entry_cost_mean,
                            entry_cost_uncertainty=entry_cost_uncertainty,
                            market_cost_uncertainty_applied=(
                                eqe_yes is not None and entry_cost_uncertainty > 0.0
                            ),
                        )
                        edges.append(edge)
                        yes_decision = "yes_edge_accepted"
                    else:
                        yes_decision = "yes_ci_lower_nonpositive"
                    trace.append(
                        EdgeScanTrace(
                            support_index=i,
                            bin_label=b.label,
                            executable=True,
                            direction="buy_yes",
                            p_posterior=float(self.p_posterior[i]),
                            p_market=float(self.p_market[i]),
                            raw_edge=edge_yes,
                            ci_lower=ci_lo,
                            ci_upper=ci_hi,
                            p_value=p_val,
                            decision=yes_decision,
                            native_quote_available=True,
                        )
                    )
                else:
                    trace.append(
                        EdgeScanTrace(
                            support_index=i,
                            bin_label=b.label,
                            executable=True,
                            direction="buy_yes",
                            p_posterior=float(self.p_posterior[i]),
                            p_market=float(self.p_market[i]),
                            raw_edge=edge_yes,
                            ci_lower=None,
                            ci_upper=None,
                            p_value=None,
                            decision="yes_raw_edge_nonpositive",
                            native_quote_available=True,
                        )
                    )

            # Buy NO direction: payoff probability is complement; executable
            # entry price must come from the native NO side when available.
            if self.supports_buy_no_edges(i):
                p_model_no = 1.0 - float(self.p_cal[i])
                p_market_no = self.buy_no_market_price(i)
                p_post_no = 1.0 - float(self.p_posterior[i])
                # K2 (PR #348 P0-3): hard-veto NO-side too when EQE reliability
                # is THIN_BOOK / CROSSED.
                eqe_no = (
                    self._entry_quote_evidence_no[i]
                    if self._entry_quote_evidence_no is not None
                    else None
                )
                if eqe_no is not None and eqe_no.reliability_status in (
                    "THIN_BOOK", "CROSSED"
                ):
                    trace.append(
                        EdgeScanTrace(
                            support_index=i,
                            bin_label=b.label,
                            executable=True,
                            direction="buy_no",
                            p_posterior=p_post_no,
                            p_market=p_market_no,
                            raw_edge=None,
                            ci_lower=None,
                            ci_upper=None,
                            p_value=None,
                            decision=f"market_cost_hard_veto:{eqe_no.reliability_status.lower()}",
                            native_quote_available=True,
                        )
                    )
                    continue  # skip this bin's buy_no construction
                # K1 (PR #348 P0-2): NO-side edge off cost-corrected mean.
                entry_cost_mean_no = (
                    float(eqe_no.all_in_entry_price) if eqe_no is not None
                    else float(p_market_no)
                )
                entry_cost_uncertainty_no = (
                    float(eqe_no.cost_uncertainty) if eqe_no is not None else 0.0
                )
                edge_no = p_post_no - entry_cost_mean_no

                if edge_no > 0:
                    ci_lo, ci_hi, p_val = self._bootstrap_bin_no(i, n_bootstrap)
                    if ci_lo > 0:
                        # Wave 2 (INV-38): buy_no uses NATIVE NO-side VWMP from
                        # buy_no_market_price (executable NO quote, not the YES
                        # complement). Same provenance as buy_yes.
                        if eqe_no is not None:
                            no_entry_price = eqe_no.to_execution_price()
                        else:
                            no_entry_price = ExecutionPrice(
                                value=float(p_market_no),
                                price_type="vwmp",
                                fee_deducted=False,
                                currency="probability_units",
                            )
                        edge = BinEdge(
                            bin=b,
                            direction="buy_no",
                            edge=edge_no,
                            ci_lower=ci_lo,
                            ci_upper=ci_hi,
                            p_model=p_model_no,
                            p_market=p_market_no,
                            p_posterior=p_post_no,
                            entry_price=no_entry_price,
                            p_value=p_val,
                            vwmp=p_market_no,
                            forward_edge=edge_no,
                            support_index=i,
                            entry_quote_evidence=eqe_no,
                            entry_cost_mean=entry_cost_mean_no,
                            entry_cost_uncertainty=entry_cost_uncertainty_no,
                            market_cost_uncertainty_applied=(
                                eqe_no is not None and entry_cost_uncertainty_no > 0.0
                            ),
                        )
                        edges.append(edge)
                        no_decision = "no_edge_accepted"
                    else:
                        no_decision = "no_ci_lower_nonpositive"
                    trace.append(
                        EdgeScanTrace(
                            support_index=i,
                            bin_label=b.label,
                            executable=True,
                            direction="buy_no",
                            p_posterior=p_post_no,
                            p_market=p_market_no,
                            raw_edge=edge_no,
                            ci_lower=ci_lo,
                            ci_upper=ci_hi,
                            p_value=p_val,
                            decision=no_decision,
                            native_quote_available=True,
                        )
                    )
                else:
                    trace.append(
                        EdgeScanTrace(
                            support_index=i,
                            bin_label=b.label,
                            executable=True,
                            direction="buy_no",
                            p_posterior=p_post_no,
                            p_market=p_market_no,
                            raw_edge=edge_no,
                            ci_lower=None,
                            ci_upper=None,
                            p_value=None,
                            decision="no_raw_edge_nonpositive",
                            native_quote_available=True,
                        )
                    )
            else:
                no_quote_decision = (
                    "no_native_quote_not_probed"
                    if self.p_market_no is None or self.buy_no_quote_available is None
                    else "no_native_quote_unavailable"
                )
                no_quote_available = None if no_quote_decision.endswith("not_probed") else False
                trace.append(
                    EdgeScanTrace(
                        support_index=i,
                        bin_label=b.label,
                        executable=True,
                        direction="buy_no",
                        p_posterior=1.0 - float(self.p_posterior[i]),
                        p_market=None if self.p_market_no is None else float(self.p_market_no[i]),
                        raw_edge=None,
                        ci_lower=None,
                        ci_upper=None,
                        p_value=None,
                        decision=no_quote_decision,
                        native_quote_available=no_quote_available,
                    )
                )

        return edges, trace

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

        rng = self._rng
        bootstrap_edges = np.zeros(n)

        input_space = getattr(self._calibrator, "input_space", "raw_probability") if self._calibrator else "raw_probability"
        is_wnd = input_space == "width_normalized_density"

        # BUG #129 (estimator-mismatch fix, symmetric with _bootstrap_bin_no): use the SAME
        # current/MAP Platt params (A, B, C) as the point estimate self.p_cal, NOT a random
        # historical bootstrap-param triple per sample. The historical-param distribution is a
        # second estimator that can push percentile(q, 5) above the MAP point in the high-q
        # ceiling regime, bypassing the CI haircut. Live YES bins are low-q so the YES inversion
        # is latent today, but the defect class is identical — fix it here too. Member resampling
        # (σ_ensemble) is retained as the legitimate forecast-uncertainty source.
        map_A = float(self._calibrator.A) if has_platt else 0.0
        map_B = float(self._calibrator.B) if has_platt else 0.0
        map_C = float(self._calibrator.C) if has_platt else 0.0

        for i in range(n):
            # Layer 1: sample the configured signal probability object for all
            # bins. Generic ENS uses member resampling; Day0 injects the
            # observation-fused signal sampler so CI and p_raw share authority.
            p_raw_all = self._bootstrap_p_raw_all(n_members)

            # Layer 2: calibrate with the current/MAP Platt parameterization for ALL bins
            if has_platt:
                A, B, C = map_A, map_B, map_C
                p_cal_boot_all = np.empty(len(self.bins))
                for j, bb in enumerate(self.bins):
                    p_input = p_raw_all[j]
                    if is_wnd:
                        p_input = normalize_bin_probability_for_calibration(
                            p_raw_all[j],
                            bin_width=bb.width,
                        )
                    z = A * logit_safe(p_input) + B * self._lead_days + C
                    if self._transfer_logit_sigma > 0.0:
                        z += rng.normal(0.0, self._transfer_logit_sigma)
                    p_cal_boot_all[j] = 1.0 / (1.0 + np.exp(-z))
            else:
                p_cal_boot_all = p_raw_all

            p_post = self._compute_posterior(p_cal_boot_all)
            # Wave 5: σ_market sampling. When EntryQuoteEvidence is provided
            # for this bin, draw c_b ~ N(all_in_entry_price, cost_uncertainty);
            # otherwise fall back to the fixed-p_market path (legacy bit-
            # identical behaviour). c_b is clipped to (0, 1) so degenerate
            # tail samples cannot drive the edge into the unbounded region
            # where the downstream Kelly formula loses meaning.
            c_b = float(self.p_market[bin_idx])
            if self._entry_quote_evidence_yes is not None:
                eqe = self._entry_quote_evidence_yes[bin_idx]
                if eqe is not None and float(eqe.cost_uncertainty) > 0.0:
                    c_b = float(eqe.all_in_entry_price) + self._cost_rng.normal(
                        0.0, float(eqe.cost_uncertainty)
                    )
                    # X5 fix (Copilot review of PR #348): clip range aligned
                    # with Platt's operator-pinned P_CLAMP_LOW (INV-eps-spec-
                    # conformance) so both probability-space gates use the
                    # same bound. Tighter clipping (1e-6) was inconsistent
                    # with the rest of the calibration pipeline.
                    c_b = float(np.clip(c_b, P_CLAMP_LOW, P_CLAMP_HIGH))
                elif eqe is not None:
                    c_b = float(eqe.all_in_entry_price)
            bootstrap_edges[i] = p_post[bin_idx] - c_b

        # Spec: p-value = np.mean(edges <= 0), NOT approximated
        p_value = float(np.mean(bootstrap_edges <= 0))
        ci_lo = float(np.percentile(bootstrap_edges, 5))
        ci_hi = float(np.percentile(bootstrap_edges, 95))

        # BUG #129 antibody (symmetric with _bootstrap_bin_no): clamp ci_lo so the restored
        # q_lcb = ci_lo + c_b_point can never exceed the YES-side point q = p_posterior[bin_idx].
        # Makes "q_lcb > q_point" unconstructable on the YES leg too. c_b_point is the FIXED YES
        # market price the adapter adds back at restore, so the cancellation is exact.
        c_b_point = float(self.p_market[bin_idx])
        point_edge_ceiling = float(self.p_posterior[bin_idx]) - c_b_point
        ci_lo = min(ci_lo, point_edge_ceiling)
        ci_hi = max(ci_hi, ci_lo)

        result = (ci_lo, ci_hi, p_value)
        self._bootstrap_cache[("yes", bin_idx, n)] = result
        return result

    def _no_certain_yes_floor(self, bin_idx: int) -> float:
        """Irreducible Gaussian YES-mass of a bin given the representativeness σ.

        "No certain NO" structural antibody (#89, iron rule 5, 2026-06-03). The #129 clamp caps
        the NO lower bound at the NO POINT q_no = 1 - p_posterior[bin_idx]. For a DEEP-tail bin
        (member mean many σ away) p_posterior ≈ 0, so q_no_point ≈ 1.0 and the member-resampling
        bootstrap almost never lands in the bin — q_no_lcb saturates at ~1.0 ("certain NO") even
        with σ_repr folded into the MC noise. A mean-only bias correction CANNOT make a far bin a
        certain not-settle when the irreducible residual σ is ~2°C.

        This returns the HONEST Gaussian mass P(settlement ∈ bin | mean = member mean, σ = σ_repr):
        the irreducible probability the settlement still lands in the bin given only the
        representativeness uncertainty. The caller subtracts it from the NO ceiling so
        q_no_lcb ≤ 1 - YES_floor < 1 whenever σ_repr > 0 — making a corrected-domain q_no_lcb of
        exactly 1.0 UNCONSTRUCTABLE.

        ANTI-P-HACKING: the floor is the GENUINE Gaussian tail mass of the bin, NOT an invented
        constant. At a realistic deep-NO distance (~2-3°C, the q≈0.93 regime the operator cited)
        it is material (~0.07). At extreme distance (≥6°C) it is honestly microscopic — the bin
        really is ~99.9% not-settle, so the ceiling barely moves (and that is correct, not a bug).
        σ_repr = 0 → floor = 0 → the ceiling reduces to the legacy #129 clamp, byte-identical.
        The Gaussian is centred on the CORRECTED member mean (same array q is computed from) so
        train==serve.

        POINT-BIN ROUNDING (correctness, not tuning): a °C point market is labelled "29°C" with
        low == high == 29, but the SETTLEMENT is the ROUNDED value, so the bin actually captures
        the rounding interval [v - precision/2, v + precision/2]. Integrating the Gaussian over a
        zero-width point would give a spurious mass of exactly 0 (a "certain NO" the σ cannot
        forbid). We therefore expand any closed bin by half the settlement precision on each side
        so the mass is the honest P(rounded settlement ∈ bin). For a range bin (°F width-2) the
        boundaries already span the rounding interval, so the same half-precision expansion is
        the consistent treatment of the inclusive integer endpoints.
        """
        sigma = float(self._representativeness_sigma)
        if sigma <= 0.0 or not np.isfinite(sigma):
            return 0.0  # no correction / no σ → no ceiling beyond the legacy #129 clamp
        members = self._member_maxes
        if len(members) == 0:
            return 0.0
        mean = float(np.mean(members))
        b = self.bins[bin_idx]
        from statistics import NormalDist

        nd = NormalDist(mean, sigma)
        lo = None if b.low is None else float(b.low)
        hi = None if b.high is None else float(b.high)
        if lo is None and hi is None:
            return 0.0
        # Expand closed endpoints by half the settlement precision so the Gaussian integrates
        # over the rounding interval the settlement actually falls in (point bins low==high would
        # otherwise yield mass 0). Open ends stay open.
        half = max(float(getattr(self, "_precision", 1.0)), 0.0) / 2.0
        upper = 1.0 if hi is None else nd.cdf(hi + half)
        lower = 0.0 if lo is None else nd.cdf(lo - half)
        mass = upper - lower
        if not np.isfinite(mass) or mass <= 0.0:
            return 0.0
        return float(min(1.0, mass))

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

        rng = self._rng
        bootstrap_edges = np.zeros(n)

        input_space = getattr(self._calibrator, "input_space", "raw_probability") if self._calibrator else "raw_probability"
        is_wnd = input_space == "width_normalized_density"

        # BUG #129 (estimator-mismatch fix): ground the bootstrap calibration in the SAME
        # current/MAP Platt params (A, B, C) used to produce the point estimate self.p_cal,
        # NOT a random HISTORICAL bootstrap-param triple drawn per sample. Drawing historical
        # params introduces a SECOND estimator whose distribution maps high-q_no bins
        # systematically higher, so percentile(q_no, 5) lands ABOVE the MAP point and the
        # designed CI haircut is bypassed at robust_trade_score's min(q_lcb, q_live). With MAP
        # params, point and LCB share one estimator; member resampling (the legitimate
        # forecast-uncertainty source) is retained. See PROBABILITY_INTEGRITY_AUDIT_2026-06-02 LEG 1.
        map_A = float(self._calibrator.A) if has_platt else 0.0
        map_B = float(self._calibrator.B) if has_platt else 0.0
        map_C = float(self._calibrator.C) if has_platt else 0.0

        for i in range(n):
            p_raw_all = self._bootstrap_p_raw_all(n_members)

            if has_platt:
                A, B, C = map_A, map_B, map_C
                p_cal_boot_all = np.empty(len(self.bins))
                for j, bb in enumerate(self.bins):
                    p_input = p_raw_all[j]
                    if is_wnd:
                        p_input = normalize_bin_probability_for_calibration(
                            p_raw_all[j],
                            bin_width=bb.width,
                        )
                    z = A * logit_safe(p_input) + B * self._lead_days + C
                    if self._transfer_logit_sigma > 0.0:
                        z += rng.normal(0.0, self._transfer_logit_sigma)
                    p_cal_boot_all[j] = 1.0 / (1.0 + np.exp(-z))
            else:
                p_cal_boot_all = p_raw_all

            p_post_yes = self._compute_posterior(p_cal_boot_all)[bin_idx]
            # Wave 5: σ_market sampling on the NO side. Same semantics as the
            # buy_yes path — when EntryQuoteEvidence is provided for the NO
            # leg, draw c_b ~ N(eqe.all_in_entry_price, eqe.cost_uncertainty);
            # legacy callers (no EQE) keep the fixed buy_no_market_price path.
            c_b = float(self.buy_no_market_price(bin_idx))
            if self._entry_quote_evidence_no is not None:
                eqe = self._entry_quote_evidence_no[bin_idx]
                if eqe is not None and float(eqe.cost_uncertainty) > 0.0:
                    c_b = float(eqe.all_in_entry_price) + self._cost_rng.normal(
                        0.0, float(eqe.cost_uncertainty)
                    )
                    # X5 fix (Copilot review of PR #348): clip range aligned
                    # with Platt's operator-pinned P_CLAMP_LOW (INV-eps-spec-
                    # conformance) so both probability-space gates use the
                    # same bound. Tighter clipping (1e-6) was inconsistent
                    # with the rest of the calibration pipeline.
                    c_b = float(np.clip(c_b, P_CLAMP_LOW, P_CLAMP_HIGH))
                elif eqe is not None:
                    c_b = float(eqe.all_in_entry_price)
            bootstrap_edges[i] = (1.0 - p_post_yes) - c_b

        p_value = float(np.mean(bootstrap_edges <= 0))
        ci_lo = float(np.percentile(bootstrap_edges, 5))
        ci_hi = float(np.percentile(bootstrap_edges, 95))

        # BUG #129 antibody (construction-level invariant): the 5th-percentile edge restores to
        # q_lcb = ci_lo + c_b_point (event_reactor_adapter restore). Clamp ci_lo so q_lcb can NEVER
        # exceed the NO-side point q_no = 1 - p_posterior[bin_idx]. This makes "q_lcb > q_point"
        # UNCONSTRUCTABLE regardless of the calibration estimator or sampling skew — a lower bound
        # that exceeds the point is not a lower bound. c_b_point is the FIXED NO market price (the
        # same cost the adapter adds back at restore), so the cancellation is exact.
        c_b_point = float(self.buy_no_market_price(bin_idx))
        # "NO CERTAIN NO" ceiling (#89, iron rule 5, 2026-06-03). On the bias-corrected domain
        # (σ_repr > 0) the NO ceiling is tightened from the legacy q_no_point (= 1 - p_posterior)
        # to 1 - YES_floor, where YES_floor is the irreducible Gaussian mass the settlement still
        # lands in this bin given σ_repr. This makes a corrected-domain q_no_lcb of exactly 1.0
        # UNCONSTRUCTABLE: a deep-NO bin where the member-resampling bootstrap saturates (q_no_lcb
        # → q_no_point ≈ 1.0) is capped at the honest 1 - YES_floor instead. σ_repr = 0 ⇒
        # YES_floor = 0 ⇒ ceiling == legacy #129 clamp, byte-identical. The ceiling only ever
        # LOWERS the bound (max with the legacy floor below would be wrong — we take the tighter
        # of the two so the bound is never RAISED above either).
        yes_floor = self._no_certain_yes_floor(bin_idx)
        q_no_ceiling = min(1.0 - float(self.p_posterior[bin_idx]), 1.0 - yes_floor)
        point_edge_ceiling = q_no_ceiling - c_b_point
        ci_lo = min(ci_lo, point_edge_ceiling)
        ci_hi = max(ci_hi, ci_lo)

        result = (ci_lo, ci_hi, p_value)
        self._bootstrap_cache[("no", bin_idx, n)] = result
        return result

    @staticmethod
    def _bin_probability(measured: np.ndarray, b: Bin) -> float:
        """Compute fraction of measured values falling in bin."""
        return bin_probability_from_values(measured, b)
