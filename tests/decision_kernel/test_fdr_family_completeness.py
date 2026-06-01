# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: S6 (FDR family-completeness) — spec §9.1/§13.10; defect w43x2ut5u
"""S6 relationship test: legacy ``fdr_filter`` must not run BH on a partial family.

DEFECT (w43x2ut5u): ``src/strategy/fdr_filter.py`` computes the Benjamini-
Hochberg denominator as ``m = len(edges)`` of whatever list the caller hands
in. The module is documented as "legacy for older call sites that already
provide a complete family" but enforced NOTHING. Any caller passing a
PRE-FILTERED subset (e.g. the positive-edge survivors of
``MarketAnalysis.find_edges``) gets an inflated significance threshold
``fdr_alpha * k / m`` — more false accepts — because ``m`` undercounts the
true family size.

This is a RELATIONSHIP test, not a function test. It asserts the cross-call
property: a hypothesis that full-family BH REJECTS must not be ACCEPTED merely
because the survivor subset it lives in was handed to ``fdr_filter`` alone.

Spec §9.1 / §13.10: the BH family must cover every tested
(city, date, mode, bin, direction) hypothesis.
"""
from __future__ import annotations

import pytest

from src.strategy.fdr_filter import FDRPartialFamilyError, fdr_filter
from src.strategy.selection_family import benjamini_hochberg_mask
from src.types import Bin, BinEdge


def _make_edge(p_value: float, *, low: float = 40, high: float = 41) -> BinEdge:
    """A BinEdge carrying a known bootstrap p-value (other fields are inert)."""
    return BinEdge(
        bin=Bin(low=low, high=high, unit="F", label=f"{int(low)}-{int(high)}°F"),
        direction="buy_yes",
        edge=0.05,
        ci_lower=0.01,
        ci_upper=0.10,
        p_model=0.15,
        p_market=0.10,
        p_posterior=0.15,
        entry_price=0.10,
        p_value=p_value,
        vwmp=0.10,
    )


# Complete tested family: m=10. Smallest p=0.04.
#   Full-family BH at q=0.10: k=1 threshold = 0.10 * 1/10 = 0.01.
#   p=0.04 > 0.01  -> the p=0.04 hypothesis is REJECTED by full-family BH.
_COMPLETE_FAMILY_P_VALUES = [
    0.04, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95,
]

# The prefilter (e.g. find_edges positive-forward-edge survivors) keeps only a
# 2-element subset of the family — the p=0.04 edge plus one other survivor.
#   Legacy BH at q=0.10 over the SUBSET: m=2, k=1 threshold = 0.10 * 1/2 = 0.05.
#   p=0.04 <= 0.05 -> the p=0.04 hypothesis is ACCEPTED by the subset path.
_SUBSET_P_VALUES = [0.04, 0.20]


class TestFDRPartialFamilyInflation:
    """The legacy subset path must not silently inflate the BH threshold."""

    def test_full_family_bh_rejects_the_borderline_hypothesis(self):
        """Baseline (ground truth): under the COMPLETE family, p=0.04 is rejected.

        This pins the relationship's reference truth: the borderline hypothesis
        is genuinely non-significant once every tested hypothesis is in the
        denominator (m=10).
        """
        mask = benjamini_hochberg_mask(_COMPLETE_FAMILY_P_VALUES, q=0.10)
        # Position 0 holds p=0.04 — must be NOT selected under the full family.
        assert mask[0] is False
        assert all(selected is False for selected in mask), (
            "Full-family BH (m=10) must reject every hypothesis here — the "
            "smallest p=0.04 already exceeds the k=1 threshold 0.01."
        )

    def test_partial_family_call_raises_instead_of_inflating_threshold(self):
        """RELATIONSHIP INVARIANT (the defect): handing the survivor SUBSET to
        ``fdr_filter`` must NOT accept the p=0.04 hypothesis that full-family BH
        rejected.

        On the ORIGINAL (pre-S6) code, ``fdr_filter(subset)`` runs BH with m=2,
        accepts p=0.04 (0.04 <= 0.05), and returns 1 edge — a FALSE ACCEPT
        caused purely by the undercounted denominator. After S6, the partial
        call must be refused (family_complete not asserted), so the inflated
        threshold can never be produced.
        """
        subset_edges = [_make_edge(p) for p in _SUBSET_P_VALUES]

        # The structural fix: a partial-family call (no family_complete=True,
        # no full_family_size) must raise rather than run BH on the
        # undercounted denominator. Omitting the required keyword-only args
        # reproduces the legacy subset call shape `fdr_filter(edges)`.
        with pytest.raises(Exception):
            fdr_filter(subset_edges)

    def test_complete_family_call_runs_bh_and_rejects_borderline(self):
        """Positive control: when the caller PROVES the family is complete, the
        helper runs BH with the correct m and rejects the borderline p=0.04 —
        identical verdict to ``benjamini_hochberg_mask`` over the full family.

        This guarantees the fix did not break legitimate complete-family use
        and did not turn FDR into a stricter calibration guard: same weak BH
        noise filter, just with a trusted denominator.
        """
        full_family_edges = [_make_edge(p) for p in _COMPLETE_FAMILY_P_VALUES]
        result = fdr_filter(
            full_family_edges,
            family_complete=True,
            full_family_size=len(full_family_edges),
        )
        assert result == [], (
            "Over the complete family (m=10), BH rejects all — including the "
            "borderline p=0.04 — so the helper must return no edges."
        )

    def test_partial_family_call_with_false_flag_also_raises(self):
        """Explicit ``family_complete=False`` is as forbidden as omission — a
        caller cannot opt out of completeness by lying with False.
        """
        subset_edges = [_make_edge(p) for p in _SUBSET_P_VALUES]
        with pytest.raises(Exception):
            fdr_filter(
                subset_edges,
                family_complete=False,
                full_family_size=len(_COMPLETE_FAMILY_P_VALUES),
            )

    def test_q4_completeness_attestation_over_subset_raises(self):
        """Q4 CATEGORY-ANTIBODY (2026-06-01): a caller that LIES — attests
        ``family_complete=True`` while handing in a SUBSET — must RAISE, not
        silently inflate the BH threshold.

        This is the structural upgrade over the bare boolean: the integer
        ``full_family_size`` lets the helper assert ``len(edges) ==
        full_family_size``. Here the survivor subset (m=2) is attested complete
        against the true family of size 10. Under the original bare-boolean
        contract this call would have RUN BH on m=2 and FALSE-ACCEPTED p=0.04.
        Under the Q4 contract the mismatch (2 != 10) is mechanically caught and
        the false attestation is UNCONSTRUCTABLE — it raises instead.
        """
        subset_edges = [_make_edge(p) for p in _SUBSET_P_VALUES]
        full_family_size = len(_COMPLETE_FAMILY_P_VALUES)  # 10, but len(subset)=2

        with pytest.raises(FDRPartialFamilyError) as excinfo:
            fdr_filter(
                subset_edges,
                family_complete=True,
                full_family_size=full_family_size,
            )

        message = str(excinfo.value).lower()
        assert "full_family_size" in message, (
            "Refusal must cite the structural size mismatch — the integer "
            "full_family_size is the antibody that catches the lie."
        )
        # Prove the false accept the inflated denominator WOULD have produced
        # never happens: the borderline p=0.04 hypothesis is not returned
        # because the call raised before any BH math ran.
        assert "2" in message and "10" in message, (
            "Refusal must surface the concrete subset/family sizes (2 vs 10) "
            "so the inflation is auditable."
        )
