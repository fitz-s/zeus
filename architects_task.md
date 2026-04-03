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
`P1.2-CANONICAL-SCHEMA-BOOTSTRAP`

### State
`APPROVED / READY TO COMMIT`

### Execution mode verdict
`SOLO_EXECUTE`

### Objective
Add an explicit canonical-schema bootstrap entrypoint that can install the architecture-kernel tables on a fresh database, reject legacy name collision, and fail loudly when legacy runtime helpers are used against a canonically bootstrapped-but-not-runtime-ready database.

### Why this packet is next
- `FOUNDATION-TEAM-GATE` is complete and pushed
- `FOUNDATION-MAINLINE-PLAN` moves the repo into Stage 2 canonical-authority rollout next
- repo truth already contains migration SQL plus helper-level canonical append/project scaffolding, but there is still no explicit collision-aware schema bootstrap path
- current runtime reality still lacks `position_current`, and legacy `init_schema()` would otherwise blur target-vs-runtime truth
- adversarial review confirmed the slice must stay explicitly transitional and not runtime-ready

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
- `work_packets/P1.2-CANONICAL-SCHEMA-BOOTSTRAP.md`
- `src/state/db.py`
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
- `architecture/**`
- `docs/governance/**`
- `docs/architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime cutover surfaces
- `zeus_final_tribunal_overlay/**`

### Non-goals
- no runtime writer/reader cutover
- no dual-write
- no DB-first reads
- no legacy-table rename/repurpose
- no Day0/K3 work
- no team launch
- no claim that a fresh canonical bootstrap is live-runtime compatible yet

### Current blocker
- no active hard blocker
- local working tree contains out-of-scope non-mainline dirt and reference material; it must not be silently mixed into `P1.2-CANONICAL-SCHEMA-BOOTSTRAP`
- repo-local `zeus_final_tribunal_overlay/` remains an untracked reference directory outside versioned packet scope
- root `AGENTS.md` has unrelated local dirt outside this packet scope

### Ready-to-commit slice
`P1.2-CANONICAL-SCHEMA-BOOTSTRAP landed — canonical schema bootstrap is explicit on fresh DBs, legacy collision is rejected loudly, legacy-helper misuse now fails loudly, and no runtime-ready/cutover behavior is claimed.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm `FOUNDATION-TEAM-GATE` is complete
- [x] confirm Stage 2 is next in the foundation mainline
- [x] freeze `P1.2-CANONICAL-SCHEMA-BOOTSTRAP`
- [x] define allowed / forbidden files for the packet

### Phase B — implementation
- [x] add canonical-schema bootstrap entrypoint
- [x] keep `init_schema()` legacy/transitional
- [x] add targeted architecture-contract tests for fresh bootstrap and legacy collision rejection
- [x] add explicit negative-compatibility guard for legacy helper misuse on canonical bootstrap DB
- [x] add caller-audit coverage for the new bootstrap helper

### Phase C — bounded design discipline
- [x] keep live/runtime cutover out of scope
- [x] keep legacy `position_events` collision explicit
- [x] keep replay/parity staged-advisory
- [x] keep the packet explicitly not-runtime-ready

### Phase D — evidence bundle
- [ ] append packet transitions to `architects_progress.md`
- [x] run explicit adversarial review
- [x] obtain final architect verification
- [ ] commit and push the accepted packet

---

## Next Required Action

The next owner should do exactly this:
1. Commit and push this accepted packet without mixing unrelated working-tree dirt.
2. Freeze the next Stage-2 packet after push.
3. Keep cutover, dual-write, and runtime rewiring for later packets.
4. Preserve the explicit not-runtime-ready boundary until a later migration packet changes it.

If this cannot be done without a new packet, freeze that packet before acting.
