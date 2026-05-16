# Code Review Graph Online Context Work Log

Date: 2026-04-20
Branch: data-improve
Task: Track Code Review Graph as an online context artifact.

Changed files:
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
- `docs/operations/task_2026-04-20_code_review_graph_online_context/plan.md`
- `docs/operations/task_2026-04-20_code_review_graph_online_context/work_log.md`
- `docs/operations/task_2026-04-20_code_review_graph_online_context/receipt.json`
- `scripts/topology_doctor_code_review_graph.py`
- `tests/test_topology_doctor.py`

Summary: Converted `.code-review-graph/graph.db` from ignored local cache to a
tracked derived online-context artifact while preserving non-authority graph
semantics and ignoring graph scratch byproducts.

Verification: `python scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-20_code_review_graph_online_context/plan.md`; `python -m py_compile scripts/topology_doctor_code_review_graph.py`; `python -m pytest -q tests/test_topology_doctor.py -k code_review_graph`; `python scripts/topology_doctor.py --code-review-graph-status --json` (passes with one warning for unrelated untracked `tests/test_phase10e_closeout.py` not represented in the graph); `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files ...`.

Next: After verification, push the branch so Pro/review agents can inspect the
tracked graph artifact from GitHub.
