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

1. Close the tribunal / current-phase `P-*` governance and enforcement packets.
2. Confirm current-phase authority install is complete without claiming runtime convergence.
3. Only then write the foundation-mainline architecture plan.
4. Only after that prepare and open team execution.
5. Use the foundation mainline to move Zeus from `hardened_transition` -> `governed_runtime` -> `mature_project`.

Current completion ladder:
- `current-phase complete` = tribunal / `P-*` packet family closed
- `foundation-planned` = architecture mainline packet map and automation plan frozen
- `team-ready` = team entry rules, staffing plan, and verification path frozen after current-phase closure
- `governed_runtime complete` = canonical authority + machine gates + strategy-aware protection landed
- `mature_project complete` = shadow persistence demoted/removed and replay/parity can block regressions

Current user-directed queue:
1. `P-BOUND-01`
2. `P-ROLL-01`
3. `P-STATE-01`
4. `P-OPS-01`

After these four packets close:
- write the architecture mainline plan
- prepare the team launch path

---

## Frozen Current-Phase Queue

### 1. `P-BOUND-01`
- Status: `FROZEN / NEXT`
- Objective: repo-local Zeus ↔ Venus / OpenClaw boundary clarification
- Allowed files:
  - `docs/governance/zeus_openclaw_venus_delivery_boundary.md`
  - `scripts/audit_architecture_alignment.py`
  - `src/supervisor_api/contracts.py`
  - `architects_progress.md`
  - `architects_task.md`
- Non-goals:
  - no architecture-law rewrite
  - no schema/runtime/cutover work
  - no outer-host authority expansion

### 2. `P-ROLL-01`
- Status: `FROZEN / QUEUED AFTER P-BOUND-01`
- Objective: migration delta + archive/cutover truth surfaces
- Allowed files:
  - `docs/rollout/**`
  - `docs/governance/zeus_runtime_delta_ledger.md`
  - `architects_progress.md`
  - `architects_task.md`
- Non-goals:
  - no runtime code edits
  - no cutover claim
  - no hidden drift

### 3. `P-STATE-01`
- Status: `FROZEN / QUEUED AFTER P-ROLL-01`
- Objective: patch highest-risk state drift
- Allowed files:
  - `src/state/strategy_tracker.py`
  - `src/data/observation_client.py`
  - targeted tests/docs
  - `architects_progress.md`
  - `architects_task.md`
- Non-goals:
  - no schema widening
  - no control-plane widening
  - no governance-law edits

### 4. `P-OPS-01`
- Status: `FROZEN / QUEUED AFTER P-STATE-01`
- Objective: operator cookbook / runbook / first-phase operating guidance
- Allowed files:
  - `docs/governance/zeus_omx_omc_*`
  - `docs/governance/zeus_first_phase_execution_plan.md`
  - `architects_progress.md`
  - `architects_task.md`
- Non-goals:
  - no manifest/schema/runtime truth edits
  - no authority overclaim
  - no team opening before packet discipline is explicit

---

## Current Active Packet

### Packet
`P-ROLL-01`

### State
`ACTIVE / PATCH LANDED LOCALLY / VERIFYING`

### Execution mode verdict
`RALPH_NOW`

### Objective
Record the rollout/cutover truth honestly by distinguishing resolved, narrowed, and still-open deltas and by keeping the archive/cutover plan explicit about what is complete vs still pending in current phase.

### Why this packet is next
- user explicitly set the remaining queue order
- `P-BOUND-01` is now closed
- rollout truth must remain honest before `P-STATE-01` touches runtime drift

### Owner model
- Required: one named execution owner for `P-ROLL-01`
- Tribunal/principal architect remains the scope-freezing authority
- Verifier remains independent docs/evidence reviewer
- Critic remains contradiction / blast-radius reviewer

### Planning lane baseline
- leader: `gpt-5.4 xhigh`
- scouts: `gpt-5.3-codex-spark low` read-only
- verifier: `gpt-5.4-mini high`

### Current execution owner
- execution owner: `Architects local lead (current Codex session)`
- verifier/critic support will be used when execution starts

