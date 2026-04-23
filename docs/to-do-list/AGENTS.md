# docs/to-do-list AGENTS

This directory holds active checklist/workbook surfaces that agents may use as
task queues or audit inventories. They are operational evidence, not authority.

## File registry

| File | Purpose |
|------|---------|
| `zeus_data_improve_bug_audit_100_dual_track_reassessment.md` | Dual-track reassessment notes for the 100-bug audit; evidence only |
| `zeus_bug100_reassessment_table.csv` | Machine-readable reassessment table for the 100-bug audit; evidence only |
| `zeus_live_readiness_upgrade_checklist_2026-04-23.md` | Live-readiness upgrade checklist (B1–B5 + G5–G10 + R1 + U1 + N1) derived from 2026-04-23 pro/con Opus debate converged verdict; task queue, not authority |
| `zeus_midstream_trust_upgrade_checklist_2026-04-23.md` | Midstream trust upgrade checklist (T1 test-currency + T2 midstream fails + T3 D4 + T4-D3/D1/D2/D5/D6) derived from 2026-04-23 pro/con Opus midstream debate converged verdict; task queue, not authority |
| `zeus_midstream_fix_plan_2026-04-23.md` | Joint 36-slice implementation plan v2 (Waves 1–4 CONDITIONAL at ~10 engineer-days / ~5 with 2-engineer parallelism, Wave 5 TRUSTWORTHY substrate-deferred) decomposed by pro-vega (T1/T2/T5/T7) + con-nyx (T3/T4/T6/N1); task queue, not authority |

## Rules

- Checklist workbooks are not active law.
- Do not make binary workbooks default reads.
- Allowed non-Markdown extensions are `.xlsx`, `.csv`, and `.json`; extending this list requires updating `architecture/topology.yaml` and `architecture/artifact_lifecycle.yaml`.
- If a workbook item becomes durable law, extract it into a machine manifest,
  test, contract, or lore card instead of pointing agents at the workbook by
  default.
