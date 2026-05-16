# RUN — obs_outside_bin QUARANTINED rows root cause + remediation
# Fix #231
# Investigator: executor (sonnet), 2026-05-08

---

## queries-run

All queries executed against `state/zeus-world.db` (read-only).

```sql
-- 1. Schema discovery
PRAGMA table_info(settlements_v2);
-- Result: authority column holds 'QUARANTINED'/'VERIFIED'; quarantine_reason is in provenance_json

-- 2. Total count
SELECT COUNT(*) FROM settlements_v2
WHERE authority='QUARANTINED'
AND json_extract(provenance_json,'$.quarantine_reason')='harvester_live_obs_outside_bin';
-- Result: 612

-- 3. Distribution by city
SELECT city, COUNT(*) FROM settlements_v2
WHERE authority='QUARANTINED'
AND json_extract(provenance_json,'$.quarantine_reason') LIKE '%outside_bin%'
GROUP BY city ORDER BY 2 DESC LIMIT 30;

-- 4. Distribution by target_date (descending, top 30)

-- 5. Distribution by unit + bin_lo + bin_hi
SELECT json_extract(provenance_json,'$.unit') as unit,
       json_extract(provenance_json,'$.pm_bin_lo') as bin_lo,
       json_extract(provenance_json,'$.pm_bin_hi') as bin_hi,
       COUNT(*) FROM settlements_v2
WHERE authority='QUARANTINED'
AND json_extract(provenance_json,'$.quarantine_reason') LIKE '%outside_bin%'
GROUP BY unit, bin_lo, bin_hi ORDER BY 4 DESC LIMIT 30;

-- 6. F-unit vs C-unit by city
SELECT city, json_extract(provenance_json,'$.unit') as unit, COUNT(*)
FROM settlements_v2 WHERE authority='QUARANTINED'
AND json_extract(provenance_json,'$.quarantine_reason') LIKE '%outside_bin%'
GROUP BY city, unit ORDER BY 3 DESC LIMIT 30;

-- 7. London F->C check: obs_as_F vs bin
SELECT city, target_date, settlement_value as obs_c,
  ROUND(settlement_value*9.0/5.0+32,1) as obs_f,
  json_extract(provenance_json,'$.pm_bin_lo') as bin_lo,
  json_extract(provenance_json,'$.pm_bin_hi') as bin_hi
FROM settlements_v2 WHERE authority='QUARANTINED'
AND json_extract(provenance_json,'$.quarantine_reason') LIKE '%outside_bin%'
AND city='London' LIMIT 20;

-- 8. NULL-bin cluster: confirm both lo and hi null
SELECT COUNT(*) FROM settlements_v2 WHERE authority='QUARANTINED'
AND json_extract(provenance_json,'$.quarantine_reason')='harvester_live_obs_outside_bin'
AND json_extract(provenance_json,'$.unit')='F'
AND json_extract(provenance_json,'$.pm_bin_lo') IS NULL
AND json_extract(provenance_json,'$.pm_bin_hi') IS NULL;
-- Result: 181 (all F-unit NULL-bin rows have BOTH bins null)

-- 9. NULL-bin cluster: market_slug pattern
SELECT market_slug FROM settlements_v2 WHERE authority='QUARANTINED'
AND json_extract(provenance_json,'$.quarantine_reason') LIKE '%outside_bin%'
AND json_extract(provenance_json,'$.unit')='F'
AND json_extract(provenance_json,'$.pm_bin_lo') IS NULL LIMIT 5;
-- Result: all 'uma_backfill_*' synthetic slugs

-- 10. Cluster summary
SELECT
  CASE
    WHEN city='London' AND json_extract(provenance_json,'$.unit')='C'
      THEN 'CLUSTER_A: London_F_to_C_transition'
    WHEN json_extract(provenance_json,'$.unit')='F'
      AND json_extract(provenance_json,'$.pm_bin_lo') IS NULL
      AND json_extract(provenance_json,'$.pm_bin_hi') IS NULL
      THEN 'CLUSTER_B: null_bin_misclassification'
    WHEN json_extract(provenance_json,'$.unit')='C' AND city!='London'
      THEN 'CLUSTER_C: C_unit_WU_vs_UMA_disagreement'
    ELSE 'OTHER'
  END as cluster, COUNT(*)
FROM settlements_v2 WHERE authority='QUARANTINED'
AND json_extract(provenance_json,'$.quarantine_reason')='harvester_live_obs_outside_bin'
GROUP BY cluster ORDER BY 2 DESC;
-- Result:
--   CLUSTER_A: London_F_to_C_transition     | 317
--   CLUSTER_B: null_bin_misclassification   | 181
--   CLUSTER_C: C_unit_WU_vs_UMA_disagreement| 108
--   OTHER (genuine F-unit outside real bin) |   6

-- 11. Reconstruction provenance
SELECT json_extract(provenance_json,'$.reconstructed_at'),
       json_extract(provenance_json,'$.legacy_table'), COUNT(*)
FROM settlements_v2 WHERE authority='QUARANTINED'
AND json_extract(provenance_json,'$.quarantine_reason')='harvester_live_obs_outside_bin'
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 5;
-- All rows reconstructed from 'settlements' (old table) on 2026-05-07–08
```

