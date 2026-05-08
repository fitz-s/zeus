# Fix #262 -- London F-to-C bin conversion at settlement reconstruction

**Date:** 2026-05-08  
**Branch:** `fix/262-london-f-to-c-settlement-2026-05-08`  
**Cluster A from PR #95 audit -- 317 obs_outside_bin QUARANTINED rows**

---

## Investigation findings

### Root cause (verified independently)

`_parse_temp_range(question)` in `src/data/market_scanner.py` extracts numeric
bin bounds from Polymarket market question text but **discards the unit symbol**.
`"Will London high be 40-41\xb0F on April 1?"` -> `(40.0, 41.0)` -- no unit returned.

These raw numbers flow into `_write_settlement_truth` as `pm_bin_lo=40, pm_bin_hi=41`.
London is now configured as `settlement_unit="C"` in `config/cities.json`.
A London observation of `5\xb0C` compared against `[40, 41]` always fails -> QUARANTINED
with `quarantine_reason="harvester_live_obs_outside_bin"`.

The 2025 Gamma London markets (London High Temperature for date X) used F bin labels
(typical North American/Polymarket convention from that era). After London's unit
reconfiguration to C, all 317 rows from those markets got quarantined.

### Unit-detection point

The unit symbol is present in the `range_label` field of each market outcome dict
(stored in `winning["range_label"]` in `write_settlement_truth_for_open_markets`).
This is the Gamma market question text verbatim. No schema column stores the unit
separately -- it is embedded in the question text only.

### Code path

```
write_settlement_truth_for_open_markets()
  |-- _extract_resolved_market_outcomes(event)  # builds outcomes with range_label
  |     |-- _parse_temp_range(question)         # returns (lo, hi) -- unit stripped
  |-- winning["range_low"], winning["range_high"]  # F values for 2025 London markets
  |-- _write_settlement_truth(conn, city, ..., pm_bin_lo=40, pm_bin_hi=41)
        |-- containment: 5.0 <= 40 <= 41? NO -> QUARANTINED   <- BUG
```

### F->C transform

`(F - 32) x 5/9`

- bin `40-41F` -> `4.444...C -- 5.0C`
- Observation `5.0C` = `41F` exactly -> contained in `[4.444, 5.0]C` -> VERIFIED

### Sample rows pattern (re-derived from audit description)

| pm_bin_lo | pm_bin_hi | bin_unit | obs_C | converted_lo_C | converted_hi_C | contained |
|-----------|-----------|----------|-------|----------------|----------------|-----------|
| 40.0 | 41.0 | F | 5.0 | 4.444 | 5.000 | YES |
| 41.0 | 42.0 | F | 6.0 | 5.000 | 5.556 | YES |
| 39.0 | 40.0 | F | 4.0 | 3.889 | 4.444 | YES |
| 42.0 | 43.0 | F | 6.5 | 5.556 | 6.111 | NO (obs between bins) |
| 38.0 | 39.0 | F | 4.0 | 3.333 | 3.889 | NO (obs > hi) |

Rows 4-5 remain correctly QUARANTINED after conversion. Only rows where the
observation actually falls within the converted bin become VERIFIED.

---

## Fix approach

**File:** `src/ingest/harvester_truth_writer.py`

### Changes (all in-scope, no schema change)

1. **`import re`** -- added to stdlib imports.

2. **`_detect_bin_unit(question: str) -> Optional[str]`** (new helper)  
   Scans question text for `F` or `C` degree symbol. Returns `'F'`, `'C'`, or `None`.
   Checks F before C; both-present is impossible in practice but handled defensively.

3. **`_f_to_c(val: float) -> float`** (new helper)  
   `(val - 32.0) * 5.0 / 9.0`

4. **`_write_settlement_truth` signature** -- added `pm_bin_unit: Optional[str] = None`
   keyword-only parameter. Backward-compatible default `None` -> no conversion applied
   (all existing callers unaffected).

