# Phase C activation-flag unlock criteria

**Authority**: this document.  Replaces the "operator-paced" framing in `docs/runbooks/live-operation.md` §"Recommended flip order" with **evidence-gated** unlock.

**Scope**: the three activation flags wired in PR #47 Phase C —
`ZEUS_ENTRY_FORECAST_READINESS_WRITER`,
`ZEUS_ENTRY_FORECAST_ROLLOUT_GATE`,
`ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS`.

**Default state (post-2026-05-04 operator authorization)**:
- `_READINESS_WRITER` and `_ROLLOUT_GATE` default **ON** at the
  predicate level. Operator emergency kill-switch: set the env var
  to literal `"0"` to disable that flag without redeploy.
- `_HEALTHCHECK_BLOCKERS` remains default **OFF** until 24h of
  flags 1+2 stable observation per §Flag 3 below.

**Created**: 2026-05-04. **Last reused/audited**: 2026-05-04.

---

## Why evidence-gated and not operator-paced

The runbook used to read "flip when ready". Operators read that as "today is good enough". Two failure modes follow:
1. Flag flipped before the daemon-side wiring is exercised against any state at all → the first cycle after the flip is the first execution.
2. Flag flipped while a co-tenant change has rotted the assumed call site → silent fail-OPEN.

The fix is a thermodynamic-translation-loss countermeasure (CLAUDE.md §2): encode the unlock as **artifacts on disk** that ship with the flip commit. A flip is unauthorized unless the artifacts exist, are dated within the last 7 days, and pass the relationship tests.

---

## Producer

```bash
python scripts/produce_activation_evidence.py --all \
  --out-dir evidence/activation/ \
  --evidence state/entry_forecast_promotion_evidence.json
```

Each invocation writes a date-stamped artifact per flag plus a `<date>_summary.md` aggregator. Artifacts stay in `evidence/activation/` (under git) so the flip commit can reference them.

The producer is a thin wrapper around `tests/test_activation_flag_combinations.py`. Both are authoritative — the test file pins the relationship invariants; the producer captures the per-environment dry-run snapshot.

---

## Per-flag unlock checklist

Symbol: ✅ = required + currently green ; ⏳ = required + operator-runnable today ; 📅 = required + bound to observation period.

### Flag 1 — `ZEUS_ENTRY_FORECAST_READINESS_WRITER` (flip first per runbook)

| Evidence | Spec |
|---|---|
| ✅ `tests/test_entry_forecast_evaluator_cutover.py::test_phase_c3_writer_flag_off_does_not_write_entry_readiness` | Default-OFF byte-equal to pre-Phase-C |
| ✅ `tests/test_entry_forecast_evaluator_cutover.py::test_phase_c3_writer_flag_on_writes_blocked_row_when_evidence_missing` | First-flip fail-closed contract |
| ✅ `tests/test_entry_forecast_evaluator_cutover.py::test_phase_c3_writer_flag_on_writes_live_eligible_when_all_gates_align` | All-gates-aligned reachability |
| ✅ `tests/test_activation_flag_combinations.py::test_inv_a_flag2_alone_writes_blocked_when_evidence_missing` | Out-of-order flip 2-only safe |
| ✅ `tests/test_activation_flag_combinations.py::test_inv_a_flags_1_and_2_on_no_evidence_both_sites_fail_closed` | Two-call-site convergence |
| ⏳ `evidence/activation/<date>_c3_writer.sql` | Producer artifact: writer dry-run row dump |
| ⏳ Verdict `ready_to_flip=True` in `<date>_summary.md` | Producer summary |

**Sufficient set**: all five tests green AND producer-summary `c3.ready_to_flip=True` within last 7 days.

**Forbidden states for flip**:
- producer `rows_written=0` (writer skipped)
- producer `row_status` unrecognized (neither `BLOCKED` nor `LIVE_ELIGIBLE`)
- any test in `tests/test_activation_flag_combinations.py` red

### Flag 2 — `ZEUS_ENTRY_FORECAST_ROLLOUT_GATE` (flip second per runbook)

