# Docs Truth Refresh P0 Plan

Date: 2026-04-22
Branch: `data-improve`
Package: `zeus_docs_truth_refresh_reconstruction_package_2026-04-22`
Phase: P0 stale truth purge and current fact install

## Objective

Remove stale factual influence from trusted docs surfaces by making
`docs/reference/` canonical-only, installing current-fact surfaces under
`docs/operations/`, and routing dated support material to `docs/reports/`.

## Allowed scope

- docs routers and references
- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- moved legacy reference snapshots under `docs/reports/`
- docs machine manifests needed for routing
- narrow stale-path updates in `architecture/history_lore.yaml` and
  `docs/operations/data_rebuild_plan.md` when they only point old reference
  paths at the moved legacy reports
- this packet folder

## Forbidden scope

- `src/**`
- `state/**`
- `raw/**`
- `.code-review-graph/graph.db`
- `docs/archives/**`
- `docs/authority/**`
- runtime DBs or generated outputs

## Local adaptations

- `architecture/reference_replacement.yaml` may be updated only by removing
  entries for support docs that no longer live in `docs/reference/`. P2 owns
  retirement or freshness-aware redesign of that manifest.
- `docs/reference/market_microstructure.md` had a pre-existing dirty wording
  fix. P0 preserves that content when moving the file to reports.

## Verification

- `python scripts/topology_doctor.py --docs --json`
- `python scripts/topology_doctor.py --context-budget --json`
- `python scripts/topology_doctor.py --reference-replacement --json`
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P0 files> --json`
- `python scripts/topology_doctor.py --planning-lock --changed-files <P0 files> --plan-evidence docs/operations/task_2026-04-22_docs_truth_refresh/plan.md --json`
- `python scripts/topology_doctor.py --work-record --changed-files <P0 files> --work-record-path docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md --json`
- `python scripts/topology_doctor.py --change-receipts --changed-files <P0 files> --receipt-path docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json --json`
- `python scripts/topology_doctor.py closeout --changed-files <P0 files> --plan-evidence docs/operations/task_2026-04-22_docs_truth_refresh/plan.md --work-record-path docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md --receipt-path docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json --json`
- `pytest -q tests/test_topology_doctor.py -k "docs or map_maintenance or context_budget"`
- stale-truth negative grep bundle from the package
- `git diff --check`
- `git diff -- docs/authority`