---

## evidence-table

10 representative rows across 6 cities / 3 clusters:

| cluster | city | target_date | obs_value | obs_unit | bin_lo | bin_hi | obs_as_F | note |
|---|---|---|---|---|---|---|---|---|
| A | London | 2025-01-22 | 5.0°C | C | 40.0 | 41.0 | 41.0°F | obs_F fits bin exactly |
| A | London | 2025-01-23 | 9.0°C | C | 48.0 | 49.0 | 48.2°F | obs_F inside bin |
| A | London | 2025-01-24 | 11.0°C | C | 51.0 | 52.0 | 51.8°F | obs_F inside bin |
| A | Paris | 2026-02-11 | 14.0°C | C | 13.0 | 13.0 | 57.2°F | 1°C above WU point bin |
| A | Seoul | 2026-03-24 | 14.0°C | C | 13.0 | 13.0 | 57.2°F | same ±1 pattern |
| B | NYC | 2026-01-02 | 30.0°F | F | NULL | NULL | — | uma_backfill slug, no bin |
| B | Dallas | 2026-01-01 | 70.0°F | F | NULL | NULL | — | uma_backfill slug, no bin |
| B | Seattle | 2026-01-01 | 42.0°F | F | NULL | NULL | — | uma_backfill slug, no bin |
| C (genuine) | NYC | 2025-03-09 | 36.0°F | F | 53.0 | 54.0 | — | WU 36°F vs UMA 53-54°F; large miss |
| C (genuine) | NYC | 2026-04-16 | 65.0°F | F | 68.0 | 69.0 | — | WU 65°F vs UMA 68-69°F |

---

## root-cause-analysis

### Cluster A — London F→C transition (317 rows, 52%) [DIRECT_OBSERVATION]

