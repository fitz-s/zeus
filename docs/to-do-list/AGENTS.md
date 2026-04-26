# docs/to-do-list AGENTS

This directory holds active checklist/workbook surfaces that agents may use as
task queues or audit inventories. They are operational evidence, not authority.

## File registry

| File | Purpose |
|------|---------|
| `zeus_operations_archive_deferrals_2026-04-24.md` | Operations archive deferrals workbook (2026-04-26 status: D3 absorbed by `zeus-pr18-fix-plan-20260426`; D4 closed; D5 near-closed; D1+D2 awaiting operator decision); task queue, not authority |
| `archive/2026-04-26_closed/` | Workbooks archived in the 2026-04-26 audit pass (5 files). Verdicts and successor packets recorded in directory README. Open items from `zeus_live_readiness_upgrade_checklist_2026-04-23.md` were absorbed into `docs/operations/task_2026-04-26_live_readiness_completion/`. |

## Rules

- Checklist workbooks are not active law.
- Do not make binary workbooks default reads.
- Allowed non-Markdown extensions are `.xlsx`, `.csv`, and `.json`; extending this list requires updating `architecture/topology.yaml` and `architecture/artifact_lifecycle.yaml`.
- If a workbook item becomes durable law, extract it into a machine manifest,
  test, contract, or lore card instead of pointing agents at the workbook by
  default.