### Allowed edit surface
Only the following may be edited in the next packet:
- `docs/rollout/**`
- `docs/governance/zeus_runtime_delta_ledger.md`
- `architects_progress.md`
- `architects_task.md`
- `work_packets/P-ROLL-01.md`

### Forbidden edit surface
Explicitly forbidden for edits in the next packet:
- all non-allowed files
- `architecture/**`
- `migrations/**`
- `src/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime state and cutover surfaces

### Non-goals
- no runtime code edits
- no live cutover claim
- no authority-law rewrites
- no team execution yet

### Current blocker
- no active hard blocker
- carry-forward fact: `P-BOUND-01` is closed and pushed in `5778e8b`
- queue fact: `P-STATE-01` and `P-OPS-01` remain frozen behind this packet
- rollout drift now centers on documenting resolved vs open deltas honestly

### Ready-to-commit slice
`P-ROLL-01 landed locally — delta ledger now distinguishes resolved / narrowed / open drift, and the cutover/archive plan now distinguishes completed current-phase setup from still-pending actions without claiming cutover readiness.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current session has re-read the required authority surfaces for `P-BOUND-01`
- [x] confirm no runtime/schema/cutover work is being mixed into the packet
- [x] confirm the user-directed queue order remains `P-BOUND-01 -> P-ROLL-01 -> P-STATE-01 -> P-OPS-01`

### Phase B — allowed-surface inspection
- [x] inspect `docs/governance/zeus_openclaw_venus_delivery_boundary.md`
- [x] inspect `scripts/audit_architecture_alignment.py`
- [x] inspect `src/supervisor_api/contracts.py`
- [x] classify each as `present`, `missing`, `partial`, or `drifted`

Inventory result:
- `docs/rollout/zeus_authority_cutover_and_archive_plan.md` -> `present / needs completed-vs-pending split`
- `docs/governance/zeus_runtime_delta_ledger.md` -> `present / needs resolved-vs-open status clarity`

### Phase C — bounded packet design
- [x] identify the narrowest boundary delta needed
- [x] decide whether the packet is docs-only or includes narrow contract/audit edits
- [x] keep all non-boundary packet families out of scope

Current slice shape:
- keep the packet docs-only
- distinguish resolved / narrowed / open deltas explicitly
- distinguish completed current-phase setup from still-pending archive/demotion steps
- keep runtime truth and cutover claims out of scope

### Phase D — evidence bundle
- [x] run `python3 scripts/check_work_packets.py`
- [x] record resolved-vs-open delta note
- [x] append execution result to `architects_progress.md`
- [x] review docs-only diff
- [ ] commit and push once the packet slice lands

Evidence snapshot:
- resolved-vs-open delta note:
  - `DELTA-02` to `DELTA-04` resolved
  - `DELTA-10` narrowed
  - `DELTA-05` to `DELTA-09` and `DELTA-11` remain open
- rollout-plan note:
  - immediate actions 1-4 completed
  - demotion/archive and state-drift cleanup still pending

---

## Definition of Done For The Remaining Current-Phase Queue

The remaining `P-*` queue is closed only when:
- `P-BOUND-01`, `P-ROLL-01`, `P-STATE-01`, and `P-OPS-01` are each frozen, executed, verified, committed, and pushed
- no packet silently widens into schema/cutover/foundation-mainline work
- current-phase authority install is complete without falsely claiming runtime convergence
- `architects_progress.md` and `architects_task.md` reflect the cloud-visible truth

---

## Next Required Action

The next owner should do exactly this:
1. Land the first bounded `P-BOUND-01` slice as docs + audit-script consolidation unless a typed-contract contradiction proves necessary.
2. After `P-BOUND-01` closes, move to `P-ROLL-01`.
3. Then close `P-STATE-01`.
4. Then close `P-OPS-01`.
5. Only after those four packets close, write the architecture mainline plan and prepare team execution.

If this cannot be done without leaving the active packet boundary, stop and freeze a new packet rather than forcing progress.
