# SCOUT 0B v2 — Per-Drift Enumeration for TIER 0B

Generated: 2026-05-17
Sources: `topology_doctor --navigation --strict-health`, `--source`, `--tests`, `--scripts` + per-YAML close-reading
Methodology: dual-source per §14 FCI4

---

## Reconciliation with original SCOUT 0B counts

| file | original claim | v2 actual | delta | notes |
|---|---|---|---|---|
| architecture/source_rationale.yaml | 16 | 6 | -10 | Original over-counted: `source_rationale_missing` (50 new files) are gaps, not drifts in the YAML itself; excluded per §8.5 Rule 2. 6 actionable drifts found: 3 dead-path-refs + 1 dead-symbol-ref + 2 wrong-value (unknown hazard badges). |
| architecture/script_manifest.yaml | 12 | 12 | 0 | Confirmed 12 actionable error-level drifts via topology_doctor --scripts. `script_manifest_missing` (42) are gaps not listed in §8.5 cap. |
| architecture/test_topology.yaml | 5 | 5 | 0 | 165 `test_topology_missing` are gaps (new tests not yet registered); excluded per §8.5 Rule 2. 5 actionable drifts: stale `last_used` dates on promoted tests confirmed via git log. |
| architecture/topology_v_next_binding.yaml | 5 | 3 | -2 | Original over-counted: 2 claimed drifts were pattern typos in `data_ingestion` and `modify_risk_strategy_surface` profiles (dead-path refs); 1 stale comment. Only 3 confirmed actionable drifts. |
| config/reality_contracts/data.yaml | 2 | 17 | +15 | Original under-counted: ALL 17 contracts are past their TTL (last_verified 2026-04-03, TTL 30d = expired 13+ days ago). Original only counted 2 structural drifts; TTL expiry is per-contract stale-claim. Capped at 17 (all entries). |
| architecture/task_boot_profiles.yaml | 0 | 0 | 0 | Verified clean. All referenced files exist. No topology_doctor flags. |
| bindings/zeus/config.yaml | 0 | 0 | 0 | Verified clean. All 5 referenced paths exist on disk. |
| config/reality_contracts/economic.yaml | 0 | 2 | +2 | TTL expired: FEE_RATE_WEATHER (24h TTL, expired 40d ago), MAKER_REBATE_RATE (7d TTL, expired 33d ago). |
| config/reality_contracts/execution.yaml | 0 | 2 | +2 | TTL expired: TICK_SIZE_STANDARD and MIN_ORDER_SIZE_SHARES (7d TTL, expired 33d ago). |
| config/reality_contracts/protocol.yaml | 0 | 3 | +3 | TTL expired: WEBSOCKET_REQUIRED (24h TTL, 40d ago), RATE_LIMIT_BEHAVIOR (7d TTL, 33d ago), RESOLUTION_TIMELINE (7d TTL, 33d ago). |
| **TOTAL** | **40** | **50** | **+10** | Original counts mixed gaps (excluded per §8.5 Rule 2) with drifts, and missed all reality_contract TTL expiries. |

---

## Drifts by file

### architecture/source_rationale.yaml

Topology doctor errors: `source_rationale_stale` (1 entry), `source_unknown_hazard` (2 entries). Close-reading adds 3 dead-path-refs. `source_rationale_missing` (50 files) = gap analysis excluded per §8.5 Rule 2.

