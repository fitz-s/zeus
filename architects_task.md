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
4. Advance only through the ordered P0 bearing-capacity packets:
   - `P0.2`
   - `P0.1`
   - `P0.3`
   - `P0.4`
   - `P0.5`
5. Prepare and open durable team execution only after `P0.5` is complete and a later team gate allows it.

Current completion ladder:
- `current-phase complete` = tribunal / `P-*` packet family closed ✅
- `foundation-planned` = architecture mainline packet map and automation plan frozen ✅
- `P0.2 complete` = attribution freeze landed and pushed ✅
- `P0.5 complete` = implementation operating system strong enough for later packet-by-packet team autonomy
- `team-ready` = later gate packet freezes staffing, lane ownership, and verification path
- `governed_runtime complete` = canonical authority + machine gates + strategy-aware protection landed
- `mature_project complete` = shadow persistence demoted/removed and replay/parity can block regressions

---

## Current Active Packet

### Packet
`P0.1-EXIT-SEMANTICS-SPLIT`

### State
`ACTIVE / INVENTORY COMPLETE / READY FOR FIRST SLICE`

### Execution mode verdict
`RALPH_NOW`

### Objective
Introduce explicit exit-intent semantics and exit event vocabulary scaffolding before any broader lifecycle or ledger work, without batching full cutover behavior.

### Why this packet is next
- `P0.2-ATTRIBUTION-FREEZE` is complete and pushed
- foundation spec `P0 sequence` requires `P0.1` next
- local-close semantics remain the deepest pre-ledger corruption risk

### Owner model
- Required: one named execution owner for `P0.1-EXIT-SEMANTICS-SPLIT`
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
- `src/execution/executor.py`
- `src/execution/exit_lifecycle.py`
- `src/engine/cycle_runtime.py`
- `tests/test_runtime_guards.py`
- `architects_progress.md`
- `architects_task.md`
- `work_packets/P0.1-EXIT-SEMANTICS-SPLIT.md`

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
- no full runtime cutover yet
- no `P0.3` transaction boundary work
- no `P0.4` data-availability fact work
- no `P0.5` implementation-OS work
- no `P1/P2/P3` jump
- no team execution
- no schema or migration changes

### Current blocker
- no active hard blocker
- `P0.2-ATTRIBUTION-FREEZE` is complete and pushed in `a1ac706`
- the next blocker is executional: the first exit-semantics scaffolding slice is not yet landed
- repo-local `zeus_final_tribunal_overlay/` is currently an untracked reference directory and remains outside versioned packet scope

### Ready-to-commit slice
`P0.1 inventory is complete — next execution slice is to add or normalize ExitIntent / exit-event vocabulary scaffolding on the touched execution path without doing the full behavioral cutover yet.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current-phase `P-*` queue is closed
- [x] confirm `FOUNDATION-MAINLINE-PLAN` is executed and accepted
- [x] confirm `P0.2-ATTRIBUTION-FREEZE` is complete
- [x] freeze `P0.1-EXIT-SEMANTICS-SPLIT`
- [x] define allowed / forbidden files for the packet

### Phase B — packet intake
- [x] confirm exact execution / runtime surfaces to touch
- [x] confirm the smallest first-slice file set inside this packet
- [x] confirm targeted tests for exit-event legality and scaffolding behavior

Inventory result:
- `src/execution/exit_lifecycle.py` -> `present / already has explicit exit lifecycle state machine`
- `src/execution/executor.py` -> `present / already has live sell-order primitive`
- `src/engine/cycle_runtime.py` -> `partial / still contains local close / void decision points that anchor the next slice`
- `tests/test_runtime_guards.py` -> `present / best target for event-model legality tests`

### Phase C — bounded packet design
- [x] keep work inside `P0.1` only
- [x] block `P1/P2/P3` momentum
- [x] keep team execution disallowed before `P0.5`

### Phase D — evidence bundle
- [x] append prior packet closure + next packet freeze to `architects_progress.md`
- [ ] run targeted exit-semantics tests
- [ ] run architecture-contract verification
- [ ] run explicit adversarial review
- [ ] obtain architect verification
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
1. Execute `P0.1-EXIT-SEMANTICS-SPLIT`.
2. Preserve the foundation-spec rules:
   - P0 is bearing-capacity work, not feature work
   - do not jump to P1/P2/P3
   - do not open team from momentum
3. If any stage, goal, or sequencing detail is unclear, return to:
   - `zeus_final_tribunal_overlay/`
   - `zeus_mature_project_foundation/`
4. After `P0.1` closes, freeze `P0.3`.

If this cannot be done without a new packet, freeze that packet before acting.
