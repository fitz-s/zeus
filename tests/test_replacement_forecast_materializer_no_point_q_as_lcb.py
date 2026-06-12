# Created: 2026-06-09
# Last reused or audited: 2026-06-12
# Authority basis: /tmp/qlcb_coverage_fix_report.md — the materializer NO LONGER leaves q_lcb NULL
#   on the soft-anchor (no-fusion) path. The Wilson-over-AIFS-member-votes bound is promoted into
#   materialization (single-authority law), so a posterior carries a non-NULL q_lcb with the
#   "wilson_aifs_member_votes" basis whenever the fused-center bootstrap did not run. The OLD law
#   ("leave q_lcb NULL when only point q exists") is REVOKED; the relationship that survives is the
#   no_point_q_as_lcb invariant (q_lcb is a genuine bound, never the q point) — see
#   tests/test_replacement_materializer_soft_anchor_qlcb.py for the live coverage of that invariant.
"""The point q is NEVER copied into q_lcb (the surviving invariant after the NULL law was revoked)."""
from src.data.replacement_forecast_materializer import (
    _QLCB_SOFT_ANCHOR_BASIS,
    _build_soft_anchor_wilson_lcb,
)


def test_soft_anchor_qlcb_is_a_bound_not_the_point_q() -> None:
    # Even on the soft-anchor (no-fusion) path the materializer now writes a q_lcb, but it is the
    # Wilson member-vote LOWER bound — strictly <= the point q per bin, never the point q itself.
    aifs = {"a": 0.20, "b": 0.50, "c": 0.30}
    q_point = {"a": 0.22, "b": 0.48, "c": 0.30}
    lcb = _build_soft_anchor_wilson_lcb(aifs_probabilities=aifs, member_count=51, q_point=q_point)
    assert _QLCB_SOFT_ANCHOR_BASIS == "wilson_aifs_member_votes"
    for bin_id, q_pt in q_point.items():
        assert lcb[bin_id] <= q_pt + 1e-12
        # And it is NOT a verbatim copy of the point q (the disease this test originally guarded).
        assert not (abs(lcb[bin_id] - q_pt) < 1e-9 and q_pt > 0.0)
