# W8 — Dead-table roll: three-way diff (sqlite_master vs manifest vs live code)

Evidence discipline: every claim below is either an exact query result (safe-pattern
sqlite_master read, rule 4) or a `file:line` `rg` hit. Nothing here required dbstat,
VACUUM, or any write — all reads were single bounded `sqlite_master` scans plus static
`rg` over `src/` and `scripts/`. WAL files checked before and after (rule 5): none
exceeded 512MiB at any point (`zeus_trades.db-wal` was 175MB pre-scan, all three
canonical WALs were 0 bytes post-scan — normal live checkpoint activity, not caused by
this audit).

## 0. Coverage caveat — census_raw.jsonl is NOT DB-complete

`census_raw.jsonl` (the only byte-truth source this lane is allowed to use) has:

| `db` field value | rows | tables covered |
|---|---|---|
| `trades` | 274 | 80 tables (≈ all 79 on-disk trade tables) — effectively complete |
| `forecasts` | 57 | ~40 of 43 on-disk forecasts tables — near-complete |
| **world** | **0** | **zero rows for `zeus-world.db` anywhere in the file** |

**zeus-world.db (the 84GB WORLD_CLASS DB) has NO size coverage in the census at all.**
Every "dead" or "ghost" claim below that touches world.db is a **code-reference /
sqlite_master-presence** finding only — no current byte size or row count can be cited
for any world.db table, including the documented legacy position-table ghosts. Do not
treat the manifest's 2026-05-17 "0 rows / EMPTY" audit note as current fact; it is
~2 months stale and unverifiable from this lane's tools.

## 1. A-not-B: unregistered on-disk tables (invisible per root AGENTS §4)

### zeus-world.db: 1 table
- `_migrations_applied` — present on disk, declared in manifest **only** for `db: trade`
  (line ~90-104). Mechanical, not a money-path risk: `scripts/migrations/__init__.py:32`
  `_ensure_ledger()` is a generic per-connection ledger creator; `scripts/deploy_live.py:1036`
  calls `apply_migrations(world_conn, ..., db_identity='world')` at every deploy, which
  legitimately creates this table on world.db too. The registry simply never grew a
  `db: world` sibling entry for it. Same table also exists on forecasts.db (see below) via
  the identical mechanism (`scripts/migrations/__main__.py` supports `--db-identity forecasts`
  for manual operator runs). **Fix is a 3-line manifest addition, not a code change.**

### zeus-forecasts.db: 18 tables — the significant finding of this lane

`_migrations_applied` (same mechanical gap as above) plus **17 more**, which split into
two very different categories:

**(a) 4 fully orphaned migration-rename artifacts — zero code references anywhere, genuine DEAD_DELETE candidates:**

| table | rows | bytes | rg hits (src+scripts) |
|---|---|---|---|
| `readiness_state_legacy_no_ready_20260607T131810Z` | 5,244 | 5.3MB | 0 |
| `deterministic_forecast_anchors_legacy_coarse_unique_20260607T131448Z` | not in census | — | 0 |
| `forecast_posteriors_legacy_coarse_unique_20260607T131448Z` | not in census | — | 0 |
| `replacement_shadow_decisions_legacy_coarse_unique_20260607T131448Z` | not in census | — | 0 |

The `_20260607T131448Z` / `_20260607T131810Z` suffixes are timestamp markers from a
2026-06-07 rebuild-and-rename migration (same idiom documented elsewhere in the manifest
for `no_trade_events_new`, `evidence_tier_assignments_new`: create-new, copy, drop-old,
rename-to-canonical). The rename evidently completed and the transient pre-rename names
were left behind on disk instead of being dropped. They are declared **nowhere** in the
manifest (not even as `legacy_archived`) and have **zero** `rg` hits under `src/` or
`scripts/` — no writer, no reader, no migration script that still references them by
name. `readiness_state_legacy_no_ready_20260607T131810Z` alone carries 5,244 rows / 5.3MB
of pure dead weight. **Verdict: DEAD_DELETE, all 4.**

**(b) 13 undocumented "world-schema-on-forecasts" ghost duplicates — parallel to the already-known trade contamination, but never registered:**

