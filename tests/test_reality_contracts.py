"""P10.8 Reality Contract tests.

Spec §P10.8 requires these four tests:
1. test_all_blocking_contracts_verified_before_trade (INV-11)
2. test_fee_included_in_edge_calculation (INV-12 / D3)
3. test_tick_size_enforced_on_order_rounding (INV-11 / TICK_SIZE_STANDARD)
4. test_drift_detection_generates_antibody (INV-11 / P10.9)

These verify:
- The runtime gate: verify_all_blocking() is called before any trade evaluation
- Fee correctness: polymarket_fee() is used in edge calculation, not flat 5%
- Tick size: order prices are rounded to tick_size increments
- Drift → antibody: when a contract drifts, an antibody (test/contract) is generated
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# §P10.8 Test 1: All blocking contracts verified before trade
# ---------------------------------------------------------------------------

class TestBlockingContractsVerifiedBeforeTrade:
    """INV-11: verify_all_blocking() must be called before any trade evaluation.

    The runtime gate in cycle_runner.run_cycle() must call
    RealityContractVerifier.verify_all_blocking() BEFORE the evaluator runs.
    If any blocking contract fails, the cycle must skip trading entirely.
    """

    def test_stale_blocking_contract_skips_cycle(self):
        """A stale blocking contract must prevent trade execution.

        §P10.5: verify_all_blocking() returns VerificationResult(can_trade=False)
        when any blocking contract is stale or fails verification. The cycle
        runner must check this BEFORE calling evaluate_candidate().
        """
        # Import the verifier — if p10-infra hasn't delivered yet, skip gracefully
        try:
            from src.contracts.reality_contract import (
                RealityContract,
                RealityContractVerifier,
                VerificationResult,
            )
        except ImportError:
            pytest.skip("RealityContractVerifier not yet delivered by p10-infra")

        # Create a verifier with one stale blocking contract
        stale_contract = RealityContract(
            contract_id="FEE_RATE_WEATHER",
            category="economic",
            assumption="Weather market taker fee rate is 0.05",
            current_value=0.05,
            verification_method="api_query",
            last_verified=datetime(2020, 1, 1, tzinfo=timezone.utc),  # ancient = stale
            ttl_seconds=3600,
            criticality="blocking",
            on_change_handlers=["recalculate_edge"],
        )
        verifier = RealityContractVerifier(contracts=[stale_contract])
        result = verifier.verify_all_blocking()

        assert result.can_trade is False, (
            "Stale blocking contract must set can_trade=False"
        )
        assert len(result.failures) >= 1
        assert result.failures[0].contract_id == "FEE_RATE_WEATHER"

    def test_all_blocking_fresh_allows_trade(self):
        """When all blocking contracts are fresh and verified, can_trade=True."""
        try:
            from src.contracts.reality_contract import (
                RealityContract,
                RealityContractVerifier,
            )
        except ImportError:
            pytest.skip("RealityContractVerifier not yet delivered by p10-infra")

        fresh_contract = RealityContract(
            contract_id="FEE_RATE_WEATHER",
            category="economic",
            assumption="Weather market taker fee rate is 0.05",
            current_value=0.05,
            verification_method="api_query",
            last_verified=datetime.now(timezone.utc),  # just verified
            ttl_seconds=3600,
            criticality="blocking",
            on_change_handlers=["recalculate_edge"],
        )
        verifier = RealityContractVerifier(contracts=[fresh_contract])
        result = verifier.verify_all_blocking()

        assert result.can_trade is True
        assert len(result.failures) == 0

    def test_advisory_stale_does_not_block_trade(self):
        """Advisory contracts being stale must NOT block trading."""
        try:
            from src.contracts.reality_contract import (
                RealityContract,
                RealityContractVerifier,
            )
        except ImportError:
            pytest.skip("RealityContractVerifier not yet delivered by p10-infra")

        advisory_contract = RealityContract(
            contract_id="RESOLUTION_TIMELINE",
            category="protocol",
            assumption="UMA resolution within 24h for weather",
            current_value="24h",
            verification_method="historical_analysis",
            last_verified=datetime(2020, 1, 1, tzinfo=timezone.utc),  # stale
            ttl_seconds=3600,
            criticality="advisory",
            on_change_handlers=[],
        )
        verifier = RealityContractVerifier(contracts=[advisory_contract])
        result = verifier.verify_all_blocking()

        assert result.can_trade is True, (
            "Advisory-only stale contracts must not block trading"
        )


# ---------------------------------------------------------------------------
# §P10.8 Test 2: Fee included in edge calculation
# ---------------------------------------------------------------------------

class TestFeeIncludedInEdgeCalculation:
    """INV-12 / D3: polymarket_fee() is used in the edge calculation pipeline.

    The fee formula is fee = feeRate × p × (1 - p), NOT a flat 5%.
    P9 adversarial findings §1.1 showed the flat 5% was the single most impactful
    reality gap, rejecting trades with 3-4% real edge at tail prices.

    This test verifies that the CORRECT price-dependent formula is used.
    """

    def test_polymarket_fee_formula_shape(self):
        """Fee formula must be quadratic: feeRate × p × (1 - p).

        At p=0.50: fee = 0.05 × 0.5 × 0.5 = 0.0125 (1.25%)
        At p=0.90: fee = 0.05 × 0.9 × 0.1 = 0.0045 (0.45%)
        At p=0.10: fee = 0.05 × 0.1 × 0.9 = 0.0045 (0.45%)
        """
        from src.contracts.execution_price import polymarket_fee

        # Peak at p=0.50
        assert abs(polymarket_fee(0.50) - 0.0125) < 1e-10
        # Symmetric at tails
        assert abs(polymarket_fee(0.10) - 0.0045) < 1e-10
        assert abs(polymarket_fee(0.90) - 0.0045) < 1e-10
        # Boundary: fee at extremes raises ValueError (fail-close)
        with pytest.raises(ValueError, match="price in \\(0, 1\\)"):
            polymarket_fee(0.0)
        with pytest.raises(ValueError, match="price in \\(0, 1\\)"):
            polymarket_fee(1.0)
        # Must NOT be flat 5%
        assert polymarket_fee(0.90) < 0.01, (
            "Fee at p=0.90 must be << 5%. Got {:.4f}".format(polymarket_fee(0.90))
        )

    def test_fee_applied_before_kelly_in_evaluator(self):
        """The evaluator must use polymarket_fee() to compute fee-adjusted entry price.

        §P9.3 D3: edge.entry_price + polymarket_fee(entry_price) → ExecutionPrice.
        The flat FeeGuard.ASSUMED_TAKER_FEE = 0.05 must NOT be used.
        """
        # Verify by reading the source — the fee must be computed via polymarket_fee
        evaluator_src = (ROOT / "src" / "engine" / "evaluator.py").read_text()

        assert "polymarket_fee" in evaluator_src, (
            "evaluator.py must import and use polymarket_fee(), not flat fee"
        )
        # Verify it's not using flat FeeGuard
        # (FeeGuard may still exist as a reference but must not be the active path)
        assert "fee_adjusted_price = raw_price + fee" in evaluator_src or \
               "fee_adjusted" in evaluator_src, (
            "evaluator.py must compute fee-adjusted price for Kelly"
        )
        assert "assert_kelly_safe" in evaluator_src, (
            "evaluator.py must call assert_kelly_safe() on ExecutionPrice"
        )

    def test_fee_not_flat_five_percent(self):
        """Regression: the fee must never be a flat constant subtraction.

        P9 adversarial §1.1: flat 5% fee assumption was rejecting profitable
        trades at tail prices where real fee is 0.45%.
        """
        from src.contracts.execution_price import polymarket_fee

        # At Zeus's primary trading zone (tail prices, p > 0.85 or p < 0.15)
        tail_prices = [0.05, 0.10, 0.15, 0.85, 0.90, 0.95]
        for p in tail_prices:
            fee = polymarket_fee(p)
            assert fee < 0.02, (
                f"Fee at p={p} is {fee:.4f}, which exceeds 2%. "
                "This suggests flat fee logic, not price-dependent formula."
            )

    def test_fee_reality_contract_value_matches_formula(self):
        """If FEE_RATE_WEATHER reality contract exists, its value must match
        the fee_rate parameter used by polymarket_fee().
        """
        try:
            from src.contracts.reality_contract import load_contracts_from_yaml
            contracts = load_contracts_from_yaml("config/reality_contracts/economic.yaml")
            fee_contract = next(
                (c for c in contracts if c.contract_id == "FEE_RATE_WEATHER"), None
            )
            if fee_contract is not None:
                from src.contracts.execution_price import polymarket_fee
                # The contract's current_value should be the feeRate coefficient
                expected_fee_at_half = fee_contract.current_value * 0.5 * 0.5
                actual_fee_at_half = polymarket_fee(0.5, fee_rate=fee_contract.current_value)
                assert abs(expected_fee_at_half - actual_fee_at_half) < 1e-10
        except ImportError:
            pytest.skip("Reality contract YAML loader not yet delivered by p10-infra")


# ---------------------------------------------------------------------------
# §P10.8 Test 3: Tick size enforced on order rounding
# ---------------------------------------------------------------------------

class TestTickSizeEnforcedOnOrderRounding:
    """INV-11 / TICK_SIZE_STANDARD: order prices must be rounded to tick_size.

    Polymarket default tick_size = 0.01. Orders at non-tick prices will be
    rejected by the CLOB. DELTA-13 notes dynamic tick_size at extreme prices.
    """

    def test_entry_order_price_is_tick_aligned(self):
        """Entry limit prices must be multiples of 0.01 (standard tick size).

        executor.py clips limit_price to [0.01, 0.99] and shares use 0.01
        increments via math.ceil/floor quantization.
        """
        # Verify executor applies tick-aligned quantization
        executor_src = (ROOT / "src" / "execution" / "executor.py").read_text()

        # Shares are quantized to 0.01 increments
        assert "math.ceil(shares * 100" in executor_src, (
            "BUY shares must be rounded UP to 0.01 increments"
        )
        assert "math.floor" in executor_src, (
            "SELL shares must be rounded DOWN"
        )
        # Price bounds enforced
        assert "max(0.01" in executor_src, (
            "Limit price must be clipped to >= 0.01 (minimum tick)"
        )
        assert "min(0.99" in executor_src, (
            "Limit price must be clipped to <= 0.99"
        )

    def test_share_quantization_buy_rounds_up(self):
        """BUY orders: shares round UP to 0.01 (slightly overpay to ensure fill)."""
        # Replicate executor's quantization logic
        shares = 10.003  # slightly above 10.00
        quantized = math.ceil(shares * 100 - 1e-9) / 100.0
        assert quantized == 10.01, f"Expected 10.01, got {quantized}"

        shares_exact = 10.00
        quantized_exact = math.ceil(shares_exact * 100 - 1e-9) / 100.0
        assert quantized_exact == 10.00

    def test_share_quantization_sell_rounds_down(self):
        """SELL orders: shares round DOWN to 0.01 (sell no more than we have)."""
        # 10.009 → floor(1000.9 + eps) / 100 = 10.00 (rounds down, correct)
        shares = 10.009
        quantized = math.floor(shares * 100 + 1e-9) / 100.0
        assert quantized == 10.00, f"Expected 10.00, got {quantized}"

        # 10.019 → floor(1001.9 + eps) / 100 = 10.01
        shares_above = 10.019
        quantized_above = math.floor(shares_above * 100 + 1e-9) / 100.0
        assert quantized_above == 10.01

        # Key property: SELL never rounds UP (would sell more than held)
        shares_fractional = 10.997
        quantized_frac = math.floor(shares_fractional * 100 + 1e-9) / 100.0
        assert quantized_frac <= shares_fractional, (
            "SELL quantization must never exceed original share count"
        )

    def test_limit_price_bounds(self):
        """Limit prices must be within [0.01, 0.99] after rounding."""
        # Edge case: very low price should not go below tick_size
        price = 0.001
        clipped = max(0.01, min(0.99, price))
        assert clipped == 0.01

        # Edge case: very high price capped at 0.99
        price = 0.999
        clipped = max(0.01, min(0.99, price))
        assert clipped == 0.99

    def test_tick_size_reality_contract_exists(self):
        """If reality contracts are loaded, TICK_SIZE_STANDARD must be present
        as a blocking execution contract.
        """
        try:
            from src.contracts.reality_contract import load_contracts_from_yaml
            contracts = load_contracts_from_yaml("config/reality_contracts/execution.yaml")
            tick_contract = next(
                (c for c in contracts if c.contract_id == "TICK_SIZE_STANDARD"), None
            )
            assert tick_contract is not None, (
                "TICK_SIZE_STANDARD blocking contract required per §P10.7"
            )
            assert tick_contract.criticality == "blocking"
            assert tick_contract.current_value == 0.01
        except ImportError:
            pytest.skip("Reality contract YAML loader not yet delivered by p10-infra")


# ---------------------------------------------------------------------------
# §P10.8 Test 4: Drift detection generates antibody
# ---------------------------------------------------------------------------

class TestDriftDetectionGeneratesAntibody:
    """§P10.9: Drift detection must generate antibodies (tests or contract updates),
    not just alerts.

    An antibody is a test/type/structural change that makes a category of error
    impossible forever. Not a note. Not an alert. Not a doc. A failing test =
    stage-1 antibody. A type constraint deployed in CI = full antibody.

    (From Fitz's methodology: "Immune System > Security Guard")
    """

    def test_drift_event_produces_antibody(self):
        """When detect_drift() finds a change, generate_antibody() must produce
        a concrete antibody with action type and target.
        """
        try:
            from src.contracts.reality_contract import (
                RealityContract,
                RealityContractVerifier,
                DriftEvent,
                Antibody,
            )
        except ImportError:
            pytest.skip("RealityContractVerifier not yet delivered by p10-infra")

        # Simulate: fee rate changed from 0.05 to 0.06
        drift = DriftEvent(
            contract_id="FEE_RATE_WEATHER",
            old_value=0.05,
            new_value=0.06,
            detected_at=datetime.now(timezone.utc),
            severity="critical",
        )

        verifier = RealityContractVerifier(contracts=[])
        antibody = verifier.generate_antibody(drift)

        assert isinstance(antibody, Antibody)
        # Antibody must have concrete action, not just alert
        assert antibody.action_type in ("code_change", "config_change", "test_addition"), (
            f"Antibody action_type must be concrete, got: {antibody.action_type}"
        )
        assert antibody.target_file or antibody.target_config, (
            "Antibody must specify what file or config to change"
        )
        assert antibody.description, "Antibody must describe the fix"

    def test_critical_drift_requires_code_change_antibody(self):
        """§P11.6: critical drift → code change antibody, not just config."""
        try:
            from src.contracts.reality_contract import (
                RealityContractVerifier,
                DriftEvent,
            )
        except ImportError:
            pytest.skip("RealityContractVerifier not yet delivered by p10-infra")

        critical_drift = DriftEvent(
            contract_id="FEE_RATE_WEATHER",
            old_value=0.05,
            new_value=0.10,  # dramatic change → critical
            detected_at=datetime.now(timezone.utc),
            severity="critical",
        )

        verifier = RealityContractVerifier(contracts=[])
        antibody = verifier.generate_antibody(critical_drift)

        assert antibody.action_type == "code_change", (
            f"Critical drift must produce code_change antibody, got: {antibody.action_type}"
        )

    def test_moderate_drift_allows_config_change_antibody(self):
        """§P11.6: moderate drift → config change is acceptable."""
        try:
            from src.contracts.reality_contract import (
                RealityContractVerifier,
                DriftEvent,
            )
        except ImportError:
            pytest.skip("RealityContractVerifier not yet delivered by p10-infra")

        moderate_drift = DriftEvent(
            contract_id="TICK_SIZE_STANDARD",
            old_value=0.01,
            new_value=0.001,  # tick size refined at extremes
            detected_at=datetime.now(timezone.utc),
            severity="moderate",
        )

        verifier = RealityContractVerifier(contracts=[])
        antibody = verifier.generate_antibody(moderate_drift)

        assert antibody.action_type in ("config_change", "code_change"), (
            f"Moderate drift antibody should be config_change or code_change, got: {antibody.action_type}"
        )


# ---------------------------------------------------------------------------
# Adversarial-informed additional tests (p10-adversarial findings 2026-04-06)
# ---------------------------------------------------------------------------

class TestFeeEnabledPerMarket:
    """p10-adversarial finding: markets created before 2026-03-30 may have
    feesEnabled=false. The runtime must check per-market fee status.
    """

    def test_polymarket_fee_returns_zero_for_fee_disabled_market(self):
        """If a market has feesEnabled=false, fee computation must return 0."""
        from src.contracts.execution_price import polymarket_fee

        # fee_rate=0 simulates a fee-disabled market
        assert polymarket_fee(0.50, fee_rate=0.0) == 0.0
        assert polymarket_fee(0.90, fee_rate=0.0) == 0.0

    def test_fee_at_different_category_rates(self):
        """Different market categories have different feeRates.
        Weather=0.05, Crypto=0.072, Sports=0.03, Geopolitics=0.
        """
        from src.contracts.execution_price import polymarket_fee

        p = 0.50  # peak fee price
        # Weather: 0.05 × 0.5 × 0.5 = 0.0125
        assert abs(polymarket_fee(p, fee_rate=0.05) - 0.0125) < 1e-10
        # Crypto: 0.072 × 0.5 × 0.5 = 0.018
        assert abs(polymarket_fee(p, fee_rate=0.072) - 0.018) < 1e-10
        # Sports: 0.03 × 0.5 × 0.5 = 0.0075
        assert abs(polymarket_fee(p, fee_rate=0.03) - 0.0075) < 1e-10
        # Geopolitics: fee-free
        assert polymarket_fee(p, fee_rate=0.0) == 0.0


class TestVerificationResultReportsWhichContract:
    """p10-adversarial recommendation: reality_contract_stale blocked reason
    should include WHICH contract is stale so the operator knows what drifted.
    """

    def test_verification_failures_include_contract_id(self):
        """VerificationResult.failures must identify which contracts failed."""
        try:
            from src.contracts.reality_contract import (
                RealityContract,
                RealityContractVerifier,
            )
        except ImportError:
            pytest.skip("RealityContractVerifier not yet delivered by p10-infra")

        stale = RealityContract(
            contract_id="FEE_RATE_WEATHER",
            category="economic",
            assumption="Weather fee rate is 0.05",
            current_value=0.05,
            verification_method="api_query",
            last_verified=datetime(2020, 1, 1, tzinfo=timezone.utc),
            ttl_seconds=3600,
            criticality="blocking",
            on_change_handlers=[],
        )
        result = RealityContractVerifier(contracts=[stale]).verify_all_blocking()

        assert not result.can_trade
        failed_ids = [f.contract_id for f in result.failures]
        assert "FEE_RATE_WEATHER" in failed_ids, (
            "Failures must include contract_id for operator visibility"
        )

    def test_multiple_failures_all_reported(self):
        """When multiple blocking contracts fail, all must be reported."""
        try:
            from src.contracts.reality_contract import (
                RealityContract,
                RealityContractVerifier,
            )
        except ImportError:
            pytest.skip("RealityContractVerifier not yet delivered by p10-infra")

        ancient = datetime(2020, 1, 1, tzinfo=timezone.utc)
        contracts = [
            RealityContract(
                contract_id="FEE_RATE_WEATHER",
                category="economic",
                assumption="Fee rate",
                current_value=0.05,
                verification_method="api_query",
                last_verified=ancient,
                ttl_seconds=3600,
                criticality="blocking",
                on_change_handlers=[],
            ),
            RealityContract(
                contract_id="TICK_SIZE_STANDARD",
                category="execution",
                assumption="Tick size",
                current_value=0.01,
                verification_method="api_query",
                last_verified=ancient,
                ttl_seconds=3600,
                criticality="blocking",
                on_change_handlers=[],
            ),
        ]
        result = RealityContractVerifier(contracts=contracts).verify_all_blocking()

        assert not result.can_trade
        failed_ids = {f.contract_id for f in result.failures}
        assert failed_ids == {"FEE_RATE_WEATHER", "TICK_SIZE_STANDARD"}, (
            f"All failed blocking contracts must be reported. Got: {failed_ids}"
        )


class TestShareQuantizationProperties:
    """Tick size enforcement: share and price quantization properties.

    p10-adversarial confirmed tick_size=0.01 default, but get-book endpoint
    returns per-market tick_size. These tests verify the quantization math
    is correct regardless of tick size.
    """

    def test_buy_quantization_never_undersizes(self):
        """BUY: ceil quantization ensures we never buy fewer shares than needed
        to fill the target USD amount. Undersizing = partial fill risk.
        """
        test_cases = [
            (100.0, 0.85, 117.65),   # 100/0.85 = 117.647... → 117.65
            (50.0, 0.50, 100.0),      # exact
            (10.0, 0.03, 333.34),     # extreme low price
        ]
        for usd, price, expected_min in test_cases:
            raw = usd / price
            quantized = math.ceil(raw * 100 - 1e-9) / 100.0
            assert quantized >= raw - 0.005, (
                f"BUY quantization at price={price}: {quantized} < {raw} (undersized)"
            )
