# LIFECYCLE structural findings — INVESTIGATION COMPLETE, FIX DEFERRED

**Status**: DEFERRED to a separate PR. Audit framing for F108/F102 was incorrect; the real structural smell is narrower but requires touching `src/execution/exit_lifecycle.py` which is hard-excluded from WAVE-2 (live cascade surface).

**Date**: 2026-05-17
**Investigator**: sonnet executor (agent a663e09171e91998f), 10 min wall clock
**Authority basis**: MASS_TRIAGE_2026-05-17.md cluster (F102, F106, F108, F110)

## Audit reframes (premise overturned)

### F108 — "London stuck in pending_exit" was WRONG
- Actual stuck positions: **Miami + Buenos Aires** (not London)
- Both are **dust-held BY DESIGN** with `backoff_exhausted` set
- Transitions ARE logged correctly to `position_events`
- London positions are **actively retrying** — not stuck
- Verdict: **RETRACT** the F108 framing; the design intent (dust holding) is working as specified

### F102 — "temp_persistence empty/missing" was IMPRECISE
- Table EXISTS in schema
- 0 rows present
- Empty ≠ missing; needs to determine whether the writer is supposed to populate it
- Verdict: **REFRAME** as "is temp_persistence intentionally empty or is there a dead writer?" — separate trace needed

## Real structural smell (CONFIRMED)

**Smell**: 6 `_mark_pending_exit` call sites lack a paired DB event write in the same scope.

| Line | Risk | Notes |
|---|---|---|
| 396 | HIGH | unpaired event |
| 414 | LOW | caller writes broader event with `exit_state` in payload — works by accident |
| 750 | MED | unpaired |
| 1286 | HIGH | unpaired |
| 1593 | HIGH | unpaired |
| 1611 | HIGH | unpaired |

**K decision**: phase mutations happen in multiple sites without a unified transition writer enforcing `(phase_before, phase_after, event_type)` row generation per mutation.

**Proposed structural fix** (out-of-scope for WAVE-2):
- Centralize phase mutations behind a single `transition_phase(pos_id, from, to, event_type, evidence)` helper that atomically writes both the phase column AND a position_events row
- Refactor 6 call sites to use the helper
- Add antibody invariant: every phase change in test fixtures has a corresponding event row

## Karachi safety

Karachi position `c30f28a5-d4e`: `day0_window` phase, `synced`, untouched by this defect cluster. The 6 unprotected `_mark_pending_exit` sites do NOT execute on Karachi's current state.

## Why deferred

1. Fix surface is `src/execution/exit_lifecycle.py` — hard-excluded from WAVE-2 (live cascade)
2. Audit framing was wrong on F108/F102 — needs fresh investigation before fix, not implementation from stale audit
3. 6-call-site refactor exceeds the 2-file-budget heuristic for safe sweeper dispatch
4. No live blast radius — dust-held positions are working as designed

**Track separately as**: WAVE-3 lifecycle unification PR (post WAVE-2 merge).
