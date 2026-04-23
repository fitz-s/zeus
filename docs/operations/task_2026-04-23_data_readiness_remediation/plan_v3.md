# Data Readiness Remediation — Master Plan v3

**Status**: DRAFT v3 — supersedes plan_v2.md. Awaiting architect + critic review.
**Created**: 2026-04-23
**Authority basis**: plan_v2 + architect review + critic review + DR-07 RCA investigation (2026-04-23)

## Relationship to v2

Plan v2 stands as the canonical issue inventory. v3 is **surgical amendment** fixing:
- 1 fabricated root cause (DR-07) — rewritten with direct data-signature evidence
- 1 misinterpreted "bug" (933 C-city `lo=hi=value`) — retracted; actually Polymarket point-bin semantics
- 4 P0 execution blockers from reviewer consensus
- 5 P1 corrections
- 6 P2 precision fixes
- Added Issue DR-31 (schema CHECK for winning_bin), DR-32 (promote `_build_pm_truth.py` to tick lane)

**Read v2 first** for Section 1 (environment snapshot), Section 4 (antibody suite base), Section 5 (file inventory), Section 6 (risks), Section 9 (scope boundaries), Section 10 (receipt). This document patches/supersedes the parts listed below.

---

## Section 0 — v2→v3 delta log

| # | v2 item | v3 action | Evidence source |
|---|---|---|---|
| D1 | **DR-07 root cause: "harvester writes NULL when `_find_winning_bin` returns None"** | **REWRITTEN.** Real cause: historical one-shot script (no longer in codebase) ingested `data/pm_settlement_truth.json` on 2026-04-16T12:39:58, omitting winning_bin+settlement_value+unit columns. Harvester's `continue` guard at `harvester.py:313-314` PREVENTS the claimed v2 failure mode. | architect + critic + my own RCA this session |
| D2 | DR-07 solution: patch harvester + Gamma re-fetch 1,202 rows | **REPLACED.** Script `scripts/backfill_settlements_from_pm_truth.py` reads existing `data/pm_settlement_truth.json` (1,566 entries, 100% `pm_high` populated, exact bin metadata) and UPDATES 1,561 rows. Only 1 DB row (Denver/2026-04-15) not in JSON. | `python3 -c "json analysis"` run 2026-04-23 |
| D3 | "933 C-city placeholders = bug (promote to DR-31)" | **RETRACTED.** Pattern is Polymarket C-city point-bin semantics (1°C-wide bin: `lo=hi=value`). Not a defect. Evidence: JSON samples London/2025-12-31 `lo=5.0 hi=5.0 pm_high=5.0`; Buenos Aires `lo=-999 hi=40` (below-range shoulder); Seoul `lo=-1 hi=999` (above-range shoulder). | JSON inspection 2026-04-23 |
| D4 | R2-B forbidden list | **EXTENDED** with `src.calibration` (critic P0-1). Still need `src.data.polymarket_client`. | `src/data/daily_obs_append.py:69` has `from src.calibration.manager import season_from_date` |
| D5 | DR-10 scope: 3 src.data callers | **EXPANDED to 6 callers** (+3 script-layer): `scripts/backfill_hourly_openmeteo.py:52,133`, `scripts/backfill_wu_daily_all.py:432`, `scripts/backfill_hko_daily.py:319`. Also add `season_from_date` to relocation target. | critic P1-1 |
| D6 | DR-11 test names | **CORRECTED**: both tests at `tests/test_k2_live_ingestion_relationships.py` are `test_R12_main_py_defines_all_k2_functions` (L790) and `test_R12_main_py_references_k2_job_ids` (L813). v2 wrote R11/R12 mix. | critic + architect both verified |
| D7 | R2-D AC `grep -c "from src.data" src/main.py == 0` | **CORRECTED to whitelist grep**: `grep -cE "from src.data\.(daily_obs_append\|hourly_instants_append\|solar_append\|forecasts_append\|hole_scanner\|ecmwf_open_data) " src/main.py` must be 0. `src.data.polymarket_client` + `src.data.proxy_health` remain (trading-intrinsic). | critic P0-3 |
| D8 | Lucknow duplicates = 60 | **CORRECTED to 59** (scientist snapshot drifted 1 row). Total pairs unchanged: 59+7+2=68. | `SELECT city, target_date, COUNT(*) FROM observations GROUP BY 1,2 HAVING COUNT(*)>1` re-run 2026-04-23 |
| D9 | AC-R3-2 "≤ 15" after DR-12 + DR-16 | **CORRECTED to ≤ 22** (15 + 7 = 22) OR explicit "≤ 0 with documented unrecoverable cases" | critic P1-2 |
| D10 | DR-13 "6 active through 2026-03-15" | **REFINED**: 5 active through 2026-03-15 (kmia, katl, cyyz, fact, kdal) + 1 (efhk) through 2026-03-11. | critic P2-2 |
| D11 | DR-05 rollback | **STRENGTHENED**: backup captures exact row IDs before DELETE (not sentinel threshold); plan's existing backup step updated to use `SELECT id WHERE ... ORDER BY id` output | architect + critic P2-1 |
| D12 | DR-17 dedupe direction | **STRENGTHENED**: value-compare step added. If WU and Ogimet high_temp disagree, log to `state/quarantine/observations_dupe_disagreements.jsonl` AND flag for operator review. Plan v2 naively preferred WU, which is exactly the source of 3 DR-05 poisoned rows. | critic P1-4 |
| D13 | DR-14 AC with "OR documented" escape | **CONSTRAINED**: if RCA unrecoverable, AC requires a tombstone doc at `docs/operations/task_2026-04-23_data_readiness_remediation/auto_pause_rca_unrecoverable.md` with exact log paths checked + operator sign-off. Not a free pass. | critic P2/architect |
| D14 | DR-01 "~20 K1 columns" prose | **CORRECTED**: INSERT at `forecasts_append.py:256-262` references **14 columns**; live `forecasts` has 12 matching; 2 missing (`rebuild_run_id`, `data_source_version`). | critic H2 |
| D15 | Cross-lane arrows missing | **ADDED** to Section 3 DAG: DR-15 → DR-12, DR-01 → R2-A forecasts_tick.py, DR-28 (pytz) → R2-A (shared time helper) | architect |
| D16 | status_summary.risk_level=DATA_DEGRADED | **REFINED**: `risk_level` is currently `null` (may be transient; evidence had a write-time discrepancy). Claim reframed: `status_summary.discrepancy_flags contains 'v2_empty_despite_closure_claim'` which is the stable anchor. | critic P2-5 |
| D17 | Task Zero planning-lock command | **CONCRETIZED**: new script `scripts/_planning_lock_for_plan_v3.sh` generates exact argv by parsing Section 5 of v3; invokes topology_doctor.py; exits non-zero on mismatch | critic P1-5 |
| D18 | DR-07 harvester "patch step 2" ambiguity (caller vs callee) | **DISAMBIGUATED**: modify CALLEE `_write_settlement_truth` to raise if `winning_bin is None` — defensive guard matching the caller's `continue`. Caller behavior unchanged. | critic ambiguity risk |
| D19 | DR-31 (new) | Add CHECK constraint on `settlements`: `CHECK(winning_bin IS NOT NULL OR authority='UNRECOVERABLE')` post DR-07 backfill | structural decision #5 |
| D20 | DR-32 (new) | Promote `scripts/_build_pm_truth.py` to R2 ingest lane `polymarket_truth_tick.py` (~ every 6h). JSON becomes canonical truth source; DB is projection of JSON. | structural decision from D1 RCA |

