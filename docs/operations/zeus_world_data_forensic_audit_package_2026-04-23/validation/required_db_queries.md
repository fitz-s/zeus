# required_db_queries.md

Run all SQL files in `/sql`. Minimum pass/fail gates:

1. `table_counts.sql`: report all row counts; canonical readiness fails if forecast/ensemble/calibration/replay/market tables are unexpectedly empty.
2. `provenance_coverage_checks.sql`: canonical readiness fails if any `VERIFIED` canonical row has empty provenance.
3. `settlement_alignment_checks.sql`: canonical readiness fails if settlement rows lack market identity, metric, unit, station/source, or finalization metadata.
4. `timezone_dst_checks.sql`: canonical readiness fails on nonpositive local-day windows or unexplained local-day row-count anomalies.
5. `causality_checks.sql`: canonical readiness fails if forecast rows have missing issue/available/fetch time or training rows lack `causality_status='OK'`.
6. `source_tiering_checks.sql`: canonical readiness fails if fallback-like sources appear in training/settlement views by default.
7. `backfill_consistency_checks.sql`: canonical readiness fails if coverage says written but no physical row exists, or physical rows exist without coverage.
8. `suspicious_rows_queries.sql`: investigate all rows returned before using them in model/trading paths.

Current known blocker values from uploaded DB:

- `forecasts`: 0
- `ensemble_snapshots_v2`: 0
- `calibration_pairs_v2`: 0
- `market_events_v2`: 0
- `settlements_v2`: 0
- WU/daily empty provenance: 39,431
- settlement `market_slug` null: 1,561
