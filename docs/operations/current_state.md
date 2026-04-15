# Current State

Role: single live control-entry surface for the repo.

## Read Order

1. `AGENTS.md`
2. `workspace_map.md`
3. `docs/operations/current_state.md`
4. Active work packet listed below
5. `docs/known_gaps.md` when runtime blockers matter

## Active Work

- Branch: `data-improve`
- Program: Zeus Topology Compiler and Recurrence-Proof Program
- Active packet: Packet 4 — source file rationale mapping (Packet 3 closed 2026-04-15, all acceptance criteria verified)
- Primary packet file: `docs/operations/task_2026-04-13_topology_compiler_program.md`
- Archived:
  - `docs/archives/work_packets/branches/data-improve/topology/2026-04-14_topology_context_efficiency/` — Phase 7 modularization COMPLETE, closeout steps verified, archived 2026-04-15
- Active backlog:
  - `docs/operations/task_2026-04-13_remaining_repair_backlog.md` — all items DEFERRED until DB rebuild completes
  - `docs/operations/task_2026-04-14_session_backlog.md` — sequential backfill PID 49371 running; post-backfill ETL cascade blocked on completion
  - `docs/operations/data_rebuild_plan.md` — upstream data rebuild plan (v2.2, scope: collect+store code prep while TIGGE completes)
- Stale/deferred:
  - `docs/operations/phase1live_2026-04-11_plan.md` — Phase 1 Live Daemon Maturity plan (IN PROGRESS since 2026-04-11, no updates since creation; conceptual authority only)
- Active checklist/evidence:
  - `docs/to-do-list/zeus_data_improve_bug_audit_75.xlsx` — 75-bug audit COMPLETE (Excel needs manual status update for Phase 4 bugs)
- Next packet: Packet 4 — source file rationale mapping

## Runtime

- Sequential backfill PID 49371 running (`backfill_wu_daily_all.py --all --missing-only --days 834`)
- Waiter process PID 50114 (`post_sequential_fillback.sh`) polls every 60s, kicks off HKO + hole scanner on completion
- Post-backfill unlocks: Task #63 (ETL cascade, 11 steps), Task #61 (TIGGE transfer, 41K rows), historical forecasts recovery (171K rows)

## Recent Packet State

- Packet 0 closed as a read-only topology inventory baseline.
- Packet 1 closed the WMO half-up executable rounding law and recurrence gates.
- Packet 2 added the topology compiler MVP and `scripts/topology_doctor.py`.
- Packet 3 closed 2026-04-15 — docs mesh repair and authority normalization complete.

## 75-Bug Audit Progress (data-improve branch)

| Phase | Macros | Bugs | Status | Commit |
|-------|--------|------|--------|--------|
| Phase 1 | K1+K2 | 39 | ✅ Complete | `96b70a8`, `f6f612e` |
| Phase 2 | K4+K5 | 13 | ✅ Complete | `4abbeb7` |
| Phase 3 | K6+K7 | 10 | ✅ Complete | `f6092ca` |
| Phase 4 | K3+K8 | 13 | ✅ Complete | `3e35808`, `f6eaa72` |

75 of 75 bugs addressed (implemented, confirmed pre-fixed, or confirmed false positive). Audit complete.

## Boundaries

- Do not mutate DB files from this packet.
- Do not run data rebuild from this packet.
- Do not perform broad stale-test/script cleanup from this packet.
- Runtime closeout notes under OMX context are evidence logs, not repo authority.

## Next Expected Packet

Packet 4 should begin source file rationale mapping unless Packet 3 leaves a blocking mesh issue.