---

## Section 1 (replacement for v2 Section 1) — RCA-verified evidence base for DR-07

### DR-07 root cause — forensic timeline

| Time | Event | Evidence |
|---|---|---|
| Pre 2026-04-16 06:28 UTC | `data/pm_settlement_truth.json` authored (1,566 entries, `pm_high` populated for all, `pm_bin_lo/hi` per Polymarket bin shape, `resolution_source=<WU URL>`) | `ls -la data/pm_settlement_truth.json` → Apr 16 06:28 |
| 2026-04-16 **12:39:58.026729** UTC | Unknown script reads JSON → INSERTs 1,562 rows into `settlements` with: `city, target_date, pm_bin_lo, pm_bin_hi, unit (F only — C cases got NULL), settled_at=<now>, authority=VERIFIED, settlement_source=<WU URL>`. **Explicitly omits** winning_bin + settlement_value + pm_high + (for C-city) unit. | `SELECT DISTINCT settled_at FROM settlements` → single value `2026-04-16T12:39:58.026729+00:00` for all 1,562 rows |
| 2026-04-16 14:22:32 UTC | Commit `d99273a` lands: deletes `scripts/rebuild_settlements.py` (unrelated — wrote `<src>_rebuild` format), deletes `scripts/audit_polymarket_city_settlement.py`, `scripts/smoke_test_settlements.py`; keeps `scripts/_build_pm_truth.py` as the current JSON producer | `git show d99273a --stat` |
| 2026-04-16+ | No `winning_bin` writer path remains. New settlements via `harvester._write_settlement_truth` correctly gated by `continue` at L313-314; wouldn't be called with None anyway. | `harvester.py:312-320` re-read 2026-04-23 |

