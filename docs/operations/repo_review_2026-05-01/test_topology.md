# Zeus Test Topology Audit — 2026-05-01

**Branch**: ultrareview25-remediation-2026-05-01
**Auditor**: test-engineer (read-only)
**Authority chain**: AGENTS.md > architecture/invariants.yaml > architecture/test_topology.yaml > tests/

---

## Boot Evidence

Documents read before analysis:
- `AGENTS.md` — money path, lifecycle, settlement mechanics, risk levels
- `architecture/invariants.yaml` — 20 live INV-## definitions (INV-01 through INV-36, with gaps)
- `architecture/test_topology.yaml` — schema_version 1, created 2026-04-13; 154 trusted_tests registered; categories: core_law_antibody, useful_regression, transitional_advisory, diagnostic, tooling_runtime, midstream_guardian_panel
- `architecture/module_manifest.yaml` — not read (not in top-10 needed for this audit)
- `tests/conftest.py` — ZEUS_MODE defaulted to "live", UnitTestRiskAllocator fixture, FakePolymarketVenue fixture
- `tests/conftest_connection_pair.py` — fake_connection_pair() helper, in-memory world/trade sqlite schemas

---

## 1. Test Inventory

**Total test files: 271** (plus `tests/integration/test_p0_live_money_safety.py`)

| Surface | File Count | Notable Files |
|---------|-----------|--------------|
| Calibration / Platt | 16 | test_calibration_*, test_platt*, test_load_platt_v2_* |
| Architecture contracts / cross-module | 7 | test_architecture_contracts, test_cross_module_invariants, test_cross_module_relationships, test_structural_linter, test_reality_contracts, test_inv_prototype |
| Ingest / observation | 12 | test_ingest_*, test_obs_v2_*, test_k2_live_ingestion_relationships, test_ingestion_guard |
| Backtest / replay | 9 | test_backtest_*, test_replay_*, test_run_replay_cli |
| Settlement / PnL | 9 | test_settlement_*, test_settlements_*, test_pnl_flow_and_audit |
| Executor / execution | 7 | test_executor*, test_execution_intent_typed_slippage, test_execution_price, test_live_execution |
| Riskguard / Kelly | 7 | test_riskguard*, test_kelly*, test_risk_allocator |
| DB / state / truth | 3 | test_db, test_truth_surface_health, test_truth_layer |
| Lifecycle | 2 | test_lifecycle, test_lifecycle_terminal_predicate |
| Chain reconciliation | 2 | test_dt4_chain_three_state, test_exchange_reconcile |
| Edge observation | 2 | test_edge_observation, test_edge_observation_weekly |
| WS poll reaction | 2 | test_ws_poll_reaction, test_ws_poll_reaction_weekly |
| Harvester | 3 | test_harvester_dr33_live_enablement, test_harvester_metric_identity, test_harvester_split_independence |
| Other (slice, phase, k-series, diagnostics) | ~190 | — |

**Trust classification** (per architecture/test_topology.yaml `test_trust_policy`):
- Trusted (lifecycle headers present): **154 files**
- Audit required (no lifecycle headers): **117 files**

The 117 audit-required files are classified as LEGACY UNTIL AUDITED per CLAUDE.md provenance rule. Running them without audit is forbidden by test_topology.yaml §test_trust_policy. No enforcement mechanism currently blocks this at the pytest invocation layer.

---

## 2. INV-## Coverage Matrix

All INV-## definitions from `architecture/invariants.yaml`:

