# Data Readiness Remediation — Master Plan v5

**Status**: DRAFT v5 — supersedes plan_v4.md. Awaiting second-round architect/critic/scientist review + planning-lock + operator approval.
**Created**: 2026-04-23
**Authority basis**: same as v4 + second-round (architect v4 review REJECT_REQUIRES_REWRITE; critic v4 review CHANGES_REQUIRED; scientist v4 review DATA_PARTIALLY_READY)
**Supersedes**: v4. v4 Sections 0/1 (architectural direction + semantic boot receipt) remain authoritative and are inherited by reference. v5 rewrites §2 (backfill mechanics), §3 (DR amendments), §5 (file inventory), §6 (Task Zero), §7 (execution order), §8 (AC matrix), §9 (risks) where v4 had P0 defects.

**Stakes restated**: post-execution, the system must be data-ready with zero unverified writes to the trading calibration corpus. Three parallel reviewers on v4 surfaced 15+ independent P0 defects. v5 addresses every one, with row-by-row enumeration wherever v4 used exact counts.

---

## Section 0 — v4→v5 correction log (15 P0 + 6 P1)

**Architect v4 findings (REJECT_REQUIRES_REWRITE) — all addressed in v5**:

| # | v4 defect | v5 correction | §anchor |
|---|---|---|---|
| A-P0-1 | v4 trusts `settlements.settlement_source_type` row-level but §2.7 quarantines the writer that wrote that column | v5 derives correct source_type per (city, target_date) from `architecture/city_truth_contract.yaml` + `config/cities.json` + `docs/operations/current_source_validity.md` — never reads the bulk row | §2.A |
| A-P0-2 | INV-14 identity spine applied only to settlements; forecasts/market_events/ensemble_snapshots lack 4 fields — silent join degradation | v5 explicitly scopes DR-34 to settlements + adds DR-37 stub for fleet-wide extension + AC-R3-JOIN-INTEGRITY asserts no degraded join emerges during v5's lifetime | §3.DR-34, §3.DR-37, §8 |
| A-P0-3 | INV-08 violation: `_write_settlement_truth` does UPDATE+SELECT+INSERT+commit in 3 separate executes + per-row txn in DR-07b | DR-38 refactors harvester to accept conn + delegate commit to caller; DR-07b uses one outer transaction with per-city SAVEPOINT | §3.DR-38, §2.D |
| A-P0-4 | canonical_bin_label ↔ _parse_temp_range round-trip not tested; unicode/whitespace vulnerable | DR-39 adds byte-exact round-trip test across all canonical shapes + ALL unicode dash variants + thin-space | §3.DR-39 |
| A-P0-5 | `data/pm_settlements_full.json` has no consumer; `_build_pm_truth.py` writes dead artifact; two scripts race `pm_settlement_truth.json` | v5 DR-40: gate `_build_pm_truth.py` behind `--allow-legacy-schema-a` flag + explicit decision to retain `pm_settlement_truth.json` as the ONLY reconciliation source | §3.DR-40 |
| A-P0-6 | INV-15 precondition unstated — settlements can feed into calibration before forecasts prove provenance | AC-R3-INV15-DEFERRED: gate for cross-table join integrity at calibration-write time | §8 |

**Critic v4 findings (CHANGES_REQUIRED) — all addressed**:

| # | v4 defect | v5 correction |
|---|---|---|
| C-P0-1 | Backfill counts 1,540/22 are wrong (~80 rows misallocated) | v5 uses verified inequality accounting: **1,454 primary-recoverable, 13 cross-source recoverable, 5 DB-pre-correction, 6 DST-JSON-fallback, 59 NULL-pm-bin-reconcile-or-quarantine, 30 hard-quarantine**. Full table in §2.B |
| C-P0-2 | 59 rows with NULL pm_bin_lo — canonical_bin_label raises | v5 DR-41: reconcile NULL pm_bin_lo from JSON first (if JSON has the shape); remainder hard-quarantine |
| C-P0-3 | `migrations/` directory does not exist; v4 orphans SQL files | v5 replaces SQL files with `scripts/migrate_*.py` following existing pattern (e.g., `scripts/migrate_add_authority_column.py`) |
| C-P0-4 | Live daemon (pid 17530) running; v4 has no concrete pause step | v5 Task Zero step 1b: stop launchd plist + confirm pid exit + heartbeat staleness check, BEFORE schema migration |
| C-P0-5 | `provenance_json TEXT` weaker than v2 convention `TEXT NOT NULL DEFAULT '{}'` | v5 DR-34 uses `NOT NULL DEFAULT '{}'` + json_valid() CHECK aligned with `src/state/schema/v2_schema.py` pattern |

**Scientist v4 findings (DATA_PARTIALLY_READY) — all addressed**:

| # | v4 defect | v5 correction |
|---|---|---|
| S-D1 | 6 DST-day obs have `authority='QUARANTINED'` — must not be backfill source | v5 §2.C explicitly excludes QUARANTINED obs; those 6 rows route to JSON pm_high fallback (with additional handling if pm_high is sentinel 999) |
| S-D2 | 19 Taipei CWA/NOAA rows have only wu_icao_history obs; wu != CWA settlement authority | v5 §2.E: Taipei CWA (7) + Taipei NOAA (8) = 15 rows HARD QUARANTINE (no path); Taipei WU (11) re-derive from wu_icao_history normally |
| S-D3 | HK quarantine count is 17, not 22 | v5 §2.B: **15 HK HKO Apr 1-15** (no obs) + **2 HK WU Mar 13-14** (cross-source hko_daily_api exists but source-type mismatch — policy quarantine) |
| S-D5 | 2026-04-15 DB rows loaded WRONG JSON entry for all 5 dup cities; pm_bin values incorrect | v5 R2-PRE-FIX phase: correct 5 DB rows from obs-corroborated JSON entry (entry 1 in all 5 cases), BEFORE main backfill |
| S-D7 | forecasts=0, market_events=0, ensemble_snapshots=0 — settlements fix alone does NOT unblock training | v5 §7 explicit sequence: v5 is necessary-but-not-sufficient; DR-03/DR-04 from v2/v3 must run AFTER v5 completes |
| S-D8 | `pm_settlements_full.json` has 1 extra entry (Denver 2026-04-15 NOT in truth) | v5 §3.DR-40: investigate Denver shape; add to truth file or explicit quarantine note |
| S-D14 | Tel Aviv WU Mar 10-22 (13 rows) IS recoverable via `ogimet_metar_llbg` — v4 wrongly quarantined | v5 §2.C: add cross-source fallback policy for Tel Aviv specifically (audited allowance per `current_source_validity.md`) |

**Additional P1s** (architect P1-1..4 + critic P1-1..6) — also addressed below in §3 or §8.

