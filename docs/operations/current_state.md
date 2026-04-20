# Current State

Role: single live control-entry surface for the repo.

## Read Order

1. `AGENTS.md`
2. `workspace_map.md`
3. `docs/operations/current_state.md`
4. Active work packet listed below
5. `docs/known_gaps.md` when runtime blockers matter

## Active Work

- Branch: `data-improve` @ HEAD `f2ffcad` (pushed `origin/data-improve` 2026-04-19)
- Active program: Zeus Dual-Track Metric Spine Refactor — Phase 0 opened 2026-04-16. **Phase 10A + 10B shipped 2026-04-19.** Dual-track scaffold complete; v2 data migration deferred to post-Golden-Window. Residue: B055, B099 (architect-gated), R10/R12/R13/monitor_refresh LOW (user-ruled deferrals).
- Dual-Track refactor packet file: `docs/operations/task_2026-04-16_dual_track_metric_spine/plan.md`
- Dual-Track authority file: `docs/authority/zeus_dual_track_architecture.md`
- Archived:
  - `docs/archives/work_packets/branches/data-improve/topology/2026-04-14_topology_context_efficiency/` — Phase 7 modularization COMPLETE, closeout steps verified, archived 2026-04-15
  - `docs/archives/work_packets/branches/data-improve/topology/2026-04-15_topology_enforcement_hardening/` — topology enforcement hardening COMPLETE, merged to `data-improve`, branch-diff closeout verified, archived 2026-04-16
  - `docs/archives/work_packets/branches/data-improve/live_reorientation/2026-04-11_phase1live/` — stale Phase 1 live-only plan moved out of active operations on 2026-04-16
  - `docs/archives/work_packets/branches/data-improve/math_semantics/2026-04-16_k6_k7_k8_math_semantics/` — completed K6/K7/K8 remediation work log moved out of active operations on 2026-04-16
- Active backlog:
  - `docs/operations/task_2026-04-13_remaining_repair_backlog.md` — all items DEFERRED until DB rebuild completes
  - `docs/operations/task_2026-04-14_session_backlog.md` — sequential backfill PID 49371 running; post-backfill ETL cascade blocked on completion
  - `docs/operations/data_rebuild_plan.md` — upstream data rebuild plan (v2.2, scope: collect+store code prep while TIGGE completes)
- Stale/deferred:
  - Phase 1 live-only plan is archived; do not use it as an active control surface.
- Active checklist/evidence:
  - `docs/to-do-list/zeus_data_improve_bug_audit_75.xlsx` — 75-bug audit COMPLETE (Excel needs manual status update for Phase 4 bugs)
  - `docs/operations/runtime_artifact_inventory.md` — `.omx/.omc` planning artifact inventory and disposition guide
  - `docs/operations/task_2026-04-16_function_naming_freshness/plan.md` — small completed governance package for function naming and changed-file script/test freshness metadata
  - `docs/operations/task_2026-04-19_code_review_graph_topology_bridge/plan.md` — active package for Code Review Graph topology-first integration and Codex MCP safety
  - `docs/operations/task_2026-04-19_workspace_artifact_sync/plan.md` — active package syncing non-Phase-10A workspace artifacts and topology map updates
  - `docs/operations/task_2026-04-20_code_impact_graph_context_pack/plan.md` — active package adding derived Code Review Graph appendix to topology context packs
  - `docs/operations/task_2026-04-20_code_review_graph_online_context/plan.md` — active package tracking Code Review Graph DB as non-authority online repo context
- Next packet: Continue the Dual-Track Metric Spine Refactor (Phase 10C or architect-gated residue) unless a higher-priority governance break supersedes it.
- Post-P10B forward-log: B055 + B099 (architect-gated, no phase assigned); R10 Kelly strict / R12 H7 triage / R13 TRUTH_AUTHORITY_MAP (user-ruling required); monitor_refresh LOW plumbing + Literal→MetricIdentity P10C migration (explicitly deferred). Regression baseline: 144/1905/93.

## Runtime

- Sequential backfill PID 49371 running (`backfill_wu_daily_all.py --all --missing-only --days 834`)
- Waiter process PID 50114 (`post_sequential_fillback.sh`) polls every 60s, kicks off HKO + hole scanner on completion
- Post-backfill unlocks: Task #63 (ETL cascade, 11 steps), Task #61 (TIGGE transfer, 41K rows), historical forecasts recovery (171K rows)

## Recent Packet State

- Packet 0 closed as a read-only topology inventory baseline.
- Packet 1 closed the WMO half-up executable rounding law and recurrence gates.
- Packet 2 added the topology compiler MVP and `scripts/topology_doctor.py`.
- Packet 3 closed 2026-04-15 — docs mesh repair and authority normalization complete.
- Packet 4 closed 2026-04-15 — source file rationale map complete.
- Phase 10A: independent hygiene fix pack (R1 monitor rename + B071 + B091-lower + S5 doc flip). Merged `2026-04-19` at `81294d2`. Regression baseline: 142/1894/93.
- Phase 10B: DT-seam cleanup (R3+R4+R5+R9+R11). Merged `2026-04-19` at `f2ffcad`. Regression baseline: 144/1905/93.

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

Continue the Dual-Track Metric Spine Refactor unless a new architecture/governance blocker supersedes it.
