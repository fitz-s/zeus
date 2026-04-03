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

## Current Active Packet

### Packet
`P-INSTR-01-SLICE-ROOT-AGENTS`

### State
`ACTIVE / RALPH_NOW / ROOT AGENTS SYNC AUTHORIZED`

### Execution mode verdict
`RALPH_NOW`

### Objective
Land the existing root `AGENTS.md` routing-policy update as a narrow single-owner governance slice, while keeping `architects_progress.md` and `architects_task.md` current so the team can recover state from GitHub alone.

### Why this packet is active first
- the user explicitly approved pushing `AGENTS.md`
- root `AGENTS.md` is the primary repo instruction surface
- leaving a local-only root-instruction delta would create visible drift between local execution truth and cloud review truth
- this is a narrow, bounded, single-owner governance patch and does not need team execution

### Owner model
- Required: one named execution owner for this root-instruction slice
- Tribunal/principal architect remains the scope-freezing authority
- Verifier remains independent gate/evidence review
- Critic remains contradiction / blast-radius review

### Planning lane baseline
- leader: `gpt-5.4 xhigh`
- scouts: `gpt-5.3-codex-spark low` read-only
- verifier: `gpt-5.4-mini high`

### Current execution owner
- execution owner: `Architects local lead (current Codex session)`
- verifier/critic support remains advisory-only unless a later bounded slice needs explicit re-review

### Allowed edit surface
Only the following may be edited in this packet:
- `AGENTS.md`
- `architects_progress.md`
- `architects_task.md`

### Forbidden edit surface
Explicitly forbidden for edits in this packet:
- all non-allowed files
- all scoped `AGENTS.md`
- `.github/workflows/**`
- `scripts/**`
- `tests/**`
- `src/**`
- `migrations/**`
- `architecture/**`
- `docs/**`
- `.claude/CLAUDE.md`
- runtime state and cutover surfaces

### Non-goals
- no runtime/schema/workflow changes
- no scoped `AGENTS.md` changes
- no authority expansion beyond the existing root routing contract delta
- no packet widening back into `P-GATE-01`

### Current blocker
- no active hard blocker
- user explicitly authorized pushing `AGENTS.md`
- carry-forward note: the previously landed `P-GATE-01` workflow slice remains valid and already pushed in commit `eba4321`

### Ready-to-commit slice
`Slice 2 — commit the existing root AGENTS routing-policy update together with synchronized progress/task ledgers, then push so cloud review sees the same instruction truth as the local leader session.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current session has read the required authority surfaces
- [x] confirm `P-BOUND-01` prerequisite is satisfied in the live repo state
- [x] confirm no one is attempting runtime/schema/cutover work under this packet
- [x] confirm the user explicitly approved pushing `AGENTS.md`

### Phase B — allowed-surface inspection
- [x] inspect `AGENTS.md`
- [x] inspect current local diff for root-instruction changes
- [x] confirm the two Architects control files will carry the current execution truth

Inventory result:
- `AGENTS.md` -> `partial / locally modified / not yet pushed`
- `architects_progress.md` -> `present / active ledger`
- `architects_task.md` -> `present / active control surface`

### Phase C — bounded enforcement design
- [x] keep this slice narrow and single-owner
- [x] ensure the root `AGENTS.md` delta is treated as governance/instruction sync, not runtime work
- [x] keep all other packet families out of scope
- [x] record the carry-forward relationship to pushed `P-GATE-01` Slice 1

Slice 2 shape:
- land root `AGENTS.md` routing/reasoning contract already present locally
- keep progress/task synchronized with the new active packet state
- commit and push immediately after local contradiction review

### Phase D — evidence bundle
- [x] record user authorization for `AGENTS.md` push
- [x] capture affected-surface note
- [x] write rollback note
- [x] write unresolved uncertainty note
- [x] append execution result to `architects_progress.md`

Evidence snapshot for Slice 2:
- affected-surface note: root instruction surface only; no runtime/schema/workflow edits
- rollback note: revert `AGENTS.md` plus the paired `architects_progress.md` / `architects_task.md` updates
- unresolved uncertainty: none on scope; only carry-forward is that semgrep/replay-parity promotion still belongs to a later packet

Local verification completed:
- root `AGENTS.md` diff manually reviewed against current repo routing contract
- `git diff --check` will be run before commit
- prior pushed packet reminder: `P-GATE-01` Slice 1 local checks already passed before commit `eba4321`

---

## Stop Conditions

Hard stop immediately if any of the following appears:
- required reads are unmet
- `P-BOUND-01` prerequisite is not satisfied
- scope exceeds the allowed edit surface
- authority disagreement changes file selection
- CI fails manifests, boundaries, or architecture tests
- runtime/schema/control-plane spillover appears
- any gate attempts to block on external workspace artifacts

Escalate to human for:
- live cutover timing
- schema cutover
- control-plane expansion
- destructive archive/delete
- permanent `.claude` retirement

---

## Subagent Policy For This Packet

Allowed use of subagents:
- read-only inventory of allowed gate surfaces
- bounded verification
- bounded test drafting
- contradiction / blast-radius review

Not allowed for subagents under this packet:
- workflow authorship without owner approval
- any runtime/codepath edits outside the frozen file set
- authority-surface rewrites
- silent scope expansion

Recommended baton split:
- Scout lane: inventory and classify allowed surfaces only
- Verifier lane: compress findings into acceptance / evidence / risks
- Critic lane: review false-positive risk, maintenance burden, and blast radius

---

## Definition of Done For `P-GATE-01`

`P-GATE-01` is done only when all of the following are true:
- work stayed entirely inside the allowed edit set
- gate surfaces have explicit severity and rationale
- replay parity remains advisory-first / warn-only at CI layer until prerequisites exist
- external-workspace-dependent checks remain advisory
- every gate has owner, rationale, and sunset/review condition
- the evidence bundle is complete
- the result is appended to `architects_progress.md` with any remaining uncertainty clearly stated

---

## Next Required Action

The next owner should do exactly this:
1. Commit and push this root `AGENTS.md` slice immediately.
2. Verify GitHub now shows the pushed root instruction change alongside the updated Architects ledgers.
3. Return the active packet to the next bounded follow-up after cloud confirmation.
4. Keep recording every new slice in `architects_progress.md`.

If this cannot be done without leaving the allowed file boundary, stop and freeze a new packet rather than forcing progress.
