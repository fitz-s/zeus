# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/F34_COST_OF_FILL_STRUCTURAL.md
#   F34 cost-of-fill optimizer — opt-in via ZEUS_TAKER_CROSSING_ENABLED=1.
"""Antibody tests for F34 cost-of-fill crossing decision.

Test 1: thin book → PASSIVE_THIN_BOOK regardless of opportunity cost
Test 2: high opportunity cost > crossing cost → CROSS
Test 3: low opportunity cost < crossing cost → PASSIVE
Test 4: ZEUS_TAKER_CROSSING_ENABLED=0 (default) → _crossing_decision NOT called
Test 5: ZEUS_TAKER_CROSSING_ENABLED=1 → _crossing_decision IS called
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.engine import cycle_runtime
from src.engine.evaluator import _crossing_decision
from src.types import Bin, BinEdge


class TestCrossingDecisionHelper:
    """Unit tests for the _crossing_decision pure function (Tests 1-3)."""

    def _base_kwargs(self) -> dict:
        return dict(
            best_ask_price=0.60,
            best_ask_size=100.0,
            best_bid_price=0.58,
            p_posterior=0.75,
            expected_pnl_if_filled=10.0,
            non_fill_probability=0.89,
            taker_fee_bps=200.0,
            min_economical_size=10.0,
        )

    def test_thin_book_passive_regardless_of_opportunity(self):
        """Test 1: thin book → PASSIVE_THIN_BOOK even when opportunity cost is high."""
        kwargs = self._base_kwargs()
        kwargs["best_ask_size"] = 5.0      # below min_economical_size=10.0
        kwargs["expected_pnl_if_filled"] = 1_000.0   # enormous opportunity cost
        kwargs["non_fill_probability"] = 0.99

        cross, evidence = _crossing_decision(**kwargs)

        assert cross is False
        assert evidence["decision"] == "PASSIVE_THIN_BOOK"

    def test_high_opportunity_cost_triggers_cross(self):
        """Test 2: opportunity cost >> crossing cost → CROSS."""
        kwargs = self._base_kwargs()
        # cost_of_crossing: (0.60 * 200/10000 + (0.60-0.58)) * 100 = (0.012 + 0.02)*100 = 3.2
        # opportunity_cost: 10.0 * 0.89 = 8.9   → 8.9 > 3.2 → CROSS
        cross, evidence = _crossing_decision(**kwargs)

        assert cross is True
        assert evidence["decision"] == "CROSS"
        assert evidence["opportunity_cost"] > evidence["cost_of_crossing"]

    def test_low_opportunity_cost_stays_passive(self):
        """Test 3: opportunity cost << crossing cost → PASSIVE."""
        kwargs = self._base_kwargs()
        kwargs["expected_pnl_if_filled"] = 0.01   # tiny pnl
        kwargs["non_fill_probability"] = 0.01     # almost always fills anyway
        # opportunity_cost: 0.01 * 0.01 = 0.0001 << cost_of_crossing 3.2 → PASSIVE

        cross, evidence = _crossing_decision(**kwargs)

        assert cross is False
        assert evidence["decision"] == "PASSIVE"
        assert evidence["cost_of_crossing"] > evidence["opportunity_cost"]


class TestCrossingDecisionIntegrationFlag:
    """Flag-gate tests (Tests 4-5) verifying integration site behavior."""

    def _build_minimal_decision(self) -> SimpleNamespace:
        """Build a decision object accepted by executable snapshot repricing."""
        edge = BinEdge(
            bin=Bin(low=70, high=71, unit="F", label="70-71°F"),
            direction="buy_yes",
            edge=0.15,
            ci_lower=0.01,
            ci_upper=0.20,
            p_model=0.75,
            p_market=0.59,
            p_posterior=0.75,
            entry_price=0.60,
            p_value=0.01,
            vwmp=0.59,
            forward_edge=0.15,
        )
        return SimpleNamespace(
            edge=edge,
            selected_method="ens_member_counting",
            edge_context=None,
            edge_context_json="{}",
            size_usd=10.0,
            sizing_bankroll=1000.0,
            kelly_multiplier_used=0.25,
            execution_fee_rate=0.02,
            applied_validations=[],
            tokens={"token_id": "yes-token", "no_token_id": "no-token"},
        )

    def _snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(
            snapshot_id="snap-1",
            selected_outcome_token_id="yes-token",
            outcome_label="YES",
            orderbook_depth_jsonb=json.dumps(
                {
                    "bids": [{"price": "0.58", "size": "100"}],
                    "asks": [{"price": "0.60", "size": "250"}],
                }
            ),
            min_tick_size="0.01",
            raw_orderbook_hash="raw-orderbook-hash",
            executable_snapshot_hash="executable-snapshot-hash",
        )

    def _install_runtime_stubs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        snapshot: SimpleNamespace,
    ) -> None:
        monkeypatch.setattr("src.state.snapshot_repo.get_snapshot", lambda _conn, _id: snapshot)
        monkeypatch.setattr(
            "src.contracts.executable_market_snapshot_v2.is_fresh",
            lambda *_args, **_kwargs: True,
        )
        monkeypatch.setattr(
            cycle_runtime,
            "_attach_corrected_pricing_authority",
            lambda **_kwargs: {"live_submit_authority": False, "final_execution_intent_id": None},
        )

    def _run_reprice(
        self,
        decision: SimpleNamespace,
        final_intent_context: dict,
    ) -> float | None:
        return cycle_runtime._reprice_decision_from_executable_snapshot(
            None,
            decision,
            {"executable_snapshot_id": "snap-1"},
            final_intent_context,
        )

    def test_flag_off_does_not_call_crossing_decision(self, monkeypatch: pytest.MonkeyPatch):
        """Test 4: ZEUS_TAKER_CROSSING_ENABLED not set → _crossing_decision never called."""
        monkeypatch.delenv("ZEUS_TAKER_CROSSING_ENABLED", raising=False)
        decision = self._build_minimal_decision()
        self._install_runtime_stubs(monkeypatch, self._snapshot())

        with patch("src.engine.evaluator._crossing_decision") as mock_cd:
            self._run_reprice(decision, {"allow_taker_upgrade": False})

        assert mock_cd.call_count == 0

    def test_flag_on_without_intent_gate_does_not_call_crossing_decision(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """ZEUS_TAKER_CROSSING_ENABLED cannot bypass allow_taker_upgrade."""
        monkeypatch.setenv("ZEUS_TAKER_CROSSING_ENABLED", "1")
        decision = self._build_minimal_decision()
        self._install_runtime_stubs(monkeypatch, self._snapshot())

        with patch("src.engine.evaluator._crossing_decision") as mock_cd:
            self._run_reprice(decision, {"allow_taker_upgrade": False})

        assert mock_cd.call_count == 0

    def test_flag_on_with_intent_gate_calls_crossing_decision_with_intended_order_size(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test 5: enabled flag + intent gate calls the real integration site."""
        monkeypatch.setenv("ZEUS_TAKER_CROSSING_ENABLED", "1")
        decision = self._build_minimal_decision()
        self._install_runtime_stubs(monkeypatch, self._snapshot())
        mock_cd = MagicMock(return_value=(False, {"decision": "PASSIVE"}))

        with patch("src.engine.evaluator._crossing_decision", mock_cd):
            self._run_reprice(decision, {"allow_taker_upgrade": True})

        assert mock_cd.call_count == 1
        call_kwargs = mock_cd.call_args.kwargs
        reprice = decision.tokens["executable_snapshot_reprice"]
        assert call_kwargs["best_ask_size"] == pytest.approx(
            reprice["best_ask_size_at_fee_adjusted_cost"]
        )
        assert call_kwargs["best_ask_size"] != pytest.approx(reprice["snapshot_best_ask_size"])
        assert reprice["f34_crossing_evidence"]["orderbook_best_ask_size"] == pytest.approx(250.0)
        assert reprice["f34_crossing_evidence"]["intended_order_size_usd"] == pytest.approx(
            call_kwargs["best_ask_size"]
        )
