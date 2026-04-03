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
`P1.6B-HARVESTER-CHRONICLE-COMPAT`

### State
`FROZEN / IN EXECUTION`

### Execution mode verdict
`SOLO_EXECUTE / BLOCKER_PACKET`

### Objective
Make the chronicle write helper degrade cleanly on canonically bootstrapped databases so the remaining harvester compatibility inventory can proceed without the current raw `chronicle` table failure.

### Why this packet is next
- `P1.6A-HARVESTER-SETTLEMENT-TELEMETRY-COMPAT` is complete and pushed
- the remaining explicit harvester blocker is the chronicle write path
- repo truth shows `src/state/chronicler.py::log_event` still fails on canonical-only DBs

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
- `work_packets/P1.6B-HARVESTER-CHRONICLE-COMPAT.md`
- `src/state/chronicler.py`
- `tests/test_architecture_contracts.py`
- `architects_progress.md`
- `architects_task.md`

### Forbidden edit surface
Explicitly forbidden for edits in this packet:
- all non-allowed files
- `AGENTS.md`
- `migrations/**`
- `src/engine/**`
- `src/execution/**`
- `src/riskguard/**`
- `src/control/**`
- `src/supervisor_api/**`
- `src/state/db.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `architecture/**`
- `docs/governance/**`
- `docs/architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime cutover surfaces
- `zeus_final_tribunal_overlay/**`

### Non-goals
- no harvester caller migration
- no broader dual-write in caller code
- no DB-first reads
- no exit/reconciliation changes
- no Day0/K3 work
- no team launch

### Current blocker
- no active hard blocker
- local working tree contains out-of-scope non-mainline dirt and reference material; it must not be silently mixed into `P1.6B-HARVESTER-CHRONICLE-COMPAT`
- repo-local `zeus_final_tribunal_overlay/` remains an untracked reference directory outside versioned packet scope
- root `AGENTS.md` has unrelated local dirt outside this packet scope

### Ready-to-commit slice
`P1.6B-HARVESTER-CHRONICLE-COMPAT landed — touched chronicle helper degrades cleanly on canonical DBs, remaining harvester blockers stay explicit, and no harvester migration is claimed.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm `P1.6A-HARVESTER-SETTLEMENT-TELEMETRY-COMPAT` is complete
- [x] confirm the remaining explicit harvester blocker is the chronicle write path
- [x] freeze `P1.6B-HARVESTER-CHRONICLE-COMPAT`
- [x] define allowed / forbidden files for the packet

### Phase B — implementation
- [ ] make chronicle helper degrade cleanly on canonical DBs
- [ ] preserve fail-loud behavior for malformed/non-canonical unexpected states
- [ ] add targeted architecture-contract coverage for the compatibility change

### Phase C — bounded design discipline
- [ ] keep live/runtime cutover out of scope
- [ ] keep replay/parity staged-advisory
- [ ] keep remaining harvester blockers explicit
- [ ] keep team closed unless a new freeze explicitly justifies it

### Phase D — evidence bundle
- [ ] append packet transitions to `architects_progress.md`
- [x] run explicit adversarial review
- [x] obtain final architect verification
- [ ] commit and push the accepted packet

---

## Next Required Action

The next owner should do exactly this:
1. Fix only the chronicle compatibility blocker.
2. Keep harvester caller migration for a later successor packet.
3. Keep cutover and broader state rewiring out of scope.
4. Keep unrelated working-tree dirt out of the packet commit.

If this cannot be done without a new packet, freeze that packet before acting.
