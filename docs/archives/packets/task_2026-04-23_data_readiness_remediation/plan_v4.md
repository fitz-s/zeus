# Data Readiness Remediation — Master Plan v4

**Status**: DRAFT v4 — supersedes plan_v3.md. Awaiting planning-lock receipt + operator approval.
**Created**: 2026-04-23
**Authority basis**: AGENTS.md task_boot_profiles `settlement_semantics` + `hourly_observation_ingest` + `calibration` + `docs_authority` profiles; architecture/invariants.yaml INV-14 / INV-17; architecture/fatal_misreads.yaml; architecture/city_truth_contract.yaml; architecture/source_rationale.yaml; docs/authority/zeus_current_architecture.md
**Supersedes**: v3. v2 still stands for Section 1 (env snapshot), Section 4 (antibody suite base), Section 5 (file inventory), Section 6 (risks), Section 9 (scope).

**Stakes restated**: live real-money trading. Data-semantics bugs are invisible to 1M code reviews. v3 contained a P0 architectural inversion (INV-17 violation) that neither architect nor critic flagged. v4 restores the correct authority direction.

---

## Section 0 — The v3→v4 structural corrections

v3 committed five category errors beyond its inventory of 20 surface fixes:

| # | v3 position | v4 correction | Authority cited |
|---|---|---|---|
| **C1** | "JSON is canonical; DB is projection" (Section 2, DR-07 step 1 + DR-32) | **INVERTED**. DB is canonical. `data/pm_settlement_truth.json` is derived external-verification evidence only. | `architecture/invariants.yaml` **INV-17** ("DB authority writes must COMMIT before any derived JSON export is updated") + `docs/authority/zeus_current_architecture.md` §4.6 + §5 truth-ownership matrix |
| **C2** | Backfill `SET settlement_value = pm_high` | **REJECTED**. `pm_high` for F-city is the JSON's fabricated bin midpoint (NYC 2025-12-30: pm_high=32.5, midpoint of 32–33 — not a measurement). Using it hard-codes 0.5°F systematic bias into calibration. | `src/contracts/settlement_semantics.py:101` MANDATORY gate + JSON-inspection evidence (Section 2.3) |
| **C3** | DR-32 promotes `scripts/_build_pm_truth.py` as "truth source" tick for DB | **REJECTED**. `_build_pm_truth.py:185` writes `data/pm_settlements_full.json` (SCHEMA A: `pm_exact_value`, 631/1567 NULL). DR-07 reads `data/pm_settlement_truth.json` (SCHEMA B: `pm_high`, 0/1566 NULL). Two different files. No producer currently exists for the file DR-07 reads. v4 reclassifies the entire lane as verification, not projection. | Architect EF-1 + `ls -la data/pm_settlement*.json` + JSON schema diff (Section 2.3) |
| **C4** | settlements CHECK `authority='UNRECOVERABLE'` | **REJECTED** as written. Current enum `CHECK (authority IN ('VERIFIED','UNVERIFIED','QUARANTINED'))` at `src/state/db.py:167` rejects `UNRECOVERABLE`. v4 uses existing `QUARANTINED` rather than enum expansion (QUARANTINED already means "known-bad, excluded from calibration" per architecture). | `src/state/db.py:167` direct read |
| **C5** | Plan did not address INV-14 precondition violation | **ADDED**. Current `settlements` schema lacks `temperature_metric`, `physical_quantity`, `observation_field`, `data_version` — 4 identity fields INV-14 requires for every temperature-market row. Backfilling 1,562 rows without fixing this first hardens a fundamentally incomplete schema into place. DR-34 adds the columns. | `architecture/invariants.yaml` **INV-14** + `docs/authority/zeus_current_architecture.md` §7.1 Metric Identity Spine + PRAGMA table_info(settlements) direct read |

Plus 9 surface errors confirmed by architect (EF-1/EF-2/EF-3/EF-4/EF-5/EF-6) and critic (E1/E2/E3/E6/E7/E8/E9/P1-3/P1-4/P1-5), each addressed below.

---

## Section 1 — Semantic boot receipt per AGENTS.md

Task classes triggered (per `architecture/task_boot_profiles.yaml`):

1. **settlement_semantics** — harvester writes, rounding, bin labels
2. **hourly_observation_ingest** — backfill source binding, obs_v2 provenance
3. **calibration** — calibration_pairs_v2=0 consequence; training source identity
4. **docs_authority** — authority/context separation, packet receipt
5. **graph_review** — Stage 2 only; no graph-as-truth

### Required proofs answered

| Profile | Proof ID | Answer anchor |
|---|---|---|
| settlement_semantics | `settlement_value_path` | Settlement value = `SettlementSemantics.for_city(city).assert_settlement_value(observations.high_temp)` (src/contracts/settlement_semantics.py:95-118). Rounding law per-city from `city.settlement_source_type`. |
| settlement_semantics | `dual_track_separation` | HIGH only; all 1,562 current rows are `observation_field='high_temp'`. LOW track out of scope for this packet (noted in Section 9). |
| hourly_observation_ingest | `hourly_source_and_extrema` | Not needed for settlement backfill (daily obs path). Relevant to DR-05/DR-06 which v4 inherits from v3. |
| hourly_observation_ingest | `writer_provenance_gate` | DR-34 adds `provenance_json` column to `settlements`. Writers (harvester, new backfill script) must populate it with non-default authority + data_version + provenance blob. |
| calibration | `training_source_identity` | Calibration consumes `settlements.winning_bin` × `forecasts` rows. v4 guarantees `winning_bin` is catalog-aligned string; forecasts backfill remains DR-03/DR-04 (inherited from v2/v3). |
| calibration | `strategy_key_and_dual_track` | Single HIGH track only. `strategy_key` not altered. |
| docs_authority | `authority_context_history_layering` | v4 plan classifies JSON as derived evidence (context), DB as authority. Updates `docs/operations/current_data_state.md` invalidation list on completion. |
| graph_review | `semantic_boot_before_graph` | Done. Graph used only for caller enumeration (subagent B) post-boot. |

### Fatal misreads checked

