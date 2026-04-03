# architects_task.md

## Purpose

This file is the **active execution control surface** for the current Architects packet.

It is for:
- current packet identity
- owner and lane structure
- exact allowed / forbidden edit boundaries
- execution checklist
- stop conditions
- deliverables
- immediate next action

It is **not** the durable historical ledger. Historical state belongs in `architects_progress.md`.

---

## Maintenance Rules

- Keep exactly one packet marked as `ACTIVE` unless a leader explicitly declares a controlled fork.
- When the active packet finishes, update `architects_progress.md` first, then roll this file forward.
- Do not place broad speculative roadmaps here.
- Do not let subagents widen scope beyond the packet boundary frozen by the leader.
- If scope changes materially, freeze a new packet instead of silently mutating this file.

---

## Program Workflow

1. Close the tribunal / current-phase `P-*` governance and enforcement packets. ✅
2. Confirm current-phase authority install is complete without claiming runtime convergence. ✅
3. Write the foundation-mainline architecture plan. ✅
4. Prepare and open team execution after that plan is frozen. ⏭ later, after `FOUNDATION-TEAM-GATE`
5. Use the foundation mainline to move Zeus from `hardened_transition` -> `governed_runtime` -> `mature_project`.

Current completion ladder:
- `current-phase complete` = tribunal / `P-*` packet family closed ✅
- `foundation-planned` = architecture mainline packet map and automation plan frozen ✅
- `team-ready` = team entry rules, staffing plan, and verification path frozen after current-phase closure
- `governed_runtime complete` = canonical authority + machine gates + strategy-aware protection landed
- `mature_project complete` = shadow persistence demoted/removed and replay/parity can block regressions

Closed current-phase queue:
1. `P-BOUND-01` ✅
2. `P-ROLL-01` ✅
3. `P-STATE-01` ✅
4. `P-OPS-01` ✅

---

## Current Active Packet

### Packet
`P0.2-ATTRIBUTION-FREEZE`

### State
`ACCEPTED LOCALLY / READY TO COMMIT`

### Execution mode verdict
`RALPH_NOW`

### Objective
Freeze attribution in the current runtime entry path by having the evaluator emit canonical `strategy_key` directly, preserve it through touched runtime records, and reject missing or malformed attribution instead of downstream invention.

### Why this packet is next
- `FOUNDATION-MAINLINE-PLAN` is complete and accepted
- foundation spec `P0 sequence` starts with `P0.2 attribution freeze`
- bearing-capacity work must precede more exciting P1/P2/P3 work

### Owner model
- Required: one named execution owner for `P0.2-ATTRIBUTION-FREEZE`
- Tribunal/principal architect remains the scope-freezing authority
- Verifier remains independent runtime/evidence reviewer
- Critic remains contradiction / blast-radius reviewer

### Planning lane baseline
- leader: `gpt-5.4 xhigh`
- scouts: `gpt-5.3-codex-spark low` read-only
- verifier: `gpt-5.4-mini high`

### Current execution owner
- execution owner: `Architects local lead (current Codex session)`

### Allowed edit surface
Only the following may be edited in this packet:
- `src/engine/evaluator.py`
- `src/engine/cycle_runtime.py`
- `src/state/portfolio.py`
- `src/state/decision_chain.py`
- `tests/test_runtime_guards.py`
- `tests/test_architecture_contracts.py`
- `architects_progress.md`
- `architects_task.md`

### Forbidden edit surface
Explicitly forbidden for edits in this packet:
- all non-allowed files
- `migrations/**`
- `architecture/**`
- `docs/governance/**`
- `docs/architecture/**`
- `src/control/**`
- `src/supervisor_api/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime state and cutover surfaces

### Non-goals
- no `P0.1` exit semantics work
- no `P0.3` transaction boundary work
- no `P0.4` data-availability fact work
- no `P0.5` implementation-OS work
- no team execution
- no schema or migration changes

### Current blocker
- no active hard blocker
- `FOUNDATION-MAINLINE-PLAN` is complete and pushed
- explicit adversarial review is complete
- repo-local `zeus_final_tribunal_overlay/` is currently an untracked reference directory and remains outside this packet’s versioned scope

### Ready-to-commit slice
`P0.2 accepted locally — evaluator emits canonical strategy_key on the touched path, downstream touched runtime surfaces stop inventing strategy, invalid/missing attribution is rejected on materialization, and post-attack fixes are in place. Next step is commit/push.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current-phase `P-*` queue is closed
- [x] confirm `FOUNDATION-MAINLINE-PLAN` is executed and accepted
- [x] freeze `P0.2-ATTRIBUTION-FREEZE`
- [x] define allowed / forbidden files for the packet

### Phase B — packet intake
- [x] confirm exact evaluator / runtime / record surfaces to touch
- [x] confirm the smallest first-slice file set inside this packet
- [x] confirm targeted tests for invalid/missing attribution rejection

### Phase C — bounded packet design
- [x] keep work inside `P0.2` only
- [x] block `P1/P2/P3` momentum
- [x] keep team execution disallowed before `P0.5`

### Phase D — evidence bundle
- [x] append planning completion + packet freeze result to `architects_progress.md`
- [x] run targeted runtime-guard tests
- [x] run architecture-contract verification
- [x] run explicit adversarial review
- [x] obtain architect verification
- [ ] commit and push the packet execution slice

---

## Definition of Done For Planning Completion

Planning completion is achieved:
- `FOUNDATION-MAINLINE-PLAN` is executed and accepted ✅
- stage map, workstream order, automation path, verification path, and explicit team-opening gate exist in versioned packet/control surfaces ✅
- staffing is explicitly deferred to `FOUNDATION-TEAM-GATE` ✅
- no team opening is allowed yet ✅

---

## Next Required Action

The next owner should do exactly this:
1. Execute `P0.2-ATTRIBUTION-FREEZE`.
2. Preserve the foundation-spec rules:
   - P0 is bearing-capacity work, not feature work
   - do not jump to P1/P2/P3
   - do not open team from momentum
3. If any stage, goal, or sequencing detail is unclear, return to:
   - `zeus_final_tribunal_overlay/`
   - `zeus_mature_project_foundation/`
4. After `P0.2` closes, freeze `P0.1`.

If this cannot be done without a new packet, freeze that packet before acting.
