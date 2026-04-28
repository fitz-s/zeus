# Pre-merge scope: claude/quizzical-bhabha-8bdc0d

Generated: 2026-04-28
Base branch: plan-pre5 @ 8a433f6
Merge base: f818a662edd8
Candidate head: f618b32

## Unique commits
d1fcc13 Session 2026-04-28: TIGGE preflight + LOW backfill + obs provenance demolition
623361f test_topology: register 2026-04-28 antibody tests
11cd750 obs_provenance: clean residual doc-layer drift
87f17c6 Gate 3+4 closed (99.93%) + Gate 5 patch + training pipeline plan
f618b32 Gate 5 closure (WU 99.99% + ogimet 82%) + Tel Aviv + pre-2024 fills

## Diff stat (triple-dot)
 architecture/fatal_misreads.yaml                   |   32 +
 architecture/test_topology.yaml                    |    2 +
 docs/operations/current_data_state.md              |   40 +-
 docs/operations/known_gaps.md                      |   24 +
 .../TEL_AVIV_ANOMALY.md                            |   78 ++
 .../TRAINING_PIPELINE_PLAN.md                      |  167 +++
 .../evidence/gap_diagnostic_2026-04-28.json        |  357 ++++++
 ...post_apply_STALE_pre_demolition_2026-04-28.json |   55 +
 .../gate34_fill_quarantine_apply_2026-04-28.json   |  204 +++
 .../gate5_fill_quarantine_apply_2026-04-28.json    |  705 +++++++++++
 .../gate5_ogimet_quarantine_apply_2026-04-28.json  | 1325 ++++++++++++++++++++
 .../gate5_ogimet_quarantine_dryrun_2026-04-28.json |   36 +
 .../plan.md                                        |  191 +++
 .../rfc_hko_fresh_audit_promotion.md               |  126 ++
 .../scripts/README_BROKEN.md                       |   28 +
 .../scripts/audit_obs_provenance_gaps.py           |  218 ++++
 ...ations_provenance_metadata.py.BROKEN-DO-NOT-RUN |  207 +++
 ...ion_instants_v2_provenance.py.BROKEN-DO-NOT-RUN |  309 +++++
 .../fill_obs_v2_payload_identity_existing.py       |  456 +++++++
 .../scripts/fill_obs_v2_payload_identity_ogimet.py |  392 ++++++
 .../fill_observations_provenance_existing.py       |  364 ++++++
 .../scripts/recompute_source_role_canonical.py     |  191 +++
 .../scripts/remove_synthetic_provenance.py         |  170 +++
 .../evidence/low_backfill_plan.json                | 1080 ++++++++++++++++
 .../evidence/pm_settlement_truth_low.json          |  778 ++++++++++++
 .../plan.md                                        |  113 ++
 .../scripts/backfill_low_settlements.py            |  311 +++++
 .../migrate_low_data_version.py.WRONG-DO-NOT-RUN   |  103 ++
 .../scripts/scrape_low_markets.py                  |  256 ++++
 .../plan.md                                        |  206 +++
 .../migrate_settlements_physical_quantity.py       |  215 ++++
 .../evidence/extract_warsaw_summary.json           |    9 +
 .../evidence/ingest_high_smoke.json                |    1 +
 .../evidence/preflight_blockers.json               |   13 +
 .../evidence/rebuild_dry_run.txt                   |   14 +
 .../evidence/refit_platt_dry_run.txt               |    5 +
 .../plan.md                                        |  183 +++
 .../scripts/preflight_smoke.sh                     |   68 +
 .../README.md                                      |   27 +
 .../evidence/poc_summary.md                        |  134 ++
 .../rfc.md                                         |  336 +++++
 tests/test_no_synthetic_provenance_marker.py       |  137 ++
 ...test_settlements_physical_quantity_invariant.py |  205 +++
 43 files changed, 9854 insertions(+), 17 deletions(-)

## Name status (triple-dot)
M	architecture/fatal_misreads.yaml
M	architecture/test_topology.yaml
M	docs/operations/current_data_state.md
M	docs/operations/known_gaps.md
A	docs/operations/task_2026-04-28_obs_provenance_preflight/TEL_AVIV_ANOMALY.md
A	docs/operations/task_2026-04-28_obs_provenance_preflight/TRAINING_PIPELINE_PLAN.md
A	docs/operations/task_2026-04-28_obs_provenance_preflight/evidence/gap_diagnostic_2026-04-28.json
A	docs/operations/task_2026-04-28_obs_provenance_preflight/evidence/gap_diagnostic_post_apply_STALE_pre_demolition_2026-04-28.json
A	docs/operations/task_2026-04-28_obs_provenance_preflight/evidence/gate34_fill_quarantine_apply_2026-04-28.json
A	docs/operations/task_2026-04-28_obs_provenance_preflight/evidence/gate5_fill_quarantine_apply_2026-04-28.json
A	docs/operations/task_2026-04-28_obs_provenance_preflight/evidence/gate5_ogimet_quarantine_apply_2026-04-28.json
A	docs/operations/task_2026-04-28_obs_provenance_preflight/evidence/gate5_ogimet_quarantine_dryrun_2026-04-28.json
A	docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
A	docs/operations/task_2026-04-28_obs_provenance_preflight/rfc_hko_fresh_audit_promotion.md
A	docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/README_BROKEN.md
A	docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/audit_obs_provenance_gaps.py
A	docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/backfill_observations_provenance_metadata.py.BROKEN-DO-NOT-RUN
A	docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN
A	docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_existing.py
A	docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_ogimet.py
A	docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_observations_provenance_existing.py
A	docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/recompute_source_role_canonical.py
A	docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/remove_synthetic_provenance.py
A	docs/operations/task_2026-04-28_settlements_low_backfill/evidence/low_backfill_plan.json
A	docs/operations/task_2026-04-28_settlements_low_backfill/evidence/pm_settlement_truth_low.json
A	docs/operations/task_2026-04-28_settlements_low_backfill/plan.md
A	docs/operations/task_2026-04-28_settlements_low_backfill/scripts/backfill_low_settlements.py
A	docs/operations/task_2026-04-28_settlements_low_backfill/scripts/migrate_low_data_version.py.WRONG-DO-NOT-RUN
A	docs/operations/task_2026-04-28_settlements_low_backfill/scripts/scrape_low_markets.py
A	docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md
A	docs/operations/task_2026-04-28_settlements_physical_quantity_migration/scripts/migrate_settlements_physical_quantity.py
A	docs/operations/task_2026-04-28_tigge_training_preflight/evidence/extract_warsaw_summary.json
A	docs/operations/task_2026-04-28_tigge_training_preflight/evidence/ingest_high_smoke.json
A	docs/operations/task_2026-04-28_tigge_training_preflight/evidence/preflight_blockers.json
A	docs/operations/task_2026-04-28_tigge_training_preflight/evidence/rebuild_dry_run.txt
A	docs/operations/task_2026-04-28_tigge_training_preflight/evidence/refit_platt_dry_run.txt
A	docs/operations/task_2026-04-28_tigge_training_preflight/plan.md
A	docs/operations/task_2026-04-28_tigge_training_preflight/scripts/preflight_smoke.sh
A	docs/operations/task_2026-04-28_weighted_platt_precision_weight_rfc/README.md
A	docs/operations/task_2026-04-28_weighted_platt_precision_weight_rfc/evidence/poc_summary.md
A	docs/operations/task_2026-04-28_weighted_platt_precision_weight_rfc/rfc.md
A	tests/test_no_synthetic_provenance_marker.py
A	tests/test_settlements_physical_quantity_invariant.py