5. **`_write_settlement_truth` body** -- added `bin_unit_converted: bool = False`
   initializer. Inside the containment `else` branch: if `pm_bin_unit == "F"` and
   `city.settlement_unit == "C"`, convert `effective_bin_lo` and `effective_bin_hi`
   via `_f_to_c`. The original `pm_bin_lo/pm_bin_hi` values are preserved for the
   DB write and provenance (what Gamma provided). Containment uses the converted
   effective values.

6. **Provenance dict** -- added `"pm_bin_unit"` and `"bin_unit_converted"` fields
   for audit traceability of which rows had conversion applied.

7. **`write_settlement_truth_for_open_markets`** -- added:
   ```python
   winning_bin_unit = _detect_bin_unit(winning.get("range_label", ""))
   ```
   and passes `pm_bin_unit=winning_bin_unit` to `_write_settlement_truth`.

### What was NOT changed

- `src/data/market_scanner.py` (`_parse_temp_range`) -- OUT_OF_SCOPE; the discard
  of unit from parse output is correct for its existing callers. The fix is applied
  at the consumer side.
- `config/cities.json` -- London stays `"unit": "C"`.
- No DB schema changes -- `pm_bin_unit` is provenance-only (JSON blob).
- The 317 already-quarantined rows are NOT auto-resolved by this PR -- future
  settlements from any re-submitted or remaining markets use the fix. Existing rows
  need a separate backfill (see follow-up below).

---

## Tests added

**File:** `tests/test_settlement_semantics_f_to_c.py` (9 tests)

| Test | What it covers |
|------|----------------|
| T1 `test_f_bin_c_city_obs_in_converted_range_is_verified` | Core fix: 40-41F bin + 5C obs -> VERIFIED after conversion |
| T2 `test_f_bin_c_city_obs_outside_converted_range_is_quarantined` | 10C obs outside converted bin stays QUARANTINED |
| T3 `test_c_bin_c_city_no_conversion_control` | C bin + C city -> no conversion, normal containment |
| T4 `test_f_bin_f_city_no_conversion_control` | F bin + F city -> no conversion, containment in F |
| T5 `test_detect_bin_unit_f_symbol` | `_detect_bin_unit` returns 'F' for F-symbol questions |
| T6 `test_detect_bin_unit_c_symbol` | `_detect_bin_unit` returns 'C' for C-symbol questions |
| T7 `test_detect_bin_unit_no_symbol` | `_detect_bin_unit` returns None with no degree symbol |
| T8 `test_f_to_c_arithmetic` | (40-32)*5/9 = 4.444, (41-32)*5/9 = 5.0, 32F = 0C, 212F = 100C |
| T9 `test_open_shoulder_f_bin_c_city_hi_only_contained` | Open-shoulder hi-only F bin converts hi bound to C |

**Results:** 97 passed (9 new + 88 regression), 0 failed.

---

## Follow-up: backfill of 317 quarantined rows

The 317 existing rows with `quarantine_reason='harvester_live_obs_outside_bin'`
and `city='London'` from the 2025 Gamma markets are NOT resolved by this PR.

Required follow-up tasks:
1. Write a backfill script that:
   - Queries `settlements_v2` / `settlements` for London rows where
     `quarantine_reason='harvester_live_obs_outside_bin'` and
     `pm_bin_lo >= 32` (values only plausible in F for London spring temps)
   - Re-runs containment after `_f_to_c()` conversion on `pm_bin_lo/pm_bin_hi`
   - Updates `authority='VERIFIED'` and `winning_bin` where containment passes
   - Records `bin_unit_converted=true` in provenance with audit_ref to this fix
2. Validate: count of resolved rows should be close to 317 (some may remain
   quarantined if the obs truly was outside even the converted bin).
3. Test the backfill script before running on production DB.

**Tracking:** add as issue #263 or as a follow-up task in the operations INDEX.md.
