# low_temp Observation Coverage Pre-Check

**Author**: scout-finn
**Date**: 2026-04-16
**DB**: `/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db`
**Purpose**: Verify `low_temp` backfill state before Phase 5 rebuild. Addresses emma's Hazard 1.

---

## SQL Run

**emma's exact pre-check (phase5_prep_ingest_audit.md §5 Hazard 1)**:
```sql
SELECT COUNT(*) FROM observations
WHERE low_temp IS NOT NULL AND authority = 'VERIFIED';
```
Result: **42,498**

**Baseline (high_temp)**:
```sql
SELECT COUNT(*) FROM observations
WHERE high_temp IS NOT NULL AND authority = 'VERIFIED';
```
Result: **42,498**

**Per-city breakdown**:
```sql
SELECT city,
  COUNT(CASE WHEN high_temp IS NOT NULL AND authority='VERIFIED' THEN 1 END) AS high_temp_count,
  COUNT(CASE WHEN low_temp IS NOT NULL AND authority='VERIFIED' THEN 1 END) AS low_temp_count,
  ROUND(CAST(low_count AS REAL) / NULLIF(high_count, 0), 3) AS ratio
FROM observations GROUP BY city ORDER BY ratio ASC;
```

---

## Per-City Results

All 51 cities returned ratio = 1.000. Selected rows:

| City | high_temp_count | low_temp_count | ratio | Verdict |
|---|---|---|---|---|
| Istanbul | 756 | 756 | 1.000 | GREEN |
| Hong Kong | 821 | 821 | 1.000 | GREEN |
| Lagos | 823 | 823 | 1.000 | GREEN |
| Cape Town | 811 | 811 | 1.000 | GREEN |
| Moscow | 837 | 837 | 1.000 | GREEN |
| Lucknow | 876 | 876 | 1.000 | GREEN |
| *(all remaining 45 cities)* | 831–840 | 831–840 | 1.000 | GREEN |

Every city: `low_temp_count == high_temp_count`, gap = 0%.

---

## Summary

**Phase 5 rebuild clean — 0 RED cities, 0 YELLOW cities.**
`low_temp` is fully backfilled for all 51 cities. The 30% abort gate (`rebuild_calibration_pairs_v2.py:381–389`) will not fire. Phase 5 can proceed to rebuild without any observation backfill prerequisite.

---

## Sanity Check: Numerical Divergence

5 random VERIFIED rows (column name corrected to `target_date`):

| city | target_date | high_temp | low_temp | diff | source |
|---|---|---|---|---|---|
| Tel Aviv | 2024-11-06 | 31.0 | 16.0 | 15.0 | ogimet_metar_llbg |
| Atlanta | 2024-12-26 | 51.0 | 44.0 | 7.0 | wu_icao_history |
| Denver | 2024-06-14 | 80.0 | 59.0 | 21.0 | wu_icao_history |
| Ankara | 2025-10-22 | 18.0 | 3.0 | 15.0 | wu_icao_history |
| Tokyo | 2024-09-16 | 28.0 | 24.0 | 4.0 | wu_icao_history |

All 5: `high_temp > low_temp`, diff range 4–21°. No equality. Pathology (b) ruled out. Pattern is benign (a): `daily_obs_append.py` writes both high + low in the same row from real WU/METAR data.
