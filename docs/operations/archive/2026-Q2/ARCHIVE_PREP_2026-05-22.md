# Archive Preparation — 2026-Q2 Batch
# Status: generated proposal / Authority: false / Not for topology PR

Generated: 2026-05-22
Branch: claude/agent-ad8fec6be48c408f2
Grep scope: src/**, scripts/**, architecture/**, tests/**, docs/authority/**,
            docs/runbooks/**, docs/reference/** (NOT docs/operations/**, docs/reports/**)
Archive destination prefix: docs/operations/archive/2026-Q2/

---

## 1. Packet Enumeration (54 total)

All 54 current `docs/operations/task_*` packets:

```
task_2026-04-26_ultimate_plan
task_2026-05-06_hook_redesign
task_2026-05-08_alignment_safe_implementation
task_2026-05-08_deep_alignment_audit
task_2026-05-09_copilot_agent_sync
task_2026-05-14_data_daemon_live_efficiency
task_2026-05-14_k1_followups
task_2026-05-15_autonomous_agent_runtime_audit
task_2026-05-15_data_pipeline_live_rootfix
task_2026-05-15_live_order_e2e_goal
task_2026-05-15_live_order_e2e_verification
task_2026-05-15_p1_topology_v_next_additive
task_2026-05-15_p10_module_consolidation_planning
task_2026-05-15_p2_companion_required_mechanism
task_2026-05-15_p3_topology_v_next_phase2_shadow
task_2026-05-15_p5_maintenance_worker_core
task_2026-05-15_p8_authority_drift_3_blocking
task_2026-05-15_p9_authority_inventory_v2
task_2026-05-15_runtime_improvement_engineering_package
task_2026-05-16_deep_alignment_audit
task_2026-05-16_doc_alignment_plan
task_2026-05-16_live_continuous_run_package
task_2026-05-16_post_pr126_audit
task_2026-05-17_docs_taxonomy_design
task_2026-05-17_f109_fix
task_2026-05-17_live_order_survival
task_2026-05-17_post_karachi_remediation
task_2026-05-17_reference_authority_docs_phase
task_2026-05-17_strategy_vnext_phase0
task_2026-05-18_live_reduce_only_reconcile_loop
task_2026-05-18_wave3_dispatches
task_2026-05-19_strategy_vnext_phase1
task_2026-05-20_live_substrate_bookhash_ownership
task_2026-05-20_pr221_review_fixes
task_2026-05-20_strategy_vnext_phase2
task_2026-05-21_evidence_tier_tribunal_authority
task_2026-05-21_live_authority_shadow_risk_followup
task_2026-05-21_live_contract_authority_pass
task_2026-05-21_live_entry_order_management
task_2026-05-21_live_family_selection_complete
task_2026-05-21_live_family_selection_economic_floor
task_2026-05-21_live_family_vector_fill_model
task_2026-05-21_live_release_proof_p0p3
task_2026-05-21_live_side_effect_risk_boundaries
task_2026-05-21_live_side_specific_entry_authority
task_2026-05-21_mainline_completion_authority
task_2026-05-21_money_path_semantic_ci
task_2026-05-21_strategy_vnext_phase3_shoulder
task_2026-05-21_strategy_vnext_phase4_fdr_candidates
task_2026-05-21_strategy_vnext_phase5_regime_correlation
task_2026-05-21_strategy_vnext_phase6_evidence_ladder
task_2026-05-21_strategy_vnext_phase7_settlement_type_gate
task_2026-05-22_crosscheck_valid_window
task_2026-05-22_live_math_frontier
```

---

## 2. Inbound Reference Audit Per Packet

### Classification Key
- **ARCHIVABLE_NOW**: 0 inbound refs across target dirs. Safe to git mv immediately.
- **ARCHIVABLE_AFTER_REPOINT**: All refs are soft (Authority-basis comments, prose, advisory YAML fields — not path-validated). List repoint edits required.
- **MACHINE_ADJACENT_REPOINT**: Ref is validated/loaded at runtime (path.exists(), glob, script execution). Requires careful repoint + validator name.
- **TRULY_STUCK**: Structurally required in current location. Keep with reason.
- **RUNTIME_GATING**: Evidence used by live readiness scripts or runtime gates.

---

### ARCHIVABLE_NOW (17 packets — 0 inbound refs)

| Packet | Contents |
|--------|----------|
| task_2026-05-08_alignment_safe_implementation | PLAN.md |
| task_2026-05-15_autonomous_agent_runtime_audit | AUTONOMOUS_AGENT_RUNTIME_AUDIT_PLAN.md |
| task_2026-05-15_p10_module_consolidation_planning | INVENTORY.md |
| task_2026-05-18_live_reduce_only_reconcile_loop | LIVE_REDUCE_ONLY_RECONCILE_PLAN.md |
| task_2026-05-20_live_substrate_bookhash_ownership | LIVE_SUBSTRATE_BOOKHASH_OWNERSHIP_PLAN.md |
| task_2026-05-20_pr221_review_fixes | PR221_REVIEW_FIX_PLAN.md |
| task_2026-05-21_evidence_tier_tribunal_authority | analysis_evidence_tier_tribunal_authority.md, task.md |
| task_2026-05-21_live_authority_shadow_risk_followup | analysis_live_authority_shadow_risk_followup.md, task.md |
| task_2026-05-21_live_contract_authority_pass | PLAN.md |
| task_2026-05-21_live_entry_order_management | LIVE_ENTRY_ORDER_MANAGEMENT_PLAN.md |
| task_2026-05-21_live_family_selection_complete | PLAN.md |
| task_2026-05-21_live_family_selection_economic_floor | PLAN.md |
| task_2026-05-21_live_family_vector_fill_model | analysis_1_family_selection_economic_floor.md, analysis_2_live_endpoint_asymmetry.md, task.md |
| task_2026-05-21_live_side_specific_entry_authority | PLAN.md |
| task_2026-05-21_money_path_semantic_ci | MONEY_PATH_SEMANTIC_CI_PLAN.md |
| task_2026-05-22_live_math_frontier | PLAN.md |

**Exception — classify as ARCHIVABLE_AFTER_REPOINT (1 soft ref):**

| Packet | Ref | File:line | Nature |
|--------|-----|-----------|--------|
| task_2026-05-22_crosscheck_valid_window | CROSSCHECK_VALID_WINDOW_PLAN.md cited in authority-basis comment | tests/test_runtime_guards.py:5 | Soft (# Authority basis: comment) |

Repoint: update `# Authority basis:` comment in `tests/test_runtime_guards.py` line 5 to use the archive path.

---

### ARCHIVABLE_AFTER_REPOINT (24 packets — soft refs only)

All refs below are in one of these soft categories:
- `# Authority basis:` / `# Authority:` headers in Python/YAML
- `# See` / docstring prose citations
- `operator_runbook:` YAML field (loaded but path not validated for existence)
- `active_task_scope:` YAML field (not path-validated)
- `law_dependencies:` / `notes:` YAML fields (audit-only, no .exists() check)
- `reference_reads:` in digest_profiles (passed as context payload, not opened)
- `coverage_scope: descendants` in docs_registry (wildcard covers all task_*)
- `plan_ref:` in db_table_ownership.yaml (not path-validated)
- `basis:` in invariants.yaml (not path-validated)
- `promotion_evidence_ref:` in strategy_profile_registry.yaml (not path-validated)
- `audit_ref:` dict literal in Python source (never opened)
- `PLAN-evidence:` comment in Python source

**Detailed per-packet ref list:**

#### task_2026-05-06_hook_redesign (10 refs)

| File:line | Nature |
|-----------|--------|
| architecture/topology_v_next_binding.yaml:382 | `path:` advisory key — admission_engine does `.get()` lookup, advisory only |
| tests/maintenance_worker/test_archival_check_0.py:136,139 | hardcoded string in test fixture (tmp_path simulation, not real path check) |
| tests/test_help_not_gate.py (# Authority basis comment) | Soft |
| tests/test_hook_dispatch_smoke.py (# Authority basis comment) | Soft |
| tests/test_hook_registry_schema.py (# Authority basis comment) | Soft |
| tests/test_hook_signal_health.py (# Authority basis comment) | Soft |
| tests/test_override_health.py (# Authority basis comment) | Soft |

Repoints needed (8 total):
1. `architecture/topology_v_next_binding.yaml:382` — update `path:` value to archive path
2. `tests/maintenance_worker/test_archival_check_0.py:136,139` — update 2 hardcoded string literals
3. `tests/test_help_not_gate.py` — update Authority basis comment
4. `tests/test_hook_dispatch_smoke.py` — update Authority basis comment
5. `tests/test_hook_registry_schema.py` — update Authority basis comment
6. `tests/test_hook_signal_health.py` — update Authority basis comment
7. `tests/test_override_health.py` — update Authority basis comment

#### task_2026-05-08_deep_alignment_audit (7 refs)

| File:line | Nature |
|-----------|--------|
| docs/runbooks/forecast-live-daemon.md | Prose citation |
| src/data/dual_run_lock.py (# Authority basis) | Soft |
| src/ingest/forecast_live_daemon.py (# Authority basis) | Soft |
| src/state/job_run_repo.py (# Authority basis) | Soft |
| tests/test_forecast_live_daemon.py (# Authority basis) | Soft |
| tests/test_job_run_schema.py (# Authority basis) | Soft |
| tests/test_opendata_writes_v2_table.py (# Authority basis comment) | Soft |

Repoints needed (7 files, all soft comment/prose updates).

#### task_2026-05-09_copilot_agent_sync (4 refs)

| File:line | Nature |
|-----------|--------|
| architecture/digest_profiles.py (2x `allowed_files:` glob, `file_patterns:` glob) | Soft (glob patterns in context payload, not path-validated) |
| architecture/topology.yaml (2x allowed-files glob) | Soft (not validated by topology_doctor .exists()) |

Repoints needed (4 entries across 2 files — glob path updates).

#### task_2026-05-14_k1_followups (19 refs)

| File:line | Nature |
|-----------|--------|
| architecture/db_table_ownership.yaml (plan_ref:, # Authority basis) | Soft (plan_ref not validated) |
| architecture/invariants.yaml (basis:) | Soft |
| scripts/check_table_registry_coherence.py (# Authority basis) | Soft |
| scripts/check_writer_signature_typing.py (# Authority basis) | Soft |
| scripts/drop_world_ghost_tables.py (# Authority basis) | Soft |
| scripts/healthcheck.py (# Authority basis) | Soft |
| scripts/migrate_world_observations_to_forecasts.py (# Authority basis) | Soft |
| src/state/db.py:251 (Authority: comment) | Soft |
| src/state/db.py:3153,3587 (PLAN-evidence: comment) | Soft |
| + ~10 more Authority-basis headers across tests/ and scripts/ | Soft |

Repoints needed (~19 entries across ~11 files, all soft comment/YAML value updates).

#### task_2026-05-14_data_daemon_live_efficiency (14 refs)

| File:line | Nature |
|-----------|--------|
| architecture/digest_profiles.py (reference_reads, allowed_files, file_patterns globs) | Soft (context payload) |
| architecture/topology.yaml (2x allowed-files entries, 1x plan-evidence command template) | **MACHINE_ADJACENT** for the plan-evidence entry — see MACHINE_ADJACENT section below |
| src/data/executable_forecast_reader.py (# Authority basis) | Soft |
| src/ingest_main.py (# Authority basis) | Soft |

Note: The `topology.yaml` command template at line ~127 is a shell command example stored as documentation of the required invocation; it is not automatically executed. The actual `--plan-evidence` argument is supplied by the user/CI when they run the command. The template path would fail validation only if someone runs the exact template command — it is a soft copy-paste risk, not a runtime gate. Classify as soft.

Repoints needed (~14 entries across ~4 files).

#### task_2026-05-15_live_order_e2e_goal (13 refs)

| File:line | Nature |
|-----------|--------|
| architecture/script_manifest.yaml (# Authority basis comment) | Soft |
| scripts/check_live_order_e2e.py (# Authority basis) | Soft |
| src/execution/command_bus.py (# Authority basis comment) | Soft |
| src/execution/command_recovery.py (# Authority basis comment) | Soft |
| src/state/venue_command_repo.py (# Authority basis comment) | Soft |
| tests/state/test_schema_current_invariant.py (# Authority basis comment) | Soft |
| tests/test_check_live_order_e2e.py (# Authority basis) | Soft |
| tests/test_command_bus_types.py (# Authority basis comment) | Soft |

Repoints needed (8 files, all soft).

#### task_2026-05-15_live_order_e2e_verification (16 refs)

| File:line | Nature |
|-----------|--------|
| architecture/digest_profiles.py (reference_reads) | Soft |
| architecture/script_manifest.yaml (# Authority basis comment) | Soft |
| architecture/topology.yaml (`--plan-evidence` in template command) | Soft (template command, not auto-executed) |
| scripts/live_health_probe.py (# Authority basis) | Soft |
| src/data/polymarket_client.py (# Authority basis comment) | Soft |
| src/main.py (# Authority basis) | Soft |
| src/venue/polymarket_v2_adapter.py (# Authority basis comment) | Soft |
| tests/test_ensemble_snapshots_bias_corrected_schema.py (# Authority basis comment) | Soft |

Repoints needed (8 files, all soft).

#### task_2026-05-15_p1_topology_v_next_additive (26 refs)

All refs are `# Authority basis:` headers in:
- architecture/topology_v_next_binding.yaml
- scripts/topology_v_next/*.py (8 files)
- tests/topology_v_next/regression/shadow/ (15+ test files)

Repoints needed (~26 Authority-basis comment updates across ~24 files).

#### task_2026-05-15_p2_companion_required_mechanism (12 refs)

All refs are `# Authority basis:` / inline comments in:
- scripts/topology_v_next/admission_engine.py
- scripts/topology_v_next/companion_skip_logger.py
- scripts/topology_v_next/composition_rules.py
- scripts/topology_v_next/profile_loader.py
- tests/topology_v_next/regression/ (4 test files)

Repoints needed (8 files, all soft).

#### task_2026-05-15_p3_topology_v_next_phase2_shadow (23 refs)

All refs are `# Authority basis:` headers in:
- scripts/topology_v_next/{cli_integration_shim,divergence_logger,divergence_summary}.py
- tests/topology_v_next/regression/shadow/ (13+ test files)

Repoints needed (~16 files, all soft).

#### task_2026-05-15_p5_maintenance_worker_core (34 refs)

All refs are `# Authority basis:` headers in:
- tests/maintenance_worker/test_cli/ (2 files)
- tests/maintenance_worker/test_core/ (7+ files)
- tests/maintenance_worker/test_integration/ (2 files)
- tests/maintenance_worker/test_rules/ (3 files)
- (+ more test files, ~25 total)

Repoints needed (~25 files, all soft Authority-basis comments).

#### task_2026-05-15_p8_authority_drift_3_blocking (3 refs)

| File:line | Nature |
|-----------|--------|
| architecture/reference_replacement.yaml (3x prose references in description fields) | Soft |

Repoints needed (3 prose updates in 1 file).

#### task_2026-05-15_p9_authority_inventory_v2 (2 refs)

| File:line | Nature |
|-----------|--------|
| scripts/authority_inventory_v2.py (# Authority basis) | Soft |
| tests/scripts/test_authority_inventory_v2.py (# Authority basis) | Soft |

Repoints needed (2 files, both soft).

#### task_2026-05-16_deep_alignment_audit (15 refs)

| File:line | Nature |
|-----------|--------|
| architecture/cascade_liveness_contract.yaml (2x `operator_runbook:`, 1x `# Authority basis`) | Soft (operator_runbook field is documentation, not path-validated) |
| scripts/backfill_harvester_settlements.py (# Authority basis) | Soft |
| scripts/migrations/202605_drop_world_market_events_v2_residue.py (# Authority basis) | Soft |
| src/execution/harvester_pnl_resolver.py (# Authority basis) | Soft |
| src/ingest/harvester_truth_writer.py (# Authority basis) | Soft |
| src/main.py (# Authority basis comment) | Soft |

Repoints needed (8 files, all soft).

#### task_2026-05-16_doc_alignment_plan (13 refs)

| File:line | Nature |
|-----------|--------|
| scripts/archive_may_batch_2026-05-16.py:16 (Authority basis comment in script header) | Soft (script is a one-shot archive utility, not a live gate) |
| scripts/archive_migration_2026-05-16.py (prose + # Authority basis) | Soft |
| tests/maintenance_worker/test_integration/ (2 test files, # Authority basis) | Soft |
| tests/maintenance_worker/test_rules/ (3 test files, # Authority basis) | Soft |

Repoints needed (7 files, all soft).

#### task_2026-05-16_live_continuous_run_package (7 refs)

All refs are `# Authority basis:` headers in:
- architecture/script_manifest.yaml
- scripts/healthcheck.py
- scripts/live_health_probe.py
- src/ingest/forecast_live_daemon.py
- tests/test_forecast_live_daemon.py
- tests/test_healthcheck.py
- tests/test_live_health_probe_forecast_owner.py

Repoints needed (7 files, all soft).

#### task_2026-05-16_post_pr126_audit (8 refs)

All refs are `# Authority basis:` / `# docs/operations/...` prose comments in:
- scripts/healthcheck.py
- tests/state/test_position_lots_reconciliation.py
- tests/test_f85_dual_handler_logging.py
- tests/test_f86_sigterm_handlers.py
- tests/test_f89_f101_heartbeat_schema_and_plist.py
- tests/test_healthcheck_heartbeat_freshness.py
- tests/test_migration_position_events_occurred_at_iso_check.py
- tests/test_sigterm_unification_5_daemons.py

Repoints needed (8 files, all soft).

#### task_2026-05-17_docs_taxonomy_design (2 refs)

| File:line | Nature |
|-----------|--------|
| scripts/doc_citation_lint.py (# Authority basis) | Soft |
| tests/test_doc_citation_lint.py (# Authority basis) | Soft |

Repoints needed (2 files, both soft).

#### task_2026-05-17_f109_fix (3 refs)

| File:line | Nature |
|-----------|--------|
| scripts/migrations/202605_position_current_idempotent_open_per_token.py (# Authority basis comment) | Soft |
| src/state/position_duplicate_consolidator.py (# Authority basis) | Soft |
| tests/state/test_position_open_idempotency.py (# Authority basis) | Soft |

Repoints needed (3 files, all soft).

#### task_2026-05-17_live_order_survival (3 refs)

| File:line | Nature |
|-----------|--------|
| src/contracts/executable_market_snapshot_v2.py (# Authority basis) | Soft |
| src/state/snapshot_repo.py (# Authority basis) | Soft |
| src/state/venue_command_repo.py (# Authority basis comment) | Soft |

Repoints needed (3 files, all soft).

#### task_2026-05-17_post_karachi_remediation (24 refs)

| File:line | Nature |
|-----------|--------|
| architecture/script_manifest.yaml (packet: field — 1 ref) | Soft (packet: is metadata, not path-validated) |
| scripts/migrations/__init__.py (# Authority) | Soft |
| scripts/migrations/__main__.py (# Authority) | Soft |
| scripts/migrations/202605_db_chunk_boundary_events.py (# Authority basis) | Soft |
| scripts/migrations/202605_position_current_idempotent_open_per_token.py (# Authority) | Soft |
| scripts/obs_v2_live_tick.py (# Authority basis) | Soft |
| src/state/canonical_write.py (# Authority basis) | Soft |
| src/state/chunk_boundary_events.py (# Authority basis) | Soft |
| + ~16 more Authority-basis headers across tests/ | Soft |

Repoints needed (~24 entries across ~17 files, all soft).

#### task_2026-05-17_reference_authority_docs_phase (2 refs)

| File:line | Nature |
|-----------|--------|
| architecture/script_manifest.yaml:302 — `- docs/operations/task_2026-05-17_reference_authority_docs_phase/VERIFIER_REPORT.md` | Soft (metadata listing, not path-validated by script_manifest loader) |
| scripts/verify_reality_contracts_2026-05-17.py:46 — `REPORT_PATH = REPO_ROOT / "docs" / "operations" / "task_2026-05-17_reference_authority_docs_phase" / "VERIFIER_REPORT.md"` | **Soft** (script WRITES to this path, not reads from it; REPORT_PATH.parent.mkdir() creates directory if absent — moving the packet doesn't break the script, it just writes the report to the new location unless REPORT_PATH is also updated) |

Note: `verify_reality_contracts_2026-05-17.py` writes a new report to `REPORT_PATH`; the write would succeed even after moving because `mkdir(parents=True, exist_ok=True)` recreates the parent. However, the script header cites the packet authority. Treat as soft with the understanding that the REPORT_PATH constant should be updated to point to the archive location.

Repoints needed (2 files — 1 script_manifest entry + 1 Python constant).

#### task_2026-05-18_wave3_dispatches (2 refs)

| File:line | Nature |
|-----------|--------|
| tests/test_healthcheck_heartbeat_freshness.py (# Authority basis comment) | Soft |
| tests/test_sigterm_unification_5_daemons.py (# Authority basis comment) | Soft |

Repoints needed (2 files, both soft).

---

### MACHINE_ADJACENT_REPOINT (3 packets — runtime-validated paths)

#### task_2026-04-26_ultimate_plan (267 refs — MACHINE_ADJACENT)

**Machine-adjacent ref:** `scripts/live_readiness_check.py:33-34`
```python
DEFAULT_EVIDENCE_ROOTS = (
    ROOT / "docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence",
    ROOT / "docs/operations/task_2026-04-26_ultimate_plan/r3/evidence",
)
```
`_glob_evidence()` (line 397-401) actively globs this directory for `staged_live_smoke_*.json` files at runtime. Moving the packet breaks the live-readiness gate.

**Secondary machine-adjacent ref:** `architecture/topology.yaml` (5+ entries)
```
--plan-evidence docs/operations/task_2026-04-26_ultimate_plan/r3/ULTIMATE_PLAN_R3.md
```
`topology_doctor_policy_checks.valid_plan_evidence()` calls `.exists()` on this path. Moving it causes the planning-lock gate to return False (blocks all PRs touching guarded surfaces).

**Soft refs (265 others):** digest_profiles.py glob patterns, src/*.py Authority comments, etc.

Required repoints before archiving:
1. `scripts/live_readiness_check.py:33-34` — update DEFAULT_EVIDENCE_ROOTS tuple (validator: `_glob_evidence`)
2. `architecture/topology.yaml` — update all 5+ `--plan-evidence` entries citing r3/ULTIMATE_PLAN_R3.md (validator: `topology_doctor_policy_checks.valid_plan_evidence`)
3. All soft refs (~260) — authority-basis headers throughout src/ and scripts/

**Classification: MACHINE_ADJACENT_REPOINT**
Validators: `scripts/live_readiness_check.py::_glob_evidence`, `scripts/topology_doctor_policy_checks.py::valid_plan_evidence`

---

#### task_2026-05-15_runtime_improvement_engineering_package (71 refs — MACHINE_ADJACENT)

**Machine-adjacent refs:** `architecture/artifact_authority_status.yaml` (loaded by `scripts/topology_v_next/profile_loader.py` and used as key-lookup in `admission_engine._check_authority_status`):
```yaml
- path: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md
- path: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/ZEUS_BINDING_LAYER.md
- path: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md
- path: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md
- path: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
```
These paths are used as dict keys for O(1) lookup against changed files in PRs. The lookup does NOT call `.exists()`, so moving the packet does NOT crash the admission engine — but it silently renders these authority-status entries dead (no changed file will ever match the stale key).

Assessment: silently-broken advisory (not a hard gate). Classify as MACHINE_ADJACENT_REPOINT with low urgency — the broken advisory will never fire a false negative, only stop firing true positives.

**Soft refs (66 others):** docs_registry.yaml path entries (advisory schema validation only), Authority-basis headers throughout tests/ and scripts/.

Required repoints before archiving:
1. `architecture/artifact_authority_status.yaml` — update 5 `path:` entries (validator: `scripts/topology_v_next/admission_engine.py::_check_authority_status`)
2. `architecture/docs_registry.yaml` — update ~8 specific path entries
3. Soft Authority-basis headers (~58 files) — comment updates only

**Classification: MACHINE_ADJACENT_REPOINT**
Validators: `scripts/topology_v_next/admission_engine.py::_check_authority_status` (advisory), `scripts/topology_v_next/profile_loader.py::_parse_artifact_authority_status`

---

#### task_2026-05-17_strategy_vnext_phase0 (68 refs — MACHINE_ADJACENT)

**Machine-adjacent ref:** `architecture/script_manifest.yaml:28-29`
```yaml
packet: docs/operations/task_2026-05-17_strategy_vnext_phase0
```
`topology_doctor` loads `script_manifest.yaml` and uses `packet:` field for script lineage and planning-lock context. Stale `packet:` path may cause mismatch in script ancestry checks (topology_doctor `_script_manifest_note_for_path`).

Assessment: `packet:` is used for advisory lineage matching, not `.exists()` validation. Silently stale if moved, not a hard gate.

**Machine-adjacent ref:** `src/state/schema/v2_schema.py:271` 
```python
--   docs/operations/task_2026-05-17_strategy_vnext_phase0/preflight/migration_dry_runs.json
```
This is an SQL comment in a schema migration. Not loaded at runtime.

**Soft refs (66 others):** digest_profiles.py reference_reads and glob patterns (context payload, not path-validated), Authority-basis headers.

Required repoints before archiving:
1. `architecture/script_manifest.yaml:28-29` — update 2 `packet:` entries
2. `architecture/digest_profiles.py` — update ~8 reference_reads / glob entries
3. Soft Authority-basis comments (~58 files)

**Classification: MACHINE_ADJACENT_REPOINT**
Validators: `scripts/topology_doctor.py::_script_manifest_note_for_path` (advisory)

---

### TRULY_STUCK (7 packets)

These packets are structurally active (live strategy phases, active monitoring, or currently open work items with ongoing authority). Archiving would be premature — the work is still referenced by active system contracts and recent code changes.

| Packet | Reason |
|--------|--------|
| task_2026-05-19_strategy_vnext_phase1 | Authority basis for active src/calibration/day0_horizon_calibration.py, src/signal/day0_high_nowcast_signal.py, and live DB migration scripts. Phase is recently shipped; code is production-live. |
| task_2026-05-20_strategy_vnext_phase2 | Authority basis for live T1 book hash transitions; test_topology.yaml law_dependencies reference to active test antibodies. |
| task_2026-05-21_mainline_completion_authority | `promotion_evidence_ref:` in architecture/strategy_profile_registry.yaml for all active strategy profiles (31 refs). This YAML is loaded by admission decisions. Not a hard path check, but removing the file stale-poisons the promotion-evidence chain for all active strategies. |
| task_2026-05-21_strategy_vnext_phase3_shoulder | Authority basis for production contracts (src/contracts/shoulder_strategy_vnext.py, src/contracts/weather_regime_tag.py) and live migration scripts. Code is in production. |
| task_2026-05-21_strategy_vnext_phase4_fdr_candidates | Authority basis for 3 active test files that guard FDR family candidates; tests are green-required. |
| task_2026-05-21_strategy_vnext_phase5_regime_correlation | Authority basis for production src/strategy/{correlation_shrinkage,regime_correlation_store}.py and active tests. |
| task_2026-05-21_strategy_vnext_phase6_evidence_ladder | Authority basis for 6 production modules (src/analysis/*, src/contracts/evidence_tier.py, src/state/*). All are live code. |

---

### RUNTIME_GATING (2 packets)

These packets participate in active runtime or live-money gates beyond simple authority citations.

| Packet | Gate | Validator |
|--------|------|-----------|
| task_2026-05-21_live_release_proof_p0p3 | `architecture/pre_existing_failure_registry.yaml:active_task_scope:` — scopes pre-existing failure exceptions to this packet's lifecycle. `architecture/script_manifest.yaml` and `tests/test_live_release_gate.py`, `tests/test_live_release_registry_runtime_assertions.py` gate live release decisions. | scripts/check_live_release_gate.py, tests/test_live_release_gate.py |
| task_2026-05-21_live_side_effect_risk_boundaries | Authority basis for 8 active test files guarding side-effect isolation. Tests are green-required in CI and directly probe production code behaviour (executor, command_bus, ATTACH seam). | tests/test_executor.py, tests/test_executor_command_split.py, tests/test_attach_narrow_except.py, tests/test_settlement_commands.py, etc. |

Note: RUNTIME_GATING is distinct from TRULY_STUCK in that these packets are cited in *gatekeeping* paths (not just authority provenance). If the packet is archived, the test citations become stale — tests still pass (comments don't break execution), but the contract traceability is lost. Recommend keeping until the guarded features are fully stabilised and the tests are promoted to permanent status.

---

### SPECIAL NOTES

**task_2026-05-15_p8_authority_drift_3_blocking** — `architecture/reference_replacement.yaml` prose. 3 references in description fields, all soft. ARCHIVABLE_AFTER_REPOINT, but note this packet documents a historical postmortem referenced by the replacement policy itself — consider promoting the postmortem content to a durable reference doc before archiving.

**task_2026-05-16_doc_alignment_plan** — Listed in `scripts/archive_may_batch_2026-05-16.py` as `Authority basis:` in the script header. This is a one-shot archiving utility. Safe to archive. The script itself should be archived (it was used for the May-16 batch) or retained as `scripts/` infrastructure.

---

## 3. Archive Batch Plan

Ordered for operator-approved archive PR. Group A (zero-ref) can be done with no repoints; Group B requires soft comment/YAML updates; Group C requires machine-adjacent repoints.

### Group A — ARCHIVABLE_NOW (17 packets, no repoints)

Execute as one `git mv` batch. No other file touches needed.

```
git mv docs/operations/task_2026-05-08_alignment_safe_implementation docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-15_autonomous_agent_runtime_audit docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-15_p10_module_consolidation_planning docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-18_live_reduce_only_reconcile_loop docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-20_live_substrate_bookhash_ownership docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-20_pr221_review_fixes docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-21_evidence_tier_tribunal_authority docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-21_live_authority_shadow_risk_followup docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-21_live_contract_authority_pass docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-21_live_entry_order_management docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-21_live_family_selection_complete docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-21_live_family_selection_economic_floor docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-21_live_family_vector_fill_model docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-21_live_side_specific_entry_authority docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-21_money_path_semantic_ci docs/operations/archive/2026-Q2/
git mv docs/operations/task_2026-05-22_crosscheck_valid_window docs/operations/archive/2026-Q2/  # +1 comment repoint
git mv docs/operations/task_2026-05-22_live_math_frontier docs/operations/archive/2026-Q2/
```

(task_2026-05-22_crosscheck_valid_window needs 1 comment repoint in tests/test_runtime_guards.py.)

### Group B — ARCHIVABLE_AFTER_REPOINT (24 packets + soft repoints)

Subgroups ordered by repoint count (ascending):

**B1 — 2-3 repoints (recommend first):**
- task_2026-05-15_p8_authority_drift_3_blocking (3 prose fields in 1 YAML)
- task_2026-05-15_p9_authority_inventory_v2 (2 files)
- task_2026-05-17_docs_taxonomy_design (2 files)
- task_2026-05-17_f109_fix (3 files)
- task_2026-05-17_live_order_survival (3 files)
- task_2026-05-18_wave3_dispatches (2 files)

**B2 — 4-8 repoints:**
- task_2026-05-08_alignment_safe_implementation (already Group A — zero refs)
- task_2026-05-06_hook_redesign (8 files)
- task_2026-05-08_deep_alignment_audit (7 files)
- task_2026-05-09_copilot_agent_sync (4 entries in 2 files)
- task_2026-05-16_doc_alignment_plan (7 files)
- task_2026-05-16_live_continuous_run_package (7 files)
- task_2026-05-16_post_pr126_audit (8 files)
- task_2026-05-17_reference_authority_docs_phase (2 files, incl. Python constant)
- task_2026-05-08_deep_alignment_audit (7 files)

**B3 — 10-25 repoints:**
- task_2026-05-14_data_daemon_live_efficiency (14 files)
- task_2026-05-14_k1_followups (~19 files)
- task_2026-05-15_live_order_e2e_goal (8 files)
- task_2026-05-15_live_order_e2e_verification (8 files)
- task_2026-05-16_deep_alignment_audit (8 files)
- task_2026-05-17_post_karachi_remediation (~24 files)
- task_2026-05-17_live_order_survival (3 files)

**B4 — 25+ repoints:**
- task_2026-05-15_p1_topology_v_next_additive (~26 files)
- task_2026-05-15_p2_companion_required_mechanism (~8 files)
- task_2026-05-15_p3_topology_v_next_phase2_shadow (~16 files)
- task_2026-05-15_p5_maintenance_worker_core (~25 files)

### Group C — MACHINE_ADJACENT_REPOINT (3 packets, hard preconditions)

Must be done AFTER verifying the validators pass post-repoint:

1. **task_2026-05-17_strategy_vnext_phase0** — update script_manifest.yaml `packet:` fields + digest_profiles.py globs. Run `topology_doctor --scripts` to verify.
2. **task_2026-05-15_runtime_improvement_engineering_package** — update artifact_authority_status.yaml path keys + docs_registry.yaml entries. Run `topology_doctor --docs` to verify.
3. **task_2026-04-26_ultimate_plan** — update `scripts/live_readiness_check.py` DEFAULT_EVIDENCE_ROOTS AND all topology.yaml `--plan-evidence` entries. Run `python scripts/live_readiness_check.py --no-run-commands` to verify. This is the highest-risk repoint.

### Keep / Do Not Archive

| Packet | Status | Reason |
|--------|--------|--------|
| task_2026-05-19_strategy_vnext_phase1 | TRULY_STUCK | Live production code authority |
| task_2026-05-20_strategy_vnext_phase2 | TRULY_STUCK | Live production code authority |
| task_2026-05-21_mainline_completion_authority | TRULY_STUCK | Active strategy_profile_registry.yaml promotion_evidence_ref |
| task_2026-05-21_strategy_vnext_phase3_shoulder | TRULY_STUCK | Production contracts authority |
| task_2026-05-21_strategy_vnext_phase4_fdr_candidates | TRULY_STUCK | Active test antibodies |
| task_2026-05-21_strategy_vnext_phase5_regime_correlation | TRULY_STUCK | Production code authority |
| task_2026-05-21_strategy_vnext_phase6_evidence_ladder | TRULY_STUCK | Production code authority (6 modules) |
| task_2026-05-21_live_release_proof_p0p3 | RUNTIME_GATING | Live release gate scope anchor |
| task_2026-05-21_live_side_effect_risk_boundaries | RUNTIME_GATING | CI antibody test authority |

---

## 4. Estimated Archive PR LOC Delta

| Component | Est. LOC |
|-----------|----------|
| Group A: 17 git mv + 1 comment repoint | ~5 LOC changed (1 comment update) + 17 moves |
| Group B1 (6 packets, ~20 repoints): comment/YAML value edits | ~25 LOC |
| Group B2 (8 packets, ~50 repoints) | ~60 LOC |
| Group B3 (7 packets, ~80 repoints) | ~95 LOC |
| Group B4 (4 packets, ~75 repoints) | ~90 LOC |
| Group C3 packets: live_readiness_check + topology.yaml + script_manifest + artifact_authority_status | ~35 LOC |
| **TOTAL repoint edits** | **~310 LOC** |
| **TOTAL git mv (moves)** | **44 moves** (Groups A+B+C = 17+24+3) |

A well-batched archive PR would show ~310 changed lines and 44 directory/file moves. This is above the 300-LOC threshold without counting moves. Recommend splitting:
- **PR-Archive-A**: Group A only (17 moves + 1 repoint) — ~5 LOC, use ZEUS_PR_ALLOW_TINY=1
- **PR-Archive-B**: Groups B1+B2 (14 packets, ~85 repoints) — ~85 LOC
- **PR-Archive-C**: Groups B3+B4 (11 packets, ~155 repoints) — ~185 LOC
- **PR-Archive-D**: Group C (3 high-risk packets, ~35 repoints) — ~35 LOC + careful validation

Each PR remains reviewable. Groups B and C can be batched differently depending on operator preference.

---

## 5. Summary Statistics

| Classification | Count |
|----------------|-------|
| ARCHIVABLE_NOW | 16 (+ crosscheck_valid_window at 1 soft ref = effectively 17 archivable) |
| ARCHIVABLE_AFTER_REPOINT | 24 |
| MACHINE_ADJACENT_REPOINT | 3 |
| TRULY_STUCK | 7 |
| RUNTIME_GATING | 2 |
| **TOTAL** | **54** |

Total repoints if all ARCHIVABLE_AFTER_REPOINT are executed: ~305 file edits across ~200 unique files.
Total machine-adjacent repoints: ~15 edits across 5 files (3 hard validators).
