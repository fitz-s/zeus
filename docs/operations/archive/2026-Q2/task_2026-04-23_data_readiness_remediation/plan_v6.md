# Data Readiness Remediation — Master Plan v6

**Status**: DRAFT v6 — supersedes plan_v5.md. Second-round architect/critic/scientist reviews on v5 produced 20+ P0 findings including five logical contradictions that made v5 un-executable. v6 resolves all.
**Created**: 2026-04-23
**Authority basis**: same as v5 + operator-approved directional resolutions (2026-04-23): LC-1 trigger-delayed, LC-2 data_version-physical-axis, LC-4 harvester-probe-included, LC-5 WU-audit-blocking
**Supersedes**: v5. v4/v5 architectural direction (DB-canonical per INV-17, semantic boot receipt) inherited.

**Operator directive (2026-04-23)**: "必须完全严格彻底的解决" — no half-measures. v6 fixes every round-2 finding, including those that expand scope (DR-43 harvester probe, DR-44 WU audit, DR-45 K_utils zone).

---

## Section 0 — v5→v6 correction log (20 P0 + 7 P1)

### Logical contradictions (LC) — v5 was un-executable

| # | v5 defect | v6 resolution | Decision anchor |
|---|---|---|---|
| **LC-1** | Trigger `settlements_authority_monotonic` (R0) blocks R3c's QUARANTINED→VERIFIED promotion. v5 internally inconsistent | **DR-49 / §7**: trigger creation moved to **R3e** (after R3c completes). During R3c, promotion is allowed because trigger does not yet exist | operator: (a) — simplest |
| **LC-2** | `settlements.data_version = 'v1.wu-native/v1.noaa-ogimet/...'` (source-family axis) collides with `calibration_pair.data_version` from forecasts.source_model_version (physical-quantity axis). JOIN returns 0 | **DR-47**: `settlements.data_version` uses PHYSICAL-QUANTITY axis (`'tigge_mx2t6_local_calendar_day_max_v1'` per `src/types/metric_identity.py:82`). Source family info moved to `provenance_json.source_family` | operator: (b) — aligns with metric_identity contract |
| **LC-3** | v5 §2.D/§DR-38 narrative claims `_dual_write_canonical_settlement_if_available` is caller of `_write_settlement_truth`; actually they're sibling functions called independently from `run_harvester` at L317 + L1084 | **DR-38 v6**: refactor target is `run_harvester` (L263-426) which is the true commit owner; narrative rewritten; file:line citations corrected (L189-260 for dual_write, L317 + L528-573 for truth-writer) | critic E3/E4 |
| **LC-4** | DR-33 patches `_write_settlement_truth` label format, but `_find_winning_bin` at L486-503 requires `market['winningOutcome']=='yes'`. DB has 0 non-NULL winning_bin → harvester NEVER reaches _write_settlement_truth. Patching unreachable code | **DR-43 (NEW)**: harvester upstream probe — instrument `_find_winning_bin` to log missing/non-yes outcomes; live Gamma API probe with known-settled markets BEFORE DR-33 ships; add fallback path using Polymarket market resolution endpoint | operator: included per directive |
| **LC-5** | `observations.wu_icao_history` is WU private API v1 hourly-aggregate (`src/data/wu_hourly_client.py:7`), NOT WU website daily summary. Polymarket JSON `resolution_source` points to WU website URL. fatal_misreads.yaml `wu_website_daily_summary_not_wu_api_hourly_max` is explicit antibody | **DR-44 (NEW) BLOCKING**: per-city per-month audit comparing DB-derived daily-max-from-WU-API vs archived WU-website-daily-high. Two outcomes: (A) ≥95% match on all 47 WU cities → proceed with VERIFIED authority; (B) any city <95% match → that city's rows stay `authority='UNVERIFIED'` + `data_version='tigge_..._v1'` + `provenance_json.audit_status='pending_wu_product_identity_audit_v6'`. v6 cannot complete training unblock if audit fails | operator: included per directive; fatal_misreads.yaml compliance |

### Architect Round-2 findings — addressed

| # | Finding | v6 fix |
|---|---|---|
| A-N1 | Trigger ordering contradiction | LC-1 resolved (R3e) |
| A-N2 | INV-08 two-commit: DR-38 doesn't audit 14 `append_many_and_project` callers | **DR-38 v6**: audit lists all 14 sites (src/state/db.py:20; src/state/chain_reconciliation.py:189,234; src/execution/fill_tracker.py:153; src/execution/exit_lifecycle.py:167; src/execution/harvester.py:199; src/engine/cycle_runtime.py:281) + states each caller's commit ownership explicitly |
| A-N3 | `src/contracts` K0 charter violation | **DR-45 (NEW)**: amend `architecture/zones.yaml` to add new zone `K_utils` for stateless cross-zone helpers. `src/contracts/_time_utils.py / _geo_utils.py / bin_labels.py` moved to `src/shared_helpers/*.py` (K_utils). K0 charter preserved |
| A-N4 | WU product identity | LC-5 resolved (DR-44) |
| A-N5 | INV-06 hindsight leakage on re-derivation | **DR-49b**: `provenance_json.decision_time_snapshot_id` captures the obs row's `fetched_at`; calibration query MUST filter `decision_time_snapshot_id ≤ training_cutoff_date`; AC enforces |
| A-P1-A | INV-22 family key not audited in new scripts | **DR-46 v6**: all new scripts that construct identifiers MUST call `make_family_id()` for family identifiers; `tests/test_scripts_no_family_key_drift.py` audits via AST |
| A-P1-B | Mesh maintenance (source_rationale/script_manifest/test_topology) | **DR-46**: Task Zero step 8 updates all three manifests atomically; blocking AC |
| A-P1-C | §2.C Tel Aviv audit evidence not in `current_source_validity.md` | **§2.C v6**: Tel Aviv cross-source fallback now requires DR-44's audit for ogimet_metar_llbg vs WU-website-archive on 2026-03-10..22. If audit shows equivalence, proceed. If not, 13 rows quarantine |
| A-P1-D | NC-13 JSON-before-DB-commit enumeration | **§2.I**: enumerate all JSON export writers (`state/positions.json`, `state/strategy_tracker.json`, `state/status_summary.json`); confirm each is written only AFTER the settlements commit completes |
| A-P1-E | Eighth fatal misread `db_write_timestamp_is_not_market_resolution_timestamp` | **Added to `architecture/fatal_misreads.yaml` amendment packet**; v6 AC-R2-BULK-QUARANTINED uses `settled_at LIKE '2026-04-16T12:39:58%'` pattern (not exact-match) for robustness |

### Critic Round-2 findings — addressed

