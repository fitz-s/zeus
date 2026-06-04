# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: PR #348 operator review verdict — 5 P0 blockers restated as 4 K decisions
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: PR #348 blocker antibodies — per-edge cost evidence, hard veto, depth cap, Stage B refusal
# Reuse: Pins K1 (per-edge unified budget gate), K2 (THIN_BOOK/CROSSED hard veto), K3 (depth cap), K4 (Stage B live refusal) — Stage 2 live promotion requires all 4 GREEN.
"""PR #348 blocker antibodies — 5 P0 / 4 K decisions.

Operator review (2026-05-27) on top of PR #348 identified five P0 blockers
preventing Stage 2 live promotion. Per Fitz §1 they reduce to four
structural decisions:

  K1 (P0-1 + P0-2): per-edge cost evidence drives BOTH bootstrap AND
       multiplier suppression AND stored edge magnitude. The pre-blocker
       implementation made unified-uncertainty-budget a GLOBAL env flag
       independent of whether each edge actually had EntryQuoteEvidence
       in its bootstrap. Same global flag also left ``edge`` / ``forward_edge``
       computed off legacy ``p_market``, so family selection + economic
       floors used optimistic numbers even when EQE was present.

  K2 (P0-3): EntryQuoteEvidence.reliability_status THIN_BOOK / CROSSED
       are documented as hard-veto-eligible but find_edges did not
       enforce — bootstrap and edge construction continued for these.

  K3 (P0-4): depth-walk target_shares uses min_order_usd / best_ask
       (floor estimate); larger Kelly orders may face larger slippage
       than σ_market accounts for. Simplest correct response is to cap
       sized Kelly output to ``eqe.depth_at_target_size`` so live
       execution cannot exceed depth-walked authority.

  K4 (P0-5): Stage B family optimizer's expected_log_growth is computed
       over normalized candidate legs only, not the full family outcome
       distribution. ``max_legs > 1`` is mathematically incorrect for
       live capital until the optimizer ships a true full-outcome ELG.
       Until then live mode must refuse the unsafe combination.
"""
from __future__ import annotations

from unittest import mock

import numpy as np
import pytest

from src.contracts.entry_quote_evidence import EntryQuoteEvidence
from src.strategy.kelly import _ENV_UNIFIED_UNCERTAINTY_BUDGET, dynamic_kelly_mult
from src.types.market import Bin, BinEdge
from src.contracts.forecast_sharpness import ForecastSharpnessEvidence


def _bins() -> list[Bin]:
    return [
        Bin(low=None, high=26.0, unit="C", label="26C or below"),
        Bin(low=26.0, high=None, unit="C", label="27C or above"),
    ]


def _eqe(
    value: float = 0.30,
    cost_uncertainty: float = 0.02,
    reliability: str = "LIVE_OK",
    depth_at_target: float = 200.0,
) -> EntryQuoteEvidence:
    return EntryQuoteEvidence(
        token_id="tok",
        side="yes",
        best_bid=value - 0.01,
        best_ask=value,
        spread_usd=0.01,
        top_of_book_size=200.0,
        depth_at_target_size=depth_at_target,
        fill_price_walk=value,
        slippage_bps=0.0,
        quote_age_ms=0,
        book_hash="",
        fee_rate=0.0,
        fee_per_share=0.0,
        all_in_entry_price=value,
        cost_uncertainty=cost_uncertainty,
        reliability_status=reliability,
    )


def _make_analysis(*, eqe_yes=None, rng_seed=42):
    from src.strategy.market_analysis import MarketAnalysis

    return MarketAnalysis(forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="F"), 
        p_raw=np.array([0.55, 0.45]),
        p_cal=np.array([0.55, 0.45]),
        p_market=np.array([0.30, 0.30]),
        alpha=0.0,
        bins=_bins(),
        member_maxes=np.array([25.5, 25.8, 26.0, 26.3, 26.6]),
        executable_mask=np.array([True, True]),
        rng_seed=rng_seed,
        entry_quote_evidence_yes=eqe_yes,
    )


# ---------------------------------------------------------------------------
# K1 — per-edge unified-budget guard (P0-1)
# ---------------------------------------------------------------------------


