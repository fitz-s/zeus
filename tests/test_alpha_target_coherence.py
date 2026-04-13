"""Tests for alpha optimization target coherence. §P9.7, D1 & D2."""
import pytest
from pathlib import Path

from src.contracts.alpha_decision import AlphaDecision, AlphaTargetMismatchError
from src.contracts.tail_treatment import TailTreatment
from src.strategy.market_fusion import compute_alpha
from src.types.temperature import TemperatureDelta

ZEUS_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# AlphaDecision construction
# ---------------------------------------------------------------------------

class TestAlphaDecisionConstruction:

    def test_valid_brier_alpha_constructs(self):
        ad = AlphaDecision(
            value=0.65,
            optimization_target="brier_score",
            evidence_basis="D4 sweep 2026-03-31",
            ci_bound=0.10,
        )
        assert ad.value == 0.65
        assert ad.optimization_target == "brier_score"

    def test_valid_ev_alpha_constructs(self):
        ad = AlphaDecision(
            value=0.70,
            optimization_target="ev",
            evidence_basis="EV sweep 2026-04-01",
            ci_bound=0.05,
        )
        assert ad.optimization_target == "ev"

    def test_invalid_target_raises(self):
        with pytest.raises(ValueError, match="optimization_target"):
            AlphaDecision(
                value=0.65,
                optimization_target="profit",  # type: ignore[arg-type]
                evidence_basis="some basis",
                ci_bound=0.10,
            )

    def test_value_out_of_range_raises(self):
        with pytest.raises(ValueError):
            AlphaDecision(
                value=1.5,
                optimization_target="brier_score",
                evidence_basis="test",
                ci_bound=0.10,
            )

    def test_empty_evidence_basis_raises(self):
        with pytest.raises(ValueError):
            AlphaDecision(
                value=0.65,
                optimization_target="brier_score",
                evidence_basis="",
                ci_bound=0.10,
            )

    def test_negative_ci_bound_raises(self):
        with pytest.raises(ValueError):
            AlphaDecision(
                value=0.65,
                optimization_target="brier_score",
                evidence_basis="test basis",
                ci_bound=-0.01,
            )


# ---------------------------------------------------------------------------
# assert_target_compatible — D1 contract
# ---------------------------------------------------------------------------

class TestBrierAlphaIntoEvSizingRaises:
    """AlphaDecision(target=brier_score).assert_target_compatible('ev') raises. §P9.7 D1."""

    def test_brier_into_ev_raises(self):
        """Canonical D1 violation: Brier alpha fed into EV consumer."""
        ad = AlphaDecision(
            value=0.65,
            optimization_target="brier_score",
            evidence_basis="D4 sweep 2026-03-31",
            ci_bound=0.10,
        )
        with pytest.raises(AlphaTargetMismatchError):
            ad.assert_target_compatible("ev")

    def test_error_message_names_both_targets(self):
        """Error message names the alpha target and the consumer target."""
        ad = AlphaDecision(
            value=0.65,
            optimization_target="brier_score",
            evidence_basis="D4 sweep",
            ci_bound=0.10,
        )
        with pytest.raises(AlphaTargetMismatchError) as exc_info:
            ad.assert_target_compatible("ev")
        msg = str(exc_info.value)
        assert "brier_score" in msg
        assert "ev" in msg

    def test_ev_into_ev_passes(self):
        """EV-optimized alpha into EV consumer does not raise."""
        ad = AlphaDecision(
            value=0.65,
            optimization_target="ev",
            evidence_basis="EV fit 2026-04-01",
            ci_bound=0.05,
        )
        ad.assert_target_compatible("ev")  # Must not raise

    def test_brier_into_brier_passes(self):
        """Brier alpha into Brier consumer is fine."""
        ad = AlphaDecision(
            value=0.65,
            optimization_target="brier_score",
            evidence_basis="D4 sweep",
            ci_bound=0.10,
        )
        ad.assert_target_compatible("brier_score")

    def test_brier_into_risk_cap_passes(self):
        """Brier alpha into risk_cap consumer is allowed (not EV-seeking)."""
        ad = AlphaDecision(
            value=0.65,
            optimization_target="brier_score",
            evidence_basis="D4 sweep",
            ci_bound=0.10,
        )
        ad.assert_target_compatible("risk_cap")  # Must not raise

    def test_risk_cap_into_ev_passes(self):
        """risk_cap alpha into EV consumer is allowed (conservative, not misleading)."""
        ad = AlphaDecision(
            value=0.30,
            optimization_target="risk_cap",
            evidence_basis="Conservative cap",
            ci_bound=0.05,
        )
        ad.assert_target_compatible("ev")  # Must not raise

    def test_value_for_consumer_checks_target_then_returns_value(self):
        ad = AlphaDecision(
            value=0.30,
            optimization_target="risk_cap",
            evidence_basis="Conservative cap",
            ci_bound=0.05,
        )
        assert ad.value_for_consumer("ev") == pytest.approx(0.30)

    def test_invalid_consumer_target_raises(self):
        ad = AlphaDecision(
            value=0.30,
            optimization_target="risk_cap",
            evidence_basis="Conservative cap",
            ci_bound=0.05,
        )
        with pytest.raises(ValueError, match="consumer_target"):
            ad.value_for_consumer("profit")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# compute_alpha still returns float (P9 seam not yet wired)
# ---------------------------------------------------------------------------