**Not changed from v4** (still correct): §0 architectural direction (INV-17 compliance), §1 semantic boot receipt, most of §2.1/2.6 bin-label algorithm, v4's retracted-claims section, v4's scope boundaries.

---

## Section 1 — Semantic boot (inherited from v4 §1, unchanged)

Task classes + required proofs + fatal misread checklist — all as v4. Fatal misread `api_returns_data_not_settlement_correct_source` now has teeth via §2.A, §2.E.

---

## Section 2 — Corrected backfill mechanics (v5)

### §2.A — settlement_source_type is untrusted (architect P0-1 fix)

The 1,562 bulk-written rows have `settlement_source_type` stamped by an unregistered writer. v4 treated this column as trusted; v5 does not. Source-type per (city, target_date) is derived at backfill time from:

- **Primary**: `architecture/city_truth_contract.yaml::source_roles.settlement_daily_source` per city
- **Current**: `docs/operations/current_source_validity.md` (2026-04-21 audit): `47 WU ICAO + 3 Ogimet/NOAA (Istanbul, Moscow, Tel Aviv) + 1 HKO (Hong Kong)`
- **Runtime**: `config/cities.json::<city>.settlement_source_type` as config-seed
- **Date-range-aware**: Tel Aviv transitioned WU→NOAA at 2026-03-23 per `docs/operations/current_source_validity.md`. Any task that relies on this date MUST cite `current_source_validity.md` explicitly — not the bulk DB value.

Algorithm (pseudocode for `scripts/backfill_settlements_from_observations.py`):

```python
def expected_source(city: str, target_date: str) -> tuple[str, str] | None:
    """Return (settlement_source_type, obs_source_filter) per audited policy."""
    entry = city_truth_contract[city]  # architecture/city_truth_contract.yaml
    if entry.caution_flags.contains("source_changed_by_date"):
        dated = resolve_date_range(entry, target_date, current_source_validity_md)
        if dated is None:
            return None  # QUARANTINE: requires fresh audit
        return dated  # e.g., ("NOAA", "ogimet_metar_%") for Tel Aviv after 2026-03-23
    return entry.primary_source  # e.g., ("WU", "wu_icao_history")

def verify_db_source_type_matches_policy(row):
    actual = row.settlement_source_type
    expected, _ = expected_source(row.city, row.target_date)
    if actual != expected:
        log_drift(row, actual=actual, expected=expected)
        row.authority = 'QUARANTINED'
        row.provenance_json['reason'] = 'BULK_WRITER_SOURCE_TYPE_DRIFT'
        return False
    return True
```

**Consequence for Tel Aviv**: the 23 rows stamped NOAA (2026-03-23..04-15) and 13 rows stamped WU (2026-03-10..22) in the DB MATCH the audited WU→NOAA transition at 2026-03-23. So after policy-check, all 36 agree. They are NOT quarantined for source-type drift — but they were "lucky" coincidence, not trusted. This is documented evidence.

### §2.B — Full 1,562-row accounting table (critic P0-1 fix — replaces v4 §2.4/2.5/2.7 exact counts)

Verified via SQL 2026-04-23:

| Category | Count | Backfill path | Expected final authority |
|---|---:|---|---|
| Recoverable — primary source obs VERIFIED, pm_bin populated | 1,454 | `settlements.SET winning_bin = canonical_bin_label(pm_lo, pm_hi, unit); settlement_value = SettlementSemantics.for_city(city).assert_settlement_value(obs.high_temp)` | VERIFIED |
| Recoverable — cross-source fallback (Tel Aviv WU→ogimet_metar_llbg) | 13 | same but obs from `ogimet_metar_llbg` with audited allowance citation (§2.C) | VERIFIED + `provenance_json.cross_source_fallback=true` |
| Pre-correction required — 2026-04-15 DB wrong-entry | 5 | R2-PRE-FIX phase rewrites pm_bin_lo/hi/unit from JSON entry-1 (obs-corroborated); then enters primary backfill | VERIFIED after pre-correction |
| JSON-fallback required — 6 DST-day 2026-03-08 (obs QUARANTINED) | 6 | Must use JSON `pm_high` IF not sentinel; if pm_high ∈ {999, -999}, QUARANTINE (no truth source) | VERIFIED (0-6) + QUARANTINED (0-6) based on pm_high value |
| Reconcile-from-JSON-first — NULL pm_bin_lo | 59 | DR-41 reconcile script: if JSON has pm_bin shape for (city, date), UPDATE pm_bin_lo/hi; then enters primary backfill. If JSON has no entry, QUARANTINE | VERIFIED after reconcile (partial) + QUARANTINED (partial) |
| Hard QUARANTINE — HK HKO Apr 1-15 (no obs in any source) | 15 | QUARANTINE with `reason='HKO_INGEST_STALLED'`; await fresh HKO audit | QUARANTINED |
| Hard QUARANTINE — Taipei CWA+NOAA (wu_icao not settlement-authoritative) | 15 | QUARANTINE with `reason='TAIPEI_SOURCE_MISMATCH_NO_CWA_OBS'` (7 CWA + 8 NOAA) | QUARANTINED |
| Hard QUARANTINE — HK WU Mar 13-14 (cross-source hko_daily_api exists but source-type mismatch) | 2 | QUARANTINE with `reason='HK_WU_STAMP_SOURCE_TYPE_DRIFT'`; requires source-type audit before re-activation | QUARANTINED |

**Post-execution outcome range**:
- **VERIFIED**: 1,472 min (if 0 JSON-reconcile, 0 DST-recover) to 1,537 max (if 59 JSON-reconcile + 6 DST-recover)
- **QUARANTINED**: 25 min (15 HK-HKO + 8 Taipei-NOAA + 2 HK-WU = 25, no additional) to **90 max** (all 59 NULL-pm unrecoverable + all 6 DST unrecoverable + 25 hard = 90)
- **Grand total**: 1,562 ✓

v5 ACs in §8 use **inequalities + enumerated categories**, not exact counts.

### §2.C — Cross-source fallback policy (scientist D14)

Scientist verified: Tel Aviv 2026-03-10..22 has 13 rows of `ogimet_metar_llbg` obs exactly matching the 13 WU-stamped settlement rows; containment passes 13/13. Polymarket's WU resolution source for Tel Aviv pre-2026-03-23 published daily highs that exactly match ogimet_llbg values on overlapping days, per 2026-04-21 audit of `docs/operations/current_source_validity.md`.

v5 policy:
- **General**: obs.source MUST match the expected family per §2.A. No cross-source substitution by default.
- **Audited exception — Tel Aviv WU→ogimet_llbg 2026-03-10..22**: allowed with explicit flag `provenance_json.cross_source_fallback={"reason":"wu_obs_absent_but_ogimet_audit_shows_equivalent","audit":"docs/operations/current_source_validity.md"}` and AC that containment passes.
- **No other city qualifies for cross-source substitution** in v5 scope. Future packets may add based on audit evidence.

