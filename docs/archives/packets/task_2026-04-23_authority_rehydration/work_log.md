# Authority Rehydration Work Log

Date: 2026-04-23
Branch: `data-improve`
Task: P0 scaffold

Changed files:

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
- `docs/operations/task_2026-04-23_authority_rehydration/plan.md`
- `docs/operations/task_2026-04-23_authority_rehydration/work_log.md`
- `docs/operations/task_2026-04-23_authority_rehydration/receipt.json`

Summary:

- activated the authority rehydration packet as the live operations pointer
- added `architecture/module_manifest.yaml` as the skeletal machine registry for
  the dense module-reference layer
- created `docs/reference/modules/AGENTS.md` as the router for future module
  books
- updated root/docs/reference/topology routing so module books are visible as
  dense reference, not authority
- expanded context-budget and map-maintenance policies so the new layer has
  explicit routing and ownership rules

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --current-state-receipt-bound --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <P0 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P0 files> --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <P0 files> --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P0 files> --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P0 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok
- `git diff --check -- <P0 files>` -> ok

Known follow-up:

- P1 will add the first concrete module books and scoped router upgrades for
  `state`, `engine`, and `data`.
- P3 will add topology-doctor enforcement for module-book presence, module
  manifest coherence, and module-book section completeness.

Next:

- land P1 first-wave module books for `state`, `engine`, and `data`
- upgrade `src/state/AGENTS.md`, `src/engine/AGENTS.md`, and `src/data/AGENTS.md`
  into medium-density launchers

## P1 First-Wave Module Books

Changed files:

- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/docs_registry.yaml`
- `architecture/module_manifest.yaml`
- `docs/reference/AGENTS.md`
- `docs/reference/modules/AGENTS.md`
- `docs/reference/modules/state.md`
- `docs/reference/modules/engine.md`
- `docs/reference/modules/data.md`
- `docs/reference/zeus_architecture_reference.md`
- `docs/reference/zeus_data_and_replay_reference.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_authority_rehydration/plan.md`
- `docs/operations/task_2026-04-23_authority_rehydration/work_log.md`
- `docs/operations/task_2026-04-23_authority_rehydration/receipt.json`
- `src/state/AGENTS.md`
- `src/engine/AGENTS.md`
- `src/data/AGENTS.md`

Summary:

- landed dense module books for `state`, `engine`, and `data`
- upgraded the three scoped `AGENTS.md` files into explicit launchers that
  force module-book reads before risky edits
- enriched the first-wave `architecture/module_manifest.yaml` entries with
  high-risk files, law/current-fact dependencies, required tests, and graph
  follow-up markers
- registered the books in `architecture/docs_registry.yaml` and linked the
  system references to the new module layer
- advanced `current_state.md` so the live pointer now routes P2 as the next step

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --source --json` -> repo-wide baseline still has unrelated `source_rationale` gaps, but packet-scoped closeout source lane for changed files passed
- `python scripts/topology_doctor.py --planning-lock --changed-files <P1 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P1 files> --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <P1 files> --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P1 files> --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P1 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok
- `git diff --check -- <P1 files>` -> ok

Next:

- land P2 remaining module books and broader manifest enrichment

## P2 Remaining Books And Manifest Enrichment

Changed files:

- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/docs_registry.yaml`
- `architecture/module_manifest.yaml`
- `architecture/naming_conventions.yaml`
- `architecture/source_rationale.yaml`
- `architecture/test_topology.yaml`
- `architecture/script_manifest.yaml`
- `docs/AGENTS.md`
- `docs/reference/AGENTS.md`
- `docs/reference/modules/AGENTS.md`
- `docs/reference/modules/contracts.md`
- `docs/reference/modules/state.md`
- `docs/reference/modules/engine.md`
- `docs/reference/modules/data.md`
- `docs/reference/modules/execution.md`
- `docs/reference/modules/riskguard.md`
- `docs/reference/modules/control.md`
- `docs/reference/modules/supervisor_api.md`
- `docs/reference/modules/strategy.md`
- `docs/reference/modules/signal.md`
- `docs/reference/modules/calibration.md`
- `docs/reference/modules/observability.md`
- `docs/reference/modules/types.md`
- `docs/reference/modules/analysis.md`
- `docs/reference/modules/scripts.md`
- `docs/reference/modules/tests.md`
- `docs/reference/modules/topology_system.md`
- `docs/reference/modules/docs_system.md`
- `docs/reference/modules/code_review_graph.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_authority_rehydration/plan.md`
- `docs/operations/task_2026-04-23_authority_rehydration/work_log.md`
- `docs/operations/task_2026-04-23_authority_rehydration/receipt.json`
- `scripts/AGENTS.md`
- `tests/AGENTS.md`
- `src/contracts/AGENTS.md`
- `src/control/AGENTS.md`
- `src/execution/AGENTS.md`
- `src/riskguard/AGENTS.md`
- `src/supervisor_api/AGENTS.md`
- `src/strategy/AGENTS.md`
- `src/signal/AGENTS.md`
- `src/calibration/AGENTS.md`
- `src/observability/AGENTS.md`
- `src/types/AGENTS.md`
- `src/analysis/AGENTS.md`

