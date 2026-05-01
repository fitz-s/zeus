# Forecasts Table Consumer Audit (F11.5 migration scope)

Created: 2026-04-28
Authority basis: live grep against `src/`, `scripts/`, `tests/` at HEAD `5bd9be8`
Status: evidence appendix to F11 packet; informs follow-up F11.5-migrate slice scoping.

---

## 1. Methodology

```
grep -rn 'FROM forecasts\b\|FROM {self._sp}forecasts\b\|forecasts WHERE\|JOIN forecasts' \
    src/ scripts/ tests/ | grep -v __pycache__ | grep -v 'test_'
```

This locates every read site on the `forecasts` table outside of test fixtures. The F11.5 `training_eligibility` SQL filter (`SKILL_ELIGIBLE_SQL` + `ECONOMICS_ELIGIBLE_SQL`) should be applied at the read sites that participate in **training/learning** flows; diagnostic / coverage / count readers do NOT need the filter.

---

## 2. Reader inventory + migration verdict

| File:Line | Purpose | Needs F11.5 filter? | Notes |
|---|---|---|---|
| `src/data/hole_scanner.py:363` | Coverage scanner — counts (city, source, target_date) tuples; flags missing rows | ✗ No | Coverage is purpose-agnostic; ALL rows count for completeness, not just training-eligible. |
| `scripts/etl_historical_forecasts.py:71` | Legacy `forecasts` → `historical_forecasts` ETL | ✓ **Yes (SKILL filter)** | Output feeds calibration / training; RECONSTRUCTED rows would corrupt training. Highest-priority migration. |
| `src/engine/replay.py:314` | Replay synthetic forecast fallback (`forecasts_table_synthetic`) | ⚠ Partial | Already labeled `decision_reference_source: "forecasts_table_synthetic"` and rejected by SKILL purpose via S1's `gate_for_purpose`. The read at L314 is purpose-agnostic; the gate happens downstream. The pure SQL reader does NOT need a WHERE filter, but a defense-in-depth filter could be added. |
| `scripts/etl_forecast_skill_from_forecasts.py:120` | Forecast skill ETL — produces forecast skill aggregates | ✓ **Yes (SKILL filter)** | Output feeds skill scoring; semantically requires only training-eligible rows. |
| `scripts/etl_forecasts_v2_from_legacy.py:104, 141` | Legacy `forecasts` → `forecasts_v2` ETL | ⚠ Partial — preserve provenance | When migrating legacy → v2, the per-row `availability_provenance` should propagate to v2 (not be dropped). v2 schema may need extension. |

| File:Line | Purpose | Needs filter? |
|---|---|---|
| `scripts/backfill_openmeteo_previous_runs.py:330, 369, 370` | COUNT(*) before/after for backfill report | ✗ No (count-only) |
| `scripts/migrate_forecasts_availability_provenance.py:44, 53` | F11.2 schema migration; counts + groupby | ✗ No (own slice) |
| `scripts/backfill_forecast_issue_time.py:50, 138, 156, 161, 166` | F11.4 backfill; reads NULL rows + per-source distribution | ✗ No (own slice) |
| `scripts/resume_backfills_sequential.sh:63, 67` | Operational DELETE/COUNT for partial city resets | ✗ No (operational) |

---

## 3. F11.5-migrate slice scope (recommended for follow-up packet)

**Scope = 2 scripts** (highest-priority training-eligibility leaks):

1. `scripts/etl_historical_forecasts.py:71`
   - Current: `FROM forecasts WHERE ...`
   - After: `FROM forecasts WHERE ... AND availability_provenance IN ('fetch_time', 'recorded', 'derived_dissemination')` (or equivalent via `SKILL_ELIGIBLE_SQL`)
2. `scripts/etl_forecast_skill_from_forecasts.py:120`
   - Same pattern.

**Optional defense-in-depth (lower priority)**:

3. `src/engine/replay.py:314` — add `availability_provenance` predicate redundantly with the existing `gate_for_purpose` downstream check.
4. `scripts/etl_forecasts_v2_from_legacy.py:141` — propagate `availability_provenance` from legacy row to v2 row.

**Out of scope**:
- All counting / hole-scanner / migration / backfill readers — they need every row, not just training-eligible.

---

## 4. Apply ordering

This migration depends on F11.4 backfill having populated `availability_provenance` on existing rows. Apply order:

1. F11.2 schema migration (operator approves) — `availability_provenance` column added.
2. F11.4 backfill (operator approves) — 23,466 rows populated; new cron rows arrive populated via F11.3 writer.
3. **F11.5-migrate** (this audit's recommendation) — modify the 2 scripts to add filter.
4. Re-run any historical ETL that consumed unfiltered `forecasts` data, since output may have RECONSTRUCTED contamination.

---

## 5. Test coverage hooks

Each migrated script should add a test in:

- `tests/test_etl_historical_forecasts.py` (NEW) — assert that running the ETL with mixed-provenance fixture produces only DERIVED+RECORDED+FETCH_TIME rows.
- `tests/test_etl_forecast_skill.py` (NEW or existing) — same pattern.

Both can use the in-memory fixture style from `tests/test_backtest_training_eligibility.py`.

---

## 6. Memory + audit references

- L20 grep-gate: every file:line cited above was grep'd within the writing window 2026-04-28; no premise rot expected within next 24h.
- The F11.5 module (`src/backtest/training_eligibility.py`) was committed at HEAD `5b1b05d`. The 2 scripts above import from it for the migration.
- Per `feedback_grep_gate_before_contract_lock.md`, when this audit's findings inform a future packet's contract, the future packet must re-grep these citations within its own 10-min window.