**Cause**: Polymarket market questions for London dates 2025-01-22 through 2025-12-10 were written in °F (e.g. "40-41°F") because London was a Fahrenheit market at that time. The reconstruction (2026-05-07/08) re-parsed these questions via `_parse_temp_range()`, which strips the unit symbol and returns raw floats (40.0, 41.0). It then called `_write_settlement_truth()` with `city.settlement_unit="C"` (London's current config, post F→C transition). The observation (5°C) is correct in Celsius; the bin (40-41) is actually Fahrenheit. Since 5 ∉ [40, 41], the row is quarantined as `obs_outside_bin`. When the observation is converted to °F (5°C → 41°F), it lands squarely inside the bin.

**Code path**: `_write_settlement_truth()` line 292-293: `contained = pm_bin_lo <= rounded <= pm_bin_hi` — no unit awareness in the comparison.

**Scope**: London rows 2025-01-22 to 2025-12-10. Paris/Shenzhen/Taipei/Seoul etc. (108 rows, Cluster C) are a separate issue (WU vs UMA 1°C disagreement on point-bin markets, not a unit mismatch).

**Fix required**: Re-run reconstruction for London 2025-01-22–2025-12-10 with unit-aware bin comparison — detect that market question unit (°F) differs from `city.settlement_unit` (°C) and convert before comparison. This is a **data remediation**, not a live-code fix. The live write path (`write_settlement_truth_for_open_markets`) is safe because it only processes currently-active markets, which are already in the correct unit.

### Cluster B — NULL-bin misclassification (181 rows, 30%) [DIRECT_OBSERVATION]

**Cause**: Code bug in `_write_settlement_truth()` lines 291-305. When both `pm_bin_lo` and `pm_bin_hi` are `None`, the three-branch if/elif chain falls through without setting `contained=True`, so `contained` stays `False`, and the code writes `reason = "harvester_live_obs_outside_bin"`. This is wrong: the observation is not outside any bin — there simply was no bin available. All 181 rows are `uma_backfill_*` synthetic slugs that have no Polymarket winning-outcome bin.

**Code location**: `src/ingest/harvester_truth_writer.py` lines 291-305 (pre-fix).

**Fix**: Check both-None first; emit `"harvester_live_no_bin_info"` (QUARANTINED, observation value recorded). **Shipped in this PR.**

### Cluster C — WU vs UMA 1°C disagreement on point-bin markets (108 rows, 18%) [DIRECT_OBSERVATION]

**Cause**: For C-unit point-bin markets (Paris, Shenzhen, Taipei, Seoul, Beijing, etc.), the winning UMA outcome says "the high was 13°C" but WU ICAO history records 14°C. These are genuinely different data sources. The settlements_v2 row correctly records the WU observation and correctly quarantines because it doesn't match the resolved bin. No code bug; the quarantine is accurate. Resolution requires either accepting UMA authority over WU for these markets or treating 1°C disagreements as within tolerance — an operator policy decision.

### OTHER — Genuine °F outside bin (6 rows, <1%) [DIRECT_OBSERVATION]

NYC 2025-03-09 (WU=36°F vs UMA=53-54°F, 17°F gap) and 5 others. Correct quarantine; likely observation source mismatch on the day.

---

## fix

### Shipped: Cluster B (181 rows) — null-bin misclassification

**File**: `src/ingest/harvester_truth_writer.py` lines 290-313

**Before** (lines 290-305):
```python
if rounded is not None and math.isfinite(rounded):
    contained = False
    if pm_bin_lo is not None and pm_bin_hi is not None:
        contained = pm_bin_lo <= rounded <= pm_bin_hi
    elif pm_bin_lo is None and pm_bin_hi is not None:
        contained = rounded <= pm_bin_hi
    elif pm_bin_hi is None and pm_bin_lo is not None:
        contained = rounded >= pm_bin_lo
    if contained:
        authority = "VERIFIED"
        settlement_value = rounded
        winning_bin = _canonical_bin_label(pm_bin_lo, pm_bin_hi, city.settlement_unit)
        reason = None
    else:
        settlement_value = rounded
        reason = "harvester_live_obs_outside_bin"
```

**After**:
```python
if rounded is not None and math.isfinite(rounded):
    contained = False
    if pm_bin_lo is None and pm_bin_hi is None:
        # No bin information available — cannot evaluate containment.
        # Record the observation value but quarantine with a distinct reason
        # so data consumers can distinguish "obs outside known bin" from
        # "no bin was provided at all" (e.g. uma_backfill synthetic slugs).
        settlement_value = rounded
        reason = "harvester_live_no_bin_info"
    else:
        if pm_bin_lo is not None and pm_bin_hi is not None:
            contained = pm_bin_lo <= rounded <= pm_bin_hi
        elif pm_bin_lo is None and pm_bin_hi is not None:
            contained = rounded <= pm_bin_hi
        elif pm_bin_hi is None and pm_bin_lo is not None:
            contained = rounded >= pm_bin_lo
        if contained:
            authority = "VERIFIED"
            settlement_value = rounded
            winning_bin = _canonical_bin_label(pm_bin_lo, pm_bin_hi, city.settlement_unit)
            reason = None
        else:
            settlement_value = rounded
            reason = "harvester_live_obs_outside_bin"
```

Impact on existing DB rows: none (no backfill; future reconstruction runs and live writes will use the corrected reason). The 181 existing QUARANTINED rows retain their stale `quarantine_reason`; a separate DB migration can re-label them if desired.

### Not shipped: Cluster A (317 rows) — separate task recommended

Remediation requires unit-aware bin comparison during reconstruction. Scope: detect when market question unit (from `_parse_temp_range` — which discards the unit symbol) differs from `city.settlement_unit`, and convert bins to the city's unit before comparison. This touches the reconstruction script path, not the live write path. Recommend opening a separate task: `fix/231a-london-f-to-c-reconstruction-bins`.

### Not shipped: Cluster C (108 rows) — operator policy decision

WU vs UMA 1°C disagreement on point-bin C markets. No code bug; quarantine is accurate. Operator must decide whether to accept WU authority when it disagrees with the UMA-resolved bin by ≤1°C.

---

## tests-added

`tests/test_harvester_truth_writer_null_bin.py` — 6 tests:
- T1: null bins → reason `harvester_live_no_bin_info` (not `obs_outside_bin`)
- T2: null bins → authority QUARANTINED, settlement_value recorded
- T3: open-shoulder lo-only → VERIFIED (regression)
- T4: open-shoulder hi-only → VERIFIED (regression)
- T5: lo+hi range, obs inside → VERIFIED (regression)
- T6: lo+hi range, obs outside → `harvester_live_obs_outside_bin` (regression)

All 6 pass. Full related suite (53 tests) passes.

---

## not-shipped-explanation

**Cluster A** (317 rows, dominant): Root cause is in the reconstruction script's bin parsing — `_parse_temp_range()` discards the unit symbol from Gamma market questions, losing the information needed to detect F-vs-C mismatch. Fix requires touching the reconstruction path. Logged as separate task `fix/231a-london-f-to-c-reconstruction-bins`.

**Cluster C** (108 rows): Correct quarantine. WU and UMA disagree by 1°C on point-bin markets. Operator policy decision required.

**DB backfill of existing rows**: Not shipped. The 181 existing Cluster B rows in the DB still carry `quarantine_reason='harvester_live_obs_outside_bin'`; a targeted `UPDATE` could re-label them, but that is a separate DB migration outside this PR's scope.
