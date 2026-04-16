# Phase 2 Opener Digest

Three parallel opener dispatches ran before main-thread wrote `phase2_plan.md`.
This file captures the load-bearing findings; full reports were in-context and
synthesized into the plan.

## Opener 1 — architect (opus, read-only)

- v2 DDL sketch already exists in
  `zeus_dual_track_refactor_package_v2_2026-04-16/03_SCHEMA/01_world_db_v2.sql`;
  Phase 2 integrates it, does not re-invent.
- Per-table verdict + DDL refinements:
  - `settlements_v2`: sketch OK; add index `ON settled_at` for harvest scans.
  - `market_events_v2`: sketch OK; add partial index on open markets.
  - `ensemble_snapshots_v2`: sketch OK; `data_version NOT NULL` confirmed.
  - `calibration_pairs_v2`: sketch has NO UNIQUE — add
    `UNIQUE(city, target_date, temperature_metric, range_label, lead_days,
    forecast_available_at, bin_source, data_version)` mirroring legacy dedup.
  - `platt_models_v2`: sketch OK; `PRIMARY KEY(model_key)` is sufficient.
  - `observation_instants_v2`: sketch OK.
  - `historical_forecasts_v2`: sketch OK (ship Option A = single `forecast_value`
    column + metric).
  - `day0_metric_fact`: sketch OK; add
    `UNIQUE(city, target_date, temperature_metric, utc_timestamp, source)`.
- **Second DT#1 site** at `src/execution/harvester.py:380-386` (main-thread's
  mental model did not anchor it). Direction is correct (commit before JSON)
  but not routed through a tested helper; worth folding into Phase 2.
- `src/state/db.py` is 3864 lines (classic god-object). Proposed split:
  `schema/v2_schema.py`, `canonical_write.py`, `writers/*.py`,
  `queries/*.py`. Phase 2 lands only `v2_schema.py` + `canonical_write.py`;
  the broader split is a chore commit later.
- 4 dead tables confirmed: `promotion_registry`, `model_eval_point`,
  `model_eval_run`, `model_skill` — 0 rows, no writers, referenced only in
  `src/calibration/blocked_oos.py:239` docstring. Safe to DROP in v2
  migration.
- Also 0-row tables not dropped: `market_price_history`, `replay_results`,
  `forecast_error_profile`, `hourly_observations` — these have writers or
  audit paths; defer cleanup to separate chore.
- Both DB files (`zeus-world.db`, `zeus_trades.db`) carry identical 44-table
  schema — v2 migration must land in both.
- WAL + `PRAGMA foreign_keys=ON` concern: migration helper must not leave
  foreign keys disabled.

## Opener 2 — Explore writers/readers inventory

- `settlements`: 2 prod writers — `harvester.py:497,508` (canonical) and
  `wu_daily_collector.py:142` (settlement_value UPDATE; single-metric
  assumption; **not safe for v2 dual-metric without explicit target**).
  Flagged as Phase 4 cutover concern, not Phase 2.
- `market_events`: **zero prod writers**. Test-only INSERTs. Suggests market
  metadata is pulled from Gamma API at query time, not stored.
- `ensemble_snapshots`: no live writer found in `src/`; only backfill scripts
  write. Live TIGGE/ensemble ingestion pipeline appears absent from current
  codebase (or lives elsewhere).
- `calibration_pairs`: single-point production writer via
  `src/calibration/store.py:92 add_calibration_pair()`. **Easy rewire
  point** when Phase 4 cutover lands.
- `platt_models`: single writer at `store.py:259` with `INSERT OR REPLACE ON
  bucket_key` — safe under v2 because bucket_key is cluster×season, not
  city×date.
- `observation_instants`: `INSERT OR REPLACE` with UNIQUE including
  `utc_timestamp` — safe under v2.
- **No `INSERT OR REPLACE` pattern in legacy writers that breaks under v2
  dual-metric.** Good news.
- Duplicated DDL: `calibration_decision_group` declared both inline in
  `db.py:294-307` and as string constant `_CALIBRATION_DECISION_GROUP_DDL`
  at `db.py:841-857` — chore cleanup, not Phase 2.

## Opener 3 — simplification scout

- Code hygiene is high. Very few TODOs older than 30 days; no `@deprecated`
  decorators; no `.py.bak` / `.py.old` files.
- One no-op live: `scripts/migrate_rainstorm_full.py` — self-reports
  COMPLETE, called at `src/main.py:249`. Phase 2 deletes script + removes
  the call.
- Known orphan utility candidates (`scripts/parse_change_log.py`,
  `scripts/venus_autonomy_gate.py`) — defer. Low confidence; they read
  memory/ which is outside the repo.
- Test files larger than 2000 lines exist but thematically coherent
  (`test_runtime_guards.py` 5307, `test_db.py` 3011). Split later as chore.
- Acknowledged documented duplications in data fetch layer
  (`src/data/*_append.py` vs `scripts/backfill_*`) — intentional
  decoupling, do not consolidate.
- `state/auto_pause_failclosed.tombstone` — brand new, active runtime
  marker, not a stale orphan.
- `k_bugs.json` — actively curated bug tracker (56+ entries, last modified
  same day); not stale.
- `.claude/worktrees/data-rebuild/` — fully merged into data-improve; no
  divergence.

## Main-thread ratified decisions (from 5 architect asked)

1. harvester.py:380-386 is in Phase 2 scope (folded into DT#1 fix).
2. 4 dead tables DROPPED inside Phase 2 migration.
3. v2 DDL lives in new `src/state/schema/v2_schema.py`.
4. Both DB files get v2 schema.
5. `save_portfolio` gains `last_committed_artifact_id` field.

See `phase2_plan.md` §3 for rationale.