| INV-## | Statement (abbreviated) | Tests Cited in YAML | Grep Result | Coverage Classification |
|--------|------------------------|---------------------|-------------|------------------------|
| INV-01 | Exit is not local close | test_architecture_contracts::test_negative_constraints_include_no_local_close | CONFIRMED | REAL_TEST_BACKED |
| INV-02 | Settlement is not exit | test_architecture_contracts (2 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-03 | Canonical authority is append-first | None | Grep empty in tests/ for "INV-03"; only found in src/observability/AGENTS.md | **DOC_ONLY** |
| INV-04 | strategy_key is sole governance key | test_architecture_contracts::test_strategy_key_manifest_is_frozen | CONFIRMED (test collected) | REAL_TEST_BACKED |
| INV-05 | Risk must change behavior | test_architecture_contracts::test_risk_actions_exist_in_schema | CONFIRMED | REAL_TEST_BACKED |
| INV-06 | Point-in-time truth beats hindsight | test_cross_module_invariants (1 test) | CONFIRMED (also test_phase10e_closeout) | REAL_TEST_BACKED |
| INV-07 | Lifecycle grammar is finite | None | Grep found only test_topology_doctor.py:4306 asserting "INV-07 not in ids" | **DOC_ONLY** (semgrep-backed per yaml but not test-backed) |
| INV-08 | Canonical write path one transaction | None | test_architecture_contracts has `test_canonical_transaction_boundary_helper_is_atomic` (no INV-08 label in YAML `tests:` block) | **WEAK_ASSERTION** (test exists but uncited in YAML) |
| INV-09 | Missing data is first-class truth | 8 tests across test_db, test_k2_live_ingestion, test_runtime_guards, test_collateral_ledger | CONFIRMED | REAL_TEST_BACKED |
| INV-10 | LLM output is never authority | None | Grep empty in tests/ for "INV-10" or "llm.*authority" | **DOC_ONLY** (scripts + docs enforced only) |
| INV-13 | Numeric multipliers traceable to registry | test_provenance_enforcement | CONFIRMED | REAL_TEST_BACKED |
| INV-14 | temperature_metric/physical_quantity/observation_field/data_version required | 4 tests across test_canonical_position_current_schema_alignment, test_dual_track_law_stubs | CONFIRMED | REAL_TEST_BACKED |
| INV-15 | Rows lacking cycle identity banned from training | 8 tests in test_phase4_rebuild, test_harvester_high_calibration_v2_route | CONFIRMED | REAL_TEST_BACKED |
| INV-16 | Day0 low causality_status != OK blocks Platt lookup | 3 tests in test_phase6_causality_status | CONFIRMED | REAL_TEST_BACKED |
| INV-17 | DB writes commit before JSON exports | 6 tests in test_dt1_commit_ordering | CONFIRMED | REAL_TEST_BACKED |
| INV-18 | Chain reconciliation is three-valued | test_dual_track_law_stubs::test_chain_reconciliation_three_state_machine | CONFIRMED | REAL_TEST_BACKED |
| INV-19 | RED risk cancels+sweeps | test_dual_track_law_stubs::test_red_triggers_active_position_sweep | CONFIRMED | REAL_TEST_BACKED |
| INV-20 | Authority-loss keeps monitor/exit paths | test_dual_track_law_stubs::test_load_portfolio_degrades_gracefully_on_authority_loss | CONFIRMED | REAL_TEST_BACKED |
| INV-21 | Kelly needs executable-price distribution | test_dual_track_law_stubs::test_kelly_input_carries_distributional_info | CONFIRMED | REAL_TEST_BACKED |
| INV-22 | make_family_id() canonical | test_dual_track_law_stubs::test_fdr_family_key_is_canonical | CONFIRMED | REAL_TEST_BACKED |
| INV-23 | Degraded portfolio never exports VERIFIED | test_p0_hardening (2 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-24 | place_limit_order gateway-only | test_p0_hardening::test_place_limit_order_gateway_only | CONFIRMED | REAL_TEST_BACKED |
| INV-25 | V2 preflight failure blocks placement | test_p0_hardening (3 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-26 | runtime_posture read-only at runtime | test_p0_hardening (2 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-27 | Cycle summary surfaces execution-truth warnings | test_p0_hardening (4 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-28 | Every venue order pre-persisted in venue_commands | test_venue_command_repo (4 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-29 | VenueCommand/IdempotencyKey frozen | test_command_bus_types (10 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-30 | place_limit_order preceded by SUBMITTING row same process | test_executor_command_split (9 tests), test_executor_db_target (2 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-31 | Cycle start scans unresolved venue_commands | test_command_recovery (8 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-32 | Position authority advances only after ACKED/PARTIAL/FILLED | test_discovery_idempotency (3 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-33 | Posterior must use calibrated belief + named MarketPriorDistribution | test_no_bare_float_seams (3 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-34 | Kelly/FDR sizing uses executable cost-basis, not implied probability | test_no_bare_float_seams (2 tests) | CONFIRMED | REAL_TEST_BACKED |
| INV-35 | FinalExecutionIntent submit-ready without recompute inputs | test_no_bare_float_seams (1 test) | CONFIRMED | REAL_TEST_BACKED |
| INV-36 | Monitor/exit held-token quote separate from posterior | test_no_bare_float_seams (1 test) | CONFIRMED | REAL_TEST_BACKED |

**Summary**: 3 of 36 INV-## entries are coverage gaps:
- **INV-03** (DOC_ONLY): Canonical authority is append-first — no `tests:` block in YAML, no test string asserts the append-first invariant
- **INV-07** (DOC_ONLY): Lifecycle grammar is finite — semgrep rule cited but no pytest test; topology_doctor only checks the ID is absent from certain manifests
- **INV-10** (DOC_ONLY): LLM output is never authority — governance-layer enforcement via scripts/docs only; no executable pytest antibody
- **INV-08** (WEAK_ASSERTION): Transaction boundary test exists (`test_canonical_transaction_boundary_helper_is_atomic`) but YAML `tests:` block is missing

---

## 3. Relationship vs Function Test Ratio

**Classification method**: RELATIONSHIP tests cross module seams and verify properties that must survive module handoff (units, tz, data_version, mode, source-authority). FUNCTION tests verify single-function input/output.

Applying Fitz's definition ("when Module A's output flows into Module B, what property must survive?"):

**RELATIONSHIP tests** (strong cross-module invariant assertion):
- `test_cross_module_invariants.py` — 6+ relationship checks across state/engine/execution
- `test_cross_module_relationships.py` — settlement→chronicle, position_current→canonical
- `test_dt1_commit_ordering.py` — DB commit before JSON export (INV-17)
- `test_executor_command_split.py` — pre-persist→submit ordering (INV-30)
- `test_executor_db_target.py` — executor writes to trades_db not world_db (INV-30)
- `test_k2_live_ingestion_relationships.py` — scanner→DB append ordering (INV-09)
- `test_pe_reconstruction_relationships.py` — P-E reconstruction cross-module
- `test_pnl_flow_and_audit.py` — settlement→pnl→audit chain
- `test_data_rebuild_relationships.py` — rebuild pipeline cross-module
- `test_entry_exit_symmetry.py` — entry/exit contract symmetry across modules
- `test_edge_observation.py`, `test_attribution_drift.py`, `test_ws_poll_reaction.py`, `test_calibration_observation.py`, `test_learning_loop_observation.py` — cross-module learning cycle

**Estimated ratio**:
- RELATIONSHIP: ~45 files (17%)
- FUNCTION: ~226 files (83%)

**This inverts Fitz's preferred order.** The money path has these relationship-sparse zones:

| Zone | Relationship Tests | Function Tests | Risk |
|------|-------------------|----------------|------|
| harvester→DB | test_cross_module_invariants (INV-06), test_harvester_* | many | PARTIAL |
| calibration→evaluator→executor | test_evaluate_candidate_metric_integration | many | **SPARSE** |
| settlement→learning→calibration_retrain | test_calibration_observation, test_learning_loop | few | **SPARSE** |
| riskguard→cycle_runner→executor | test_riskguard + test_cycle_runner (absent) | many | **SPARSE** |
| WS ingest→command_repo→executor | test_user_channel_ingest | limited | PARTIAL |

**Key gap**: No relationship test verifies that the calibration→evaluator→position_sizing seam preserves temperature_metric and data_version across the full path. Individual function tests exist for each module but not the cross-seam property.

---

## 4. Silent Disabling Ledger

**Total `@pytest.mark.skip` decorators: 83**
**Total runtime `pytest.skip()` calls: ~50 across 20+ files**
**Total `@pytest.mark.xfail`: 2**
**`if False:` blocks: 1** (test_execution_price.py:393 — semantic provenance guard comment, benign)

### High-Risk Skip Clusters

| File | Count | Pattern | Risk | Sunset Status |
|------|-------|---------|------|---------------|
| `test_architecture_contracts.py` | 22 | "P9: legacy position_events vocabulary eliminated" | **MEDIUM** — each skip was a regression guard for a removed write path; 22 orphaned regression markers with no replacement equivalents for the canonical ledger path | NO sunset date |
| `test_db.py` | 19 | "P9: legacy position_events write path eliminated" | **HIGH** — test_db is a core_law_antibody for CANONICAL_DB_TRUTH law gate; 19 skipped tests means 19 DB write-path behaviors are unexercised | Sunset: "reconcile with canonical ledger/projection tests in test cleanup packet" (packet not yet created) |
| `test_strategy_tracker_regime.py` | 6 | "K1: strategy_tracker migrated to no-op" | LOW — tracker is genuinely dead; file should be archived | NO sunset date |
| `test_truth_layer.py` | 5 | "K1: save_tracker is no-op" | LOW — same as above | NO sunset date |
| `test_truth_surface_health.py` | 5 | "P9/Phase2: legacy position_events_legacy eliminated" | **HIGH** — truth_surface_health is a law gate test; 5 skips means partial blind spot | Sunset: "truth-surface cleanup packet" (not yet created) |
| `test_pnl_flow_and_audit.py` | 5 | 4x "BI-05" + 1x "Phase2: paper_mode removed" | **HIGH** — BI-05 skips are zone boundary tests for `portfolio` K2→K3 import path, which BI-05 flags as a live violation; skipped since at least 2026-04-23 with no sunset | BI-05 is labeled "Move from JSON to canonical ledger" — not implemented |
| `test_heartbeat_supervisor.py` | 4 | "M5 exchange reconciliation / T1 fake venue" | MEDIUM — deferred until M5/T1 phases which have no scheduled landing date | Sunset: "when those phases implement" |
| `test_live_safety_invariants.py` | 3 | "OBSOLETE_BY_ARCHITECTURE" (2) + "paper-mode removed" | MEDIUM — two explicitly labeled OBSOLETE but not deleted | Manual decision required |
| `test_cross_module_relationships.py` | 10 | runtime `pytest.skip()` "No SETTLEMENT events in chronicle", "Canonical position tables not present" | **HIGH** — data-dependent skips mean CI may silently pass these relationship tests without checking them | Sunset: "replace with fixture-backed tests" |
| `test_reality_contracts.py` | 10 | runtime `pytest.skip()` "RealityContractVerifier not yet delivered by p10-infra" | **HIGH** — INV-11 / reality contract framework never delivered; entire file is effectively dead | Sunset: "after reality verifier/loader delivery" |

### xfail Details

| File:Line | Reason | Risk |
|-----------|--------|------|
| `test_execution_price.py:235` | "Pre-existing integration test requiring full evaluator stub rewrite... Out of P10E scope — tracked for P11" — strict=False | **MEDIUM** — P11 scope undefined; this is a deferred regression |
| `test_discovery_idempotency.py:377` | "P2: cycle_runtime decision_id sourcing requires full execute_discovery_phase integration test with real DB harness" — strict=False | **MEDIUM** — P2 decision_id flow is live-critical; xfail with strict=False means silent pass even if fixed |

---

## 5. Fixture Validity Audit

### conftest.py fixtures (tests/conftest.py)
- `fake_venue` — `FakePolymarketVenue` from `tests/fakes/polymarket_v2.py`. Header: created 2026-04-27, last used 2026-04-29.
- `r3_default_risk_allocator_for_unit_tests` — autouse, provides `UnitTestRiskAllocator` that always returns `AllocationDecision(True, ...)`. **Risk**: every test that uses the autouse fixture bypasses the real risk allocator `can_allocate` logic. Tests validating the risk allocator guard itself must call `clear_global_allocator()` explicitly.

### conftest_connection_pair.py fixtures
- `fake_connection_pair()` — duck-typed, in-memory SQLite. Schema includes `settlements`, `observation_instants_v2`, `forecasts` tables. **Schema currency concern**: `observation_instants_v2` fixture schema uses `max_temp_c/min_temp_c` (Celsius). The dual-track law (INV-14) requires `temperature_metric` as a canonical column, but the fixture world schema has no `temperature_metric` column. Any test that creates positions via this fixture and then queries `temperature_metric` will get SQLite "no such column" errors unless the trade conn schema is initialized by `init_schema`.

### Day0TemporalContext fixture
- Used directly in `test_fdr.py` (line 1552) via `Day0TemporalContext(...)` constructor. The fixture is hand-built with specific `SolarDay` fields. No audit of whether these fields match the current `Day0TemporalContext` dataclass shape post-dual-track refactor. **Risk**: shape drift could cause silent wrong-value tests.

### Settlement event payloads
- `tests/contracts/spec_validation_manifest.py` provides `SPEC_ENTRY_VALIDATIONS`, `SPEC_EXIT_VALIDATIONS`, `SYMMETRY_PAIRS`, `EXIT_REQUIRED_STEPS`. These are static manifests — last-audited date unknown (no lifecycle header on the contracts/ directory). No `# Created:` header found in `tests/contracts/AGENTS.md` context.

### WS message shapes
- `tests/fakes/polymarket_v2.py` is the primary fake for WS/order interactions. Created 2026-04-27. Matches the R3 T1 contract surface. No evidence of stale schema.

---

## 6. Mode Isolation in Tests

`conftest.py:15`: `os.environ.setdefault("ZEUS_MODE", "live")`

This means **all tests default to ZEUS_MODE=live**. This is intentional (Zeus is now live-only post-Phase2). The following observations apply:

- `test_kelly_live_safety_cap.py:131` — explicitly asserts that `kelly.py` contains zero references to `mode/paper/live/ZEUS_MODE`. This is a positive antibody.
- `test_live_safe_strategies.py:133` — tests that "ZEUS_MODE=paper cannot bypass live strategy allowlisting" — mode isolation confirmed.
- `test_config.py:39` — uses `monkeypatch.setenv("ZEUS_MODE", "paper")` to test legacy path; paper mode should be dead.
- `test_truth_surface_health.py:1912,1956,2000` — 3 tests set `ZEUS_MODE=paper`, testing deprecated behavior that is skipped-or-dead. **Risk**: these tests run with a mode that no longer has a corresponding runtime path, creating ghost tests against paper-mode code.
- `test_architecture_contracts.py:937` — spawns subprocess with `ZEUS_MODE=paper`. Should be audited for whether the subprocess target still supports paper mode.
- `test_db.py:1113` — `monkeypatch.setenv("ZEUS_MODE", "paper")` — but this test may be in the 19-skip cluster; verify.

**Paper-mode isolation gap**: No test verifies that setting `ZEUS_MODE=paper` raises an error or is rejected at the cycle runner entry point. If paper mode is truly dead, a test asserting `ZEUS_MODE=paper → RuntimeError or startup rejection` would prevent accidental re-introduction.

---

## 7. Top-10 Flake Risk Inventory

| Rank | Test(s) | Root Cause | Risk Level |
|------|---------|-----------|------------|
| 1 | `test_cross_module_relationships.py` (10 runtime skips) | Data-dependent: skips silently when `chronicle`, `position_events`, `risk_state` tables are empty. In CI the DB is always empty, so these always skip. **Antibodies that never execute are not antibodies.** | CRITICAL |
| 2 | `test_reality_contracts.py` (10 runtime skips) | `RealityContractVerifier` never delivered by p10-infra. File has been silently inert since creation. All 10 INV-11 tests skip in every CI run. | CRITICAL |
| 3 | `test_runtime_guards.py:6706,6910` | `date.today()` used as `target_date` in test without mocking. DST boundary day or leap day could produce different DB query results. | HIGH |
| 4 | `test_truth_surface_health.py:1850` | `date.today()` used unguarded in a health check that queries a live DB path. | HIGH |
| 5 | `test_drift_detector_threshold.py:169` | `date.today()` used to construct test input without freeze. | HIGH |
| 6 | `test_ws_poll_reaction_weekly.py:402` | Skips if venv python not found at hardcoded path. Environment-sensitive skip. | MEDIUM |
| 7 | `test_calibration_quality.py:265` | Skips if "live DB with ensemble_snapshots data" absent. CI always skips. An integration test that is always skipped is a dead test. | MEDIUM |
| 8 | `test_tracker_integrity.py:14` | Skips if real DB file not found. Always skips in CI. | MEDIUM |
| 9 | `test_settlements_physical_quantity_invariant.py:87,137` | "Live DB not present or not initialized — skipping in CI". Two law-adjacent tests always skip in CI. | MEDIUM |
| 10 | `test_provenance_enforcement.py` (4 skipif) | Skips if PyYAML not installed. Requirements.txt should guarantee this; if not, 4 INV-13 tests are silently skipped. | LOW |

---

## 8. CI Gating Analysis

**CI file**: `.github/workflows/architecture_advisory_gates.yml`

| Job | Status | Tests Run | Gap |
|-----|--------|----------|-----|
| `advisory-gate-policy` | **BLOCKING** | scripts/check_advisory_gates.py | Policy only, no pytest |
| `architecture-manifests` | **BLOCKING** | scripts/check_kernel_manifests.py | No pytest |
| `module-boundaries` | **BLOCKING** | scripts/check_module_boundaries.py | No pytest |
| `packet-grammar` | **BLOCKING** | scripts/check_work_packets.py | No pytest |
| `kernel-invariants` | **BLOCKING** | pytest test_architecture_contracts + test_cross_module_invariants | **Only 2 of 271 test files** |
| `topology-doctor-modes` | **BLOCKING** | topology_doctor --docs --source --tests --scripts | Structural only |
| `semantic-linter` | **BLOCKING** | scripts/semantic_linter.py | No pytest |
| `assumptions-validation` | **BLOCKING** | scripts/validate_assumptions.py | No pytest |
| `law-gate-tests` | **BLOCKING** | 20 specific test files (curated law gate) | **7.4% of test suite** |
| `topology-full-strict` | **ADVISORY** (continue-on-error) | topology_doctor --strict | Never blocks PR |
| `semgrep-zeus` | **ADVISORY** (continue-on-error) | semgrep architecture/ast_rules/semgrep_zeus.yml | Never blocks PR |
| `replay-parity` | **ADVISORY** (continue-on-error) | scripts/replay_parity.py | Never blocks PR |

**Critical CI gap**: The full test suite (271 files) is **never run in CI**. Only 22 specific test files are in the blocking `law-gate-tests` job. The remaining 249 files run only if manually invoked. Regressions in `test_executor.py`, `test_riskguard.py`, `test_settlement_commands.py`, `test_pnl_flow_and_audit.py`, etc. are **not caught by any blocking CI gate.**

**Semgrep and replay-parity are advisory** — both cite "current findings still need packetized follow-up before promotion to blocking." This means known architecture violations (semgrep findings) do not block PRs.

---

## 9. Topology Doctor Coverage

- `tests/test_topology_doctor.py` — 6,114 lines, created 2026-04-13, last used 2026-04-28 (trusted).
- CI blocking job `topology-doctor-modes` runs topology_doctor in 4 modes: `--docs`, `--source`, `--tests`, `--scripts`.
- `test_digest_admission_policy.py` imports and directly tests `topology_doctor.run_navigation()` and `build_digest`.
- `test_inv_prototype.py:132` — heuristically checks that topology_doctor reads `semgrep_rule_ids` fields.
- **Finding**: `topology_doctor --strict` is advisory-only in CI. The strict mode catches "unregistered tracked files, root artifact classification, and state artifact classification" residuals that the 4 individual modes miss. These residuals have been known since at least Packet 7 with no resolution packet shipped.

---

## 10. Top-10 Antibody Gaps (Ranked by Money-Path Stage)

These are ordered by where on the money path the gap appears (earlier = closer to signal → execution → money at risk).

| Rank | Gap | Money-Path Stage | INV ## | Risk |
|------|-----|-----------------|--------|------|
| 1 | `test_cross_module_relationships.py` — 10 relationship tests always skip in CI due to data-dependency. No relationship test verifies settlement→chronicle→pnl flow end-to-end with fixtures. | settlement / learning | INV-02, INV-06 | **CRITICAL** |
| 2 | `test_reality_contracts.py` — 10 INV-11 tests always skip (RealityContractVerifier never delivered). No test verifies `verify_all_blocking()` is called before trade evaluation. | evaluation (pre-trade) | INV-11 | **CRITICAL** |
| 3 | INV-03 (DOC_ONLY) — No pytest verifies that derived JSON is never promoted back to canonical authority (append-first constraint). A race between `save_portfolio` JSON write and DB append could violate this silently. | canonical authority | INV-03 | **HIGH** |
| 4 | INV-07 (DOC_ONLY) — No pytest asserts that lifecycle phase strings come only from the `LifecyclePhase` enum. The semgrep rule `zeus-no-direct-phase-assignment` is advisory-only in CI. | lifecycle | INV-07 | **HIGH** |
| 5 | INV-10 (DOC_ONLY) — No pytest prevents LLM-generated code from entering the authority chain without packet + gate + evidence. Governance is procedural only. | governance | INV-10 | **HIGH** |
| 6 | `test_db.py` — 19 skipped write-path tests. Canonical ledger write path (append_canonical_event + project_position_current) has no active replacement regression guards. | canonical DB truth | INV-03, INV-08 | **HIGH** |
| 7 | Calibration→evaluator→Kelly seam has no relationship test. No test verifies that `temperature_metric` and `data_version` survive the P_raw→P_cal→P_posterior→Kelly path. | calibration / Kelly | INV-14, INV-21 | **HIGH** |
| 8 | INV-08 (WEAK_ASSERTION) — Transaction boundary test exists but YAML `tests:` citation is missing, so topology_doctor cannot assert enforcement. | canonical DB write | INV-08 | **MEDIUM** |
| 9 | 4 BI-05 skips in `test_pnl_flow_and_audit.py`. The K3→K2 `portfolio` module import boundary violation was never fixed. Tests skip because the violation exists. Skipping the tests masks a live zone boundary failure. | pnl / audit | BI-05 | **MEDIUM** |
| 10 | Paper-mode isolation: no test asserts that `ZEUS_MODE=paper` is rejected at cycle runner startup. Dead code path is still testable by setting env var. | mode isolation | INV-26 | **MEDIUM** |

---

## 11. Recommended New Tests

All recommendations are specific: name, location, assertion text.

### T1 — Relationship test for calibration→evaluator temperature_metric survival
**File**: `tests/test_calibration_evaluator_seam.py`
**Category**: RELATIONSHIP
**Assertion**:
```python
def test_evaluator_calibration_output_carries_temperature_metric():
    """When calibrated Platt output flows from CalibrationStore into the evaluator,
    the resulting p_cal value is tagged with temperature_metric='HIGH' or 'LOW',
    not bare float. Cross-module property: INV-14 survives the seam."""
    from src.calibration.manager import CalibrationManager
    from src.engine.evaluator import evaluate_candidate
    # ... build fixture with temperature_metric='HIGH' ...
    result = evaluate_candidate(market, snapshot, calib_mgr=mgr)
    assert result.p_cal_attribution.temperature_metric in ("HIGH", "LOW")
```
**Protects**: INV-14, calibration→evaluator seam
**Priority**: HIGH

### T2 — Antibody for INV-03 append-first constraint
**File**: `tests/test_append_first_authority.py`
**Category**: RELATIONSHIP
**Assertion**:
```python
def test_derived_json_export_cannot_update_canonical_db():
    """save_portfolio JSON write must never be callable before
    the canonical event+projection commit. This test calls
    save_portfolio on a mock DB that raises IntegrityError and
    asserts that the JSON file is not written (INV-03: append-first).
    """
    # commit_then_export pattern already covers INV-17; this covers INV-03's
    # append-first direction by verifying JSON cannot precede DB commit
    assert not json_written, "JSON must not be written before DB commit commits"
```
**Protects**: INV-03 (currently DOC_ONLY)
**Priority**: HIGH

### T3 — Antibody for INV-07 lifecycle grammar enforcement
**File**: `tests/test_lifecycle_grammar_enforcement.py`
**Category**: RELATIONSHIP
**Assertion**:
```python
def test_no_direct_phase_string_assignment_in_source():
    """No source file outside lifecycle_manager.py assigns a lifecycle phase
    string directly (INV-07). This is the pytest equivalent of the semgrep
    rule zeus-no-direct-phase-assignment, which is currently advisory-only."""
    import ast, pathlib
    source_files = list(pathlib.Path("src").rglob("*.py"))
    forbidden_pattern = re.compile(r'phase\s*=\s*["\'](?!LifecyclePhase)')
    for f in source_files:
        if "lifecycle_manager" in str(f):
            continue
        text = f.read_text()
        matches = forbidden_pattern.findall(text)
        assert not matches, f"{f}: bare phase string assignment found — use LifecyclePhase enum"
```
**Protects**: INV-07 (currently DOC_ONLY, semgrep advisory only)
**Priority**: HIGH

### T4 — Fixture-backed settlement→chronicle relationship test
**File**: `tests/test_settlement_to_chronicle_relationship.py`
**Category**: RELATIONSHIP
**Assertion**:
```python
def test_settlement_event_writes_to_chronicle_and_chronicle_is_queryable(tmp_path):
    """When a settlement event is committed, the chronicle table gains a SETTLEMENT
    row, and test_cross_module_relationships::test_settlement_in_chronicle_matches_canonical
    can execute without skipping. Replaces the data-dependent skip."""
    conn = build_test_db_with_settlement(tmp_path)
    rows = conn.execute("SELECT * FROM chronicle WHERE event_type='SETTLEMENT'").fetchall()
    assert len(rows) == 1
    assert rows[0]["city"] == "Miami"
```
**Protects**: INV-02, INV-06; fixes the #1 ranked gap
**Priority**: CRITICAL

### T5 — ZEUS_MODE=paper rejection test at cycle runner startup
**File**: `tests/test_mode_isolation.py`
**Category**: FUNCTION + MODE ISOLATION
**Assertion**:
```python
def test_cycle_runner_rejects_paper_mode(monkeypatch):
    """Post-Phase2 Zeus is live-only. Setting ZEUS_MODE=paper must raise
    RuntimeError or SystemExit at cycle runner import/startup, not silently
    run paper logic."""
    monkeypatch.setenv("ZEUS_MODE", "paper")
    with pytest.raises((RuntimeError, SystemExit, ValueError)):
        from src.engine import cycle_runner
        cycle_runner.assert_live_mode()
```
**Protects**: Mode isolation; prevents ghost paper-mode code re-introduction
**Priority**: MEDIUM

### T6 — INV-10 LLM-output exclusion from authority (procedural gate test)
**File**: `tests/test_llm_authority_exclusion.py`
**Category**: FUNCTION
**Assertion**:
```python
def test_no_source_file_has_ai_generated_marker_without_packet():
    """INV-10: every file with an AI/LLM-generated marker must have a
    co-located work packet reference. Bare AI-generated code is not authority."""
    import pathlib
    ai_markers = re.compile(r'(generated by|claude|chatgpt|llm-generated)', re.IGNORECASE)
    for f in pathlib.Path("src").rglob("*.py"):
        text = f.read_text()
        if ai_markers.search(text):
            assert "docs/work_packets/" in text or "packet" in text.lower(), \
                f"{f}: AI-generated marker without packet reference (INV-10)"
```
**Protects**: INV-10 (currently DOC_ONLY)
**Priority**: MEDIUM

---

## 12. Verification

This audit is read-only. No tests were run. All grep commands were executed against the current disk state of branch `ultrareview25-remediation-2026-05-01`.

Key file paths verified to exist:
- `/Users/leofitz/.openclaw/workspace-venus/zeus/architecture/invariants.yaml` — read in full
- `/Users/leofitz/.openclaw/workspace-venus/zeus/architecture/test_topology.yaml` — read in full
- `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/conftest.py` — read in full
- `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/conftest_connection_pair.py` — read in full
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.github/workflows/architecture_advisory_gates.yml` — read in full

---

*Report generated: 2026-05-01 by test-engineer (read-only audit)*
