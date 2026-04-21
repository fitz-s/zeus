# Validation Matrix

| Command | What it proves | Expected result | Failure interpretation | Blocks commit? |
|---|---|---|---|---|
| `python scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence ... --json` | The packet is validly framed for high-sensitivity edits. | JSON says planning-lock satisfied. | Packet scope/evidence is missing or invalid. | Yes |
| `python scripts/topology_doctor.py --work-record --changed-files ... --work-record-path ... --json` | Work log exists and is coherent enough for the packet. | JSON ok. | Work record missing or malformed. | Yes |
| `python scripts/topology_doctor.py --change-receipts --changed-files ... --receipt-path ... --json` | Receipt shape and closeout evidence satisfy repo contract. | JSON ok. | Receipt missing/invalid. | Yes |
| `python scripts/topology_doctor.py --docs --json` | Docs tree, registries, pointers, and docs-specific authority wiring are coherent. | No docs issues. | Stale registry/path/reference drift in docs surfaces. | Yes for P0/P1/P3 |
| `python scripts/topology_doctor.py --strict --json` | Full workspace-law surface remains coherent. | No strict issues. | Machine-law or registry drift remains. | Yes for P1; optional-but-strong for others |
| `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files ... --json` | Companion-file updates landed together. | No companion-missing issue. | Partial routing change likely to drift later. | Yes |
| `python scripts/topology_doctor.py --context-budget --json` | Boot surfaces are within declared size/density budgets. | No budget issues. | Repaired entry surface got too thick again. | Yes for P1; advisory in P0 if unchanged budgets |
| `python scripts/topology_doctor.py --code-review-graph-status --json` | Graph contract, freshness/usability, and tracked-db expectations. | Graph status loads; issues are absent or explicitly warning-grade. | Missing/untracked DB or unusable graph lane. | Yes for graph packets; warning-grade if stale only |
| `python scripts/topology_doctor.py --context-packs --json` | Context-pack builders still work and graph appendices stay non-authority. | JSON ok. | Derived context pack logic drifted. | Yes for P2 |
| `python -m py_compile scripts/code_review_graph_mcp_readonly.py scripts/topology_doctor.py scripts/topology_doctor_cli.py scripts/topology_doctor_code_review_graph.py scripts/topology_doctor_context_pack.py` | Touched Python files still parse. | No output. | Syntax error or import-level parse issue. | Yes |
| `pytest -q tests/test_topology_doctor.py -k "docs or registry or current_state or map_maintenance"` | Docs/topology packet protections still hold. | Selected tests pass. | P0/P1 policy not protected. | Yes for P1 |
| `pytest -q tests/test_topology_doctor.py -k "code_review_graph or context_pack"` | Graph portability and summary policy still holds. | Selected tests pass. | P2 broke graph lane behavior. | Yes for P2 |
| `python scripts/topology_doctor.py --history-lore --json` | History-lore registry stays valid after P3. | JSON ok. | Lore schema/routing drifted. | Yes for P3 |
| `git diff --cached --check` | Staged patch has no whitespace/conflict-marker defects. | Clean output. | Patch hygiene failure. | Yes |
| `git status --short` | Confirms what is staged vs unstaged and protects unrelated dirty work. | Only intended files staged. | Accidental staging or collateral damage. | Yes |

## P0 verification command set

```bash
python scripts/topology_doctor.py --planning-lock --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --plan-evidence docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md --json
python scripts/topology_doctor.py --work-record --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --work-record-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md --json
python scripts/topology_doctor.py --change-receipts --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --receipt-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --json
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --context-budget --json
python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml --json
git diff --cached --check
git status --short
```

## P1 verification command set

```bash
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --strict --json
python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files architecture/topology.yaml architecture/topology_schema.yaml architecture/map_maintenance.yaml architecture/context_budget.yaml architecture/artifact_lifecycle.yaml docs/archive_registry.md docs/README.md docs/AGENTS.md docs/operations/current_state.md docs/operations/AGENTS.md scripts/topology_doctor_map_maintenance.py scripts/topology_doctor_registry_checks.py tests/test_topology_doctor.py --json
pytest -q tests/test_topology_doctor.py -k "docs or registry or current_state or map_maintenance"
git diff --cached --check
```

## P2 verification command set

```bash
python scripts/topology_doctor.py --code-review-graph-status --json
python scripts/topology_doctor.py --context-packs --json
python -m py_compile scripts/code_review_graph_mcp_readonly.py scripts/topology_doctor.py scripts/topology_doctor_cli.py scripts/topology_doctor_code_review_graph.py scripts/topology_doctor_context_pack.py
pytest -q tests/test_topology_doctor.py -k "code_review_graph or context_pack"
git diff --cached --check
```

## P3 verification command set

```bash
python scripts/topology_doctor.py --history-lore --json
python scripts/topology_doctor.py --docs --json
pytest -q tests/test_topology_doctor.py -k "history or docs or archive"
git diff --cached --check
```
