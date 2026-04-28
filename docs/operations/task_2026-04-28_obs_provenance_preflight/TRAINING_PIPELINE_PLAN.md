# Zeus Training Pipeline — Full Task Plan
# Created: 2026-04-28
# Status: in-flight (Gate 3+4 backfill running, VM HIGH extract finishing)

This document is the durable task plan for restoring zeus's HIGH-track
calibration training pipeline end-to-end. It is the source-of-truth for
auto-progression after the in-flight Gate 3+4 backfill completes.

## Money-path the chain must restore

```
TIGGE GRIB (VM extract output, ~1M JSON files)
    ↓ ingest_grib_to_snapshots.py --track mx2t6_high
ensemble_snapshots_v2 (currently 0 rows)
    ↓ backfill_tigge_snapshot_p_raw_v2.py --no-dry-run --force
ensemble_snapshots_v2.p_raw_json populated
    ↓ rebuild_calibration_pairs_v2.py --no-dry-run --force
calibration_pairs_v2 (currently 0 rows)
    ↓ refit_platt_v2.py
Platt coefficients per (city × bin × lead_days)
```

Two side-channels feed observations into the rebuild's
`_fetch_verified_observation` (`rebuild_calibration_pairs_v2.py:167-193`):

```
WU/Ogimet/HKO daily readings
    → observations table (settled high_temp + low_temp)
    → consumed by rebuild via SELECT high_temp,low_temp,unit,authority,source

WU/Ogimet hourly readings (with payload identity)
    → observation_instants_v2 (NOT consumed by rebuild_calibration_pairs_v2)
    → consumed by DST gap fill, day0 monitor_refresh.py, HKO ingest
```

## Task graph with deps

```
[A] Gate 3+4 backfill (observations.provenance_metadata)        [RUNNING, ETA ~25min]
    └── [V] verify Gate 3+4 = 0 via preflight                    [auto-after-A]

[B] VM HIGH extract (TIGGE → ~1M JSON on VM)                    [RUNNING, 13/16 done]
    └── [B'] rsync VM → local /<zeus>/../51\ source\ data/      [auto-after-B]

[C] Gate 5 backfill (obs_v2 payload identity, WU subset 932k)   [SCRIPT WRITTEN, run after V]
[C2] Gate 5 backfill (obs_v2 ogimet subset 60k)                 [SCRIPT TBD, run after C]
[C3] Gate 5 meteostat 820k (training_allowed=0)                 [DECISION needed: backfill OR document as legacy]

[D] Gate 2 HKO RFC (821 rows operator-blocked)                  [STUB written, operator decision]

[E] LOW-track VM extract                                        [TBD — separate scope, post-HIGH]

[I] ingest_grib_to_snapshots.py --track mx2t6_high              [DEPS: B' done]
    └── populates ensemble_snapshots_v2 with members_json + provenance

[I2] backfill_tigge_snapshot_p_raw_v2.py --no-dry-run           [DEPS: I done]
    └── computes + writes p_raw_json per row

[R] rebuild_calibration_pairs_v2.py --no-dry-run --force        [DEPS: V, I2 done; preflight all-pass]
    └── populates calibration_pairs_v2

[P] refit_platt_v2.py                                           [DEPS: R done]
    └── per-bucket Platt fit
```

Critical path: A → V → C → C2 → I → I2 → R → P
Parallel path: B → B' (ready when A is in-flight)

## Auto-progression after Gate 3+4 finishes

Sequence the agent will execute once Gate 3+4 backfill exits cleanly:

1. **Verify gates 3+4 closed**:
   ```bash
   python -c "from scripts.verify_truth_surfaces import build_calibration_pair_rebuild_preflight_report as f; \
              import json; r=f(); print(json.dumps([b for b in r['blockers'] if 'provenance' in b['code']], indent=2))"
   ```
   Expected: `[]` (no provenance blockers)