class TestK1_PerEdgeUnifiedBudgetGuard:
    def test_dynamic_kelly_mult_accepts_market_uncertainty_in_lcb_kwarg(self):
        """dynamic_kelly_mult must accept a per-edge market_uncertainty_in_lcb flag.

        Pre-blocker: flag was global env only. Post-blocker: caller passes
        per-edge evidence so the haircut is suppressed only for edges that
        actually have σ_market in edge_LCB.
        """
        import inspect
        sig = inspect.signature(dynamic_kelly_mult)
        assert "market_uncertainty_in_lcb" in sig.parameters, (
            "dynamic_kelly_mult must accept market_uncertainty_in_lcb per-edge flag"
        )

    def test_flag_on_but_no_per_edge_evidence_preserves_ci_width_haircut(
        self, monkeypatch
    ):
        """Global unified-budget flag ON + per-edge evidence False → haircut still applies."""
        monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", "1")
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "1")
        m_baseline = dynamic_kelly_mult(
            base=0.25, ci_width=0.05, market_uncertainty_in_lcb=False
        )
        m_wide = dynamic_kelly_mult(
            base=0.25, ci_width=0.20, market_uncertainty_in_lcb=False
        )
        # No per-edge evidence → legacy ci_width haircut still fires.
        assert m_wide == pytest.approx(m_baseline * 0.35)

    def test_flag_on_with_per_edge_evidence_collapses_haircut(self, monkeypatch):
        """Global unified-budget flag ON + per-edge evidence True → haircut collapses."""
        monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", "1")
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "1")
        m_no_ci = dynamic_kelly_mult(
            base=0.25, ci_width=0.05, market_uncertainty_in_lcb=True
        )
        m_wide_ci = dynamic_kelly_mult(
            base=0.25, ci_width=0.20, market_uncertainty_in_lcb=True
        )
        assert m_wide_ci == pytest.approx(m_no_ci)


# ---------------------------------------------------------------------------
# K1 — edge / forward_edge use all_in_entry_cost (P0-2)
# ---------------------------------------------------------------------------


class TestK1_StoredEdgeUsesAllInCost:
    def test_bin_edge_carries_entry_cost_mean_and_uncertainty(self):
        b = _bins()[0]
        ep = mock.MagicMock()
        ep.value = 0.35
        edge = BinEdge(
            bin=b, direction="buy_yes",
            edge=0.20, ci_lower=0.05, ci_upper=0.35,
            p_model=0.55, p_market=0.30, p_posterior=0.55,
            entry_price=0.35, p_value=0.01, vwmp=0.30,
            forward_edge=0.20, support_index=0,
        )
        # New optional fields exist with sensible defaults.
        assert hasattr(edge, "entry_cost_mean"), "BinEdge needs entry_cost_mean field"
        assert hasattr(edge, "entry_cost_uncertainty"), "BinEdge needs entry_cost_uncertainty field"
        assert hasattr(edge, "market_cost_uncertainty_applied"), (
            "BinEdge needs market_cost_uncertainty_applied flag"
        )

    def test_find_edges_with_eqe_sets_edge_off_all_in_cost_not_p_market(self):
        from src.contracts.entry_quote_evidence import EntryQuoteEvidence

        # EQE has higher all_in (fee) than raw p_market → edge MUST shrink.
        eqe_high = EntryQuoteEvidence(
            token_id="t", side="yes",
            best_bid=0.29, best_ask=0.30, spread_usd=0.01,
            top_of_book_size=200.0, depth_at_target_size=200.0,
            fill_price_walk=0.30, slippage_bps=0.0,
            quote_age_ms=0, book_hash="",
            fee_rate=0.05,
            fee_per_share=0.05 * 0.30 * 0.70,  # ~0.0105
            all_in_entry_price=0.30 + 0.05 * 0.30 * 0.70,  # ~0.3105
            cost_uncertainty=0.0,
            reliability_status="LIVE_OK",
        )
        a = _make_analysis(eqe_yes=[eqe_high, eqe_high], rng_seed=42)
        edges = a.find_edges(n_bootstrap=200)
        assert len(edges) >= 1
        for e in edges:
            if e.direction != "buy_yes":
                continue
            # Stored edge must reflect cost-corrected magnitude.
            expected_cost_corrected = float(e.p_posterior) - 0.3105
            assert e.edge == pytest.approx(expected_cost_corrected, abs=1e-6), (
                f"stored edge {e.edge:.6f} still uses legacy p_market; "
                f"expected cost-corrected {expected_cost_corrected:.6f}"
            )
            assert e.forward_edge == pytest.approx(e.edge, abs=1e-6), (
                "forward_edge must align with cost-corrected edge for downstream "
                "family selection + economic floors"
            )
            assert e.market_cost_uncertainty_applied is False, (
                "cost_uncertainty=0 → market_cost_uncertainty_applied should be False"
            )

    def test_find_edges_eqe_with_uncertainty_sets_evidence_applied_true(self):
        a = _make_analysis(
            eqe_yes=[_eqe(0.30, cost_uncertainty=0.03), _eqe(0.30, cost_uncertainty=0.03)],
            rng_seed=42,
        )
        edges = a.find_edges(n_bootstrap=200)
        assert len(edges) >= 1
        for e in edges:
            if e.direction != "buy_yes":
                continue
            assert e.market_cost_uncertainty_applied is True
            assert e.entry_cost_uncertainty > 0.0


# ---------------------------------------------------------------------------
# K2 — THIN_BOOK / CROSSED hard veto (P0-3)
# ---------------------------------------------------------------------------


