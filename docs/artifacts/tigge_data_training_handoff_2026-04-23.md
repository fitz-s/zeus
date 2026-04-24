# TIGGE Data Training Handoff

Status: active evidence handoff
Created: 2026-04-23
Scope: current TIGGE historical data state, cloud/local asset inventory, and the
next Zeus training steps

This file is an execution handoff, not architecture authority. Binding law
remains in `docs/authority/zeus_dual_track_architecture.md` plus code/tests.

## 1. Current State

The historical TIGGE replacement-data pipeline has reached this state:

1. Raw cloud download completed.
2. Plan-external duplicate raw windows were quarantined and then deleted.
3. GRIB integrity passed for both tracks.
4. Local-calendar-day extraction completed for both tracks.
5. JSON validation passed for both tracks.
6. Coverage scan completed for both tracks.
7. Zeus local/world DB has **not** yet ingested these snapshots.

Current local Zeus DB counts:

```text
observations         42,749
ensemble_snapshots_v2      0
calibration_pairs_v2       0
platt_models_v2            0
```

That means the data asset is ready for the next stage, but Zeus has not yet
consumed it.

## 2. Data Asset Summary

### Raw GRIB

Cloud clean raw directory:

```text
/data/tigge/workspace-venus/51 source data/raw
```

Current clean raw size after duplicate-window pruning:

```text
~703 GiB
```

Per the completed duplicate cleanup, plan-external raw windows were moved to:

```text
/data/tigge/trash/tigge_extra_windows_20260422T083237Z
```

and later deleted after integrity passed.

### Extracted localday JSON

Cloud extracted output roots:

```text
/data/tigge/workspace-venus/51 source data/raw/tigge_ecmwf_ens_mx2t6_localday_max
/data/tigge/workspace-venus/51 source data/raw/tigge_ecmwf_ens_mn2t6_localday_min
```

Extracted counts:

```text
mx2t6 localday JSON: 342,312 files
mn2t6 localday JSON: 342,312 files
combined:            684,624 files
```

Extracted sizes:

```text
mx2t6 localday JSON: ~1.61 GiB
mn2t6 localday JSON: ~3.93 GiB
combined:            ~5.54 GiB
```

This is the key operational fact: Zeus no longer needs the 703 GiB raw GRIB on
the local machine for training. The extracted JSON asset is small enough to move
or archive comfortably.

## 3. Validation Results

### GRIB integrity

Files:

```text
tmp/tigge_mx2t6_grib_integrity_full_latest.json
tmp/tigge_mn2t6_grib_integrity_full_latest.json
```

Results:

```text
mx2t6: ok=true, files_checked=2,240, failures=0
mn2t6: ok=true, files_checked=2,240, failures=0
```

### JSON validation

Files:

```text
tmp/tigge_mx2t6_json_integrity_full_20260423T175156Z.json
tmp/tigge_mn2t6_json_integrity_full_20260423T175156Z.json
```

Results:

```text
mx2t6_high: ok=true, files_checked=342,312, failures=0
mn2t6_low:  ok=true, files_checked=342,312, failures=0
```

### Coverage scan

Files:

```text
tmp/tigge_mx2t6_coverage_full_20260423T175156Z.json
tmp/tigge_mn2t6_coverage_full_20260423T175156Z.json
```

Results:

```text
mx2t6_high counts:
  OK = 342,312

mn2t6_low counts:
  OK = 66,256
  N/A_CAUSAL_DAY_ALREADY_STARTED = 28,966
  REJECTED_BOUNDARY_AMBIGUOUS    = 247,090
```

Interpretation:

- High track is fully usable for historical training.
- Low track validation passed structurally, but most rows are intentionally
  quarantined by the boundary-ambiguity rule or are causal Day0 rejects.
- This is expected under the current low-track design; it is not a corruption
  signal.

## 4. Cloud Runtime Note

To accelerate extraction, the GCE runner was scaled up to:

```text
custom E2: 24 vCPU / 96 GiB RAM
```

The instance was used as a compute box for extraction/validation. That does not
change the logical asset; it only changed wall-clock speed.

## 5. What Zeus Uses Next

The next Zeus consumption path is:

```text
localday JSON
-> scripts/ingest_grib_to_snapshots.py
-> ensemble_snapshots_v2
-> scripts/rebuild_calibration_pairs_v2.py
-> calibration_pairs_v2
-> scripts/refit_platt_v2.py
-> platt_models_v2
```

Optional audit/replay enhancement after ingest:

```text
scripts/backfill_tigge_snapshot_p_raw_v2.py
-> ensemble_snapshots_v2.p_raw_json
```

### Script inventory (present locally in zeus/)

- `scripts/ingest_grib_to_snapshots.py`
- `scripts/rebuild_calibration_pairs_v2.py`
- `scripts/refit_platt_v2.py`
- `scripts/backfill_tigge_snapshot_p_raw_v2.py`

### Runtime read path status

The important read seam is already wired:

- `src/calibration/manager.py::get_calibrator(...)`
  now accepts `temperature_metric`
- it prefers `load_platt_model_v2(...)`
- `src/engine/evaluator.py` calls `get_calibrator(..., temperature_metric=...)`

That means once `platt_models_v2` is trained, Zeus is structurally closer to
using the metric-aware models than it was before. The remaining gate is not
reader absence; it is successful population and verification of the v2 tables.

## 6. Cloud vs Local Training

### What is currently possible

**Local training from extracted JSON** is the simplest next move.

Why:

- the extracted asset is only ~5.54 GiB
- the local machine already has the full Zeus repository
- the local machine already has the active `state/zeus-world.db`
- no cloud repo sync or cloud DB bootstrap is required

### What is currently blocked for full cloud training

The cloud VM does **not** currently hold a full Zeus checkout. It only has:

- `51 source data/`
- `zeus/config/cities.json`

So full cloud training is blocked until the complete `zeus/` repository and a
consistent training DB snapshot are deployed there.

## 7. Recommended Next Step

Recommended next step:

1. Copy or rsync the extracted JSON asset from cloud to local.
2. On the local machine, run ingest into a training copy of `zeus-world.db`.
3. Dry-run pair rebuild.
4. Live-run pair rebuild if counts and unit checks look sane.
5. Dry-run Platt v2 refit.
6. Live-run Platt v2 refit.
7. Only after row counts and smoke checks pass, decide whether to activate or
   keep the resulting `platt_models_v2` as offline evidence.

## 8. Suggested Local Command Order

The exact local paths should be chosen at execution time, but the intended order
is:

```text
1. Prepare a training DB copy from state/zeus-world.db
2. ingest_grib_to_snapshots.py --track mx2t6_high
3. ingest_grib_to_snapshots.py --track mn2t6_low
4. rebuild_calibration_pairs_v2.py --dry-run
5. rebuild_calibration_pairs_v2.py --no-dry-run --force
6. refit_platt_v2.py --dry-run
7. refit_platt_v2.py --no-dry-run --force
8. backfill_tigge_snapshot_p_raw_v2.py --dry-run (optional)
9. backfill_tigge_snapshot_p_raw_v2.py --no-dry-run --force (optional)
```

## 9. Cautions

1. Do not train directly into the live canonical DB without making a copy first.
2. Do not re-download or re-extract raw data unless a specific corruption signal
   appears.
3. Do not treat low-track quarantine volume as a failure by itself; it is a
   feature of the current boundary policy.
4. Do not assume cloud VM state remains durable forever; the asset to preserve
   now is the extracted JSON plus the validation/coverage artifacts.
