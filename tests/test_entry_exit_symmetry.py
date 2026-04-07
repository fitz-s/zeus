"""Tests for entry-exit epistemic symmetry. §P9.7, D4.

Entry: bootstrap n=200+ with BH-FDR α=0.10.
Exit:  2-cycle consecutive confirmation with conservative_forward_edge.
D4 requires a shared DecisionEvidence contract so both use the same burden.
"""
import pytest
from unittest.mock import MagicMock
import numpy as np

from src.contracts.decision_evidence import DecisionEvidence, EvidenceAsymmetryError
from src.contracts.edge_context import EdgeContext
from src.contracts.semantic_types import EntryMethod
from src.execution.exit_triggers import evaluate_exit_triggers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_edge_context(**overrides):
    defaults = dict(
        p_raw=np.array([0.1, 0.6, 0.3]),
        p_cal=np.array([0.1, 0.6, 0.3]),
        p_market=np.array([0.1, 0.5, 0.4]),
        p_posterior=0.60,
        forward_edge=0.10,
        alpha=0.70,
        confidence_band_upper=0.70,
        confidence_band_lower=0.50,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="test-snap-001",
        n_edges_found=3,
        n_edges_after_fdr=2,
        market_velocity_1h=0.0,
        divergence_score=0.0,
    )
    defaults.update(overrides)
    return EdgeContext(**defaults)


def _entry_evidence(sample_size=200, fdr_corrected=True, consecutive=1):
    return DecisionEvidence(
        evidence_type="entry",
        statistical_method="bootstrap_ci_bh_fdr",
        sample_size=sample_size,
        confidence_level=0.10,
        fdr_corrected=fdr_corrected,
        consecutive_confirmations=consecutive,
    )


def _exit_evidence(sample_size=20, fdr_corrected=True, consecutive=2):
    return DecisionEvidence(
        evidence_type="exit",
        statistical_method="bootstrap_ci_bh_fdr",
        sample_size=sample_size,
        confidence_level=0.10,
        fdr_corrected=fdr_corrected,
        consecutive_confirmations=consecutive,
    )


# ---------------------------------------------------------------------------
# Current-state tests (document asymmetry baseline)
# ---------------------------------------------------------------------------

class TestCurrentExitUsesConsecutiveCycles:

    def test_exit_requires_consecutive_confirmations(self):
        """Single negative cycle does NOT trigger exit."""
        position = MagicMock()
        position.trade_id = "TEST-001"
        position.direction = "buy_no"
        position.neg_edge_count = 1
        position.size_usd = 10.0
        position.entry_ci_width = 0.10

        ctx = _make_edge_context(
            forward_edge=-0.05,
            confidence_band_upper=0.50,
            confidence_band_lower=0.40,
            p_posterior=0.45,
        )
        signal = evaluate_exit_triggers(position, ctx)
        if signal is not None:
            assert signal.trigger != "EDGE_REVERSAL", (
                "Single negative cycle triggered EDGE_REVERSAL — requires consecutive."
            )

    def test_exit_uses_ci_width_in_evidence_edge(self):
        """conservative_forward_edge applies CI penalty."""
        from src.state.portfolio import conservative_forward_edge
        evidence = conservative_forward_edge(0.05, 0.20)
        assert evidence <= 0.05


# ---------------------------------------------------------------------------
# DecisionEvidence contract tests
# ---------------------------------------------------------------------------