### Why the v2 "harvester is the culprit" narrative was wrong

Reading `harvester.py:312-320` directly:
```python
winning_label, winning_range = _find_winning_bin(event)
if winning_label is None:
    continue           # skips to next event; _write_settlement_truth NOT called
_write_settlement_truth(
    shared_conn, city, target_date, winning_label, ...)
```

The caller's `continue` guard means `_write_settlement_truth` is never invoked with a None winning_label. Therefore the harvester is not the source of the 1,562 NULL-winning_bin rows. v2's narrative was inference inherited from scout subagent without re-verifying the `continue` guard.

### Data signature proving one-shot bulk ingest

- `SELECT DISTINCT settled_at FROM settlements` returns exactly one value: `2026-04-16T12:39:58.026729+00:00` — single write to the microsecond across all 1,562 rows.
- `SELECT COUNT(*) FROM settlements WHERE market_slug IS NULL` = 1,562. Harvester's INSERT (L558-568) always sets `market_slug` from `event_slug`. Therefore these rows came from a writer that did NOT set market_slug.
- Per JSON entries, `resolution_source` field IS the WU URL (matches DB `settlement_source`).
- `authority='VERIFIED'` for all 1,562 — likely the bulk writer's default.

### 933 C-city `lo=hi=value` pattern — SEMANTIC not bug

v2 plan's DR-07 post-review flagged 933 rows as "placeholder anomaly". Architect proposed promoting to DR-31. **This was a misinterpretation**.

Polymarket C-city bins are **1°C-wide point bins**: `lo=hi=settlement_value`. Evidence from JSON sample:
- `London/2025-12-31`: `pm_bin_lo=5.0, pm_bin_hi=5.0, pm_high=5.0, unit=C` — point bin at 5°C
- `Buenos Aires/2025-12-31`: `pm_bin_lo=-999.0, pm_bin_hi=40.0, pm_high=40.0` — below-range shoulder bin
- `Seoul/2025-12-31`: `pm_bin_lo=-1.0, pm_bin_hi=999.0, pm_high=-1.0` — above-range shoulder bin

Meanwhile F-city range bins: `NYC/2025-12-30`: `pm_bin_lo=32.0, pm_bin_hi=33.0, pm_high=32.5` — 2°F-wide range bin.

This is the canonical Polymarket bin structure per `docs/reference/zeus_market_settlement_reference.md`. Not a placeholder; not a bug. DR-31 in v3 repurposed for schema CHECK constraint (see below).

---

## Section 2 — DR-07 full replacement (v3)

### Issue #07 (v3) — Settlement pipeline: 1,562 NULL-winning_bin + 629 NULL-settlement_value; missing JSON→DB projection

- **ID**: DR-07 (v3 REWRITE)
- **Severity**: 🔴 P0 — blocks all training via empty calibration_pairs_v2
- **Discovery source**: completeness agent flagged symptoms; architect + critic caught v2's fabricated harvester narrative; my RCA on 2026-04-23 traced real cause
- **Detection method**:
  - `[VERIFIED sql]` `SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL = 1,562`
  - `[VERIFIED sql]` `SELECT COUNT(*) FROM settlements WHERE settlement_value IS NULL = 629`
  - `[VERIFIED sql]` `SELECT DISTINCT settled_at FROM settlements` returns single value
  - `[VERIFIED fs]` `data/pm_settlement_truth.json` exists with 1,566 entries
  - `[VERIFIED code]` `harvester.py:313-314` contains None-guard
  - `[VERIFIED cross]` JSON ↔ DB overlap = 1,561/1,562 (99.9%)
- **Key defect**: system has NO JSON → DB projection path. `scripts/_build_pm_truth.py` produces the authoritative JSON; a one-shot historical writer (no longer in codebase) partially ingested it on 2026-04-16, omitting winning_bin + settlement_value + (C-city) unit. Every new Polymarket settlement today produces a JSON entry but no DB update.
- **Macro-context preserved**:
  - Polymarket Gamma API is the Polymarket side-of-truth (via `_build_pm_truth.py`)
  - JSON shape (city / date / pm_bin_lo / pm_bin_hi / pm_high / unit / resolution_source) is stable
  - Harvester's `continue` guard at L313-314 is correct; do not alter caller semantics
  - C-city point-bin structure (`lo=hi=value`) is the canonical Polymarket representation; DO NOT treat as defect
- **New solution** (structural):
  1. JSON is canonical; DB is projection
  2. Add JSON → DB projection script
  3. Schedule `_build_pm_truth.py` as R2 ingest lane (polymarket_truth_tick) to keep JSON fresh
  4. Add schema-level CHECK preventing future `winning_bin IS NULL` settlements
  5. Add callee-side defensive guard in `harvester._write_settlement_truth` (idempotent with caller guard)
