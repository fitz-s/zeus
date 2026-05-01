# TIGGE 12-Day Gap Backfill — Reality Check

**Date:** 2026-04-30
**Status:** DEFERRED — backfill not feasible in current operational environment.

## Gap Summary

- `ensemble_snapshots_v2` last issue_time: `2026-04-18T00:00:00+00:00`
- Today: 2026-04-30
- Gap: 12 days × 2 cycles = ~24 missing TIGGE forecast cycles per metric (HIGH + LOW)

## Why Backfill Cannot Be Completed Today

### 1. Raw GRIBs are not on disk

```
ls /Users/leofitz/.openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens_mx2t6_localday_max/nyc/
# Latest directory: 20260418
```

The TIGGE backfill chain requires raw GRIBs:
- `scripts/extract_tigge_mx2t6_localday_max.py` → reads GRIB, emits JSON
- `scripts/extract_tigge_mn2t6_localday_min.py` → same for LOW
- `scripts/ingest_grib_to_snapshots.py` → JSON → `ensemble_snapshots_v2`

GRIBs for 2026-04-19 → 2026-04-30 do not exist locally.

### 2. TIGGE MARS download is not trivially scriptable from this session

`51 source data/scripts/tigge_mn2t6_download_resumable.py` exists. It connects to ECMWF MARS API.
Requirements: ECMWF MARS API credentials configured for the account; the TIGGE archive ingest schedule has ~48h lag from issue date — cycles for 2026-04-29/30 may not yet be in the archive.

Operator-only operation. Out of scope for this session.

### 3. ECMWF Open Data is not a substitute

`src/data/ecmwf_open_data.collect_open_ens_cycle` writes to **legacy `ensemble_snapshots`** with:
- `data_version = "open_ens_v1"`
- `authority = "UNVERIFIED"` (DIAGNOSTIC_AUTHORITY)

It does NOT write to `ensemble_snapshots_v2`. Calibration pipeline + Platt models read v2. Backfilling Open Data would not restore the same surface.

Additionally, ECMWF Open Data has ~6-day retention, so for older dates (2026-04-19 → 2026-04-24) the public API has already expired the cycle.

## Why This Gap Does NOT Block Live Launch

| Use case | Affected? | Reason |
|---|---|---|
| Live discovery → entry decision | ❌ No | `opening_hunt` fetches CURRENT TIGGE forecast on-demand for any newly discovered market via `fetch_ensemble`. |
| Active position monitoring | ❌ No | `monitor_refresh.py` uses `ens.p_raw_vector(bins, n_mc=ensemble_n_mc())` against current ensemble snapshot, not historical. |
| Calibration_pairs_v2 training corpus | ❌ No | The 2026-04-29 rebuild used target dates through 2026-04-25; the 12-day gap is in ISSUE dates after the training window's last viable lead-day cutoff. |
| Day0 same-day evaluation | ❌ No | Day0 reads observation_instants_v2 + current TIGGE; observation gap will self-fill via `_k2_startup_catch_up(days_back=30)` on daemon start. |
| Future Platt drift-triggered refit | ❌ No | Refit consumes calibration_pairs_v2 (already populated) + new pairs from forward-going TIGGE (post-daemon-start). |
| Historical replay/audit window 4/19-4/30 | ⚠️ Yes | Replay of this period's forecasts not possible. No live trades occurred during this period (daemon was unloaded), so there is nothing to replay against. |

## Recommended Resolution

### Short-term (Phase 1 deliverable)

When `com.zeus.data-ingest` daemon goes live (per the two-system independence design), it will run `_ecmwf_open_data_cycle` at UTC 01:30 / 13:30 onward. From that moment, no new TIGGE gap can accumulate during normal operation.

The current 12-day historical gap remains as a fixed historical artifact.

### Long-term (operator decision)

If historical replay through this window is required for audit:
1. Operator runs `tigge_mn2t6_download_resumable.py` (and the corresponding mx2t6 downloader) with valid ECMWF MARS credentials, restricted to dates `2026-04-19 → 2026-04-30`.
2. Operator runs the extract scripts on the downloaded GRIBs.
3. Operator runs `ingest_grib_to_snapshots.py` to write to `ensemble_snapshots_v2`.

Estimated time: 4-8 hours operator-attended (download throughput + extract + ingest), plus any debug if MARS API has changed since last successful run.

## Subagent Backfill Attempt — Status

A subagent (executor-type) was dispatched 2026-04-30 to perform backfill via `collect_open_ens_cycle`. The agent stalled at 600s with no progress and was killed by the stream watchdog. Root cause (inferred from artifacts): the agent likely identified that `collect_open_ens_cycle` writes the wrong table (legacy `ensemble_snapshots`, not v2) and could not find a feasible alternative path within its capability boundary. No script was written, no DB modifications occurred.

This document supersedes that attempt with an explicit DEFER decision.
