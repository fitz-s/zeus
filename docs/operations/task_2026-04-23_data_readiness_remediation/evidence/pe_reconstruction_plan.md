# P-E Reconstruction Plan — Pre-Packet

**Packet**: P-E (DELETE + INSERT reconstruction from obs + SettlementSemantics)
**Goal**: replace all 1556 current settlements rows with fresh rows whose authority is earned, not stamped. Re-insert the 5 HIGH-market 2026-04-15 rows that P-G DELETEd (because the DB held LOW-market bins). Target: 1561 rows post-P-E, split V/Q per row-level evidence.
**Date**: 2026-04-23
**Executor**: team-lead
**Pending**: critic-opus PRE-REVIEW on the dry-run plan before any DB mutation. Heavy packet — staged into **dry-run first / pre-review / execute** sub-phases.

---

## Section 1 — Scope and philosophy

### 1.1 Why DELETE+INSERT (not UPDATE)

Per P-0 §8 rule 10 (explicit): "Do not UPDATE the corrupted 1,562 rows in P-E. They are 100% bulk-batch; UPDATE perpetuates broken provenance under new column values. DELETE then INSERT fresh from observation+contract."

P-B + P-F added retrofit + quarantine provenance layers ON TOP of the bulk-batch rows. The core values (settlement_value, winning_bin, market_slug, settlement_source) still came from the ghost writer. P-E's job is to **re-derive these values through the canonical path** (obs → SettlementSemantics → containment → label), stamping fresh provenance that references `decision_time_snapshot_id = obs.fetched_at` (INV-FP-3).

The retrofit history is preserved in git commits, evidence docs, and the App-C traceability table. The DB reflects current canonical truth.

### 1.2 Row count arithmetic

| Source | Count | Action |
|---|---:|---|
| Current DB rows | 1556 | DELETE all, then INSERT reconstructed replacements |
| 2026-04-15 HIGH-market rows (DELETEd by P-G) | 5 | INSERT from pm_settlement_truth.json EARLY indices (London 17°C, NYC 86-87°F, Seoul 21°C+, Tokyo 22°C, Shanghai 18°C) |
| Denver 2026-04-15 synthetic orphan (P-G DELETE) | 0 | Do NOT re-insert (no Gamma market; P-D verified) |
| **Target post-P-E total** | **1561** | |

Note: this revises first_principles.md §7.7's "1,562 target" — post-P-G Denver stays deleted, so 1,561 is the correct post-P-E count. App-C should be updated on P-E closure.

### 1.3 Expected authority distribution (estimated; exact counts determined by dry-run)

Based on P-C post-P-G audit (`pc_agreement_audit_postPG.json`):
- ~1,400–1,420 rows: VERIFIED (containment passes; obs-derived settlement_value) — 1438 matches out of 1507 audited WU-routed rows
- ~140–160 rows: QUARANTINED:
  - 74 rows from P-F's enumerable reason set — these rows will also be QUARANTINED in P-E output (their P-F reasons persist in provenance_json)
  - Additional rows flagged by P-E containment check that weren't in P-F's list (if any)
  - 49 NO_OBS rows — some overlap with P-F AP-4 bucket; the remainder (if any) → QUARANTINE with `pe_no_source_correct_obs`
  - 7 CWA rows — persist QUARANTINE from P-F

### 1.4 Authority assignment rule (per row)

For each candidate (city, target_date) with a populated pm_bin_lo/hi:

```
1. Look up source-family-correct obs via:
     WU → o.source='wu_icao_history'
     NOAA → o.source LIKE 'ogimet_metar_%'
     HKO → o.source='hko_daily_api'
     CWA → no accepted proxy

2. If no source-correct obs exists:
     authority = 'QUARANTINED'
     reason = 'pe_no_source_correct_obs'
     (may carry forward P-F reason if this was one of the 27 AP-4 rows OR 7 CWA rows)
     settlement_value = NULL
     winning_bin = NULL (will be computed from pm_bin_lo/hi at observation rather than at oracle — left NULL since we can't verify)

3. Else (source-correct obs found):
     rounded = SettlementSemantics.for_city(city).assert_settlement_value(obs.high_temp)
     contained = rounded ∈ [pm_bin_lo, pm_bin_hi] (point/range/shoulder-aware)
     if contained:
         authority = 'VERIFIED'
         settlement_value = rounded
         winning_bin = canonical_bin_label(pm_bin_lo, pm_bin_hi, unit)
     else:
         authority = 'QUARANTINED'
         reason = 'pe_obs_outside_bin' (or carries forward P-F reason if applicable)
         settlement_value = rounded   (recorded as evidence, but row is quarantined)
         winning_bin = NULL

4. INV-14 identity fields populated UNCONDITIONALLY (CHECK would reject invalid values):
     temperature_metric = 'high'   (all 1556 current + 5 re-inserts are HIGH markets)
     physical_quantity = 'daily_maximum_air_temperature'
     observation_field = 'high_temp'
     data_version = {
         WU: 'wu_icao_history_v1',
         NOAA: 'ogimet_metar_v1',
         HKO: 'hko_daily_api_v1',
         CWA: 'cwa_no_collector_v0',
     }[settlement_source_type]

5. provenance_json UNCONDITIONALLY includes (INV-FP-1 + INV-FP-3):
     writer = 'p_e_reconstruction_2026-04-23'
     source_family = settlement_source_type
     obs_source = o.source if obs found else None
     obs_id = o.id if obs found else None
     decision_time_snapshot_id = o.fetched_at (UTC ISO) if obs found else None
     rounding_rule = 'wmo_half_up' | 'oracle_truncate' (per source family)
     reconstruction_method = 'obs_plus_settlement_semantics' | 'quarantine_no_obs' | 'quarantine_obs_outside_bin'
     prior_authority = (from current DB row before P-E DELETE)
     prior_quarantine_reason = (from current DB row's $.quarantine_reason if present)
     reconstructed_at = '<UTC ISO>'
     audit_ref = 'pc_agreement_audit_postPG.json / pe_reconstruction_plan.json'
```

### 1.5 Canonical bin label format (post-critic C1)

For the `winning_bin` column, use a consistent label format. The shoulder cases use ENGLISH TEXT form (not unicode ≥/≤) because `src/data/market_scanner.py::_parse_temp_range` silently misparses `≥21°C` as the POINT bin `(21.0, 21.0)` — the regex uses `re.search` not `re.fullmatch`, so prefix characters are ignored. Verified empirically; critic-opus P-E pre-review C1.

| shape | example | format |
|---|---|---|
| point (lo=hi) | `17°C` | `f"{int(lo)}°{unit}"` |
| range (lo<hi) | `86-87°F` | `f"{int(lo)}-{int(hi)}°{unit}"` |
| low shoulder (lo NULL) | `15°C or below` | `f"{int(hi)}°{unit} or below"` |
| high shoulder (hi NULL) | `75°F or higher` | `f"{int(lo)}°{unit} or higher"` |