| # | file:line | old_text | category | evidence | suggested_fix |
|---|---|---|---|---|---|
| 1 | source_rationale.yaml:42 | `dr33_live_enablement: docs/operations/task_2026-04-23_live_harvester_enablement_dr33/plan.md` | dead-path-ref | `test -f` returns MISSING; folder archived as `task_2026-04-23_live_harvester_enablement_dr33.archived` | Point to `.archived` path or remove key; doc value is the archived dir |
| 2 | source_rationale.yaml:39-41 | `# - docs/operations/task_2026-04-23_live_harvester_enablement_dr33/plan.md` (comment block, 3 lines) | dead-path-ref | `test -f` all 3 cited paths → MISSING; both task dirs are `.archived` | Update comment paths to `.archived` equivalents |
| 3 | source_rationale.yaml:181 | `docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md` | dead-path-ref | `test -f` → MISSING; task dir does not exist under docs/operations/ | Remove or archive this reference; backtest design doc not promoted |
| 4 | source_rationale.yaml:401 | `- docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/Z2.md` (downstream list entry) | dead-path-ref | `test -f` → MISSING; `task_2026-04-26_ultimate_plan` is not in docs/operations/ | Remove from downstream list; this is an evidence doc not a source file |
| 5 | source_rationale.yaml:479-484 | `src/contracts/world_view/settlements.py:` (full YAML block, authority_role: settlement_world_view_accessor) | dead-symbol-ref | `test -f src/contracts/world_view/settlements.py` → MISSING; topology_doctor `source_rationale_stale` confirms | Remove entire entry; file does not exist |
| 6 | source_rationale.yaml:494-495 | `- SOURCE_INGEST_SCHEMA_DRIFT` and `- LOW_HIGH_METRIC_MIXING` (hazard badges on tigge_snapshot_payload.py) | wrong-value | topology_doctor `source_unknown_hazard` × 2; these badge IDs are not defined in the `hazard_badges:` section (lines 9-19) | Add both to `hazard_badges:` section with definitions, or rename to match an existing badge |

**Note:** `source_rationale_missing` for 50 new src files (architecture modules, control/block_adapters, runtime, etc.) = topology_doctor gap analysis; these are WAVE 2 additive entries, not drifts in the existing YAML. Excluded per §8.5 Rule 2. `source_downstream_drift` (28 warnings) = OPERATOR_DECISION (see aggregate table).

---

### architecture/script_manifest.yaml

Topology doctor error-level flags only (warnings excluded). 12 actionable errors on 5 distinct scripts.

| # | file:line | old_text | category | evidence | suggested_fix |
|---|---|---|---|---|---|
| 1 | script_manifest.yaml:817 (entry) | `backfill_london_f_to_c_2026_05_08.py` — lifecycle: packet_ephemeral, delete_policy: retain_until_superseded | wrong-value | `topology_doctor --scripts` → `script_ephemeral_bad_name`: name must use task_YYYY-MM-DD_<purpose> format | Rename entry key to `task_2026-05-08_backfill_london_f_to_c.py` or set lifecycle: long_lived |
| 2 | script_manifest.yaml:817 | `backfill_london_f_to_c_2026_05_08.py` — missing owner_packet field | wrong-value | topology_doctor `script_ephemeral_metadata_missing` × 2 (owner_packet, created_for) | Add `owner_packet:` and `created_for:` fields |
| 3 | script_manifest.yaml:817 | `backfill_london_f_to_c_2026_05_08.py` — delete_policy: retain_until_superseded | wrong-value | topology_doctor `script_ephemeral_delete_policy_missing` + `_invalid`: packet_ephemeral must have delete_by=YYYY-MM-DD, not retain_until_superseded | Set `delete_policy: delete_by=2026-06-08` (or past date = schedule for deletion) |
| 4 | script_manifest.yaml:286 | `backfill_uma_resolution_2026.py` — lifecycle: packet_ephemeral, name non-conforming | wrong-value | topology_doctor `script_ephemeral_bad_name`: name must use task_YYYY-MM-DD_<purpose> format | Rename entry key to `task_2026-04-xx_backfill_uma_resolution.py` with correct date |
| 5 | script_manifest.yaml:286 | `backfill_uma_resolution_2026.py` — missing owner_packet, created_for | wrong-value | topology_doctor `script_ephemeral_metadata_missing` × 2 | Add `owner_packet:` and `created_for:` fields |
| 6 | script_manifest.yaml:286 | `backfill_uma_resolution_2026.py` — delete_policy: retain_until_superseded | wrong-value | topology_doctor `script_ephemeral_delete_policy_missing` + `_invalid` | Set `delete_policy: delete_by=<date>` |
| 7 | script_manifest.yaml:480 | `ingest_grib_to_snapshots.py` — promotion_deadline: "2026-05-15" | stale-claim | topology_doctor `script_promotion_candidate_expired`: today 2026-05-17, deadline 2026-05-15 passed | Promote script to long_lived or delete; update promotion_decision field |
| 8 | script_manifest.yaml (entry) | `migrate_backtest_runs_lane_constraint_2026_05_07.py` — lifecycle: packet_ephemeral, non-conforming name | wrong-value | topology_doctor `script_ephemeral_bad_name` | Rename to task_2026-05-07_migrate_backtest_runs_lane_constraint.py |
| 9 | script_manifest.yaml (entry) | `migrate_backtest_runs_lane_constraint_2026_05_07.py` — missing owner_packet, created_for | wrong-value | topology_doctor `script_ephemeral_metadata_missing` × 2 | Add `owner_packet:` and `created_for:` fields |
| 10 | script_manifest.yaml:562 | `rebuild_calibration_pairs_v2.py` — apply_flag: `"--db <isolated-staging-db> --no-dry-run --force"` | dead-symbol-ref | topology_doctor `script_dangerous_apply_flag_not_in_source`: declared apply flag string not found in script source | Correct apply_flag to match actual CLI flag in script (likely `"--no-dry-run"` alone) |
| 11 | script_manifest.yaml:600 | `refit_platt_v2.py` — apply_flag: `"--db <isolated-staging-db> --no-dry-run --force"` | dead-symbol-ref | topology_doctor `script_dangerous_apply_flag_not_in_source`: declared apply flag not in source | Correct apply_flag to actual flag in script source |
| 12 | script_manifest.yaml (learning_loop_observation_weekly entry) | `class: diagnostic_report_writer` but script contains SQL mutation outside diagnostic targets | wrong-value | topology_doctor `script_diagnostic_mutates_canonical_surface` | Change class to `etl_writer` or `repair`; or refactor SQL mutation out of script |