- ✅ `airport_station_not_city_settlement_station` — Tel Aviv WU→NOAA switch at 2026-03-23 respected; HK→HKO source binding respected (Section 2.4)
- ✅ `daily_day0_hourly_forecast_sources_are_not_interchangeable` — settlement backfill draws from settlement-daily-source observations, not day0 or hourly
- ✅ `wu_website_daily_summary_not_wu_api_hourly_max` — `observations.wu_icao_history` is our internal WU product; Polymarket JSON's `resolution_source` field is a WU website URL; v4 treats these as potentially divergent, reconciliation logs any settlement_value vs JSON pm_high mismatches ≥ 1°F
- ✅ `hong_kong_hko_explicit_caution_path` — HK obs stalled after 2026-03-31; v4 quarantines 15 HK settlements and blocks re-activation without fresh HKO audit receipt
- ✅ `hourly_downsample_preserves_extrema` — N/A (daily backfill)
- ✅ `code_review_graph_answers_where_not_what_settles` — graph used for blast-radius only
- ✅ `api_returns_data_not_settlement_correct_source` — observations.source != settlement_source always reconciled against `architecture/city_truth_contract.yaml` role schema

### Planning lock

Per AGENTS.md §Planning lock, this task hits:
- `src/state/**` schema change (DR-31, DR-34 migrations)
- Cross-zone: K0 contracts (new `_time_utils.py`), K2 runtime (harvester patch, scaffold writer change), K2 data (backfill script), K3 signal (diurnal helper removal)
- >4 changed files (inventory Section 5)
- Canonical truth / lifecycle authority scope

Planning-lock check command (Section 6 Task Zero) is mandatory and blocking.

---

## Section 2 — DR-07 correctly architected (v4)

### 2.1 Goal

Restore `settlements` to a state where every row carries a settlement_value traceable to an observation through SettlementSemantics, and a winning_bin that is canonical-catalog-aligned. Backfill the 1,562 bulk-written rows and guarantee future writers cannot NULL-populate these fields.

### 2.2 Correct data-flow direction (INV-17 compliant)

```
observations (source-bound per city_truth_contract)
        │
        │ observations.high_temp for settlement_daily_source family
        ▼
SettlementSemantics.for_city(city).assert_settlement_value(high_temp, context=…)
        │
        │ rounded value, contract-specific (WU WMO half-up | HKO oracle_truncate | …)
        ▼
settlements.settlement_value  ←── CANONICAL (DB is authority)
        │
        ├─► winning_bin derived:
        │     use pm_bin_lo/pm_bin_hi from Polymarket market description (bin shape)
        │     construct label via canonical-bin-format rule (Section 2.6)
        │     verify settlement_value falls inside [pm_bin_lo, pm_bin_hi] with shoulder semantics
        │     on mismatch → row authority='QUARANTINED' + divergence log
        │
        └─► provenance_json populated with {obs_source, obs_id, rounding_rule, data_version}

derived verification (post-commit only, per INV-17):
  data/pm_settlement_truth.json is compared against settlements row; reconciliation
  script emits divergence_log but never overwrites the DB. JSON is EVIDENCE, not source.
```

### 2.3 Why `pm_high` is not a settlement source

Direct JSON inspection evidence (run 2026-04-23):

```
F-city interior bins — pm_high equals the midpoint:
  NYC      2025-12-30  lo=32.0 hi=33.0 pm_high=32.5  midpoint=32.5  is_mid=True
  Dallas   2025-12-30  lo=52.0 hi=53.0 pm_high=52.5  midpoint=52.5  is_mid=True
  Atlanta  2025-12-30  lo=42.0 hi=43.0 pm_high=42.5  midpoint=42.5  is_mid=True
  [... all F-city interior rows tested: is_midpoint=True for 100%]

C-city point bins — pm_high IS the value:
  London      2025-12-31  lo=5.0  hi=5.0  pm_high=5.0
  Buenos Aires 2026-01-01 lo=31.0 hi=31.0 pm_high=31.0
```

Corroborating DB evidence: `NYC/2025-12-30` observation `wu_icao_history.high_temp = 33.0°F`. Polymarket JSON bin `32-33` with pm_high=32.5. **True settlement_value for WMO half-up is 33 (from observation), not 32.5 (fabricated midpoint)**. v3's SET `settlement_value=pm_high` would have injected 32.5 into the DB — 0.5°F systematic bias into every F-city calibration pair.

This is architecture/fatal_misreads.yaml `wu_website_daily_summary_not_wu_api_hourly_max` in a specific form: the JSON was assembled by reading WU website summary and computing a bin, but the bin midpoint is not a measured temperature.

### 2.4 Backfill source binding per city (architecture/city_truth_contract.yaml compliance)

Verified via `SELECT settlement_source_type, source FROM settlements JOIN observations`:

| settlement_source_type | Cities | Obs source for backfill | Rounding law | Coverage |
|---|---|---|---|---|
| WU (1,459 rows) | NYC, Atlanta, Dallas, Seoul, Toronto, London, Chicago, Miami, Paris, Seattle, Buenos Aires, Sao Paulo, Wellington, Ankara, Lucknow, Munich, Tokyo, Shanghai, Singapore, Taipei, etc. | `observations.source='wu_icao_history'` | `SettlementSemantics.default_wu_fahrenheit` / `default_wu_celsius` — WMO half-up | 1,459 rows all have WU obs; direct |
| NOAA (67 rows) | Istanbul, Moscow, Tel Aviv (2026-03-23+) | `observations.source LIKE 'ogimet_metar_%'` (ltfm / uuww / llbg) | `SettlementSemantics` NOAA default — WMO half-up | 67 rows, obs-covered |
| HKO (29 rows) | Hong Kong | `observations.source='hko_daily_api'` | `SettlementSemantics.for_city(hk)` — **oracle_truncate** | 14 rows obs-covered (through 2026-03-31); **15 rows 2026-04-01→15 NOT obs-covered** |
| CWA (7 rows) | Taipei (2026-03-16..22 only) | No `observations.source='cwa_*'` table rows; Taipei has `wu_icao_history` obs only | Ambiguous — CWA settlement but only WU obs in DB | Needs evidence-level decision (see 2.8) |

**Key**: `settlement_source_type` is the binding field, not any inferred station code. Tel Aviv's row-level source switch at 2026-03-23 (WU→NOAA) IS captured in DB already and must be respected row-by-row. This closes fatal_misread `airport_station_not_city_settlement_station`.

### 2.5 Handling for rows with no obs coverage

| Category | Count | v4 disposition | Re-activation condition |
|---|---|---|---|
| HK 2026-04-01..15 (HKO stalled) | 15 | `authority='QUARANTINED'`; `provenance_json={"reason":"HKO_INGEST_STALLED","evidence":"docs/operations/current_source_validity.md hko suspect line"}`; `settlement_value=NULL`; `winning_bin=NULL` | Fresh HKO audit receipt + hko_daily_api rows present for 2026-04-01..15 → re-run backfill for HK slice only |
| CWA Taipei 2026-03-16..22 | 7 | `authority='QUARANTINED'`; `provenance_json={"reason":"CWA_SOURCE_NOT_IN_OBS_TABLE","evidence":"observations.source distinct-value enumeration"}` | Decision packet on CWA ingest (new scope); alternative: explicit operator sign-off to use WU obs as settlement-correct for Taipei on this date range |
| Denver/2026-04-15 (v3 orphan) | 0 | Not present; previous "orphan" claim was scoped to JSON, not DB. Closes v3 Q2. | — |