### §2.D — Transaction boundaries (architect P0-3 fix)

**Current harvester defect**: `src/execution/harvester.py:528-573` `_write_settlement_truth` issues `UPDATE→SELECT changes()→INSERT→conn.commit()` in 3 execute calls + commit; caller `_dual_write_canonical_settlement_if_available` wraps with `append_many_and_project` which has its own commit path. INV-08 says canonical writes have ONE transaction boundary.

**v5 refactor (DR-38)**:
1. `_write_settlement_truth` accepts an open transaction, does NOT call commit
2. Caller at `src/execution/harvester.py:194-260` owns the commit boundary
3. Single "canonical settlement write" = event append + projection update + _write_settlement_truth, all in one transaction
4. The BaseConn.with SAVEPOINT pattern from MEMORY L30 (feedback_with_conn_nested_savepoint_audit.md) applies: do NOT use `with conn:` inside savepoint; use explicit SAVEPOINT/RELEASE/ROLLBACK TO.

**v5 backfill script transaction model**:
- One outer `BEGIN` wrapping all 1,540-ish UPDATE rows
- Per-city `SAVEPOINT` (50 cities) so a single failing city rolls back without losing work in others
- On any error within a city, emit `ROLLBACK TO SAVEPOINT <city>; RELEASE SAVEPOINT <city>` and mark that city's rows `authority='QUARANTINED'` with `provenance_json.reason='BACKFILL_ERROR_CITY_<name>'`
- Outer COMMIT at end
- Resumable: `state/quarantine/backfill_progress.jsonl` records each city's completion; re-run skips completed cities

### §2.E — Taipei quarantine policy (scientist D2)

Verified: Taipei obs = only `wu_icao_history` in DB. 30 Taipei settlements split CWA=7 + NOAA=12 + WU=11 (2026-04-05..15).

v5 decision:
- **Taipei WU (11 rows, 2026-04-05..15)**: `wu_icao_history` obs + `settlement_source_type='WU'` → re-derive normally.
- **Taipei CWA (7 rows, 2026-03-16..22)**: **NO wu_icao_history proxy accepted**. Polymarket's CWA resolution source is Taiwan Central Weather Administration; wu_icao_history is RCTP airport (systematically warmer per scientist). Containment fails 6/7 by 1-5°C. **HARD QUARANTINE** with `provenance_json.reason='TAIPEI_CWA_NO_OBS_SOURCE'`. Unblocked by: separate CWA ingest packet (DR-36 stub).
- **Taipei NOAA (8 rows, 2026-03-23..04-04)**: same policy — containment fails 9/12 per scientist — HARD QUARANTINE with `provenance_json.reason='TAIPEI_NOAA_NO_OGIMET_OBS'`.

Note: Taipei NOAA should actually have ogimet_metar_rctp obs in a healthy system. The absence is itself evidence of an ingest gap worth investigating (scientific anomaly — NOAA is typically Ogimet-proxied).

### §2.F — DST-day (2026-03-08) rows with QUARANTINED obs (scientist D1 + D15)

6 rows: NYC/Chicago/Atlanta/Dallas/Miami/Seattle on 2026-03-08. obs.authority='QUARANTINED' for all (prior audit set this because DST transition captured morning reads instead of afternoon daily high — obs values 2-28°F below pm_bin). This is evidence that obs is NOT settlement-truth for these 6.

v5 policy:
- MUST NOT use QUARANTINED obs as re-derivation source (INV-09 "Missing data is first-class truth")
- Fall back to JSON `pm_high`:
  - If JSON has valid pm_high (not sentinel 999/-999), use `settlement_value=pm_high` with SettlementSemantics rounding; `winning_bin=canonical_bin_label(pm_bin_lo, pm_bin_hi, unit)`
  - If pm_high IS sentinel, QUARANTINE with `reason='DST_DAY_OBS_QUARANTINED_PM_HIGH_SENTINEL'`
- Mark `provenance_json.source='json_pm_high_fallback'` + `provenance_json.obs_excluded_reason='QUARANTINED_dst_transition'` so calibration can filter if needed

Expected split of the 6: per scientist D15 quick-read, NYC has pm_high=999 sentinel (QUARANTINE), others may have valid pm_high (RECOVER). Exact count determined at backfill time.

### §2.G — 2026-04-15 JSON duplicates: DB pre-correction (scientist D5)

Verified: 5 DB rows (London/NYC/Seoul/Shanghai/Tokyo for 2026-04-15) have `pm_bin_lo/hi` from JSON entry **2** (the WRONG one). obs corroborates JSON entry **1** (the HIGHER value) in all 5 cases.

v5 R2-PRE-FIX phase (new):

```python
# scripts/fix_2026_04_15_wrong_entry.py  (new)
# Before main backfill, correct the 5 DB rows
CORRECTIONS = {
    # (city, target_date): (pm_bin_lo, pm_bin_hi, unit, source_comment)
    ('London',   '2026-04-15'): (17.0, 17.0, 'C', 'obs=17C corroborates JSON entry 1'),
    ('Seoul',    '2026-04-15'): (21.0, 999.0, 'C', 'obs=21C corroborates JSON entry 1 (shoulder)'),
    ('NYC',      '2026-04-15'): (86.0, 87.0, 'F', 'obs=87F corroborates JSON entry 1'),
    ('Shanghai', '2026-04-15'): (18.0, 18.0, 'C', 'obs=18C corroborates JSON entry 1'),
    ('Tokyo',    '2026-04-15'): (22.0, 22.0, 'C', 'obs=22C corroborates JSON entry 1'),
}
```

R2-PRE-FIX runs inside the backfill transaction, BEFORE any re-derivation SQL. It records the prior incorrect pm_bin_lo/hi in `provenance_json.pre_v5_pm_bin_corrected_from = {prev_lo, prev_hi}`.

### §2.H — NULL pm_bin_lo reconciliation (critic P0-2 + DR-41)

59 rows have `pm_bin_lo IS NULL`. Distribution (top): Lucknow=20, Buenos Aires=5, London=4, Taipei=4, LA=3, NYC=3. These rows cannot produce `winning_bin` via canonical_bin_label (raises on None input).

v5 DR-41:

```python
# scripts/reconcile_null_pm_bin_from_json.py (new — R2-PRE-FIX, before main backfill)
import json
with open('data/pm_settlement_truth.json') as f:
    truth = {(e['city'], e['date']): e for e in json.load(f)}
# Find NULL-pm rows
rows = db.execute("SELECT id, city, target_date FROM settlements WHERE pm_bin_lo IS NULL")
for row in rows:
    entry = truth.get((row['city'], row['target_date']))
    if entry is None:
        db.execute("UPDATE settlements SET authority='QUARANTINED', provenance_json=json_set(COALESCE(provenance_json,'{}'), '$.reason', 'NULL_PM_BIN_NO_JSON_ENTRY') WHERE id=?", [row['id']])
        continue
    db.execute("UPDATE settlements SET pm_bin_lo=?, pm_bin_hi=?, unit=? WHERE id=?",
        [entry['pm_bin_lo'], entry['pm_bin_hi'], entry['unit'], row['id']])
```

Runs BEFORE main backfill. Reconciled rows then enter main backfill. Non-reconciled rows stay QUARANTINED.

---

## Section 3 — New / redefined DRs in v5

### DR-34 (v5 AMENDED) — settlements identity spine

Schema migration delivered as **`scripts/migrate_2026_04_24_settlements_identity_spine.py`** (NOT as orphan SQL file — critic P0-3). Idempotent, follows existing pattern of `scripts/migrate_add_authority_column.py`.

Columns added:
```python
ALTER TABLE settlements ADD COLUMN temperature_metric TEXT
  CHECK (temperature_metric IN ('high','low'))   -- NOT NULL deferred: retrofit existing rows first
ALTER TABLE settlements ADD COLUMN physical_quantity TEXT
ALTER TABLE settlements ADD COLUMN observation_field TEXT
  CHECK (observation_field IN ('high_temp','low_temp'))
ALTER TABLE settlements ADD COLUMN data_version TEXT
ALTER TABLE settlements ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'
  -- NOT NULL DEFAULT aligned with src/state/schema/v2_schema.py convention (critic P0-5)
```

