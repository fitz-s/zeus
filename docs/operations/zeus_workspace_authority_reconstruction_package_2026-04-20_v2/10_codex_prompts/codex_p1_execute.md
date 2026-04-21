# Codex P1 Execute Prompt

Execute **P1 — Machine Visibility and Registry Alignment** only after P0 is implemented and reviewed.

## Read first

1. `00_executive_ruling.md`
2. `02_authority_order_rewrite.md`
3. `06_topology_debt_triage.md`
4. `07_execution_packets.md`
5. `08_patch_blueprints/p1_patch_blueprint.md`
6. `11_pro_followup_prompt.md` review result

## Allowed files

- architecture/topology.yaml
- architecture/topology_schema.yaml
- architecture/map_maintenance.yaml
- architecture/context_budget.yaml
- architecture/artifact_lifecycle.yaml
- docs/README.md
- docs/AGENTS.md
- docs/archive_registry.md
- docs/operations/AGENTS.md
- docs/operations/current_state.md
- scripts/topology_doctor_map_maintenance.py
- scripts/topology_doctor_registry_checks.py
- tests/test_topology_doctor.py
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json

## Forbidden files

- src/**
- scripts/code_review_graph_mcp_readonly.py
- scripts/topology_doctor_code_review_graph.py
- .code-review-graph/graph.db
- docs/archives/**
- state/**
- raw/**
- .omx/**
- .omc/**

## Mission

Encode the P0 visibility/current-state/archive-interface policy into machine law with the smallest possible checker/test expansion.

## Rules

- Preserve unrelated dirty work.
- No source/runtime behavior changes.
- No graph wrapper work yet.
- No archive-body promotion.
- Add only the minimum tests needed to guard the new policy.

## Validation

```bash
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --strict --json
python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files architecture/topology.yaml architecture/topology_schema.yaml architecture/map_maintenance.yaml architecture/context_budget.yaml architecture/artifact_lifecycle.yaml docs/archive_registry.md docs/README.md docs/AGENTS.md docs/operations/current_state.md docs/operations/AGENTS.md scripts/topology_doctor_map_maintenance.py scripts/topology_doctor_registry_checks.py tests/test_topology_doctor.py --json
pytest -q tests/test_topology_doctor.py -k "docs or registry or current_state or map_maintenance"
git diff --cached --check
```

## Output required

- exact files changed
- why each machine-law change was necessary
- test/check results
- staged diff summary
- proposed Lore Commit Protocol message
