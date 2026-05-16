# Inventory: Topology Doctor Modules & P1 SCAFFOLD Cross-Check

Created: 2026-05-15
Last Audited: 2026-05-15
Authority: docs/operations/task_2026-05-15_p10_module_consolidation_planning/INVENTORY.md

## §1. Existing Topology Doctor Modules (scripts/topology_doctor*.py)

| filename | LOC | Classes | Pub Funcs | Last Modified | Stated Purpose |
|----------|-----|---------|-----------|---------------|----------------|
| topology_doctor.py | 2941 | 1 | ~50 | 2026-05-14 | Main facade for compiled topology, navigation, and closeout checks. |
| topology_doctor_digest.py | 1905 | 0 | ~30 | 2026-05-10 | Build bounded topology digests with explicit admission reconciliation. |
| topology_doctor_code_review_graph.py | 757 | 0 | 12 | 2026-04-29 | Validate tracked code-review-graph online context without making it authority. |
| topology_doctor_docs_checks.py | 776 | 0 | 15 | 2026-05-07 | Docs-tree, operations-registry, runtime-plan, and docs-registry checks. |
| topology_doctor_policy_checks.py | 770 | 0 | 18 | 2026-05-07 | Validate governance manifests, planning locks, context budgets, policy gates. |
| topology_doctor_cli.py | 574 | 0 | 5 | 2026-05-10 | CLI facade for scripts.topology_doctor; parsing and rendering. |
| topology_doctor_closeout.py | 464 | 0 | 8 | 2026-05-08 | Combine changed-file topology lanes into one closeout result. |
| topology_doctor_registry_checks.py | 450 | 0 | 10 | 2026-05-07 | Registry, root, docs-strict, and archive-interface checks. |
| topology_doctor_script_checks.py | 340 | 0 | 12 | 2026-05-07 | Validate top-level script lifecycle, naming, and write-target metadata. |
| topology_doctor_reference_checks.py | 286 | 0 | 8 | 2026-04-15 | Reference replacement and core-claim checker family. |
| topology_doctor_receipt_checks.py | 263 | 0 | 10 | 2026-04-16 | Change-receipt checker family. |
| topology_doctor_ownership_checks.py | 222 | 0 | 6 | 2026-05-08 | Validate manifest fact-type ownership and module-manifest maturity. |
| topology_doctor_source_checks.py | 216 | 0 | 4 | 2026-04-24 | Source rationale and scoped-AGENTS checker. |
| topology_doctor_artifact_checks.py | 202 | 0 | 10 | 2026-04-15 | Artifact lifecycle and work-record checker family. |
| topology_doctor_test_checks.py | 190 | 0 | 5 | 2026-05-07 | Enforce test topology classification, law-gate, and skip-debt metadata. |
| topology_doctor_freshness_checks.py | 184 | 0 | 8 | 2026-04-16 | Enforce changed-file freshness headers and canonical naming map. |
| topology_doctor_map_maintenance.py | 161 | 0 | 5 | 2026-05-09 | Check changed-file companion registry requirements. |
| topology_doctor_data_rebuild_checks.py | 123 | 0 | 2 | 2026-04-16 | Data-rebuild topology checker family. |

**Total modules inventoried:** 18
**Total LOC:** 10,824

## §2. Pattern Grouping

Modules are grouped by dominant functional concern.

### 2.1 Validators / Checkers (The "Checker Family" pattern)
Modules that perform specific domain validation (12 members).
- `topology_doctor_artifact_checks.py`
- `topology_doctor_data_rebuild_checks.py`
- `topology_doctor_docs_checks.py`
- `topology_doctor_freshness_checks.py`
- `topology_doctor_ownership_checks.py`
- `topology_doctor_policy_checks.py`
- `topology_doctor_receipt_checks.py`
- `topology_doctor_reference_checks.py`
- `topology_doctor_registry_checks.py`
- `topology_doctor_script_checks.py`
- `topology_doctor_source_checks.py`
- `topology_doctor_test_checks.py`

### 2.2 Orchestrators & Facades
Modules that coordinate multiple sub-lanes (4 members).
- `topology_doctor.py` (Main Facade)
- `topology_doctor_cli.py` (CLI Interface)
- `topology_doctor_closeout.py` (Closeout Aggregator)
- `topology_doctor_digest.py` (Admission/Profile Resolution)

