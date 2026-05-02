# TIGGE 2026-04-29 Backfill — Work Log

**Date**: 2026-05-01  
**Operator**: executor agent  
**Status**: IN PROGRESS

---

## Objective

Backfill TIGGE issue=2026-04-29T00:00Z (mx2t6_high + mn2t6_low, all 4 regions, all 49 cities).
Embargo expired 2026-05-01T00:00Z UTC (~current time + 0h). Lead day 3 → target 2026-05-02;
lead day 4 → target 2026-05-03. Degraded precision vs lead 1/2 but covers all 112 active markets.

Context: prior 5/01 run was blocked by ECMWF 48h embargo. See
`../task_2026-05-01_tigge_5_01_backfill/work_log.md` for that attempt.

---

## Baseline (pre-run)

```
SELECT temperature_metric, MAX(issue_time), COUNT(*) FROM ensemble_snapshots_v2 GROUP BY temperature_metric;
  high: max_issue=2026-04-28T00:00:00+00:00, count=344835
  low:  max_issue=2026-04-28T00:00:00+00:00, count=344532
  NYC high 2026-05-02: 0
  NYC high 2026-05-03: 0
```

---

## Pipeline

**Script**: `/tmp/tigge_backfill_2026-04-29.sh` (derived from `tigge_backfill_2026-05-01.sh`, DATE changed to 2026-04-29)  
**Launch time**: 2026-05-01 (pipeline PID=68336)  
**Logs**: `/tmp/tigge_backfill_2026-04-29_master.log`, `_dl_mx2t6.log`, `_dl_mn2t6.log`, `_extract_*.log`, `_ingest_*.log`

Steps:
1. Download mx2t6_high + mn2t6_low in parallel (background)
2. Extract mx2t6_high via `tigge_local_calendar_day_extract.py --track mx2t6_high`
3. Extract mn2t6_low via `tigge_local_calendar_day_extract.py --track mn2t6_low`
4. Ingest mx2t6_high via `scripts/ingest_grib_to_snapshots.py --track mx2t6_high`
5. Ingest mn2t6_low via `scripts/ingest_grib_to_snapshots.py --track mn2t6_low`

---

## Result

<!-- TO BE FILLED IN AFTER PIPELINE COMPLETES -->

---

## 4/30 Follow-up

Issue 2026-04-30 clears embargo at 2026-05-02T00:00Z UTC (~10h from launch time).
4/30 retry scheduled for >= 2026-05-02T00:00Z; the structural daemon job (parallel agent B)
will pick it up automatically once the embargo lifts.

---

## Files Not Touched

- `src/ingest_main.py` — reserved for parallel agent B (daily TIGGE retrieval job)
- No commits made to overlap with agent B's work area

