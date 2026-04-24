# T2 Midstream Adversarial Followups — Work Log

Date: 2026-04-24
Branch: `data-improve`
Task: Tier 2 adversarial-audit followups (M1 / C5 / C6 / H3 / M3)
C3 ruled false positive (see plan §Task).

## Pre-flight (grep-gate L20)

- `scripts/ingest_grib_to_snapshots.py:61-69` `_UNIT_MAP = {"C": "degC",
  "F": "degF"}` — module header L13-16 confirms this maps CITY manifest
  unit (C/F), not members_unit (Kelvin). C3 premise falsified; skipped.
- `scripts/ingest_grib_to_snapshots.py:164` `setdefault("causality",
  {"status": "OK"})` — CONFIRMED, defeats Law 5 (R-AJ) at
  `src/contracts/snapshot_ingest_contract.py:54-58`.
- `src/execution/harvester.py` settlements INSERT — CONFIRMED hardcoded
  `"high", "daily_maximum_air_temperature", "high_temp"` vs canonical
  `HIGH_LOCALDAY_MAX.physical_quantity = "mx2t6_local_calendar_day_max"`
  at `src/types/metric_identity.py:82`.
- `src/execution/harvester.py` calibration branches — CONFIRMED LOW branch
  uses `add_calibration_pair_v2` (with metric_identity), HIGH branch uses
  legacy `add_calibration_pair`. `refit_platt_v2` at
  `scripts/refit_platt_v2.py:29` reads v2 only → HIGH pairs never reach
  trainer.
- 4 cross-table JOINs without temperature_metric filter — all CONFIRMED
  at anchor lines.
- `CANONICAL_DATA_VERSIONS` — CONFIRMED at
  `src/contracts/ensemble_snapshot_provenance.py:68,141,145` + 4 test
  callsites.
- Both TIGGE extractors (mn2t6 + mx2t6) emit `causality` field — safe to
  remove the legacy setdefault without breaking current extraction flow.
- Law 5 contract test exists at
  `tests/test_phase5b_low_historical_lane.py:306` (pins contract); but
  ingest-layer bypass means a corresponding ingest-layer antibody is
  owed.

## Changed files (will be updated per slice)

_Will be populated as each slice lands._

## Slices

### S1 — M1 + C6 (ingest Law 5 + harvester canonical identity)

**Status**: landed in-tree, pending critic verdict before commit.

**Files**:
- `scripts/ingest_grib_to_snapshots.py` — removed the `setdefault("causality",
  {"status": "OK"})` bypass (L164). Updated preceding comment to explain why
  Law 5 (R-AJ) cannot be defaulted. Added Lifecycle / Purpose / Reuse header.
- `src/execution/harvester.py` — replaced the hardcoded identity triple
  `"high", "daily_maximum_air_temperature", "high_temp"` in the settlements
  INSERT OR REPLACE path (~line 768) with canonical
  `HIGH_LOCALDAY_MAX.temperature_metric`,
  `HIGH_LOCALDAY_MAX.physical_quantity`,
  `HIGH_LOCALDAY_MAX.observation_field`. Extended
  `from src.types.metric_identity import LOW_LOCALDAY_MIN` to import
  `HIGH_LOCALDAY_MAX` as well.
- `tests/test_ingest_grib_law5_antibody.py` — NEW 2-test antibody file
  asserting `ingest_json_file` surfaces `MISSING_CAUSALITY_FIELD` on
  causality-less payloads and accepts the Law 5 boundary when causality
  is explicit.
- `tests/test_harvester_metric_identity.py` — NEW 3-test antibody file
  asserting `_write_settlement_truth` writes canonical HIGH_LOCALDAY_MAX
  identity and structural import guard.
- `architecture/test_topology.yaml` — registered both new test files in
  `test_file_registry` (alphabetically inserted into settlements/harvester
  cluster).

**Verification**:
- `pytest tests/test_ingest_grib_law5_antibody.py tests/test_harvester_metric_identity.py`
  → 5 passed (0.1s).
- Regression: `pytest tests/test_phase5b_low_historical_lane.py
  tests/test_phase4_5_extractor.py tests/test_settlements_unique_migration.py
  tests/test_settlements_authority_trigger.py
  tests/test_settlements_verified_row_integrity.py` → 96 passed.
- `python -m py_compile scripts/ingest_grib_to_snapshots.py
  src/execution/harvester.py` → clean.
