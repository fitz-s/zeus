# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: /tmp/qlcb_coverage_fix_report.md — promote the Wilson-over-AIFS-member-votes
#   q_lcb bound into the materializer so NO posterior is born with a NULL q_lcb. Relationship
#   tests for the cross-module invariants: (1) the materializer Wilson bound is BYTE-IDENTICAL to
#   the live decision-path Wilson (single-authority), (2) the bound is a TRUE lower bound (<= q
#   point, >= 0), (3) the provenance basis DISTINGUISHES the soft-anchor bound from the certified
#   fused-center bootstrap bound so the calibration-credential reader cannot alias them, (4) the
#   credential reader treats the soft-anchor basis as NON-bootstrap (no auto-promotion).
"""Soft-anchor (no-fusion) q_lcb fallback — relationship + unit tests."""
from __future__ import annotations

import math

import pytest

from src.data.replacement_forecast_materializer import (
    _QLCB_BASIS,
    _QLCB_SOFT_ANCHOR_BASIS,
    _build_soft_anchor_wilson_lcb,
    _wilson_lower_bound,
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
