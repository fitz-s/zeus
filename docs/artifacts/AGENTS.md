# docs/artifacts AGENTS

This directory holds active evidence artifacts that are useful for audits or
workbooks but are not authority. Prefer generated inventories or work records
over making binary artifacts default reads.

## File registry

| File | Purpose |
|------|---------|
| `polymarket_city_settlement_audit_2026-04-14.md` | Historical evidence snapshot for city settlement-source/station changes; not current authority |
| `tigge_cloud_wiring_snapshot_2026-04-19.md` | Dated local/cloud TIGGE wiring snapshot and 2026-04-21 rebalance evidence; not a durable runbook |
| `zeus_architecture_deep_map_2026-04-16.md` | Legacy architecture deep-map snapshot; extracted to `docs/reference/zeus_architecture_reference.md`, evidence only |

## Rules

- Artifacts here are not active law.
- Do not make binary workbooks default reads.
- Allowed non-Markdown extensions are `.xlsx`, `.csv`, and `.json`; extending this list requires updating `architecture/topology.yaml` and `architecture/artifact_lifecycle.yaml`.
- Extract durable lessons into `architecture/history_lore.yaml` or machine
  manifests instead of pointing agents at the workbook by default.
