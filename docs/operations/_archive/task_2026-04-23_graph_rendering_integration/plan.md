# Graph Rendering Integration Plan

Date: 2026-04-23
Branch: `data-improve`
Classification: derived-context/docs integration
Phase: implementation prep

## Objective

Use the remaining value of
`/Users/leofitz/Downloads/zeus_graph_db_deep_rendering_package_2026-04-23`
to create an implementation packet for repo-relative, human-readable graph
summaries and graph-aware module/context-pack integration, without elevating
graph authority or changing official refresh behavior.

## Source Package

- `/Users/leofitz/Downloads/zeus_graph_db_deep_rendering_package_2026-04-23`
- `05_hidden_paths_report.md`
- `08_module_impact_map.md`
- `09_high_risk_nodes_and_edges.md`
- `10_test_coverage_and_gap_map.md`
- `11_authority_integration_policy.md`
- `12_agent_workflow_prompts.md`
- `14_machine_readable_summary.json`

## Implementation Target

The packet should prepare a later implementation that:

1. regenerates current graph-derived summaries from the live local graph using
   official `code-review-graph` commands
2. converts high-value graph findings into repo-relative text surfaces
3. integrates those summaries into Zeus module books and/or topology_doctor
   context packs
4. keeps `graph.db` derived-only, non-authority, and non-ambient

## Proposed Phase Split

- P0: packet activation and current graph/value audit
- P1: repo-relative rendered graph summary targets and file placement plan
- P2: context-pack / module-book integration plan
- P3: implementation and review

## Allowed files

- `architecture/topology.yaml`
- `docs/operations/current_state.md`
- `docs/operations/AGENTS.md`
- `docs/operations/task_2026-04-23_graph_rendering_integration/**`

Forbidden for this prep packet:

- runtime/source behavior
- `.code-review-graph/graph.db` staging
- custom graph refresh logic
- archive bodies

## Acceptance

- packet exists and is the active execution packet
- implementation target is narrowed to remaining graph deep-rendering value
- packet clearly states that official graph refresh behavior is preserved
- packet does not yet widen into source/runtime edits

## Verification

- `python scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-23_graph_rendering_integration/plan.md --json`
- `python scripts/topology_doctor.py --docs --json`
- `python scripts/topology_doctor.py --work-record --changed-files <packet files> --work-record-path docs/operations/task_2026-04-23_graph_rendering_integration/work_log.md --json`
- `python scripts/topology_doctor.py --change-receipts --changed-files <packet files> --receipt-path docs/operations/task_2026-04-23_graph_rendering_integration/receipt.json --json`
- `python scripts/topology_doctor.py closeout --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-23_graph_rendering_integration/plan.md --work-record-path docs/operations/task_2026-04-23_graph_rendering_integration/work_log.md --receipt-path docs/operations/task_2026-04-23_graph_rendering_integration/receipt.json --json`
- `git diff --check -- <packet files>`