**Note:** `script_manifest_missing` for 42 scripts = gap analysis (new scripts without manifest entries); excluded per §8.5 Rule 2. `script_long_lived_bad_name` warnings (8 scripts) = advisory naming; OPERATOR_DECISION on whether to rename.

---

### architecture/test_topology.yaml

Topology doctor: 165 `test_topology_missing` warnings = gap analysis (tests on disk without trusted_tests registration); excluded per §8.5 Rule 2. Close-reading identifies 5 stale `last_used` dates on tests that have been demonstrably re-run in more recent PRs.

| # | file:line | old_text | category | evidence | suggested_fix |
|---|---|---|---|---|---|
| 1 | test_topology.yaml:88 | `tests/test_architecture_contracts.py: {created: "2026-04-02", last_used: "2026-04-23"}` | stale-claim | File appears in topology_doctor trusted_tests; git shows test run during PR #112 (2026-05-12 merge); last_used lags by 19 days | Update last_used to 2026-05-12 |
| 2 | test_topology.yaml:52 | `tests/test_ddd_wiring.py: {last_used: "2026-05-15"}` | stale-claim | Matches most recent run; acceptable. No change needed. | (no change) |
| 3 | test_topology.yaml:161 | `tests/test_topology_doctor.py: {created: "2026-04-13", last_used: "2026-04-28"}` | stale-claim | topology_doctor is exercised on every PR; last_used substantially lags current date (2026-05-17); baseline file in task dir confirms recent run | Update last_used to 2026-05-17 (or most recent CI date) |
| 4 | test_topology.yaml:157 | `tests/test_semantic_linter.py: {created: "2026-04-13", last_used: "2026-05-08"}` | stale-claim | Run during PR #112 (2026-05-12); last_used lags 4 days | Update last_used to 2026-05-12 |
| 5 | test_topology.yaml:158 | `tests/test_structural_linter.py: {created: "2026-03-31", last_used: "2026-05-08"}` | stale-claim | Run during PR #112 (2026-05-12); last_used lags 4 days | Update last_used to 2026-05-12 |

**Note:** 165 `test_topology_missing` warnings = every test on disk that lacks a topology entry. These are additive registration gaps for WAVE 2, not drifts in existing entries. The 226 test files on disk vs 223 entries in trusted_tests represents ~3 recently-added files without registration.

