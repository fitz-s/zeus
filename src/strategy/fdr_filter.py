"""Legacy Benjamini-Hochberg FDR helper for caller-supplied COMPLETE families.

Active evaluator selection prefers the full-family scan plus
``selection_family.apply_familywise_fdr`` so the denominator includes every
tested bin/direction hypothesis for the candidate market snapshot. This helper
keeps the same BH math for compatibility with older call sites that already
provide a complete family.

S6 family-completeness enforcement (2026-06-01)
-----------------------------------------------
The BH denominator ``m`` is ``len(edges)``. If a caller hands in a
PRE-FILTERED subset (e.g. the survivors of ``MarketAnalysis.find_edges``,
which only returns bins whose forward edge is positive and executable), then
``m`` undercounts the true family size and the significance threshold
``fdr_alpha * k / m`` is INFLATED — more false accepts. Spec §9.1 / §13.10
require the BH family to cover every tested (city, date, mode, bin, direction)
hypothesis, not just the survivors.

There is no way to reconstruct the true ``m`` from a survivor subset, so the
helper cannot silently "fix" a partial call. Instead it makes the partial
call STRUCTURALLY UNCONSTRUCTABLE.

Q4 STRUCTURAL HARDENING (2026-06-01, Fitz #4 — instance-antibody → category-antibody)
-------------------------------------------------------------------------------------
A bare ``family_complete=True`` boolean is a PROVENANCE CLAIM the caller can
make falsely: nothing stops a caller from attesting completeness over a
survivor subset, silently re-introducing the inflated-``m`` defect. The boolean
alone makes the bug "less likely", not "impossible".

The category-antibody replaces the unverifiable claim with a STRUCTURALLY
CHECKABLE contract: the caller MUST pass ``full_family_size: int`` — the true
number of tested hypotheses in the BH family — AND ``family_complete=True``. The
helper then asserts ``len(edges) == full_family_size``. If the caller hands in a
subset while attesting completeness, ``len(edges) < full_family_size`` and the
call RAISES ``FDRPartialFamilyError`` instead of silently running BH on an
undercounted denominator. A false attestation over a subset is therefore
UNCONSTRUCTABLE — it raises rather than inflates the threshold. The integer
``full_family_size`` is the antibody: the lie is now mechanically detectable at
the helper boundary, not merely discouraged by documentation.

This keeps FDR as a WEAK noise filter — it does NOT turn it into a calibration
or false-confidence guard. It only refuses to run BH math on a denominator it
cannot trust. Callers holding a partial family must route through
``selection_family.apply_familywise_fdr`` (the full-family path), which
receives every tested hypothesis (``tested=True`` rows) and therefore carries a
correct ``m`` per family.

p-values are computed via np.mean(bootstrap_edges <= 0) in MarketAnalysis,
NEVER via approximation formula.
"""

from src.config import settings
from src.types import BinEdge

# HARDCODED(setting_key="edge.fdr_alpha", note_key="edge._fdr_alpha_note",
#           tier=1, replace_after="500+ candidate evaluations",
#           data_needed="observed false positive rate versus target FDR")
DEFAULT_FDR_ALPHA = float(settings["edge"]["fdr_alpha"])


class FDRPartialFamilyError(ValueError):
    """Raised when ``fdr_filter`` is invoked over an UNTRUSTED denominator.

    The Benjamini-Hochberg denominator ``m = len(edges)`` is only valid when
    ``edges`` is the COMPLETE tested family. A pre-filtered subset undercounts
    ``m`` and inflates the significance threshold (more false accepts).

    Q4 structural contract (2026-06-01): the caller must (a) attest
    ``family_complete=True`` AND (b) pass ``full_family_size`` equal to
    ``len(edges)``. The helper asserts ``len(edges) == full_family_size`` so a
    completeness attestation over a subset (``len(edges) < full_family_size``)
    is mechanically caught and RAISED here rather than silently inflating the
    threshold. Callers holding a partial family must instead route through
    ``selection_family.apply_familywise_fdr`` with every tested hypothesis.
    """


def fdr_filter(
    edges: list[BinEdge],
    fdr_alpha: float = DEFAULT_FDR_ALPHA,
    *,
    family_complete: bool,
    full_family_size: int,
) -> list[BinEdge]:
    """Benjamini-Hochberg procedure for FDR control over a COMPLETE family.

    Spec §4.4: Sort by p-value ascending, find largest k where
    p_value[k] <= fdr_alpha * k / m. Return edges 1..k.

    S6 family-completeness (spec §9.1 / §13.10): ``m = len(edges)`` is the BH
    denominator and is only correct when ``edges`` already contains EVERY
    tested hypothesis in the family.

    Q4 STRUCTURAL CONTRACT (2026-06-01): completeness is no longer a bare
    boolean claim. The caller MUST pass BOTH:

      - ``family_complete=True`` — the provenance assertion, AND
      - ``full_family_size`` — the integer count of tested hypotheses in the
        BH family.

    The helper asserts ``len(edges) == full_family_size``. If a caller attests
    completeness while handing in a survivor SUBSET (``len(edges) <
    full_family_size``), the mismatch RAISES ``FDRPartialFamilyError`` — the
    inflated-denominator defect is structurally UNCONSTRUCTABLE, not merely
    discouraged. A partial family cannot be silently corrected because the true
    ``m`` is unrecoverable from a subset; it must be routed through
    ``selection_family.apply_familywise_fdr``.

    Args:
        edges: list of BinEdge with p_value from bootstrap. MUST be the
            complete tested family, not a pre-filtered survivor subset.
        fdr_alpha: target FDR level (default 10%).
        family_complete: REQUIRED keyword-only provenance assertion. Must be
            ``True`` to certify ``edges`` is the complete BH family.
        full_family_size: REQUIRED keyword-only integer — the true number of
            tested hypotheses in the BH family. Must equal ``len(edges)``;
            otherwise the call raises (subset attested as complete).

    Returns: filtered list of edges passing BH threshold.

    Raises:
        FDRPartialFamilyError: if ``family_complete`` is not ``True`` or if
            ``len(edges) != full_family_size`` (a subset attested as complete).
    """
    if family_complete is not True:
        raise FDRPartialFamilyError(
            "fdr_filter requires family_complete=True: the BH denominator "
            "m=len(edges) is only valid over the COMPLETE tested family "
            "(every city/date/mode/bin/direction hypothesis). A pre-filtered "
            "subset undercounts m and inflates the significance threshold "
            "(spec §9.1/§13.10). Pass family_complete=True only if `edges` "
            "already contains every tested hypothesis; otherwise route a "
            "partial family through selection_family.apply_familywise_fdr."
        )

    if len(edges) != full_family_size:
        raise FDRPartialFamilyError(
            "fdr_filter family-completeness contract violated: "
            f"len(edges)={len(edges)} != full_family_size={full_family_size}. "
            "family_complete=True was attested but `edges` is NOT the complete "
            "tested family — it is a subset of size "
            f"{len(edges)} against a declared family of {full_family_size}. "
            "Running BH on the subset would undercount the denominator m and "
            "inflate the significance threshold (spec §9.1/§13.10). Route the "
            "full tested family through selection_family.apply_familywise_fdr, "
            "or pass the COMPLETE family with full_family_size == len(edges)."
        )

    if not edges:
        return []

    m = len(edges)
    sorted_by_p = sorted(edges, key=lambda e: e.p_value)

    threshold_k = 0
    for k, e in enumerate(sorted_by_p, 1):
        if e.p_value <= fdr_alpha * k / m:
            threshold_k = k

    return sorted_by_p[:threshold_k] if threshold_k > 0 else []
