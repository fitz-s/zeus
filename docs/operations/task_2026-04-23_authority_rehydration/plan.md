# Authority Rehydration Plan

Date: 2026-04-23
Branch: `data-improve`
Classification: governance/reference/manifest
Phase: P3 topology and context-pack support

## Objective

Rehydrate Zeus's cognition layer around dense module books, richer manifests,
and module-aware routing while keeping the authority kernel small and the
runtime/source semantics frozen unless a later phase explicitly expands scope.

## Source Package

- `/Users/leofitz/Downloads/zeus_authority_rehydration_package_2026-04-23`
- `README.md`
- `00_executive_ruling.md`
- `02_authority_rehydration_strategy.md`
- `03_module_doc_standard.md`
- `07_topology_manifest_rewrite_plan.md`
- `17_apply_order.md`
- `patch_blueprints/p0_authority_rehydration_blueprint.md`

## Phase Order

- P0: scaffold packet, module manifest, and docs/topology routing
- P1: first-wave module books for `state`, `engine`, and `data`
- P2: remaining module books plus manifest enrichment
- P3: topology doctor / context-pack support for the module layer
- P4: archive and graph extraction into durable module/system docs

## P0 Scope

Allowed:

- `AGENTS.md`
- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `architecture/context_budget.yaml`
- `architecture/map_maintenance.yaml`
- `architecture/module_manifest.yaml`
- `docs/README.md`
- `docs/AGENTS.md`
- `docs/reference/AGENTS.md`
- `docs/reference/modules/AGENTS.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_authority_rehydration/**`

Forbidden:

- `src/**`
- `state/**`
- `.code-review-graph/graph.db`
- runtime DBs
- current source/data semantics
- broad `docs/authority/**` rewrites
- archive bodies

## P0 Acceptance

- authority rehydration packet exists with plan, work log, and receipt
- `architecture/module_manifest.yaml` exists in skeletal form only
- docs routing recognizes `docs/reference/modules/` as dense reference
- module books remain reference/cognition, not authority
- topology doctor gaps are recorded as follow-up, not silently ignored

## P1 Scope

Allowed:

- `docs/reference/modules/state.md`
- `docs/reference/modules/engine.md`
- `docs/reference/modules/data.md`
- `docs/reference/modules/AGENTS.md`
- `src/state/AGENTS.md`
- `src/engine/AGENTS.md`
- `src/data/AGENTS.md`
- `architecture/module_manifest.yaml`
- `architecture/docs_registry.yaml`
- `docs/reference/AGENTS.md`
- `docs/reference/zeus_architecture_reference.md`
- `docs/reference/zeus_data_and_replay_reference.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_authority_rehydration/**`
- companion routing updates required by map maintenance

Forbidden:

- `src/**/*.py`
- `state/**`
- `.code-review-graph/graph.db`
- `docs/authority/**` core-law rewrites
- archives

## P1 Acceptance

- dense module books exist for `state`, `engine`, and `data`
- `src/state/AGENTS.md`, `src/engine/AGENTS.md`, and `src/data/AGENTS.md`
  become medium-density launchers with module-book pointers
- `architecture/module_manifest.yaml` is enriched for the three first-wave
  entries
- docs registry classifies the three books explicitly
- system references link to the new books without promoting them to law

## P2 Scope

Allowed:

- remaining `docs/reference/modules/*.md`
- remaining scoped `AGENTS.md` launchers
- `architecture/module_manifest.yaml`
- `architecture/source_rationale.yaml`
- `architecture/test_topology.yaml`
- `architecture/script_manifest.yaml`
- selected routing/reference surfaces needed by map maintenance and book links
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_authority_rehydration/**`

Forbidden:

- runtime/source behavior files under `src/**/*.py`
- `state/**`
- `.code-review-graph/graph.db`
- archive bodies
- broad core-law rewrites

## P2 Acceptance

- remaining module books exist under `docs/reference/modules/`
- remaining launcher surfaces point to their module books
- `architecture/module_manifest.yaml` carries meaningful routing fields for all
  modules
- `architecture/source_rationale.yaml` covers previously unregistered sharp
  files and no longer points at removed `wu_daily_collector.py`
- `architecture/test_topology.yaml` and `architecture/script_manifest.yaml`
  carry high-risk module-routing metadata for the sharpest test/script families

## P3 Scope

Allowed:

- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_context_pack.py`
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_authority_rehydration/**`

Forbidden:

- runtime/source behavior
- DB files
- `.code-review-graph/graph.db`
- archive bodies

## P3 Acceptance

- topology_doctor exposes module-book and module-manifest lanes
- context packs surface matched module books, high-risk files, required tests,
  current-fact dependencies, and graph appendix hints
- new checks are warning-first rather than blocking by default
- topology_doctor tests cover the new lanes and context-pack payload fields

## Verification

- `python scripts/topology_doctor.py --docs --json`
- `python scripts/topology_doctor.py --planning-lock --changed-files <P0 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --json`
- `python scripts/topology_doctor.py --context-budget --json`
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P0 files> --json`
- `git diff --check -- <P0 files>`

## Follow-on Notes

- P1 must populate the first three module books and upgrade their scoped
  `AGENTS.md` files into medium-density launchers.
- P3 owns new topology-doctor enforcement lanes; P0 only makes the layer
  routable.