- **Fix procedure**:
  1. **[Backup]** `SELECT id, city, target_date, settlement_value, winning_bin, pm_bin_lo, pm_bin_hi, unit FROM settlements WHERE winning_bin IS NULL OR settlement_value IS NULL` → `state/quarantine/settlements_pre_backfill_20260423.jsonl` (captures row IDs; restore requires ID match)
  2. **Create** `scripts/backfill_settlements_from_pm_truth.py`:
     - Reads `data/pm_settlement_truth.json`
     - For each entry: compute `winning_bin` per bin type (see algorithm below)
     - `UPDATE settlements SET settlement_value=pm_high, winning_bin=<computed>, pm_bin_lo=?, pm_bin_hi=?, unit=? WHERE city=? AND target_date=?`
     - Assert `changes()=1` per row; log un-matched rows
     - Emit summary: rows matched, rows updated, rows skipped-because-no-JSON-entry
  3. **Run** backfill. Verify:
     - `[SQL]` `SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL` drops from 1,562 to **≤ 1** (just Denver/2026-04-15 orphan)
     - `[SQL]` `SELECT COUNT(*) FROM settlements WHERE settlement_value IS NULL` drops from 629 to **≤ 1**
  4. **Handle Denver/2026-04-15 orphan**: re-run `python scripts/_build_pm_truth.py` to refresh JSON; if Denver still absent, check Polymarket for the market (may have been voided/no-trade); mark as authority='UNRECOVERABLE' with specific reason in `provenance_metadata`
  5. **Patch `harvester._write_settlement_truth`** (defensive, idempotent with caller guard):
     - Add at function top: `if winning_bin is None or winning_bin == "": raise ValueError(f"_write_settlement_truth called with empty winning_bin for {city}/{target_date}")`
     - Add after UPDATE: `if conn.execute("SELECT changes()").fetchone()[0] == 0: # INSERT fallback` and after INSERT: `assert conn.execute("SELECT changes()").fetchone()[0] >= 1`
  6. **Add schema CHECK** (DR-31): `ALTER TABLE settlements ADD CONSTRAINT winning_bin_present CHECK(winning_bin IS NOT NULL OR authority='UNRECOVERABLE')` — SQLite doesn't support ALTER ADD CONSTRAINT; alternative is recreate table with CHECK + copy data. Preferred: post-backfill antibody test `tests/test_settlements_winning_bin_present.py` asserts invariant daily.
  7. **Schedule `_build_pm_truth.py`** (DR-32): new tick script `scripts/ingest/polymarket_truth_tick.py` wraps `_build_pm_truth.py` + auto-triggers backfill; runs every 6h (00:00, 06:00, 12:00, 18:00 UTC)

- **winning_bin computation algorithm** (per JSON entry):
  ```python
  def compute_winning_bin(pm_bin_lo, pm_bin_hi, pm_high, unit):
      # Shoulder detection
      if pm_bin_lo <= -900:  # below-range shoulder
          return f"{pm_bin_hi}°{unit} or below"
      if pm_bin_hi >= 900:   # above-range shoulder
          return f"{pm_bin_lo}°{unit} or higher"
      # Point bin (C-city)
      if pm_bin_lo == pm_bin_hi:
          return f"{int(pm_bin_lo)}°{unit}"
      # Range bin (F-city)
      return f"{int(pm_bin_lo)}-{int(pm_bin_hi)}°{unit}"
  ```
  **Verify** against `src/contracts/settlement_semantics.py` bin-labeling conventions before running.

- **Rollback**: for each UPDATEd row, restore from `state/quarantine/settlements_pre_backfill_20260423.jsonl` by row ID. `UPDATE settlements SET settlement_value=?, winning_bin=?, pm_bin_lo=?, pm_bin_hi=?, unit=? WHERE id=?` for each captured row. Antibody test removed. Harvester patch reverted via git revert.

- **Verification / acceptance**:
  - `[AC-R3-W]` `SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL` ≤ 1 (just Denver orphan until resolved)
  - `[AC-R3-V]` `SELECT COUNT(*) FROM settlements WHERE settlement_value IS NULL` ≤ 1
  - `[AC-R3-U]` Post-backfill: `SELECT COUNT(*) FROM settlements WHERE unit IS NULL` == 0 (previously only F-city had unit; backfill populates C-city too)
  - `[AC-R3-HC]` `tests/test_settlements_winning_bin_present.py` green
  - `[AC-R3-FC]` `tests/test_harvester_winning_bin_required.py` green (raises on None input)
  - `[AC-R3-QC]` `state/quarantine/settlements_pre_backfill_20260423.jsonl` contains exactly 1,562 (null winning_bin) or 629 (null settlement_value) pre-update snapshots

