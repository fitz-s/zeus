File: architecture/AGENTS.md
Disposition: NEW
Authority basis: docs/authority/zeus_current_architecture.md; docs/authority/zeus_current_delivery.md; docs/authority/zeus_change_control_constitution.md; architecture/kernel_manifest.yaml; architecture/invariants.yaml; architecture/zones.yaml; architecture/negative_constraints.yaml; architecture/maturity_model.yaml.
Supersedes / harmonizes: informal architecture claims in historical docs.
Why this file exists now: this is the K0/K1 law zone and needs narrower instructions than the repo root.
Current-phase or long-lived: Long-lived.

# architecture AGENTS

This directory contains machine-checkable and constitutional authority surfaces.

Module book: `docs/reference/modules/topology_system.md`

## Treat this zone as high sensitivity
Changes here are architecture or governance changes, never “just docs.”

## Required before edit
- approved packet
- explicit invariant references
- list of touched authority surfaces
- statement of what existing surface this change harmonizes or supersedes

## Do
- keep manifests, constitution, and spec mutually consistent
- prefer delta over rewrite
- preserve descriptive vs normative distinction
- record migration drift instead of hiding it

## Do not
- create parallel authority files
- copy historical rationale into active law without saying so
- claim runtime convergence unless current code actually matches
- widen semantics by convenience

## File registry

