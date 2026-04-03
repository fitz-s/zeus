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
`READY TO FREEZE`

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
- the planning packet is now frozen
- the next blocker is executional: the stage-map/planning artifact itself is not yet written

### Ready-to-commit slice
`Planning freeze landed locally — the next execution slice is to extract the stage map, goals, automation path, and team-opening gate from the tribunal overlay and mature foundation package.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current-phase `P-*` queue is closed
- [x] freeze the foundation-mainline planning packet
- [x] define allowed / forbidden files for the planning packet

### Phase B — planning intake
- [ ] identify the planning surfaces that will define:
  - foundation work order
  - automation strategy
  - verification path
  - team-opening gate
- [ ] confirm which artifacts the next plan packet should create

### Phase C — bounded planning design
- [ ] define the planning packet
- [ ] define the post-plan team-opening gate
- [ ] keep the completed current-phase queue closed

### Phase D — evidence bundle
- [ ] append planning freeze result to `architects_progress.md`
- [ ] commit and push the planning freeze

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
1. Freeze the foundation-mainline planning packet.
2. Freeze the team-opening / staffing gate after the planning packet.
3. Do not reopen the completed `P-*` queue unless a new contradiction appears.
4. Begin the next phase from explicit planned authority, not from momentum alone.

If this cannot be done without a new packet, freeze that packet before acting.