- **Possible omissions**:
  - The 5 JSON duplicate entries (1,566 vs 1,561 unique) — may represent multi-market same day; investigate during backfill run
  - pm_high precision: JSON stores floats; SettlementSemantics rounding may need application (e.g., HKO floor for HK). Verify winning_bin algorithm handles this
  - Some older settlements may have stale pm_bin_lo/hi that disagrees with current Gamma data — if Gamma's truth has evolved, JSON truth wins (JSON is derived from closed markets at time of scrape)

- **Post-completion review points**:
  - Monitor `data/pm_settlement_truth.json` freshness via polymarket_truth_tick log
  - If Gamma API returns altered bin data for a previously-settled event, should backfill re-run? Probably not — closed markets are immutable. But add a divergence detector in the tick.
  - Denver/2026-04-15 orphan: follow up; may reveal a class of "settled but not via Polymarket closed-events API" markets

---

## Section 3 — Updated Issue Inventory (delta-only)

### DR-09 R2-B forbidden list (v3)

New forbidden list for `tests/test_ingest_isolation.py` AST-walker:

```python
FORBIDDEN_PREFIXES = (
    "src.engine",
    "src.execution",
    "src.strategy",
    "src.signal",
    "src.supervisor_api",
    "src.control",
    "src.observability",
    "src.main",
    "src.data.polymarket_client",  # trading-intrinsic; live CLOB client
    "src.calibration",              # training logic, belongs to signal-side
)
```

Additionally: antibody scope extends to `src/data/*.py` (not just `scripts/ingest/*`). The data-layer imports already violate (DR-10 targets) — post-DR-10 they stop, and the antibody permanently enforces.

Transitive check: walk imports recursively. Flag any path `scripts/ingest/X → src.data.Y → <forbidden>`.

String scan: reject any file containing `importlib.import_module(`, `__import__(`, or `exec(` with runtime-computed module name.

### DR-10 expanded target list (v3)

Relocate BOTH helpers into `src/data/_time_utils.py`:
- `_is_missing_local_hour` (from `src/signal/diurnal.py:19`)
- `season_from_date` and `season_from_month` (from `src/calibration/manager.py`)

Update callers:
- `src/data/daily_obs_append.py:69, 72`
- `src/data/hourly_instants_append.py:50`
- `src/data/ingestion_guard.py:475`
- `src/data/observation_client.py:436`
- `src/signal/diurnal.py` (3 internal usages)
- `scripts/backfill_hourly_openmeteo.py:52, 133`
- `scripts/backfill_wu_daily_all.py:432`
- `scripts/backfill_hko_daily.py:319`

Post-relocation: leave re-export shims in `src.signal.diurnal` and `src.calibration.manager` for a 30-day deprecation window, then remove.

### DR-11 test naming (v3 corrected)

Actual test names at `tests/test_k2_live_ingestion_relationships.py`:
- L790: `test_R12_main_py_defines_all_k2_functions`
- L813: `test_R12_main_py_references_k2_job_ids`

Both are R12, not R11. R11 tests in same file are URL-matching tests (e.g., `test_R11_wu_url_matches_backfill`) — unrelated.

Fix action: rewrite both R12_* tests to assert new structure (scripts/ingest/ package, manifest yaml, plist references). Do NOT touch R11 URL tests.

### DR-13 meteostat active sources (v3 corrected)

5 active through 2026-03-15 (exact `MAX(utc_timestamp)` = `2026-03-15T23:00:00+00:00`):
`meteostat_bulk_kmia`, `_katl`, `_cyyz`, `_fact`, `_kdal`

1 active through 2026-03-11 (MAX = `2026-03-11T23:00:00+00:00`):
`meteostat_bulk_efhk`

All others dropped out between 2025-07-27 and 2026-02-XX.

### DR-17 dedupe direction (v3 strengthened)

Before DELETE, value-compare step:

```sql
-- Extract disagreeing pairs
SELECT city, target_date, source, high_temp 
FROM observations 
WHERE (city, target_date) IN (
    SELECT city, target_date FROM observations 
    GROUP BY 1, 2 HAVING COUNT(*) > 1
)
ORDER BY city, target_date, source;
```

Python wrapper:
- If all pairs' high_temp values agree (within unit tolerance) → safe to dedupe keeping WU
- If ANY pair disagrees → write to `state/quarantine/observations_dupe_disagreements.jsonl` + skip that pair + alert operator

This prevents the DR-05 class of error (v2 naive "keep WU" assumption fails when WU is the poisoned source).

