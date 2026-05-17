# docs/artifacts AGENTS

This directory holds active evidence artifacts that are useful for audits or
workbooks but are not authority. Prefer generated inventories or work records
over making binary artifacts default reads.

## File registry

| File | Purpose |
|------|---------|
| `polymarket_city_settlement_audit_2026-04-14.md` | Historical evidence snapshot for city settlement-source/station changes; not current authority |
| `tigge_cloud_wiring_snapshot_2026-04-19.md` | Dated local/cloud TIGGE wiring snapshot and 2026-04-21 rebalance evidence; not a durable runbook |
| `tigge_data_training_handoff_2026-04-23.md` | Dated handoff for completed TIGGE raw/extraction/validation state and the next Zeus training steps |
| `Zeus_May2_review_ strategy_update.md` | May 2 strategy update review artifact used as evidence for the Stage 0/1 execution plan; not authority |
| `Zeus_May6_calibration_low_metric_shortage_and_fallback.md` | 2026-05-06 root-cause investigation artifact for the calibration READ-path: corrected fallback-chain semantics + Law 1 boundary-ambiguous LOW rejection; operator decision matrix for LOW-metric posture at launch; not authority |
| `zeus_architecture_deep_map_2026-04-16.md` | Legacy architecture deep-map snapshot; extracted to `docs/reference/zeus_architecture_reference.md`, evidence only |
| `Zeus_Apr25_review_topology.md` | 2026-04-25 topology review artifact; evidence only |
| `Zeus_Apr26_review_polymarket.md` | 2026-04-26 Polymarket review artifact; evidence only |
| `Zeus_May2_review_data_deamon.md` | 2026-05-02 data daemon review artifact; evidence only |
| `Zeus_May3_review_ultimate.md` | 2026-05-03 ultimate review artifact; evidence only |

## Rules

- Artifacts here are not active law.
- Do not make binary workbooks default reads.
- Allowed non-Markdown extensions are `.xlsx`, `.csv`, and `.json`; extending this list requires updating `architecture/topology.yaml` and `architecture/artifact_lifecycle.yaml`.
- Extract durable lessons into `architecture/history_lore.yaml` or machine
  manifests instead of pointing agents at the workbook by default.
