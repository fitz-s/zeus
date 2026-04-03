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
   - `P0.4` ⏭ active closeout
   - `P0.5` ⏭ frozen next
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
`P0.4-DATA-AVAILABILITY-TRUTH`

### State
`ACCEPTED LOCALLY / READY TO COMMIT`

### Execution mode verdict
`RALPH_NOW`

### Objective
Make upstream data availability explicit truth on the touched path so missing, stale, rate-limited, or chain-unavailable conditions are recorded as first-class outcomes rather than disappearing into logs.

### Why this packet is active
- `P0.3-CANONICAL-TRANSACTION-BOUNDARY` is complete and pushed
- `P0.4` code/test changes are landed locally and verified
- `P0.5` is frozen but remains queued until `P0.4` is actually committed and pushed

### Owner model
- Required: one named execution owner for `P0.4-DATA-AVAILABILITY-TRUTH`
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
- `src/engine/evaluator.py`
- `src/engine/cycle_runtime.py`
- `src/state/decision_chain.py`
- `tests/test_runtime_guards.py`
- `architects_progress.md`
- `architects_task.md`
- `work_packets/P0.4-DATA-AVAILABILITY-TRUTH.md`

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
- no `P0.5` implementation-OS work
- no `P1/P2/P3/P4` jump
- no team execution
- no schema or migration changes
- no broader analytics/fact-table rollout beyond the touched path

### Current blocker
- no active hard blocker
- adversarial review is complete
- architect verification is complete
- repo-local `zeus_final_tribunal_overlay/` is currently an untracked reference directory and remains outside versioned packet scope
- repo also has a pre-existing local `AGENTS.md` diff outside the active packet scope; it is not part of this packet

### Ready-to-commit slice
`P0.4 accepted locally — explicit availability truth now lands on the touched path, diagnostics separate availability-driven no-trades from no-edge cases, and post-attack fixes are in place. Next step is commit/push, then rotate to P0.5.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current-phase `P-*` queue is closed
- [x] confirm `FOUNDATION-MAINLINE-PLAN` is executed and accepted
- [x] confirm `P0.3-CANONICAL-TRANSACTION-BOUNDARY` is complete
- [x] freeze `P0.4-DATA-AVAILABILITY-TRUTH`
- [x] define allowed / forbidden files for the packet

### Phase B — packet intake
- [x] confirm exact runtime/diagnostic surfaces to touch
- [x] confirm the smallest first-slice file set inside this packet
- [x] confirm targeted tests for explicit availability outcomes and no-edge vs no-data separation

### Phase C — bounded packet design
- [x] keep work inside `P0.4` only
- [x] block `P1/P2/P3/P4` momentum
- [x] keep team execution disallowed before `P0.5`

### Phase D — evidence bundle
- [x] append prior packet closure + next packet freeze to `architects_progress.md`
- [x] run targeted availability-truth tests
- [x] run architecture-contract verification
- [x] run explicit adversarial review
- [x] obtain architect verification
- [ ] commit and push the packet execution slice

---

## Queued Next Packet

### Packet
`P0.5-IMPLEMENTATION-OS`

### Status
`FROZEN / QUEUED AFTER P0.4 CLOSE`

### Rule
- Do not start `P0.5` execution until `P0.4` is committed and pushed.

---

## Next Required Action

The next owner should do exactly this:
1. Commit and push `P0.4-DATA-AVAILABILITY-TRUTH`.
2. Rotate the active packet to `P0.5-IMPLEMENTATION-OS`.
3. Continue the same packet-by-packet loop without opening team early.

If this cannot be done without a new packet, freeze that packet before acting.
