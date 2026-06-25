# D_impl_feasibility — Implementation Element Feasibility Verification
# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: live code read-only audit; files cited by line below

---

## Claim 1 — HIERARCHICAL ESTIMATOR

**Verdict: SUPPORTED — partial pooling (EB shrinkage) exists at city×season level; lead/cycle/product are NOT current hierarchy levels but the architecture is directly extensible.**

### What exists

`src/calibration/ens_bias_model.py` (Created 2026-05-24, authority: operator
hierarchical-bias adjudication):

- `BiasPrior` / `LiveResidual` / `PosteriorBias` dataclasses: EB posterior as
  `w·e_bar + (1−w)·(mu_T+delta_g)`, `w = V0/(V0+V_O)`.
  File:1–169.
- `fit_bucket(tigge_residuals, opendata_residuals, ...)` → `PosteriorBias`.
  Hierarchy levels today: the calling code groups residuals by **(city, season,
  [month])** — i.e. a 2-level pool: city×season bucket shrinks toward a
  TIGGE structural prior. File:199–231.
- `transport_bias_prior()` adds a product-lineage transfer step (mx2t6→mx2t3)
  via paired Δ samples. File:295–325.
- `V_TRANSFER_DEFAULT = 0.25` is the irreducible cross-product variance floor.
  File:51.
- `delta_g` parameter on `posterior_bias()` carries a group-level offset (e.g.
  coastal/cluster correction). File:103–169. This is the hook for a
  coarser-parent mean shift.

`src/calibration/ens_bias_repo.py`:
- PK of `model_bias_ens`: `(city, season, month, metric, live_data_version)`.
  File:61–62. No `lead_bucket`, `cycle`, or `product` columns in the PK.
- Schema allows extension: `error_model_family`, `error_model_key`, and
  `code_commit` canonical-extension columns are nullable add-ons. File:70–87.

### Hierarchy levels and shrinkage currently

| Level | Pooling mechanism |
|---|---|
| city × season (× opt. month) | Empirical Bayes: TIGGE prior V0, live V_O; w∈[0,1] |
| product transfer (mx2t6 → mx2t3) | `transport_bias_prior`: mean shift by E[Δ_paired], variance inflation by Var(Δ) + κ·sd50·sd_Δ |
| global transfer offset | `delta_g` param on `posterior_bias` (e.g. hemisphere correction) |

No `lead_bucket`, `cycle (00z/12z)`, or `product (mx2t3/mx2t6)` grouping keys
exist in the current estimator or DB PK.

### Extensibility assessment

**PARTIALLY SUPPORTED.** The EB shrinkage math (`posterior_bias`) is fully
general: adding `lead_bucket × cycle × product` as additional bucket keys
requires (a) passing finer-keyed residual lists to `fit_bucket`, (b) adding
columns to `model_bias_ens` (nullable ALTERs, pattern already established at
ens_bias_repo.py:70–131), and (c) implementing the coarse-parent fallback by
passing the parent bucket's `PosteriorBias` as the TIGGE prior when the fine
bucket is thin. There is no global/hierarchical shrinkage spine today (no
multi-level shrinkage object or city→metric→global parent chain) — the parent
fallback must be implemented new. With ~28× more buckets and only ~7,858 OpenData
LOW pairs, the fine buckets will typically have n_live < `min_live_n=20`, so the
posterior will collapse to the TIGGE prior — which is the correct thin-bucket
behavior and requires no new math, just correct key routing. The architecture
does NOT need a hierarchical-fallback object; it needs the caller to supply the
parent-bucket posterior as the prior argument when the fine bucket is thin.

**Recommended extension pattern** (no new classes required):
```
parent = fit_bucket(tigge_by_coarse, opd_by_coarse)   # city×season
child  = fit_bucket([parent.bias]*1, opd_by_fine,     # degenerate prior from parent
                    paired_delta_abs=..., min_live_n=20)
```
Or: set `BiasPrior(mu_t=parent.bias, v0=parent.sd**2)` and call `posterior_bias`
directly with the fine-bucket live residuals.

