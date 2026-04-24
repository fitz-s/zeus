# Docs Reclassification / Reference Extraction Plan

Date: 2026-04-21
Branch: data-improve
Classification: governance

## Objective

Apply P0 from the downloaded docs reclassification package:

`/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21`

P0 installs machine-readable docs classification and canonical reference
anchors. It does not perform the heavy P1 extraction/demotion sweep.

## P0 Scope

Allowed:

- `architecture/docs_registry.yaml`
- routing docs and docs manifests
- `docs/known_gaps.md` -> `docs/operations/known_gaps.md`
- canonical reference anchor files under `docs/reference/`
- topology_doctor docs checks and targeted tests
- active packet plan/work log/receipt

Forbidden:

- `src/**`
- `state/**`
- `raw/**`
- `.code-review-graph/graph.db`
- archive bodies or archive bundles
- semantic law changes in `docs/authority/**`
- P1 reference extraction, root snapshot demotion, fragment deletion, or
  runbook sanitization

## Local Adaptations

- Register `docs/operations/task_2026-04-21_gate_f_data_backfill/` so docs
  checks are not blocked by a visible operation task folder.
- Index `.omx/plans/docs-reclassification-p0-ralplan*.md` in
  `docs/operations/runtime_artifact_inventory.md`.

These adaptations are routing hygiene only and are not docs reclassification
semantics.

## Verification

- `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_docs_checks.py scripts/topology_doctor_map_maintenance.py`
- `python scripts/topology_doctor.py --docs --json`
- `python scripts/topology_doctor.py --context-budget --json`
- `python scripts/topology_doctor.py --reference-replacement --json`
- `python scripts/topology_doctor.py --history-lore --json`
- `python -m pytest -q tests/test_topology_doctor.py -k "docs or map_maintenance or context_budget"`
- `git diff --check`

## P1 Addendum

Plan evidence:

- `.omx/plans/docs-reclassification-p1-ralplan-revised.md`

Allowed:

- extract durable content into the four canonical reference files under
  `docs/reference/`
- move root-level legacy snapshots into `docs/artifacts/`, `docs/reports/`,
  `docs/reference/`, or `docs/runbooks/` according to their role
- update docs routers, `architecture/docs_registry.yaml`,
  `architecture/reference_replacement.yaml`, `architecture/context_budget.yaml`,
  and `architecture/topology.yaml`
- update this packet's work log, receipt, and current-state pointer

Forbidden:

- `src/**`
- `state/**`
- `raw/**`
- `.code-review-graph/graph.db`
- `docs/archives/**` or archive bundle ingestion
- semantic law changes in `docs/authority/**`
- deleting conditional reference sources or historical evidence files

## P2 Addendum

Plan evidence:

- `.omx/plans/docs-reclassification-p2-ralplan-2026-04-21.md`

Allowed:

- normalize `docs/runbooks/**` and `docs/operations/**`
- demote the TIGGE cloud wiring snapshot to evidence-only if sanitization is
  uncertain
- add a compact durable TIGGE cloud runbook without machine-specific details
- update docs routers, `architecture/docs_registry.yaml`,
  `architecture/artifact_lifecycle.yaml`, `architecture/context_budget.yaml`,
  current-state pointer, work log, and receipt

Forbidden:

- `src/**`
- `state/**`
- `raw/**`
- `.code-review-graph/graph.db`
- `docs/archives/**` or archive bundle ingestion
- semantic law changes in `docs/authority/**`
- deleting operator evidence without a replacement route