2. **Commit script + evidence + plan.md retraction**:
   - `docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_observations_provenance_existing.py`
   - `docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_existing.py` (Gate 5 patch)
   - `docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/TEL_AVIV_ANOMALY.md`
   - plan.md retraction (line 139 "58 hour" → "~65 min daily-granularity")
   - Evidence: gate34_fill_quarantine_apply_*.json + post-fill preflight report

3. **Run Gate 5 WU backfill** (after Gate 3+4 verified):
   ```bash
   python docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_existing.py --start-date 2024-01-01 --apply
   ```
   ETA ~70 min; reuses same pattern as Gate 3+4 but on `observation_instants_v2.provenance_json`.

4. **VM extract completion + rsync** (when 16/16 done):
   ```bash
   gcloud compute scp --recurse \
     tigge-runner:'/data/tigge/workspace-venus/51 source data/raw/tigge_ecmwf_ens_mx2t6_localday_max' \
     /Users/leofitz/.openclaw/workspace-venus/51\ source\ data/raw/ \
     --project snappy-frame-468105-h0 --zone=europe-west4-a
   ```

5. **Run TIGGE ingest**:
   ```bash
   cd /Users/leofitz/.openclaw/workspace-venus/zeus
   .venv/bin/python scripts/ingest_grib_to_snapshots.py --track mx2t6_high
   ```

6. **Compute p_raw**:
   ```bash
   .venv/bin/python scripts/backfill_tigge_snapshot_p_raw_v2.py --no-dry-run --force
   ```

7. **Final preflight check**:
   ```bash
   .venv/bin/python -c "from scripts.verify_truth_surfaces import build_calibration_pair_rebuild_preflight_report as f; \
              import json; print(json.dumps(f(), indent=2)['status'])"
   ```
   Expected: `READY` (or only HKO Gate 2 + 4 meteostat audit gaps blocking, depending on operator decision).

8. **Calibration pairs rebuild**:
   ```bash
   .venv/bin/python scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force
   ```

9. **Platt fit**:
   ```bash
   .venv/bin/python scripts/refit_platt_v2.py
   ```

## Out-of-scope for this auto-sequence

| Item | Status | Decision needed |
|---|---|---|
| HKO Gate 2 (821 rows) | operator-blocked | RFC stub written; operator decides |
| Tel Aviv 10 anomalous wu rows | sideline | doc-only (TEL_AVIV_ANOMALY.md); skipped by Gate 3+4 fill |
| Meteostat 820k obs_v2 (training_allowed=0) | sideline | operator: backfill audit OR document as legacy |
| LOW-track full pipeline | scope-distinct | parallel run after HIGH validated |

## Stop-conditions

The agent halts auto-progression and asks operator if:
- Gate 3+4 fill quarantine count > 100 mismatches (drift suspicion)
- Preflight after fill still shows provenance blockers (writer behavior didn't act as expected)
- VM extract loses any shard mid-stream (would create disjoint date holes)
- TIGGE ingest reports validate_snapshot_contract violation
- p_raw computation rejects > 5% of snapshots
- rebuild_calibration_pairs_v2 outputs zero rows (silent failure mode)

Each stop condition triggers an operator-visible status report rather than a
forge-ahead.

## Estimated wall time (post-current-state)

| Step | ETA | Cumulative |
|---|---|---|
| Gate 3+4 finish (in-flight) | ~25 min | 0:25 |
| Verify + commit | ~3 min | 0:28 |
| Gate 5 WU backfill | ~70 min | 1:38 |
| VM extract finish + rsync | ~30 min (overlap with Gate 5 ok) | 2:00 |
| TIGGE ingest | ~30-60 min | 2:45 |
| p_raw backfill | ~10-20 min | 3:00 |
| Final preflight verify | ~1 min | 3:01 |
| rebuild_calibration_pairs_v2 | ~20-40 min | 3:35 |
| refit_platt_v2 | ~5-10 min | 3:45 |

**Total ~3-4 hours from now to trained Platt coefficients (HIGH-only).**

LOW-track requires separate VM extract + ingest pass post-HIGH validation.
