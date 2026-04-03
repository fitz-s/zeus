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
   - `P0.4` ✅
   - `P0.5` ✅
5. Later phases may use packet-by-packet team autonomy only after a future `FOUNDATION-TEAM-GATE` is frozen and accepted.

Current completion ladder:
- `current-phase complete` = tribunal / `P-*` packet family closed ✅
- `foundation-planned` = architecture mainline packet map and automation plan frozen ✅
- `P0.2 complete` = attribution freeze landed and pushed ✅
- `P0.1 complete` = exit semantics split scaffolding landed and pushed ✅
- `P0.3 complete` = canonical transaction-boundary scaffold landed and pushed ✅
- `P0.4 complete` = data-availability truth landed and pushed ✅
- `P0.5 complete` = implementation operating system strong enough for later packet-by-packet team autonomy ✅
- `team-ready` = future `FOUNDATION-TEAM-GATE` freezes staffing, lane ownership, and verification path

---

## Current Active Packet

### Packet
`FOUNDATION-TEAM-GATE`

### State
`ACCEPTED / PUSHED / CLOSED`

### Execution mode verdict
`RALPLAN_NEXT`

### Objective
Freeze the future team-opening gate packet that will turn post-P0.5 packet-by-packet team autonomy from “eligible” into “allowed”.

### Why this packet is next
- `P0.5-IMPLEMENTATION-OS` is complete
- team autonomy is now eligible for later phases, but not yet allowed
- staffing, lane ownership, verification path, and shutdown/rollback expectations still need an explicit gate packet

### Owner model
- Required: one named planning owner for the team gate packet
- Named owner: `Architects mainline lead`
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
Only the following may be edited in this packet:
- `work_packets/FOUNDATION-TEAM-GATE.md`
- `architects_progress.md`
- `architects_task.md`

### Forbidden edit surface
Explicitly forbidden for edits in this packet:
- all non-allowed files
- all Day0/K3 feature-family files and packets
- `AGENTS.md`
- `src/**`
- `migrations/**`
- `architecture/**`
- `docs/governance/**`
- `docs/architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime state and cutover surfaces

### Non-goals
- no immediate team launch
- no reopening of completed P0 packets
- no claim that destructive/cutover work becomes autonomous

### Current blocker
- no active hard blocker
- active mainline packet drift acknowledged explicitly
- local working tree contains out-of-scope non-mainline dirt and reference material; it must not be silently mixed into `FOUNDATION-TEAM-GATE`
- `FOUNDATION-TEAM-GATE` execution slice is landed locally and accepted
- repo-local `zeus_final_tribunal_overlay/` remains an untracked reference directory outside versioned packet scope

### Ready-to-commit slice
`FOUNDATION-TEAM-GATE accepted and pushed — staffing, lane ownership, verification path, cleanup/rollback path, and exact post-P0.5 team-autonomy conditions are now explicit.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm `P0.5-IMPLEMENTATION-OS` is complete
- [x] acknowledge active packet drift explicitly
- [x] freeze `FOUNDATION-TEAM-GATE`
- [x] define allowed / forbidden files for the team gate packet

### Phase B — planning intake
- [x] define staffing
- [x] define lane ownership
- [x] define verification path
- [x] define shutdown / rollback / cleanup path

### Phase C — bounded planning design
- [x] keep team autonomy packet-by-packet only
- [x] keep destructive/cutover work human-gated
- [x] define an operational gate rubric instead of prose-only eligibility

### Phase D — evidence bundle
- [x] append team-gate freeze result to `architects_progress.md`
- [x] run explicit adversarial review
- [x] obtain final architect verification
- [x] commit and push the accepted team-gate packet

---

## Next Required Action

The next owner should do exactly this:
1. Freeze the first post-P0.5 eligible packet.
2. Keep packet-by-packet team autonomy narrow and explicit.
3. Do not mutate Day0/K3 files under the next mainline packet unless separately packetized there.
4. Keep destructive/cutover work human-gated.

If this cannot be done without a new packet, freeze that packet before acting.