### 2.3 Integration / Bridge Modules
Modules bridging to external systems (2 members).
- `topology_doctor_code_review_graph.py` (Bridge to Code Review Graph)
- `topology_doctor_map_maintenance.py` (Bridge to Git status/staged changes)

### Groups with 3+ members:
- **Validators/Checkers**: 12 members. This is the primary candidate for consolidation. Most are < 300 LOC and could be merged into unified domain modules (e.g. `topology_validators.py`).

## §3. Sidecar-Suspicion Table

Modules that appear to parallel or duplicate concerns found in other modules.

| Suspicious Module | Parallel/Primary Module | Concern |
|-------------------|-------------------------|---------|
| topology_doctor_registry_checks.py | topology_doctor_docs_checks.py | Both check docs/AGENTS.md and registry integrity; split is purely by "strictness" rather than domain. |
| topology_doctor_freshness_checks.py | topology_doctor_script_checks.py | Both check script headers and naming; freshness is a subset of script lifecycle. |
| topology_doctor_receipt_checks.py | topology_doctor_policy_checks.py | Change receipts are a policy gate; split into separate module adds overhead for a simple check. |
| topology_doctor_map_maintenance.py | topology_doctor_closeout.py | Both deal with staged/changed file lists and map integrity. |

## §4. P1 SCAFFOLD Cross-Check

Cross-check of 10 new modules in `scripts/topology_v_next/` against existing `topology_doctor_*.py` sidecars.

| New Module (v_next) | Overlapping Existing Module(s) | Verdict | Rationale |
|---------------------|--------------------------------|---------|-----------|
| admission_engine.py | topology_doctor_digest.py | **EXTENDS** | Replaces the core admission logic with a typed-intent-first model. |
| profile_loader.py | topology_doctor.py | **NEW_CONCERN** | Specializes in YAML loading for the new binding layer format. |
| intent_resolver.py | topology_doctor_digest.py | **NEW_CONCERN** | First-class intent validation; existing code conflates this with profile resolution. |
| hard_safety_kernel.py | topology_doctor.py (invariants) | **EXTENDS** | Formalizes hard-stop logic previously buried in the main facade. |
| coverage_map.py | topology_doctor.py (scope rules) | **EXTENDS** | Structural implementation of the Universal Topology Design map. |
| composition_rules.py | topology_doctor_digest.py | **NEW_CONCERN** | Implements new cohort and multi-profile union rules. |
| companion_loop_break.py | topology_doctor_digest.py | **EXTENDS** | Generalizes the `_apply_companion_loop_break` from digest module. |
| dataclasses.py | topology_doctor.py | **NEW_CONCERN** | First structured data model for topology decisions. |
| divergence_logger.py | (none) | **NEW_CONCERN** | Purely for the P1 shadow window. |
| cli_integration_shim.py | topology_doctor_cli.py | **NEW_CONCERN** | Minimal wire-up to current CLI. |

**Verdict Summary:** Zero **DUPLICATE_RISK** found in P1 SCAFFOLD. The v_next modules are cleanly decoupled from the existing `topology_doctor_*.py` checker families. The overlap is limited to the core admission engine (`topology_doctor_digest.py`), which is the intended replacement target of the engineering package.

## §5. Last-Modified Distribution

| Age Range | Count | Modules |
|-----------|-------|---------|
| 0-30 days | 18 | ALL modules have been touched since mid-April 2026. |
| 31-90 days | 0 | |
| 91-180 days | 0 | |
| 180+ days | 0 | |

**Histogram:**
- Last 30 days: 18 (100%)

**Archival Candidates (Untouched 180+ days):**
- None. The topology doctor system is under active development.

**Consolidation Priority:**
- Modules touched in mid-April (e.g. `artifact_checks`, `data_rebuild_checks`, `reference_checks`, `receipt_checks`) are the oldest and appear to be the "early sidecars" created during the initial extraction from `topology_doctor.py`. These are the highest-value targets for consolidation into unified checker families.

---
**BATCH_DONE**
- inventory_path: docs/operations/task_2026-05-15_p10_module_consolidation_planning/INVENTORY.md
- modules_inventoried: 18
- total_topology_doctor_loc: 10824
- pattern_groups_with_3plus_members: ["Validators/Checkers", "Orchestrators & Facades"]
- sidecar_suspicion_count: 4
- p1_scaffold_duplicate_risk_count: 0
- p1_scaffold_duplicate_risk_modules: none
- modules_untouched_180plus_days: 0
- deviations_observed: none