Total quarantined: 22 of 1,562. Remaining 1,540 re-derivable from observations+SettlementSemantics.

### 2.6 Canonical winning_bin label algorithm (byte-exact with `src/contracts/calibration_bins.py`)

Target format (verified against `src/contracts/calibration_bins.py:156/165/172/200/207/213`):

```
F interior range (2°F odd- or even-start pair)   →  f"{int(lo)}-{int(hi)}°F"       e.g., "32-33°F"
F shoulder low                                   →  f"{int(hi_sentinel)}°F or below"  e.g., "41°F or below"
F shoulder high                                  →  f"{int(lo_sentinel)}°F or higher" e.g., "141°F or higher"
C interior point (1°C single integer)            →  f"{int(v)}°C"                    e.g., "15°C"
C shoulder low                                   →  f"{int(hi_sentinel)}°C or below"  e.g., "-40°C or below"
C shoulder high                                  →  f"{int(lo_sentinel)}°C or higher" e.g., "61°C or higher"
```

Sentinel recognition from JSON (`data/pm_settlement_truth.json` shape):
- `pm_bin_lo == -999` → shoulder-low; boundary = `int(pm_bin_hi)`; label = `f"{int(pm_bin_hi)}°{unit} or below"`
- `pm_bin_hi == 999` → shoulder-high; boundary = `int(pm_bin_lo)`; label = `f"{int(pm_bin_lo)}°{unit} or higher"`
- `pm_bin_lo == pm_bin_hi` + `unit == 'C'` → point bin; label = `f"{int(pm_bin_lo)}°C"`
- `pm_bin_lo < pm_bin_hi` + `unit == 'F'` → range bin; label = `f"{int(pm_bin_lo)}-{int(pm_bin_hi)}°F"`

`°` is U+00B0 (byte sequence `\xc2\xb0` UTF-8). Parser at `src/data/market_scanner.py:628-648` accepts both `°F` and `F` but canonical writes must use `°`.

Reference implementation (lives in `src/contracts/_time_utils.py` alongside unit helpers — see DR-10 target decision):

```python
# src/contracts/bin_labels.py (NEW — sibling of calibration_bins.py)
from typing import Literal

def canonical_bin_label(pm_bin_lo: float, pm_bin_hi: float, unit: Literal["F","C"]) -> str:
    """Construct canonical bin label matching src/contracts/calibration_bins.py
    output format exactly. Raises ValueError on inputs that do not describe a
    canonical bin (cross-unit midpoints, None, NaN, shoulder/unit mismatch)."""
    if unit not in ("F", "C"):
        raise ValueError(f"unit must be F or C, got {unit!r}")
    import math
    for v in (pm_bin_lo, pm_bin_hi):
        if v is None or math.isnan(v) or math.isinf(v):
            raise ValueError(f"bin bounds must be finite; got lo={pm_bin_lo}, hi={pm_bin_hi}")
    # Shoulder detection (Polymarket sentinel contract: -999 / +999)
    if pm_bin_lo <= -900:
        return f"{int(pm_bin_hi)}°{unit} or below"
    if pm_bin_hi >= 900:
        return f"{int(pm_bin_lo)}°{unit} or higher"
    if unit == "C":
        if pm_bin_lo != pm_bin_hi:
            raise ValueError(f"C bins are 1°C point bins; got lo={pm_bin_lo}, hi={pm_bin_hi}")
        return f"{int(pm_bin_lo)}°C"
    # F interior: 2°F-wide range
    if int(pm_bin_hi) - int(pm_bin_lo) != 1:
        raise ValueError(f"F interior must be 2°F-wide pair; got lo={pm_bin_lo}, hi={pm_bin_hi}")
    return f"{int(pm_bin_lo)}-{int(pm_bin_hi)}°F"
```

Golden tests (`tests/test_canonical_bin_label.py`):

```python
assert canonical_bin_label(32, 33, "F") == "32-33°F"
assert canonical_bin_label(-999, 41, "F") == "41°F or below"
assert canonical_bin_label(34, 999, "F") == "34°F or higher"
assert canonical_bin_label(15, 15, "C") == "15°C"
assert canonical_bin_label(-999, 40, "C") == "40°C or below"
assert canonical_bin_label(-1, 999, "C") == "-1°C or higher"
# Negative boundaries preserved
assert canonical_bin_label(-38, -37, "F") == "-38--37°F"  # matches calibration_bins.py line 165 literal output
# Refusals
import pytest
with pytest.raises(ValueError): canonical_bin_label(32, 34, "F")    # 3°F wide, not canonical
with pytest.raises(ValueError): canonical_bin_label(10, 12, "C")    # C must be point
with pytest.raises(ValueError): canonical_bin_label(float("nan"), 5, "C")
```

**Note on "-38--37°F"**: calibration_bins.py:165 emits the literal f-string for negatives and produces that exact double-minus form. v4 matches it byte-exact rather than pretty-format, so round-trip parse in `src/data/market_scanner.py:_parse_temp_range` succeeds. A prettification pass is a separate refactor (noted but not in scope).

### 2.7 Bulk-writer quarantine protocol (subagent A recommendation)

The 2026-04-16T12:39:58.026729+00:00 bulk insert (1,562 rows) is **unregistered** — not produced by any currently-known writer in `architecture/source_rationale.yaml::write_routes`. Registered settlement writer is `src/execution/harvester.py::_write_settlement_truth`; that path calls `conn.commit()` per row, cannot produce a single-microsecond settled_at for 1,562 rows. Subagent A forensic search (git log --all --diff-filter=D, git log -S content, .omc session scan, state/ scan) did not identify the writer in git. Most likely cause: interactive Python REPL or untracked one-shot script.

v3 marked these rows `authority='VERIFIED'` and proposed surgical UPDATE. v4 rejects that: an unregistered writer whose provenance we cannot prove MUST NOT stamp VERIFIED authority per INV-10 ("LLM output is never authority"; operating principle: unregistered path is not authority-grade).

**v4 protocol**:

1. Step 1 (pre-migration): mark all 1,562 rows `authority='QUARANTINED'` with `provenance_json = {"reason":"unregistered_bulk_writer_2026-04-16T12:39:58Z","investigation":"docs/operations/task_2026-04-23_data_readiness_remediation/bulk_writer_rca_v4.md"}`. This takes the rows out of calibration without deleting evidence.