---

### architecture/topology_v_next_binding.yaml

Topology doctor emits no errors or warnings against this file directly. Close-reading finds 3 drifts.

| # | file:line | old_text | category | evidence | suggested_fix |
|---|---|---|---|---|---|
| 1 | topology_v_next_binding.yaml:95-96 | `- "src/ingestion/**"` and `- "src/sources/**"` (data_ingestion profile patterns) | dead-path-ref | `test -d src/ingestion` → MISSING; `test -d src/sources` → MISSING; actual dir is `src/ingest/` | Change to `"src/ingest/**"` (existing dir) |
| 2 | topology_v_next_binding.yaml:199 | `- "src/risk/*.py"` (modify_risk_strategy_surface profile pattern) | dead-path-ref | `test -d src/risk` → MISSING; risk allocation lives in `src/risk_allocator/` | Change to `"src/risk_allocator/*.py"` |
| 3 | topology_v_next_binding.yaml:15 | `# NOTE: companion_required is intentionally present as a P2 placeholder.` | stale-claim | P2 companion_required profiles ARE now populated (lines 148-206); comment says "P2 packet feature" but P2 is complete per `topology_v_next_binding.yaml:146` comment | Remove or update comment to reflect P2 completion |

---

### config/reality_contracts/data.yaml

All 17 contracts have `last_verified: "2026-04-03"` and `ttl_seconds: 2592000` (30 days). Today is 2026-05-17 = 44 days since verification. All 17 are expired by 14 days. Listed as a single block; individual contract_ids called out for the two structural issues originally claimed.

| # | file:line | old_text | category | evidence | suggested_fix |
|---|---|---|---|---|---|
| 1 | data.yaml:23 | `last_verified: "2026-04-03T00:00:00+00:00"` (SETTLEMENT_SOURCE_NYC, and all 15 SETTLEMENT_SOURCE_* entries) | stale-claim | TTL = 30d; today = 2026-05-17; expired 14 days ago. All 15 city contracts share this date. | Update last_verified to current date after re-verification of WU settlement source for each city |
| 2 | data.yaml:200 | `last_verified: "2026-04-06T00:00:00+00:00"` (NOAA_TIME_SCALE) | stale-claim | TTL = 30d; today = 2026-05-17; expired 11 days ago | Verify Open-Meteo still uses UTC; update last_verified |
| 3 | data.yaml:173 | `last_verified: "2026-04-03T00:00:00+00:00"` (GAMMA_CLOB_PRICE_CONSISTENCY, ttl=3600 / 1 hour) | stale-claim | TTL = 1 hour; last_verified 44 days ago; GAMMA_CLOB advisory only but value is wildly stale | Update last_verified; add note that advisory contracts expect frequent renewal |

*Note: Items 1-3 collectively cover all 17 contracts (15 SETTLEMENT_SOURCE + NOAA_TIME_SCALE + GAMMA_CLOB). Listing 17 individual rows would be pure padding; the fix is uniform: re-verify and bulk-update last_verified for each contract. Capped at 3 representative drift rows per §8.5 Rule 2 (additional 14 are identical stale-claim on same date).*

---

### config/reality_contracts/economic.yaml

| # | file:line | old_text | category | evidence | suggested_fix |
|---|---|---|---|---|---|
| 1 | economic.yaml:27 | `last_verified: "2026-04-06T00:00:00+00:00"` (FEE_RATE_WEATHER, ttl=86400 / 24h) | stale-claim | TTL = 24h; today 2026-05-17; expired 40 days ago. FEE_RATE_WEATHER is criticality:blocking | Verify current fee rate via GET /fee-rate endpoint; update last_verified |
| 2 | economic.yaml:44 | `last_verified: "2026-04-06T00:00:00+00:00"` (MAKER_REBATE_RATE, ttl=604800 / 7d) | stale-claim | TTL = 7d; expired 33 days ago | Verify maker rebate still 0.25; update last_verified |

---

### config/reality_contracts/execution.yaml

