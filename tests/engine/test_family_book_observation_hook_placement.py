# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO (HIGH finding "population-bias": several
#   actionability paths reset _spine_fact_decision = None before the OLD
#   pre-branch hook, silently dropping a decision that existed earlier in the
#   retry sequence from the evidence population).
"""Hook-placement regression test for the capture-at-decision-production fix.

KNOWN, DISCLOSED SCOPE: this is a source-position proof, not a full dynamic
execution of `_build_event_bound_no_submit_receipt_core`'s three actionability
branches end-to-end. That was attempted and abandoned as out of reasonable
scope for this PR: reaching the retry loop requires satisfying (or
monkeypatching around) `EventBoundDecisionEngine.evaluate`,
`_forecast_lane_phase_admits` / `_edli_forecast_lane_phase_evidence`, real
`row`/`proofs` construction, and more gates each revealing another --
comparable in scale to the codebase's existing 1000+ line reactor test files
(tests/engine/test_s3_native_side_candidate_materialization.py,
tests/engine/test_s6_submit_recapture_gate.py). Reconstructing that scaffold
risked introducing its own bugs for a telemetry-only capture path. This test
instead proves the PRECISE structural property the fix requires by source
position -- which module the review itself accepts as a real (if partial)
signal, paired with the fully-executed unit tests in
tests/events/test_family_book_telemetry_writer.py that prove the actual
enqueue/sampling/write behavior with real code execution.
"""
from __future__ import annotations

import inspect

import src.engine.event_reactor_adapter as era


def test_capture_precedes_every_actionability_veto_reset_point():
    """enqueue_family_book_observation must run at the moment _spine_fact_decision
    is PRODUCED, strictly before any of the three later veto checks that can
    reset it to None within the SAME retry-loop iteration (near-day0 qkernel,
    rest-then-cross not-actionable, same-token fill-up exclusion) -- otherwise
    a decision that existed here disappears from the evidence population
    exactly as the deep review found."""
    source = inspect.getsource(era._build_event_bound_no_submit_receipt_core)

    production_marker = "_spine_fact_decision = _spine_result.decision"
    capture_call = "enqueue_family_book_observation("
    veto_markers = (
        "_near_day0_qkernel_reason = (",
        "_rest_then_cross_not_actionable_reason = (",
        "_qkernel_same_token_fill_up_selection_rejection_reason(",
    )

    assert production_marker in source
    assert capture_call in source
    production_idx = source.index(production_marker)
    capture_idx = source.index(capture_call, production_idx)
    # Capture sits immediately at production, strictly before every veto marker
    # that follows it in the same loop body.
    assert production_idx < capture_idx
    for marker in veto_markers:
        assert marker in source
        veto_idx = source.index(marker, production_idx)
        assert capture_idx < veto_idx, (
            f"capture call must precede veto check {marker!r} -- otherwise a "
            "decision vetoed later in this same retry iteration would never "
            "have been captured (the population-bias bug this fix closes)"
        )


def test_capture_call_passes_the_active_retry_scoped_proof_set():
    """The manifest must be built from the proofs ACTIVE at production time
    (_active_spine_entry_proofs), not the full unfiltered `proofs` -- this is
    what family_book_builder itself was bound to for this exact retry
    iteration (src/engine/qkernel_spine_bridge.py route_proofs = tuple(
    selection_proofs) if selection_proofs is not None else belief_proofs)."""
    source = inspect.getsource(era._build_event_bound_no_submit_receipt_core)
    capture_idx = source.index("enqueue_family_book_observation(")
    call_body = source[capture_idx:capture_idx + 400]
    assert "active_proofs=_active_spine_entry_proofs" in call_body
    assert "candidate_bin_id=_candidate_bin_id" in call_body
    assert "decision=_spine_result.decision" in call_body
