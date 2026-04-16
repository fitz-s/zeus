# Low-Track Data Readiness Audit

Date: 2026-04-16
Branch: data-improve
Auditor: scientist agent (aa45b297)
Phase context: Phase 2 landed (v2 schema), Phase 3 not yet started

---

## Summary

The low lane is **not runnable today**. The single biggest blocker is that
`ingest_grib_to_snapshots.py` — the mandatory GRIB-to-DB bridge for BOTH
tracks — is a placeholder stub (task #53, not implemented). Without it, 65 GB
of `mn2t6` GRIB archive cannot enter `ensemble_snapshots_v2`, and every
downstream layer (calibration pairs, Platt models, settlements) remains at zero
for the low metric. The secondary blocker is that `rebuild_calibration_pairs_canonical.py`
has no `temperature_metric` parameter and will only produce high-track pairs
until it is extended for low. The good news: `observations.low_temp` is
**fully populated** across all 51 cities (42,504 rows, 100% coverage), so
the ground-truth Y for low-track calibration is ready — Phase 5 is not blocked
on observations.

---

## Layer-by-layer gap

### Layer 1 — Raw TIGGE archive

- **Archive path**: `/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/`
- **mn2t6 GRIB files**: 413 files across 4 regions × ~52 batch directories
  (each batch covers a 3-day window). Date range: **2024-01-01 to 2025-09-24**.
  Status: **GRIB only — 0 JSON extracted**.
- **mx2t6 comparison**: 420 GRIB files, same 4-region structure, same date
  range (2024-01-01 to 2025-09-24). Status: **GRIB only — 0 JSON extracted**.
- **Archive symmetry**: mn2t6 and mx2t6 archives are structurally identical
  (4 regions, ~52 batch dirs each, comparable file counts). Coverage gap
  relative to observations (which reach back to 2023-12-27): TIGGE does not
  cover 2023-12-27 through 2023-12-31 — a 5-day tail not available in either
  archive.
- **Disk size**: 65 GB each (mn2t6 and mx2t6), totaling 130 GB of GRIB-only
  regional archives.
- **Verdict**: Raw archive is present and symmetric between tracks.
  **Blocker: no extraction pipeline exists (ingest_grib_to_snapshots.py is stub).**

### Layer 2 — ensemble_snapshots / ensemble_snapshots_v2

- **Legacy `ensemble_snapshots`**: 0 rows (table exists, never populated).
- **`ensemble_snapshots_v2` metric=high**: 0 rows.
- **`ensemble_snapshots_v2` metric=low**: 0 rows.
- Both DBs (`zeus-world.db` and `zeus_trades.db`) have identical empty state
  for all v2 tables — Phase 2 created the schema but no data has been written.
- **Date range coverage (high v2)**: none — ingest has never run.
- **Gap for low parity**: approximately **451,962 row-equivalents** needed
  (51 cities × 633 TIGGE-coverage days × 2 issue_times × 7 lead steps).
  Gap is identical for high and low since neither has been ingested.
- **Verdict**: MISSING. Blocked by task #53 (ingest_grib_to_snapshots.py stub).

### Layer 3 — calibration_pairs / calibration_pairs_v2

- **Legacy `calibration_pairs`**: 0 rows. Schema confirmed (no `data_version`
  column, no `temperature_metric` column — single-track design).
- **`calibration_pairs_v2` metric=high**: 0 rows.
- **`calibration_pairs_v2` metric=low**: 0 rows.
- Rebuild script: `scripts/rebuild_calibration_pairs_canonical.py` (current
  implementation). **Critical gap**: this script has no `temperature_metric`
  parameter and no `mn2t6` logic — it was written for the high track only.
  It is also gated on `ensemble_snapshots` being populated, which it is not.
- **Verdict**: MISSING. Two prerequisites unmet:
  (1) Layer 2 must be populated first;
  (2) `rebuild_calibration_pairs_canonical.py` must be extended for low metric.

### Layer 4 — platt_models / platt_models_v2

- **Legacy `platt_models`**: 0 rows.
- **`platt_models_v2` metric=high**: 0 rows.
- **`platt_models_v2` metric=low**: 0 rows.
- Refit script: `scripts/refit_platt.py` (existing). Low-track refit requires
  `calibration_pairs_v2` with sufficient samples per
  `(temperature_metric, cluster, season, lead_days)` bucket.
- **Estimated low Platt models needed**: ~140 rows
  (5 clusters × 4 seasons × 7 lead_steps).
- **Verdict**: MISSING. Causally blocked on Layer 3.

### Layer 5 — observations (daily settlement history)

- **Total rows**: 42,504 across 51 cities.
- **`high_temp` populated**: 42,504 / 42,504 (100%).
- **`low_temp` populated**: 42,504 / 42,504 (100%).
- **Date range**: 2023-12-27 to 2026-04-16.
- **Per-city coverage**: all 51 cities fully covered; minor gaps at Cape Town
  (811 rows vs ~835 typical), Istanbul (756), Hong Kong (821), Lagos (823).
- **Verdict**: READY. The ground-truth Y for low-track calibration is complete.
  This is the one layer that does NOT block Phase 5.

### Layer 6 — historical_forecasts / historical_forecasts_v2

- **Legacy `historical_forecasts`**: 0 rows. Schema confirmed as high-only
  (`forecast_high REAL NOT NULL` — no `forecast_low`, no `temperature_metric`).
- **`historical_forecasts_v2` metric=high**: 0 rows.
- **`historical_forecasts_v2` metric=low**: 0 rows.
- The v2 schema correctly adds `temperature_metric` and `forecast_value` (not
  split high/low columns). The ingest script `etl_historical_forecasts.py`
  still writes to `model_skill` (legacy) and has not been migrated to v2.
- **Verdict**: MISSING (both tracks). The low-track gap is complete and
  expected — no backfill path exists yet.

### Layer 7 — settlements / settlements_v2

- **Legacy `settlements`**: 1,562 rows (2025-12-30 to 2026-04-16, 50 cities).
  Schema is single-track: unique key is `(city, target_date)` with no
  `temperature_metric` — an SD-2 violation per law. These are high-only
  implicit settlements used by current live system.
- **`settlements_v2` metric=high**: 0 rows.
- **`settlements_v2` metric=low**: 0 rows.
- Low settlements need to be harvested from Polymarket `winningOutcome` for
  `daily-low` markets (separate market_slug space from `daily-high`).
- **Estimated low settlements needed for TIGGE coverage window**:
  ~32,283 rows (51 × 633 days).
- **Verdict**: MISSING for both v2 tracks. Low harvest requires market_slug
  enumeration for daily-low Polymarket markets.

---

## TIGGE ingest chain

The intended canonical pipeline (as documented in the codebase):

| Script | Status | Purpose |
|---|---|---|
| `scripts/ingest_grib_to_snapshots.py` | **STUB — NOT IMPLEMENTED (task #53)** | GRIB → `ensemble_snapshots_v2` (the critical bridge) |
| `scripts/backfill_tigge_snapshot_p_raw.py` | Implemented (repairs p_raw_json field) | Post-ingest derived-field repair for replay compatibility |
| `scripts/rebuild_calibration_pairs_canonical.py` | Implemented, **high-track only** | `ensemble_snapshots` → `calibration_pairs` (canonical bin grid) |
| `scripts/refit_platt.py` | Implemented | `calibration_pairs` → `platt_models` (Extended Platt fit) |
| `scripts/etl_tigge_ens.py` | **Retired, fails closed** | Legacy unaudited path — replaced by ingest_grib_to_snapshots |
| `scripts/etl_tigge_calibration.py` | **Retired, fails closed** | Legacy unaudited path — replaced by rebuild_calibration_pairs_canonical |
| `scripts/etl_tigge_direct_calibration.py` | **Retired, fails closed** | Legacy unaudited path — replaced |

**Metric flag / mode control**: None exists. `rebuild_calibration_pairs_canonical.py`
has no `--metric` or `temperature_metric` argument. The unimplemented
`ingest_grib_to_snapshots.py` would need to accept `--physical-quantity mn2t6`
(or equivalent) to distinguish the two tracks during extraction.

**Critical blocker before Phase 5 can run**:

1. **Task #53** must be implemented: `ingest_grib_to_snapshots.py` needs to
   parse `mn2t6` GRIB parameter codes (param 122 vs mx2t6's param 121 or
   similar), set `temperature_metric='low'`, `physical_quantity='mn2t6_local_calendar_day_min'`,
   `data_version='tigge_mn2t6_local_calendar_day_min_v1'`, and enforce
   `causality_status` / `training_allowed` per §5 of the dual-track law.
2. `rebuild_calibration_pairs_canonical.py` must be extended or a v2
   variant written to accept `temperature_metric` and produce rows into
   `calibration_pairs_v2`.

---

## Backfill scope (concrete numbers)

| Step | Scope | Unit |
|---|---|---|
| TIGGE mn2t6 GRIB → DB ingest | 413 GRIB files × 4 regions = 207 batch-dirs; ~65 GB raw | Files |
| `ensemble_snapshots_v2` rows (low) | ~451,962 | Rows |
| `observations.low_temp` fill | ALREADY COMPLETE — 42,504 rows, 51 cities | N/A |
| `settlements_v2` (low) | ~32,283 (51 cities × 633 days) | Rows |
| `calibration_pairs_v2` (low) | ~1,186,400 after causality/boundary filter (~75% of raw) | Rows |
| `platt_models_v2` (low) | ~140 model rows (5 clusters × 4 seasons × 7 lead_steps) | Rows |

Note: The TIGGE coverage window (2024-01-01 to 2025-09-24, 633 days) is the
binding constraint on all downstream row counts. Data before 2024-01-01 exists
in observations but has no TIGGE evidence to pair with.

---

## Forward risks for Phase 5

**INV-14 / §5 causality**: For positive-UTC-offset cities (e.g., Tokyo, Seoul,
Singapore, Auckland), the local calendar low day may have already started or
completed by ECMWF 00Z issue time. The ingest_grib_to_snapshots implementation
must compute `causality_status` per city per target_date per issue_time and
set `training_allowed=0` for `N/A_CAUSAL_DAY_ALREADY_STARTED`. Approximately
20-30% of low-track rows for Asia/Pacific cities will be non-OK causality;
these inflate the gap between raw file count and usable training pairs.

**§8 forbidden**: Any row with `data_version='tigge_mn2t6_local_calendar_day_min_v1'`
written to the legacy `ensemble_snapshots` table (not v2) is a forbidden move.
The ingest script must target `ensemble_snapshots_v2` exclusively.

**SD-7 ordering law**: High must be re-canonicalized onto `mx2t6_local_calendar_day_max_v1`
(Phase 4) BEFORE any low row enters the world (Phase 5). With BOTH tracks
currently at 0 rows in v2, Phase 4 must complete first. Since `ensemble_snapshots_v2`
is also empty for high, Phase 4's high ingest is an equally unblocked task —
they race to the same missing ingest_grib_to_snapshots stub.

**Boundary-ambiguous low slots** (§7 DT#7): Near midnight local time, the
minimum-temperature window for a calendar day can span two UTC days. The ingest
must quarantine these with `boundary_ambiguous=1`,
`causality_status='REJECTED_BOUNDARY_AMBIGUOUS'`, `training_allowed=0`. Failure
to do so will silently corrupt calibration pairs with ambiguous outcomes.

**TIGGE coverage gap**: The 5-day tail (2023-12-27 to 2023-12-31) covered by
observations has no TIGGE evidence for either track. Low-track Platt training
will start from 2024-01-01, giving approximately 21 months of pairs — adequate
for seasonal model fitting but narrower than observations suggest.

---

## Recommendation to team-lead

Phase 5 (low historical lane) **must not be queued yet**. The blocking
dependency is task #53 — `ingest_grib_to_snapshots.py` is a documented stub
that must be implemented before any GRIB → DB flow is possible for either
track. An additional prerequisite is extending `rebuild_calibration_pairs_canonical.py`
(or creating a v2 variant) to carry `temperature_metric` as a first-class
parameter. Both of these are Phase 5 implementation work, not Phase 3/4 work.
The correct sequencing: complete Phase 3 (observation closure, `low_so_far`),
complete Phase 4 (high canonical cutover — which also requires task #53),
then open Phase 5 with task #53 already verified against the high track. There
is no need for a Phase 3.5 to produce low_temp observations — they are fully
populated today. The real Phase 3.5 risk, if any, is the daily-low Polymarket
market_slug harvest needed for `settlements_v2` — that may warrant an early
scout before Phase 5 opens.
