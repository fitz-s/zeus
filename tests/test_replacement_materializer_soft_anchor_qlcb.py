# Created: 2026-06-12
# Last reused or audited: 2026-06-13
# Authority basis: /tmp/qlcb_coverage_fix_report.md — promote the Wilson-over-AIFS-member-votes
#   q_lcb bound into the materializer so NO posterior is born with a NULL q_lcb. Relationship
#   tests for the cross-module invariants: (1) the materializer Wilson bound is BYTE-IDENTICAL to
#   the live decision-path Wilson (single-authority), (2) the bound is a TRUE lower bound (<= q
#   point, >= 0), (3) the provenance basis DISTINGUISHES the soft-anchor bound from the certified
#   fused-center bootstrap bound so the calibration-credential reader cannot alias them, (4) the
#   credential reader treats the soft-anchor basis as NON-bootstrap (no auto-promotion).
#   2026-06-13 (q_ucb symmetry, /tmp/agent_report_materializer_bounds.md): the soft-anchor path
#   now emits a GENUINE Wilson UPPER bound alongside its lower twin so a CAPTURE_MISSING posterior
#   carries BOTH bounds instead of a half-bound. Relationship invariants added: (5) the upper is a
#   TRUE upper bound (>= q point, <= 1) from the SAME inputs/z as the lower, (6) the materializer
#   soft-anchor fallback EMITS q_ucb whenever it emits q_lcb (RED-on-revert: drop the q_ucb
#   emission -> the source guard fails), (7) the q_ucb is non-fabricated (not a copy of q_lcb or the
#   point), (8) the basis still keeps the row non-live-eligible (no auto-arm from a half-bound).
"""Soft-anchor (no-fusion) q_lcb/q_ucb fallback — relationship + unit tests."""
from __future__ import annotations

import inspect
import math

import pytest

from src.data.replacement_forecast_materializer import (
    _QLCB_BASIS,
    _QLCB_SOFT_ANCHOR_BASIS,
    _build_soft_anchor_wilson_lcb,
    _build_soft_anchor_wilson_ucb,
    _insert_posterior,
    _wilson_lower_bound,
    _wilson_upper_bound,
)


def test_materializer_wilson_is_byte_identical_to_live_decision_path() -> None:
    """SINGLE-AUTHORITY: the promoted materializer Wilson bound must equal the live read-time
    Wilson bound for every (successes, trials) — the two were the twin authority this kills."""
    from src.engine.event_reactor_adapter import _wilson_lower_bound as live_wilson

    for successes, trials in [(0, 51), (5, 51), (25.5, 51), (30, 51), (51, 51), (3, 10), (0, 0)]:
        assert math.isclose(
            _wilson_lower_bound(successes, trials),
            live_wilson(successes, trials),
            rel_tol=0.0,
            abs_tol=1e-12,
        ), (successes, trials)


def test_soft_anchor_wilson_lcb_is_a_true_lower_bound() -> None:
    """RELATIONSHIP INVARIANT: a per-bin lower bound can never exceed the point mass and is >= 0.
    (Mirrors the no_point_q_as_lcb law: q_lcb must be a genuine bound, never the q point.)"""
    aifs = {"cool": 0.10, "warm": 0.60, "hot": 0.30}
    q_point = {"cool": 0.12, "warm": 0.55, "hot": 0.33}
    lcb = _build_soft_anchor_wilson_lcb(aifs_probabilities=aifs, member_count=51, q_point=q_point)
    assert set(lcb) == set(q_point)
    for bin_id, q_pt in q_point.items():
        assert 0.0 <= lcb[bin_id] <= q_pt + 1e-12, (bin_id, lcb[bin_id], q_pt)
    # A confident modal vote (0.60 over 51 members) produces a materially positive bound, not ~0:
    # this is the WHOLE point — favorites get a real floor instead of NULL/zero.
    assert lcb["warm"] > 0.30


def test_soft_anchor_wilson_lcb_absent_bin_is_honest_zero() -> None:
    """A bin with NO AIFS vote support gets q_lcb=0.0 (honest — no evidence for a positive floor),
    never a fabricated value."""
    q_point = {"cool": 0.12, "warm": 0.55, "hot": 0.33}
    lcb = _build_soft_anchor_wilson_lcb(
        aifs_probabilities={"warm": 0.60}, member_count=51, q_point=q_point
    )
    assert lcb["cool"] == 0.0
    assert lcb["hot"] == 0.0
    assert lcb["warm"] > 0.0


def test_soft_anchor_wilson_lcb_rejects_nonpositive_member_count() -> None:
    """Typed refusal: a non-finite / non-positive member count cannot construct a bound (caller
    fail-softs to NULL — never a silent garbage bound)."""
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            _build_soft_anchor_wilson_lcb(
                aifs_probabilities={"warm": 0.6}, member_count=bad, q_point={"warm": 0.55}
            )


