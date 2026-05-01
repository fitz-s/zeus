# HKO Backfill Log — 2026-05-01

**Operator request:** Backfill Hong Kong observations for 2026-04-01 through 2026-05-01 using HKO opendata API, in preparation for releasing the `hko_canonical` quarantine.

---

## Run Summary

**Date run:** 2026-05-01  
**Script:** `scripts/backfill_hko_daily.py --start 2026-04 --end 2026-05`  
**Venv:** `.venv/bin/python`  
**Run ID:** `backfill_hko_daily_2026-05-01T13:43:27Z`  

```
Months fetched:   2  (2026-04, 2026-05)
Days complete:    0
Days incomplete:  0
Days unavailable: 0
Inserted:         0
Guard rejected:   0
Fetch errors:     0
Insert errors:    0
```

---

## Finding: HKO API Publication Lag

The HKO `CLMMAXT` / `CLMMINT` opendata endpoint (`data.weather.gov.hk/weatherAPI/opendata/opendata.php`) returned **empty `data` arrays** for both 2026-04 and 2026-05. HTTP 200 was returned but with `"data": []`.

Publication availability survey:

| Month    | API rows | Status                   |
|----------|----------|--------------------------|
| 2025-10  | 31       | PUBLISHED (all C)        |
| 2025-11  | 30       | PUBLISHED (all C)        |
| 2025-12  | 31       | PUBLISHED (all C)        |
| 2026-01  | 31       | PUBLISHED (all C)        |
| 2026-02  | 28       | PUBLISHED (all C)        |
| 2026-03  | 31       | PUBLISHED (all C)        |
| 2026-04  | 0        | **NOT YET PUBLISHED**    |

**Conclusion:** HKO's verified climate data has a publication lag of approximately 1 full calendar month. Despite today being 2026-05-01, April 2026 climate data is not yet available. The operator assumption that HKO publishes the prior month's data on the 1st of the following month does not apply to this endpoint — the lag is longer.

---

## Pre-backfill DB State (verified)

```sql
SELECT MAX(target_date), COUNT(*), authority
FROM observations WHERE city='Hong Kong'
GROUP BY authority;
-- Result: 2026-03-31 | 821 | QUARANTINED
```

- 821 QUARANTINED rows spanning 2024-01-01 to 2026-03-31 (untouched)
- 0 VERIFIED rows
- 0 rows for any date >= 2026-04-01

All 26 `data_coverage` rows for `hko_daily_api` in April are status `MISSING` / `SCANNER_DETECTED`.

---

## Realtime Accumulator State

The daemon has been accumulating `hko_hourly_accumulator` readings since ~April 22, but:

| Date       | Readings | High | Low |
|------------|----------|------|-----|
| 2026-04-22 | 1        | 26   | 26  |
| 2026-04-23 | 5        | 28   | 25  |
| 2026-04-24 | 4        | 24   | 21  |
| 2026-04-25 | 4        | 26   | 20  |
| 2026-04-26 | 4        | 28   | 23  |
| 2026-04-27 | 4        | 27   | 24  |
| 2026-04-28 | 4        | 29   | 25  |
| 2026-04-29 | 2        | 27   | 23  |

None of these days meet the `HKO_REALTIME_MIN_READINGS = 18` threshold needed to finalize a realtime observation. No `hko_realtime_api` rows exist in `observations`.

For 2026-04-01 through 2026-04-21 (21 days), there are zero accumulator readings — the daemon was not running or HK was under quarantine during that period.

---

## Conclusion and Recommended Next Steps

**Zero rows were written.** The QUARANTINED 821 rows are intact and unmodified.

The HKO opendata API simply has not published April 2026 data yet. This is not a script or config error — it is an upstream publication timing constraint.

**Operator action required before releasing the HK quarantine:**

1. **Wait for HKO to publish April 2026 data.** Based on the observed lag pattern, this will likely be available sometime in late May or early June 2026. Re-run the backfill at that point:
   ```
   .venv/bin/python scripts/backfill_hko_daily.py --start 2026-04 --end 2026-05
   ```

2. **Alternative (partial coverage):** If the quarantine release can proceed without a complete April observation set, the operator should note that the HK calibration pool will train on data through 2026-03-31 only. The gap is a known HKO publication lag, not a data quality issue.

3. **Realtime gap (Apr 1-21):** 21 days of April have no realtime accumulator readings. These will not be recoverable from `hko_rhrread` — they can only be filled when HKO publishes the CLMMAXT/CLMMINT archive (same endpoint, ~1-month lag).

---

## Quarantine Status (unchanged)

- Existing 821 rows: `authority='QUARANTINED'` — NOT modified by this run
- Release of quarantine is a separate operator step (preflight_overrides_2026-04-28.yaml)
- This run wrote 0 new rows and made 0 modifications to existing rows
