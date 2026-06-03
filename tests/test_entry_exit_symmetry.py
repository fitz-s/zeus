# Created: 2026-04-07
# Last reused/audited: 2026-06-02
# Authority basis: docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md; docs/operations/task_2026-05-08_object_invariance_wave31/PLAN.md
# Wave 3 (2026-06-02): evaluate_exit_triggers deleted (dead twin). Single usage repointed
#   to Position.evaluate_exit.
"""Tests for entry-exit epistemic symmetry. §P9.7, D4.

Entry: bootstrap n=200+ with BH-FDR α=0.10.
Exit:  2-cycle consecutive confirmation with conservative_forward_edge.
D4 requires a shared DecisionEvidence contract so both use the same burden.
"""
import pytest
import numpy as np

from src.contracts.decision_evidence import DecisionEvidence, EvidenceAsymmetryError
from src.contracts.edge_context import EdgeContext
from src.contracts.semantic_types import EntryMethod


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
        """The flat BUY_NO_EDGE_EXIT exit requires TWO consecutive negative
        cycles, not one. Drives the REAL Position.evaluate_exit live path
        (no MagicMock) with neg_edge_count starting at its 0 default — never
        pre-seeded — so the test cannot accidentally cross the 2-cycle
        threshold on the first cycle.

        Invariant guarded (consecutive_confirmations() == 2):
          cycle 1 (neg_edge_count 0 -> 1)  : MUST NOT exit on BUY_NO_EDGE_EXIT
          cycle 2 (neg_edge_count 1 -> 2)  : MUST exit, trigger BUY_NO_EDGE_EXIT

        Regression caught: a prior MagicMock rewrite pre-seeded
        neg_edge_count = 1, so a single negative cycle reached the threshold
        and returned a real BUY_NO_EDGE_EXIT — yet the weak assertion only
        checked ``trigger != "EDGE_REVERSAL"``, letting that wrong-side
        single-cycle exit pass. RED proof: with neg_edge_count pre-seeded to
        1 the first call returns should_exit=True / BUY_NO_EDGE_EXIT, which
        the cycle-1 assertion below fails on. GREEN: from the 0 default,
        cycle 1 holds and only cycle 2 exits.
        """
        from src.state.portfolio import (
            Position,
            ExitContext,
            consecutive_confirmations,
            near_settlement_hours,
        )

        # The invariant under test is "threshold == 2". If config ever moves
        # off 2, assert the count rather than silently testing a different
        # property.
        assert consecutive_confirmations() == 2, (
            "This test asserts the 2-consecutive-cycle exit threshold; "
            f"consecutive_confirmations()={consecutive_confirmations()}."
        )

        def _fresh_position() -> Position:
            # neg_edge_count is the dataclass default (0) — NOT pre-seeded.
            return Position(
                trade_id="TEST-001", market_id="m-sym", city="Dallas",
                cluster="Dallas", target_date="2026-04-01", bin_label="70-75",
                direction="buy_no", size_usd=10.0, entry_price=0.50,
                p_posterior=0.50, edge=0.0, entry_ci_width=0.10,
                # cost_basis >= $1 to avoid the micro-position hold path.
                cost_basis_usd=10.0, shares=20.0, shares_filled=20.0,
                filled_cost_basis_usd=10.0,
            )

        def _negative_cycle_ctx() -> ExitContext:
            # hours_to_settlement ABOVE near_settlement_hours() so we bypass
            # the BUY_NO_NEAR_EXIT branch and reach the consecutive_cycle_check.
            # forward_edge = fresh_prob - current_market_price = 0.20 - 0.50 =
            # -0.30 -> evidence_edge -0.35 < edge_threshold -0.15 -> a negative
            # cycle. best_bid (0.55) > fresh_prob (0.20) so the EV gate does NOT
            # suppress the exit on the confirming cycle.
            return ExitContext(
                fresh_prob=0.20, fresh_prob_is_fresh=True,
                current_market_price=0.50, current_market_price_is_fresh=True,
                best_bid=0.55,
                hours_to_settlement=near_settlement_hours() + 100.0,
                position_state="active",
                market_velocity_1h=0.0, divergence_score=0.0,
            )

        position = _fresh_position()
        assert position.neg_edge_count == 0, "neg_edge_count must start at 0 (not pre-seeded)."

        # Cycle 1 — one negative cycle must NOT exit on the flat edge trigger.
        first = position.evaluate_exit(_negative_cycle_ctx())
        assert not (first.should_exit and first.trigger == "BUY_NO_EDGE_EXIT"), (
            "Single negative cycle triggered BUY_NO_EDGE_EXIT — the flat exit "
            "requires TWO consecutive confirmations, not one."
        )
        assert position.neg_edge_count == 1, (
            f"After one negative cycle neg_edge_count should be 1, got {position.neg_edge_count}."
        )

        # Cycle 2 — the second consecutive negative cycle MUST exit. This
        # proves the threshold is exactly 2 (not <= 1): a never-firing exit
        # would also satisfy cycle 1, so cycle 2 closes that loophole.
        second = position.evaluate_exit(_negative_cycle_ctx())
        assert second.should_exit and second.trigger == "BUY_NO_EDGE_EXIT", (
            "Two consecutive negative cycles must trigger BUY_NO_EDGE_EXIT; "
            f"got should_exit={second.should_exit}, trigger={second.trigger!r}."
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


# ---------------------------------------------------------------------------
# T4.3 2026-04-23 — Static AST-walk call-site presence antibodies
#
# Closes the D4 immunity loop at grep/CI time. T4.3b's runtime-mock test
# (tests/test_decision_evidence_runtime_invocation.py) would flag a
# silent refactor AFTER the fact by failing on an accept-path fixture;
# T4.3 flags it BEFORE anyone runs evaluator fixtures, as soon as the
# AST no longer contains the contract call sites.
#
# Plan-premise correction #11: fix plan T4.3 row cited
# "assert_symmetric_or_stronger" as the required literal. The actual
# method on DecisionEvidence is assert_symmetric_with (verified at
# src/contracts/decision_evidence.py:156). Tests use the correct name.
# ---------------------------------------------------------------------------


class TestDecisionEvidenceStaticCallSitePresence:
    """AST-walk presence tests — D4 contract call sites must survive
    refactors. Antibody against silent removal or rerouting of the
    T4.1b entry-path construction and T4.2 exit-gate wiring."""

    @staticmethod
    def _source_tree(path: str):
        import ast
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[1]
        return ast.parse((repo_root / path).read_text(), filename=path)

    @staticmethod
    def _decision_evidence_calls_with_type(tree, *, evidence_type: str) -> list[int]:
        """Return line numbers of every DecisionEvidence(...) Call node
        whose `evidence_type` keyword is the literal constant requested.
        Matches both `DecisionEvidence(...)` and
        `decision_evidence.DecisionEvidence(...)` by resolving the final
        callable name."""
        import ast
        hits: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name != "DecisionEvidence":
                continue
            for kw in node.keywords:
                if kw.arg != "evidence_type":
                    continue
                if isinstance(kw.value, ast.Constant) and kw.value.value == evidence_type:
                    hits.append(node.lineno)
                    break
        return hits

    def test_evaluator_accept_path_constructs_entry_evidence(self):
        """src/engine/evaluator.py must construct
        DecisionEvidence(evidence_type="entry", ...) somewhere (the
        T4.1b accept-path wiring at L1700+). If this fails, a refactor
        silently removed or relocated the construction — T4.1b /
        T4.2 read path will begin returning None entry evidence
        in production."""
        tree = self._source_tree("src/engine/evaluator.py")
        hits = self._decision_evidence_calls_with_type(tree, evidence_type="entry")
        assert hits, (
            "src/engine/evaluator.py must contain "
            'DecisionEvidence(evidence_type="entry", ...) — T4.1b accept '
            "path. Check evaluator.py accept site around the "
            "should_trade=True EdgeDecision construction."
        )

    def test_cycle_runtime_constructs_exit_evidence_for_gate(self):
        """src/engine/cycle_runtime.py must construct
        DecisionEvidence(evidence_type="exit", ...) for the
        T4.2 symmetry gate. Absence means the exit-side weak-burden
        comparison was silently dropped and D4 statistical exits can bypass
        the gate."""
        tree = self._source_tree("src/engine/cycle_runtime.py")
        hits = self._decision_evidence_calls_with_type(tree, evidence_type="exit")
        assert hits, (
            "src/engine/cycle_runtime.py must contain "
            'DecisionEvidence(evidence_type="exit", ...) — T4.2 '
            "hard gate around pos.evaluate_exit."
        )

    def test_cycle_runtime_invokes_assert_symmetric_with(self):
        """src/engine/cycle_runtime.py must invoke
        `<exit_evidence>.assert_symmetric_with(entry_evidence)` at
        least once — the T4.2 symmetry gate. If this call is removed,
        weak statistical exits can again become executable sell intents."""
        import ast
        tree = self._source_tree("src/engine/cycle_runtime.py")
        hits: list[int] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "assert_symmetric_with"
            ):
                hits.append(node.lineno)
        assert hits, (
            "src/engine/cycle_runtime.py must invoke "
            "exit_evidence.assert_symmetric_with(entry_evidence) — "
            "the T4.2 D4 hard gate."
        )
