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
   - `P0.2` ✅
   - `P0.1` ✅
   - `P0.3` ✅
   - `P0.4` ⏭ current
   - `P0.5` ⏭ next
5. Prepare and open durable team execution only after `P0.5` is complete and a later team gate allows it.

Current completion ladder:
- `current-phase complete` = tribunal / `P-*` packet family closed ✅
- `foundation-planned` = architecture mainline packet map and automation plan frozen ✅
- `P0.2 complete` = attribution freeze landed and pushed ✅
- `P0.1 complete` = exit semantics split scaffolding landed and pushed ✅
- `P0.3 complete` = canonical transaction-boundary scaffold landed and pushed ✅
- `P0.5 complete` = implementation operating system strong enough for later packet-by-packet team autonomy
- `team-ready` = later gate packet freezes staffing, lane ownership, and verification path

---

## Current Active Packet

### Packet
`P0.5-IMPLEMENTATION-OS`

### State
`FROZEN / READY TO EXECUTE`

### Execution mode verdict
`RALPH_NOW`

### Objective
Complete the implementation operating system so later packet-by-packet autonomous team execution can be allowed without widening authority or verification drift.

### Why this packet is next
- `P0.4-DATA-AVAILABILITY-TRUTH` is complete and pushed
- foundation spec `P0 sequence` requires `P0.5` next
- `P0.5` is the last packet before later team autonomy can become allowed

### Owner model
- Required: one named execution owner for `P0.5-IMPLEMENTATION-OS`
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
- `AGENTS.md`
- `architects_progress.md`
- `architects_task.md`
- `work_packets/P0.5-IMPLEMENTATION-OS.md`

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
- no `P1/P2/P3/P4` jump
- no team execution
- no runtime/schema work
- no broad governance rewrite beyond the implementation-OS rule set

### Current blocker
- no active hard blocker
- `P0.4-DATA-AVAILABILITY-TRUTH` is complete and pushed
- the next blocker is executional: the final P0 implementation-OS slice is not yet landed
- repo-local `zeus_final_tribunal_overlay/` is currently an untracked reference directory and remains outside versioned packet scope
- repo also has a pre-existing local `AGENTS.md` diff outside the active packet scope; it is not part of this packet

### Ready-to-commit slice
`P0.5 is frozen — next execution slice is to formalize the post-P0.5 implementation operating system and explicit team-autonomy rule without widening into runtime/schema work.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current-phase `P-*` queue is closed
- [x] confirm `FOUNDATION-MAINLINE-PLAN` is executed and accepted
- [x] confirm `P0.4-DATA-AVAILABILITY-TRUTH` is complete
- [x] freeze `P0.5-IMPLEMENTATION-OS`
- [x] define allowed / forbidden files for the packet

### Phase B — packet intake
- [ ] confirm exact implementation-OS surfaces to touch
- [ ] confirm the smallest first-slice file set inside this packet
- [ ] confirm targeted verification for explicit post-P0.5 team-autonomy permission and boundaries

### Phase C — bounded packet design
- [x] keep work inside `P0.5` only
- [x] block `P1/P2/P3/P4` momentum
- [x] keep team execution disallowed before `P0.5`

### Phase D — evidence bundle
- [x] append prior packet closure + next packet freeze to `architects_progress.md`
- [ ] run targeted implementation-OS verification
- [ ] run architecture/governance verification
- [ ] run explicit adversarial review
- [ ] obtain architect verification
- [ ] commit and push the packet execution slice

---

## Next Required Action

The next owner should do exactly this:
1. Execute `P0.5-IMPLEMENTATION-OS`.
2. Preserve the foundation-spec rules:
   - P0 is bearing-capacity work, not feature work
   - do not jump to P1/P2/P3/P4
   - do not open team from momentum
3. If any stage, goal, or sequencing detail is unclear, return to:
   - `zeus_final_tribunal_overlay/`
   - `zeus_mature_project_foundation/`
4. After `P0.5` closes, explicitly record that later packet-by-packet team autonomy is allowed for subsequent phases, while destructive cutover remains human-gated.

If this cannot be done without a new packet, freeze that packet before acting.
