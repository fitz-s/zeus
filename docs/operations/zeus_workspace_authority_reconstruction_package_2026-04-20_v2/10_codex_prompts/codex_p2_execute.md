# Codex P2 Execute Prompt

Execute **P2 — Graph Portability and Online Summary Upgrade** only after P0 and preferably P1 are in place.

## Read first

1. `04_code_review_graph_policy.md`
2. `06_topology_debt_triage.md`
3. `07_execution_packets.md`
4. `08_patch_blueprints/p2_patch_blueprint.md`
5. latest P0/P1 review notes

## Allowed files

- .gitignore
- .code-review-graph/.gitignore
- .code-review-graph/graph_meta.json
- architecture/topology.yaml
- architecture/artifact_lifecycle.yaml
- architecture/context_budget.yaml
- architecture/script_manifest.yaml
- scripts/code_review_graph_mcp_readonly.py
- scripts/topology_doctor.py
- scripts/topology_doctor_cli.py
- scripts/topology_doctor_code_review_graph.py
- scripts/topology_doctor_context_pack.py
- tests/test_topology_doctor.py
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json

## Forbidden files

- src/**
- docs/authority/**
- docs/archives/**
- state/**
- raw/**
- .omx/**
- .omc/**
- runtime DBs

## Mission

Make Zeus's tracked Code Review Graph lane portable and more online-legible without promoting it into authority.

## Required posture

- Preserve the read-only safety boundary.
- Remove hardcoded repo-root assumptions.
- Prefer env/repo-relative discovery.
- Only add `graph_meta.json` if you can generate and verify it truthfully.
- Do not hand-edit `graph.db`.
- Do not stage regenerated graph artifacts until validation passes and you are confident the local builder is correct.

## Validation

```bash
python scripts/topology_doctor.py --code-review-graph-status --json
python scripts/topology_doctor.py --context-packs --json
python -m py_compile scripts/code_review_graph_mcp_readonly.py scripts/topology_doctor.py scripts/topology_doctor_cli.py scripts/topology_doctor_code_review_graph.py scripts/topology_doctor_context_pack.py
pytest -q tests/test_topology_doctor.py -k "code_review_graph or context_pack"
git diff --cached --check
```

## Output required

- whether `graph_meta.json` was added or deferred
- path-mode result (`absolute`, `repo_relative`, `mixed`, or unknown)
- wrapper portability change summary
- exact validation results
- staged diff summary
- proposed Lore Commit Protocol message
