# Architecture Index

Navigation entry point for the 55 YAML manifests and 12 design docs in this directory.
See [AGENTS.md](AGENTS.md) for routing rules and authority order. This file is a file map, not an authority source.

---

## Start Here

The five manifests that govern almost every code change:

| File | What it governs |
|------|-----------------|
| `invariants.yaml` | INV-01…N: machine-readable system invariants with enforcing tests |
| `negative_constraints.yaml` | Hard prohibitions (NC-##): forbidden patterns, cross-DB writes, JSON promotion |
| `money_path_ci.yaml` | CI routing rules: which diff classifications require which invariant proofs |
| `money_path_objects.yaml` | Registry of every money-path economic object; unknown objects are unsafe until registered |
| `topology.yaml` | Active nav authority for `topology_doctor.py`; drives routing, admission, and risk-tier decisions at runtime |

---

## Invariants & Constraints

| File | What it governs |
|------|-----------------|
| `invariants.yaml` | System invariants (INV-##) with zones, enforcing semgrep rules, and required tests |
| `negative_constraints.yaml` | Hard constraints (NC-##): what may never happen regardless of packet |
| `failure_chains.yaml` | FC-01…FC-10: formalized failure chains with root hazard, evidence, and required guards |
| `fatal_misreads.yaml` | Machine-readable antibodies for semantic equivalences that cause wrong-but-plausible changes |
| `pre_existing_failure_registry.yaml` | Failures observed before a PR that are temporarily allowed outside the active diff; entries expire |
| `antibody_specs.yaml` | Specs for runtime antibodies (citation-grep gate, etc.) |

---

## Money Path

| File | What it governs |
|------|-----------------|
| `money_path_ci.yaml` | Semantic diff → required invariant + relationship test routing |
| `money_path_objects.yaml` | Economic objects, state machines, external truth surfaces, strategy identity, scheduler jobs |
| `kernel_manifest.yaml` | Principal authority sources: architecture spec, change control constitution, operator brief |
| `cascade_liveness_contract.yaml` | State-machine tables in `src/`; required pollers per live mode; boot guard enforcement |
| `lifecycle_grammar.md` | Canonical lifecycle status strings across the codebase (DRAFT; migration PR pending) |

---

## Ownership & Topology

| File | What it governs |
|------|-----------------|
| `db_table_ownership.yaml` | Every table → DB (world/forecasts/trade/risk_state/backtest) + schema class + schema version owner |
| `module_manifest.yaml` | Package-level module book: ownership, hazard badges, write routes |
| `zones.yaml` | Zone grammar and package-level boundaries; file-level authority routes to `source_rationale.yaml` |
| `topology.yaml` | Nav authority graph read by `topology_doctor.py`; not read-only |
| `topology_enforcement.yaml` | BLOCKING vs ADVISORY vs NO_OVERRIDE rule split for CI |
| `topology_surfaces.yaml` | Surface registry: path/symbol/table → money-path object, risk tier, context packs, required tests |
| `source_rationale.yaml` | Per-file rationale map for `src/**`: why each file exists and what it protects |
| `test_topology.yaml` | Test trust policy: law antibodies vs regressions vs advisory vs stale |
| `script_manifest.yaml` | Safety manifest for `scripts/`: class, lifecycle, authority scope, dry-run defaults, DB targets |
| `map_maintenance.yaml` | Which registry files must move when source files are added or deleted |
| `data_rebuild_topology.yaml` | Certification criteria and non-promotion rules for data rebuild |
| `file_arrangement.yaml` | Workspace routing rules for file placement (advisory) |

---

## Sources & Data

| File | What it governs |
|------|-----------------|
| `data_sources_registry_2026_05_08.yaml` | All weather data sources (ECMWF, ENS, TIGGE, WU, HKO, OpenMeteo, ASOS): feeds, latency, authority |
| `source_rationale.yaml` | *(also listed under Ownership)* |
| `ecmwf_opendata_tigge_equivalence_2026_05_06.yaml` | Equivalence contract between ECMWF OpenData and TIGGE ensemble members |
| `zeus_grid_resolution_authority_2026_05_07.yaml` | Grid resolution authority: cell-distance-to-airport, fusion precision rules |
| `settlement_dual_source_truth_2026_05_07.yaml` | Settlement dual-source truth contract (Gamma + operator backup) |
| `paris_station_resolution_2026-05-01.yaml` | Canonical settlement station for Paris: LFPB, not LFPG |
| `city_truth_contract.yaml` | Per-city truth contract: settlement station canonical mapping |
| `preflight_overrides_2026-04-28.yaml` | Documented data drift residuals allowed through preflight |

---

## Strategy & Calibration

| File | What it governs |
|------|-----------------|
| `strategy_profile_registry.yaml` | Single source of truth for per-strategy authority (replaces 5 hardcoded sites) |
| `runtime_posture.yaml` | Branch posture authority (INV-26): read-only at runtime; change by PR only |
| `runtime_modes.yaml` | Machine-readable index of Zeus discovery modes |
| `reversibility.yaml` | Reversibility classes for changes (INV-related) |
| `core_claims.yaml` | Proof-backed semantic claims that topology views may emit as verified facts |

---

## Agent Runtime & Context Packs

| File | What it governs |
|------|-----------------|
| `task_boot_profiles.yaml` | Question-first boot profiles: source-sensitive, settlement, data-ingest, calibration, docs-authority |
| `context_pack_profiles.yaml` | Task-shaped context pack contracts for agents |
| `context_pack_schema.yaml` | Formal schema for context pack atomic output (from `topology_doctor_context_pack.py`) |
| `context_budget.yaml` | S0 context budget gate |
| `ci_overrides.yaml` | Bounded escape hatch registry for context pack / topology enforcement overrides |
| `worktree_merge_protocol.yaml` | Protocol for cross-session/worktree merges into Zeus branches |
| `agent_pr_discipline_2026_05_09.md` | Four first-principles of workflow quality (post PR #105/#106) |

---

## Governance & Code Standards

| File | What it governs |
|------|-----------------|
| `admission_severity.yaml` | Severity classification for CI admission gates |
| `capabilities.yaml` | Capability catalog (21 entries); authority basis §2.2 |
| `artifact_authority_status.yaml` | Closed-artifact authority distinction: what is/is not canonical truth |
| `artifact_lifecycle.yaml` | Lifecycle classification for agent-created files |
| `change_receipt_schema.yaml` | Route/change receipts: typed route-artifact evidence for high-risk closeout |
| `naming_conventions.yaml` | Canonical naming map for files, functions, and freshness metadata |
| `canonical_vocabulary.yaml` | One-concept-one-name registry (checked_policy_input, advisory-scope): canonical terms + forbidden aliases + migration wave, checked by `topology_doctor --repr` on new code only |
| `code_idioms.yaml` | Registry for intentional non-obvious code shapes |
| `maturity_model.yaml` | Current (`hardened_transition`) → target (`governed_runtime`) state |
| `reference_replacement.yaml` | Tracks which bulky reference docs have been replaced by machine manifests |
| `improvement_backlog.yaml` | Operator queue for improvement insights surfaced by the AGENTS.md context capsule |
| `history_lore.yaml` | Dense registry of hard-won failure modes, wrong moves, durable rules |

---

## Design Docs (dated .md)

Point-in-time design records. Not authority — read the YAML manifests and source for current state.

| File | Subject |
|------|---------|
| `calibration_transfer_oos_design_2026-05-05.md` | OOS calibration transfer evidence gap (PR #55/#56 wiring) |
| `math_defects_2_3_2_4_3_1_design_2026-05-05.md` | Unified design for math defects 2.3, 2.4, 3.1 (post Phase 0a) |
| `exit_strategy_audit_2026_05_27.md` | Structural verification of exit strategy code (base b360211d99) |
| `exit_strategy_integration_plan_2026_05_27.md` | Integration plan following exit strategy math review |
| `market_cost_seam_executable_uncertainty_2026_05_27.md` | Market-cost seam + executable-uncertainty architecture upgrade |
| `pr348_premerge_critic_2026_05_27.md` | Pre-merge critic pass for PR #348 |
| `pr_exit_strategy_premerge_critic_2026_05_27.md` | Pre-merge critic pass 1 for exit strategy PR |
| `pr_exit_strategy_premerge_critic_pass2_2026_05_27.md` | Pre-merge critic pass 2 for exit strategy PR |
| `world_mutex_io_offmutex_refactor_2026_06_04.md` | Plan to kill blocking I/O under world write mutex (2026-06-04 incident) |
| `lifecycle_grammar.md` | *(also listed under Money Path)* |
| `agent_pr_discipline_2026_05_09.md` | *(also listed under Agent Runtime)* |
