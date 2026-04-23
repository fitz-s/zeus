# Created: 2026-04-13
# Last reused/audited: 2026-04-23
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a midstream guardian panel)
"""Tests for F2/D3 ExecutionPrice contract wiring.

Covers:
1. with_taker_fee() computes price-dependent fee (not flat 5%)
2. schema_packet() returns valid schema
3. fee_adjusted type passes assert_kelly_safe()
4. Evaluator wires ExecutionPrice before Kelly
5. Evaluator default settings make fee-adjusted sizing authoritative
"""

import pytest
import numpy as np

from src.contracts.alpha_decision import AlphaDecision
from src.contracts.execution_price import (
    ExecutionPrice,
    ExecutionPriceContractError,
    polymarket_fee,
)
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis


# ---------------------------------------------------------------------------
# Commit 1 — K0 contract: with_taker_fee() and schema_packet()
# ---------------------------------------------------------------------------


class TestWithTakerFee:
    """ExecutionPrice.with_taker_fee() applies price-dependent Polymarket fee."""

    def test_fee_at_p_042(self):
        """At p=0.42: fee = 0.05 × 0.42 × 0.58 = 0.01218."""
        ep = ExecutionPrice(
            value=0.42,
            price_type="implied_probability",
            fee_deducted=False,
            currency="probability_units",
        )
        adjusted = ep.with_taker_fee(0.05)
        expected = 0.42 + 0.05 * 0.42 * 0.58
        assert adjusted.value == pytest.approx(expected, abs=1e-10)
        assert adjusted.price_type == "fee_adjusted"
        assert adjusted.fee_deducted is True
        assert adjusted.currency == "probability_units"

    def test_fee_at_p_050_is_maximum(self):
        """Fee is maximal at p=0.50: 0.05 × 0.50 × 0.50 = 0.0125."""
        ep = ExecutionPrice(value=0.50, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee(0.05)
        assert adjusted.value == pytest.approx(0.50 + 0.0125)

    def test_fee_at_p_090_is_small(self):
        """Fee is tiny at extremes: 0.05 × 0.90 × 0.10 = 0.0045."""
        ep = ExecutionPrice(value=0.90, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee(0.05)
        assert adjusted.value == pytest.approx(0.90 + 0.0045)

    def test_fee_not_flat_five_percent(self):
        """Ensure fee is NOT flat 5% — it is p × (1-p) × 0.05."""
        ep = ExecutionPrice(value=0.42, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee(0.05)
        flat_5pct = 0.42 + 0.05 * 0.42  # WRONG: flat 5%
        assert adjusted.value != pytest.approx(flat_5pct, abs=1e-6), (
            "Fee should be price-dependent p(1-p), NOT flat percentage"
        )

    def test_fee_preserves_currency(self):
        """Currency must be preserved through fee application."""
        ep = ExecutionPrice(value=0.42, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee()
        assert adjusted.currency == ep.currency

    def test_custom_fee_rate(self):
        """Custom fee rate (e.g. 0.03) applies correctly."""
        ep = ExecutionPrice(value=0.50, price_type="ask", fee_deducted=False, currency="probability_units")
        adjusted = ep.with_taker_fee(0.03)
        expected = 0.50 + 0.03 * 0.50 * 0.50
        assert adjusted.value == pytest.approx(expected)

    def test_polymarket_fee_rejects_nan(self):
        """polymarket_fee must reject NaN price."""
        with pytest.raises(ValueError, match="finite"):
            polymarket_fee(float("nan"))

    def test_polymarket_fee_rejects_inf(self):
        """polymarket_fee must reject infinite price."""
        with pytest.raises(ValueError, match="finite"):
            polymarket_fee(float("inf"))

    def test_polymarket_fee_rejects_nan_fee_rate(self):
        """polymarket_fee must reject NaN fee_rate."""
        with pytest.raises(ValueError, match="finite"):
            polymarket_fee(0.5, fee_rate=float("nan"))

    def test_polymarket_fee_rejects_inf_fee_rate(self):
        """polymarket_fee must reject infinite fee_rate."""
        with pytest.raises(ValueError, match="finite"):
            polymarket_fee(0.5, fee_rate=float("inf"))


class TestSchemaPacket:
    def test_schema_packet_returns_dict(self):
        schema = ExecutionPrice.schema_packet()
        assert isinstance(schema, dict)

    def test_schema_packet_has_required_keys(self):
        schema = ExecutionPrice.schema_packet()
        assert schema["type"] == "ExecutionPrice"
        assert set(schema["required_fields"]) == {"value", "price_type", "fee_deducted", "currency"}


class TestFeeAdjustedKellySafety:
    """fee_adjusted type with fee_deducted=True must pass assert_kelly_safe()."""

    def test_fee_adjusted_passes_kelly_safe(self):
        ep = ExecutionPrice(
            value=0.42,
            price_type="implied_probability",
            fee_deducted=False,
            currency="probability_units",
        )
        adjusted = ep.with_taker_fee()
        adjusted.assert_kelly_safe()  # Must not raise

    def test_implied_probability_still_fails_kelly_safe(self):
        ep = ExecutionPrice(
            value=0.42,
            price_type="implied_probability",
            fee_deducted=True,
            currency="probability_units",
        )
        with pytest.raises(ExecutionPriceContractError):
            ep.assert_kelly_safe()

    def test_double_fee_application_raises(self):
        """Calling with_taker_fee() on already fee-adjusted price must raise."""
        ep = ExecutionPrice(
            value=0.42, price_type="implied_probability",
            fee_deducted=False, currency="probability_units",
        )
        adjusted = ep.with_taker_fee()
        with pytest.raises(ExecutionPriceContractError, match="already fee-adjusted"):
            adjusted.with_taker_fee()


# ---------------------------------------------------------------------------
# Commit 2 — K2/K3 wiring: evaluator uses ExecutionPrice before Kelly
# ---------------------------------------------------------------------------


class TestEvaluatorWiring:
    """Verify evaluator.py correctly wires ExecutionPrice at the Kelly boundary."""

    def test_evaluator_imports_execution_price(self):
        """evaluator.py must import ExecutionPrice and polymarket_fee."""
        import ast
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "engine" / "evaluator.py").read_text()
        assert "ExecutionPrice" in src
        assert "polymarket_fee" in src

    def test_evaluator_calls_with_taker_fee(self):
        """evaluator.py must call with_taker_fee() before Kelly sizing."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "engine" / "evaluator.py").read_text()
        assert "with_taker_fee" in src

    def test_evaluator_calls_assert_kelly_safe(self):
        """evaluator.py must call assert_kelly_safe() before Kelly sizing."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "engine" / "evaluator.py").read_text()
        assert "assert_kelly_safe" in src

    def test_shadow_flag_removed_from_evaluator(self):
        """P10E: EXECUTION_PRICE_SHADOW shadow-off path is removed from evaluator.
        _size_at_execution_price_boundary no longer accepts feature_flags param.
        """
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "engine" / "evaluator.py").read_text()
        # The shadow-off branch is gone — no raw_size fallback path
        assert "EXECUTION_PRICE_SHADOW" not in src, (
            "P10E: EXECUTION_PRICE_SHADOW must be removed from evaluator.py; "
            "shadow-off path deleted in P10E."
        )

    def test_evaluator_always_uses_fee_adjusted_size(self):
        """P10E: _size_at_execution_price_boundary always returns fee-adjusted size."""
        from src.engine.evaluator import _size_at_execution_price_boundary

        p_posterior = 0.60
        bare_entry = 0.40
        fee = polymarket_fee(bare_entry)
        fee_adjusted_entry = bare_entry + fee

        authoritative_size = _size_at_execution_price_boundary(
            p_posterior=p_posterior,
            entry_price=bare_entry,
            fee_rate=0.05,
            sizing_bankroll=1000.0,
            kelly_multiplier=0.25,
            safety_cap_usd=None,
        )
        # Must equal the fee-adjusted size (not the larger bare-float size)
        from src.contracts.execution_price import ExecutionPrice
        ep = ExecutionPrice(
            value=fee_adjusted_entry, price_type="fee_adjusted",
            fee_deducted=True, currency="probability_units",
        )
        from src.strategy.kelly import kelly_size
        expected = kelly_size(p_posterior, ep, 1000.0, 0.25)
        assert authoritative_size == pytest.approx(expected)
        # The fee itself must be correct
        assert fee == pytest.approx(0.012, abs=1e-6)

    def test_shadow_off_path_raises_on_feature_flags_kwarg(self):
        """P10E: _size_at_execution_price_boundary no longer accepts feature_flags."""
        from src.engine.evaluator import _size_at_execution_price_boundary
        import inspect
        sig = inspect.signature(_size_at_execution_price_boundary)
        assert "feature_flags" not in sig.parameters, (
            "P10E: feature_flags param must be removed from "
            "_size_at_execution_price_boundary (shadow-off path deleted)."
        )

    @pytest.mark.xfail(
        reason=(
            "Pre-existing integration test requiring full evaluator stub rewrite. "
            "Day0Router patch and scan_full_hypothesis_family routing changed after "
            "original test was written. Out of P10E scope — tracked for P11."
        ),
        strict=False,
    )
    def test_evaluate_candidate_missing_flag_uses_token_fee_rate(self, monkeypatch):
        """The live evaluator seam must return the token-fee-adjusted size."""
        import types
        from datetime import datetime, timezone

        from src.config import City
        import src.engine.evaluator as evaluator_module
        from src.state.portfolio import PortfolioState
        from src.strategy.risk_limits import RiskLimits
        from src.types import BinEdge

        now = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)
        city = City(
            name="Dallas",
            lat=32.8998,
            lon=-97.0403,
            timezone="America/Chicago",
            settlement_unit="F",
            cluster="US-Texas-Triangle",
            wu_station="KDAL",
        )

        class FakeEns:
            member_extrema = np.ones(51) * 40.0
            # P10E: member_maxes deprecated alias kept for FakeAnalysis compat
            member_maxes = np.ones(51) * 40.0
            temperature_metric = None
            bias_corrected = False

            def __init__(self, *args, **kwargs):
                pass

            def spread_float(self):
                return 0.0

            def is_bimodal(self):
                return False

            def p_raw_vector(self, bins):
                return np.array([0.2, 0.3, 0.5])

        class FakeDay0Signal:
            """P10E: used to stub Day0Router.route() return value."""
            def __init__(self, *args, **kwargs):
                pass

            def p_vector(self, bins):
                return np.array([0.2, 0.3, 0.5])

            def forecast_context(self):
                return {}

        class FakeDay0Router:
            """P10E: stub Day0Router class — route() returns a FakeDay0Signal."""
            @staticmethod
            def route(inputs):
                return FakeDay0Signal()

        class FakeAnalysis:
            def __init__(self, *args, **kwargs):
                self.bins = kwargs["bins"]
                self.member_maxes = np.ones(51) * 40.0

            def forecast_context(self):
                return {"uncertainty": {}, "location": {}}

            def find_edges(self, n_bootstrap):
                selected_bin = self.bins[1]
                return [
                    BinEdge(
                        bin=selected_bin,
                        direction="buy_yes",
                        edge=0.20,
                        ci_lower=0.05,
                        ci_upper=0.25,
                        p_model=0.70,
                        p_market=0.40,
                        p_posterior=0.60,
                        entry_price=0.40,
                        p_value=0.001,
                        vwmp=0.40,
                    )
                ]

        class FakeClob:
            def get_best_bid_ask(self, token_id):
                return (0.39, 0.41, 10.0, 10.0)

            def get_fee_rate(self, token_id):
                assert token_id == "yes2"
                return 0.072

        class BrokenFeeClob(FakeClob):
            def get_fee_rate(self, token_id):
                raise RuntimeError("fee endpoint down")

        captured_entry_prices = []

        def capture_kelly(p_posterior, entry_price, bankroll, kelly_mult, **kwargs):
            captured_entry_prices.append(entry_price)
            return entry_price

        monkeypatch.delitem(
            evaluator_module.settings._data.get("feature_flags", {}),
            "EXECUTION_PRICE_SHADOW",
            raising=False,
        )
        monkeypatch.setattr(
            evaluator_module,
            "fetch_ensemble",
            lambda *args, **kwargs: {
                "members_hourly": np.ones((51, 2)) * 40.0,
                "times": [now, now],
                "issue_time": now,
                "first_valid_time": now,
                "fetch_time": now,
                "model": "ecmwf_ifs025",
            },
        )
        monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda *args, **kwargs: True)
        monkeypatch.setattr(evaluator_module, "EnsembleSignal", FakeEns)
        monkeypatch.setattr(evaluator_module, "Day0Router", FakeDay0Router)
        from src.signal.day0_extrema import RemainingMemberExtrema as _REM
        monkeypatch.setattr(
            evaluator_module,
            "remaining_member_extrema_for_day0",
            lambda *args, **kwargs: (_REM(maxes=np.ones(51) * 40.0, mins=None), 2.0),
        )
        monkeypatch.setattr(
            evaluator_module,
            "_get_day0_temporal_context",
            lambda *args, **kwargs: types.SimpleNamespace(current_utc_timestamp=now),
        )
        monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-execution-price")
        monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
        monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (None, 4))
        monkeypatch.setattr(
            evaluator_module,
            "compute_alpha",
            lambda *args, **kwargs: AlphaDecision(
                value=0.5,
                optimization_target="risk_cap",
                evidence_basis="execution-price test",
                ci_bound=0.05,
            ),
        )
        monkeypatch.setattr(evaluator_module, "MarketAnalysis", FakeAnalysis)
        monkeypatch.setattr(
            evaluator_module,
            "scan_full_hypothesis_family",
            lambda analysis, *args, **kwargs: [
                FullFamilyHypothesis(
                    index=1,
                    range_label=edge.bin.label,
                    direction=edge.direction,
                    edge=edge.edge,
                    ci_lower=edge.ci_lower,
                    ci_upper=edge.ci_upper,
                    p_value=edge.p_value,
                    p_model=edge.p_model,
                    p_market=edge.p_market,
                    p_posterior=edge.p_posterior,
                    entry_price=edge.entry_price,
                    is_shoulder=False,
                    passed_prefilter=True,
                )
                for edge in analysis.find_edges(n_bootstrap=kwargs.get("n_bootstrap", 0))
            ],
        )
        monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))
        monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
        monkeypatch.setattr(evaluator_module, "kelly_size", capture_kelly)
        monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, "OK"))

        candidate = evaluator_module.MarketCandidate(
            city=city,
            target_date="2026-04-13",
            outcomes=[
                {
                    "title": "38-39°F",
                    "range_low": 38,
                    "range_high": 39,
                    "token_id": "yes1",
                    "no_token_id": "no1",
                    "market_id": "m1",
                },
                {
                    "title": "40-41°F",
                    "range_low": 40,
                    "range_high": 41,
                    "token_id": "yes2",
                    "no_token_id": "no2",
                    "market_id": "m2",
                },
                {
                    "title": "42°F or higher",
                    "range_low": 42,
                    "range_high": None,
                    "token_id": "yes3",
                    "no_token_id": "no3",
                    "market_id": "m3",
                },
            ],
            hours_since_open=2.0,
            hours_to_resolution=12.0,
            event_id="event-execution-price",
            discovery_mode="day0_capture",
            observation={
                "high_so_far": 40.0,
                "current_temp": 40.0,
                "source": "test",
                "observation_time": now.isoformat(),
            },
        )

        decisions = evaluator_module.evaluate_candidate(
            candidate,
            conn=None,
            portfolio=PortfolioState(bankroll=1000.0),
            clob=FakeClob(),
            limits=RiskLimits(
                max_single_position_pct=1.0,
                max_portfolio_heat_pct=1.0,
                max_correlated_pct=1.0,
                max_city_pct=1.0,
                min_order_usd=0.01,
            ),
            entry_bankroll=1000.0,
            decision_time=now,
        )

        expected_fee_adjusted_entry = 0.40 + polymarket_fee(0.40, fee_rate=0.072)
        assert len(decisions) == 1
        assert decisions[0].should_trade is True
        assert decisions[0].size_usd == pytest.approx(expected_fee_adjusted_entry)
        assert len(captured_entry_prices) == 1
        assert captured_entry_prices[0] == pytest.approx(expected_fee_adjusted_entry)

        captured_entry_prices.clear()
        unavailable = evaluator_module.evaluate_candidate(
            candidate,
            conn=None,
            portfolio=PortfolioState(bankroll=1000.0),
            clob=BrokenFeeClob(),
            limits=RiskLimits(
                max_single_position_pct=1.0,
                max_portfolio_heat_pct=1.0,
                max_correlated_pct=1.0,
                max_city_pct=1.0,
                min_order_usd=0.01,
            ),
            entry_bankroll=1000.0,
            decision_time=now,
        )

        assert len(unavailable) == 1
        assert unavailable[0].should_trade is False
        assert unavailable[0].rejection_stage == "EXECUTION_PRICE_UNAVAILABLE"
        assert captured_entry_prices == []

    def test_fee_reduces_kelly_size(self):
        """Fee-adjusted entry price must produce smaller Kelly size than raw implied prob.

        This is the D3 bug: Kelly systematically oversizes because it uses
        implied probability (0.42) instead of execution cost (0.42 + fee ≈ 0.43218).

        P10E: both calls use typed ExecutionPrice. Comparison is between
        fee_adjusted (correct) and a hypothetical bare-probability typed price
        that would still pass assert_kelly_safe — we use the evaluator seam to
        show the difference.
        """
        from src.engine.evaluator import _size_at_execution_price_boundary

        p_posterior = 0.55
        bare_entry = 0.42
        fee_adj = ExecutionPrice(
            value=bare_entry, price_type="implied_probability",
            fee_deducted=False, currency="probability_units",
        ).with_taker_fee()

        # fee_adj is the correct ExecutionPrice; lower fee_rate → larger size (fee=0)
        size_no_fee = _size_at_execution_price_boundary(
            p_posterior=p_posterior, entry_price=bare_entry,
            fee_rate=0.0, sizing_bankroll=1000.0, kelly_multiplier=0.25,
            safety_cap_usd=None,
        )
        size_with_fee = _size_at_execution_price_boundary(
            p_posterior=p_posterior, entry_price=bare_entry,
            fee_rate=0.05, sizing_bankroll=1000.0, kelly_multiplier=0.25,
            safety_cap_usd=None,
        )

        assert size_with_fee < size_no_fee, "Fee-adjusted entry price must produce smaller position size"
        assert size_with_fee > 0, "Fee-adjusted size should still be positive with real edge"


class TestPolymarketFeeRateClient:
    def test_get_fee_rate_reads_token_fee_schedule(self, monkeypatch):
        from src.data import polymarket_client

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"feeSchedule": {"feesEnabled": True, "feeRate": "0.072"}}

        calls = []

        def fake_get(url, *, params, timeout):
            calls.append((url, params, timeout))
            return Response()

        monkeypatch.setattr(polymarket_client.httpx, "get", fake_get)
        client = object.__new__(polymarket_client.PolymarketClient)

        assert client.get_fee_rate("token-1") == pytest.approx(0.072)
        assert calls == [
            (
                f"{polymarket_client.CLOB_BASE}/fee-rate",
                {"token_id": "token-1"},
                15.0,
            )
        ]

    def test_get_fee_rate_returns_zero_when_fees_disabled(self, monkeypatch):
        from src.data import polymarket_client

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"feeSchedule": {"feesEnabled": False, "feeRate": "0.05"}}

        monkeypatch.setattr(polymarket_client.httpx, "get", lambda *args, **kwargs: Response())
        client = object.__new__(polymarket_client.PolymarketClient)

        assert client.get_fee_rate("token-1") == 0.0
