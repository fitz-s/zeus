File: architecture/AGENTS.md
Disposition: NEW
Authority basis: docs/authority/zeus_current_architecture.md; docs/authority/zeus_current_delivery.md; docs/authority/zeus_change_control_constitution.md; architecture/kernel_manifest.yaml; architecture/invariants.yaml; architecture/zones.yaml; architecture/negative_constraints.yaml; architecture/maturity_model.yaml.
Supersedes / harmonizes: informal architecture claims in historical docs.
Why this file exists now: this is the K0/K1 law zone and needs narrower instructions than the repo root.
Current-phase or long-lived: Long-lived.

# architecture AGENTS

This directory contains machine-checkable and constitutional authority surfaces.

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
| `topology_schema.yaml` | Schema for compiled topology graph nodes, enums, and strict issue codes |
| `topology.yaml` | Initial compiled topology graph for root/src/tests/scripts/docs/config/CI/state/runtime/shadow surfaces |
| `source_rationale.yaml` | Per-file rationale map for tracked `src/**` files, hazards, and write-route cards |
| `test_topology.yaml` | Test-suite topology manifest: law gate, categories, high-sensitivity skips, reverse-antibody status |
| `script_manifest.yaml` | Script manifest with authority class, write targets, dry-run/apply metadata, and safety gates |
| `naming_conventions.yaml` | Canonical file/function naming and script/test freshness metadata map |
| `data_rebuild_topology.yaml` | Data/rebuild certification criteria and non-promotion topology |
| `history_lore.yaml` | Dense historical lore registry: failure modes, wrong moves, antibodies, residual risks, and task routing |
| `artifact_lifecycle.yaml` | Artifact classification and minimum work-record contract |
| `context_budget.yaml` | Context budget and maintenance cadence for keeping entry maps/digests slim |
| `context_pack_profiles.yaml` | Task-shaped context-pack profiles for generated agent work packets |
| `task_boot_profiles.yaml` | Question-first semantic boot profiles for source/settlement/hourly/Day0/calibration/docs/graph tasks |
| `fatal_misreads.yaml` | Machine-readable fatal semantic shortcut antibodies |
| `city_truth_contract.yaml` | Stable city/source/date truth contract schema and evidence taxonomy |
| `code_review_graph_protocol.yaml` | Two-stage graph protocol: semantic boot first, graph derived context second |
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

## Subdirectory navigation

Each subdirectory has its own `AGENTS.md` with file registry and rules:

| Subdirectory | AGENTS.md | Purpose |
|--------------|-----------|---------|
| `ast_rules/` | `ast_rules/AGENTS.md` | AST-level enforcement rules (Semgrep + forbidden patterns) |
| `packet_templates/` | `packet_templates/AGENTS.md` | Work packet templates for change classification |
| `self_check/` | `self_check/AGENTS.md` | Agent entry checklists |

## Review rule
At least one independent verifier must read the final diff before acceptance.
