# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC re-audit Blocker B7 (2026-05-28). When the stored
#   gate_set_hash on disk differs from the current `current_gate_set_hash()`, the
#   selective driver currently returns `set(all_cohorts)` — silently degrading
#   selective rebuild into a full reproduce. Operator: this is wrong. A gate change
#   means the ROW_ACTION_MANIFEST was generated under a superseded gate set; the
#   replay-equivalence + B/E/A_failed classification it carries is no longer
#   trustworthy. Caller must regenerate the manifest under the current gate first,
#   then re-run selective. compute_final_regen must raise (abort), not silently
#   return all_cohorts.
"""B7 — gate_changed must abort selective rebuild, never silently full-regen."""
from __future__ import annotations

import pytest


def test_gate_changed_raises_systemexit():
    """compute_final_regen(rows, replay_results, gate_changed=True) MUST raise
    SystemExit so the caller stops and regenerates the manifest under the current
    gate. Returning `set(all_cohorts)` is a silent degradation and is the wrong
    semantics — the manifest's row classifications were computed under the OLD
    gate and are stale.

    Pre-fix RED: selective_refit_from_manifest.compute_final_regen:202-206 returns
    `set(all_cohorts)` without raising.
    """
    from scripts.selective_refit_from_manifest import compute_final_regen

    rows = [
        {"city": "Atlanta", "season": "MAM", "metric": "high",
         "action": "A_REUSE_PENDING_REPLAY"},
        {"city": "Buenos Aires", "season": "JJA", "metric": "high",
         "action": "B_REFIT_AND_REGEN_COHORT"},
    ]
    with pytest.raises(SystemExit):
        compute_final_regen(rows, {}, gate_changed=True)


def test_gate_changed_message_directs_to_manifest_regen():
    """The SystemExit message must explicitly direct the operator to regenerate
    the manifest. A bare `raise SystemExit(1)` leaves the operator guessing.
    """
    from scripts.selective_refit_from_manifest import compute_final_regen

    rows = [
        {"city": "Atlanta", "season": "MAM", "metric": "high",
         "action": "A_REUSE_PENDING_REPLAY"},
    ]
    with pytest.raises(SystemExit) as exc_info:
        compute_final_regen(rows, {}, gate_changed=True)
    text = str(exc_info.value).lower()
    assert "manifest" in text or "regenerate" in text or "gate" in text, (
        f"SystemExit message must reference manifest regeneration / gate change; got {exc_info.value!r}"
    )


def test_gate_unchanged_returns_selective_subset_only():
    """Sanity: gate_changed=False keeps selective semantics — B∪E∪A_failed only,
    NOT all cohorts. Establishes the contrast with the gate_changed branch.
    """
    from scripts.selective_refit_from_manifest import compute_final_regen

    rows = [
        {"city": "Atlanta", "season": "MAM", "metric": "high",
         "action": "A_REUSE_PENDING_REPLAY"},   # passed → reuse, NOT regen
        {"city": "Buenos Aires", "season": "JJA", "metric": "high",
         "action": "B_REFIT_AND_REGEN_COHORT"},  # always regen
        {"city": "Cape Town", "season": "MAM", "metric": "high",
         "action": "D_MONTH_SCOPE"},             # fit-only, not regen
    ]
    replay_results = {("Atlanta", "MAM", "high"): True}
    regen = compute_final_regen(rows, replay_results, gate_changed=False)
    assert regen == {("Buenos Aires", "JJA", "high")}, (
        f"selective regen must be B∪E∪A_failed; got {regen}"
    )