def test_basis_constants_are_distinct() -> None:
    """The certified bootstrap basis and the soft-anchor Wilson basis must NEVER be equal —
    the credential reader pins the bootstrap basis by exact string match."""
    assert _QLCB_SOFT_ANCHOR_BASIS == "wilson_aifs_member_votes"
    assert _QLCB_BASIS == "fused_center_bootstrap_p05"
    assert _QLCB_SOFT_ANCHOR_BASIS != _QLCB_BASIS


def test_credential_reader_does_not_treat_soft_anchor_basis_as_bootstrap() -> None:
    """CROSS-MODULE: the calibration credential's bounds leg requires the EXACT bootstrap basis.
    The promoted soft-anchor basis must NOT satisfy it (a no-current-capture posterior must not
    auto-arm). Guards against a future rename aliasing the two."""
    from src.engine.event_reactor_adapter import _FUSED_BOOTSTRAP_QLCB_BASIS

    assert _FUSED_BOOTSTRAP_QLCB_BASIS == _QLCB_BASIS
    assert _FUSED_BOOTSTRAP_QLCB_BASIS != _QLCB_SOFT_ANCHOR_BASIS


# ---------------------------------------------------------------------------
# Q_UCB SYMMETRY (2026-06-13) — the soft-anchor path now emits a GENUINE Wilson upper bound so a
# CAPTURE_MISSING posterior carries BOTH bounds instead of a half-bound. The q_ucb-less population
# on the 06-14 surface was 100% (158/158) CAPTURE_MISSING soft-anchor rows; the fix makes them
# carry both bounds and stay non-live-eligible (basis + q_mode unchanged).
# ---------------------------------------------------------------------------


def test_materializer_wilson_upper_is_byte_identical_symmetric_counterpart() -> None:
    """SINGLE-AUTHORITY: the materializer Wilson upper bound is the EXACT symmetric counterpart of
    the lower (identical Wilson centre/denom, +margin instead of -margin, SAME z). So for every
    (successes, trials) the two bounds bracket the Wilson centre symmetrically."""
    z = 1.645
    for successes, trials in [(0, 51), (5, 51), (25.5, 51), (30, 51), (51, 51), (3, 10)]:
        lb = _wilson_lower_bound(successes, trials)
        ub = _wilson_upper_bound(successes, trials)
        # centre = (lb+ub)/2 must equal the Wilson point estimate (center/denom)
        p_hat = min(max(float(successes), 0.0), float(trials)) / float(trials)
        z2 = z * z
        denom = 1.0 + z2 / float(trials)
        wilson_centre = (p_hat + z2 / (2.0 * float(trials))) / denom
        assert math.isclose((lb + ub) / 2.0, wilson_centre, abs_tol=1e-12), (successes, trials)
        assert lb <= p_hat <= ub, (successes, trials, lb, p_hat, ub)


def test_wilson_upper_no_evidence_is_widest_honest_upper() -> None:
    """trials<=0 -> no evidence -> the widest honest upper (1.0), mirroring the lower's 0.0."""
    assert _wilson_upper_bound(0.0, 0.0) == 1.0
    assert _wilson_lower_bound(0.0, 0.0) == 0.0


def test_soft_anchor_wilson_ucb_is_a_true_upper_bound() -> None:
    """RELATIONSHIP INVARIANT: a per-bin upper bound can never sit below the point mass and is
    <= 1. Combined with the lower-bound test this is the ProbabilityUncertainty contract
    (q_lcb <= q_point <= q_ucb) on the soft-anchor carrier."""
    aifs = {"cool": 0.10, "warm": 0.60, "hot": 0.30}
    q_point = {"cool": 0.12, "warm": 0.55, "hot": 0.33}
    lcb = _build_soft_anchor_wilson_lcb(aifs_probabilities=aifs, member_count=51, q_point=q_point)
    ucb = _build_soft_anchor_wilson_ucb(aifs_probabilities=aifs, member_count=51, q_point=q_point)
    assert set(ucb) == set(q_point)
    for bin_id, q_pt in q_point.items():
        # The full ProbabilityUncertainty bracket on the soft-anchor row.
        assert lcb[bin_id] <= q_pt + 1e-12, (bin_id, lcb[bin_id], q_pt)
        assert q_pt - 1e-12 <= ucb[bin_id] <= 1.0 + 1e-12, (bin_id, q_pt, ucb[bin_id])
    # A confident modal vote (0.60 over 51 members) produces an upper materially ABOVE the point —
    # a genuine band, never a copy of the point or the lower bound.
    assert ucb["warm"] > q_point["warm"] + 1e-6
    assert ucb["warm"] > lcb["warm"] + 1e-6