2. Step 2 (backfill): insert re-derived rows via `scripts/backfill_settlements_from_observations.py` (new). Writer path: obs → SettlementSemantics → winning_bin construct → INSERT … `authority='VERIFIED'` + proper provenance_json. Re-derivation is UPDATE-by-id on existing rows (preserving the (city,target_date) UNIQUE constraint) but every field except id/city/target_date is overwritten atomically within a single transaction per row.

3. Step 3 (verification): `scripts/reconcile_settlements_vs_pm_truth.py` compares each re-derived row vs JSON (pm_bin_lo/hi, pm_high-as-midpoint-or-point). Divergences logged to `state/quarantine/settlement_reconciliation_log.jsonl`; rows with irreconcilable divergence re-marked QUARANTINED.

4. Step 4 (rollback): snapshot of pre-migration state at `state/quarantine/settlements_pre_v4_20260423.jsonl` (captures every column per row).

### 2.8 CWA ambiguity decision

7 Taipei 2026-03-16..22 rows have `settlement_source_type='CWA'` + `settlement_source = https://www.cwa.gov.tw/V8/C/W/OBS_Station.html?ID=46692` — Taiwan Central Weather Administration URL. But `observations` has no row with `source LIKE 'cwa%'`; only `wu_icao_history` covers Taipei.

Options:
- (a) **QUARANTINE pending CWA ingest packet** — correct per fatal_misread `api_returns_data_not_settlement_correct_source`; must not assume WU obs equals CWA settlement value.
- (b) Document as authority-evidence violation that user must fix; backfill skipped.

v4 chooses (a). Add DR-36 ("CWA ingest lane") as out-of-scope new packet reference. For now: 7 rows are QUARANTINED with explicit `provenance_json.reason = "CWA_OBS_SOURCE_MISSING"`.

### 2.9 Unified DR-07 v4 issue card

- **ID**: DR-07 (v4 REWRITE — full replacement of v3 rewrite)
- **Severity**: 🔴 P0
- **Authority**: INV-17 (DB before JSON), INV-14 (identity spine), SettlementSemantics mandatory gate
- **Detection method**:
  - `[VERIFIED sql]` `SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL` = 1,562 (100%)
  - `[VERIFIED sql]` `SELECT COUNT(*) FROM settlements WHERE settlement_value IS NULL` = 629
  - `[VERIFIED sql]` `SELECT DISTINCT settled_at FROM settlements` = 1 value
  - `[VERIFIED sql]` `SELECT COUNT(*) FROM settlements WHERE winning_bin IS NOT NULL` = 0 → harvester has NEVER successfully written a settlement
  - `[VERIFIED code]` `src/execution/harvester.py:313-314` `continue` on None, confirming callee-guard would not have been tripped
  - `[VERIFIED code]` `src/execution/harvester.py:506-514` `_format_range` produces wrong sentinel-format `-999-40` not canonical (DR-33 below)
- **Key defect**: no working canonical settlement writer. 1562 rows exist with unregistered provenance. All winning_bin fields NULL. All calibration downstream blocked.
- **Solution (v4 structural)**: six structural decisions collected under DR-07:
  1. DR-34 adds INV-14 identity fields to settlements schema
  2. DR-07a adds `provenance_json` column
  3. DR-33 fixes harvester canonical label output
  4. DR-07b quarantines bulk rows then re-derives from observations
  5. DR-31 adds CHECK constraint guaranteeing future writes carry winning_bin + identity
  6. DR-32 (redefined) establishes reconciliation lane, not projection lane
- **Rollback**: restore from `state/quarantine/settlements_pre_v4_20260423.jsonl`; revert schema migration; revert harvester patch. Backup captures every pre-migration column per row.
- **Verification (acceptance)**: see Section 8 matrix.

---

## Section 3 — New and redefined issue cards

### DR-31 (v4) — Schema CHECK: winning_bin + identity present when authority=VERIFIED

- **Severity**: 🟠 P1 structural antibody
- **Fix**: after DR-07b backfill, migrate `settlements` with CHECK:
  ```
  CHECK (
    authority IN ('QUARANTINED','UNVERIFIED')
    OR (winning_bin IS NOT NULL AND settlement_value IS NOT NULL
        AND temperature_metric IS NOT NULL AND physical_quantity IS NOT NULL
        AND observation_field IS NOT NULL AND data_version IS NOT NULL)
  )
  ```
- **Migration strategy**: SQLite 3.25+ supports `ALTER TABLE … RENAME`. Migration script creates `settlements_new`, copies, drops, renames. Runs inside a single transaction with PRAGMA foreign_keys pause/resume.
- **Antibody test**: `tests/test_settlements_invariant.py` — every VERIFIED row has all fields populated; QUARANTINED rows allowed to have NULLs with reason in provenance_json.

### DR-32 (v4 REDEFINED) — Polymarket reconciliation lane (NOT projection lane)

