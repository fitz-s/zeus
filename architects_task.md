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
`P1.3-CANONICAL-LEDGER-API`

### State
`FROZEN / IN EXECUTION`

### Execution mode verdict
`SOLO_EXECUTE`

### Objective
Extract the canonical append/project API from `src/state/db.py` into dedicated `src/state/ledger.py` and `src/state/projection.py` modules while preserving current helper behavior and keeping runtime wiring/cutover out of scope.

### Why this packet is next
- `P1.2-CANONICAL-SCHEMA-BOOTSTRAP` is complete and pushed
- `FOUNDATION-MAINLINE-PLAN` Stage 2 continues from schema add into append/project API
- repo truth still keeps the canonical API inside transitional `src/state/db.py`
- the architecture spec requires dedicated ledger/projection modules before later dual-write work

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
- `work_packets/P1.3-CANONICAL-LEDGER-API.md`
- `src/state/db.py`
- `src/state/ledger.py`
- `src/state/projection.py`
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
- no schema rename/repurpose
- no Day0/K3 work
- no team launch

### Current blocker
- no active hard blocker
- local working tree contains out-of-scope non-mainline dirt and reference material; it must not be silently mixed into `P1.3-CANONICAL-LEDGER-API`
- repo-local `zeus_final_tribunal_overlay/` remains an untracked reference directory outside versioned packet scope
- root `AGENTS.md` has unrelated local dirt outside this packet scope

### Ready-to-commit slice
`P1.3-CANONICAL-LEDGER-API landed — canonical append/project helpers now live in dedicated ledger/projection modules, db.py preserves compatibility wrappers, and no runtime cutover is claimed.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm `P1.2-CANONICAL-SCHEMA-BOOTSTRAP` is complete
- [x] confirm Stage 2 append/project API is next in the foundation mainline
- [x] freeze `P1.3-CANONICAL-LEDGER-API`
- [x] define allowed / forbidden files for the packet

### Phase B — implementation
- [ ] extract canonical append helper into `src/state/ledger.py`
- [ ] extract canonical projection helper into `src/state/projection.py`
- [ ] keep `src/state/db.py` compatibility wrappers explicit
- [ ] add targeted architecture-contract coverage for the extracted module surface

### Phase C — bounded design discipline
- [ ] keep live/runtime cutover out of scope
- [ ] keep replay/parity staged-advisory
- [ ] keep the packet extraction-only and not dual-write

### Phase D — evidence bundle
- [ ] append packet transitions to `architects_progress.md`
- [ ] run explicit adversarial review
- [ ] obtain final architect verification
- [ ] commit and push the accepted packet

---

## Next Required Action

The next owner should do exactly this:
1. Extract the helper-level canonical API into dedicated modules.
2. Keep db.py wrappers compatibility-only.
3. Keep cutover, dual-write, and runtime rewiring for later packets.
4. Keep unrelated working-tree dirt out of the packet commit.

If this cannot be done without a new packet, freeze that packet before acting.