`data_coverage`, `day0_metric_fact`, `decision_log`, `hko_hourly_accumulator`,
`market_price_history`, `observation_instants`, `observation_revisions`, `platt_models`,
`refit_bucket_failures`, `rescue_events`, `settlement_schema_migrations`,
`validated_calibration_transfers`, `zeus_meta`.

Every one of these names is already registered `db: world` (mostly `world_class`) in the
manifest — they are the **canonical** world tables. All code references (`rg` confirmed
per-table) point at the world-owned writer/reader, e.g. `day0_metric_fact_store.py` line
119 explicitly asserts `"write_day0_metric_fact requires a world DB connection"`. Yet the
physical file `zeus-forecasts.db` also carries a table by the same name for all 13, and
the manifest has **no `db: forecasts` entry for any of them** — this is structurally
identical to the trade DB's documented "pre-PR-S4b `init_schema(trade_conn)` created 66
world-schema tables on the trade file" contamination (manifest lines ~2438-2452), except
that event was never analyzed or registered for the forecasts file. One data point is
directly corroborating: `scripts/task_2026-06-09_drop_dead_tables.py:62-65` already
targets `platt_models` on `zeus-forecasts.db` for drop, with the comment *"platt_models on
forecasts.db is 0-row and RECREATED empty by tigge_pipeline... dropping it is cosmetic"* —
confirming the operator already knows this ghost class exists on forecasts.db but only
chased one of the 13 names.

`data_coverage` on forecasts.db is not cosmetic: 2,959 rows / 299KB per census — worth an
explicit read-provenance check (is anything reading it under `write_class` confusion?);
`rg -i data_coverage` shows only the canonical world-scoped writer
(`get_forecasts_connection_with_world SAVEPOINT`, per the manifest note) — no distinct
forecasts-local reader found, consistent with ghost/contamination, not a second authority.

**Verdict: registry gap, not (yet) proven a live-money risk — but `assert_db_matches_registry(FORECASTS)`
set-equality is either silently tolerating these 13+1 unregistered tables or the boot
gate has a broader carve-out than the yaml documents. Either way the registry cannot
currently answer "what tables legitimately exist on forecasts.db" — recommend the same
audit-and-register-as-legacy_archived treatment already given to trade.db's ghosts.**

**(c) 1 fully live, actively-written-and-read table with ZERO registry entry — the standout finding:**

`day0_hourly_vectors` — **15,480 rows, 21.2MB** per census, present on `zeus-forecasts.db`,
**absent from `architecture/db_table_ownership.yaml` entirely** (not even as a ghost —
checked the whole 3,358-line file). This is not contamination: it is a real, actively
maintained table.
- DDL: `src/data/day0_hourly_vectors.py:84` `CREATE TABLE IF NOT EXISTS day0_hourly_vectors`
- Writes: `persist_day0_hourly_vectors()` at `src/data/day0_hourly_vectors.py:396` (`INSERT OR IGNORE`), `:414` (`DELETE FROM ... WHERE captured_at < ?` retention trim)
- Reads: `read_freshest_day0_hourly_vectors()` at `:474` (`FROM day0_hourly_vectors`); also read from `src/data/replacement_forecast_materializer.py:635,646` and referenced by `src/engine/monitor_refresh.py:1256`

This is a live forecast-pipeline table with real production traffic that has simply never
been added to the ownership registry. **This is the highest-severity A-not-B finding in
this lane** — root `AGENTS.md` §4 treats unregistered = invisible; a boot-time
`assert_db_matches_registry(FORECASTS)` set-equality check should already be failing or
silently permissive for this table, and no operator would know from the manifest alone
that this table, its retention policy, or its consumers exist.

### zeus_trades.db: 0 unregistered tables
Clean — every on-disk trade table has a manifest entry (`db: trade`). A ⊆ B holds.

## 2. B-not-A: manifest declares, disk doesn't have (manifest rot)

- **world: 20 entries**, **trade: 16 entries**, **forecasts: 1 entry** (`platt_oos_decisions`
  — matches its own manifest note: "UNWIRED... never gates a trade", plausibly never
  materialized).