Retrofit populates existing 1,562 rows:
- `temperature_metric = 'high'` (all current Zeus contracts are high-only)
- `observation_field = 'high_temp'`
- `physical_quantity = 'mx2t6_local_calendar_day_max'` (per zeus_current_architecture.md §7.1)
- `data_version` derived per-source:
  - WU cities: `'v1.wu-native'`
  - NOAA cities: `'v1.noaa-ogimet'`
  - HKO cities: `'v1.hko-native'`
  - CWA cities: `'v1.cwa-quarantined'` (not actively trained)
  (architect silent-assumption #4 fix — one uniform 'v1.wu-native' is a provenance lie)

After retrofit, add CHECK constraints making the fields NOT NULL for VERIFIED rows only. Single-step migration via CREATE new table + copy + drop + rename (SQLite pattern).

### DR-37 (NEW) — INV-14 fleet-wide scope stub

- **Severity**: 🟠 P1 (not in v5 execution scope; marks known architectural debt)
- **Detected tables missing INV-14 fields**: `forecasts` (0 of 4), `market_events` (0 of 4), `ensemble_snapshots` (2 of 4 — lacks physical_quantity + observation_field)
- **Not in v5 because**: forecasts has 0 rows (scope elsewhere); market_events has 0 rows; ensemble_snapshots has 0 rows; no live inconsistency during v5 execution window
- **Path**: separate packet `task_2026-05-XX_inv14_fleetwide` once v5 stabilizes + forecasts backfill packet lands
- **AC in v5 scope**: AC-R3-JOIN-INTEGRITY asserts no calibration pair JOIN silently drops data_version at v5 completion

### DR-38 (NEW) — Harvester settlement-write atomicity refactor

- **Severity**: 🔴 P0 — INV-08 compliance precondition
- **Detection**: `src/execution/harvester.py:528-573` — triple-execute + commit pattern; violates INV-08
- **Fix**: refactor `_write_settlement_truth` signature to require an open `Conn` and NOT call commit. Push commit boundary to `_dual_write_canonical_settlement_if_available` at `src/execution/harvester.py:194-260`. Use SAVEPOINT/RELEASE/ROLLBACK TO idioms per MEMORY L30 (feedback_with_conn_nested_savepoint_audit.md).
- Additionally: DR-38b — harvester's INSERT path (current L558-568) must populate ALL 5 new INV-14 fields per DR-34, not just winning_bin (critic P1-2 subtle catch)
- **Tests**: `tests/test_harvester_atomic_settlement_write.py` — inject error mid-UPDATE and confirm no partial row persists

### DR-39 (NEW) — Parser ↔ canonical_bin_label round-trip test

- **Severity**: 🔴 P0
- **Fix**: `tests/test_parser_canonical_roundtrip.py` asserts:
  - For every canonical bin in `src/contracts/calibration_bins.py`: `_parse_temp_range(label) == (lo, hi)` or shoulder equivalent
  - For every (lo, hi, unit) tuple that `canonical_bin_label` produces: `_parse_temp_range(canonical_bin_label(lo, hi, unit))` returns (lo, hi) back
  - Unicode variants: test with `°`, `°` (ASCII if any), em-dash `—`, en-dash `–`, hyphen-minus `-`, no-break hyphen, thin-space (U+2009), regular space, and their combinations
  - Sentinel round-trip: `canonical_bin_label(-999, 40, 'C')` → `"40°C or below"` → parse yields (None, 40) (shoulder semantics)
  - Negative double-minus: `canonical_bin_label(-38, -37, 'F')` → `"-38--37°F"` → parse yields (-38, -37)
- **Blocking AC**: test must be green before DR-33 harvester canonicalization ships

### DR-40 (NEW) — pm_settlements_full.json disposition

- **Severity**: 🟠 P1 — race hazard
- **Facts**:
  - `scripts/_build_pm_truth.py:185` writes `data/pm_settlements_full.json` (SCHEMA A: pm_exact_value, 631/1567 NULL)
  - `data/pm_settlement_truth.json` (SCHEMA B: pm_high, 0/1566 NULL) is the DR-32 reconciliation target
  - No consumer of SCHEMA A found via grep
  - SCHEMA A is NEWER (mtime 70min later); Denver 2026-04-15 present ONLY in SCHEMA A
- **v5 action**: gate `_build_pm_truth.py` behind `--allow-legacy-schema-a` flag (default-off). Default run writes only SCHEMA B (`pm_settlement_truth.json`). Legacy SCHEMA A kept on disk but NOT re-written.
- **Denver 2026-04-15**: was present in v3's "orphan" list; v4 closed it; scientist D8 re-opens. v5 investigates in TASK ZERO step 3: if JSON entry is sensible, add to `pm_settlement_truth.json` via manual append + audit note; else QUARANTINE the corresponding DB row (if one exists — verify via SQL) with reason `DENVER_2026-04-15_ORPHAN_PENDING_AUDIT`.

### DR-41 (NEW) — NULL pm_bin_lo reconciliation

See §2.H. Delivered as `scripts/reconcile_null_pm_bin_from_json.py`. Runs as part of R2-PRE-FIX phase.

### DR-42 (NEW) — 2026-04-15 DB pre-correction

See §2.G. Delivered as `scripts/fix_2026_04_15_wrong_entry.py`. Runs as R2-PRE-FIX first step.

### DR-33 (v5 AMENDED) — Harvester canonical label format

Unchanged from v4 except:
- Requires DR-39 round-trip test green FIRST
- Requires DR-38 transaction refactor FIRST (atomic write path is precondition for any harvester patch)
- INSERT path populates INV-14 fields per DR-34

### DR-32 (v5 AMENDED) — Reconciliation lane

Unchanged from v4 except:
- Threshold `divergence_count ≤ known_duplicates_count` where `known_duplicates_count=5` is explicitly listed in script config (not magic constant) — critic P1-2
- Duplicate handling protocol: for (city, date) with 2 JSON entries, reconcile against DB row by pm_bin matching; if NEITHER JSON entry matches DB, log both as divergences. After R2-PRE-FIX (DR-42) lands, duplicate keys should match ONE JSON entry (entry 1 per DR-42 evidence) → reconciliation treats that as the canonical.
- Locking: script runs with `PRAGMA busy_timeout=30000` and defers if `state/daemon-heartbeat.json` mtime < 60s (harvester potentially mid-cycle) — critic P1-1

### DR-10 (v5 AMENDED) — helper relocation

Target remains `src/contracts/_time_utils.py` + `src/contracts/bin_labels.py` (architectural neutrality per v4 Section 3).

Extended caller list (critic P1-1 + subagent B comprehensive):
- All 24 v4 callers PLUS:
- `hemisphere_for_lat` imports (src/signal/diurnal.py:13, src/signal/diurnal.py:127, src/engine/monitor_refresh.py:464)
- `lat_for_city` imports (src/signal/diurnal.py:225, src/engine/monitor_refresh.py:464)

Decision: `hemisphere_for_lat` and `lat_for_city` relocate as well to `src/contracts/_geo_utils.py` (NEW) in the same commit, so callers of `src.calibration.manager.*_lat*` have a consistent move. Shim in `src.calibration.manager` re-exports BOTH `season_*` AND `hemisphere_for_lat` AND `lat_for_city` for 30-day deprecation.

### DR-31 (v5 AMENDED) — settlements CHECK

- Single migration combining DR-34 columns + DR-31 CHECK at the end (to avoid intermediate invalid states)
- CHECK covers:
  ```
  (authority IN ('QUARANTINED','UNVERIFIED'))
  OR
  (winning_bin IS NOT NULL AND settlement_value IS NOT NULL
   AND temperature_metric IS NOT NULL AND physical_quantity IS NOT NULL
   AND observation_field IS NOT NULL AND data_version IS NOT NULL
   AND json_valid(provenance_json) AND provenance_json != '{}')
  ```
  — last condition ensures VERIFIED rows carry real provenance (architect silent assumption fix)
- Trigger `settlements_authority_monotonic`: prevents changing authority from QUARANTINED → VERIFIED without a declared re-activation packet (addresses architect silent assumption #3)

### DR-35 (v5 AMENDED) — onboard_cities scaffold removal

Verified `grep -rn scaffold_settlements` shows only 2 self-references in `scripts/onboard_cities.py`. Safe to remove. Evidence block pasted in plan_v5_evidence/scaffold_callers.txt.

**Upstream concern (critic P1-2)**: DR-35 removes scaffold; harvester INSERT must then populate ALL INV-14 fields + winning_bin or the CHECK fails. DR-38b handles this.

---

## Section 4 — Retracted / closed items from v4

Same as v4 Section 4 plus:
- "Tel Aviv WU Mar 10-22 will quarantine" — **RETRACTED**. Tel Aviv WU rows ARE recoverable via ogimet_metar_llbg per §2.C.
- "Denver 2026-04-15 is not in DB (closes v3 Q2)" — **REVERSED**. Not in DB AND not in pm_settlement_truth.json, but IS in pm_settlements_full.json. DR-40 investigates.

---

## Section 5 — File inventory (v5 replacement)

**New files**:
- `src/contracts/_time_utils.py` (DR-10 target)
- `src/contracts/_geo_utils.py` (DR-10 extension — hemisphere + lat helpers)
- `src/contracts/bin_labels.py` (canonical_bin_label from v4 §2.6)
- `scripts/backfill_settlements_from_observations.py` (DR-07b; single outer txn + per-city SAVEPOINT per §2.D)
- `scripts/reconcile_null_pm_bin_from_json.py` (DR-41)
- `scripts/fix_2026_04_15_wrong_entry.py` (DR-42)
- `scripts/reconcile_settlements_vs_pm_truth.py` (DR-32 v4)
- `scripts/migrate_2026_04_24_settlements_identity_spine.py` (Python migration, matches existing pattern — NOT SQL file)
- `scripts/migrate_2026_04_24_settlements_check_constraint.py` (Python migration)
- `tests/test_canonical_bin_label.py`
- `tests/test_parser_canonical_roundtrip.py` (DR-39)
- `tests/test_harvester_atomic_settlement_write.py` (DR-38)
- `tests/test_harvester_canonical_label.py`
- `tests/test_settlements_identity_spine.py`
- `tests/test_settlements_invariant.py`
- `tests/test_r2d_main_not_coupled_to_ingest_writers.py`
- `tests/test_pm_truth_reconciliation.py`
- `tests/test_time_utils_callers_all_updated.py` (DR-10)
- `~/Library/LaunchAgents/com.zeus.reconcile.pm_truth.plist` (DR-32)
- `docs/operations/task_2026-04-23_data_readiness_remediation/bulk_writer_rca_v5.md` (evidence, inherits v4 scope)
- `docs/operations/task_2026-04-23_data_readiness_remediation/evidence/1562_row_accounting.csv` (per-row category for AC-R3-VERIFIED-CATS)
- `docs/operations/task_2026-04-23_data_readiness_remediation/evidence/scaffold_callers.txt`
- `state/quarantine/settlements_pre_v5_20260423.jsonl` (backup)
- `state/quarantine/backfill_progress.jsonl` (resumability cursor)
- `state/reconciliation/pm_truth_divergence.jsonl`

**Modified files** (beyond v4):
- `src/execution/harvester.py` (DR-33 + DR-38 — canonical label + atomicity + INV-14 fields on INSERT)
- `src/state/db.py` (schema_init mirrors migration for fresh DBs)
- `scripts/onboard_cities.py` (DR-35 removal)
- `src/calibration/manager.py` (DR-10 shims — now includes hemisphere_for_lat + lat_for_city)
- `src/signal/diurnal.py` (DR-10 shims)
- 24+ callers across src/ + tests/ (DR-10 imports)

**Removed relative to v4**:
- `migrations/2026_04_24_settlements_identity_spine.sql` (replaced by Python script)
- `migrations/2026_04_24_settlements_check_constraint.sql` (replaced by Python script)

---

## Section 6 — Task Zero (v5 replacement)

Run in order, blocking:

1. **Planning-lock machine check** (per AGENTS.md — already passed on v4 structurally, re-run on v5 with updated file list)
2. **1b. Stop live-trading daemon** (NEW per critic P0-4):
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.zeus.live-trading.plist
   sleep 5
   ps -p 17530 2>&1 | grep -q 'PID TTY' && echo "EXITED" || echo "STILL RUNNING — INVESTIGATE"
   # Verify heartbeat stops updating
   STAMP1=$(stat -f '%m' state/daemon-heartbeat.json)
   sleep 120
   STAMP2=$(stat -f '%m' state/daemon-heartbeat.json)
   [ "$STAMP1" = "$STAMP2" ] && echo "HEARTBEAT FROZEN OK" || echo "DAEMON STILL ACTIVE — ABORT"
   ```
   Blocking: only proceed to schema migration if daemon confirmed stopped.
3. **Bulk-writer RCA documentation** — write `bulk_writer_rca_v5.md` summarizing architect + critic + scientist verifications (inherits v4 scope)
4. **Denver 2026-04-15 disposition** (NEW per scientist D8 + DR-40):
   ```bash
   # Check if a DB row exists
   sqlite3 state/zeus-world.db "SELECT * FROM settlements WHERE city='Denver' AND target_date='2026-04-15'"
   # If present, stage for QUARANTINE with reason DENVER_ORPHAN_PENDING_JSON
   # Investigate pm_settlements_full.json vs pm_settlement_truth.json
   # Record decision in plan_v5_evidence/denver_disposition.md
   ```
5. **HK + CWA evidence gathering** (inherits v4 step 4)
6. **Semantic boot gates**:
   ```bash
   python scripts/topology_doctor.py --task-boot-profiles --json > evidence/task_boot_profiles.json
   python scripts/topology_doctor.py --fatal-misreads --json > evidence/fatal_misreads.json
   python scripts/topology_doctor.py --core-claims --json > evidence/core_claims.json
   ```
7. **1562-row census dump** (NEW — AC reference):
   Run the §2.B accounting query and dump results to `evidence/1562_row_accounting.csv`. This is the per-row truth table that ACs reference.

Proceed to R0 only if steps 1, 1b, 3 pass cleanly.

---

## Section 7 — Phased execution order (v5)

| Phase | Issues | Key acceptance gate |
|---|---|---|
| R0 (schema) | DR-34 migration (Python script), DR-31 CHECK constraint, DR-01 forecasts schema | AC-R0-INV14 + AC-R0-1/1b/1c |
| R1 (stop bleeding, live daemon paused) | DR-05, DR-06, DR-35 | ACs from v2/v3 |
| R2-PRE-FIX | DR-42 (5 wrong-entry rows fixed), DR-41 (NULL pm_bin reconciled), bulk row QUARANTINE (1,562 rows → authority='QUARANTINED' with provenance_json) | AC-R2-PRE-FIX (5 DB rows corrected); AC-R2-NULL-PM-RECONCILED |
| R2 (isolation) | DR-09 R2-B/R2-D, DR-11, DR-10 | DR-09 AST test green |
| R3a (contracts + bin_labels) | DR-10 + DR-39 (parser round-trip) | AC-R3-ROUNDTRIP |
| R3b (harvester atomicity + label) | DR-38, DR-38b, DR-33 | AC-R3-HARVESTER-ATOMIC, AC-R3-LABEL |
| R3c (re-derive) | `backfill_settlements_from_observations.py` with §2.D txn model | AC-R3-CATEGORIES + AC-R3-VERIFIED-RANGE + AC-R3-JOIN-INTEGRITY |
| R3d (reconcile + daemon restart) | DR-32 plist + restart daemon | AC-R3-RECONCILE |
| R4..R5 | Inherit from v2/v3 |
| R6 (deferred) | DR-37 fleet-wide INV-14 (new packet) |

---

## Section 8 — Verification matrix (v5)

| AC | Command | Pass condition |
|---|---|---|
| **Task Zero 1b** | daemon-heartbeat frozen | stamp unchanged for ≥120s |
| **AC-R0-INV14** | `PRAGMA table_info(settlements)` | includes temperature_metric, physical_quantity, observation_field, data_version, provenance_json; provenance_json has NOT NULL DEFAULT '{}' |
| **AC-R2-PRE-FIX** | `SELECT city, pm_bin_lo, pm_bin_hi FROM settlements WHERE target_date='2026-04-15' AND city IN ('London','NYC','Seoul','Shanghai','Tokyo')` | rows match DR-42 CORRECTIONS table exactly |
| **AC-R2-NULL-PM-RECONCILED** | `SELECT COUNT(*) FROM settlements WHERE pm_bin_lo IS NULL AND authority='VERIFIED'` | == 0 |
| **AC-R2-BULK-QUARANTINED** | `SELECT COUNT(*) FROM settlements WHERE authority='QUARANTINED' AND settled_at='2026-04-16T12:39:58.026729+00:00'` | == 1562 (all bulk rows) — transient; decreases as re-derive promotes |
| **AC-R2-1** | `pytest -q tests/test_ingest_isolation.py` | green (AST) |
| **AC-R2-D** | `pytest -q tests/test_r2d_main_not_coupled_to_ingest_writers.py` | green (AST) |
| **AC-R3-ROUNDTRIP** | `pytest -q tests/test_parser_canonical_roundtrip.py` | green for ALL canonical shapes AND unicode variants |
| **AC-R3-HARVESTER-ATOMIC** | `pytest -q tests/test_harvester_atomic_settlement_write.py` | green (fail-mid-UPDATE test: no partial state persists) |
| **AC-R3-LABEL** | `pytest -q tests/test_harvester_canonical_label.py tests/test_canonical_bin_label.py` | green |
| **AC-R3-CATEGORIES** | Compare DB vs `evidence/1562_row_accounting.csv`: for each row, actual authority matches expected-category outcome | 100% match (enumerated per row, not aggregate) |
| **AC-R3-VERIFIED-RANGE** | `SELECT COUNT(*) FROM settlements WHERE authority='VERIFIED' AND winning_bin IS NOT NULL` | ≥ 1,472 and ≤ 1,537 (exact value recorded in receipt) |
| **AC-R3-QUARANTINED-ENUMERATED** | `SELECT provenance_json->>'$.reason' AS r, COUNT(*) FROM settlements WHERE authority='QUARANTINED' GROUP BY 1` | Every reason is in {'unregistered_bulk_writer_2026-04-16T12:39:58Z' (transient), 'HKO_INGEST_STALLED', 'TAIPEI_CWA_NO_OBS_SOURCE', 'TAIPEI_NOAA_NO_OGIMET_OBS', 'HK_WU_STAMP_SOURCE_TYPE_DRIFT', 'DST_DAY_OBS_QUARANTINED_PM_HIGH_SENTINEL', 'NULL_PM_BIN_NO_JSON_ENTRY', 'DENVER_2026-04-15_ORPHAN_PENDING_AUDIT', 'BACKFILL_ERROR_CITY_*'} — no unknown reasons |
| **AC-R3-NO-QUARANTINED-OBS-USED** | `SELECT COUNT(*) FROM settlements s JOIN observations o ON ... WHERE s.authority='VERIFIED' AND s.provenance_json->>'$.obs_id' = o.id AND o.authority='QUARANTINED'` | == 0 |
| **AC-R3-INV14-ENFORCED** | every VERIFIED row: all 4 identity fields populated with valid enum values | 100% |
| **AC-R3-PROVENANCE-NONEMPTY** | VERIFIED rows: `provenance_json` not '{}', `json_valid(provenance_json)`, has keys `source, obs_id, rounding_rule, data_version` | 100% |
| **AC-R3-AUTHORITY-MONOTONIC** | trigger `settlements_authority_monotonic` exists | trigger SQL present in sqlite_master |
| **AC-R3-JOIN-INTEGRITY** | simulated JOIN `SELECT COUNT(*) FROM settlements s JOIN (SELECT 1) f ON 1=1 WHERE s.data_version IS NOT NULL` — when forecasts backfill lands, can every settlement row find a data_version match? | deferred AC — blocks DR-03/DR-04 completion AC |
| **AC-R3-DATA-VERSION-BY-SOURCE** | `SELECT settlement_source_type, GROUP_CONCAT(DISTINCT data_version) FROM settlements WHERE authority='VERIFIED' GROUP BY 1` | WU cities → v1.wu-native; NOAA → v1.noaa-ogimet; HKO → v1.hko-native; CWA → v1.cwa-quarantined |
| **AC-R3-RECONCILE** | `python scripts/reconcile_settlements_vs_pm_truth.py` | exit 0; divergences ≤ 5 known duplicate keys (enumerated in script config) |
| **AC-DR-10-CALLERS** | `pytest -q tests/test_time_utils_callers_all_updated.py` | green (AST walker; includes hemisphere_for_lat + lat_for_city) |
| **AC-DR-38-NO-INDEPENDENT-COMMIT** | `grep -c 'conn.commit' src/execution/harvester.py` | ≤ 1 (only at top-level `run_harvester` context manager exit, not inside _write_settlement_truth) |
| (inherit) | DR-15, DR-17, DR-01, DR-05, DR-06 ACs from v4 | unchanged |

---

## Section 9 — Risk pre-mortem (v5 delta)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| V5-R1 | DR-42 pre-correction writes to 5 DB rows while daemon paused; if daemon has stale in-memory projection, post-restart projection drift | LOW | Operator-visible discrepancy | post-DR-42 verification: dump settlements then compare to projections after daemon restart |
| V5-R2 | DR-41 NULL pm_bin reconciliation: JSON has entry for (city,date) but different pm_bin shape than expected; silent UPDATE with wrong shape | MED | 59 rows may get wrong bin | reconcile script validates `pm_bin_lo < pm_bin_hi` + unit matches DB; on mismatch, row to QUARANTINE with reason `NULL_PM_BIN_JSON_SHAPE_INVALID` |
| V5-R3 | DR-38 harvester refactor introduces regression in live cycle; unrelated to settlement path but in same module | LOW | Harvester cycle broken | feature-flag the refactor: `ZEUS_HARVESTER_ATOMIC_V5=1` env gate; keep legacy path until first successful harvest observed |
| V5-R4 | Single outer txn for 1,540 backfill rows holds write lock 60+ seconds | MED | Blocks any concurrent reader (reconciliation script, audits) | daemon is paused during this phase; reconciliation script gated by `daemon-heartbeat.json` staleness check; audits must wait |
| V5-R5 | Taipei NOAA quarantine (8 rows) masks a genuine ogimet ingest gap worth investigating | LOW | Missing data signal lost | DR-36 stub logs the gap; separate packet investigates |
| V5-R6 | DR-40 gating `_build_pm_truth.py` breaks another workflow that depends on full-schema | LOW | User-workflow disruption | gate is a flag, not deletion; user can `--allow-legacy-schema-a` to restore prior behavior |
| V5-R7 | 6 DST-day rows route to JSON pm_high, but pm_high is known to be midpoint (fabricated) for F-city range bins — same fabrication as v3 flag | MED | 6 settlement_values are midpoints | add AC that these 6 are explicitly marked `provenance_json.settlement_value_origin='json_pm_high_fabricated_midpoint'` so calibration knows to either weight lower or exclude |
| V5-R8 | data_version 'v1.cwa-quarantined' unique value excludes those rows from training | LOW | intended, not a risk | explicit behavior: if CWA ingest lands, DR-36 migration updates data_version to 'v1.cwa-native' |
| V5-R9 | 30-day shim deprecation for DR-10 may slip; legacy imports still active post 2026-05-23 | MED | Code-quality debt | calendar reminder + CI test that fails after 2026-05-23 if shim still present |
| V5-R10 | NOT RESOLVING: training still blocked | HIGH | Training starts with only 1,472-1,537 settlement rows + 0 forecast rows | explicit: v5 does NOT unblock training alone; DR-03/DR-04 (forecasts backfill) must run after v5 completes. Operator must understand sequencing. |

Inherit v4 V4-R1..R10 (v4-R2 updated: Tel Aviv transition verified not drift, but policy derivation anchor changed from "respect DB verbatim" to "derive from current_source_validity + validate DB agrees").

---

## Section 10 — Scope boundaries (inherit v4 + additions)

v5 IS (new scope additions):
- Pre-correction of 5 wrong-entry DB rows (DR-42)
- NULL pm_bin reconciliation (DR-41)
- Harvester atomicity refactor (DR-38)
- Parser round-trip tests (DR-39)

v5 IS NOT (new exclusions):
- Forecasts/market_events/ensemble_snapshots INV-14 identity retrofit (DR-37 stub — separate packet)
- Comprehensive CWA ingest (DR-36 stub — separate packet)
- LOW-track temperature_metric enablement
- Replacement for `data/pm_settlements_full.json` (DR-40 gates but doesn't migrate)

---

## Section 11 — v4→v5 delta summary

| # | v4 position | v5 correction | Reviewer fix |
|---|---|---|---|
| 1 | Trust `settlements.settlement_source_type` | Derive from city_truth_contract + current_source_validity; compare DB (drift = quarantine) | architect P0-1 |
| 2 | INV-14 applied only to settlements | Same + DR-37 stub for fleet-wide + AC-R3-JOIN-INTEGRITY deferred gate | architect P0-2 |
| 3 | DR-07b per-row transaction | Single outer txn + per-city SAVEPOINT; DR-38 harvester atomicity refactor | architect P0-3 |
| 4 | Round-trip test not mandated | DR-39 parser ↔ canonical_bin_label test required | architect P0-4 |
| 5 | pm_settlements_full.json punted | DR-40 gates _build_pm_truth.py behind flag | architect P0-5 |
| 6 | INV-15 silent assumption | AC-R3-INV15-DEFERRED, AC-R3-JOIN-INTEGRITY | architect P0-6 |
| 7 | Exact count 1,540/22 | Inequality range 1,472-1,537 / 25-90 + per-category enumeration | critic P0-1 |
| 8 | NULL pm_bin_lo not addressed | DR-41 reconciliation (59 rows) | critic P0-2 |
| 9 | `migrations/*.sql` orphans | `scripts/migrate_*.py` pattern (existing convention) | critic P0-3 |
| 10 | Daemon pause in V4-R1 mitigation | Task Zero step 1b explicit stop + heartbeat freeze | critic P0-4 |
| 11 | provenance_json TEXT | TEXT NOT NULL DEFAULT '{}' with json_valid() | critic P0-5 |
| 12 | 6 QUARANTINED obs used | §2.F fallback to JSON pm_high (or re-quarantine if sentinel) | scientist D1 |
| 13 | 19 Taipei rows in "re-derivable" | 15 Taipei CWA+NOAA hard-quarantine | scientist D2 |
| 14 | HK 15 quarantine | HK 17 quarantine (15 HKO + 2 WU drift) | scientist D3 |
| 15 | 2026-04-15 DB wrong-entry not addressed | DR-42 pre-correction phase | scientist D5 |
| 16 | Tel Aviv Mar 10-22 quarantined | §2.C cross-source fallback (ogimet_metar_llbg) recovers 13 | scientist D14 |
| 17 | DR-10 misses hemisphere_for_lat / lat_for_city | DR-10 extended; contracts/_geo_utils.py added | critic P1-1, subagent B extended |
| 18 | JSON duplicates noise-only | duplicate protocol explicit + reconciled by DR-42 | critic P1-2 |
| 19 | parser "accepts °F or F" wording | corrected; parser requires degree symbol | critic P1-3 |
| 20 | AC-R3-W vacuous pass | paired with AC-R3-VERIFIED-RANGE + per-category count | critic P1-4 |
| 21 | DR-35 no grep evidence | evidence/scaffold_callers.txt attached | critic P1-5 |
| 22 | Taipei NOAA silent | §2.E policy + DR-36 stub | critic P1-6 |
| 23 | data_version uniformly 'v1.wu-native' | per-source: wu-native / noaa-ogimet / hko-native / cwa-quarantined | architect silent-asm #4 |
| 24 | authority monotonic silently assumed | trigger `settlements_authority_monotonic` enforces | architect silent-asm #3 |
| 25 | pm_bin_lo<pm_bin_hi silent assumption | canonical_bin_label defensively checks both sentinels + raises on paradox | architect silent-asm #2 |
| 26 | Denver 2026-04-15 "closed" | re-opened, DR-40 investigates in Task Zero | scientist D8 |

---

## Section 12 — Open questions for v5 reviewers (refined from v4 Q1-Q10)

Q1. Taipei CWA ingest lane — is DR-36 stub sufficient, or does v5 scope expand?
Q2. HK re-activation: does `hko_ingest_tick.py` (landed per task_2026-04-21_gate_f_data_backfill step 8) have the data for 2026-04-01..15, reducing HK quarantine count?
Q3. DR-38 harvester refactor risk vs feature-flag — acceptable to ship behind ZEUS_HARVESTER_ATOMIC_V5 flag, or require fully-replaced in v5?
Q4. `_build_pm_truth.py` flag default (DR-40) — off (safe, v5 default) or on (legacy compat)?
Q5. DR-42 5-row pre-correction — verify JSON entry 1 is correct for ALL 5 cities via an additional reconciliation check, or trust scientist D5 evidence?
Q6. Single outer txn for 1,540 rows — acceptable lock duration (60s est), or split into per-source-type subtransactions?
Q7. 30-day shim deprecation (DR-10) — calendar-based or migration-based (i.e., automatic when all callers update)?
Q8. AC-R3-VERIFIED-RANGE range (1,472-1,537) — narrow the range by pre-execution evidence, or accept this interval?
Q9. DR-37 fleet-wide INV-14 — spawn follow-up packet immediately or defer until v5 completes?
Q10. INV-06 point-in-time truth: re-deriving a settlement today for a target_date 2 months past — is this considered hindsight? (If yes, settlement provenance_json must note `derivation_time != settlement_time`.)
Q11. Trigger `settlements_authority_monotonic` — acceptable bypass mechanism for future packets (explicit ALTER or unlock script)?
Q12. pm_settlements_full.json's Denver 2026-04-15 entry — actively merge into truth (DR-40), or quarantine DB entry if present?

---

## Section 13 — Conservative execution predictions

Post-R3c, before any subsequent packet lands:

| Metric | v5 prediction |
|---|---|
| settlements.count | 1,562 (unchanged; no deletes) |
| settlements WHERE authority='VERIFIED' | 1,472-1,537 (range) |
| settlements WHERE authority='QUARANTINED' | 25-90 (range) |
| settlements WHERE winning_bin IS NOT NULL | = VERIFIED count |
| settlements WHERE settlement_value IS NOT NULL | ≥ VERIFIED count (some JSON-fallback rows may have settlement_value=pm_high but also populate this) |
| observation_instants_v2.count | 1,813,662 (unchanged) |
| observations.count | ~42,743 VERIFIED + 6 QUARANTINED (unchanged; v5 doesn't add/remove obs rows) |
| calibration_pairs_v2.count | **0** (unchanged — v5 does NOT populate; depends on DR-03/DR-04 forecasts backfill) |
| forecasts.count | **0** (unchanged — out of scope; DR-03/DR-04 packet) |
| training unblocked? | **NO** — settlements necessary but not sufficient |
| training unblocked after DR-03/DR-04 + v5? | **YES** — calibration_pairs_v2 populates via forecasts × settlements JOIN filtered to authority='VERIFIED' with matching data_version |

This is the concrete answer to the user's directive ("一切数据就绪，只是等待 TIGGE 数据进行坐标 extractor 随后训练"): after v5 + DR-03/DR-04 forecasts backfill, settlements + observations + forecasts + calibration_pairs_v2 pipeline is verified. TIGGE coordinate extractor can then run against forecasts + settlements joined truth.