### DR-14 AC (v3 constrained)

If auto-pause RCA unrecoverable (logs rotated):
- `[AC]` `docs/operations/task_2026-04-23_data_readiness_remediation/auto_pause_rca_unrecoverable.md` exists with:
  - List of exact log file paths grep'd
  - Decision-log query range + result count
  - Operator sign-off signature line (filled at execution time)

Without this, plan cannot close DR-14 via "document escape hatch". Operator must explicitly acknowledge unrecoverable-state before ACing.

### DR-31 (NEW) — Schema-level CHECK for winning_bin

- **ID**: DR-31
- **Severity**: 🟠 P1 — Immune-System antibody
- **Discovery source**: plan v3 structural decision
- **Detection method**: none at time of drafting — proactive defense
- **Key defect**: current schema has no constraint preventing future writers from INSERTing NULL winning_bin
- **Macro-context preserved**: existing VERIFIED authority semantic; introduces UNRECOVERABLE as explicit escape-hatch
- **New solution**: recreate settlements table with CHECK constraint; SQLite doesn't support ADD CONSTRAINT post-hoc
- **Fix procedure**:
  1. Backup settlements table to `state/quarantine/settlements_pre_check_migration.jsonl`
  2. `CREATE TABLE settlements_new AS SELECT * FROM settlements; DROP TABLE settlements; ALTER TABLE settlements_new RENAME TO settlements;` — or better: write new CREATE with CHECK, INSERT INTO ... SELECT from old
  3. Execute only AFTER DR-07 backfill completes (else 1,562 pre-backfill rows violate the check)
  4. Add `tests/test_settlements_winning_bin_present.py` asserting invariant
- **Rollback**: restore backup; drop CHECK
- **Verification**: `PRAGMA foreign_key_check; PRAGMA integrity_check;` + test green
- **Possible omissions**: CHECK not enforced across older ALTER path if operator somehow migrates again; document in schema comment
- **Post-completion review**: consider extending CHECK to `settlement_value NOT NULL` and other invariants

### DR-32 (NEW) — Promote `_build_pm_truth.py` to ingest lane `polymarket_truth_tick.py`

- **ID**: DR-32
- **Severity**: 🟠 P1 — sustainability of DR-07 fix
- **Discovery source**: plan v3 structural decision from DR-07 RCA
- **Detection method**: `scripts/_build_pm_truth.py` runs only on manual invocation; no cron/launchd home
- **Key defect**: without scheduled refresh, JSON ages; new closed markets don't land in DB
- **Macro-context preserved**: `_build_pm_truth.py` current behavior (Gamma API fetch → JSON write); wrapper adds scheduling + downstream DB sync
- **New solution**: new tick script wraps `_build_pm_truth` + `backfill_settlements_from_pm_truth` in sequence; launchd schedule 4×/day
- **Fix procedure**:
  1. Create `scripts/ingest/polymarket_truth_tick.py`:
     - Calls `_build_pm_truth.py` (refresh JSON)
     - Then calls `backfill_settlements_from_pm_truth.py` (sync DB)
     - Logs to `state/ingest_log.jsonl` per lane
  2. Create launchd plist `com.zeus.ingest.polymarket_truth.plist` — schedule at 00:30, 06:30, 12:30, 18:30 UTC
  3. Add to DR-09's lane count (now 9 lanes instead of 8)
- **Rollback**: unload plist; delete script
- **Verification**: `[AC]` plist exists; tick script imports cleanly; after 24h plist-run, `data/pm_settlement_truth.json` mtime is within 12h
- **Possible omissions**: Gamma API failure during tick — script exits with structured error; next cycle recovers
- **Post-completion review**: if Gamma rate-limits on frequent polling, reduce cadence

---

## Section 4 — Updated antibody suite (v3)

v2's 10 antibodies + 3 new:

