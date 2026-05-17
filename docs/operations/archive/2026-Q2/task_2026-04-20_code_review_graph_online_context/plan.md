# Code Review Graph Online Context Plan

Date: 2026-04-20
Branch: data-improve

## Objective

Promote `.code-review-graph/graph.db` from local-only ignored cache to a tracked
derived online-context artifact so Pro/review agents can build a repo mental
model from GitHub without local disk access.

## Decision

Track `.code-review-graph/graph.db` in git as **derived context, not authority**.
It may help online reviewers inspect file/symbol/test impact, but it never
waives Zeus routing, planning-lock, source rationale, script manifest, test
topology, route receipts, or canonical truth rules.

Keep `.code-review-graph/` scratch byproducts ignored. The tracked DB is the
only intended durable graph artifact.

## Known Limitation

The current DB may contain local absolute paths. This is acceptable for the
first online-context artifact because the paths are graph metadata, not
authority. A later graph-builder hardening packet should prefer repo-relative
paths.

## Allowed Files

- `.gitignore`
- `.code-review-graph/.gitignore`
- `.code-review-graph/graph.db`
- `AGENTS.md`
- `workspace_map.md`
- `architecture/artifact_lifecycle.yaml`
- `architecture/topology.yaml`
- `architecture/topology_schema.yaml`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-20_code_review_graph_online_context/**`
- `scripts/topology_doctor_code_review_graph.py`
- `tests/test_topology_doctor.py`

## Forbidden Files

- `src/**`
- `state/**`
- `raw/**`
- `docs/archives/**`
- `.code-review-graph/*` except `.gitignore` and `graph.db`

## Verification

- `python -m py_compile scripts/topology_doctor_code_review_graph.py`
- `python -m pytest -q tests/test_topology_doctor.py -k code_review_graph`
- `python scripts/topology_doctor.py --code-review-graph-status --json`
- `git status --short --ignored=matching .code-review-graph`
- `git diff --cached --name-status -- .code-review-graph`

