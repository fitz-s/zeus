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
3. Write the foundation-mainline architecture plan. ⏭ next
4. Prepare and open team execution after that plan is frozen. ⏭ after planning
5. Use the foundation mainline to move Zeus from `hardened_transition` -> `governed_runtime` -> `mature_project`.

Current completion ladder:
- `current-phase complete` = tribunal / `P-*` packet family closed ✅
- `foundation-planned` = architecture mainline packet map and automation plan frozen
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
`FOUNDATION-MAINLINE-PLAN`

### State
`FROZEN / PUSHED / READY TO EXECUTE`

### Execution mode verdict
`RALPLAN_NEXT`

### Objective
Freeze the post-current-phase architecture mainline plan that sequences foundation work, automation, verification, and team opening now that all remaining current-phase `P-*` packets are closed.

### Why this packet is next
- all four remaining current-phase `P-*` packets are closed and pushed
- the next gated phase is planning, not more current-phase cleanup
- team opening must now be prepared from an explicit plan rather than packet-memory

### Owner model
- Required: one named planning owner for the mainline plan packet
- Tribunal/principal architect remains the scope-freezing authority
- Verifier remains independent planning/evidence reviewer
- Critic remains contradiction / blast-radius reviewer

### Planning lane baseline
- leader: `gpt-5.4 xhigh`
- scouts: `gpt-5.3-codex-spark low` read-only
- verifier: `gpt-5.4-mini high`

### Current execution owner
- execution owner: `Architects local lead (current Codex session)`

### Allowed edit surface
Only the following may be edited in this planning packet:
- `work_packets/FOUNDATION-MAINLINE-PLAN.md`
- `architects_progress.md`
- `architects_task.md`

### Forbidden edit surface
Explicitly forbidden for edits in this planning packet:
- all non-allowed files
- `src/**`
- `migrations/**`
- `architecture/**`
- `docs/governance/**`
- `docs/architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime state and cutover surfaces

### Non-goals
- no ad hoc team opening
- no silent foundation-mainline start without plan approval
- no reopening of the completed current-phase `P-*` queue without a new contradiction packet

### Current blocker
- no active hard blocker
- the planning packet is now frozen and pushed in `7fff4d4`
- the next blocker is executional: the stage-map/planning artifact itself is not yet written
- repo-local `zeus_final_tribunal_overlay/` is currently an untracked reference directory and remains outside this packet’s versioned scope

### Ready-to-commit slice
`Planning execution landed locally — FOUNDATION-MAINLINE-PLAN now contains the stage map, workstreams, automation path, verification path, explicit team-opening gate, and explicit successor packet requirement for staffing.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current-phase `P-*` queue is closed
- [x] freeze the foundation-mainline planning packet
- [x] define allowed / forbidden files for the planning packet

### Phase B — planning intake
- [x] identify the planning surfaces that will define:
  - foundation work order
  - automation strategy
  - verification path
  - team-opening gate
- [x] confirm which artifacts the next plan packet should create

### Phase C — bounded planning design
- [x] define the planning packet
- [x] define the post-plan team-opening gate
- [x] keep the completed current-phase queue closed

### Phase D — evidence bundle
- [x] append planning execution result to `architects_progress.md`
- [ ] commit and push the planning execution slice

Planning deliverables now live in:
- `work_packets/FOUNDATION-MAINLINE-PLAN.md`
  - stage map
  - source-package crosswalk
  - mainline workstreams
  - automation path
  - verification path
  - explicit team-opening gate
  - explicit successor packet requirement: `FOUNDATION-TEAM-GATE`

---

## Definition of Done For Current-Phase Closure

Current-phase closure is achieved:
- `P-BOUND-01`, `P-ROLL-01`, `P-STATE-01`, and `P-OPS-01` are all executed, verified, committed, and pushed ✅
- no packet silently widened into schema/cutover/foundation-mainline work ✅
- current-phase authority install is complete without falsely claiming runtime convergence ✅
- `architects_progress.md` and `architects_task.md` reflect the cloud-visible truth ✅

---

## Next Required Action

The next owner should do exactly this:
1. Execute `FOUNDATION-MAINLINE-PLAN`.
2. Extract from `zeus_final_tribunal_overlay/` and `zeus_mature_project_foundation/`:
   - stage map
   - workstreams
   - automation path
   - verification path
   - explicit team-opening gate
3. If team staffing is to be a precondition for team opening, freeze it in this packet; otherwise explicitly declare the successor packet required for staffing.
4. Do not open team until the planning packet is complete.
5. If any stage, goal, or sequencing detail is unclear, return to the two source packages before deciding.

If this cannot be done without a new packet, freeze that packet before acting.