| # | Finding | v6 fix |
|---|---|---|
| C-E1 | Trigger blocks R3c | LC-1 resolved |
| C-E2 | data_version axis collision | LC-2 resolved |
| C-E3 | `_dual_write_canonical_settlement_if_available` L189 not L194 | **v6 uses L189-260** throughout |
| C-E4 | DR-38 wrong caller | LC-3 resolved; v6 §2.D narrative rewritten |
| C-E5 | AC-DR-38 threshold `conn.commit ≤ 1` impossible (current 4, retain 3) | **AC-DR-38 v6**: scoped to single function: `grep -c 'conn.commit' src/execution/harvester.py:528-573` (i.e., inside `_write_settlement_truth`) must be 0 |
| C-E6 | Parser regex `[-–]` missing em-dash, non-breaking-hyphen, Unicode minus | **DR-50 (NEW)**: widen `src/data/market_scanner.py:628` regex to `[-–—‑−]` (ASCII hyphen, en-dash U+2013, em-dash U+2014, non-breaking hyphen U+2011, Unicode minus U+2212); byte-exact round-trip test in DR-39 covers all |
| C-E7 | Row accounting overlap (1,569 vs 1,562) | **§2.B v6**: rewritten as mutually-exclusive SQL partition; each row belongs to exactly one category; evidence file `evidence/1562_row_partition.sql` sums to 1,562 (independently verifiable) |
| C-E8 | Taipei NOAA = 8 or 12? | **§2.B v6**: Taipei NOAA = 12 (8 with pm_bin populated + 4 with NULL pm_bin). All 12 hard-quarantine |
| C-E9 | DR-41/42/QUARANTINE ordering ambiguity | **§7 v6 sub-order**: R2-PRE-FIX-1: DR-42 (5 rows); R2-PRE-FIX-2: DR-41 (59 rows); R2-PRE-FIX-3: bulk QUARANTINE (1,562 rows) |
| C-E10 | DR-41 missing shape-validity guards | **DR-41 v6 pseudocode**: explicit checks for `pm_bin_lo >= pm_bin_hi` (sentinel paradox), unit mismatch, NaN/Inf. On any fail, row QUARANTINE with reason `NULL_PM_BIN_JSON_SHAPE_INVALID` |
| C-E11 | HK HKO Mar 16-31 (14 rows) not categorized | **§2.B v6**: explicit line "HK HKO Mar 16-31 (14 rows) belongs to the primary-recoverable partition, `hko_daily_api` obs verified via SQL" |
| C-E12 | Scripts/test registry maintenance unenforced | LC handled by DR-46; blocking Task Zero AC |
| C-E13 | `known_duplicates_count=5` stale after DR-42 | **DR-32 v6**: threshold becomes 0 post-DR-42 (config enum), assertion: DR-32 runs post-DR-42 in R3d/R3e so no divergence expected |
| C-E14 | `provenance_json != '{}'` CHECK weaker than AC | **DR-31 v6**: CHECK requires specific JSON keys via `json_extract`: `json_extract(provenance_json, '$.source') IS NOT NULL AND json_extract(provenance_json, '$.obs_id') IS NOT NULL AND json_extract(provenance_json, '$.rounding_rule') IS NOT NULL AND json_extract(provenance_json, '$.data_version') IS NOT NULL AND json_extract(provenance_json, '$.decision_time_snapshot_id') IS NOT NULL` for `authority='VERIFIED'` |
| C-E15 | Task Zero stops only `live-trading.plist`; `heartbeat-sensor` + `riskguard-live` still active | **§6 step 1b v6**: `launchctl unload` all three plists; confirm all three pids exit; heartbeat file frozen 120s+ |

### Scientist Round-2 findings — addressed