- **Severity**: 🟡 P2 (downgraded from v3's P1 — verification, not critical path)
- **Removed**: "JSON → DB projection tick" + "JSON is canonical truth source"
- **New scope**: `scripts/reconcile_settlements_vs_pm_truth.py` (NEW)
  - Reads DB settlements (authority=VERIFIED)
  - Reads `data/pm_settlement_truth.json`
  - For each matching (city,target_date): compute bin containment (settlement_value ∈ [pm_bin_lo, pm_bin_hi] respecting shoulders) and canonical-label equality
  - Divergences → `state/reconciliation/pm_truth_divergence.jsonl`
  - Exit code non-zero if divergence count > threshold (configurable, default: `len(known_duplicates) = 5`)
- **Schedule**: launchd daily at 04:15 UTC (post-daily-market-close). Divergences trigger operator alert via existing Discord pipeline.
- **NOT in scope**: re-scraping `_build_pm_truth.py` cadence. Gamma API scraping remains manual until a separate packet evaluates cadence.

### DR-33 (NEW) — Harvester canonical bin label format

- **Severity**: 🟠 P1 — prevents future regressions
- **Detection**: `src/execution/harvester.py:506-514 _format_range` returns strings like `"-999-40"` or `"32-33"` (missing `°F`/`°C`). `_find_winning_bin` at line 498 returns raw Polymarket `question` field which has varying format.
- **Fix**:
  - Delete `_format_range`
  - Replace call site with `canonical_bin_label` from `src/contracts/bin_labels.py` (new from Section 2.6)
  - When writing, harvester must have known `pm_bin_lo`, `pm_bin_hi`, and `unit` in scope. If harvester only has Polymarket question text, parse via `src/data/market_scanner._parse_temp_range` (existing), then canonicalize. Refuse (raise) on parse failure rather than write wrong-format string.
- **Rollback**: restore `_format_range` definition.
- **Tests**: `tests/test_harvester_canonical_label.py` — mock Polymarket event with range-bin question + shoulder-bin question + point-bin question; verify harvester writes canonical format.

### DR-34 (NEW) — INV-14 identity spine on settlements

- **Severity**: 🔴 P0 — INV-14 compliance precondition for DR-07 backfill
- **Detection**: `PRAGMA table_info(settlements)` — columns are [id, city, target_date, market_slug, winning_bin, settlement_value, settlement_source, settled_at, authority, pm_bin_lo, pm_bin_hi, unit, settlement_source_type]. INV-14 requires `temperature_metric`, `physical_quantity`, `observation_field`, `data_version`.
- **Fix**: migration `migrations/2026_04_24_settlements_identity_spine.sql`:
  ```sql
  ALTER TABLE settlements ADD COLUMN temperature_metric TEXT
    CHECK (temperature_metric IN ('high','low'));
  ALTER TABLE settlements ADD COLUMN physical_quantity TEXT;
  ALTER TABLE settlements ADD COLUMN observation_field TEXT
    CHECK (observation_field IN ('high_temp','low_temp'));
  ALTER TABLE settlements ADD COLUMN data_version TEXT;
  ALTER TABLE settlements ADD COLUMN provenance_json TEXT;
  ```
- **Backfill for existing 1,562 rows**:
  - All current rows are daily-high contracts → `temperature_metric='high'`, `physical_quantity='mx2t6_local_calendar_day_max'`, `observation_field='high_temp'`, `data_version='v1.wu-native'` (matches obs corpus per plan v2 §1.6)
  - `provenance_json` populated row-by-row during DR-07b backfill (includes obs_source, obs_id, rounding_rule)
- **Tests**: `tests/test_settlements_identity_spine.py` — every row has 4 identity fields populated with valid enum values.

### DR-35 (NEW) — onboard_cities.py:383 scaffold writer guard

- **Severity**: 🟡 P2
- **Detection**: critic P1-5; `scripts/onboard_cities.py:383` has `INSERT OR IGNORE INTO settlements (city, target_date)` — inserts scaffold rows with everything else NULL. Pre-DR-31 this was harmless-ish; post-DR-31 the CHECK constraint blocks these inserts.
- **Fix options**:
  - (a) Remove scaffold behavior; onboarding no longer pre-populates settlements
  - (b) Scaffold with `authority='UNVERIFIED'` + minimal identity fields
- v4 choice: **(a)**. Rationale: settlements are supposed to be written by harvester at settlement time, not pre-populated. The scaffold made sense only in a (hypothetical) prior architecture; it doesn't survive INV-17.
- **Tests**: existing onboard test regression check.

### DR-09 R2-B (v4) — forbidden list

Same as v3 (extend to include `src.calibration`), plus:
- Remove redundant `src.data.polymarket_client` exemption language (it's trading-intrinsic; make it an explicit whitelist with `src.data.proxy_health`)
- Antibody test uses **Python AST walker**, not grep (fixes critic E1 "regex doesn't actually match")

### DR-09 R2-D (v4) — ingest isolation acceptance, regex fixed

v3 AC:
```
grep -cE "from src.data\.(daily_obs_append\|hourly_instants_append\|solar_append\|forecasts_append\|hole_scanner\|ecmwf_open_data) " src/main.py == 0
```
Verified broken: escaped `\|` in `-E` context is literal pipe, regex returns 0 trivially even when imports exist.

v4 AC uses Python AST + unescaped grep sanity:
```python
# tests/test_r2d_main_not_coupled_to_ingest_writers.py
import ast
FORBIDDEN_DATA_MODULES = {
    "src.data.daily_obs_append",
    "src.data.hourly_instants_append",
    "src.data.solar_append",
    "src.data.forecasts_append",
    "src.data.hole_scanner",
    "src.data.ecmwf_open_data",
}
def test_main_not_coupled_to_ingest_writers():
    src = open("src/main.py").read()
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in FORBIDDEN_DATA_MODULES:
                found.append(f"{mod} (line {node.lineno})")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_DATA_MODULES:
                    found.append(f"{alias.name} (line {node.lineno})")
    assert not found, f"main.py must not import ingest writers: {found}"
```

Regression also via sanity grep (unescaped pipes, Python re):
```bash
python -c "
import re
s = open('src/main.py').read()
pat = re.compile(r'from src\.data\.(daily_obs_append|hourly_instants_append|solar_append|forecasts_append|hole_scanner|ecmwf_open_data)\s')
print('hits:', len(pat.findall(s)))
" # must print 0
```

### DR-10 (v4) — helper relocation target

v3 target: `src/data/_time_utils.py`. Critic + subagent B both flag this inverts dependency direction if taken strictly — but per `architecture/zones.yaml`, K2 → K3 is already allowed (BI-01..BI-05 do not forbid it) and current code already does it (harvester K2 imports season_from_date from src/calibration K3 today).

However: for `season_from_date`/`season_from_month`/`_is_missing_local_hour` specifically, the clearest home is **K0 (src/contracts)** because:
- All callers (K2 runtime + K3 math + K2 data) can import K0 with zero forbidden-import concerns
- Time / season / DST helpers are semantic atoms, aligned with K0's charter
- Existing K0 module `src/contracts/settlement_semantics.py` already imports numpy and does time-adjacent work; a sibling `_time_utils.py` + `bin_labels.py` fits the K0 pattern

v4 target: **`src/contracts/_time_utils.py`** + **`src/contracts/bin_labels.py`** (new, Section 2.6).

Caller list (subagent B verified — **24 total direct + test callers**, authoritative list):

| Helper | Prod callers | Test callers |
|---|---|---|
| `season_from_date` | src/calibration/manager.py:85,159,354; src/execution/harvester.py:19,825; src/data/observation_client.py:436,437; src/data/daily_obs_append.py:69,701; src/engine/monitor_refresh.py:13,178,372,464,465; src/signal/ensemble_signal.py:346,350; src/engine/evaluator.py:23,993,1257 | tests/test_calibration_manager.py:20,85; tests/test_pnl_flow_and_audit.py:27,3104; tests/test_cities_config_authoritative.py:19,114-141 (6 sites) |
| `season_from_month` | src/signal/diurnal.py:13,129,226; src/engine/replay.py:1186,1216 | tests/test_cities_config_authoritative.py:19,146-148 |
| `_is_missing_local_hour` | src/signal/diurnal.py:19,340,415; src/data/ingestion_guard.py:475,476 (function-scoped); src/data/daily_obs_append.py:72,686; src/data/hourly_instants_append.py:50,135 | tests/test_cities_config_authoritative.py:177-188 (4 sites); tests/test_observation_atom.py:269,274 |

Migration:
1. Create `src/contracts/_time_utils.py` containing all three helpers
2. Add `from src.contracts._time_utils import season_from_date, season_from_month, _is_missing_local_hour` to bottom of `src/calibration/manager.py` and `src/signal/diurnal.py` as re-export shims for 30-day deprecation
3. Update all callers (by file, atomically)
4. Delete old definitions in same commit
5. Post-migration sweep: re-run tests; verify re-export shims still work for any straggler

30-day removal planned at `docs/operations/task_2026-05-23_drop_time_utils_shim.md` (placeholder).

### DR-11 (v4 unchanged from v3)

Test names remain `test_R12_main_py_defines_all_k2_functions` (line 790) and `test_R12_main_py_references_k2_job_ids` (line 813). Implementation rewrites both to assert new `scripts/ingest/*` package structure + yaml manifest + launchd plist presence.

### DR-12, DR-13, DR-14, DR-15, DR-16, DR-17 (v4 inherit v3 with strengthened ACs)

Inherit per v3 text. Notable strengthening:
- DR-17 dedupe: value-compare step retained; quarantine log path is `state/reconciliation/observations_dupe_disagreements.jsonl` (consistent with Section 3 DR-32 naming)
- DR-14 AC: logs path confirmed `logs/zeus-live.err*` via launchd plist stderr-redirect; tombstone doc path updated per Section 5.

### DR-01 through DR-06, DR-08, DR-18 through DR-30 (inherit v2/v3)

No v4-specific changes. Inherit as-is.

---

## Section 4 — Retracted claims from v3

1. **933 C-city `lo=hi=value` as bug** — RETRACTED in v3 already. v4 inherits: it's canonical Polymarket C-city point-bin semantics. Fully agreed with v3.

2. **v3's DR-07 backfill uses pm_high** — RETRACTED. See Section 2.3.

3. **v3's DR-32 as projection lane** — RETRACTED. See DR-32 (v4).

4. **authority='UNRECOVERABLE'** — RETRACTED. Use existing `QUARANTINED`.

5. **v3 Section 6 Task Zero step 3 "harvester path audit — no patch needed on caller"** — PARTIALLY RETRACTED. Caller (`_find_winning_bin`) keeps `continue` guard; callee (`_write_settlement_truth`) gets defensive assert AND `_format_range` is removed (DR-33).

6. **v3 DR-18 ambiguity about `status_summary.risk_level`** — REVERTED. Current state shows `risk.level="DATA_DEGRADED"` (stable); v2 language was correct; v3's "currently null" language was transient snapshot anomaly. v4 text: `risk_level=DATA_DEGRADED` as the stable anchor.

---

## Section 5 — File inventory (v4 delta from v2/v3)

New files:
- `src/contracts/_time_utils.py` (DR-10 target)
- `src/contracts/bin_labels.py` (DR-33 helper)
- `scripts/backfill_settlements_from_observations.py` (DR-07b)
- `scripts/reconcile_settlements_vs_pm_truth.py` (DR-32 v4)
- `migrations/2026_04_24_settlements_identity_spine.sql` (DR-34)
- `migrations/2026_04_24_settlements_check_constraint.sql` (DR-31, follows DR-34)
- `tests/test_canonical_bin_label.py`
- `tests/test_harvester_canonical_label.py`
- `tests/test_settlements_identity_spine.py`
- `tests/test_settlements_invariant.py`
- `tests/test_r2d_main_not_coupled_to_ingest_writers.py`
- `tests/test_pm_truth_reconciliation.py`
- `~/Library/LaunchAgents/com.zeus.reconcile.pm_truth.plist` (DR-32 v4 schedule)
- `docs/operations/task_2026-04-23_data_readiness_remediation/bulk_writer_rca_v4.md` (evidence)
- `state/quarantine/settlements_pre_v4_20260423.jsonl` (backup)
- `state/reconciliation/pm_truth_divergence.jsonl` (ongoing)

Modified files:
- `src/execution/harvester.py` (DR-33: remove _format_range, callee guard)
- `src/state/db.py` (DR-34: new schema; DR-31: new CHECK constraint in CREATE)
- `scripts/onboard_cities.py` (DR-35: remove scaffold_settlements step)
- `src/calibration/manager.py` (DR-10: re-export shim)
- `src/signal/diurnal.py` (DR-10: re-export shim)
- 24 callers across src/ + tests/ (DR-10 import updates)

Removed (v3 files that do NOT land in v4):
- `scripts/backfill_settlements_from_pm_truth.py` — v3 DR-07 script; replaced by observations-based backfill
- `scripts/ingest/polymarket_truth_tick.py` — v3 DR-32 script; replaced by reconciliation script
- `tests/test_polymarket_truth_json_freshness.py` — implied freshness is moot under v4; JSON is evidence not truth

---

## Section 6 — Task Zero (must complete before Phase R0)

Run in order, blocking:

1. **Planning-lock machine check** (REQUIRED per AGENTS.md §Planning lock):
   ```bash
   python scripts/topology_doctor.py --planning-lock \
     --changed-files \
       src/contracts/_time_utils.py src/contracts/bin_labels.py \
       src/execution/harvester.py src/state/db.py \
       scripts/backfill_settlements_from_observations.py \
       scripts/reconcile_settlements_vs_pm_truth.py \
       scripts/onboard_cities.py \
       src/calibration/manager.py src/signal/diurnal.py \
       migrations/2026_04_24_settlements_identity_spine.sql \
       migrations/2026_04_24_settlements_check_constraint.sql \
     --plan-evidence \
       docs/operations/task_2026-04-23_data_readiness_remediation/plan_v4.md
   ```
   Exit must be 0 with receipt. Save receipt as `planning_lock_receipt_v4.json`.

2. **Bulk-writer RCA documentation**: write `bulk_writer_rca_v4.md` summarizing subagent A findings (git log filters, reflog, .omc scan, launchd scan, conclusion: unidentified — mandates QUARANTINE protocol).

3. **Harvester path re-audit** (evidence only): confirm `_write_settlement_truth` (L528-573) and `_find_winning_bin` (L486-503) match current git HEAD. Log to work_log.

4. **HK / CWA evidence gathering**: inspect `config/cities.json` for HK and Taipei entries; verify `settlement_source_type`, `wu_station`, evidence of source role. Produce summary `hk_cwa_evidence_v4.md`.

5. **Semantic boot gates**:
   ```bash
   python scripts/topology_doctor.py --task-boot-profiles --json > evidence/task_boot_profiles.json
   python scripts/topology_doctor.py --fatal-misreads --json > evidence/fatal_misreads.json
   python scripts/topology_doctor.py --core-claims --json > evidence/core_claims.json
   ```

Proceed to R0 only if steps 1 + 3 pass cleanly.

---

## Section 7 — Phased execution order (v4)

| Phase | Issues | Acceptance gate |
|---|---|---|
| R0 (schema) | DR-34 (identity spine migration), DR-01 (forecasts schema align) | AC-R0-INV14 + AC-R0-1/1b/1c |
| R1 (stop bleeding) | DR-05 (poisoned obs_v2 delete), DR-06 (DST gap positive-confirmation test), DR-35 (disable onboard scaffold) | AC-R0-2/2b/6 + onboarding regression |
| R2 (isolation) | DR-09 R2-B/R2-D, DR-11 (test rewrite), DR-10 (helper relocate) | DR-09 v4 AST test green |
| R3 (bulk quarantine) | DR-07a provenance_json backfill, DR-07 quarantine 1,562 rows | All 1,562 rows `authority='QUARANTINED'` with provenance_json populated |
| R3b (harvester fix) | DR-33 (canonical label fix) | Harvester unit test green |
| R3c (re-derive) | DR-07b backfill from observations + SettlementSemantics | AC-R3-W/V/U/HC + 1,540 rows VERIFIED, 22 QUARANTINED |
| R3d (CHECK) | DR-31 (migration + CHECK) | AC-R3-CHECK |
| R3e (reconcile) | DR-32 (reconciliation lane + launchd plist) | First reconciliation run, divergences ≤ 5 (known duplicates) |
| R4..R5 | Inherit v2/v3 phases (DR-04 ensemble, DR-17 dedupe, etc.) | inherit |

---

## Section 8 — Verification matrix (v4)

| AC | Command | Pass condition |
|---|---|---|
| **Task Zero** | `scripts/_planning_lock_for_plan_v4.sh` | exit 0 + receipt |
| **AC-R0-INV14** | `PRAGMA table_info(settlements)` | includes temperature_metric, physical_quantity, observation_field, data_version, provenance_json |
| **AC-R0-1** | `PRAGMA table_info(forecasts)` | all declared columns per `src/state/db.py:653-668` |
| **AC-R0-1b/1c** | `pytest -q tests/test_forecasts_schema_alignment.py tests/test_all_tables_schema_alignment.py` | green |
| **AC-R0-2** | `SELECT COUNT(*) FROM observation_instants_v2 WHERE (running_max>60 AND temp_unit='C') OR (running_max>140 AND temp_unit='F')` | ==0 |
| **AC-R0-2b** | Inspect backup JSONL | exactly 3 entries |
| **AC-R0-6** | `pytest -q tests/test_obs_v2_dst_gap_hour_absent.py` | green |
| **AC-R2-1** | `pytest -q tests/test_ingest_isolation.py` | green (AST-based, not grep) |
| **AC-R2-D** | `pytest -q tests/test_r2d_main_not_coupled_to_ingest_writers.py` | green |
| **AC-R2-TEST** | `pytest -q tests/test_ingest_lanes_defined.py` | green |
| **AC-R3-QUARANTINE** | `SELECT COUNT(*) FROM settlements WHERE authority='QUARANTINED'` | == 1,562 at start of R3 |
| **AC-R3-PROVENANCE** | `SELECT COUNT(*) FROM settlements WHERE provenance_json IS NULL` | == 0 post DR-07a |
| **AC-R3-HARVESTER** | `pytest -q tests/test_harvester_canonical_label.py` | green |
| **AC-R3-LABEL** | `pytest -q tests/test_canonical_bin_label.py` | green |
| **AC-R3-W** | `SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL AND authority='VERIFIED'` | == 0 post DR-07b |
| **AC-R3-V** | `SELECT COUNT(*) FROM settlements WHERE settlement_value IS NULL AND authority='VERIFIED'` | == 0 |
| **AC-R3-U** | `SELECT COUNT(*) FROM settlements WHERE unit IS NULL` | == 0 |
| **AC-R3-VERIFIED** | `SELECT COUNT(*) FROM settlements WHERE authority='VERIFIED'` | == 1,540 (exactly) |
| **AC-R3-QUARANTINE-FINAL** | `SELECT COUNT(*) FROM settlements WHERE authority='QUARANTINED'` | == 22 (15 HK + 7 CWA Taipei) |
| **AC-R3-SEMANTICS** | `pytest -q tests/test_settlement_semantics_gate_enforced.py` | green: every settlement_value is a SettlementSemantics.round_single output (no .5-fractional for precision=1.0 markets) |
| **AC-R3-IDENTITY** | `pytest -q tests/test_settlements_identity_spine.py` | green: 4 identity fields populated, enum valid |
| **AC-R3-CHECK** | `SELECT sql FROM sqlite_master WHERE name='settlements'` | includes INV-14 CHECK constraint |
| **AC-R3-INVARIANT** | `pytest -q tests/test_settlements_invariant.py` | green |
| **AC-R3-RECONCILE** | `python scripts/reconcile_settlements_vs_pm_truth.py` | exit 0, divergence count ≤ 5 |
| **AC-R3-RECONCILE-SCHEDULE** | `plutil -p ~/Library/LaunchAgents/com.zeus.reconcile.pm_truth.plist` | exists + `StartCalendarInterval` set |
| **AC-DR-10-CALLERS** | `pytest -q tests/test_time_utils_callers_all_updated.py` | green — AST walker confirms no caller imports helper from old location |
| **AC-DR-15** | `plutil -p ~/Library/LaunchAgents/com.zeus.live-trading.plist \| grep WU_API_KEY` | non-empty |
| **AC-DR-17** | `SELECT city, target_date, COUNT(*) FROM observations GROUP BY 1,2 HAVING COUNT(*)>1` | 0 rows |
| **AC-DR-17-DISAGREE** | `state/reconciliation/observations_dupe_disagreements.jsonl` | exists with operator sign-off OR absent (all pairs agreed) |

---

## Section 9 — Risk pre-mortem (v4-specific)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| V4-R1 | DR-34 migration fails on running live trading session | LOW | Restart required | Pause live-trading plist before migration; confirmed by Task Zero step 5 heartbeat check |
| V4-R2 | Tel Aviv 2026-03-23 WU→NOAA transition logic off-by-one | MED | 1 day of wrong-source binding | Backfill script respects DB row's existing `settlement_source_type` field verbatim; no inference |
| V4-R3 | SettlementSemantics for HKO with oracle_truncate and observations.hko_daily_api raw precision (0.1°C) produces off-by-one from Polymarket UMA resolution | LOW | 0-2 HK rows mismatched | Quarantine is acceptable; reconciliation log captures divergence |
| V4-R4 | bin label canonical format mismatches Polymarket question text → harvester future writes blocked by DR-33 refusal | MED | Harvester stops writing until parse + canonicalize works per market | Accept — this is intended fail-closed; operator alerted via Discord |
| V4-R5 | `scripts/_build_pm_truth.py` continues writing to `data/pm_settlements_full.json` diverging from reconciliation target `data/pm_settlement_truth.json` | HIGH | User confusion | Evidence file `bulk_writer_rca_v4.md` documents both files. Future packet decides which to keep. v4 does NOT touch either script. |
| V4-R6 | CWA 7 Taipei rows permanently quarantined; operator treats calibration gap as permanent | LOW | 7 pairs lost | Explicit DR-36 stub created; operator priority call |
| V4-R7 | Re-export shim in `src.calibration.manager` + `src.signal.diurnal` breaks 30-day later | LOW | Silent breakage on deprecation day | `docs/operations/task_2026-05-23_drop_time_utils_shim.md` placeholder; calendar reminder |
| V4-R8 | SQLite ALTER TABLE for migration fails (some SQLite versions lack `RENAME TO`) | VERY LOW | Migration abort | Migration uses CREATE new + copy + DROP + RENAME pattern inside single transaction; explicit version check at top |
| V4-R9 | The 1,562 bulk writer re-runs while we are mid-migration | LOW | Re-creates 1,562 NULL rows | Post-Task-Zero, move `data/pm_settlement_truth.json` to `data/pm_settlement_truth.json.pre_v4.bak` during migration window (restore post-R3) |
| V4-R10 | calibration_pairs_v2 still empty after R3 because forecasts_v2 is empty (DR-03/DR-04 unfinished) | HIGH | Training still blocked | Cross-reference DR-03/DR-04 from v2/v3; sequencing doc notes R3 is necessary but not sufficient — forecasts backfill is the remaining blocker |

---

## Section 10 — Scope boundaries

v4 IS NOT:
- A forecasts/TIGGE backfill plan (DR-03/DR-04 in v2/v3 remain)
- A LOW-track enablement plan
- A CWA ingest lane plan (creates DR-36 stub only)
- A replacement for `data/pm_settlements_full.json` investigation

v4 IS:
- Settlements table correctness + identity spine
- Canonical bin label pipeline
- Reconciliation lane (not projection)
- DR-10 helper relocation
- Ingest isolation antibody repair

---

## Section 11 — v3→v4 delta summary table

| # | v3 position | v4 correction | Reviewer finding addressed |
|---|---|---|---|
| 1 | JSON is canonical | DB is canonical (INV-17) | (new; C1 architectural) |
| 2 | `SET settlement_value=pm_high` | settlement_value = SettlementSemantics(obs.high_temp) | critic E8 |
| 3 | DR-32 projection lane | DR-32 reconciliation lane | (new; C3 architectural) |
| 4 | UNRECOVERABLE authority | QUARANTINED (existing enum) | critic E3 |
| 5 | No INV-14 handling | DR-34 adds identity spine | (new; C5 INV-14) |
| 6 | compute_winning_bin in plan | canonical_bin_label in src/contracts/bin_labels.py | critic E7+E9 |
| 7 | grep -E with escaped pipes | AST walker test | critic E1 |
| 8 | provenance_metadata (doesn't exist) | provenance_json (added via DR-34) | critic E2 |
| 9 | Writer "no longer in codebase" | Writer unidentified → QUARANTINE all 1,562 as unregistered provenance | critic E6, subagent A |
| 10 | DR-10 target src/data/_time_utils.py | DR-10 target src/contracts/_time_utils.py | architect P1-3, subagent B |
| 11 | 9 DR-10 callers listed | 24 DR-10 callers verified | critic P1-4, subagent B |
| 12 | scaffold_settlements at onboard:383 unchanged | DR-35 disables scaffold | critic P1-5 |
| 13 | 5 JSON duplicates = backfill concern | 5 JSON duplicates = reconciliation-log noise (JSON not canonical) | architect EF-5 |
| 14 | R2-B misses src.calibration | R2-B includes src.calibration | architect EF-6 (already in v3, preserved) |
| 15 | status_summary.risk_level="currently null" | risk_level="DATA_DEGRADED" (stable anchor per v2) | architect "revert to v2 phrasing" |
| 16 | Harvester caller audit only | DR-33 fixes _format_range + adds callee-guard | subagent C |
| 17 | settlements schema unchanged | DR-34 migration adds 5 columns | (new; INV-14) |
| 18 | Meteostat T23:00:00 fabricated timestamps | v4 inherits DR-13 with architect EF pending evidence review | critic E4 (defer to v2 DR-13 evidence scope) |
| 19 | No planning-lock receipt | Task Zero step 1 is blocking | AGENTS.md required |
| 20 | No semantic boot receipt | Section 1 explicit boot receipt | AGENTS.md required |

---

## Section 12 — Open questions for v4 reviewers

Q1. `src/contracts/_time_utils.py` vs `src/calibration/_time_utils.py` — architect preference?
Q2. DR-34 migration ordering: before or after DR-07a QUARANTINE? v4 puts migration first (empty columns for existing rows), then QUARANTINE fills provenance_json. Alternative: QUARANTINE first, then migration. Sequencing concern: if migration fails, 1,562 rows are still VERIFIED.
Q3. HK re-activation: operator-approved packet or automatic on obs row reappearance?
Q4. CWA Taipei 2026-03-16..22 rows: QUARANTINE permanent, or accept WU obs as settlement-correct on explicit operator sign-off?
Q5. The `data/pm_settlements_full.json` file (DIFFERENT SCHEMA from `pm_settlement_truth.json`) — who owns its lifecycle? v4 does not touch it.
Q6. DR-32 reconciliation cadence — daily 04:15 UTC sufficient, or every 6h?
Q7. Harvester canonical-label failure mode: refuse (raise) vs fall-through to empty-string — v4 chose refuse; confirm.
Q8. 30-day DR-10 shim deprecation vs explicit atomic migration (no shim) — v4 chose shim; confirm.
Q9. The 3 rows with running_max > physically-possible (DR-05 scope): integrated here or left in v2's separate slice?
Q10. v3's Denver/2026-04-15 "orphan" — confirmed not in DB (see Section 2.5). Close the question?
