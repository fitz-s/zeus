# validation_matrix.md

| Area | Required check | Current result | Pass condition after fixes |
|---|---|---|---|
| Schema inventory | `schema_inventory.sql` | v2 scaffolding exists; several v2 tables empty | Canonical paths target v2 tables/views. |
| Row counts | `table_counts.sql` | Forecast/model/replay tables empty | Required canonical tables populated or explicitly not in scope. |
| Provenance | `provenance_coverage_checks.sql` | WU daily provenance empty at scale | Zero canonical rows with empty provenance. |
| DST/time geometry | `timezone_dst_checks.sql` | v2 good; legacy hourly unsafe | No canonical local-day anomalies; legacy excluded. |
| Settlement alignment | `settlement_alignment_checks.sql` | v1 high-only, market_slug null | v2 market/metric/station identity populated. |
| Source tiering | `source_tiering_checks.sql` | fallbacks mixed in current family | Source role and eligibility gates enforced. |
| Causality | `causality_checks.sql` | forecast/ensemble/calibration empty | Verified issue/available/fetch times and training flags. |
| Backfill consistency | `backfill_consistency_checks.sql` | missing forecast coverage and partial-risk | Coverage and physical rows reconcile. |
| Downstream usage | grep/tests | unsafe table use possible | Consumers read safe views only. |