class TestDecisionEvidenceConstruction:

    def test_entry_evidence_constructs(self):
        ev = _entry_evidence()
        assert ev.evidence_type == "entry"
        assert ev.fdr_corrected is True
        assert ev.sample_size == 200

    def test_exit_evidence_constructs(self):
        ev = _exit_evidence()
        assert ev.evidence_type == "exit"

    def test_invalid_evidence_type_not_runtime_enforced(self):
        """Literal["entry","exit"] is a type hint, not runtime-enforced.

        assert_symmetric_with() IS enforced — this documents the boundary.
        """
        # Construction does not raise (Literal is a static type hint only)
        ev = DecisionEvidence(
            evidence_type="unknown",
            statistical_method="bootstrap",
            sample_size=10,
            confidence_level=0.10,
            fdr_corrected=True,
            consecutive_confirmations=1,
        )
        assert ev.evidence_type == "unknown"

    def test_zero_sample_size_raises(self):
        with pytest.raises(ValueError, match="sample_size"):
            DecisionEvidence(
                evidence_type="entry",
                statistical_method="bootstrap",
                sample_size=0,
                confidence_level=0.10,
                fdr_corrected=True,
                consecutive_confirmations=1,
            )

    def test_zero_consecutive_raises(self):
        with pytest.raises(ValueError, match="consecutive"):
            DecisionEvidence(
                evidence_type="exit",
                statistical_method="bootstrap",
                sample_size=5,
                confidence_level=0.10,
                fdr_corrected=True,
                consecutive_confirmations=0,
            )


class TestEntryExitEvidenceSymmetric:
    """assert_symmetric_with enforces D4 symmetry."""

    def test_symmetric_evidence_passes(self):
        """Exit with same sample_size/fdr/consecutive as entry passes."""
        entry = _entry_evidence(sample_size=200, fdr_corrected=True, consecutive=2)
        exit_ = _exit_evidence(sample_size=200, fdr_corrected=True, consecutive=2)
        exit_.assert_symmetric_with(entry)  # Must not raise

    def test_stronger_exit_passes(self):
        """Exit with larger sample and more consecutive confirmations passes."""
        entry = _entry_evidence(sample_size=100, fdr_corrected=False, consecutive=1)
        exit_ = _exit_evidence(sample_size=200, fdr_corrected=True, consecutive=2)
        exit_.assert_symmetric_with(entry)  # Must not raise


class TestExitCannotUseWeakerEvidenceThanEntry:

    def test_2_cycle_vs_200_bootstrap_raises(self):
        """The canonical D4 violation: exit sample_size=2 vs entry=200."""
        entry = _entry_evidence(sample_size=200, fdr_corrected=True, consecutive=2)
        exit_ = _exit_evidence(sample_size=2, fdr_corrected=True, consecutive=2)
        with pytest.raises(EvidenceAsymmetryError, match="sample_size|D4"):
            exit_.assert_symmetric_with(entry)

    def test_exit_without_fdr_when_entry_has_fdr_raises(self):
        """Entry FDR-corrected but exit not — D4 violation."""
        entry = _entry_evidence(sample_size=200, fdr_corrected=True, consecutive=2)
        exit_ = _exit_evidence(sample_size=200, fdr_corrected=False, consecutive=2)
        with pytest.raises(EvidenceAsymmetryError, match="FDR|fdr"):
            exit_.assert_symmetric_with(entry)

    def test_fewer_exit_consecutive_than_entry_raises(self):
        """Exit requiring fewer consecutive confirmations than entry raises."""
        entry = _entry_evidence(sample_size=50, fdr_corrected=False, consecutive=3)
        exit_ = _exit_evidence(sample_size=50, fdr_corrected=False, consecutive=1)
        with pytest.raises(EvidenceAsymmetryError, match="confirmation"):
            exit_.assert_symmetric_with(entry)

    def test_error_message_mentions_d4(self):
        """Error message references D4."""
        entry = _entry_evidence(sample_size=200, fdr_corrected=True, consecutive=2)
        exit_ = _exit_evidence(sample_size=2, fdr_corrected=False, consecutive=1)
        with pytest.raises(EvidenceAsymmetryError) as exc_info:
            exit_.assert_symmetric_with(entry)
        assert "D4" in str(exc_info.value)

    def test_assert_symmetric_requires_exit_evidence_type(self):
        """Calling assert_symmetric_with on entry evidence raises ValueError."""
        entry = _entry_evidence()
        another_entry = _entry_evidence()
        with pytest.raises(ValueError, match="exit"):
            entry.assert_symmetric_with(another_entry)

    def test_assert_symmetric_requires_entry_paired_evidence(self):
        """Pairing two exit evidences raises ValueError."""
        exit1 = _exit_evidence()
        exit2 = _exit_evidence()
        with pytest.raises(ValueError, match="entry"):
            exit1.assert_symmetric_with(exit2)