class TestK2_ReliabilityHardVeto:
    def test_thin_book_eqe_hard_vetoes_buy_yes_edge(self):
        thin = _eqe(value=0.30, cost_uncertainty=0.02, reliability="THIN_BOOK",
                    depth_at_target=5.0)
        a = _make_analysis(eqe_yes=[thin, thin], rng_seed=42)
        edges = a.find_edges(n_bootstrap=200)
        # THIN_BOOK must hard-veto the edge (no BinEdge emitted).
        assert all(e.direction != "buy_yes" for e in edges), (
            "THIN_BOOK reliability must hard-veto buy_yes edges before construction"
        )

    def test_crossed_eqe_hard_vetoes_buy_yes_edge(self):
        crossed = _eqe(value=0.30, cost_uncertainty=0.02, reliability="CROSSED")
        a = _make_analysis(eqe_yes=[crossed, crossed], rng_seed=42)
        edges = a.find_edges(n_bootstrap=200)
        assert all(e.direction != "buy_yes" for e in edges), (
            "CROSSED reliability must hard-veto buy_yes edges before construction"
        )

    def test_live_ok_eqe_does_not_veto(self):
        live = _eqe(value=0.30, cost_uncertainty=0.02, reliability="LIVE_OK")
        a = _make_analysis(eqe_yes=[live, live], rng_seed=42)
        edges = a.find_edges(n_bootstrap=200)
        # Should still produce edges (no hard veto for LIVE_OK).
        assert any(e.direction == "buy_yes" for e in edges)


# ---------------------------------------------------------------------------
# K4 — Stage B refuses live max_legs > 1 (P0-5)
# ---------------------------------------------------------------------------


class TestK3_DepthCap:
    def test_size_capped_to_depth_walked_authority(self):
        from src.engine.evaluator import _size_at_execution_price_boundary
        from src.contracts.execution_price import ExecutionPrice

        ep = ExecutionPrice(value=0.30, price_type="fee_adjusted",
                            fee_deducted=True, currency="probability_units")
        # Large bankroll → Kelly would size big; depth caps it.
        uncapped = _size_at_execution_price_boundary(
            p_posterior=0.60, entry_price=ep, fee_rate=0.0,
            sizing_bankroll=100000.0, kelly_multiplier=0.25,
            allow_missing_context=True,
        )
        capped = _size_at_execution_price_boundary(
            p_posterior=0.60, entry_price=ep, fee_rate=0.0,
            sizing_bankroll=100000.0, kelly_multiplier=0.25,
            allow_missing_context=True,
            max_executable_shares=10.0,  # 10 shares @ 0.30 = $3.00 cap
        )
        assert uncapped > capped, "depth cap must reduce an over-large order"
        assert capped == pytest.approx(10.0 * 0.30, abs=1e-6), (
            f"capped size {capped} != depth authority 10*0.30=3.0"
        )

    def test_no_cap_when_max_shares_none(self):
        from src.engine.evaluator import _size_at_execution_price_boundary
        from src.contracts.execution_price import ExecutionPrice

        ep = ExecutionPrice(value=0.30, price_type="fee_adjusted",
                            fee_deducted=True, currency="probability_units")
        a = _size_at_execution_price_boundary(
            p_posterior=0.60, entry_price=ep, fee_rate=0.0,
            sizing_bankroll=1000.0, kelly_multiplier=0.25,
            allow_missing_context=True, max_executable_shares=None,
        )
        b = _size_at_execution_price_boundary(
            p_posterior=0.60, entry_price=ep, fee_rate=0.0,
            sizing_bankroll=1000.0, kelly_multiplier=0.25,
            allow_missing_context=True,
        )
        assert a == pytest.approx(b)


class TestK4_StageBLiveRefusal:
    def test_live_max_legs_gt_1_is_capped_to_1_until_full_optimizer(self, monkeypatch):
        """Stage B optimizer's ELG is not yet full-family. LIVE max_legs > 1 must
        be refused (capped to 1 with WARNING) until full-outcome ELG ships.
        """
        from src.strategy.family_exclusive_dedup import _family_portfolio_max_legs

        monkeypatch.setenv("ZEUS_LIVE_FAMILY_PORTFOLIO_MAX_LEGS", "3")
        with mock.patch("src.strategy.family_exclusive_dedup.get_mode", return_value="live"):
            legs = _family_portfolio_max_legs()
        # Per K4 the live tier must NOT allow > 1 until full-outcome ELG is correct.
        assert legs == 1, (
            f"_family_portfolio_max_legs returned {legs} in live mode with env=3; "
            "expected cap to 1 until Stage B optimizer ships full-family ELG"
        )

    def test_shadow_max_legs_gt_1_still_honoured(self, monkeypatch):
        """Shadow tier may activate Stage B for observation — only LIVE is refused."""
        from src.strategy.family_exclusive_dedup import _family_portfolio_max_legs

        monkeypatch.setenv("ZEUS_SHADOW_FAMILY_PORTFOLIO_MAX_LEGS", "3")
        with mock.patch("src.strategy.family_exclusive_dedup.get_mode", return_value="shadow"):
            legs = _family_portfolio_max_legs()
        assert legs == 3
