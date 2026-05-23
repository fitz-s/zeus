# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/WAVE2_CRITIC_VERDICT.md MAJOR-3
"""Router exhaustiveness test — all 10 wave shadow strategies route correctly.

Wave critic MAJOR-3: OpeningInertiaRelaxation and ImminentOpenCapturePosteriorCollapse
emit strategy_keys ("opening_inertia_relaxation", "imminent_open_capture_posterior_collapse")
that are NOT aliased by the legacy base keys in _PIPELINE_B_STRATEGY_KEYS.
Without this fix they defaulted to B by the catch-all — correct pipeline, but no test.

This test asserts routing-by-contract, not routing-by-accident.
"""

from __future__ import annotations

import pytest

from src.analysis.promotion_proof_router import route_proof_class


# ---------------------------------------------------------------------------
# Ground-truth routing table for all 10 wave shadow candidates
# (strategy_key → expected pipeline as emitted by each candidate's evaluate())
# ---------------------------------------------------------------------------

# Pipeline A: deterministic payoff-identity evidence
_EXPECTED_A: list[tuple[str, str | None]] = [
    ("neg_risk_basket", None),
    ("settlement_capture", None),
    ("resolution_window_maker", None),
    ("shoulder_impossible_tail_capture", None),
    # Sub-typed A overrides
    ("center_sell", "pair_parity"),
    ("stale_quote_detector", "fok_latency"),
]

# Pipeline B: calibrated stochastic CI evidence (all wave candidates → B)
_EXPECTED_B: list[tuple[str, str | None]] = [
    # Pre-wave base keys
    ("opening_inertia", None),
    ("center_buy", None),
    ("center_sell", None),          # default route (no proof_type)
    ("shoulder_buy", None),
    ("weather_event_arbitrage", None),
    ("liquidity_provision_with_heartbeat", None),
    ("cross_market_correlation_hedge", None),
    ("imminent_open_capture", None),
    ("stale_quote_detector", None),  # default route (no proof_type)
    # Wave-added distinct keys (MAJOR-3 fix — these were "routing-by-accident" before)
    ("opening_inertia_relaxation", None),              # S2 OpeningInertiaRelaxation
    ("imminent_open_capture_posterior_collapse", None),  # S3 ImminentOpenCapturePosteriorCollapse
]


class TestRouterExhaustiveness:
    """All 10 wave shadow strategy_keys route to the intended pipeline."""

    @pytest.mark.parametrize("strategy_key,proof_type", _EXPECTED_A)
    def test_pipeline_a_routes(self, strategy_key: str, proof_type: str | None) -> None:
        result = route_proof_class(strategy_key, proof_type)
        assert result == "A", (
            f"strategy_key={strategy_key!r} proof_type={proof_type!r} → got {result!r}, expected 'A'"
        )

    @pytest.mark.parametrize("strategy_key,proof_type", _EXPECTED_B)
    def test_pipeline_b_routes(self, strategy_key: str, proof_type: str | None) -> None:
        result = route_proof_class(strategy_key, proof_type)
        assert result == "B", (
            f"strategy_key={strategy_key!r} proof_type={proof_type!r} → got {result!r}, expected 'B'"
        )

    def test_unknown_key_defaults_to_b(self) -> None:
        """Unknown strategy_keys default to B (fail-safe: route to CI pipeline, never drop)."""
        assert route_proof_class("__totally_unknown__") == "B"

    def test_wave_keys_not_routing_by_accident(self) -> None:
        """The two new wave keys must be in _PIPELINE_B_STRATEGY_KEYS (not just catch-all B).

        If they fall through to the catch-all, this test still passes — the distinction
        is documented via the explicit import check below.
        """
        from src.analysis.promotion_proof_router import _PIPELINE_B_STRATEGY_KEYS
        assert "opening_inertia_relaxation" in _PIPELINE_B_STRATEGY_KEYS, (
            "opening_inertia_relaxation must be explicitly registered in "
            "_PIPELINE_B_STRATEGY_KEYS (MAJOR-3)"
        )
        assert "imminent_open_capture_posterior_collapse" in _PIPELINE_B_STRATEGY_KEYS, (
            "imminent_open_capture_posterior_collapse must be explicitly registered in "
            "_PIPELINE_B_STRATEGY_KEYS (MAJOR-3)"
        )