---

## Claim 2 — BEFORE/AFTER VALIDATION HARNESS

**Verdict: SUPPORTED — substantial machinery exists; dual-mode EQUIVALENCE+IMPROVEMENT harness can be composed without rebuild.**

### LogLoss / RPS / Brier vs settlement

`scripts/audit_refit_proper_scores.py` (Created 2026-05-25):
- Computes multinomial Brier (`_brier_dist`), LogLoss (`_logloss_dist`), RPS
  (`_rps_dist`), PIT (`_pit_u`), P(actual) (`_p_actual`), ECE
  (`_ece_from_dists`) per distribution and aggregated. File:180–334.
- Loads from `calibration_pairs_v2` keyed by `error_model_family` (old='none'
  vs new='full_transport_v1'). File:101–119.
- Cohort splits already include `lead_bucket` and `cycle`. File:451–474.

`scripts/validate_ens_refit_oos.py` (Created 2026-05-24):
- Group-blocked 5-fold K-fold OOS Platt fit+predict over `decision_group_id`.
  File:93–100.
- `_smoke_reconcile` in `audit_refit_proper_scores.py` verifies the two scripts
  agree to |Δ|≤2e-4 on the same DB. File:548–626.

### Blocked-OOS / date splitting

`src/calibration/blocked_oos.py`:
- `evaluate_blocked_oos_calibration(conn, train_start, train_end, test_start,
  test_end)` — forward-split train/test on `target_date`. File:147–226.
- Returns Brier, LogLoss per block. No RPS (scalar pair path, not distribution).
  File:198–215.
- `recommend_calibration_promotion(report)` — promotion gate with
  `min_brier_improvement`, `max_fallback_rate`, `min_test_groups`. File:229–283.

### Frozen-replay fixture

No frozen-replay fixture mechanism found. `scripts/capture_replay_artifact.py`
exists in the scripts list but was not inspected.
exists in `src/backtest/`. Frozen-fixture replay (bit-exact input snapshot) is
NOT present in the bias/error-model pipeline — the closest analog is the
`settled_before` anti-leakage cutoff in `load_bucket_residuals`. File:184.

### Dual-mode harness composability

**SUPPORTED with the following composition:**

**(a) EQUIVALENCE mode** (analytic p_raw vs 10k MC; new-contract-wrapped serving
vs old serving — delta must ≈ 0):
- No dedicated harness exists, but `audit_refit_proper_scores.py` already loads
  two `error_model_family` slices from the same DB and computes identical metrics
  on both. Composing EQUIVALENCE requires: (i) adding a new family tag for the
  analytic path, (ii) loading both slices, (iii) asserting |mean_score_delta| <
  threshold across all distributions. No new scoring logic needed.

**(b) IMPROVEMENT mode** (old city×season bias vs new product×cycle×lead bias —
LCB(improvement)>0 on settlement OOS):
- `audit_refit_proper_scores.py` already implements per-cohort proper scores
  including `lead_bucket` and `cycle` filters. The LCB assertion requires adding
  bootstrap CIs on the score difference — not present today. The cohort split
  infrastructure is present. PARTIAL.

**Summary:** EQUIVALENCE mode is composable directly (new family tag + delta
assertion). IMPROVEMENT mode needs bootstrap CI on the score delta (new ~20-line
addition to `_aggregate_metrics`); the scoring and split infrastructure is fully
present.

---

## Claim 3 — BACKFILL FEASIBILITY

**Verdict: SUPPORTED for cycle and product; PARTIAL for lead_bucket; NO RE-INGEST needed.**

### Column inventory for `ensemble_snapshots` (v2_schema.py:107–241)

