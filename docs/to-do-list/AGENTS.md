# docs/to-do-list AGENTS

This directory holds active checklist/workbook surfaces that agents may use as
task queues or audit inventories. They are operational evidence, not authority.

## File registry

| File | Purpose |
|------|---------|
| `zeus_data_improve_bug_audit_75.xlsx` | Data-improvement bug-audit checklist workbook; evidence/task inventory only |

## Rules

- Checklist workbooks are not active law.
- Do not make binary workbooks default reads.
- Allowed non-Markdown extensions are `.xlsx`, `.csv`, and `.json`; extending this list requires updating `architecture/topology.yaml` and `architecture/artifact_lifecycle.yaml`.
- If a workbook item becomes durable law, extract it into a machine manifest,
  test, contract, or lore card instead of pointing agents at the workbook by
  default.