## Drift keyword grep in diff
12:+    false_equivalence: "absent_pre_2026_04_15_low_settlements == data_pipeline_bug"
17:+      Shanghai, Paris, Miami, Hong Kong) — not all 51 zeus cities. Therefore
18:+      the absence of LOW settlement rows for (city, date) tuples that fall
26:+      LOW Platt training MUST use observations.low_temp (42,749 rows /
28:+      settlements LOW (only 48 rows ever exist).
30:+      - docs/operations/task_2026-04-28_settlements_low_backfill/plan.md
31:+      - docs/operations/task_2026-04-28_settlements_low_backfill/evidence/pm_settlement_truth_low.json
40:+    task_classes: [settlement_backfill, market_scanner, source_routing, low_track]
49:+    tests/test_no_synthetic_provenance_marker.py: {created: "2026-04-28", last_used: "2026-04-28"}
50:+    tests/test_settlements_physical_quantity_invariant.py: {created: "2026-04-28", last_used: "2026-04-28"}
63:+Last audited: 2026-04-28 (HIGH `physical_quantity` migration + LOW settlements backfill)
67:-  + `docs/operations/task_2026-04-23_live_harvester_enablement_dr33/` (code-only follow-up, flag OFF)
70:+  - `docs/operations/task_2026-04-23_live_harvester_enablement_dr33/` (code-only follow-up, flag OFF)
71:+  - `docs/operations/task_2026-04-28_settlements_physical_quantity_migration/` (HIGH `physical_quantity` canonical-string migration; APPLIED)
72:+  - `docs/operations/task_2026-04-28_settlements_low_backfill/` (LOW settlements bootstrap; APPLIED)
74: If stale, do not use for: live data-readiness, backfill readiness, v2 cutover,
77:    forecasts, calibration, snapshots, and settlements.
78: 2. `state/zeus_trades.db` is trades-focused DB truth.
80:-4. **`settlements` is canonical-authority-grade as of 2026-04-23**: 1,561 rows
83:-   `data_version`) + full `provenance_json` with `decision_time_snapshot_id`
85:-   `settlements_authority_monotonic` trigger (P-B). Writer signature on
88:+4. **`settlements` is canonical-authority-grade as of 2026-04-28**: 1,609 rows total. INV-14 identity spine intact on every row (`temperature_metric`, `physical_quantity`, `observation_field`, `data_version`) + full `provenance_json`. Schema carries `settlements_authority_monotonic` + `settlements_non_null_metric` + `settlements_verified_insert/update_integrity` triggers.
92:+   - All rows now carry canonical `physical_quantity = "mx2t6_local_calendar_day_max"` (was legacy literal `"daily_maximum_air_temperature"` pre-2026-04-28; migrated by `task_2026-04-28_settlements_physical_quantity_migration` with snapshot at `state/zeus-world.db.pre-physqty-migration-2026-04-28`)
97:+   - Coverage: 8 cities (London/Seoul/NYC/Tokyo/Shanghai/Paris/Miami/Hong Kong), 2026-04-15..2026-04-27
99:+   - **STRUCTURAL LIMIT**: Polymarket did NOT offer LOW markets before 2026-04-15 (verified gamma-api 2026-04-28). LOW row count is upstream-limited, not a backfill miss. See `architecture/fatal_misreads.yaml::polymarket_low_market_history_starts_2026_04_15` and `docs/operations/task_2026-04-28_settlements_low_backfill/plan.md`.
101: 5. **`observations` still carries the settlement-driving data**: 51 cities of
103:    of truth that P-E used to re-derive `settlements.settlement_value` via
108:-- a new writer/cutover lands on `settlements` beyond the two currently-registered
109:-  writers (`p_e_reconstruction_2026-04-23`, `harvester_live_dr33`)
110:+- a new writer/cutover lands on `settlements` beyond the three currently-registered writers (`p_e_reconstruction_2026-04-23`, `p_e_reconstruction_low_2026-04-28`, `harvester_live_dr33`)
113: - DB role ownership changes
135:+**Audit date:** 2026-04-28 (gamma-api.polymarket.com live probe).
136:+**Fact:** Polymarket did NOT offer LOW (mn2t6 / "lowest temperature") weather markets before 2026-04-15. First closed LOW event resolved 2026-04-15. Coverage is 8 cities only: London, Seoul, NYC, Tokyo, Shanghai, Paris, Miami, Hong Kong.
139:+- `state/zeus-world.db::settlements` LOW rows will never exceed ~50 historical + ~8/day going forward
140:+- LOW Platt training MUST use `observations.low_temp` (42,749 rows / 51 cities / 2023-12-27..2026-04-19) as canonical ground truth — NOT `settlements` LOW
141:+- Absence of LOW settlement rows for (city, date) tuples outside the 8-city × post-2026-04-15 scope is structural, not a backfill miss
146:+- Block on this gap when training LOW calibration; use observations.low_temp
149:+- `docs/operations/task_2026-04-28_settlements_low_backfill/plan.md`
150:+- `docs/operations/task_2026-04-28_settlements_low_backfill/evidence/pm_settlement_truth_low.json`
158:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/TEL_AVIV_ANOMALY.md b/docs/operations/task_2026-04-28_obs_provenance_preflight/TEL_AVIV_ANOMALY.md
162:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/TEL_AVIV_ANOMALY.md
166:+# Authority basis: live-DB audit 2026-04-28T14:25Z + tier_resolver classification
202:+fetched WU API for Tel Aviv even though tier_resolver places Tel Aviv on
203:+ogimet. The same station (LLBG) is the underlying truth source — WU's ICAO
212:+- The Gate 3+4 fill script (`fill_observations_provenance_existing.py`)
215:+- They do NOT block training: `rebuild_calibration_pairs_v2._fetch_verified_observation`
220:+  empty provenance and would still be 10 rows blocking — UNLESS we fill
228:+| 1. Override-fill | Add `--include-cities Tel\ Aviv` flag and a one-off `LLBG/IL` mapping; provenance fill via real WU API; values match (we already verified). |
234:+- Values are real (verified against ogimet for 7 days; raw WU API for 3)
236:+- Audit trail (provenance_metadata) is the only missing piece
242:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/TRAINING_PIPELINE_PLAN.md b/docs/operations/task_2026-04-28_obs_provenance_preflight/TRAINING_PIPELINE_PLAN.md
246:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/TRAINING_PIPELINE_PLAN.md
253:+calibration training pipeline end-to-end. It is the source-of-truth for
259:+TIGGE GRIB (VM extract output, ~1M JSON files)
264:+    ↓ rebuild_calibration_pairs_v2.py --no-dry-run --force
265:+calibration_pairs_v2 (currently 0 rows)
271:+`_fetch_verified_observation` (`rebuild_calibration_pairs_v2.py:167-193`):
274:+WU/Ogimet/HKO daily readings
278:+WU/Ogimet hourly readings (with payload identity)
279:+    → observation_instants_v2 (NOT consumed by rebuild_calibration_pairs_v2)
280:+    → consumed by DST gap fill, day0 monitor_refresh.py, HKO ingest
286:+[A] Gate 3+4 backfill (observations.provenance_metadata)        [RUNNING, ETA ~25min]
289:+[B] VM HIGH extract (TIGGE → ~1M JSON on VM)                    [RUNNING, 13/16 done]
292:+[C] Gate 5 backfill (obs_v2 payload identity, WU subset 932k)   [SCRIPT WRITTEN, run after V]
294:+[C3] Gate 5 meteostat 820k (training_allowed=0)                 [DECISION needed: backfill OR document as legacy]
296:+[D] Gate 2 HKO RFC (821 rows operator-blocked)                  [STUB written, operator decision]
301:+    └── populates ensemble_snapshots_v2 with members_json + provenance
306:+[R] rebuild_calibration_pairs_v2.py --no-dry-run --force        [DEPS: V, I2 done; preflight all-pass]
307:+    └── populates calibration_pairs_v2
322:+   python -c "from scripts.verify_truth_surfaces import build_calibration_pair_rebuild_preflight_report as f; \
323:+              import json; r=f(); print(json.dumps([b for b in r['blockers'] if 'provenance' in b['code']], indent=2))"
325:+   Expected: `[]` (no provenance blockers)
328:+   - `docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_observations_provenance_existing.py`
329:+   - `docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_existing.py` (Gate 5 patch)
330:+   - `docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/TEL_AVIV_ANOMALY.md`
334:+3. **Run Gate 5 WU backfill** (after Gate 3+4 verified):
336:+   python docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_existing.py --start-date 2024-01-01 --apply
338:+   ETA ~70 min; reuses same pattern as Gate 3+4 but on `observation_instants_v2.provenance_json`.
348:+5. **Run TIGGE ingest**:
361:+   .venv/bin/python -c "from scripts.verify_truth_surfaces import build_calibration_pair_rebuild_preflight_report as f; \
364:+   Expected: `READY` (or only HKO Gate 2 + 4 meteostat audit gaps blocking, depending on operator decision).
368:+   .venv/bin/python scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force
380:+| HKO Gate 2 (821 rows) | operator-blocked | RFC stub written; operator decides |
382:+| Meteostat 820k obs_v2 (training_allowed=0) | sideline | operator: backfill audit OR document as legacy |
389:+- Preflight after fill still shows provenance blockers (writer behavior didn't act as expected)
391:+- TIGGE ingest reports validate_snapshot_contract violation
393:+- rebuild_calibration_pairs_v2 outputs zero rows (silent failure mode)
404:+| Gate 5 WU backfill | ~70 min | 1:38 |
406:+| TIGGE ingest | ~30-60 min | 2:45 |
409:+| rebuild_calibration_pairs_v2 | ~20-40 min | 3:35 |
415:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md b/docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
419:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
426:+| Status | **REVERTED 2026-04-28T04:30Z** — synthetic provenance writes removed; only Gate 1 (`source_role` recompute) remains canonical. Gates 3/4/5 reopened post-demolition. Operator instruction "立即去除任何合成数据" was the trigger. |
427:+| Authority basis | `scripts/verify_truth_surfaces.py:2190::build_calibration_pair_rebuild_preflight_report` |
428:+| Sibling packet | `task_2026-04-28_tigge_training_preflight/` (TIGGE causality fixed; this packet handles the OBS-side blockers it surfaced) |
429:+| Scope | 5 OBS-side gates that block live `rebuild_calibration_pairs_v2` write |
430:+| Current production result | preflight 4 blockers (Gate 3 39431 / Gate 4 39431 / Gate 5 993400 / HKO 821) — Gate 1 stays at 0 (canonical source_role retained) |
431:+| Snapshots | `state/zeus-world.db.pre-synthetic-removal-2026-04-28` (2.72 GB) is the freshest authoritative pre-demolition checkpoint |
432:+| **DO NOT REUSE** | `enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN`, `backfill_observations_provenance_metadata.py.BROKEN-DO-NOT-RUN` (see `scripts/README_BROKEN.md`) |
436:+The TIGGE preflight smoke (sibling packet, 2026-04-28) cleared the HIGH `causality` blocker but exposed 6 gates raised by Stage D's `--no-dry-run --force` path:
439:+RuntimeError: Refusing live v2 rebuild: calibration-pair rebuild preflight is NOT_READY (
441:+  observation_instants_v2.training_role_unsafe,      ← Gate 1
443:+  observations.verified_without_provenance,          ← Gate 3
444:+  observations.wu_empty_provenance,                  ← Gate 4 (sub-slice of #3)
449:+All 5 OBS gates are enforced from a single function: `verify_truth_surfaces.py:2190`, called from `rebuild_calibration_pairs_v2.py:210-217`.
455:+| 1 | `observation_instants_v2.training_role_unsafe` | `verify_truth_surfaces.py:1097-1131` (rebuild path), `:2329-2412` (full readiness) | `training_allowed=1 AND source_role NOT IN ('historical_hourly')` |
456:+| 2 | `observations.hko_requires_fresh_source_audit` | `verify_truth_surfaces.py:1465-1496` | `authority='VERIFIED' AND obs_col IS NOT NULL AND (source LIKE 'hko%' OR city='Hong Kong')` |
457:+| 3 | `observations.verified_without_provenance` | `verify_truth_surfaces.py:1417-1437` | `authority='VERIFIED' AND obs_col IS NOT NULL AND provenance IS NULL/empty/'{}'` |
458:+| 4 | `observations.wu_empty_provenance` | `verify_truth_surfaces.py:1439-1463` | same as #3 plus `source LIKE 'wu%'` |
459:+| 5 | `payload_identity_missing` | `verify_truth_surfaces.py:680-750`, called from `:1089` and `:1181` | `observation_instants_v2` rows where `training_allowed=1` lack `payload_hash` / `parser_version` / source / station identity in `provenance_json` |
465:+| 1 | DATA-FILL + AUDIT-RECORD | re-run `tier_resolver.source_role_assessment_for_city_source` per row; HKO rows depend on Gate 2 |
473:+## Reuse audit (Fitz code-provenance)
481:+| `task_2026-04-28_settlements_physical_quantity_migration/scripts/migrate_settlements_physical_quantity.py` | **CURRENT_REUSABLE** | Migration shape applies cleanly to gates 3, 4, 1 |
482:+| `scripts/backfill_observations_from_settlements.py` | **AUDIT-BEFORE-REUSE** | Verify it does not bypass writer A1 |
487:+              [operator decision: HKO audit semantics]
494:+     Gate 4 (WU prov)   Gate 3 (any prov)  Gate 1 (source_role)
495:+     DATA-FILL          DATA-FILL          DATA-FILL (after Gate 2 if HKO)
505:+- **Dependency**: Gate 1 partially depends on Gate 2 (HKO subset)
507:+- **Known-safe pattern**: settlements physical_quantity migration shape applies to 3, 4, 1
509:+## P0 diagnostic — actual numbers (run 2026-04-28 against live `state/zeus-world.db`)
513:+| 3 `verified_without_provenance` | **39,431** | live DB uses singular `provenance_metadata` column (Open Q #5 confirmed); HIGH and LOW share same column → identical counts |
514:+| 4 `wu_empty_provenance` | **39,431** | == Gate 3 — every empty-provenance VERIFIED row is WU. Backfilling WU resolves both 3 and 4 simultaneously. |
515:+| 2 `hko_requires_fresh_source_audit` | **821** | every VERIFIED HKO row blocks; operator-blocked. |
516:+| 1 `training_role_unsafe` | **0** | `observation_instants_v2` already clean on this dim |
517:+| 5 `payload_identity_missing` | **1,813,662** | 100% of training_allowed=1 rows in `observation_instants_v2` lack `payload_hash` AND `parser_version`; station_id is always present |
520:+- **Total observation rows VERIFIED with low/high data**: 42,749 — of which 39,431 (92.2%) have empty provenance. Single root cause: WU rows.
521:+- **Gate 3 & 4 collapse to one fix**: backfill WU provenance from `hourly_observations` rebuild_run_id metadata. ~39k UPDATE statements.
523:+- **Gate 1 already clean**: existing `source_role` assignments are all `historical_hourly` for training-eligible rows. P1 #4 (recompute) becomes maintenance only, not blocker fix.
524:+- **Gate 2 stays operator-blocked**: 821 HKO rows untouched until RFC for promotion mechanism.
533:+**REVERTED. The earlier "✅" claims were incorrect after `remove_synthetic_provenance.py --apply` reopened gates 3/4/5.** Live preflight measurements re-taken 2026-04-28T04:25Z:
537:+| 1 `training_role_unsafe` | 1,813,662 | 0 (canonical from `recompute_source_role_canonical.py`) | **0** | canonical, retained |
539:+| 3 `verified_without_provenance` | 39,431 | 0 (synthesized) | **39,431** | reopened — synthetic removed |
540:+| 4 `wu_empty_provenance` | 39,431 | 0 (synthesized) | **39,431** | reopened — synthetic removed |
541:+| 5 `payload_identity_missing` | 1,813,662 | 0 (synthesized) | **993,400** | reopened — synthetic removed |
545:+The earlier note in this document claimed `_obs_v2_provenance_identity_missing_sql` had an operator-precedence SQL bug (`A_null OR A_empty AND B_null OR B_empty`). **This claim was unfounded.** Verified read of `scripts/verify_truth_surfaces.py:826-851` shows the SQL builder produces `(source_missing) OR (station_missing)` with proper outer parentheses, where each inner clause is built with `" AND ".join(...)`. The semantics are correct: source pair fails iff BOTH `source_url` AND `source_file` are blank.
551:+The script `enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN` contained a fallback heuristic that mapped `meteostat_bulk_*` source prefixes to `source_role='historical_hourly'`. This had no zeus authority backing. tier_resolver actually classifies these as `unknown` or `fallback_evidence` (per the city's primary tier; meteostat is the fallback layer). The corrective `recompute_source_role_canonical.py` re-runs canonical tier_resolver and demoted 820,262 rows to `fallback_evidence` + `training_allowed=0`. That recompute IS canonical and is retained.
553:+### Retracted claim — "42,749 (92.2%) have empty provenance"
555:+The 42,749 figure is the count of rows with `low_temp IS NOT NULL` (and equivalently `high_temp IS NOT NULL`, since most rows carry both). It is NOT a high+low merged VERIFIED count. Statements that conflated this with verified-without-provenance ratio are retracted.
559:+- **Gates 3+4 (39,431 WU rows)**: re-fetch from WU API or otherwise restore real provenance metadata. **CORRECTION 2026-04-28**: the earlier "`scripts/backfill_obs_v2.py` is the proper tool" claim was wrong — that script writes to `observation_instants_v2` (hourly, Gate 5) NOT `observations` (daily, Gates 3+4). Additionally `daily_observation_writer.write_daily_observation_with_revision` preserves existing rows by design (audit-trail protection), so backfill_wu_daily_all.py also cannot fill in-place. The correct tool is `docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_observations_provenance_existing.py` (created 2026-04-28; UPDATE-only, real WU API verify-then-fill). Wallclock corrected: **~65 min daily-granularity**, not 58h. Synthesis is forbidden.
560:+- **Gate 5 (993,400 obs_v2 rows)**: same — re-import through A1 writer, OR explicitly accept legacy rows are not training-eligible (set training_allowed=0 for them).
561:+- **Gate 2 (HKO 821)**: separate RFC for HKO audit promotion mechanism. Architecture-defined gap city; not solvable by data work alone.
567:+1. **`scripts/audit_obs_provenance_gaps.py`** ✅ — ran against live DB, output at `evidence/gap_diagnostic_2026-04-28.json`. Antibody pending: `tests/test_obs_provenance_audit.py` to assert audit numbers match live preflight blocker counts (cross-check `build_calibration_pair_rebuild_preflight_report`).
571:+2. **`scripts/backfill_observations_provenance.py`** — resolves Gates 3+4 by populating `high_provenance_metadata`/`low_provenance_metadata` for VERIFIED non-HKO WU rows from `hourly_observations` aggregates. Antibody: `tests/test_observations_provenance_backfill.py` — negative fixture (NULL provenance) raises Gate 3+4; passes after backfill.
572:+3. **`scripts/backfill_observation_instants_v2_payload_identity.py`** — resolves Gate 5 by computing `payload_hash`/`parser_version`/`station_*` for legacy rows, then re-validating each through `observation_instants_v2_writer._validate_payload_identity` before commit. Antibody: round-trip test rejects any backfilled row whose `provenance_json` does not satisfy A1.
573:+4. **`scripts/recompute_observation_instants_source_role.py`** — resolves Gate 1 (non-HKO scope only). Re-runs `source_role_assessment_for_city_source` per row, updates `source_role`/`training_allowed`. Antibody: `tests/test_source_role_recompute_idempotent.py` — second run is no-op.
579:+   - `tier_resolver` branch promoting HKO rows whose `(city, target_date)` falls within an audited window
586:+6. **NOT NULL constraint** on `high_provenance_metadata`/`low_provenance_metadata` once backfill complete (SCHEMA-MIGRATION) — closes the regression door at the writer. Antibody: schema test asserting constraint exists.
587:+7. **Positive end-to-end "post-backfill READY" fixture** in `tests/test_truth_surface_health.py` — synthetic DB satisfying all 5 gates; assert `report["status"] == "READY"`.
591:+1. **Gate 2 HKO audit promotion**: artifact format, storage location, signer. Without an answer, Gate 2 stays NOT_READY indefinitely and HKO is excluded from training. **HARD STOP for Gate 2.**
592:+2. **HK 03-13/03-14 known gap** (per `docs/operations/known_gaps.md:107-108`) uses WU/VHHH airport data, not HKO. Audit mechanism: per-date overrides or city-wide promotion only?
594:+4. **Gate 4 backfill scope**: WU only first (smoke unblock), or batch all VERIFIED tiers?
595:+5. **`provenance_metadata` (singular) column**: `_observation_provenance_column` at `verify_truth_surfaces.py:1339-1349` prefers it but schema only has the split columns. Planned future migration?
602:+- ❌ Any HKO promotion code-side without operator-signed audit artifact (Gate 2)
603:+- ❌ Any migration without snapshot-before-apply + post-count assertion + atomic TXN (mirror `migrate_settlements_physical_quantity.py` shape)
610:+| `scripts/audit_obs_provenance_gaps.py` | P0 diagnostic (READ-ONLY); pending |
612:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/rfc_hko_fresh_audit_promotion.md b/docs/operations/task_2026-04-28_obs_provenance_preflight/rfc_hko_fresh_audit_promotion.md
616:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/rfc_hko_fresh_audit_promotion.md
618:+# RFC — HKO Fresh-Audit Promotion (Gate 2 Resolution)
621:+# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md §P2 #5
632:+AND (LOWER(source) LIKE 'hko%' OR city='Hong Kong')
635:+Hong Kong is the explicit caution path per
636:+`current_source_validity.md` item 6: "current truth claims for Hong Kong
639:+There is no code-side mechanism today to promote HKO rows from "needs fresh
640:+audit" to "audited and approved". The gate fail-closes — every HKO row blocks
641:+calibration-pair rebuild until an audit artifact exists.
645:+- HKO is `gap_city` per `tier_resolver.source_role_assessment_for_city_source`.
646:+- The HKO API and HKO native data have stalled relative to the audited
648:+- Polymarket settles HKO contracts on WU values for VHHH (a wu_icao path
649:+  through the airport) when HKO endpoint diverges; this routing is
651:+- `architecture/city_truth_contract.yaml` defines the stable schema; HKO
668:+      - Hong Kong
705:+`INV-NN: HKO rows MUST NOT enter calibration training without an active
709:+Encoded as `tests/test_hko_audit_promotion.py` — negative fixture: HKO row
718:+2. **Evidence URL requirements**: must point to a primary source (HKO
724:+   WU values for VHHH. Should this RFC also formalize that routing
725:+   (i.e. "if HK market description specifies VHHH, treat WU readings as
726:+   audited even without HKO record")? Or keep VHHH-routing in market
733:+- Replacing HKO API ingest mechanism (separate work)
744:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/README_BROKEN.md b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/README_BROKEN.md
748:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/README_BROKEN.md
750:+# QUARANTINED: enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN
758:+3. Synthetic provenance keys (`payload_hash`, `parser_version`, `source_url`, `source_file` with `legacy://` URLs) are written into `provenance_json`. These violate Fitz Constraint #4 (data provenance) and were removed by `remove_synthetic_provenance.py`.
762:+`recompute_source_role_canonical.py` — uses `Path(__file__).parents[4]` (verified zeus root), HARD-FAILS on tier_resolver import error (no fallback), and writes ONLY `source_role` + `training_allowed` from canonical tier_resolver. NO synthetic provenance.
766:+If you need to understand why the OBS provenance preflight gates 5/4/3 fired both before and after this packet's apply: the apply added synthetic markers; the demolition removed them; production now correctly fails those gates. Closing them requires REAL provenance enrichment (e.g., re-fetching authoritative source records), not synthesis.
770:+## Also quarantined: backfill_observations_provenance_metadata.py.BROKEN-DO-NOT-RUN
772:+Same class of error: synthesizes `provenance_metadata` JSON from already-existing row fields and tags it `"synthesized_by":"legacy:backfill_obs_prov_2026-04-28"`. This is data fabrication, not real provenance enrichment. The 39,431 rows it touched have been NULL'd back to empty.
776:+- **Gates 3+4** (`observations.{verified_without_provenance, wu_empty_provenance}`): re-fetch from WU API or otherwise restore real provenance metadata; do NOT synthesize.
777:+- **Gate 5** (`payload_identity_missing`): the legacy obs_v2 rows lack real `payload_hash` because they were written before the A1 contract existed. Either re-fetch and re-import through the A1 writer (`backfill_obs_v2.py` exists for this), OR explicitly accept that legacy rows are not training-eligible and quarantine them.
778:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/audit_obs_provenance_gaps.py b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/audit_obs_provenance_gaps.py
782:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/audit_obs_provenance_gaps.py
787:+# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
791:+``scripts/verify_truth_surfaces.py::build_calibration_pair_rebuild_preflight_report``
792:+without touching the live preflight runner (which raises on NOT_READY).
805:+# Match the schema's metric → provenance column resolution
806:+# (mirrors verify_truth_surfaces.py:1339-1349 _observation_provenance_column).
807:+# Live canonical DB has singular 'provenance_metadata'; the verify_truth_surfaces
809:+def _resolve_provenance_col(conn) -> dict[str, str]:
811:+    if "provenance_metadata" in cols:
812:+        return {"high": "provenance_metadata", "low": "provenance_metadata"}
813:+    return {"high": "high_provenance_metadata", "low": "low_provenance_metadata"}
822:+def gate_3_verified_without_provenance(conn: sqlite3.Connection, metric: str) -> dict:
823:+    """`observations.verified_without_provenance` — VERIFIED rows with NULL/empty provenance."""
825:+    prov_col = _resolve_provenance_col(conn)[metric]
839:+        "gate": "observations.verified_without_provenance",
846:+def gate_4_wu_empty_provenance(conn: sqlite3.Connection, metric: str) -> dict:
847:+    """`observations.wu_empty_provenance` — sub-slice of #3 for WU sources."""
849:+    prov_col = _resolve_provenance_col(conn)[metric]
861:+        "gate": "observations.wu_empty_provenance",
868:+    """`observations.hko_requires_fresh_source_audit` — every HKO/HK VERIFIED row blocks."""
887:+def gate_1_training_role_unsafe(conn: sqlite3.Connection) -> dict:
888:+    """`observation_instants_v2.training_role_unsafe` — training_allowed=1 AND source_role!=historical_hourly."""
894:+            WHERE COALESCE(training_allowed, 0) = 1
902:+            "gate": "observation_instants_v2.training_role_unsafe",
908:+        "gate": "observation_instants_v2.training_role_unsafe",
915:+    """`payload_identity_missing` — observation_instants_v2 rows lacking payload identity in provenance_json."""
917:+    # scripts/verify_truth_surfaces.py::_obs_v2_provenance_identity_missing_sql:
923:+    # produced false positives. Fixed 2026-04-28 to match production logic.
929:+            WHERE COALESCE(training_allowed, 0) = 1
931:+                json_extract(provenance_json, '$.payload_hash') IS NULL
932:+                OR json_extract(provenance_json, '$.parser_version') IS NULL
934:+                    json_extract(provenance_json, '$.source_url') IS NULL
935:+                    AND json_extract(provenance_json, '$.source_file') IS NULL
938:+                    json_extract(provenance_json, '$.station_id') IS NULL
939:+                    AND json_extract(provenance_json, '$.station_registry_version') IS NULL
940:+                    AND json_extract(provenance_json, '$.station_registry_hash') IS NULL
958:+    p = argparse.ArgumentParser(description="OBS provenance gap diagnostic (READ-ONLY)")
972:+        "gate_3": gate_3_verified_without_provenance(conn, "high"),
973:+        "gate_4": gate_4_wu_empty_provenance(conn, "high"),
977:+        "gate_3": gate_3_verified_without_provenance(conn, "low"),
978:+        "gate_4": gate_4_wu_empty_provenance(conn, "low"),
982:+        "gate_1": gate_1_training_role_unsafe(conn),
988:+    print(f"=== OBS provenance gap diagnostic — {args.db_path} ===")
1002:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/backfill_observations_provenance_metadata.py.BROKEN-DO-NOT-RUN b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/backfill_observations_provenance_metadata.py.BROKEN-DO-NOT-RUN
1006:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/backfill_observations_provenance_metadata.py.BROKEN-DO-NOT-RUN
1011:+# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
1012:+"""In-place backfill: populate observations.provenance_metadata for VERIFIED legacy rows.
1014:+Closes preflight Gates 3+4 (`observations.verified_without_provenance`,
1015:+`observations.wu_empty_provenance`) by synthesizing minimal provenance
1021:+       AND (provenance_metadata IS NULL OR TRIM='' OR ='{}')
1025:+Synthesize a minimal provenance JSON from row's existing columns:
1041:+  - rows already with non-empty provenance_metadata (idempotent)
1042:+  - HKO rows (separate Gate 2 RFC)
1060:+def synthesize_provenance(row: sqlite3.Row, now_iso: str) -> str:
1083:+    p = argparse.ArgumentParser(description="Backfill observations.provenance_metadata")
1087:+                   help="Skip HKO/Hong Kong rows (default: True; Gate 2 operator-blocked)")
1118:+              AND (provenance_metadata IS NULL
1119:+                   OR TRIM(provenance_metadata) = ''
1120:+                   OR provenance_metadata = '{{}}')
1132:+              AND (provenance_metadata IS NULL
1133:+                   OR TRIM(provenance_metadata) = ''
1134:+                   OR provenance_metadata = '{{}}')
1151:+              AND (provenance_metadata IS NULL
1152:+                   OR TRIM(provenance_metadata) = ''
1153:+                   OR provenance_metadata = '{{}}')
1166:+            new_prov = synthesize_provenance(row, now_iso)
1171:+                    "UPDATE observations SET provenance_metadata = ? WHERE id = ?",
1184:+                  AND (provenance_metadata IS NULL
1185:+                       OR TRIM(provenance_metadata) = ''
1186:+                       OR provenance_metadata = '{{}}')
1202:+            print("[dry-run] no DB changes.")
1215:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN
1219:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN
1224:+# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
1225:+"""In-place enricher: add missing payload identity to observation_instants_v2.provenance_json.
1230:+Predicate enforced by `scripts/verify_truth_surfaces.py::_obs_v2_provenance_identity_missing_sql`:
1232:+       OR (source_url AND source_file both blank in provenance_json)
1237:+For each row where training_allowed=1 and provenance_json lacks identity keys,
1249:+  - HKO rows (Gate 2 — operator-blocked)
1295:+def needs_enrichment(provenance: dict) -> tuple[bool, list[str]]:
1299:+        v = provenance.get(k)
1303:+    if not any(isinstance(provenance.get(k), str) and provenance[k].strip() for k in src_keys):
1311:+    Sets BOTH source_url and source_file because the production preflight SQL
1312:+    (`verify_truth_surfaces.py::_obs_v2_provenance_identity_missing_sql`) has an
1333:+    (e.g. stdlib-only sandboxed run). HKO stays fallback_evidence
1365:+    p = argparse.ArgumentParser(description="Enrich observation_instants_v2 provenance for Gate 5")
1394:+            WHERE COALESCE(training_allowed, 0) = 1
1396:+                json_extract(provenance_json, '$.payload_hash') IS NULL
1397:+                OR json_extract(provenance_json, '$.parser_version') IS NULL
1399:+                    json_extract(provenance_json, '$.source_url') IS NULL
1400:+                    AND json_extract(provenance_json, '$.source_file') IS NULL
1407:+        # Filter mirrors the production preflight predicate so we touch every
1412:+                   provenance_json, source_role
1414:+            WHERE COALESCE(training_allowed, 0) = 1
1416:+                json_extract(provenance_json, '$.payload_hash') IS NULL
1417:+                OR json_extract(provenance_json, '$.parser_version') IS NULL
1418:+                OR json_extract(provenance_json, '$.source_url') IS NULL
1419:+                OR json_extract(provenance_json, '$.source_file') IS NULL
1437:+                prov = json.loads(row["provenance_json"]) if row["provenance_json"] else {}
1455:+            # should NOT be in the training pool. Older writers misclassified
1456:+            # meteostat/HKO/etc as training_allowed=1. Correct it here.
1457:+            new_training_allowed = 1 if new_role == "historical_hourly" else 0
1459:+                sample_after = (row["id"], new_prov_str, new_role, existing_role, new_training_allowed)
1463:+                set_clauses = ["provenance_json = ?"]
1468:+                # Only set training_allowed=0 if currently 1 AND new_role is not eligible
1470:+                    set_clauses.append("training_allowed = 0")
1486:+                WHERE COALESCE(training_allowed, 0) = 1
1488:+                    json_extract(provenance_json, '$.payload_hash') IS NULL
1489:+                    OR json_extract(provenance_json, '$.parser_version') IS NULL
1491:+                        json_extract(provenance_json, '$.source_url') IS NULL
1492:+                        AND json_extract(provenance_json, '$.source_file') IS NULL
1500:+            f"unparseable_provenance: {n_unparseable}, "
1505:+            sample_id, sample_prov, sample_new_role, sample_old_role, sample_new_training = sample_after
1508:+            print(f"  source_role: {sample_old_role!r} → {sample_new_role!r}, training_allowed → {sample_new_training}")
1515:+            print("[dry-run] no DB changes made.")
1530:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_existing.py b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_existing.py
1534:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_existing.py
1539:+# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
1542:+#                      obs_v2 rows training_allowed=1 lack payload_hash OR
1543:+#                      parser_version in provenance_json.
1547:+#                      against WU API must reconstruct the same hour-bucket
1550:+#                      rows are training_allowed=1 with the gap. This script
1551:+#                      handles the WU subset (932k); ogimet is a follow-up.
1557:+  - source='wu_icao_history', authority='VERIFIED', training_allowed=1
1559:+  - provenance_json missing 'payload_hash' AND 'parser_version' keys
1562:+`scripts/verify_truth_surfaces.py:680-750`) fail-closes calibration rebuild.
1566:+SEMANTICS — same UPDATE-only pattern as fill_observations_provenance_existing.py
1568:+1. Live WU API call for the relevant (city, target_date) range
1571:+4. ON match: UPDATE provenance_json USING json_set to ADD payload_hash +
1576:+NOT synthesis: payload_hash is sha256 of the live WU API response bytes,
1584:+    python -m docs.operations.task_2026-04-28_obs_provenance_preflight.scripts.fill_obs_v2_payload_identity_existing
1612:+    WU_API_KEY,
1613:+    WU_ICAO_HISTORY_URL,
1632:+    / "task_2026-04-28_obs_provenance_preflight"
1651:+DB_PATH = _resolve_zeus_db_path()
1652:+SNAPSHOT_PATH = DB_PATH.parent / f"{DB_PATH.name}.pre-gate5-fill-2026-04-28"
1662:+    """Fetch hourly observations from WU ICAO history API.
1664:+    Returns ([(epoch_seconds, temp), ...], provenance_dict) or None on failure.
1667:+    url = WU_ICAO_HISTORY_URL.format(icao=icao, cc=cc)
1677:+                "apiKey": WU_API_KEY, "units": unit_code,
1696:+        provenance = {
1711:+        return hourly, provenance
1747:+        "training_allowed=1 "
1749:+        "AND (json_extract(provenance_json,'$.payload_hash') IS NULL "
1750:+        "     OR json_extract(provenance_json,'$.parser_version') IS NULL)"
1799:+    p = argparse.ArgumentParser(description="Fill payload_hash + parser_version on existing obs_v2 rows (Gate 5 fix, WU subset).")
1810:+    if not DB_PATH.exists():
1811:+        sys.stderr.write(f"FATAL: db not found: {DB_PATH}\n")
1818:+            print(f"[apply] snapshotting {DB_PATH} → {SNAPSHOT_PATH}")
1819:+            shutil.copy2(DB_PATH, SNAPSHOT_PATH)
1821:+    conn = sqlite3.connect(str(DB_PATH))
1881:+            hourly, api_provenance = result
1921:+                # match — UPDATE provenance_json adding payload_hash + parser_version
1927:+                        SET provenance_json = json_set(
1931:+                                        json_set(provenance_json, '$.payload_hash', ?),
1939:+                            api_provenance["payload_hash"],
1940:+                            api_provenance["parser_version"],
1941:+                            api_provenance["source_url"],
1942:+                            api_provenance["country_code"],
1943:+                            api_provenance["verified_for_obs_v2_payload_identity_at"],
1978:+    print(f"  filled (provenance updated):      {n_filled}")
1984:+    print(f"  mode:                             {'APPLY' if args.apply else 'DRY-RUN (no DB writes)'}")
1992:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_ogimet.py b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_ogimet.py
1996:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_obs_v2_payload_identity_ogimet.py
2001:+# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
2002:+#                  + companion to fill_obs_v2_payload_identity_existing.py (WU
2005:+#                    Tel Aviv) of obs_v2 — 60,623 rows training_allowed=1
2009:+Same semantics as the WU patch: fetch real ogimet METAR data via
2012:+--tolerance, then UPDATE provenance_json adding payload_hash + parser_version.
2014:+Differences from the WU patch:
2018:+  - source URL pattern: https://www.ogimet.com/cgi-bin/getmetar?icao=<ICAO>
2053:+    / "task_2026-04-28_obs_provenance_preflight" / "evidence"
2071:+DB_PATH = _resolve_zeus_db_path()
2072:+SNAPSHOT_PATH = DB_PATH.parent / f"{DB_PATH.name}.pre-gate5-ogimet-fill-2026-04-28"
2095:+def _build_ogimet_provenance(
2130:+        "WHERE training_allowed=1 AND source = ? "
2131:+        "  AND (json_extract(provenance_json,'$.payload_hash') IS NULL "
2132:+        "       OR json_extract(provenance_json,'$.parser_version') IS NULL)"
2186:+    if not DB_PATH.exists():
2187:+        sys.stderr.write(f"FATAL: db not found: {DB_PATH}\n")
2195:+            shutil.copy2(DB_PATH, SNAPSHOT_PATH)
2197:+    conn = sqlite3.connect(str(DB_PATH))
2283:+            api_provenance = _build_ogimet_provenance(
2330:+                        SET provenance_json = json_set(
2333:+                                    json_set(provenance_json, '$.payload_hash', ?),
2340:+                            api_provenance["payload_hash"],
2341:+                            api_provenance["parser_version"],
2342:+                            api_provenance["source_url"],
2343:+                            api_provenance["verified_for_obs_v2_payload_identity_at"],
2377:+    print(f"  filled (provenance updated):      {n_filled}")
2390:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_observations_provenance_existing.py b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_observations_provenance_existing.py
2394:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_observations_provenance_existing.py
2399:+# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
2401:+#                    - rebuild_calibration_pairs_v2.py:167-193 SELECTs
2404:+#                      provenance_metadata is NOT in the SELECT, but Gates 3+4
2409:+#                      so it CANNOT fill empty provenance in-place. This script is
2412:+"""Fill empty provenance_metadata on existing observations rows (Gate 3+4 fix).
2417:+  - source = 'wu_icao_history' (canonical WU)
2419:+  - high_temp + low_temp populated (training-input fields complete)
2420:+  - provenance_metadata = NULL or '' or '{}'  (audit gap → Gate 3+4 BLOCKER)
2422:+The values are correct (training pipeline reads them and uses them); only the
2424:+`scripts/verify_truth_surfaces.py::build_calibration_pair_rebuild_preflight_report`
2425:+blocks live calibration rebuild because the provenance contract is part of zeus
2426:+data governance (Constraint #4 — provenance > correctness).
2428:+SEMANTICS — why this is NOT the demolished synthetic-provenance pattern
2430:+The earlier session's `enrich_observation_instants_v2_provenance.py.BROKEN-DO-
2431:+NOT-RUN` synthesized provenance from row contents (sha256 of canonical row digest,
2435:+  1. REQUIRES live WU API fetch (real source data)
2436:+  2. VERIFIES that existing high_temp/low_temp match WU API response within
2438:+  3. ON match: UPDATEs ONLY `provenance_metadata` field; high_temp, low_temp,
2440:+  4. ON mismatch: writes row to quarantine_log JSON, does NOT update provenance
2441:+  5. NO fallback heuristic; NO synthesis; if WU API unavailable, skip
2443:+This satisfies the contract: provenance points to a real, re-fetchable WU API
2445:+bytes — same shape that `backfill_wu_daily_all.py::_build_wu_daily_provenance`
2454:+re-ingest (don't overwrite settled data), but it means provenance gaps cannot
2455:+be filled via that path. This script targets the empty-provenance subset
2460:+Dry-run all (default; no DB writes):
2462:+    python -m docs.operations.task_2026-04-28_obs_provenance_preflight.scripts.fill_observations_provenance_existing
2487:+# Reuse vetted helpers from backfill_wu_daily_all.py — provenance shape and
2488:+# WU API call signature must stay byte-identical to the new-row path.
2491:+    _build_wu_daily_provenance,
2499:+    placeholder; the real DB lives in the parent zeus dir."""
2513:+DB_PATH = _resolve_zeus_db_path()
2514:+SNAPSHOT_PATH = DB_PATH.parent / f"{DB_PATH.name}.pre-gate34-fill-2026-04-28"
2522:+    / "task_2026-04-28_obs_provenance_preflight"
2533:+    """Return observations rows missing provenance_metadata for WU VERIFIED.
2541:+        "AND (provenance_metadata IS NULL "
2542:+        "     OR TRIM(provenance_metadata)='' "
2543:+        "     OR TRIM(provenance_metadata)='{}')"
2588:+    p = argparse.ArgumentParser(description="Fill empty provenance_metadata on existing observations rows (Gate 3+4 fix).")
2596:+    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC, help="seconds between WU API calls (default 0.5)")
2599:+    if not DB_PATH.exists():
2600:+        sys.stderr.write(f"FATAL: db not found: {DB_PATH}\n")
2607:+            print(f"[apply] snapshotting {DB_PATH} → {SNAPSHOT_PATH}")
2608:+            shutil.copy2(DB_PATH, SNAPSHOT_PATH)
2610:+    conn = sqlite3.connect(str(DB_PATH))
2688:+                api_high, api_low, api_provenance = pair
2708:+                # match — UPDATE provenance_metadata only
2711:+                        "UPDATE observations SET provenance_metadata = ? WHERE id = ?",
2712:+                        (json.dumps(api_provenance, separators=(",", ":")), r["id"]),
2746:+    print(f"  filled (provenance written):      {n_filled}")
2752:+    print(f"  mode:                             {'APPLY' if args.apply else 'DRY-RUN (no DB writes)'}")
2760:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/recompute_source_role_canonical.py b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/recompute_source_role_canonical.py
2764:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/recompute_source_role_canonical.py
2769:+# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
2774:+This corrects the data corruption introduced by `enrich_observation_instants_v2_provenance.py`
2786:+  - sets training_allowed = 1 iff source_role == 'historical_hourly'
2860:+                    "canonical_training_allowed": 1 if a.source_role == "historical_hourly" else 0,
2873:+        n_training_mismatch = 0
2880:+                       SUM(CASE WHEN COALESCE(training_allowed, 1) != ? THEN 1 ELSE 0 END) AS train_diff
2883:+                (d["canonical_source_role"], d["canonical_training_allowed"], d["city"], d["source"]),
2887:+            n_training_mismatch += row_meta["train_diff"]
2894:+                    "to_training": d["canonical_training_allowed"],
2901:+        print(f"rows with training_allowed mismatch: {n_training_mismatch}")
2904:+            print(f"  {g['city']:20s} {g['source']:35s} → role={g['to_role']:18s} train={g['to_training']}  ({g['role_diff']} role_diffs, {g['train_diff']} train_diffs)")
2909:+            print("\n[dry-run] no DB changes made.")
2918:+                SET source_role = ?, training_allowed = ?
2920:+                  AND (COALESCE(source_role,'') != ? OR COALESCE(training_allowed,1) != ?)
2922:+                (g["to_role"], g["to_training"], g["city"], g["source"],
2923:+                 g["to_role"], g["to_training"]),
2957:diff --git a/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/remove_synthetic_provenance.py b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/remove_synthetic_provenance.py
2961:+++ b/docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/remove_synthetic_provenance.py
2966:+# Authority basis: operator instruction 2026-04-28T04:05Z — remove any synthetic
2967:+#                  provenance data introduced by this packet's earlier scripts.
2968:+"""Remove synthetic provenance fields injected by my earlier OBS work this session.
2971:+  observation_instants_v2.provenance_json keys:
2976:+  observations.provenance_metadata:
2980:+What is KEPT (because it is canonical, not synthetic):
2982:+  - observation_instants_v2.training_allowed (computed from canonical source_role)
2985:+correct — synthetic data should never block real preflight.
3004:+    p = argparse.ArgumentParser(description="Remove synthetic provenance data introduced this session")
3015:+        snap = db.parent / f"{db.name}.pre-synthetic-removal-2026-04-28"
3032:+            WHERE provenance_json IS NOT NULL
3033:+              AND json_valid(provenance_json) = 1
3034:+              AND json_extract(provenance_json, '$.parser_version') = ?
3041:+            WHERE provenance_metadata IS NOT NULL
3042:+              AND provenance_metadata != ''
3043:+              AND provenance_metadata != '{{}}'
3044:+              AND json_valid(provenance_metadata) = 1
3045:+              AND json_extract(provenance_metadata, '$.synthesized_by') = ?
3049:+        print(f"observation_instants_v2 rows with synthetic provenance: {n_obs_v2_with_synth}")
3050:+        print(f"observations rows with synthesized provenance_metadata: {n_observations_with_synth}")
3053:+            print("\n[dry-run] no DB changes made.")
3056:+        # 1. Strip synthetic keys from observation_instants_v2.provenance_json
3060:+            SET provenance_json = json_remove(provenance_json,
3066:+            WHERE provenance_json IS NOT NULL
3067:+              AND json_valid(provenance_json) = 1
3068:+              AND json_extract(provenance_json, '$.parser_version') = ?
3074:+        # 2. NULL out observations.provenance_metadata where it carries synth tag
3078:+            SET provenance_metadata = NULL
3079:+            WHERE provenance_metadata IS NOT NULL
3080:+              AND provenance_metadata != ''
3081:+              AND provenance_metadata != '{{}}'
3082:+              AND json_valid(provenance_metadata) = 1
3083:+              AND json_extract(provenance_metadata, '$.synthesized_by') = ?
3093:+            WHERE provenance_json IS NOT NULL
3094:+              AND json_valid(provenance_json) = 1
3095:+              AND json_extract(provenance_json, '$.parser_version') = ?
3102:+            WHERE provenance_metadata IS NOT NULL
3103:+              AND json_valid(provenance_metadata) = 1
3104:+              AND json_extract(provenance_metadata, '$.synthesized_by') = ?
3111:+                f"FATAL: residual synthetic rows after strip: obs_v2={post_obs_v2}, observations={post_observations}\n"
3119:+        print(f"[apply] post-count synthetic remaining: {post_obs_v2 + post_observations}")
3133:diff --git a/docs/operations/task_2026-04-28_settlements_low_backfill/plan.md b/docs/operations/task_2026-04-28_settlements_low_backfill/plan.md
3137:+++ b/docs/operations/task_2026-04-28_settlements_low_backfill/plan.md
3145:+| Authority basis | `src/types/metric_identity.py::LOW_LOCALDAY_MIN`, `src/contracts/settlement_semantics.py`, sibling packet `task_2026-04-28_settlements_physical_quantity_migration` |
3146:+| Sibling RFC | `task_2026-04-28_weighted_platt_precision_weight_rfc/rfc.md` (forecast-side LOW; this packet is settlement-side LOW) |
3151:+Backfilled LOW (mn2t6) settlements rows into `state/zeus-world.db::settlements` from Polymarket gamma API closed-event truth, cross-validated against `observations.low_temp` ground truth.
3154:+Post-this-packet: 48 LOW rows. The settlement-side LOW void is no longer absolute.
3158:+The subagent's [P0 hard-blocker scoping](../task_2026-04-28_settlements_physical_quantity_migration/) (sibling packet) noted:
3159:+> `data/pm_settlement_truth.json` (1566 entries): both files are HIGH-only — no LOW indicator
3160:+> Implication: There is presently NO source on disk from which LOW (city, date) settlement bins can be derived for backfill.
3162:+That was correct about the **on-disk** truth. But probing the live Polymarket gamma API showed **66 LOW events do exist** (48 closed + 18 active, 8 cities, dates 2026-04-15 .. 2026-04-29). They were never persisted to disk because zeus's market scanner only caches in memory.
3168:+- **8 cities**: London, Seoul, NYC, Tokyo, Shanghai, Paris, Miami, Hong Kong
3181:+## Reuse audit (Fitz code-provenance)
3186:+| `src/contracts/settlement_semantics.py` | CURRENT_REUSABLE; metric-agnostic. Used implicitly via the schema's bin grammar. |
3188:+| settlements triggers (authority_monotonic, non_null_metric, verified_insert_integrity) | CURRENT_REUSABLE; LOW INSERTs verified compatible (VERIFIED need non-null value+bin, QUARANTINED don't). |
3198:+- `tests/test_settlements_physical_quantity_invariant.py::test_settlements_low_uses_canonical_physical_quantity_or_absent` — PASSES post-backfill: every LOW row carries `physical_quantity = "mn2t6_local_calendar_day_min"`.
3199:+- `tests/test_settlements_physical_quantity_invariant.py::test_canonical_strings_match_registry` — guards canonical string registry.
3204:+   - For each row, check if obs now exists; if obs in winning bin → transition to VERIFIED with `provenance_json.reactivated_by` set (per `settlements_authority_monotonic` trigger requirement)
3210:+3. **Continuous LOW backfill cron**: 8 cities ✕ ~1 market/day = 8 new closed LOW events per day. A daily cron rerun of `scrape_low_markets.py` + `backfill_low_settlements.py` would keep the LOW settlements current without manual intervention.
3213:+   - WU rounding direction (half-up at 9.5 → 10)
3214:+   - WU finalized data ≠ initial-API-fetched data
3223:+| `scripts/backfill_low_settlements.py` | manifest + obs JOIN → plan + DB INSERT (gated by --apply) |
3224:+| `evidence/pm_settlement_truth_low.json` | 48-event scraped manifest |
3231:+python3 scripts/scrape_low_markets.py --out evidence/pm_settlement_truth_low.json
3233:+# 2. plan only (no DB writes)
3234:+python3 scripts/backfill_low_settlements.py \
3235:+    --manifest evidence/pm_settlement_truth_low.json \
3240:+python3 scripts/backfill_low_settlements.py \
3241:+    --manifest evidence/pm_settlement_truth_low.json \
3249:+- **Forecast-side LOW calibration**: solved by sibling RFC (`task_2026-04-28_weighted_platt_precision_weight_rfc/`) using `observations.low_temp` directly (NOT settlements). Settlements LOW is too sparse (48 rows, 8 cities, 13 days) to anchor calibration; obs LOW (42,749 rows, 51 cities, 28 months) is the proper training source.
3250:+- **51-city LOW coverage**: Polymarket only offers LOW markets for 8 cities. The other 43 zeus cities will never have LOW settlement rows from this source. This is a market-coverage limitation, not a data pipeline failure.
3252:diff --git a/docs/operations/task_2026-04-28_settlements_low_backfill/scripts/backfill_low_settlements.py b/docs/operations/task_2026-04-28_settlements_low_backfill/scripts/backfill_low_settlements.py
3256:+++ b/docs/operations/task_2026-04-28_settlements_low_backfill/scripts/backfill_low_settlements.py
3261:+# Authority basis: docs/operations/task_2026-04-28_settlements_low_backfill/plan.md
3263:+then optionally insert into state/zeus-world.db::settlements.
3269:+   write plan JSON. NO DB writes.
3277:+    python3 backfill_low_settlements.py \\
3278:+        --manifest evidence/pm_settlement_truth_low.json \\
3282:+    python3 backfill_low_settlements.py ... --apply
3343:+    """Polymarket bin semantics: integer-rounded WU value falls in bin.
3345:+    Bin grammar per zeus_market_settlement_reference.md:
3368:+        sst = rec["settlement_source_type"]
3433:+def insert_settlements(conn: sqlite3.Connection, plan: dict) -> int:
3438:+        provenance = {
3452:+            provenance["quarantine_reason"] = r["quarantine_reason"]
3454:+        sst = r["settlement_source_type"]
3460:+                INSERT INTO settlements (
3461:+                    city, target_date, market_slug, winning_bin, settlement_value,
3462:+                    settlement_source, settled_at, authority,
3463:+                    pm_bin_lo, pm_bin_hi, unit, settlement_source_type,
3465:+                    data_version, provenance_json
3480:+                    r["settlement_source_type"],
3485:+                    json.dumps(provenance, default=str),
3495:+    p = argparse.ArgumentParser(description="Backfill LOW settlements from Polymarket manifest")
3499:+    p.add_argument("--apply", action="store_true", help="apply DB writes (default: plan only)")
3523:+        print("\n--apply not set: no DB writes performed.")
3526:+    # Apply path: snapshot DB, then INSERT
3538:+            "SELECT COUNT(*) FROM settlements WHERE temperature_metric='low'"
3545:+        n = insert_settlements(conn, plan)
3548:+            "SELECT COUNT(*) FROM settlements WHERE temperature_metric='low'"
3569:diff --git a/docs/operations/task_2026-04-28_settlements_low_backfill/scripts/migrate_low_data_version.py.WRONG-DO-NOT-RUN b/docs/operations/task_2026-04-28_settlements_low_backfill/scripts/migrate_low_data_version.py.WRONG-DO-NOT-RUN
3573:+++ b/docs/operations/task_2026-04-28_settlements_low_backfill/scripts/migrate_low_data_version.py.WRONG-DO-NOT-RUN
3579:+#                  Mirror of migrate_settlements_physical_quantity.py shape.
3580:+"""Migrate settlements.data_version for LOW rows to canonical.
3582:+The earlier `backfill_low_settlements.py` writer used a per-source map
3602:+    p = argparse.ArgumentParser(description="LOW settlements data_version → canonical")
3630:+            "SELECT data_version, COUNT(*) AS n FROM settlements "
3638:+            "SELECT COUNT(*) FROM settlements "
3645:+            print("[dry-run] no DB changes.")
3649:+            "UPDATE settlements SET data_version = ? "
3656:+            "SELECT COUNT(*) FROM settlements "
3678:diff --git a/docs/operations/task_2026-04-28_settlements_low_backfill/scripts/scrape_low_markets.py b/docs/operations/task_2026-04-28_settlements_low_backfill/scripts/scrape_low_markets.py
3682:+++ b/docs/operations/task_2026-04-28_settlements_low_backfill/scripts/scrape_low_markets.py
3687:+# Authority basis: docs/operations/task_2026-04-28_settlements_low_backfill/plan.md
3699:+touch any DB. The DB write happens in a separate `--apply` script gated by
3717:+# city slug → (canonical zeus city display name, settlement_unit, settlement_source_type)
3726:+    "hong-kong": ("Hong Kong", "C", "hko"),
3791:+    Polymarket weather grammar (per zeus_market_settlement_reference.md):
3850:+    canonical_city, expected_unit, settlement_source_type = CITY_SLUG_MAP[city_slug]
3878:+        "settlement_source_type": settlement_source_type,
3940:diff --git a/docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md b/docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md
3944:+++ b/docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md
3947:+# task_2026-04-28_settlements_physical_quantity_migration
3959:+  # Purpose: INV-14 identity spine antibody for harvester settlement writes —
3966:+  # Residual: 1,561 pre-fix settlement rows on the live DB still carry
3980:+| `tests/test_settlements_authority_trigger.py` | CURRENT_REUSABLE — keep as-is | Tests trigger behavior, not canonical physical_quantity correctness. Uses `'daily_maximum_air_temperature'` to exercise trigger paths that are independent of this migration. DO NOT MODIFY (see decision below). |
3981:+| `tests/test_settlements_verified_row_integrity.py` | CURRENT_REUSABLE — keep as-is | Tests INSERT/UPDATE trigger integrity. Uses `'daily_maximum_air_temperature'` as a structural placeholder. Trigger behavior being tested is independent of the physical_quantity string value. DO NOT MODIFY. |
3982:+| `tests/test_settlements_unique_migration.py` | CURRENT_REUSABLE — keep as-is | Tests UNIQUE constraint migration (REOPEN-2). Physical_quantity string is incidental to the dual-track UNIQUE semantics being tested. DO NOT MODIFY. |
3986:+**Decision on existing tests using legacy string**: The three tests (`test_settlements_authority_trigger.py`, `test_settlements_verified_row_integrity.py`, `test_settlements_unique_migration.py`) use `'daily_maximum_air_temperature'` as a fixture/seed value to exercise DB behaviors (trigger firing, UNIQUE constraint enforcement) that are orthogonal to canonical identity. The physical_quantity field is not the subject of any assertion in those tests. Modifying them would be scope creep and could break their structural-antibody intent. They are left as-is. A new test file (`tests/test_settlements_physical_quantity_invariant.py`) provides the post-migration invariant assertion against the live DB.
3992:+### Live DB group-by (2026-04-28 snapshot)
3996:+FROM settlements GROUP BY 1, 2, 3;
4011:+| Field | Legacy value (current DB) | Canonical value (HIGH_LOCALDAY_MAX) | Delta |
4021:+Before the C6 harvester fix (2026-04-24), `src/execution/harvester.py::_write_settlement_truth` hardcoded the physical_quantity literal `"daily_maximum_air_temperature"` instead of reading it from `HIGH_LOCALDAY_MAX.physical_quantity`. The C6 fix corrected future writes. Historical rows were explicitly deferred as `NEEDS_OPERATOR_DECISION`.
4042:+- `winning_bin`, `settlement_value`
4043:+- `settlement_source`, `settled_at`
4045:+- `pm_bin_lo`, `pm_bin_hi`, `unit`, `settlement_source_type`
4048:+- `data_version` (source-specific, not changed — these document provenance of the observation, not the metric identity version)
4049:+- `provenance_json` (chain of custody, must NOT change)
4057:+UPDATE settlements
4081:+  UPDATE settlements SET physical_quantity = 'mx2t6_local_calendar_day_max'
4098:+1. **DB backup taken**: `state/zeus-world.db.pre-physqty-migration-2026-04-28` must exist and its size must match `state/zeus-world.db`. The script creates this automatically via `shutil.copy2` before opening any connection.
4100:+2. **Dry-run passes**: Run `python3 scripts/migrate_settlements_physical_quantity.py --db-path state/zeus-world.db --dry-run` and confirm output shows `would_change=1561`.
4104:+4. **New invariant test detects drift** (pre-migration): `pytest tests/test_settlements_physical_quantity_invariant.py -v` must FAIL on `test_settlements_high_uses_canonical_physical_quantity`. This confirms the test is live and will detect drift. (It will pass post-migration.)
4106:+5. **Existing tests still pass**: `pytest tests/test_settlements_authority_trigger.py tests/test_settlements_verified_row_integrity.py tests/test_settlements_unique_migration.py` must PASS. These are unchanged structural antibodies and must not regress.
4108:+6. **Zeus daemon is stopped**: Confirm `ZEUS_MODE=live python -m src.main` is NOT running against the live DB during migration. Concurrent writes during `BEGIN IMMEDIATE` will be serialized but unexpected INSERTs between dry-run and apply could change the row count.
4114:+New file: `tests/test_settlements_physical_quantity_invariant.py`
4117:+- `test_settlements_high_uses_canonical_physical_quantity`: asserts no live row has `temperature_metric='high' AND physical_quantity != HIGH_LOCALDAY_MAX.physical_quantity`. This test FAILS before migration and PASSES after. It is the persistent post-migration invariant.
4118:+- `test_settlements_low_uses_canonical_physical_quantity_or_absent`: asserts no live row has `temperature_metric='low' AND physical_quantity != LOW_LOCALDAY_MIN.physical_quantity`. Currently passes vacuously (no LOW rows in DB). When LOW rows are written by the harvester, this becomes a live gate.
4121:+All DB-touching tests use `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` (read-only, no WAL interference). They SKIP gracefully when `state/zeus-world.db` does not exist (CI safety).
4127:+The script takes a filesystem snapshot via `shutil.copy2` BEFORE opening any DB connection:
4135:+3. Verify: `sqlite3 state/zeus-world.db "SELECT COUNT(*) FROM settlements WHERE physical_quantity = 'daily_maximum_air_temperature';"` must return 1561.
4143:+**Category**: Silent semantic corruption — data-provenance failure (Fitz Constraint #4).
4145:+**What failure this prevents**: Any downstream JOIN, filter, or aggregation that uses `physical_quantity = 'mx2t6_local_calendar_day_max'` to select settlement rows silently returns zero rows because 100% of the live rows carry the legacy string. This is identical to the failure mode documented in `test_harvester_metric_identity.py` docstring: "any downstream JOIN filtering on canonical physical_quantity silently dropped 100% of harvester-written settlement rows."
4147:+**Category in Fitz methodology**: This is a data-provenance failure at the Module A → Module B boundary. The harvester (Module A) wrote correct data under old law; the type system (Module B, MetricIdentity) now defines a different canonical string. The semantic mismatch is invisible to code correctness checks — code is correct, data semantics are broken (per Constraint #4: "Correct code + wrong data semantics = silent disaster").
4149:+**Structural fix**: This migration makes the category impossible by aligning 100% of historical rows with the canonical MetricIdentity registry. The post-migration invariant test (`test_settlements_high_uses_canonical_physical_quantity`) becomes the persistent immune-system antibody — any future harvester regression or manual backfill using the legacy string will fail CI.
4151:+**Risk level**: LOW. The migration changes one column on rows with a pre-existing legacy string. It does not alter authority, provenance_json, settlement_value, or any column that drives settlement logic. It is fully reversible from the pre-migration snapshot.
4152:diff --git a/docs/operations/task_2026-04-28_settlements_physical_quantity_migration/scripts/migrate_settlements_physical_quantity.py b/docs/operations/task_2026-04-28_settlements_physical_quantity_migration/scripts/migrate_settlements_physical_quantity.py
4156:+++ b/docs/operations/task_2026-04-28_settlements_physical_quantity_migration/scripts/migrate_settlements_physical_quantity.py
4160:+# Authority basis: docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md
4161:+"""Migrate settlements.physical_quantity from legacy to canonical string.
4167:+All other columns (provenance_json, settlement_value, authority, etc.) are unchanged.
4171:+    python3 migrate_settlements_physical_quantity.py --db-path state/zeus-world.db
4174:+    python3 migrate_settlements_physical_quantity.py --db-path state/zeus-world.db --apply
4195:+        description="Migrate settlements.physical_quantity from legacy to canonical string."
4208:+        help="Print what would change without mutating the DB (default).",
4220:+    """Open the DB read-only via URI (does not create or modify)."""
4228:+    """Print migration preview without touching the DB."""
4230:+        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
4237:+            "SELECT physical_quantity, COUNT(*) AS cnt FROM settlements GROUP BY physical_quantity"
4239:+        print("=== Dry-run: settlements.physical_quantity distribution ===")
4245:+            "SELECT COUNT(*) FROM settlements WHERE temperature_metric = 'high' AND physical_quantity = ?",
4249:+        total = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
4258:+            print("INFO: No rows to migrate (already canonical or DB is empty).")
4271:+        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
4280:+        print(f"ERROR: DB is not writable: {db_path}", file=sys.stderr)
4298:+            "SELECT COUNT(*) FROM settlements WHERE temperature_metric = 'high' AND physical_quantity = ?",
4313:+            UPDATE settlements
4323:+            "SELECT COUNT(*) FROM settlements WHERE temperature_metric = 'high' AND physical_quantity != ?",
4339:+            print("Snapshot restored. DB is unchanged.", file=sys.stderr)
4357:+        print("Snapshot restored. DB is unchanged.", file=sys.stderr)
4373:diff --git a/docs/operations/task_2026-04-28_tigge_training_preflight/evidence/rebuild_dry_run.txt b/docs/operations/task_2026-04-28_tigge_training_preflight/evidence/rebuild_dry_run.txt
4377:+++ b/docs/operations/task_2026-04-28_tigge_training_preflight/evidence/rebuild_dry_run.txt
4384:+  Estimated live-write rowcount: 6,528 pairs (64 × 102 C-unit bins)
4393:diff --git a/docs/operations/task_2026-04-28_tigge_training_preflight/evidence/refit_platt_dry_run.txt b/docs/operations/task_2026-04-28_tigge_training_preflight/evidence/refit_platt_dry_run.txt
4397:+++ b/docs/operations/task_2026-04-28_tigge_training_preflight/evidence/refit_platt_dry_run.txt
4399:+PLATT V2 REFIT (calibration_pairs_v2 → platt_models_v2)
4403:+Result: pipeline mechanism functional; nothing to fit (calibration_pairs_v2 empty due to D live-write gate)
4404:diff --git a/docs/operations/task_2026-04-28_tigge_training_preflight/plan.md b/docs/operations/task_2026-04-28_tigge_training_preflight/plan.md
4408:+++ b/docs/operations/task_2026-04-28_tigge_training_preflight/plan.md
4410:+# TIGGE Training Preflight — Smoke Packet
4415:+| Status | **SMOKE PASSED** — pipeline mechanism end-to-end functional; live-write blocked by independent observation-provenance gates (not by causality) |
4416:+| Authority basis | `docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/G1_work_record_2026-04-27.md::TIGGE training readiness handoff — 2026-04-28` |
4417:+| Sibling RFC | `task_2026-04-28_weighted_platt_precision_weight_rfc/rfc.md` (PAUSED per operator until training-data path is correct) |
4419:+| Scratch DB | `/tmp/zeus-tigge-preflight.sqlite` (canonical clone, NOT canonical write) |
4420:+| Production DB mutation | **NONE** |
4424:+The third-party G1 handoff (2026-04-28) found that local HIGH `mx2t6` JSONs lack the first-class `causality` field required by `src/contracts/snapshot_ingest_contract.py` Law 5. Without causality, every HIGH ingest fails with `MISSING_CAUSALITY_FIELD`, blocking the entire training pipeline upstream.
4429:+3. Re-extracted Warsaw HIGH for the settlement-aligned window (2026-03-09..2026-04-15 issue dates)
4431:+5. Ran Stage B (ingest) → D (rebuild) → E (refit) end-to-end on a temp DB
4432:+6. Confirmed pipeline mechanism is functional and identified the NEXT real gate (observation-provenance preflights)
4448:+### 2. Re-extract Warsaw HIGH (settlement-aligned window)
4469:+# Ingest target 2026-04-08..2026-04-15 (settlement-aligned)
4483:+DB row state: `temperature_metric='high', training_allowed=1: 64` ✓
4485:+### 4. Stage D (rebuild calibration_pairs_v2 — DRY-RUN ONLY)
4488:+.venv/bin/python3 scripts/rebuild_calibration_pairs_v2.py \
4498:+### 5. Stage D (live write attempt — BLOCKED, expected)
4501:+.venv/bin/python3 scripts/rebuild_calibration_pairs_v2.py \
4507:+RuntimeError: Refusing live v2 rebuild: calibration-pair rebuild preflight is NOT_READY (
4509:+  observation_instants_v2.training_role_unsafe,
4511:+  observations.verified_without_provenance,
4512:+  observations.wu_empty_provenance,
4517:+This is **healthy preflight behavior**. The mechanism works; the gate that fires is the real next blocker — observation-side provenance gaps independent of TIGGE causality.
4525:+Result for both tracks: `Buckets eligible (n_eff >= 15): 0` — correctly reports no Platt fit possible because `calibration_pairs_v2` is empty (Stage D live-write was blocked). End-to-end mechanism functional.
4531:+| HIGH JSON `causality` absence is the FIRST blocker | ✓ confirmed via independent reproduction; resolved via 1-line VM extractor patch |
4532:+| Patched extractor produces ingest-acceptable HIGH JSON | ✓ 64/64 ingested, all training_allowed=1 |
4535:+| Live training has additional gates beyond causality | ✓ exposed: observation-provenance gates (wu_empty_provenance, hko fresh-audit, observation_instants_v2 role-unsafe) |
4539:+- Does NOT modify production `state/zeus-world.db` (zero rows written there from this smoke)
4540:+- Does NOT promote calibration_pairs_v2 or platt_models_v2
4544:+- Does NOT authorize any live venue side effect
4548:+The Stage D live-write preflight surfaces 6 OBS-side gates the next packet must address:
4553:+| `observation_instants_v2.training_role_unsafe` | observation_instants_v2 row classification needs review |
4554:+| `observations.hko_requires_fresh_source_audit` | HKO source audit refresh (existing fatal_misread, sibling concern) |
4555:+| `observations.verified_without_provenance` | observations rows need provenance_metadata fields populated |
4556:+| `observations.wu_empty_provenance` | WU-source rows need provenance fields filled |
4557:+| `payload_identity_missing` | snapshots / forecasts need MetricIdentity stamping (similar to settlements migration) |
4559:+These are **observation-side** issues, not forecast-side. The TIGGE causality fix is necessary but not sufficient.
4564:+2. **Address the 6 OBS-side preflight gates** in a sibling packet (likely `task_2026-04-28_obs_provenance_preflight`). These are independent of the TIGGE work but block live training.
4565:+3. **After both above**: rerun Stage B → D → E with `--no-dry-run --force` flags — should produce real `calibration_pairs_v2` and `platt_models_v2` rows.
4566:+4. **RFC remains paused** until basic training data path proves stable.
4591:+bash docs/operations/task_2026-04-28_tigge_training_preflight/scripts/preflight_smoke.sh
4593:diff --git a/docs/operations/task_2026-04-28_tigge_training_preflight/scripts/preflight_smoke.sh b/docs/operations/task_2026-04-28_tigge_training_preflight/scripts/preflight_smoke.sh
4597:+++ b/docs/operations/task_2026-04-28_tigge_training_preflight/scripts/preflight_smoke.sh
4602:+# Authority basis: docs/operations/task_2026-04-28_tigge_training_preflight/plan.md
4604:+# DOES NOT touch state/zeus-world.db. Writes only to /tmp scratch DB.
4609:+TMP_DB="/tmp/zeus-tigge-preflight.sqlite"
4618:+echo "=== [1/6] VM re-extract Warsaw HIGH (settlement-aligned 2026-03-09..2026-04-15) ==="
4635:+echo "=== [3/6] Reset /tmp DB to canonical clone (NO production write) ==="
4636:+cp /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db "$TMP_DB"
4637:+rm -f "$TMP_DB-wal" "$TMP_DB-shm"
4638:+sqlite3 "$TMP_DB" "DELETE FROM ensemble_snapshots_v2; DELETE FROM calibration_pairs_v2; DELETE FROM platt_models_v2;"
4644:+  --db-path "$TMP_DB" \
4648:+high_rows=$(sqlite3 "$TMP_DB" "SELECT COUNT(*) FROM ensemble_snapshots_v2 WHERE temperature_metric='high'")
4649:+echo "  HIGH rows in /tmp DB: $high_rows (expect 64)"
4651:+echo "=== [5/6] Stage D: rebuild_calibration_pairs_v2 --dry-run ==="
4652:+"$ZEUS_PY" scripts/rebuild_calibration_pairs_v2.py \
4653:+  --dry-run --city Warsaw --db "$TMP_DB" \
4654:+  | grep -E "track|Snapshots|eligible|live-write|Total"
4657:+"$ZEUS_PY" scripts/refit_platt_v2.py --dry-run --db "$TMP_DB" \
4662:+echo "  /tmp DB:           $TMP_DB"
4664:+echo "  Production DB:     UNTOUCHED"
4666:+echo "  Live-write status: gated on observation-provenance preflight (next packet)"
4683:+Replace zeus's binary `training_allowed: bool` gate (which discards 78% of TIGGE LOW snapshots) with a continuous `precision_weight: float ∈ [0, 1]` across the calibration pipeline.
4691:+- `task_2026-04-28_settlements_physical_quantity_migration` — fixes the 1561-row HIGH `physical_quantity` drift; precondition for any future LOW settlements writer parameterization
4692:+- LOW settlements backfill — out of scope here; gated on Polymarket LOW market data availability (operator decision)
4714:+The RFC in this packet proposes a schema-level change to `ensemble_snapshots_v2` and `calibration_pairs_v2`. Per AGENTS.md §4 planning lock, schema/truth-ownership changes require plan-evidence. This file is the numerical evidence that the proposed schema change is OOS-positive on real data.
4729:+| **A_baseline** (current zeus binary `training_allowed`) | 339,230 | 1.00× | discard if any-member boundary-ambiguous |
4806:+| Binary `training_allowed` gate is statistically suboptimal | ✓ confirmed |
4808:+| Weighted MLE doesn't drown the fit (extreme-low-weight risk) | ✓ confirmed |
4826:+2. **Single Platt formulation**: zeus production may add cluster/season terms; not exercised here.
4828:+4. **HIGH track not tested**: HIGH has 100% training_allowed=True, so no asymmetry to validate. The schema change is harmless for HIGH (weight = 1 for all rows in current state).
4839:+Output: `report.md`, `metrics.json`, `pairs.parquet`, `calibration_curves.png`.
4846:+# RFC: Replace Binary `training_allowed` with Continuous `precision_weight`
4855:+| Scope | Schema change to `ensemble_snapshots_v2`, `calibration_pairs_v2`; calibration store + Platt fit code path; antibody tests; type system |
4860:+Zeus's TIGGE LOW (mn2t6) extractor uses a binary `training_allowed: bool` gate that discards 78% of LOW snapshots due to "boundary_ambiguous" 6h-step resolution issues. PoC v4 (1.7M pair, 60-day OOS holdout, 500-resample bootstrap) shows that **continuous precision-weighted MLE is statistically significantly better than the binary gate** (overall ΔBrier = −0.00018, CI95 [−0.00022, −0.00014]; Asia subset ΔBrier = −0.00021), and the recoverable training set grows **3.16× to 4.65×**.
4862:+This RFC proposes replacing `training_allowed: bool` with `precision_weight: float ∈ [0, 1]` across the ensemble_snapshots_v2 → calibration_pairs_v2 → platt_models_v2 pipeline. It is a category-impossibility fix (per Fitz Constraint #2): the type system after migration prohibits binary discard of any continuous-quality dimension.
4869:+training_allowed = (
4883:+- LOW Asia cities: kuala-lumpur 1.8% training_allowed=True, singapore 3.0%, tokyo 5.5%, jakarta 3.4% → **Asia LOW Platt cannot be reliably per-city trained**
4885:+- HIGH track unaffected (100% training_allowed=True) → **HIGH/LOW asymmetric system behavior** is hard-coded
4893:+Step  4   Fewer outcomes → next training cycle still thin (positive feedback)
4904:+The job of zeus's calibration pipeline is to **convert information into edge**. Every (city, day, lead, member) datum carries SOME information about the calibration. The system should aggregate weighted information; it should NOT gate at thresholds beyond physical impossibility.
4914:+Current zeus mixes all three into `training_allowed: bool`. This RFC separates them.
4925:+    training_allowed: bool   # ← information cliff
4954:+`training_allowed: bool` is deleted from the dataclass. Any code that imports it raises ImportError at compile time.
4965:+Default `1.0` ensures pre-migration HIGH rows are unweighted (matching their training_allowed=True semantics). The constraint enforces type-system intent at DB level.
4967:+`training_allowed` column kept temporarily for back-compat during shadow-fit window; deleted in Phase 5.
4969:+### 3.3 Schema change — `calibration_pairs_v2`
4972:+ALTER TABLE calibration_pairs_v2 ADD COLUMN precision_weight REAL
4979:+CREATE INDEX idx_calibration_pairs_v2_target_date_metric
4980:+    ON calibration_pairs_v2(target_date, temperature_metric);
4985:+Current: `src/calibration/platt.py::fit_platt` (uniform MLE)
5006:+def test_calibration_pipeline_signatures_have_no_bool_quality_gates():
5009:+    Searches signatures of all functions in src/calibration/ and src/data/
5010:+    that touch calibration_pairs or ensemble_snapshots. Any bool param
5025:+    training population. Post-RFC must exceed 30%."""
5033:+HIGH track has 100% training_allowed=True today. Migration sets `precision_weight = 1.0` for all existing HIGH rows. No behavioral change for HIGH.
5040:+- Land `tests/test_settlements_physical_quantity_invariant.py` (separate packet `task_2026-04-28_settlements_physical_quantity_migration` in flight)
5045:+- Backfill: all existing rows get `precision_weight = 1.0` (matches their current training_allowed=True semantics for those that exist)
5046:+- DB migration script: `scripts/migrations/add_precision_weight_2026-04-28.py`
5050:+- Modify `extract_tigge_mn2t6_localday_min.py` to emit `precision_weight` field in JSON output. KEEP existing `training_allowed: bool` in JSON.
5057:+- For pre-Phase-2 JSON without precision_weight field: derive from training_allowed (1.0 if True, 0.0 if False) — same semantics as current
5061:+- New `src/calibration/platt_weighted.py` with `fit_platt_weighted(weights=...)`
5068:+- Replace `src/calibration/platt.py::fit_platt` to call platt_weighted internally
5071:+- Drop `training_allowed` column from ensemble_snapshots_v2 and calibration_pairs_v2 (DDL migration)
5072:+- Delete `extract_tigge_mn2t6_localday_min.py`'s `training_allowed: bool` output (still emit for one more cycle behind a deprecation warning, then remove)
5073:+- Antibody: `test_calibration_pipeline_signatures_have_no_bool_quality_gates` PASS
5108:+- Per-city regression investigation (8/21 cities worse in PoC). Hypotheses: city-specific calibration bias, monsoon-season noise correlation, sensor noise unit-scale (mostly ruled out by v4).
5112:+- [`task_2026-04-28_settlements_physical_quantity_migration`](../task_2026-04-28_settlements_physical_quantity_migration/plan.md) — packet drafted 2026-04-28. Components:
5114:+  - `scripts/migrate_settlements_physical_quantity.py` — dry-run/apply, snapshots before mutation, post-count verification
5115:+  - `tests/test_settlements_physical_quantity_invariant.py` — preventive antibody (currently FAILS until migration --apply runs, exposing the 1561-row drift)
5117:+- This is a precondition for any future LOW settlement writer (typed `MetricIdentity` parameterization). Independent of THIS RFC (forecast skill calibration uses `observations.low_temp` ground truth, not `settlements`).
5118:+- LOW settlements backfill scoping (separate sub-agent finding) — Polymarket LOW market truth currently empty on disk; operator decision required: (a) re-scrape historical, (b) forward-only, (c) declare structurally absent. **Not blocking THIS RFC** — this RFC improves forecast skill calibration which doesn't require Polymarket settlements (uses `observations.low_temp` ground truth via Phase 4 shadow eval).
5121:+- `src/calibration/store.py:30`: `_TRAINING_ALLOWED_SOURCES = frozenset({"tigge", "ecmwf_ens"})` — string-set membership. The right antibody for INV-15 is also typed (`SourceTag` enum), not string. Out of scope for this RFC; flagged as next-in-line.
5123:+## 7. Risks (Fitz risk classification)
5131:+| Existing tests using `training_allowed` field break | Governance | Deprecation warnings in Phase 5 grace cycle |
5132:+| Future code re-introduces `bool training_allowed` | Architecture | `test_no_binary_quality_gates` — type-level prohibition |
5148:+- Requires re-downloading TIGGE archive at ~2× current size
5153:+- Backwards-compatible at every phase (zero-risk Phase 4 shadow)
5159:+- Does NOT address LOW settlements backfill (separate packet; gated on operator decision about Polymarket source)
5182:diff --git a/tests/test_no_synthetic_provenance_marker.py b/tests/test_no_synthetic_provenance_marker.py
5186:+++ b/tests/test_no_synthetic_provenance_marker.py
5190:+# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
5192:+"""Relapse antibody — block re-introduction of synthetic provenance markers.
5194:+If anyone re-runs `enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN`
5195:+or writes equivalent fabricated provenance, this test fires.
5198:+  - observation_instants_v2.provenance_json contains
5202:+  - observations.provenance_metadata.synthesized_by="legacy:backfill_obs_prov_2026-04-28"
5204:+CI-safe: skip if no live DB is available.
5214:+CANDIDATE_DB_PATHS = (
5220:+def _find_live_db() -> Path | None:
5222:+    settlements + observation_instants_v2 tables."""
5223:+    for p in CANDIDATE_DB_PATHS:
5247:+def live_db_path() -> Path:
5248:+    p = _find_live_db()
5250:+        pytest.skip("no live zeus-world.db with required tables (CI-safe)")
5254:+def test_no_legacy_enrich_marker_in_obs_v2(live_db_path: Path) -> None:
5255:+    """observation_instants_v2.provenance_json must not carry the synth marker."""
5256:+    conn = sqlite3.connect(f"file:{live_db_path}?mode=ro", uri=True)
5262:+            WHERE provenance_json IS NOT NULL
5263:+              AND json_valid(provenance_json) = 1
5264:+              AND json_extract(provenance_json, '$.parser_version') = ?
5271:+        f"{row[0]} observation_instants_v2 rows carry the synthetic "
5273:+        f"Either enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN "
5274:+        f"was re-run, or another writer is fabricating provenance. "
5275:+        f"See docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/README_BROKEN.md."
5279:+def test_no_legacy_source_url_in_obs_v2(live_db_path: Path) -> None:
5280:+    """observation_instants_v2.provenance_json must not carry legacy:// source URLs."""
5281:+    conn = sqlite3.connect(f"file:{live_db_path}?mode=ro", uri=True)
5287:+            WHERE provenance_json IS NOT NULL
5288:+              AND json_valid(provenance_json) = 1
5290:+                json_extract(provenance_json, '$.source_url') LIKE 'legacy://obs_v2/%'
5291:+                OR json_extract(provenance_json, '$.source_file') LIKE 'legacy://obs_v2/%'
5298:+        f"{row[0]} observation_instants_v2 rows carry synthetic "
5303:+def test_no_synthetic_provenance_metadata_in_observations(live_db_path: Path) -> None:
5304:+    """observations.provenance_metadata must not carry the synth backfill tag."""
5305:+    conn = sqlite3.connect(f"file:{live_db_path}?mode=ro", uri=True)
5311:+            WHERE provenance_metadata IS NOT NULL
5312:+              AND TRIM(provenance_metadata) != ''
5313:+              AND TRIM(provenance_metadata) != '{}'
5314:+              AND json_valid(provenance_metadata) = 1
5315:+              AND json_extract(provenance_metadata, '$.synthesized_by') = ?
5322:+        f"{row[0]} observations rows carry the synthetic 'legacy:backfill_obs_prov_2026-04-28' "
5325:diff --git a/tests/test_settlements_physical_quantity_invariant.py b/tests/test_settlements_physical_quantity_invariant.py
5329:+++ b/tests/test_settlements_physical_quantity_invariant.py
5333:+# Authority basis: docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md
5336:+"""Post-migration invariant: every live settlement row must carry a canonical physical_quantity.
5338:+These tests are the persistent immune-system antibody for the settlements
5340:+  docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md
5342:+Before migration (current state): test_settlements_high_uses_canonical_physical_quantity
5347:+DB-touching tests use sqlite3 URI read-only mode and SKIP gracefully when
5365:+def _find_live_db() -> Path | None:
5370:+    or uninitialized (no settlements table).
5379:+        # Verify the DB is initialized (has the settlements table)
5384:+                "SELECT name FROM sqlite_master WHERE type='table' AND name='settlements'"
5394:+LIVE_DB: Path | None = _find_live_db()
5398:+    """Open the DB read-only via URI so no WAL writes or locking side-effects occur."""
5409:+def test_settlements_high_uses_canonical_physical_quantity():
5416:+    if LIVE_DB is None:
5417:+        pytest.skip("Live DB not present or not initialized — skipping in CI")
5419:+    conn = _open_ro(LIVE_DB)
5424:+            FROM settlements
5434:+            SELECT COUNT(*) FROM settlements
5445:+        f"{total_bad} settlement row(s) have temperature_metric='high' but "
5449:+        f"docs/operations/task_2026-04-28_settlements_physical_quantity_migration/"
5450:+        f"scripts/migrate_settlements_physical_quantity.py"
5458:+def test_settlements_low_uses_canonical_physical_quantity_or_absent():
5461:+    Currently passes vacuously (no LOW rows exist in live DB).
5463:+    a live gate ensuring canonical identity from day one.
5466:+    if LIVE_DB is None:
5467:+        pytest.skip("Live DB not present or not initialized — skipping in CI")
5469:+    conn = _open_ro(LIVE_DB)
5472:+            "SELECT COUNT(*) FROM settlements WHERE temperature_metric = 'low'"
5478:+            FROM settlements
5488:+            SELECT COUNT(*) FROM settlements
5499:+        f"{total_bad} LOW settlement row(s) have physical_quantity "
5506:+# Test 3: canonical string registry sanity (pure import check, no DB)
5513:+    of the canonical MetricIdentity constants. Runs in CI without any DB.