- `topology_doctor --planning-lock --plan-evidence ...` → ok (with
  architecture/test_topology.yaml registration included).
- `topology_doctor --freshness-metadata --changed-files ...` → ok.
- `topology_doctor --map-maintenance --map-maintenance-mode precommit
  --changed-files ...` → ok.

**Residual risks (surfaced to critic)**:
1. 1,561 existing settlement rows carry legacy `physical_quantity=
   "daily_maximum_air_temperature"`; forward-fix creates mixed data in
   the table. No downstream literal consumer exists today (con-nyx
   independently grep-verified), so the mix is invisible — but a future
   canonical-filter reader would silently drop all pre-fix rows.
   Migration requires `src/state/**` scope; tracked as
   `T2-S1-followup-M`.
2. Law 5 bypass removal means any pre-Phase-5B high-track JSON that
   was going to re-ingest (if anyone attempts it) will now fail with
   `MISSING_CAUSALITY_FIELD` instead of silently writing a
   "OK"-defaulted row. Intended semantic change; both TIGGE extractors
   already emit causality (`extract_tigge_mx2t6_localday_max.py:294,629`,
   `extract_tigge_mn2t6_localday_min.py:381,417`).
3. My `test_present_causality_field_survives_ingest_contract` only
   pins the contract-acceptance boundary, not the downstream writer.
   Intentional scope.