| Evidence | Spec |
|---|---|
| ✅ `tests/test_entry_forecast_evaluator_cutover.py::test_phase_c1_flag_off_preserves_legacy_rollout_blocker` | Default-OFF byte-equal |
| ✅ `tests/test_entry_forecast_evaluator_cutover.py::test_phase_c1_flag_on_blocks_when_evidence_missing` | EVIDENCE_MISSING surfaced |
| ✅ `tests/test_entry_forecast_evaluator_cutover.py::test_phase_c1_flag_on_surfaces_corruption_as_explicit_blocker` | EVIDENCE_CORRUPT typed |
| ✅ `tests/test_entry_forecast_evaluator_cutover.py::test_phase_c1_flag_on_passes_with_complete_evidence` | Full-evidence pass-through |
| ✅ `tests/test_entry_forecast_evaluator_cutover.py::test_phase_c1_flag_on_blocks_when_evidence_lacks_canary_success` | Canary-success required |
| ✅ `tests/test_activation_flag_combinations.py::test_inv_a_flag1_alone_blocks_when_evidence_missing` | Out-of-order flip 1-only safe |
| ✅ `tests/test_activation_flag_combinations.py::test_inv_b_flag1_corrupt_evidence_typed_blocker` | Cycle-resilient corruption handling |
| ✅ `tests/test_activation_flag_combinations.py::test_inv_c_evidence_file_rotation_invalidates_cache` | mtime-based cache invalidation |
| ✅ `tests/test_activation_flag_combinations.py::test_inv_c_rollout_gate_sees_rotated_evidence_without_explicit_cache_clear` | Rotation visible without manual cache-clear |
| ⏳ `state/entry_forecast_promotion_evidence.json` populated with **real** `operator_approval_id`, `g1_evidence_id`, `canary_success_evidence_id` | Operator runs the runbook recipe |
| ⏳ `evidence/activation/<date>_c1_rollout_gate.txt` | Producer artifact: blocker code dump |
| ⏳ Verdict `ready_to_flip=True` in `<date>_summary.md` | Producer summary |

**Sufficient set**: all 9 tests green AND populated `state/entry_forecast_promotion_evidence.json` AND producer-summary `c1.ready_to_flip=True` within last 7 days.

**Forbidden states for flip**:
- producer `blocker_code` starts with `ENTRY_FORECAST_PROMOTION_EVIDENCE_CORRUPT:` (operator must repair the file before flip)
- producer `evidence_present=False` while operator's stated intent is to enforce the gate (this would mean the gate would block ALL live entry-forecast on first cycle — flip during off-hours only)
- INV-C tests red (mtime cache invalidation broken; daemon would silently see stale evidence)

### Flag 3 — `ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS` (flip third per runbook)

| Evidence | Spec |
|---|---|
| ✅ `tests/test_healthcheck.py::test_phase_c4_flag_off_healthy_unaffected_by_entry_forecast_blockers` | Default-OFF preserves legacy GREEN |
| ✅ `tests/test_healthcheck.py::test_phase_c4_flag_on_healthy_false_when_entry_forecast_blocked` | Flag ON pulls False on blocker |
| ✅ `tests/test_activation_flag_combinations.py::test_inv_d_healthcheck_flag_predicate_independent_of_writer_flag` | Cross-flag isolation pinned |
| 📅 ≥24h with flags 1+2 ON, no `RISKGUARD HALT`, daemon heartbeat fresh | Observation period |
| ⏳ `evidence/activation/<date>_c4_healthcheck_diff.txt` showing `healthy_when_off ≠ healthy_when_on` | Producer artifact: predicate diff |
| ⏳ Verdict `ready_to_flip=True` in `<date>_summary.md` | Producer summary |

**Sufficient set**: all 3 tests green AND ≥24h flags-1+2-stable observation AND producer-summary `c4.ready_to_flip=True` within last 7 days AND `c4.healthy_when_off != c4.healthy_when_on` (otherwise the flip is a no-op).

**Forbidden states for flip**:
- `healthy_when_off == healthy_when_on` — flip would do nothing (operator should wait for at least one blocker to be observed)
- flags 1 or 2 not yet ON — surfacing blockers without the writer/gate live just produces dashboard alarms with no underlying state to act on

---

## Audit trail

Every flip commit MUST include in its body:

```
Activation flip: ZEUS_ENTRY_FORECAST_<NAME>=1
Evidence: evidence/activation/<date>_summary.md (c<N>.ready_to_flip=true)
Tests:    <list of test paths from "Sufficient set" above>
Observation: <if 📅 required, summary of 24h window>
```

If the commit body lacks any of the three labelled lines, the flip is unauthorized and must be reverted on next cycle.

---

## Refresh policy

- Evidence artifacts older than 7 days are stale. Re-run the producer.
- If `tests/test_activation_flag_combinations.py` adds invariants (INV-F, INV-G, ...), this doc's per-flag tables must be updated in the same commit.
- This doc is referenced from the runbook; runbook updates that change flip order MUST cite this doc.

---

## Related

- Producer script: `scripts/produce_activation_evidence.py`
- Relationship tests: `tests/test_activation_flag_combinations.py`
- Per-flag unit tests: `tests/test_entry_forecast_evaluator_cutover.py`, `tests/test_healthcheck.py`
- Runbook: `docs/runbooks/live-operation.md` §"Phase C: live entry-forecast activation flags"
- Originating plan: `docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md` Phase C