| Test | Purpose | Phase |
|---|---|---|
| ...(v2's 10)... | (unchanged) | — |
| `tests/test_settlements_winning_bin_present.py` | DR-31 invariant | R3 |
| `tests/test_polymarket_truth_json_freshness.py` | DR-32 lane freshness (<12h) | R3/R2 |
| `tests/test_no_dynamic_imports.py` | Reject `importlib.import_module`/`__import__`/`exec` in ingest scripts | R2 |

Also extend:
- `tests/test_all_tables_schema_alignment.py` (generalizing DR-01's forecast-only check) per architect recommendation — one test fleet-wide

---

## Section 5 — Cross-lane dependencies (added from architect)

Explicit arrows added to DAG (beyond v2 Section 3):

- `DR-15 (WU_API_KEY env)` → `DR-12 (HK observations backfill)` — daily_tick path needs WU env to continue past WU fetch before reaching HKO
- `DR-01 (forecasts schema)` → `R2-A (forecasts_tick.py)` — tick script depends on schema being current
- `DR-28 (pytz→zoneinfo)` → `R2-A (shared _time_utils.py)` — helper must be clean before relocation
- `DR-32 (polymarket_truth_tick)` → `DR-07 (settlement backfill)` — tick script calls backfill
- `DR-10 (relocate time helpers)` → `DR-09 R2-B (antibody)` — must land first to avoid antibody false-fire

---

## Section 6 — Updated Task Zero (v3 concretized per critic P1-5)

Task Zero **must** complete before Phase R0 begins:

1. **Planning-lock machine check**:
   ```bash
   bash scripts/_planning_lock_for_plan_v3.sh
   ```
   This helper script (new, created in Task Zero itself):
   - Parses v3 Section 5 file inventory
   - Invokes `python scripts/topology_doctor.py --planning-lock --changed-files <expanded list> --plan-evidence docs/operations/task_2026-04-23_data_readiness_remediation/plan_v3.md`
   - Exits non-zero if check fails
2. **DR-07 RCA verification** (already done in this plan — documented Section 1 replacement above)
3. **Harvester path audit**: re-read `harvester.py:312-320, 486-503, 528-572`; confirm `continue` guard; no patch needed on caller
4. **Auto-pause log survey**: `ls -la logs/zeus-live.err* logs/zeus-live.log* 2>&1 | head -20`; grep for "ValueError\|RuntimeError" around 2026-04-18T13:18. Document findings even if null result.
5. **Denver/2026-04-15 orphan investigation**: check Polymarket closed-events for Denver 2026-04-15; may reveal a class

Proceed to R0 **only if** steps 1 + 3 pass cleanly. Steps 2, 4, 5 are evidence-gathering (results documented in work_log).

---

## Section 7 — Verification matrix (replacement for v2 AC table)

| AC | Command | Pass condition |
|---|---|---|
| **Task Zero gate** | `bash scripts/_planning_lock_for_plan_v3.sh` | exit 0 |
| **DR-01** AC-R0-1 | `PRAGMA table_info(forecasts)` (superset of declared in `src/state/db.py:653-668`) | all declared columns present |
| **DR-01** AC-R0-1b | `pytest -q tests/test_forecasts_schema_alignment.py` | green |
| **DR-01** AC-R0-1c (new) | `pytest -q tests/test_all_tables_schema_alignment.py` | green |
| **DR-05** AC-R0-2 (v3) | `SELECT COUNT(*) FROM observation_instants_v2 WHERE (running_max > 60 AND temp_unit='C') OR (running_max > 140 AND temp_unit='F')` | == 0 (aligned with CHECK thresholds, not v2's 55/135) |
| **DR-05** AC-R0-2b | `state/quarantine/obs_v2_poisoned_backup_20260423.jsonl` has exactly 3 JSON objects | file present with 3 entries |
| **DR-06** AC-R0-6 | `pytest -q tests/test_obs_v2_dst_gap_hour_absent.py` | green (positive confirmation of system-correct behavior) |
| **DR-07** AC-R3-W | `SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL` | ≤ 1 |
| **DR-07** AC-R3-V | `SELECT COUNT(*) FROM settlements WHERE settlement_value IS NULL` | ≤ 1 |
| **DR-07** AC-R3-U | `SELECT COUNT(*) FROM settlements WHERE unit IS NULL` | == 0 |
| **DR-07** AC-R3-HC | `pytest -q tests/test_harvester_winning_bin_required.py` | green |
| **DR-09** AC-R2-1 | `pytest -q tests/test_ingest_isolation.py` | green |
| **DR-09** AC-R2-2 | `for f in scripts/ingest/*.py; do python $f --dry-run; done` | all exit 0 |
| **DR-09** AC-R2-4 | `ls ~/Library/LaunchAgents/com.zeus.ingest.*.plist \| wc -l` | ≥ 9 (including polymarket_truth) |
| **DR-09** AC-R2-3 (v3 corrected) | `grep -cE "from src.data\.(daily_obs_append\|hourly_instants_append\|solar_append\|forecasts_append\|hole_scanner\|ecmwf_open_data) " src/main.py` | == 0 post-R2-D |
| **DR-11** AC-R2-TEST | `pytest -q tests/test_ingest_lanes_defined.py` | green (replaces R12 tests) |
| **DR-15** AC-ENV | `plutil -p ~/Library/LaunchAgents/com.zeus.live-trading.plist \| grep WU_API_KEY` | non-empty |
| **DR-17** AC-DEDUPE | `SELECT city, target_date, COUNT(*) FROM observations GROUP BY 1,2 HAVING COUNT(*)>1` | 0 rows |
| **DR-17** AC-DISAGREE | `state/quarantine/observations_dupe_disagreements.jsonl` either exists with operator sign-off or is not created (all pairs agreed) | file state documented |
| **DR-31** AC-CHECK | new-schema CHECK constraint present via `SELECT sql FROM sqlite_master WHERE name='settlements'` | contains `CHECK(winning_bin IS NOT NULL OR authority='UNRECOVERABLE')` |
| **DR-32** AC-LANE | `data/pm_settlement_truth.json` mtime | within 12h post-plist-active |
| **DR-04** AC-R4-1 (v3 tightened) | `SELECT COUNT(DISTINCT city) FROM ensemble_snapshots_v2` | ≥ 45 with documented excluded cities |
| **DR-04** AC-R4-1b (new) | `SELECT COUNT(DISTINCT target_date) FROM ensemble_snapshots_v2` | ≥ 60 |
| **DR-04** AC-R4-1c (new) | `SELECT COUNT(*) FROM ensemble_snapshots_v2` | ≥ 50 × 60 × 51 / 10 (accounting for member sparsity) |

---

## Section 8 — Risk pre-mortem (delta from v2)

New risks introduced by v3 changes:

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| V3-S1 | `_build_pm_truth.py` returns different bin data for a settled event on re-run (Gamma data drift) | MED | Silent overwrites of valid rows | Backfill script detects divergence via checksum; logs + skips |
| V3-S2 | DR-31 schema CHECK reveals violations beyond the 1,562 backfill set | LOW | Migration fails | Migration wrapped in transaction; fail-closed |
| V3-S3 | 5 JSON duplicate entries (1,566 vs 1,561 unique) indicate multi-market same-day; backfill may update wrong row | MED | Incorrect winning_bin for 5 rows | Investigate duplicates pre-backfill; match by market_slug not just (city, date) |
| V3-S4 | Relocating `_is_missing_local_hour` + `season_from_date` breaks existing 7+ callers if import not updated atomically | MED | Broken imports | Single commit updates helper + all callers + re-export shim |
| V3-S5 | polymarket_truth_tick at 4x/day causes Gamma rate-limit | LOW | Lane errors | Backoff; reduce to 2x/day if rate-limited |

---

## Section 9 — What v3 does NOT change from v2

- Issue IDs DR-02, DR-03, DR-04, DR-05 (mostly), DR-06, DR-08, DR-12, DR-15, DR-16, DR-18, DR-19, DR-20, DR-21, DR-22, DR-23, DR-24, DR-25, DR-26, DR-27, DR-28, DR-29, DR-30 — unchanged
- Section 5 file inventory — unchanged except adding:
  - `scripts/backfill_settlements_from_pm_truth.py`
  - `scripts/ingest/polymarket_truth_tick.py`
  - `scripts/_planning_lock_for_plan_v3.sh`
  - `tests/test_settlements_winning_bin_present.py`
  - `tests/test_polymarket_truth_json_freshness.py`
  - `tests/test_no_dynamic_imports.py`
  - `tests/test_all_tables_schema_alignment.py`
  - `com.zeus.ingest.polymarket_truth.plist`
- Section 6 risks (v2 S1-S10) — unchanged
- Section 9 scope boundaries — unchanged
- Section 10 receipt binding — unchanged

---

## Section 10 — Summary of v3 corrections over v2

- **20 items changed** (Section 0 delta log)
- **0 P0 items carried from v2 unaddressed**
- **0 P1 items carried from v2 unaddressed**
- **4 new defensive decisions** (DR-31 CHECK, DR-32 lane, forbidden list extension, harvester callee guard)
- **1 retraction** (933 placeholder "bug" → semantic feature)
- **1 complete rewrite** (DR-07 from fabricated narrative to RCA-verified)

## Open questions for v3 reviewers

Q1. The 1,566-1,561=5 JSON duplicate entries — how to resolve during backfill? Match by market_slug where available, skip and log otherwise?

Q2. Denver/2026-04-15 DB orphan — same-day orphan handling?

Q3. `provenance_metadata` / `provenance_json` convention for UNRECOVERABLE rows — new schema column or reuse existing?

Q4. DR-32 cadence (4x/day vs daily vs hourly) — what's Polymarket API sustainable?

Q5. DR-31 CHECK migration — SQLite best practice for adding CHECK post-hoc (table recreate vs PRAGMA)?

Q6. Re-export shim for `_is_missing_local_hour` / `season_from_date` — 30-day deprecation adequate?

Q7. `tests/test_all_tables_schema_alignment.py` scope — all 52 tables or only write-critical subset?
