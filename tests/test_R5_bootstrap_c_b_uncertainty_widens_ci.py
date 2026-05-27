# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave5 + INV-40
"""R5: σ_market sampling widens edge_ci_lower vs legacy fixed c_b.

Wave 5 wires per-bin EntryQuoteEvidence into MarketAnalysis._bootstrap_bin
so c_b is sampled ~ N(eqe.all_in_entry_price, eqe.cost_uncertainty) every
bootstrap iteration. This codifies the contract:

  1. Behaviour preservation — when no EntryQuoteEvidence is supplied, the
     bootstrap output must be bit-identical to the pre-Wave-5 fixed-c_b path.
  2. σ_market > 0 must produce strictly wider CI (lower ci_lower) than the
     no-evidence baseline at the same RNG seed.
  3. Cost_uncertainty=0 (with EQE present) must give the same CI shape as
     the no-evidence baseline (degenerate σ = legacy behaviour).

Antibody for INV-40 (uncertainty_single_count — the market-cost arm).
"""
from __future__ import annotations

import numpy as np
import pytest

from src.contracts.entry_quote_evidence import EntryQuoteEvidence
from src.strategy.market_analysis import MarketAnalysis
from src.types.market import Bin


def _bins() -> list[Bin]:
    return [
        Bin(low=None, high=26.0, unit="C", label="26°C or below"),
        Bin(low=26.0, high=None, unit="C", label="27°C or above"),
    ]


def _eqe(value: float, cost_uncertainty: float = 0.0) -> EntryQuoteEvidence:
    return EntryQuoteEvidence(
        token_id="tok",
        side="yes",
        best_bid=value - 0.01,
        best_ask=value,
        spread_usd=0.01,
        top_of_book_size=200.0,
        depth_at_target_size=200.0,
        fill_price_walk=value,
        slippage_bps=0.0,
        quote_age_ms=0,
        book_hash="",
        fee_rate=0.0,
        fee_per_share=0.0,
        all_in_entry_price=value,
        cost_uncertainty=cost_uncertainty,
        reliability_status="LIVE_OK",
    )


def _make_analysis(*, eqe_yes=None, rng_seed=42) -> MarketAnalysis:
    bins = _bins()
    p_raw = np.array([0.55, 0.45])
    p_cal = np.array([0.55, 0.45])
    p_market = np.array([0.30, 0.30])
    member_maxes = np.array([25.5, 25.8, 26.0, 26.3, 26.6])
    return MarketAnalysis(
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_market,
        alpha=0.0,
        bins=bins,
        member_maxes=member_maxes,
        executable_mask=np.array([True, True]),
        rng_seed=rng_seed,
        entry_quote_evidence_yes=eqe_yes,
    )


# ---------------------------------------------------------------------------
# Behaviour-preservation (no EQE = legacy bit-identical path)
# ---------------------------------------------------------------------------

class TestNoEvidencePreservesLegacy:
    def test_no_eqe_returns_bit_identical_ci_under_fixed_seed(self):
        a_legacy = _make_analysis(eqe_yes=None, rng_seed=42)
        a_legacy_repeat = _make_analysis(eqe_yes=None, rng_seed=42)
        ci_1 = a_legacy._bootstrap_bin(0, 200)
        ci_2 = a_legacy_repeat._bootstrap_bin(0, 200)
        # Bit-identical given the same seed + inputs.
        assert ci_1 == ci_2

    def test_eqe_with_zero_uncertainty_matches_eqe_absent_at_same_value(self):
        # When EQE present but cost_uncertainty=0 AND value == p_market,
        # the c_b sampled is just float(eqe.all_in_entry_price) = p_market.
        # Result must equal the no-EQE baseline.
        a_baseline = _make_analysis(eqe_yes=None, rng_seed=42)
        a_zero = _make_analysis(
            eqe_yes=[_eqe(0.30, cost_uncertainty=0.0), _eqe(0.30, cost_uncertainty=0.0)],
            rng_seed=42,
        )
        ci_baseline = a_baseline._bootstrap_bin(0, 200)
        ci_zero = a_zero._bootstrap_bin(0, 200)
        # Same numeric c_b, same RNG seed → identical bootstrap edges → identical CI.
        assert ci_baseline == ci_zero