**Semantic-drift precision (con-nyx finding 6, L21 language check)**:
Of the three hardcoded literals replaced in harvester's settlements
INSERT, only `physical_quantity` changed VALUE ("daily_maximum_air_
temperature" → "mx2t6_local_calendar_day_max"); `temperature_metric`
("high") and `observation_field` ("high_temp") kept identical string
values. The C6 comment accurately calls this out; restated here for
clarity — S1's semantic-drift fix is one-of-three, the other two are
refactor-to-canonical-source with zero value drift.

**Pre-edit citation clarification (con-nyx finding 5)**:
Handoff's anchor `scripts/ingest_grib_to_snapshots.py:164` was the
PRE-edit line where `setdefault("causality", {"status": "OK"})`
lived. Post-edit, L164 is inside the comment block; the removed line
has no current line anchor. Readers verifying against HEAD should
look for the absence of `setdefault.*causality` around L170.

**Critic-found followups (con-nyx findings 3, 4 — CONDITIONAL on
follow-up; not S1 commit blockers)**:
- **T2-S1-followup-A**: extend Law 5 antibody to cover
  `causality=None` / `causality={}` / `causality="string"` /
  `causality={"status": None}`. Con-nyx traced the R-AJ code path and
  confirmed Law 5 is currently a presence-only gate, not well-formedness.
  May also want contract hardening in
  `src/contracts/snapshot_ingest_contract.py` L56-59 to require
  `isinstance(causality, dict)` + non-empty string status.
- **T2-S1-followup-B**: audit `src/state/db.py::init_schema` for
  settlements-column parity with live DB. Fresh init_schema
  does NOT create `pm_bin_lo/pm_bin_hi/unit/settlement_source_type`,
  forcing the test fixture to ALTER them in. Evidence of pre-existing
  drift surfaced by S1 (not introduced by S1). Needs planning-lock
  src/state/** scope.
- **T2-S1-followup-M**: backfill 1,561 legacy settlement rows to
  canonical physical_quantity OR document the mixed-rows state as
  permanent convention. Requires `src/state/**` scope.

### S2 — C5 (HIGH calibration-pair route to v2)

**Status**: landed in-tree, pending con-nyx verdict before commit.

**Files**:
- `src/execution/harvester.py` — collapsed the `harvest_settlement` LOW/HIGH
  branch split (previously L1075-1100) into a single `add_calibration_pair_v2`
  call. Both tracks now carry canonical `metric_identity` (LOW_LOCALDAY_MIN
  or HIGH_LOCALDAY_MAX) and `data_version` from the identity object.
  Removed legacy `add_calibration_pair` import at L24; removed
  `round_wmo_half_up_value` import at L27 (v2 internally applies
  `SettlementSemantics.for_city(city_obj).round_values`, which correctly
  handles HKO oracle_truncate — the legacy path's naive half-up rounding
  was HKO-unaware and was a latent bug surfaced by this fix). Comment
  block at L1076-1086 narrates the split-brain closure.
- `tests/test_harvester_high_calibration_v2_route.py` — NEW 5-test
  antibody pinning HIGH pairs land in `calibration_pairs_v2` with
  HIGH_LOCALDAY_MAX identity + training_allowed=1; NOT in legacy
  `calibration_pairs`; LOW branch still routes to v2 (symmetry
  regression); structural guard that harvester's `calibration.store`
  import line no longer references the legacy writer; INV-15
  training_allowed=True resolution via data_version "tigge_*" prefix.
- `tests/test_phase10c_dt_seam_followup.py` — `test_r_cs_2_high_
  settlement_stays_legacy` renamed to `test_r_cs_2_high_settlement_
  routes_to_v2_after_c5` with inverted assertions. Pre-C5 this test
  LOCKED IN the split-brain as expected behavior; post-C5 the expected
  behavior is the opposite. Per L21 (Activate vs Extend), the rename +
  reworded assertions replace the stale lock-in with the new contract.
- `tests/test_lifecycle.py` — `test_harvest_creates_pairs` now applies
  `v2_schema` to the fixture and reads from `calibration_pairs_v2`
  (previously read legacy `calibration_pairs`). Added Lifecycle /
  Purpose / Reuse header.
- `tests/test_calibration_manager.py` — `_get_test_conn` helper now
  applies `v2_schema`; `test_bias_corrected_persisted_through_harvest`
  (2 queries) + `test_bias_corrected_fallback_reads_settings` (1 query)
  now read `calibration_pairs_v2`. Added Lifecycle / Purpose / Reuse
  header.
- `architecture/test_topology.yaml` — registered
  `test_harvester_high_calibration_v2_route.py` (alphabetic insert
  above the harvester_metric_identity + ingest_grib_law5 entries from
  S1).

**Verification**:
- `pytest` on S1 antibodies + S2 antibodies + all 3 updated regression
  test files (TestHarvester, TestStoreRoundTrip, TestRCSHarvesterLowRouting)
  → 19 passed, 0 failed.
- Broader regression
  `pytest test_lifecycle test_calibration_manager test_phase10c_dt_seam_followup
   test_harvester_metric_identity test_harvester_high_calibration_v2_route
   test_ingest_grib_law5_antibody test_settlements_unique_migration
   test_settlements_authority_trigger` → 85 passed.
- `python -m py_compile src/execution/harvester.py` → clean.
- `topology_doctor --planning-lock --plan-evidence / --freshness-metadata /
  --map-maintenance --map-maintenance-mode precommit` → all ok.

**Residual risks (surfaced to critic for S2 dispatch)**:
1. Semantic shift in HIGH settlement rounding: legacy path used
   `round_wmo_half_up_value` (naive WMO half-up); v2 uses
   `SettlementSemantics.for_city(city_obj).round_values` which applies
   HKO oracle_truncate for HKO cities. HKO settlement values post-C5
   may differ by ±0.5°F from pre-C5 for boundary cases. Latent bug
   closure (HKO was previously getting WMO rounding), but behavior
   change worth acknowledging.
2. `test_r_cs_2_high_settlement_routes_to_v2_after_c5` used to assert
   `len(rows_legacy) > 0` AND `len(rows_v2) == 0`; post-fix asserts
   the opposite. The rename documents the contract flip; reviewers
   verifying the test history should grep both names.
3. Pre-existing flake bar: `test_lifecycle.py::TestHarvester` and
   `test_calibration_manager.py::TestStoreRoundTrip` now depend on
   `apply_v2_schema` being importable + idempotent. If a future
   v2_schema refactor makes this import side-effect-free in a way that
   doesn't create the table, these tests fail first — which is the
   desired signal.
4. `test_v2_pair_training_allowed_respects_inv15` asserts INV-15's
   passthrough when `source=""` is implicit. If a future contributor
   adds `source="..."` to the v2 call and the value isn't in the
   whitelist (`{"tigge", "ecmwf_ens"}`), training_allowed silently
   downgrades to 0. The test would catch the training_allowed
   downgrade but not narrate "why"; reviewers should watch for source
   additions.

### S3 — H3 (cross-table JOIN metric filter + antibody lint)

_Pending execution._

### S4 — M3 (CANONICAL_DATA_VERSIONS rename + parallel allowlists)

_Pending execution._

## Verification (will be filled per slice)

_To be populated._

## Next

- Run topology_doctor --planning-lock with this plan as --plan-evidence
  across the union of S1-S4 changed files, then execute slices in order.