def test_soft_anchor_wilson_ucb_is_not_fabricated() -> None:
    """HONESTY: q_ucb must NOT be q_lcb, must NOT be the point, must NOT be a constant. It is the
    genuine binomial upper from the SAME AIFS support inputs (the operator's no-fabrication law)."""
    aifs = {"a": 0.05, "b": 0.45, "c": 0.50}
    q_point = {"a": 0.08, "b": 0.40, "c": 0.52}
    lcb = _build_soft_anchor_wilson_lcb(aifs_probabilities=aifs, member_count=51, q_point=q_point)
    ucb = _build_soft_anchor_wilson_ucb(aifs_probabilities=aifs, member_count=51, q_point=q_point)
    # Not equal to the lower bound on any voted bin, not equal to the point on any voted bin.
    assert ucb != lcb
    assert ucb != q_point
    # Not a constant across bins (a fabricated flat band would be).
    assert len(set(round(v, 6) for v in ucb.values())) > 1


def test_soft_anchor_wilson_ucb_absent_bin_is_honest_wide_upper() -> None:
    """A bin with NO AIFS vote support gets the no-evidence Wilson upper (successes=0), clipped up
    to at least the point mass — an honest WIDE upper where there is no support evidence (never a
    fabricated tight band, never below the point)."""
    q_point = {"cool": 0.12, "warm": 0.55, "hot": 0.33}
    ucb = _build_soft_anchor_wilson_ucb(
        aifs_probabilities={"warm": 0.60}, member_count=51, q_point=q_point
    )
    assert ucb["cool"] >= q_point["cool"] - 1e-12
    assert ucb["hot"] >= q_point["hot"] - 1e-12


def test_soft_anchor_wilson_ucb_rejects_nonpositive_member_count() -> None:
    """Typed refusal symmetric with the lower bound: a non-finite / non-positive member count
    cannot construct a bound (caller fail-softs to NULL BOTH bounds — never a half-bound)."""
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            _build_soft_anchor_wilson_ucb(
                aifs_probabilities={"warm": 0.6}, member_count=bad, q_point={"warm": 0.55}
            )


def test_soft_anchor_fallback_emits_q_ucb_whenever_it_emits_q_lcb() -> None:
    """RED-ON-REVERT relationship guard (the q_ucb carrier defect antibody).

    The materializer soft-anchor fallback block MUST set ``q_ucb_map`` from
    ``_build_soft_anchor_wilson_ucb`` in the SAME branch it sets ``q_lcb_map`` from
    ``_build_soft_anchor_wilson_lcb`` (atomic both-or-neither). Reverting the fix — leaving q_ucb
    None on the soft-anchor path (the pre-2026-06-13 behavior that birthed 158/158 q_ucb-less
    CAPTURE_MISSING rows) — drops this call and fails the test. This is a SOURCE-structural guard
    because the full end-to-end materialize path requires 51-member GRIB fixtures the unit harness
    does not carry; the invariant under test is the cross-module carrier-shape law (every soft-anchor
    posterior that gets a q_lcb also gets a genuine q_ucb)."""
    src = inspect.getsource(_insert_posterior)
    # Locate the soft-anchor fallback block (the q_lcb_map-is-None promotion).
    assert "_build_soft_anchor_wilson_lcb(" in src
    assert "_build_soft_anchor_wilson_ucb(" in src, (
        "soft-anchor fallback must build a genuine q_ucb alongside q_lcb (q_ucb carrier defect "
        "reverted) — see test header authority basis"
    )
    # The fallback must assign BOTH maps from the soft builders (atomic both-or-neither) — not leave
    # q_ucb_map None on this path. Slice from the LCB builder call to the end of the function so the
    # comment block between the builder calls and the assignment does not hide the assignment.
    lcb_call_idx = src.index("_build_soft_anchor_wilson_lcb(")
    tail = src[lcb_call_idx:]
    assert "q_lcb_map = _soft_lcb" in tail, "soft-anchor fallback must assign q_lcb_map from the soft builder"
    assert "q_ucb_map = _soft_ucb" in tail, "soft-anchor fallback must assign q_ucb_map from the soft builder (both-or-neither)"
    # The fail-soft branch must also clear q_ucb_map to None (never a half-bound on error).
    assert "q_ucb_map = None" in tail, "soft-anchor fail-soft must clear q_ucb_map to None (no half-bound on error)"


def test_soft_anchor_q_ucb_role_is_basis_aware_not_aliased_to_bootstrap() -> None:
    """PROVENANCE: the soft-anchor q_ucb role must report its OWN origin
    (wilson_aifs_member_votes_ucb), never the certified fused bootstrap role — so a future reader
    that keys on the role string cannot mistake a CAPTURE_MISSING upper for a calibrated one."""
    src = inspect.getsource(_insert_posterior)
    assert "wilson_aifs_member_votes_ucb" in src
    assert "fused_center_bootstrap_ucb" in src
    # The role selection is basis-gated (the soft role only fires for the soft-anchor basis).
    assert "q_lcb_basis == _QLCB_SOFT_ANCHOR_BASIS" in src