All four forms round-trip cleanly through `_parse_temp_range` (verified 2026-04-23). Labels are written only when authority='VERIFIED' (we've confirmed the bin contains the obs). For QUARANTINED rows, `winning_bin = NULL`.

---

## Section 2 — Dry-run design

### 2.1 Dry-run script (`evidence/scripts/pe_dryrun.py`)

Read-only: connects to `state/zeus-world.db`, computes the reconstruction plan for all 1561 target rows, emits `evidence/pe_reconstruction_plan.json` with per-row:

```json
{
  "city": "NYC",
  "target_date": "2026-04-15",
  "src": "pm_settlement_truth_early_idx_1520",  // or "current_db"
  "settlement_source_type": "WU",
  "unit": "F",
  "pm_bin_lo": 86.0,
  "pm_bin_hi": 87.0,
  "obs_high_temp": 87.0,
  "obs_source": "wu_icao_history",
  "obs_fetched_at": "2026-04-21T...",
  "rounded": 87,
  "contained": true,
  "new_authority": "VERIFIED",
  "new_settlement_value": 87,
  "new_winning_bin": "86-87°F",
  "new_temperature_metric": "high",
  "new_physical_quantity": "daily_maximum_air_temperature",
  "new_observation_field": "high_temp",
  "new_data_version": "wu_icao_history_v1",
  "new_provenance_json_keys": [...]
}
```

### 2.2 Dry-run output aggregates

- Total candidate rows: 1561 (1556 current + 5 re-insert)
- Per-authority counts (predicted)
- Per-source-type counts
- Per-reason-code counts (among QUARANTINED)
- Sanity checks:
  - Total rows ≤ 1561
  - Every row has exactly one of {VERIFIED, QUARANTINED}
  - Every VERIFIED has all 4 INV-14 fields non-null
  - Every row has provenance_json with `writer` + `reconstruction_method` + `decision_time_snapshot_id` (last may be null only for pure-QUARANTINE-no-obs rows)

### 2.3 Relationship tests (Fitz Constraint — BEFORE implementation)

Per critic's P-E heads-up and Fitz's "relationship tests → implementation → function tests (not reversible)":

Cross-module invariant to test:
> **"For every (city, target_date) with source-correct obs, the reconstructed settlement row is self-consistent: `SettlementSemantics(obs.high_temp)` = settlement_value; settlement_value ∈ [pm_bin_lo, pm_bin_hi] iff authority='VERIFIED'; provenance_json.decision_time_snapshot_id = obs.fetched_at."**

Test file: `tests/test_pe_reconstruction_relationships.py`. Tests use the dry-run script's output as the system under test (not the live DB) until execution phase.

Minimum test cases:
- **T1 Self-consistency (VERIFIED)**: pick 5 random VERIFIED rows from dry-run; verify `round(obs) ∈ [lo,hi]` AND `settlement_value == round(obs)` AND `decision_time_snapshot_id == obs.fetched_at`.
- **T2 Self-consistency (QUARANTINED obs_outside_bin)**: pick the rows with reason `pe_obs_outside_bin`; verify `round(obs) ∉ [lo,hi]` AND `settlement_value == round(obs)` (evidence preserved) AND `authority == 'QUARANTINED'`.
- **T3 Self-consistency (QUARANTINED no_obs)**: pick the 27 AP-4 rows + 7 CWA rows; verify `obs_id IS None` AND `settlement_value IS NULL` AND `decision_time_snapshot_id IS None`.
- **T4 INV-14 completeness**: every reconstruction plan entry has non-null `temperature_metric`, `physical_quantity`, `observation_field`, `data_version`.
- **T5 HKO oracle_truncate**: every HKO-source row has `rounding_rule='oracle_truncate'` AND `settlement_value == floor(obs.high_temp)`.
- **T6 Source-family routing fail-closed**: no WU-labeled row has obs from `ogimet_metar_*`; no NOAA-labeled row has obs from `wu_icao_history`; etc.
- **T7 Re-insert 5 2026-04-15 rows present**: the plan includes 5 (city, '2026-04-15') entries for London / NYC / Seoul / Tokyo / Shanghai with HIGH-market bins from JSON EARLY indices.
- **T8 Total count = 1561**: the plan partitions to exactly 1561 (city, target_date) pairs.

### 2.4 No DB mutation in dry-run phase

Phase P-E.dry_run produces only:
- `evidence/scripts/pe_dryrun.py` (script)
- `evidence/pe_reconstruction_plan.json` (plan document — per-row reconstruction)
- `tests/test_pe_reconstruction_relationships.py` (relationship tests; read the JSON, not the DB)

These are submitted to critic-opus for pre-review. Execution phase follows critic APPROVE.

---

## Section 3 — Execution design (after critic APPROVE on dry-run)

### 3.1 Staged execution

Given scope, execute in per-city transactions (51 cities × 1 TXN each):

```
FOR city IN cities:
    BEGIN IMMEDIATE
    DELETE FROM settlements WHERE city = :city
    FOR each (target_date, plan_row) in city_plan:
        INSERT INTO settlements (city, target_date, market_slug, winning_bin,
                                settlement_value, settlement_source, settled_at,
                                authority, pm_bin_lo, pm_bin_hi, unit,
                                settlement_source_type, temperature_metric,
                                physical_quantity, observation_field, data_version,
                                provenance_json)
          VALUES (...)
    POST-VERIFY inside TXN:
        SELECT COUNT(*) WHERE city=:city == city_expected_count
    COMMIT (or ROLLBACK on mismatch)
```

### 3.2 Transaction boundary decision: per-city

- ~20–60 rows per city × 51 cities = 1561 total
- Per-city TXN bounds the blast radius: a failure on city K doesn't corrupt cities 1..K-1
- Enables resumability (re-run restarts from the first city with stale authority)
- Avoids monolithic 1561-row INSERT (critic's P-E heads-up #4)

### 3.3 Pre-mutation snapshot

`state/zeus-world.db.pre-pe_2026-04-23` + md5 sidecar. This is the **4th rollback point** in the workstream chain.

### 3.4 Post-execution validation

- Row count: 1561 exactly
- Authority partition: matches dry-run predictions
- Per-source-type partition: WU/NOAA/HKO/CWA totals match expectations (+5 for 2026-04-15 re-inserts in WU since those 5 all have settlement_source_type='WU')
- INV-14 completeness: every row has non-null temperature_metric / physical_quantity / observation_field / data_version
- provenance_json integrity: every row has writer='p_e_reconstruction_2026-04-23' + reconstruction_method + decision_time_snapshot_id (non-null for VERIFIED and obs-available rows)
- Relationship tests re-run against the LIVE DB (not just plan.json) → all T1–T8 pass
- Pytest unchanged: `test_schema_v2_gate_a` + `test_canonical_position_current_schema_alignment` stable

---

## Section 4 — Q1-Q10

**Q1 (invariant)**:
- INV-03 (append-first projection): DELETE+INSERT through canonical path is the clean re-appending pattern; P-0 §8 rule 10 endorses.
- INV-06 (point-in-time truth): `decision_time_snapshot_id = obs.fetched_at` locks in the point-in-time observation that produced this settlement.
- INV-FP-1 (provenance chain): every row carries unbroken writer → obs → rounding → bin → authority trail.
- INV-FP-3 (temporal causality): decision_time_snapshot_id enforces the rule.
- INV-FP-4 (semantic integrity): `SettlementSemantics.assert_settlement_value()` is the MANDATORY gate per `src/contracts/settlement_semantics.py:95-118`.
- INV-FP-5 (authority earned): new rows stamp authority only if containment passes; else QUARANTINE with reason.
- INV-FP-6 (write routes registered): P-E is its own registered script with explicit writer identity in provenance_json.
- INV-FP-7 (source role boundaries): strict per-source-family routing; no cross-family fallback.
- INV-14 (metric identity): all 4 fields populated on every INSERT; schema CHECK (permissive) allows NULL but code path must not.

**Q2 (fatal_misread)**:
- `wu_website_daily_summary_not_wu_api_hourly_max`: P-E uses `wu_icao_history` and labels `data_version='wu_icao_history_v1'`, NOT claiming equivalence to WU website. Preserves the fatal_misread as active.
- `airport_station_not_city_settlement_station`: P-E uses city.wu_station (airport) per existing config; does NOT re-label airport as city. The fatal_misread is about inference, not about whether airport stations are used.
- `daily_day0_hourly_forecast_sources_are_not_interchangeable`: P-E uses settlement_daily_source obs ONLY. No cross-role collapse.
- `hong_kong_hko_explicit_caution_path`: HKO path uses `oracle_truncate` (not wmo_half_up). Preserves.
- `hourly_downsample_preserves_extrema`: not applicable (obs.high_temp is already a daily extremum).
- `code_review_graph_answers_where_not_what_settles`: P-E uses SettlementSemantics + obs, not graph output.

**Q3 (single-source-of-truth)**: 
- Row counts: SQL against state/zeus-world.db + pm_settlement_truth.json for 5 re-inserts
- Obs selection: PC-C routing rules (WU→wu_icao_history etc.) — same as pc_agreement_audit.py
- Rounding: `src/contracts/settlement_semantics.py:69,79` inlined (wmo_half_up = floor(x+0.5); oracle_truncate = floor(x))
- INV-14 values: enumerable constants defined in plan §1.4
- Provenance_json keys: enumerable list defined in plan §1.4

**Q4 (first-failure)**:
- Dry-run SQL error: HALT, report; no DB mutation happened yet.
- Any per-city TXN fails pre-verify or post-verify: ROLLBACK that TXN; HALT; report which city.
- Relationship test fails post-execution: ROLLBACK via snapshot restore.

**Q5 (commit boundary)**: 51 transactions, one per city. Each:
- BEGIN IMMEDIATE
- DELETE city rows
- N INSERTs (where N = city's row count)
- SELECT COUNT post-verify
- COMMIT or ROLLBACK

**Q6 (verification)**: 8 relationship tests (T1-T8 §2.3) + schema pytest + live-DB sanity (count, authority partition, INV-14 completeness). critic-opus reproduces dry-run via re-running the script.

**Q7 (new hazards)**:
- **Hazard 1**: 51 transactions mean 51 commit points. Partial failure leaves DB in split state (some cities re-inserted, others still pre-P-E). **Mitigation**: resumable via per-city idempotency — if a city is already "new-style" (writer='p_e_reconstruction...' in provenance_json), skip.
- **Hazard 2**: the 5 2026-04-15 re-inserts have no PRE-P-E row to reference in prior_authority. **Mitigation**: plan JSON and INSERTs carry `prior_authority='deleted_by_p_g_low_market_contamination'` instead of a real prior value.
- **Hazard 3**: winning_bin format change (canonical `"86-87°F"` vs bulk-writer's `"86-87"` sans-unit). Downstream consumers (calibration, replay) may parse bin labels. **Mitigation**: check `_parse_temp_range` caller compatibility; grep for bin-label consumers pre-execution. If any break, ADD a conversion step (label_legacy → label_canonical) in a separate packet.
- **Hazard 4**: DELETE cascades through FK? Schema audit (from P-B): no foreign keys point to settlements.id. No cascade. ✓

**Q8 (closure)**:
- dry-run phase: `evidence/pe_reconstruction_plan.md` (this doc), `evidence/scripts/pe_dryrun.py`, `evidence/pe_reconstruction_plan.json`, `tests/test_pe_reconstruction_relationships.py`. PRE-REVIEW gate.
- execution phase: `evidence/pe_execution_log.md`, `evidence/scripts/pe_reconstruct.py`, live-DB validation outputs. POST-EXECUTION gate.

**Q9 (decision reversal)**: 
- Reverses first_principles.md §7.7 row count from 1,562 → **1,561** (Denver stays deleted).
- Not reviving any removed pattern.

**Q10 (rollback)**:
- Per-TXN ROLLBACK: within BEGIN/COMMIT, failure reverses.
- Per-packet ROLLBACK: restore `state/zeus-world.db.pre-pe_2026-04-23` snapshot.
- Partial-completion recovery: per-city idempotency allows resuming from failed city.
- Forward-compat: P-H may run in parallel; P-H is harvester atomicity refactor (feature-flagged); no DB writes expected from P-H during P-E.

---

## Section 5 — What critic-opus should pre-verify (dry-run phase)

1. **Row count math** (1561 = 1556 current + 5 re-inserts - 0 Denver): confirm pe_reconstruction_plan.json has exactly 1561 entries.
2. **INV-14 completeness**: every entry in plan.json has non-null `new_temperature_metric` / `new_physical_quantity` / `new_observation_field` / `new_data_version`.
3. **decision_time_snapshot_id presence**: for every entry with `new_authority='VERIFIED'` OR where obs was found, `new_provenance_json.decision_time_snapshot_id` is non-null and parses as ISO8601.
4. **Source-family routing strictness**: re-run the 6 routing rules from §1.4; confirm the dry-run respects them (no cross-family obs substitution).
5. **HKO oracle_truncate**: every Hong Kong HKO row uses `rounding_rule='oracle_truncate'` in provenance_json.
6. **Relationship tests**: the 8 T1-T8 tests in `test_pe_reconstruction_relationships.py` all pass against the plan.json output.
7. **Authority distribution realism**: dry-run predicts ~1400-1420 VERIFIED / ~140-160 QUARANTINED — in the expected range per P-C match rates.
8. **Re-insert 5 HIGH-market rows**: London 17°C, NYC 86-87°F, Seoul 21°C+, Tokyo 22°C, Shanghai 18°C all present with correct pm_bin_lo/hi from JSON EARLY indices (1513/1520/1517/1530/1532).

---

**Phase P-E.dry_run: awaiting critic-opus PRE-REVIEW. No DB mutation until APPROVE + execution plan follow-up.**