- Cross-checked against `scripts/task_2026-06-09_drop_dead_tables.py`, an already-executed
  operator one-off (`.omc/research/dead_table_live_read_proof.md`, "21/21 DEAD-SAFE, 0 live
  readers"): its exact target lists —
  world: `calibration_pairs_v2_archived_2026_05_11`, `ensemble_snapshots_v2_archived_2026_05_11`,
  `observations_archived_2026_05_11`, `settlements_archived_2026_05_11`,
  `settlements_v2_archived_2026_05_11`, `market_events_v2_archived_2026_05_11`,
  `source_run_archived_2026_05_11`, `forecast_error_profile`, `day0_residual_fact`,
  `settlements_v2` (10/10 confirmed absent from world disk);
  trade: `historical_forecasts_v2`, `rescue_events_v2`, `market_events_v2`,
  `platt_models_v2`, `observation_instants_v2`, `settlements_v2`, `ensemble_snapshots_v2`
  (7/7 confirmed absent from trade disk) —
  **explains 17 of the 36 B-not-A entries: they were deliberately dropped in 2026-06 and
  the yaml manifest was simply never edited to remove the now-stale entries.**
- `ensemble_snapshots` (bare name, world, B-not-A) is separately explained by an in-file
  comment (`architecture/db_table_ownership.yaml` ~line 818): *"v1.F20 (2026-05-18):
  ensemble_snapshots removed — table dropped via
  scripts/migrations/202605_drop_ensemble_snapshots_legacy.py."*
- `shadow_experiments`, `shadow_signals` (world, B-not-A) are explicitly self-documented
  as test-fixture-only, never created in production (manifest note, verbatim: *"Never
  created in production DBs"*) — not a real drop, expected absence.
- Remaining un-corroborated B-not-A names (`calibration_pairs_v2` [world],
  `evidence_tier_assignments_new`, `no_trade_events_v2`, `settlement_commands_era_quarantine`,
  `settlement_outcomes` [world bare-name ghost], `tail_stress_scenarios` [both world+trade])
  are all either transient rebuild-only names (documented in-yaml as "never live outside a
  migration transaction") or on-demand operator-migration artifacts (`settlement_commands_era_quarantine`)
  that legitimately have zero current instances. No evidence of an undocumented drop.

**Verdict: this whole class is registry hygiene, not risk. Recommend a single cleanup PR
that deletes the ~30 confirmed-already-dropped / never-materialized manifest entries once
someone re-verifies against a fresh sqlite_master snapshot (this lane already did that
verification above).**

## 3. On-disk + zero code reference = true DEAD_DELETE candidates

Beyond the 4 forecasts orphans in §1(a), two more on world.db, both already correctly
flagged `legacy_archived` with a drop date in the manifest — this lane just confirms
zero code references corroborate the manifest's own verdict:

- `observations_disputed_migrated` (world) — manifest: "164 rows fully duplicated by the
  authoritative copy," drop after 2026-08-09. 0 `rg` hits under src/scripts. Consistent.
- `settlements_disputed_migrated` (world) — manifest: "0 rows," drop after 2026-08-09.
  0 `rg` hits. Consistent.

No new evidence contradicts the manifest's existing drop schedule for these two.

## 4. Write-only tables (writer present, no reader found)

| table | db(s) | writer evidence | reader evidence | verdict |
|---|---|---|---|---|
| `day0_hourly_vectors` — see §1(c) | forecasts | active | active | NOT write-only (unregistered live table, not a log) |
| `day0_metric_fact` | world (canonical) | `src/state/day0_metric_fact_store.py:196` `INSERT INTO` | none found (`rg -i day0_metric_fact` full dump: 0 SELECT/FROM hits) | **write-only by design** — module docstring: *"It is an audit surface... not a second probability authority"*. Legitimate append-only observability log. |
| `refit_bucket_failures` | world (canonical) | `scripts/refit_platt.py:912` `INSERT INTO` | none found | **write-only by design** — comment: "Write failure record... for operator triage." Legitimate manual-read log. |
| `edli_live_cap_day_slots`, `edli_live_cap_rate_window` | world (canonical) | only `DELETE FROM` (`src/events/live_cap.py:238-239`, cleanup path) | none found; no `INSERT INTO` anywhere either | **retired, self-documented**: `src/state/schema/edli_live_cap_usage_schema.py:1-10` states verbatim *"2026-06-08: the tiny_live notional + order-count caps are DELETED... `edli_live_cap_day_slots` and `edli_live_cap_rate_window` tables are no longer written to... they remain defined only so legacy rows and the recovery/cleanup paths in command_recovery keep resolving."* **Registry mismatch**: both are still classed `world_class` (active) in the manifest; code says they are frozen/retired. Recommend reclassifying to `legacy_archived` to match actual code state — cosmetic, not a risk (the recovery-path reads via `_edli_live_cap_ref` still need the tables to exist for old rows). |

## 5. Documented legacy ghost-shell position tables in zeus-world.db

Confirmed still present on disk (all still exist in `sqlite_master` for zeus-world.db):
`trade_decisions`, `execution_fact`, `position_events`, `position_current`, `position_lots`,
`venue_commands`, `venue_command_events`, `venue_order_facts`, `venue_trade_facts`,
`venue_submission_envelopes`, `settlement_commands`, `settlement_command_events` — all
`schema_class: legacy_archived`, "Authoritative on trade.db," drop-after 2026-08-15 per
manifest. **Cannot confirm row counts or current byte size — zero world.db census
coverage (§0)**. The manifest's "EMPTY at audit time 2026-05-17 (0 rows each, verified)"
claim is unverifiable from this lane and is now ~2 months old; if disk-size relief is the
goal of the broader audit, these ghosts need a fresh row-count check via the approved
rowid-window sampling pattern (rule 3) before anyone assumes they are still empty.

## 6. `_migrations_applied` / `schema_epoch` semantics check

- **`_migrations_applied`**: generic per-DB migration ledger, `CREATE TABLE IF NOT EXISTS`
  (`scripts/migrations/__init__.py:32` `_ensure_ledger`), designed to be created on any of
  the 3 canonical DBs. Only ONE call site is wired into daemon boot
  (`src/state/db.py:6278-6279`, hardcoded `db_identity="trade"`); the world.db and
  forecasts.db copies come from `scripts/deploy_live.py`'s restart-recovery migration
  runner (world only, line 1036) and/or manual `python -m scripts.migrations apply
  --db-identity forecasts` operator runs respectively. Semantically consistent — not a
  bug — but only registered in the yaml for `db: trade` (§1 finding).
- **`schema_epoch`**: correctly registered on all 3 DBs (`db: trade`, `db: world`,
  `db: forecasts`, each `created_by: scripts/migrations/2026_07_quarantine_phase_retirement.py`).
  Confirmed present in all 3 physical `sqlite_master` dumps. No drift found — this is the
  system working as designed (`assert_schema_epoch_not_mixed` guard). No finding.

## Summary (severity order)

1. **HIGH** — `day0_hourly_vectors` on zeus-forecasts.db: live, actively read/written
   (21.2MB/15,480 rows), completely absent from `architecture/db_table_ownership.yaml`.
2. **MEDIUM** — zeus-forecasts.db carries an undocumented 13-table class of world-schema
   ghost duplicates (data_coverage, observation_instants, platt_models, zeus_meta, etc.),
   structurally identical to the already-registered trade-DB contamination but never
   analyzed/registered for forecasts. `_migrations_applied` unregistered on world+forecasts
   is the same class, mechanically explained, near-zero risk.
3. **LOW** — 4 fully dead migration-rename artifacts on forecasts.db (0 code refs;
   `readiness_state_legacy_no_ready_20260607T131810Z` alone is 5.3MB), safe DEAD_DELETE.
4. **LOW** — ~30 manifest entries describing tables already dropped by the audited
   2026-06-09 cleanup script or never materialized; registry hygiene only.
5. **INFO** — 2 more world.db legacy_archived tables corroborated dead by 0 code refs
   (already scheduled to drop 2026-08-09, no new action needed).
6. **INFO** — `edli_live_cap_day_slots`/`edli_live_cap_rate_window` self-documented as
   retired in code (2026-06-08) but still classed active (`world_class`) in the registry —
   cosmetic mismatch.
7. **BLOCKING CAVEAT** — zeus-world.db has zero census coverage; no size claim in this
   report (including the documented legacy position-table ghosts) can be corroborated with
   current bytes. Any disk-reclaim decision needs a fresh, safe-pattern row-count/size pass
   on world.db first.