Summary:

- landed the remaining module books under `docs/reference/modules/`
- upgraded the remaining launcher surfaces so every module routes into a dense
  book and the shared module manifest
- expanded `architecture/module_manifest.yaml` across all registered modules
- filled source-rationale gaps for previously unregistered sharp files and
  removed the stale `wu_daily_collector.py` rationale entry
- added high-risk test/script metadata so module books can point to exact proof
  and script surfaces instead of vague families

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --source --json` -> ok
- `python scripts/topology_doctor.py --tests --json` -> ok
- `python scripts/topology_doctor.py --scripts --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <P2 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok with advisory warning: `architecture/module_manifest.yaml` exceeds its current budget baseline
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P2 files> --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <P2 files> --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P2 files> --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P2 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok with advisory warnings: stale Code Review Graph head and module_manifest context-budget overflow
- `git diff --check -- <P2 files>` -> ok

Next:

- land P3 topology-doctor and context-pack support for the module layer

## P3 Topology And Context-Pack Support

Changed files:

- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_context_pack.py`
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_authority_rehydration/plan.md`
- `docs/operations/task_2026-04-23_authority_rehydration/work_log.md`
- `docs/operations/task_2026-04-23_authority_rehydration/receipt.json`

Summary:

- added warning-first `module-books` and `module-manifest` topology-doctor lanes
- taught context packs to attach matched module-book/module-manifest summaries
- extended repo-health reporting so context packs include module-layer health
- added CLI and test coverage for the new module-aware tooling surfaces

Verification:

- `pytest -q tests/test_topology_doctor.py` -> 210 passed, 1 external deprecation warning
- `python scripts/topology_doctor.py --module-books --json` -> ok with advisory warning: root `AGENTS.md` still serves as the code-review-graph launcher without a direct book pointer
- `python scripts/topology_doctor.py --module-manifest --json` -> ok with advisory warnings for modules whose current-fact/test lists are intentionally empty
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <P3 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <P3 files> --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P3 files> --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P3 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok with advisory Code Review Graph staleness warnings
- `python -m py_compile scripts/topology_doctor*.py` -> ok
- `git diff --check -- <P3 files>` -> ok

Next:

- land P4 archive and graph extraction closeout

## P4 Archive Extraction And Closeout

Changed files:

- `architecture/history_lore.yaml`
- `docs/archive_registry.md`
- `docs/reference/modules/contracts.md`
- `docs/reference/modules/execution.md`
- `docs/reference/modules/strategy.md`
- `docs/reference/modules/topology_system.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_authority_rehydration/plan.md`
- `docs/operations/task_2026-04-23_authority_rehydration/work_log.md`
- `docs/operations/task_2026-04-23_authority_rehydration/receipt.json`

Summary:

- normalized archive evidence paths inside the active module references that
  were already carrying historical lessons
- added an explicit archive extraction ledger to `docs/archive_registry.md`
- promoted three durable archive lessons into `architecture/history_lore.yaml`
  without promoting archive bodies into ambient context
- closed the authority rehydration packet with current_state pointing to a
  complete P0-P4 program

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok with advisory warnings: `architecture/history_lore.yaml` and `docs/archive_registry.md` exceed their current budget baselines
- `git grep -n "docs/archives/" docs AGENTS.md workspace_map.md architecture || true` -> inspected; archive references remain explicit archive-evidence citations or archive-policy surfaces
- `python scripts/topology_doctor.py --work-record --changed-files <P4 files> --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P4 files> --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P4 files> --plan-evidence docs/operations/task_2026-04-23_authority_rehydration/plan.md --work-record-path docs/operations/task_2026-04-23_authority_rehydration/work_log.md --receipt-path docs/operations/task_2026-04-23_authority_rehydration/receipt.json --json` -> ok with advisory warnings: stale Code Review Graph head and minor context-budget overflow on archive/history surfaces
- `git diff --check -- <P4 files>` -> ok

Next:

- packet complete