# ---------------------------------------------------------------------------
# σ_market > 0 widens CI
# ---------------------------------------------------------------------------

class TestSigmaMarketWidensCI:
    def test_sigma_market_gt0_lowers_ci_lower(self):
        # σ_market substantially > forecast σ so the cost-uncertainty
        # contribution dominates the sampling noise floor. Same baseline
        # values across both runs, only cost_uncertainty differs.
        a_legacy = _make_analysis(eqe_yes=None, rng_seed=42)
        a_with_sigma = _make_analysis(
            eqe_yes=[_eqe(0.30, cost_uncertainty=0.20), _eqe(0.30, cost_uncertainty=0.20)],
            rng_seed=42,
        )
        ci_legacy = a_legacy._bootstrap_bin(0, 2000)
        ci_sigma = a_with_sigma._bootstrap_bin(0, 2000)
        # σ_market >> 0 → wider CI → strictly lower ci_lower.
        assert ci_sigma[0] < ci_legacy[0], (
            f"σ_market=0.20 did not widen CI: ci_lower_sigma={ci_sigma[0]:.6f} >= "
            f"ci_lower_legacy={ci_legacy[0]:.6f}"
        )

    def test_larger_sigma_market_widens_ci_monotonically(self):
        a_small = _make_analysis(
            eqe_yes=[_eqe(0.30, cost_uncertainty=0.02), _eqe(0.30, cost_uncertainty=0.02)],
            rng_seed=42,
        )
        a_large = _make_analysis(
            eqe_yes=[_eqe(0.30, cost_uncertainty=0.10), _eqe(0.30, cost_uncertainty=0.10)],
            rng_seed=42,
        )
        ci_small = a_small._bootstrap_bin(0, 1000)
        ci_large = a_large._bootstrap_bin(0, 1000)
        # Larger σ → wider CI → strictly lower ci_lower.
        assert ci_large[0] <= ci_small[0]


# ---------------------------------------------------------------------------
# BinEdge carries EntryQuoteEvidence end-to-end
# ---------------------------------------------------------------------------

class TestBinEdgeCarriesEvidence:
    def test_find_edges_stamps_entry_quote_evidence_on_binedge(self):
        a = _make_analysis(
            eqe_yes=[_eqe(0.30, cost_uncertainty=0.02), _eqe(0.30, cost_uncertainty=0.02)],
            rng_seed=42,
        )
        edges = a.find_edges(n_bootstrap=50)
        assert len(edges) >= 1
        for edge in edges:
            if edge.direction == "buy_yes":
                assert edge.entry_quote_evidence is not None, (
                    "buy_yes BinEdge must carry EntryQuoteEvidence when MarketAnalysis was constructed with entry_quote_evidence_yes"
                )
                assert isinstance(edge.entry_quote_evidence, EntryQuoteEvidence)
                # entry_price now sourced from EQE.to_execution_price() →
                # fee_adjusted (already includes fee, even if 0 in this fixture)
                assert edge.entry_price.price_type == "fee_adjusted"
                assert edge.entry_price.fee_deducted is True

    def test_legacy_path_without_eqe_leaves_entry_quote_evidence_none(self):
        a = _make_analysis(eqe_yes=None, rng_seed=42)
        edges = a.find_edges(n_bootstrap=50)
        for edge in edges:
            if edge.direction == "buy_yes":
                assert edge.entry_quote_evidence is None, (
                    "legacy path (no EQE) must leave BinEdge.entry_quote_evidence None"
                )
                # entry_price stays VWMP-typed (Wave 2 default)
                assert edge.entry_price.price_type == "vwmp"
