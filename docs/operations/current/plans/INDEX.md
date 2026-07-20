# Plans Index

Plans under `docs/operations/current/plans/`. Rewritten 2026-07-07 against disk + git state; superseded/merged plans removed (bodies in git history).

| Plan | Status | Purpose |
|------|--------|---------|
| [`hourly_capital_gains_improvement_loop.md`](hourly_capital_gains_improvement_loop.md) | ACTIVE | Forward journal — the single work-state surface. Read this first. |
| [`upstream_data_physical_2026-07-17.md`](upstream_data_physical_2026-07-17.md) | EXECUTING | Upstream forecast-layer体检:3 live bugs fixed (low-ENS quarantine, ENS age bound, degF postmortem hardening), consult verdict folded, D2-D6 design queue open |
| [`gate_stack_simplification_2026-07-06.md`](gate_stack_simplification_2026-07-06.md) | EXECUTING | Gate collapse; Phase 1+2 deployed 2026-07-07, C1 sole-authority design pass open |
| [`allday_improvement_loop_v3_codex_2026-07-09.md`](allday_improvement_loop_v3_codex_2026-07-09.md) | ACTIVE | 24/7 loop v3:codex 沙箱执行体、单 tick、INTERVAL 旋钮、查询 escrow、自进化棘轮;执行件 loop/ + loop_guard.py |
| [`allday_improvement_loop_design_2026-07-06.md`](allday_improvement_loop_design_2026-07-06.md) | SUPERSEDED-METHOD-AUTHORITY | v2 设计:方法论/三级权限/账本法仍是权威;执行体细节被 v3 取代 |
| [`order_engine_rebuild_execution_plan_2026-07-02.md`](order_engine_rebuild_execution_plan_2026-07-02.md) | OPEN | Order-engine v2 execution packets (authority chain in docs/rebuild/) |
| [`../../../rebuild/EXECUTION_MASTER_2026-07-07.md`](../../../rebuild/EXECUTION_MASTER_2026-07-07.md) | READY | 重构执行总纲:compact 后重对齐入口(§A),R0-R8 packet 队列 + 拓扑 + 验收模板;待操作员三开关(§I)|
| [`../../../rebuild/whole_system_first_principles_2026-07-07.md`](../../../rebuild/whole_system_first_principles_2026-07-07.md) | PROPOSED | 全系统第一性原理蓝图:8 子系统判决 + 疤痕根除审计(§7)+ consult 对撞(§8);执行细节看 EXECUTION_MASTER |
| [`../../../rebuild/representation_contract_2026-07-08.md`](../../../rebuild/representation_contract_2026-07-08.md) | PROPOSED | 表示层契约(agent-first):注释/命名/元数据/锚点四法 + 实测底账 + 逐 surface 判决;R-wave 第四维,落地包 = R0-h |
| [`percity_representativeness_debias.md`](percity_representativeness_debias.md) | OPEN | Per-city representativeness de-bias |
| [`live_redecision_repair/PLAN.md`](live_redecision_repair/PLAN.md) | IMPLEMENTING | Held-position redecision + exit readiness repair |
| [`live_unit_price_band_incident/PLAN.md`](live_unit_price_band_incident/PLAN.md) | IMPLEMENTING | Reopened live-money incident: restore absolute submit band and make regressions restart-blocking |
| [`data_temporal_kernel/PLAN.md`](data_temporal_kernel/PLAN.md) | DORMANT | Ingest temporal control plane (additive; no commits since 2026-06) |
| [`ci_topology_refactor_refined.md`](ci_topology_refactor_refined.md) | DORMANT | CI topology refactor (proposed 2026-05-26, untouched since) |
| [`zeus_home_repo_migration.md`](zeus_home_repo_migration.md) | DONE-EXCEPT | Migration complete (daemons run from ~/zeus); one residue: heartbeat-sensor plist still points at old workspace-venus/bin |

Removed 2026-07-07: ../live-redecision-f109-repair/ (F109 consolidator fix landed; antibody test tests/state/test_f109_consolidator_boot_wire.py is the durable record); live_math_frontier/, crosscheck_valid_window/, live_release_blocker_repair/ (targeted the legacy discovery pipeline deleted in Phase 2 — opening_hunt/SOURCE_COMPARABILITY narratives are dead code paths); pr332_* (PR #332 merged 2026-05-28); live_family_qkernel_repair (PR #412 merged); edli_training_cutoff, horse_race_kelly, real_live_hotfix_allocator_reconcile (work landed in June commits; see git).