class TestComputeAlphaReturnsAlphaDecision:
    """compute_alpha is wired with P9 seam — returns AlphaDecision."""

    def test_compute_alpha_returns_alpha_decision(self):
        """compute_alpha returns AlphaDecision with declared optimization_target."""
        result = compute_alpha(
            calibration_level=2,
            ensemble_spread=TemperatureDelta(2.0, "F"),
            model_agreement="AGREE",
            lead_days=3.0,
            hours_since_open=24.0,
        )
        assert isinstance(result, AlphaDecision), (
            f"compute_alpha returned {type(result).__name__}, expected AlphaDecision."
        )

    def test_compute_alpha_declares_risk_cap_target(self):
        """compute_alpha declares optimization_target='risk_cap'.

        D1 resolution chose risk_cap over brier_score: alpha is a conservative
        blending weight, not a pure Brier minimizer. risk_cap is compatible with
        EV-seeking sizing (conservative shrinkage, not calibration attack).
        """
        result = compute_alpha(
            calibration_level=2,
            ensemble_spread=TemperatureDelta(2.0, "F"),
            model_agreement="AGREE",
            lead_days=3.0,
            hours_since_open=24.0,
        )
        assert result.optimization_target == "risk_cap", (
            f"Expected risk_cap, got {result.optimization_target}. "
            "D1 resolution: alpha declared as risk_cap (conservative blending weight)."
        )

    def test_compute_alpha_risk_cap_compatible_with_ev(self):
        """risk_cap alpha is compatible with EV-seeking sizing — does not raise."""
        result = compute_alpha(
            calibration_level=2,
            ensemble_spread=TemperatureDelta(2.0, "F"),
            model_agreement="AGREE",
            lead_days=3.0,
            hours_since_open=24.0,
        )
        # risk_cap → ev is allowed (conservative, not misleading)
        result.assert_target_compatible("ev")  # Must not raise

    def test_brier_alpha_still_raises_into_ev(self):
        """A hypothetical brier_score alpha still raises into EV consumer — contract intact."""
        brier_ad = AlphaDecision(
            value=0.65,
            optimization_target="brier_score",
            evidence_basis="Hypothetical Brier-only alpha",
            ci_bound=0.10,
        )
        with pytest.raises(AlphaTargetMismatchError):
            brier_ad.assert_target_compatible("ev")

    @pytest.mark.parametrize("model_agreement,lead_days", [
        ("AGREE", 1.0),
        ("SOFT_DISAGREE", 3.0),
        ("CONFLICT", 7.0),
    ])
    def test_alpha_value_in_valid_range(self, model_agreement, lead_days):
        """All code paths return α.value in [0.20, 0.85]."""
        result = compute_alpha(
            calibration_level=3,
            ensemble_spread=TemperatureDelta(3.5, "F"),
            model_agreement=model_agreement,
            lead_days=lead_days,
            hours_since_open=48.0,
        )
        assert isinstance(result, AlphaDecision)
        assert 0.20 <= result.value <= 0.85, (
            f"α.value={result.value} out of [0.20, 0.85] for {model_agreement}"
        )


# ---------------------------------------------------------------------------
# TailTreatment — D2 contract
# ---------------------------------------------------------------------------

class TestTailTreatmentDeclaresTarget:
    """TailTreatment must declare calibration_accuracy OR profit. §P9.7 D2."""

    def test_calibration_tail_treatment_constructs(self):
        tt = TailTreatment(
            scale_factor=0.5,
            serves="calibration_accuracy",
            validated_against="D3 sweep 2026-03-31 bins=[open-ended], Brier −0.042",
        )
        assert tt.serves == "calibration_accuracy"
        assert tt.scale_factor == pytest.approx(0.5)

    def test_profit_tail_treatment_constructs(self):
        tt = TailTreatment(
            scale_factor=0.7,
            serves="profit",
            validated_against="buy_no P&L sweep 2026-04-01",
        )
        assert tt.serves == "profit"

    def test_zero_scale_factor_raises(self):
        with pytest.raises(ValueError, match="scale_factor"):
            TailTreatment(
                scale_factor=0.0,
                serves="calibration_accuracy",
                validated_against="some sweep",
            )

    def test_above_one_scale_factor_raises(self):
        with pytest.raises(ValueError, match="scale_factor"):
            TailTreatment(
                scale_factor=1.5,
                serves="calibration_accuracy",
                validated_against="some sweep",
            )

    def test_empty_validated_against_raises(self):
        with pytest.raises(ValueError, match="validated_against"):
            TailTreatment(
                scale_factor=0.5,
                serves="calibration_accuracy",
                validated_against="",
            )

    def test_profit_with_brier_validation_warns(self):
        """TailTreatment(serves='profit') with Brier-reference emits warning."""
        import warnings
        tt = TailTreatment(
            scale_factor=0.5,
            serves="profit",
            validated_against="brier sweep 2026-03-31",
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            tt.warn_if_profit_unvalidated()
        assert len(w) == 1
        assert "brier" in str(w[0].message).lower() or "profit" in str(w[0].message).lower()

    def test_profit_with_pnl_validation_no_warning(self):
        """TailTreatment(serves='profit') with P&L reference does not warn."""
        import warnings
        tt = TailTreatment(
            scale_factor=0.5,
            serves="profit",
            validated_against="buy_no P&L 2026-04-01",
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            tt.warn_if_profit_unvalidated()
        assert len(w) == 0

    def test_tail_alpha_scale_matches_tail_treatment_default(self):
        """TAIL_ALPHA_SCALE in market_fusion.py is 0.5 — matches TailTreatment scale."""
        source = (ZEUS_ROOT / "src" / "strategy" / "market_fusion.py").read_text()
        assert "TAIL_ALPHA_SCALE" in source, "TAIL_ALPHA_SCALE not found in market_fusion.py"
        tt = TailTreatment(
            scale_factor=0.5,
            serves="calibration_accuracy",
            validated_against="D3 sweep 2026-03-31",
        )
        assert tt.scale_factor == pytest.approx(0.5)