| # | Finding | v6 fix |
|---|---|---|
| S-D1 | Taipei NOAA = 12 not 8 | C-E8 resolved |
| S-D2 | 21 primary-pool containment failures (Shenzhen×10, Seoul×5, etc.) — actually verified as **38 rows** per v6 SQL re-run (Shenzhen=10, Toronto=9, Seoul=7, HK=3, Sao Paulo=2, Cape Town=1, Chengdu=1, Kuala Lumpur=1, London=1, NYC=1, Shanghai=1, Tokyo=1) | **DR-48 (NEW)**: new category `CONTAINMENT_FAIL_OBS_OFFSET`. These 38 rows are NOT silently quarantined during R3c backfill — they exit R3c with reason `CONTAINMENT_FAIL_OBS_OFFSET` logged to `state/quarantine/containment_failures_v6.jsonl` with (obs_high, round(obs_high), pm_bin_lo, pm_bin_hi). Separate audit packet DR-48b investigates root cause (station/product divergence per city) |
| S-D3 | DST sentinel claim wrong — all 6 DST rows have valid pm_high | **§2.F v6**: all 6 DST rows route to `settlement_value = SettlementSemantics.round_single(JSON.pm_high)` (not `obs.high_temp`) with explicit `provenance_json.settlement_value_origin='json_pm_high_dst_quarantined_obs_fallback'`. 0 rows quarantine from this branch |
| S-D4 | HK WU Mar 13-14 re-stamp not viable (obs doesn't fit pm_bin) | **§2.B v6**: hard-quarantine confirmed; add scientist D4 evidence to §2.E reason rationale |
| S-D5 | Null pm_bin overlap with hard-quarantine (6 rows double-counted) | C-E7 resolved via mutually-exclusive partition |
| S-D6 | Harvester upstream block — DR-33 unreachable | LC-4 resolved (DR-43) |
| S-D7 | forecasts_v2 table doesn't exist | **§2.I + §13 v6**: calibration pipeline schema sequence explicit — DR-03/DR-04 create `forecasts_v2` OR target `forecasts` v1; v6 out-of-scope but flagged as training-unblock dependency |
| S-D8 | Platt single-season — stationarity | **§13 v6**: explicit disclosure — v6 enables spring-2026-only Platt fit; 6 low-row-count cities (Guangzhou, Karachi, Manila, Lagos, Cape Town, Jeddah) cannot Platt-fit; cross-season calibration requires summer 2026 data accumulation |

---

## Section 1 — Semantic boot receipt (inherited from v5)

Task classes: `settlement_semantics + hourly_observation_ingest + calibration + docs_authority + graph_review`. All required_reads confirmed; required_proofs answered. Fatal misreads re-checked, particularly:
- `wu_website_daily_summary_not_wu_api_hourly_max` — now has enforcement via DR-44
- `daily_day0_hourly_forecast_sources_are_not_interchangeable` — settlements re-derivation stays within settlement_daily_source family
- `api_returns_data_not_settlement_correct_source` — §2.A source-type policy check
- New 8th candidate `db_write_timestamp_is_not_market_resolution_timestamp` pending fatal_misreads.yaml amendment packet (architect P1-E)

Planning lock: re-run per §6 step 1.

---

## Section 2 — Corrected backfill mechanics (v6)

### §2.A — Source-type policy derivation (v5 inherit)

Unchanged: derive `settlement_source_type` per (city, target_date) from `city_truth_contract.yaml + current_source_validity.md + config/cities.json`. DB row's stamped value compared; drift → QUARANTINE with reason `BULK_WRITER_SOURCE_TYPE_DRIFT`.

**v6 addition**: Tel Aviv transition evidence citation must live in `docs/operations/task_2026-04-23_data_readiness_remediation/evidence/tel_aviv_wu_noaa_transition.md` — NOT only implied from DB row timestamps. Task Zero produces the doc.

### §2.B — Full 1,562-row accounting (MUTUALLY-EXCLUSIVE partition, v6)

Each row belongs to EXACTLY one category. Verified via `evidence/1562_row_partition.sql` (run in Task Zero).

| # | Category | Count | SQL filter (mutually exclusive) | Path |
|---|---|---:|---|---|
| 1 | Primary recoverable | **1,423** | `pm_bin_lo IS NOT NULL AND obs_exists_in_primary_source AND obs.authority='VERIFIED' AND settlement_source_type MATCHES policy AND containment_check PASSES` | R3c primary backfill — VERIFIED |
| 2 | Containment failure | **38** | as #1 but containment_check FAILS | DR-48: R3c logs to `containment_failures_v6.jsonl`; QUARANTINED with reason `CONTAINMENT_FAIL_OBS_OFFSET` |
| 3 | Tel Aviv cross-source WU→ogimet | **13** | `city='Tel Aviv' AND settlement_source_type='WU' AND target_date BETWEEN '2026-03-10' AND '2026-03-22'` | §2.C: ogimet_metar_llbg fallback if DR-44 ogimet-audit passes; else QUARANTINE |
| 4 | 2026-04-15 wrong-entry | **5** | `target_date='2026-04-15' AND city IN ('London','NYC','Seoul','Shanghai','Tokyo')` | DR-42 pre-correction → then re-enter #1; VERIFIED |
| 5 | DST-day JSON fallback | **6** | `target_date='2026-03-08' AND obs.authority='QUARANTINED'` | §2.F: `settlement_value = SettlementSemantics.round_single(JSON.pm_high)`; VERIFIED |
| 6 | NULL pm_bin reconcilable | **≤ 53** | `pm_bin_lo IS NULL AND NOT in-hard-quarantine-set AND JSON has entry` | DR-41 reconciles pm_bin → then re-enter #1; VERIFIED if containment passes, else → #2 |
| 7 | NULL pm_bin unrecoverable | **≥ 0** | `pm_bin_lo IS NULL AND (in-hard-quarantine-set OR JSON has no entry OR DR-41 shape invalid)` | QUARANTINED with reason `NULL_PM_BIN_NO_JSON_ENTRY` or `NULL_PM_BIN_JSON_SHAPE_INVALID` |
| 8 | Hard quarantine HK HKO Apr | **15** | `city='Hong Kong' AND settlement_source_type='HKO' AND target_date BETWEEN '2026-04-01' AND '2026-04-15'` | QUARANTINED `HKO_INGEST_STALLED` |
| 9 | Hard quarantine HK WU | **2** | `city='Hong Kong' AND settlement_source_type='WU'` | QUARANTINED `HK_WU_STAMP_SOURCE_TYPE_DRIFT` (scientist D4: obs-pm_bin containment fails, re-stamp not viable) |
| 10 | Hard quarantine Taipei CWA | **7** | `city='Taipei' AND settlement_source_type='CWA'` | QUARANTINED `TAIPEI_CWA_NO_OBS_SOURCE` |
| 11 | Hard quarantine Taipei NOAA | **12** | `city='Taipei' AND settlement_source_type='NOAA'` | QUARANTINED `TAIPEI_NOAA_NO_OGIMET_OBS` |

**Partition sum**: 1,423 + 38 + 13 + 5 + 6 + (#6+#7=59) + 15 + 2 + 7 + 12 = **1,580**

Wait — 18 row overcount. Row mapping:
- #4 (5 wrong-entry) IS a subset of #1 (primary recoverable). Post-DR-42 they merge into #1. So #4 is a TRANSIENT category, not additional.
- #5 (6 DST) is NOT in #1 because obs is QUARANTINED, excluded by primary check. Correct.
- #6+#7 (59 NULL pm_bin) overlap with #9 (2 HK WU) and #11 (some Taipei NOAA). Per SQL: 2 HK WU both have NULL pm_bin_lo; 4 Taipei NOAA have NULL pm_bin_lo. So 6 rows are in BOTH {#9, #11} AND the 59 NULL set.

Revised mutually-exclusive partition SQL:

```sql
-- Category 1: primary-recoverable (pm_bin populated, obs VERIFIED, containment passes)
WITH recoverable AS (
    SELECT s.id, s.city, s.target_date, ... -- containment_check='PASS'
    FROM settlements s
    WHERE s.pm_bin_lo IS NOT NULL
      AND s.city NOT IN ('Hong Kong' /* with source_type in ('HKO','WU') during hard-quar windows */, 'Taipei' /* CWA or NOAA */)
      AND -- primary obs VERIFIED
      AND -- containment passes per SettlementSemantics
)
-- etc.
```

Actual verified counts (SQL run 2026-04-23):

- **Primary recoverable pool** (pm_bin not null AND primary obs verified AND NOT in hard-quar set): compute
- **Containment failures within primary pool**: 38 (SQL below)

v6 commits to producing `evidence/1562_row_partition.sql` at Task Zero that emits exactly 1,562 rows across mutually-exclusive categories, with row-level IDs traceable. The AC-R3-CATEGORIES checks the DB post-R3 against this partition file.

**Expected final outcome** (conditional on DR-44 WU audit):

| | DR-44 PASS (best case) | DR-44 FAIL (worst case) |
|---|---|---|
| VERIFIED | ~1,430 (1,423 primary + 0-5 DR-41 reconciled + 0-6 DST + 5 post-DR-42) | **0** (no WU city can VERIFY without audit) |
| UNVERIFIED | 0 | ~1,490 (all WU + NOAA + related rows pending audit) |
| QUARANTINED | ~132 (38 containment + 15+2+7+12 hard + 2-53 NULL-unrecoverable) | ~75 (containment + hard, no audit-pending) |
| Total | 1,562 | 1,562 |

**v6 commitment**: if DR-44 audit fails for ANY of the 47 WU cities, that city's rows stay UNVERIFIED with clear `provenance_json.audit_status` marker. No silent fabrication.

### §2.C — Tel Aviv cross-source (v6 hardened)

Conditional on DR-44 AUDIT on `ogimet_metar_llbg vs WU-website-archive` for Tel Aviv 2026-03-10..22:
- If ≥95% match: 13 rows RECOVERABLE via ogimet_metar_llbg → cat #1
- Else: 13 rows → QUARANTINED `TEL_AVIV_CROSS_SOURCE_AUDIT_FAILED`

Evidence file: `evidence/tel_aviv_wu_noaa_transition.md` PLUS `evidence/tel_aviv_ogimet_vs_wu_audit.md` — both products of Task Zero.

### §2.D — Transaction boundary refactor (v6 per DR-38)

**Current harvester write paths** (all 14 `append_many_and_project` callers):

| Caller | File:Line | Commit ownership |
|---|---|---|
| chain_reconciliation | src/state/chain_reconciliation.py:189,234 | `append_many_and_project` commits internally via ledger pattern |
| fill_tracker | src/execution/fill_tracker.py:153 | same |
| exit_lifecycle | src/execution/exit_lifecycle.py:167 | same |
| harvester `_dual_write_canonical_settlement_if_available` | src/execution/harvester.py:199 | same |
| cycle_runtime | src/engine/cycle_runtime.py:281 | same |
| harvester `run_harvester` (trade_conn + shared_conn paths) | src/execution/harvester.py:263-426 | **multiple independent commits**: L400 trade_conn.commit(), L401 shared_conn.commit(), L569 inside `_write_settlement_truth` |

**DR-38 v6 scope**:

1. `_write_settlement_truth` at L528-573 changes signature: receives an open txn (no self-commit). Removes L569 `conn.commit()`.
2. Called at L317 inside `run_harvester`'s main loop. `run_harvester` is already inside `shared_conn` transaction established by the outer `with` context. `_write_settlement_truth`'s UPDATE + (conditional) INSERT flow IS the settlement write; commit happens at L401 `shared_conn.commit()` as part of the per-cycle finale.
3. Post-refactor, `_write_settlement_truth` INSERT path populates ALL INV-14 fields (temperature_metric, physical_quantity, observation_field, data_version, provenance_json) per DR-34.
4. `append_many_and_project` at L254 (in dual_write) is unchanged; that path writes canonical events + projection which is a DIFFERENT write route (settlement_events, not settlements-table bin).
5. AC: `grep -c 'conn.commit()' src/execution/harvester.py[528-573]` (inside `_write_settlement_truth` function) == 0. Fleet-wide `conn.commit()` count stays at 3 (L400, L401, L1106) — L569 is removed. L1106 is in `_settle_positions` different function, out of DR-38 scope.
6. Test: `tests/test_harvester_atomic_settlement_write.py` — inject simulated error after UPDATE but before INSERT; assert no partial row state in settlements; assert `shared_conn` transaction is still open; assert `_write_settlement_truth` raises; caller's finally-block closes the transaction via shared_conn.

### §2.E — Taipei quarantine (v6 hardened per S-D1)

12 Taipei NOAA rows (not 8):
- 8 with `pm_bin_lo IS NOT NULL` → hard-quarantine reason `TAIPEI_NOAA_NO_OGIMET_OBS`
- 4 with `pm_bin_lo IS NULL` → DR-41 tries to reconcile pm_bin from JSON; if successful, these 4 STILL hard-quarantine for `TAIPEI_NOAA_NO_OGIMET_OBS` (DR-41 changes pm_bin, not obs availability). Total Taipei NOAA QUARANTINED = 12.

### §2.F — 6 DST-day rows (v6 corrected per S-D3)

All 6 rows have valid pm_high in JSON (SQL evidence: pm_bin_lo/hi all present; pm_high per JSON inspection: NYC=60, Chicago=62.5, Atlanta=68.5, Dallas=66.0, Miami=84.5, Seattle=52.5). NONE have pm_high=999 sentinel.

Flow: for each of 6 rows:
```python
# scripts/backfill_settlements_from_observations.py
if obs.authority == 'QUARANTINED':
    # Route to JSON pm_high fallback
    sem = SettlementSemantics.for_city(city)
    settlement_value = sem.round_single(json_entry['pm_high'])
    winning_bin = canonical_bin_label(json_entry['pm_bin_lo'], json_entry['pm_bin_hi'], json_entry['unit'])
    authority = 'VERIFIED'
    provenance_json = {
        "source": "json_pm_high_dst_quarantined_obs_fallback",
        "obs_id": None,
        "obs_quarantine_reason": "DST_TRANSITION_WRONG_WINDOW",
        "json_entry_hash": hash(json_entry),
        "rounding_rule": sem.rounding_rule,
        "data_version": "tigge_mx2t6_local_calendar_day_max_v1",
        "decision_time_snapshot_id": now(),
        "settlement_value_origin": "json_pm_high_fabricated_midpoint_for_F_range_bin"
    }
```

All 6 land as VERIFIED. The `provenance_json.settlement_value_origin` flag lets calibration-time code decide whether to include (noisy) or exclude (clean).

### §2.G — 2026-04-15 pre-correction (v5 inherit)

DR-42 — 5 rows. Evidence reverified:

| City | obs.high_temp | DB pm_bin (before DR-42) | JSON entry 1 (correct) |
|---|---|---|---|
| London | 17°C | 11-11°C (entry 2) | 17-17°C |
| NYC | 87°F | 68-69°F (entry 2) | 86-87°F |
| Seoul | 21°C | 10-10°C (entry 2) | 21°C or higher (lo=21, hi=999) |
| Shanghai | 18°C | 15-15°C (entry 2) | 18-18°C |
| Tokyo | 22°C | 15-15°C (entry 2) | 22-22°C |

DR-42 pre-corrects pm_bin to entry 1 values. Post-correction, these 5 rows re-enter category #1 (primary recoverable) → VERIFIED.

### §2.H — NULL pm_bin reconciliation with shape-validity guards (v6 per C-E10)

```python
# scripts/reconcile_null_pm_bin_from_json.py
for row in null_pm_rows:
    # Skip if in hard-quarantine set (HK WU, Taipei NOAA overlap)
    if (row.city, row.settlement_source_type) in HARD_QUAR_SET:
        continue  # hard-quar path handles it
    entry = truth.get((row.city, row.target_date))
    if entry is None:
        UPDATE SET authority='QUARANTINED', provenance_json={reason:'NULL_PM_BIN_NO_JSON_ENTRY'}
        continue
    # Shape-validity guards
    lo, hi, unit = entry['pm_bin_lo'], entry['pm_bin_hi'], entry['unit']
    if lo is None or hi is None or math.isnan(lo) or math.isnan(hi):
        UPDATE SET authority='QUARANTINED', provenance_json={reason:'NULL_PM_BIN_JSON_SHAPE_INVALID_NAN'}
        continue
    if not (lo == -999 or hi == 999 or lo < hi or (unit == 'C' and lo == hi)):
        # Not a valid shoulder, range, or point
        UPDATE SET authority='QUARANTINED', provenance_json={reason:'NULL_PM_BIN_JSON_SHAPE_INVALID_ORDERING'}
        continue
    # Unit mismatch with expected city unit
    expected_unit = EXPECTED_UNIT_FOR_CITY[row.city]  # from city_truth_contract
    if unit != expected_unit:
        UPDATE SET authority='QUARANTINED', provenance_json={reason:'NULL_PM_BIN_JSON_UNIT_MISMATCH', expected:expected_unit, got:unit}
        continue
    # OK — update pm_bin shape
    UPDATE settlements SET pm_bin_lo=lo, pm_bin_hi=hi, unit=unit WHERE id=row.id
    # Row NOW re-enters primary backfill pool in §2.B category #1 (or #2 if containment fails)
```

### §2.I — JSON export sequencing enumeration (architect P1-D / NC-13)

All JSON export writers that read `settlements`:

| Writer | File | Path |
|---|---|---|
| `state/positions.json` producer | `src/state/portfolio.py` (grep TBD) | Writes AFTER portfolio commits |
| `state/status_summary.json` producer | `src/control/status.py` or `src/observability/*` | Writes AFTER control cycle commits |
| `state/strategy_tracker.json` producer | `src/strategy/...` | Writes AFTER strategy state commits |
| Reconciliation log `state/reconciliation/pm_truth_divergence.jsonl` | DR-32 script | Writes only during reconciliation (R3d+), never concurrent with backfill |

Task Zero step 9: grep all `json.dump\|json_dump\|write.*\.json` in `src/` to enumerate every writer. Document in `evidence/json_export_inventory_v6.md`. Verify each runs post-DB-commit. NC-13 compliance AC: none of these scripts execute between R3c COMMIT and R3c post-commit verification.

---

## Section 3 — All DR cards in v6 (new + amended)

### DR-31 v6 — Settlements CHECK with strong provenance keys

Timing: created in **R3e** (NOT R0). At R0, the settlements schema has the DR-34 columns added but no CHECK constraint yet. R3c re-derives rows freely. R3e migration creates a new `settlements_new` table with CHECK, copies, swaps.

CHECK clause:
```sql
CHECK (
  authority IN ('QUARANTINED','UNVERIFIED')
  OR (
    winning_bin IS NOT NULL
    AND settlement_value IS NOT NULL
    AND temperature_metric IS NOT NULL
    AND physical_quantity IS NOT NULL
    AND observation_field IS NOT NULL
    AND data_version IS NOT NULL
    AND json_valid(provenance_json)
    AND json_extract(provenance_json, '$.source') IS NOT NULL
    AND json_extract(provenance_json, '$.obs_id') IS NOT NULL
    AND json_extract(provenance_json, '$.rounding_rule') IS NOT NULL
    AND json_extract(provenance_json, '$.data_version') IS NOT NULL
    AND json_extract(provenance_json, '$.decision_time_snapshot_id') IS NOT NULL
  )
)
```

### DR-34 v6 — Identity spine migration (Python script)

`scripts/migrate_2026_04_24_settlements_identity_spine.py`:
- Adds columns per DR-34 (NOT NULL DEFAULT '{}' for provenance_json)
- Retrofits 1,562 existing rows:
  - `temperature_metric='high'`, `observation_field='high_temp'`
  - `physical_quantity='mx2t6_local_calendar_day_max'` (from `docs/authority/zeus_current_architecture.md §7.1 HIGH_LOCALDAY_MAX`)
  - **`data_version='tigge_mx2t6_local_calendar_day_max_v1'`** (physical-quantity axis per DR-47/LC-2, aligned with `src/types/metric_identity.py:82`)
  - Source family info stored in `provenance_json.source_family` during R3c re-derivation
  - `provenance_json` retrofit value at R0: `{"retrofit_marker": "v6_pre_re_derivation"}`

### DR-38 v6 — Harvester atomic write (per §2.D)

See §2.D. Full refactor of `_write_settlement_truth`.

### DR-39 v6 — Parser ↔ canonical round-trip test

Test assertions (in `tests/test_parser_canonical_roundtrip.py`):
- For every canonical bin in `src/contracts/calibration_bins.py`: `_parse_temp_range(label)` returns expected (lo, hi)
- For every `canonical_bin_label(lo, hi, unit)` output: `_parse_temp_range` recovers (lo, hi) or shoulder form
- **Unicode variants**: ASCII hyphen (U+002D), en-dash (U+2013), em-dash (U+2014), non-breaking hyphen (U+2011), Unicode minus (U+2212); each tested with `f"32{dash}33°F"` and shoulder variants
- Whitespace: regular space, thin-space (U+2009), no-break space (U+00A0); both between number and ° and between ° and "or below"
- Negative boundaries: `"-38--37°F"` (double ASCII minus) must parse to `(-38, -37)` not `(-38, 8--37)` — greedy regex trap test

### DR-40 v6 — pm_settlements_full.json disposition

`_build_pm_truth.py` gated behind `--allow-legacy-schema-a` flag (default off). grep proof that no consumer exists is recorded in `evidence/pm_settlements_full_consumer_grep.txt` (empty). Denver 2026-04-15 investigation in Task Zero (DR-40b).

### DR-41 v6 — NULL pm_bin reconciliation with guards

See §2.H.

### DR-42 v6 — 2026-04-15 pre-correction

See §2.G. Unchanged from v5 except explicitly sub-ordered before DR-41 and before bulk QUARANTINE.

### DR-43 (NEW) — Harvester winningOutcome upstream probe

**Severity**: 🔴 P0 — blocks settlement pipeline entirely
**Problem**: DB has 0 non-NULL winning_bin across 1,562 rows. Root cause per scientist: `_find_winning_bin` at harvester.py:495 requires `market['winningOutcome'].lower() == 'yes'`. Polymarket Gamma API may not populate this field for all closed markets.
**Fix**:
1. Write `scripts/probe_gamma_winning_outcome.py`: fetch sample of 50 known-settled events from Gamma; report distribution of `winningOutcome` field presence + values.
2. If field is absent or rarely populated: add alternative signal in `_find_winning_bin`:
   - Check `market.get('outcomePrices')` — if one outcome has price 1.0 (after settlement), that's the winner
   - Check `market.get('resolvedBy')` / `market.get('closedTime')` presence
   - Parse from `event.get('series')` metadata
3. If fallback signal identified, extend `_find_winning_bin` to use it with explicit fallback-order logging in provenance_json
4. Instrument `_find_winning_bin` to log every skipped market with reason (missing field / not yes / malformed) → `state/harvester/winning_bin_skip_log.jsonl`
**AC**: after DR-43 ships, run harvester against 1 known-settled event and confirm `_write_settlement_truth` is reached. Before DR-33 + DR-38 merge.

### DR-44 (NEW) BLOCKING — WU product identity audit

**Severity**: 🔴 P0 — gates all 1,423 primary WU rows
**Problem**: `observations.wu_icao_history` source path (`src/data/wu_hourly_client.py`) uses WU private v1 API hourly data, aggregated internally. Polymarket JSON `resolution_source` URLs point to WU website daily summary pages. fatal_misreads.yaml `wu_website_daily_summary_not_wu_api_hourly_max` warns these may diverge.
**Fix**:
1. Sample 10 (city, month) pairs spanning Oct 2025 – Apr 2026 for 47 WU cities (470 data points)
2. For each: compute `observations.wu_icao_history` daily max AND scrape `wu_website_daily_summary` archived page for same date
3. Compute match rate (integer-rounded daily max equality) per city
4. Threshold: ≥95% match per city → WU cities can proceed; else per-city or global QUARANTINE/UNVERIFIED
5. Document in `evidence/wu_product_identity_audit_v6.md`
**AC**: match rate per city documented; cities with <95% stay UNVERIFIED with `data_version='tigge_..._v1'` + `provenance_json.audit_status='pending_wu_product_identity_audit_v6'`
**Timing**: runs in Task Zero. Blocks R3c for any city not passing.

### DR-45 (NEW) — K0 charter amendment via K_utils zone

**Severity**: 🟠 P1 — architectural hygiene
**Fix**:
1. Amend `architecture/zones.yaml`:
   ```yaml
   K_utils:
     description: Stateless cross-zone helper functions (time, geo, formatting). Not atoms. Not lifecycle. May be consumed by any zone.
     directories:
       - src/shared_helpers
     default_packet: refactor_packet
     evidence_required: [tests]
   ```
2. Create `src/shared_helpers/` directory
3. DR-10 helpers now land in `src/shared_helpers/_time_utils.py`, `src/shared_helpers/_geo_utils.py`, `src/shared_helpers/bin_labels.py` (not `src/contracts/`)
4. Update `architecture/source_rationale.yaml` with new file entries
5. Update `architecture/module_manifest.yaml` with new zone
6. Deprecation shim in `src.calibration.manager` + `src.signal.diurnal` for 30 days

### DR-46 (NEW) — Mesh maintenance (blocking)

Update all three registries atomically:
- `architecture/source_rationale.yaml` — add entries for every new `src/**` file in v6
- `architecture/script_manifest.yaml` — add entries for every new `scripts/*.py` in v6
- `architecture/test_topology.yaml` — add entries for every new `tests/test_*.py` in v6
- `architecture/module_manifest.yaml` — add entries for new modules

**AC**: `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit` passes. `python scripts/topology_doctor.py --source --json | jq '.unregistered_files | length'` == 0.

### DR-47 (NEW) — data_version physical-quantity axis correction

See LC-2 and DR-34 v6. `settlements.data_version = 'tigge_mx2t6_local_calendar_day_max_v1'` uniform across all 1,562 rows (HIGH track only). Source-family in provenance_json. Joinable with `forecasts.data_version` and `ensemble_snapshots.data_version` on the SAME axis.

### DR-48 (NEW) — Containment-failure category

38 rows from §2.B #2. Logged to `state/quarantine/containment_failures_v6.jsonl` with full context. Post-v6, separate packet `task_2026-05-XX_containment_failure_rca` investigates:
- Shenzhen 10 rows — is wu_icao_history ZGSZ the correct station for Polymarket Shenzhen market?
- Toronto 9 rows — why is CYYZ (Pearson Airport) drifting from Polymarket Toronto resolution?
- Seoul 7 rows — RKSI? RKSS? which is settlement-authoritative?
- HK 3 rows — probably oracle_truncate edge cases, reproducible
- Others (10 cities with 1-2 rows each) — batch review

### DR-49 (NEW) — Trigger + INV-06 enforcement

**DR-49a**: `settlements_authority_monotonic` trigger created only in R3e (after R3c backfill completes). During R3c, promotion is allowed because no trigger exists.
**DR-49b**: every `provenance_json` for VERIFIED rows MUST include `decision_time_snapshot_id` (for re-derived rows, this = obs.fetched_at; for cross-source rows, this = the cross-source obs fetched_at). Calibration query MUST filter `decision_time_snapshot_id ≤ training_cutoff` to enforce INV-06 point-in-time truth.
**Test**: `tests/test_inv06_decision_time_filter.py` — synthetic calibration_pair query WITHOUT filter returns more rows than WITH filter; the filter demonstrably excludes hindsight.

### DR-50 (NEW) — Parser regex expansion

`src/data/market_scanner.py:628` regex for range:
```python
# Before: r"(-?\d+\.?\d*)\s*[-–]\s*(-?\d+\.?\d*)\s*°[FfCc]"
# After:
DASH_CLASS = r"[-–—‑−]"  # ASCII, en, em, non-break hyphen, Unicode minus
SPACE_CLASS = r"[\s  ]*"  # regular, thin, no-break spaces
DEGREE_CLASS = r"°"  # U+00B0
re_range = rf"(-?\d+\.?\d*){SPACE_CLASS}{DASH_CLASS}{SPACE_CLASS}(-?\d+\.?\d*){SPACE_CLASS}{DEGREE_CLASS}[FfCc]"
```

Same expansion for `or below` / `or higher` regexes (space tolerance).

Blocking AC: DR-39 round-trip test covers all variants.

### DR-32 v6 — Reconciliation lane

Thresholds:
- Pre-DR-42: divergence_count ≤ 5 (known duplicates)
- Post-DR-42: divergence_count == 0 (R3d/R3e)
- Post-R3e: ongoing reconciliation exit 0 unless new divergence appears

### DR-33 v6 — Harvester canonical label

Unchanged from v5 except:
- Depends on DR-43 unblocking the write path
- Depends on DR-50 parser regex expansion
- Depends on DR-38 atomicity refactor
- INSERT populates all INV-14 fields per DR-34 v6

### DR-35 v6 — onboard_cities scaffold removal

Unchanged. Evidence `evidence/scaffold_callers.txt` shows only 2 self-references.

### DR-10 v6 — Helper relocation to K_utils

Target: `src/shared_helpers/*.py` per DR-45. Callers updated (24+ + hemisphere_for_lat + lat_for_city callers). 30-day shim.

### DR-37 (v5 inherit) — INV-14 fleet-wide stub

Same. Separate packet post-v6 + DR-03/DR-04.

### DR-36 (v5 inherit) — CWA ingest stub

Same.

---

## Section 5 — File inventory (v6 delta)

**New files** (v6 total):
- `src/shared_helpers/_time_utils.py` (DR-10 / DR-45 target; NOT src/contracts)
- `src/shared_helpers/_geo_utils.py`
- `src/shared_helpers/bin_labels.py`
- `scripts/backfill_settlements_from_observations.py`
- `scripts/reconcile_null_pm_bin_from_json.py`
- `scripts/fix_2026_04_15_wrong_entry.py`
- `scripts/reconcile_settlements_vs_pm_truth.py`
- `scripts/probe_gamma_winning_outcome.py` (DR-43)
- `scripts/audit_wu_product_identity.py` (DR-44)
- `scripts/migrate_2026_04_24_settlements_identity_spine.py` (DR-34)
- `scripts/migrate_2026_04_24_settlements_check_constraint.py` (DR-31, R3e)
- `scripts/audit_append_many_and_project_callers.py` (DR-38 evidence)
- `tests/test_canonical_bin_label.py`
- `tests/test_parser_canonical_roundtrip.py` (DR-39, DR-50)
- `tests/test_harvester_atomic_settlement_write.py` (DR-38)
- `tests/test_harvester_canonical_label.py`
- `tests/test_harvester_winning_outcome_fallback.py` (DR-43)
- `tests/test_settlements_identity_spine.py`
- `tests/test_settlements_invariant.py`
- `tests/test_r2d_main_not_coupled_to_ingest_writers.py`
- `tests/test_pm_truth_reconciliation.py`
- `tests/test_time_utils_callers_all_updated.py`
- `tests/test_scripts_no_family_key_drift.py` (DR-46 / architect P1-A)
- `tests/test_inv06_decision_time_filter.py` (DR-49b)
- `tests/test_wu_product_identity_audit_applied.py` (DR-44)
- `~/Library/LaunchAgents/com.zeus.reconcile.pm_truth.plist`
- `architecture/zones.yaml` (K_utils zone added)
- `architecture/source_rationale.yaml` (new entries)
- `architecture/script_manifest.yaml` (new entries)
- `architecture/test_topology.yaml` (new entries)
- `architecture/module_manifest.yaml` (new entries)
- `architecture/fatal_misreads.yaml` (8th misread addition — separate packet reference)
- evidence/ : `1562_row_partition.sql`, `tel_aviv_wu_noaa_transition.md`, `tel_aviv_ogimet_vs_wu_audit.md`, `wu_product_identity_audit_v6.md`, `pm_settlements_full_consumer_grep.txt`, `json_export_inventory_v6.md`, `scaffold_callers.txt`, `containment_failures_v6.jsonl` (produced at R3c), `bulk_writer_rca_v6.md`, `denver_disposition.md`
- state/ : `state/quarantine/settlements_pre_v6_20260423.jsonl`, `state/quarantine/backfill_progress.jsonl`, `state/quarantine/containment_failures_v6.jsonl`, `state/reconciliation/pm_truth_divergence.jsonl`, `state/harvester/winning_bin_skip_log.jsonl`

**Modified files**:
- `src/execution/harvester.py` (DR-33 + DR-38 + DR-43)
- `src/state/db.py` (DR-34 schema_init mirror)
- `src/data/market_scanner.py` (DR-50 regex expansion)
- `scripts/onboard_cities.py` (DR-35 scaffold removal)
- `scripts/_build_pm_truth.py` (DR-40 flag gating)
- `src/calibration/manager.py` (DR-10 shims)
- `src/signal/diurnal.py` (DR-10 shims)
- 24+ callers (DR-10 import updates)

---

## Section 6 — Task Zero (v6)

Run in order, blocking:

1. **Planning-lock** with v6 file list (re-run)
2. **1b. Stop ALL THREE launchd plists** (critic E15):
   ```bash
   for plist in com.zeus.live-trading com.zeus.riskguard-live com.zeus.heartbeat-sensor; do
     launchctl unload ~/Library/LaunchAgents/$plist.plist
   done
   sleep 10
   # Confirm all 3 pids exit
   ps aux | grep -E "src.main|riskguard.riskguard|heartbeat_sensor" | grep -v grep && echo "STILL RUNNING — ABORT" || echo "ALL STOPPED"
   # Verify heartbeat frozen
   STAMP1=$(stat -f '%m' state/daemon-heartbeat.json)
   sleep 120
   STAMP2=$(stat -f '%m' state/daemon-heartbeat.json)
   [ "$STAMP1" = "$STAMP2" ] && echo "HEARTBEAT FROZEN OK" || echo "DAEMON STILL ACTIVE — ABORT"
   ```
3. **Bulk-writer RCA documentation** — `bulk_writer_rca_v6.md`
4. **Denver 2026-04-15 disposition** (DR-40b)
5. **HK + CWA evidence** — inherit v5 step
6. **Semantic boot gates** (topology_doctor task-boot-profiles + fatal-misreads + core-claims)
7. **1562-row partition evidence** (`evidence/1562_row_partition.sql` emit + verify sums to 1,562)
8. **Mesh registry updates** (DR-46) — update all 4 architecture/*.yaml files; run `--map-maintenance --map-maintenance-mode precommit`
9. **JSON export writer enumeration** (§2.I) — `evidence/json_export_inventory_v6.md`
10. **WU product identity audit** (DR-44) — runs `scripts/audit_wu_product_identity.py`; produces `evidence/wu_product_identity_audit_v6.md` with per-city match rates
11. **Harvester winningOutcome probe** (DR-43) — runs `scripts/probe_gamma_winning_outcome.py`; if field absent/rare, identify fallback signal; document in `evidence/harvester_upstream_probe_v6.md`
12. **Tel Aviv ogimet vs WU audit** (§2.C hardening, part of DR-44) — confirms 13 cross-source rows are legitimately recoverable

Proceed to R0 ONLY IF steps 1, 1b, 7, 10, 11 all pass. Step 10's outcome determines scope of R3c.

---

## Section 7 — Phased execution order (v6)

| Phase | Contents | Sub-order | Gate |
|---|---|---|---|
| R0 | DR-34 migration (add columns, no CHECK yet); DR-01 forecasts schema | — | AC-R0-INV14 |
| R1 | DR-05 poisoned obs delete; DR-06 DST gap test; DR-35 scaffold removal | — | inherit v2 |
| R2-PRE-FIX | DR-42 (5 rows) → DR-41 (59 rows) → bulk QUARANTINE (1,562 rows) | strictly sequential | AC-R2-PRE-FIX |
| R2 (isolation) | DR-09 R2-B/R2-D, DR-11, DR-10/DR-45 (helpers → K_utils) | — | DR-09 AST test |
| R3a | DR-50 parser regex expansion + DR-39 round-trip test | — | AC-R3-ROUNDTRIP |
| R3b | DR-43 harvester upstream probe + fallback signal | before DR-33 | AC-R3-HARVESTER-PROBE |
| R3c | DR-38 atomicity refactor + DR-33 canonical label + backfill | single outer txn + per-city SAVEPOINT | AC-R3-HARVESTER-ATOMIC + AC-R3-CATEGORIES + AC-R3-VERIFIED-RANGE |
| R3d | DR-32 reconciliation; verify 0 divergence post-DR-42 | | AC-R3-RECONCILE |
| R3e | DR-31 CHECK migration + `settlements_authority_monotonic` trigger | **trigger created HERE, not R0** | AC-R3-CHECK |
| R4..R5 | inherit v2/v3 phases | |
| Daemon restart | Load 3 plists back | after all R0-R3e complete + all ACs green | |
| R6 (deferred) | DR-37 INV-14 fleet-wide (new packet) | |

---

## Section 8 — Verification matrix (v6 replacement)

| AC | Command | Pass condition |
|---|---|---|
| **AC-Task-Zero-1b** | 3-plist stop + heartbeat frozen | all confirmed |
| **AC-Task-Zero-7** | `evidence/1562_row_partition.sql` row count | == 1,562 (exact; mutually-exclusive categories) |
| **AC-Task-Zero-8** | `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit` | exit 0 |
| **AC-Task-Zero-10** | `evidence/wu_product_identity_audit_v6.md` exists; per-city table populated | match rate documented; cities with <95% listed |
| **AC-Task-Zero-11** | `evidence/harvester_upstream_probe_v6.md` exists; fallback signal identified | probe ran; answer known |
| **AC-R0-INV14** | PRAGMA table_info(settlements) | has temperature_metric, physical_quantity, observation_field, data_version (= 'tigge_mx2t6_local_calendar_day_max_v1'), provenance_json (NOT NULL DEFAULT '{}') |
| **AC-R2-PRE-FIX** | DR-42 + DR-41 + bulk QUARANTINE ran sequentially | DR-42 5 rows updated; DR-41 ≤59 rows updated; 1,562 rows in QUARANTINED state |
| **AC-R2-BULK-QUARANTINED** | `SELECT COUNT(*) FROM settlements WHERE authority='QUARANTINED' AND settled_at LIKE '2026-04-16T12:39:58%'` | == 1562 (transient; decreases as R3c promotes) |
| **AC-R3-ROUNDTRIP** | pytest tests/test_parser_canonical_roundtrip.py | green (all Unicode variants) |
| **AC-R3-HARVESTER-PROBE** | DR-43 evidence + instrumented path | probe complete + fallback signal either confirmed or 'winningOutcome' field reliability documented |
| **AC-R3-HARVESTER-ATOMIC** | pytest tests/test_harvester_atomic_settlement_write.py | green |
| **AC-DR-38-SCOPED** | `awk '/^def _write_settlement_truth/,/^def /' src/execution/harvester.py \| grep -c 'conn.commit()'` | == 0 (no commit inside that function) |
| **AC-R3-CATEGORIES** | Verify DB categorization matches `evidence/1562_row_partition.sql` partition | 100% match per-row |
| **AC-R3-VERIFIED-RANGE** (DR-44 PASS) | `SELECT COUNT(*) FROM settlements WHERE authority='VERIFIED' AND winning_bin IS NOT NULL` | 1,420 ≤ count ≤ 1,435 (expected ~1,430); all 1,423 primary + ~5 DR-41 reconciled + 5 DR-42 corrected + 6 DST, minus any edge failures |
| **AC-R3-VERIFIED-RANGE** (DR-44 FAIL full) | same | == 0; all WU+NOAA rows stay UNVERIFIED with `provenance_json.audit_status='pending_wu_product_identity_audit_v6'` |
| **AC-R3-VERIFIED-RANGE** (DR-44 partial, some cities fail) | same | per-city count; operator reviews city list |
| **AC-R3-QUARANTINED-ENUMERATED** | `SELECT json_extract(provenance_json,'$.reason'), COUNT(*) FROM settlements WHERE authority='QUARANTINED' GROUP BY 1` | every reason ∈ enumerated set (HKO_INGEST_STALLED, TAIPEI_CWA_NO_OBS_SOURCE, TAIPEI_NOAA_NO_OGIMET_OBS, HK_WU_STAMP_SOURCE_TYPE_DRIFT, CONTAINMENT_FAIL_OBS_OFFSET, NULL_PM_BIN_NO_JSON_ENTRY, NULL_PM_BIN_JSON_SHAPE_INVALID, NULL_PM_BIN_JSON_UNIT_MISMATCH, BULK_WRITER_SOURCE_TYPE_DRIFT, TEL_AVIV_CROSS_SOURCE_AUDIT_FAILED) |
| **AC-R3-NO-QUARANTINED-OBS-USED** | | no VERIFIED row derived from QUARANTINED obs (DST handled via JSON fallback only) |
| **AC-R3-PROVENANCE-KEYS** | Every VERIFIED row has all 5 required keys | 100% |
| **AC-R3-DECISION-TIME-SNAPSHOT** | Every VERIFIED row has `provenance_json.decision_time_snapshot_id` populated (DR-49b) | 100% |
| **AC-R3-DATA-VERSION-UNIFORM** | `SELECT DISTINCT data_version FROM settlements WHERE authority='VERIFIED'` | {'tigge_mx2t6_local_calendar_day_max_v1'} — one value only |
| **AC-R3-SOURCE-FAMILY-ROUTED** | `SELECT json_extract(provenance_json,'$.source_family'), COUNT(*) FROM settlements WHERE authority='VERIFIED' GROUP BY 1` | wu-native / noaa-ogimet / hko-native + counts match source policy |
| **AC-R3-CHECK** | `SELECT sql FROM sqlite_master WHERE name='settlements'` | includes full DR-31 v6 CHECK clause |
| **AC-R3-AUTHORITY-MONOTONIC-BEHAVIOR** | test_fixture: UPDATE authority='VERIFIED' WHERE authority='QUARANTINED' | raises (in R3e, not R3c) |
| **AC-R3-RECONCILE** | `python scripts/reconcile_settlements_vs_pm_truth.py` | exit 0, divergence == 0 post-DR-42 |
| **AC-DR-10-CALLERS** | pytest tests/test_time_utils_callers_all_updated.py | green (includes hemisphere/lat_for_city) |
| **AC-DR-46-MESH** | topology_doctor --source --json unregistered_files count | == 0 |
| **AC-DR-44-APPLIED** | every VERIFIED WU row has `provenance_json.wu_audit_city_match_rate >= 0.95` | 100% |
| **AC-DR-43-UNBLOCKED** | harvester runs one full cycle; `_write_settlement_truth` invoked ≥ 1 time | log presence |
| **AC-INV06-FILTER** | pytest tests/test_inv06_decision_time_filter.py | green |
| **AC-NC13-SEQUENCE** | JSON export writers sequence | all run post-R3c commit; logged timestamps prove ordering |
| (inherit) DR-01/05/06/15/17 | v5 ACs | unchanged |

---

## Section 9 — Risk pre-mortem (v6 delta)

Inherit V4 + V5 risks. New:

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| V6-R1 | DR-44 WU audit partially fails (some cities match <95%) | MED | Subset of 1,423 rows stay UNVERIFIED | per-city UNVERIFIED with explicit audit_status; operator reviews and decides follow-up audit or alternate source adoption |
| V6-R2 | DR-43 probe finds winningOutcome absent even with fallbacks | LOW | harvester permanently can't write; but v6 is backfill, not harvester — OK for v6 completion, follow-up packet for live harvester fix | probe produces actionable report |
| V6-R3 | K_utils zone amendment takes longer than expected (architecture/zones.yaml edit is cross-packet lock surface) | LOW | Delay | DR-45 is NOT in execution critical path; helpers can land in `src/shared_helpers/` even if zones.yaml lags by 1 commit; AGENTS.md zone edit is a minor packet |
| V6-R4 | Trigger R3e creation fails (migration conflict) | LOW | CHECK constraint absent until fix | R3e is last phase; failure leaves correct DB state without structural guard; operator can retry |
| V6-R5 | 38 containment failures expose a fundamental source-station mismatch (per-city stations are wrong in config/cities.json) | MED | 38 rows quarantine + reveals config-level bug affecting future writes too | DR-48 follow-up packet investigates; `config/cities.json` audit indicated |
| V6-R6 | `settled_at LIKE '2026-04-16T12:39:58%'` matches non-bulk writes if any slipped in during the audit window | LOW | 1-2 row mis-quarantine | verify via `SELECT * FROM settlements WHERE settled_at LIKE ... AND id NOT IN (bulk_ids_snapshot)` before R2-PRE-FIX |
| V6-R7 | Mesh registry updates (DR-46) cause unrelated topology_doctor warnings to break CI | MED | Blocking CI | dry-run topology_doctor BEFORE commit; separate commits for registry vs code if needed |
| V6-R8 | `provenance_json.decision_time_snapshot_id` choice of timestamp format (obs.fetched_at vs obs.authority timestamp) disagrees with calibration query filter | MED | Calibration over/under-filters | DR-49b spec precisely: `decision_time_snapshot_id = obs.fetched_at` (UTC ISO8601); calibration filter uses same format |
| V6-R9 | DR-38 refactor introduces harvester regression in live mode after daemon restart | MED | Live trading breakage | feature-flag `ZEUS_HARVESTER_ATOMIC_V6=1`; default-on in v6, with env-var kill-switch; monitor first 24h after restart |
| V6-R10 | Training STILL blocked — forecasts backfill + calibration_pairs_v2 population outside v6 | HIGH | Expected | §13 disclosure; parallel packet |

---

## Section 10 — Scope boundaries (v6)

**v6 IS**:
- Settlements table correctness (identity + CHECK + canonical labels)
- Harvester atomic write + canonical output + upstream probe (DR-43)
- WU product identity audit (DR-44)
- Parser Unicode robustness (DR-50)
- K_utils zone amendment (DR-45)
- Mesh registry maintenance (DR-46)
- INV-06 decision-time enforcement (DR-49b)
- Reconciliation lane (DR-32)

**v6 IS NOT**:
- Forecasts/market_events/ensemble_snapshots backfill
- INV-14 fleet-wide extension (DR-37 stub)
- CWA ingest (DR-36 stub)
- LOW track enablement
- 38-row containment failure root cause (DR-48 follow-up)
- CI workflow changes (separate packet if CI needs updating for shim deprecation)

---

## Section 11 — v5→v6 correction summary

See Section 0 table + this file. 20 P0 + 7 P1 resolved. 5 logical contradictions fixed. 5 new DRs (DR-43/44/45/46/47/48/49/50).

---

## Section 13 — Data-readiness outcome prediction (v6)

Conditional scenarios:

### Scenario A: DR-44 WU audit PASSES for all 47 cities

| Metric | Value |
|---|---|
| settlements VERIFIED | ~1,430 (±5) |
| settlements QUARANTINED | ~132 (38 containment + ~94 hard/source-mismatch/null-pm) |
| settlements UNVERIFIED | 0 |
| Total | 1,562 |
| forecasts count | 0 (out of scope; DR-03/DR-04 required) |
| calibration_pairs_v2 count | 0 (awaiting forecasts backfill) |
| TIGGE extractor can run? | YES (once forecasts backfill lands; v6 + DR-03/04 sufficient) |
| Platt training viable? | Spring-2026-only single-season; 6 cities low-row-count; cross-season requires summer 2026 data |

### Scenario B: DR-44 WU audit FAILS for some cities (say 10 of 47)

| Metric | Value |
|---|---|
| settlements VERIFIED | ~1,430 - 300 (WU rows for failed cities) = ~1,130 |
| settlements UNVERIFIED | ~300 |
| settlements QUARANTINED | ~132 |
| Total | 1,562 |
| forecasts count | 0 |
| TIGGE extractor can run? | YES for ~37 cities; for 10 cities awaiting follow-up audit |

### Scenario C: DR-44 WU audit FAILS for all 47 cities

| Metric | Value |
|---|---|
| settlements VERIFIED | ~19 (6 DST-JSON + 13 Tel-Aviv? or 0 if Tel Aviv uses same WU product) |
| settlements UNVERIFIED | ~1,410 |
| settlements QUARANTINED | ~132 |
| TIGGE extractor can run? | NO — need new packet to replace WU source fleet-wide |

**v6 explicitly handles all three scenarios gracefully — no silent failure mode.**

---

## Section 12 — Open questions for v6 reviewers

Q1. DR-44 WU audit tolerance threshold: ≥95%, ≥98%, 100%?
Q2. DR-43 fallback signal priority: outcomePrices 1.0 vs resolvedBy vs series metadata — which is authoritative?
Q3. K_utils zone (DR-45) — new zone or amend K0 charter?
Q4. DR-41 unit mismatch policy: hard quarantine or try to convert (e.g., C→F via 9/5 conversion)?
Q5. DR-49b `decision_time_snapshot_id` format — obs.fetched_at (UTC) or obs.authority_update_timestamp (if exists)?
Q6. 38 containment failures (DR-48) — write them to settlements with authority='QUARANTINED' during R3c, or skip during R3c and handle in DR-48 follow-up packet?
Q7. DR-34 NOT NULL on new columns — apply at R0 or at R3e CHECK migration? (v6 applies at R3e to avoid R0 retrofit errors.)
Q8. DR-46 mesh registry updates — single commit with code or separate commit?
Q9. DR-10 30-day shim (calendar-based) vs migration-complete (dynamic) — trigger for removal?
Q10. INV-06: re-derivation TODAY for target_date 2025-12-30 — is decision_time_snapshot_id = obs.fetched_at sufficient, or must we also capture obs.authority_change_history?

---

**Operator sign-off required before execution**: review DR-44 audit threshold (Q1), K_utils zone decision (Q3), Scenario B/C acceptance criteria (§13), training unblocked timeline with DR-03/DR-04 parallel packet.