| File | Purpose |
|------|---------|
| `kernel_manifest.yaml` | Kernel file ownership and protection rules |
| `invariants.yaml` | 10 invariant definitions (INV-01 through INV-10) |
| `zones.yaml` | Zone definitions with import rules (K0-K4) |
| `negative_constraints.yaml` | 10 negative constraint definitions |
| `maturity_model.yaml` | Maturity model definitions |
| `topology.yaml` | Initial compiled topology graph for root/src/tests/scripts/docs/config/CI/state/runtime/shadow surfaces |
| `source_rationale.yaml` | Per-file rationale map for tracked `src/**` files, hazards, and write-route cards |
| `test_topology.yaml` | Test-suite topology manifest: law gate, categories, high-sensitivity skips, reverse-antibody status |
| `script_manifest.yaml` | Script manifest with authority class, write targets, dry-run/apply metadata, and safety gates |
| `naming_conventions.yaml` | Canonical file/function naming and script/test freshness metadata map |
| `data_rebuild_topology.yaml` | Data/rebuild certification criteria and non-promotion topology |
| `history_lore.yaml` | Dense historical lore registry: failure modes, wrong moves, antibodies, residual risks, and task routing |
| `artifact_lifecycle.yaml` | Artifact classification and minimum work-record contract |
| `context_budget.yaml` | Context budget and maintenance cadence for keeping entry maps/digests slim |
| `module_manifest.yaml` | Machine registry for dense module books, module routers, and module-level law/current-fact/test links; now includes R3 CutoverGuard, venue adapter, user-channel ingest, forecast-live data-daemon, strategy benchmark, and risk allocator/governor routes |
| `context_pack_profiles.yaml` | Registry of digest profile context routes; consumed by digest emission. Standalone context-pack/semantic-bootstrap subcommands retired in PR #71 — use `--navigation --task-class`. |
| `task_boot_profiles.yaml` | Question-first semantic boot profiles for source/settlement/hourly/Day0/calibration/docs/graph tasks |
| `fatal_misreads.yaml` | Machine-readable fatal semantic shortcut antibodies |
| `city_truth_contract.yaml` | Stable city/source/date truth contract schema and evidence taxonomy |
| `code_review_graph_protocol.yaml` | DEPRECATED 2026-04-28 (validator stub only). Authoritative summary inlined in root AGENTS.md §Code Review Graph per round2_verdict.md §1.1 #9. Full removal pending script-aware batch. |
| `change_receipt_schema.yaml` | Machine-readable route/change receipt contract for high-risk closeout |
| `code_idioms.yaml` | Registry for intentional non-obvious code shapes such as static-analysis hooks |
| `core_claims.yaml` | Proof-backed semantic claims emitted by generated topology views |
| `runtime_modes.yaml` | Discovery mode index: opening_hunt, update_reaction, day0_capture |
| `reference_replacement.yaml` | Replacement matrix for bulky reference docs and deletion eligibility |
| `docs_registry.yaml` | Machine-readable docs classification registry and default-read contract |
| `map_maintenance.yaml` | Companion-registry rules for added/deleted files in active surfaces |
| `lifecycle_grammar.md` | Lifecycle grammar specification |
| `2026_04_02_architecture_kernel.sql` | Canonical event/projection schema — position_events, position_current, strategy_health, risk_actions, control_overrides, fact tables |
| `self_check/zero_context_entry.md` | Zero-context agent entry checklist |
| `ast_rules/semgrep_zeus.yml` | Semgrep rules for code enforcement |
| `ast_rules/forbidden_patterns.md` | Forbidden code patterns |
| `packet_templates/*.md` | Work packet templates (bugfix, feature, refactor, schema) |
| `worktree_merge_protocol.yaml` | Cross-session merge protocol per Stage 4 Gate B (verdict.md §6) — conflict-first merge inspection; MERGE_AUDIT_EVIDENCE critic verdict required only for escalated broad/high-risk conflict surfaces |
| `preflight_overrides_2026-04-28.yaml` | Operator-approved preflight drift overrides for WU ICAO history residuals (schema_version 1; created 2026-04-28) |
| `inv_prototype.py` | @enforced_by decorator prototype for round-2 §H1 INV annotation experiment (created 2026-04-28) |
| `world_schema_version.yaml` | Legacy world DB schema version sentinel retained for historical two-system independence registry; live boot authority is direct `zeus-world.db` and `zeus-forecasts.db` `PRAGMA user_version` checks in `src/main.py` |
| `runtime_posture.yaml` | Runtime posture YAML — read-only at runtime per INV-26 (created 2026-04-26) |
| `scope_schema.json` | JSON Schema for Zeus packet scope.yaml sidecar; machine-read by zpkt and pre-commit hook |
| `digest_profiles.py` | Auto-generated digest profiles for topology_doctor — DO NOT EDIT BY HAND; regenerated from topology.yaml |
| `paris_station_resolution_2026-05-01.yaml` | Operator decision 2026-05-01: Paris Polymarket settlement station is LFPB (not LFPG) |
| `data_sources_registry_2026_05_08.yaml` | BINDING authoritative registry of all 12 external data sources Zeus consumes (forecast, observation, settlement, market); created 2026-05-08; includes [VERIFIED]/[INFERRED] tags, forbidden patterns FP-01..FP-10, and cross-references to all binding architecture docs |
| `agent_pr_discipline_2026_05_09.md` | Agent PR discipline: 300-LOC threshold, auto-reviewer cost economics, decision tree, author detection, bypass protocol; hook `.claude/hooks/dispatch.py::_run_advisory_check_pr_create_loc_accumulation` |
| `admission_severity.yaml` | Topology v_next admission severity overrides; maps file patterns to admission severity levels (created 2026-05-07) |
| `antibody_specs.yaml` | Machine-readable antibody specs from zeus_agent_runtime_compounding plan W1.2 (created 2026-05-16) |
| `artifact_authority_status.yaml` | Closed-artifact authority distinction registry per UNIVERSAL_TOPOLOGY_DESIGN §13 (created 2026-05-16) |
| `calibration_transfer_oos_design_2026-05-05.md` | Design doc for calibration transfer OOS evidence; legacy `evaluate_calibration_transfer_policy` wiring analysis (created 2026-05-05) |
| `capabilities.yaml` | Capability registry with sunset dates; agent capability declarations per ULTIMATE_DESIGN §2.2 (created 2026-05-06) |
| `cascade_liveness_contract.yaml` | Registry of state-machine tables and their cascade liveness obligations per SCAFFOLD_F14_F16 §G.2 (created 2026-05-16) |
| `db_table_ownership.yaml` | Canonical table→DB mapping authority post K1 DB split (eba80d2b9d); cross-reference for all DB routing decisions (created 2026-05-14) |
| `ecmwf_opendata_tigge_equivalence_2026_05_06.yaml` | ECMWF OpenData/TIGGE equivalence proof registry; calibration READ-path audit evidence (created 2026-05-06) |
| `improvement_backlog.yaml` | Structured improvement backlog from PROPOSALS_2026-05-04 P3 context capsule (created 2026-05-04) |
| `math_defects_2_3_2_4_3_1_design_2026-05-05.md` | Unified design for math defects 2.3/2.4/3.1; DDD INV-17 fix + calibration transfer scaffolding (created 2026-05-05) |
| `reversibility.yaml` | Reversibility class registry per ULTIMATE_DESIGN §2.3; governs rollback obligations by change class (created 2026-05-06) |
| `settlement_dual_source_truth_2026_05_07.yaml` | Operator-confirmed settlement dual-source truth registry; Gamma backfill re-enabled decision (created 2026-05-07) |
| `strategy_profile_registry.yaml` | Strategy profile registry; oracle/kelly evidence rebuild authority (created 2026-05-04) |
| `topology_v_next_binding.yaml` | Zeus binding layer for topology_v_next P1; maps file patterns to cohort/admission intent (created 2026-05-15) |
| `zeus_grid_resolution_authority_2026_05_07.yaml` | Grid resolution authority Plan A; operator decision 2026-05-07 on ECMWF grid handling (created 2026-05-07) |

## Subdirectory navigation

Each subdirectory has its own `AGENTS.md` with file registry and rules:

| Subdirectory | AGENTS.md | Purpose |
|--------------|-----------|---------|
| `ast_rules/` | `ast_rules/AGENTS.md` | AST-level enforcement rules (Semgrep + forbidden patterns) |
| `packet_templates/` | `packet_templates/AGENTS.md` | Work packet templates for change classification |
| `self_check/` | `self_check/AGENTS.md` | Agent entry checklists |

## Review rule
At least one independent verifier must read the final diff before acceptance.