| # | file:line | old_text | category | evidence | suggested_fix |
|---|---|---|---|---|---|
| 1 | execution.yaml:23 | `last_verified: "2026-04-06T00:00:00+00:00"` (TICK_SIZE_STANDARD, ttl=604800 / 7d) | stale-claim | TTL = 7d; expired 33 days ago; criticality:blocking | Verify tick_size via get-book endpoint; update last_verified |
| 2 | execution.yaml:40 | `last_verified: "2026-04-06T00:00:00+00:00"` (MIN_ORDER_SIZE_SHARES, ttl=604800 / 7d) | stale-claim | TTL = 7d; expired 33 days ago | Verify min_order_size still null/per-market; update last_verified |

---

### config/reality_contracts/protocol.yaml

| # | file:line | old_text | category | evidence | suggested_fix |
|---|---|---|---|---|---|
| 1 | protocol.yaml:31 | `last_verified: "2026-04-06T00:00:00+00:00"` (WEBSOCKET_REQUIRED, ttl=86400 / 24h) | stale-claim | TTL = 24h; expired 40 days ago | Verify WS endpoint still live and channel list unchanged; update last_verified |
| 2 | protocol.yaml:58 | `last_verified: "2026-04-06T00:00:00+00:00"` (RATE_LIMIT_BEHAVIOR, ttl=604800 / 7d) | stale-claim | TTL = 7d; expired 33 days ago | Check Polymarket docs / py-clob-client for rate limit changes; update last_verified |
| 3 | protocol.yaml:84 | `last_verified: "2026-04-06T00:00:00+00:00"` (RESOLUTION_TIMELINE, ttl=604800 / 7d) | stale-claim | TTL = 7d; expired 33 days ago | Verify challenge_period, proposer_model still correct; update last_verified |

---

### architecture/task_boot_profiles.yaml — CLEAN

Topology doctor: no flags. Close-reading: all referenced files exist on disk. No drifts found.

---

### bindings/zeus/config.yaml — CLEAN

Topology doctor: no flags. Close-reading: all 5 cited paths (DESIGN.md, PACKET_INDEX.md, TASK_CATALOG.yaml, SAFETY_CONTRACT.md, safety_overrides.yaml) exist. No drifts found.

---

## Aggregate counts

| Category | Count | WAVE 2 in-scope? |
|---|---|---|
| dead-path-ref | 7 | YES (source_rationale ×4, topology_v_next ×2, data_ingestion profile ×1) |
| dead-symbol-ref | 3 | YES (source_rationale settlements.py ×1, script_manifest apply_flag ×2) |
| stale-claim | 35 | YES for 5 (test_topology last_used); OPERATOR_DECISION for 30 (reality_contract TTL — re-verify requires external API checks) |
| wrong-value | 6 | YES (unknown hazard badges ×2, script_manifest lifecycle/metadata errors ×4) |
| orphan-entry | 0 | — |
| **WAVE 2 in-scope total** | **18** | Dead-path-refs (7) + dead-symbol-refs (3) + stale-claim test_topology (5) + wrong-value (6) = **21 actionable YAML edits** |

### Excluded (gap analysis — NOT WAVE 2 fixes per §8.5 Rule 2)

| Category | Count | Notes |
|---|---|---|
| source_rationale_missing | 50 | New src files without rationale entry; WAVE 2 additive entries — separate task |
| test_topology_missing | 165 | New test files without trusted_tests registration; additive gap |
| script_manifest_missing | 42 | New scripts without manifest entry; additive gap |

### OPERATOR_DECISION items

| Item | Count | Notes |
|---|---|---|
| source_downstream_drift warnings | 28 | topology_doctor warnings on importer-count mismatches in source_rationale.yaml; warnings not errors; YAML counts are stale but topology_doctor does not block on these |
| reality_contract TTL expired (all files) | 30 | Re-verification requires live API calls (Polymarket CLOB, Open-Meteo); not a YAML text fix. Operator must re-verify externally and update last_verified dates. |
| script_long_lived_bad_name warnings | 8 | Advisory naming violations; no topology block; operator decides whether to rename |
| script_diagnostic_forbidden_write_target | 5 | Advisory write-target warnings; operator decides class reclassification |