| Proposed key dimension | Column(s) available | Notes |
|---|---|---|
| `cycle` (00z/12z) | `source_cycle_time TEXT` (nullable, ALTER-added) | Already decoded to '00'/'12' in `_cycle_from_source_cycle_time` at `src/observability/calibration_serving_status.py:76–80` |
| `product` (mx2t3 / mx2t6) | `data_version TEXT` | Two canonical values: `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` (active) and `ecmwf_opendata_mx2t6_local_calendar_day_max_v1` (legacy). Defined in `src/contracts/ensemble_snapshot_provenance.py:77,82` |
| `lead_bucket` | `lead_hours REAL NOT NULL` | `lead_bucket = floor(lead_hours/24)` or a configurable bucketing. Column is NOT NULL and always populated |
| `local_day_start_utc` | `local_day_start_utc TEXT` (nullable ALTER-added) | Populated only for post-Phase-4.5 rows; may be NULL on older rows |
| `forecast_window_start/end_utc` | both present (nullable ALTERs) | Useful for verifying lead computation |

### Cycle extraction

`source_cycle_time` on `ensemble_snapshots` is a TEXT ISO timestamp
(e.g. `2026-04-10T00:00:00+00:00`). The hour component gives 00 vs 12.
The existing `_cycle_from_source_cycle_time(value)` function at
`calibration_serving_status.py:76–80` already implements this extraction.
For rows where `source_cycle_time` is NULL (legacy ingests), `issue_time`
can be used as fallback (same format, same hour semantics — see
`ens_bias_repo.py:206–236` which already extracts `issue_hour` from
`issue_time` for cycle-preference logic).

### Product extraction

`data_version` is NOT NULL on active rows and directly identifies the
product: `mx2t3_...v1` vs `mx2t6_...v1`. Backfill by query alone:

```sql
SELECT
  snapshot_id,
  SUBSTR(source_cycle_time, 12, 2) AS cycle_hour,   -- '00' or '12'
  data_version,                                       -- product identity
  CAST(lead_hours / 24.0 AS INTEGER) AS lead_day_bucket
FROM ensemble_snapshots
WHERE authority = 'VERIFIED'
  AND contributes_to_target_extrema = 1
```

This does NOT require re-ingest.

### Lead_bucket

`lead_hours` is `NOT NULL` in the DDL (v2_schema.py:121). Bucketing is a pure
arithmetic derivation. No re-ingest needed.

### Caveats

1. `source_cycle_time` is nullable (ALTER-added, pre-Phase-PLAN rows may be
   NULL). For the older TIGGE-era rows the fallback is `issue_time` which is
   also nullable but populated for ECMWF data. Rows where BOTH are NULL will
   have `cycle = NULL` and must be excluded or assigned to a sentinel bucket.

2. `data_version` for TIGGE rows uses `tigge_mx2t6_local_calendar_day_max_v1`
   (confirmed at `src/data/tigge_pipeline.py:162`). The re-key by product maps
   `mx2t3` → OpenData live and `mx2t6` → TIGGE/legacy. This is present and
   queryable without re-ingest.

3. The evidence ledger (model_bias_ens) PK does not include lead_bucket or
   cycle. Adding them requires schema ALTERs (nullable new columns + new UNIQUE
   constraint via `CREATE UNIQUE INDEX` — SQLite can't ALTER a PK). The
   established pattern for this is `_CANONICAL_EXTENSION_COLUMNS` in
   `ens_bias_repo.py:70–87`.

**Bottom line:** The re-keyed evidence ledger CAN be backfilled by query alone
(no re-ingest). The only NULL-risk column is `source_cycle_time` for legacy
rows; `issue_time` covers the majority of those. `data_version` and `lead_hours`
are reliably populated.

---

## Score summary

| Claim | Verdict | Key blocker / gap |
|---|---|---|
| 1. Hierarchical estimator | PARTIAL → SUPPORTED | EB machinery exists; multi-level parent-chain must be implemented (new calling convention, ~50 LOC); no new math needed |
| 2. Before/after harness | SUPPORTED (EQUIVALENCE); PARTIAL (IMPROVEMENT) | EQUIVALENCE composable now; IMPROVEMENT needs bootstrap CI on score delta (~20 LOC addition) |
| 3. Backfill feasibility | SUPPORTED | Query-only; `source_cycle_time` NULL-risk on legacy rows, `issue_time` fallback covers it |
