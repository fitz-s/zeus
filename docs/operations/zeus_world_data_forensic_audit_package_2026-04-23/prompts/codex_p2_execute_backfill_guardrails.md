# codex_p2_execute_backfill_guardrails.md

Execute after P1 passes.

Goals:
1. Make backfill/repair scripts dry-run default with explicit `--apply` and manifest output.
2. Add expected-count, local-day-hour-count, station-support, source-quota, and failed-window manifests.
3. Replace unsafe `INSERT OR REPLACE` with hash-checked idempotence or revision-history writes.
4. Mark fallback rows `training_allowed=0` unless a policy explicitly approves them.
5. Extend `data_coverage` or add v2 coverage for settlements_v2, ensemble_snapshots_v2, historical_forecasts_v2, calibration_pairs_v2.

Verification:
- Backfill dry-runs produce deterministic manifests.
- Partial failure fixtures fail readiness.
- No DB mutation occurs without `--apply`.
