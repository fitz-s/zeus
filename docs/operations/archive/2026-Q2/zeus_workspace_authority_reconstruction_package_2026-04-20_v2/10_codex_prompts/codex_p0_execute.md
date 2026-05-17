# Codex P0 Execute Prompt

You are local Codex operating inside the Zeus repo on branch `data-improve`.
Your task is to execute **P0 — Online Boot Surface Realignment** from the attached reconstruction package.

## Mission

Make the online-visible boot surfaces truthful without changing runtime behavior, graph binaries, archive bodies, or source code.

## Read first

1. `16_apply_order.md`
2. `00_executive_ruling.md`
3. `01_mental_model.md`
4. `02_authority_order_rewrite.md`
5. `08_patch_blueprints/p0_patch_blueprint.md`
6. `09_validation_matrix.md`

## Allowed files

- AGENTS.md
- workspace_map.md
- docs/README.md
- docs/AGENTS.md
- docs/archive_registry.md
- docs/operations/AGENTS.md
- docs/operations/current_state.md
- architecture/topology.yaml
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json

## Forbidden files

- src/**
- tests/**
- scripts/**
- docs/authority/**
- docs/archives/**
- state/**
- raw/**
- .omx/**
- .omc/**
- .code-review-graph/graph.db
- architecture/** except architecture/topology.yaml

## Non-negotiable rules

- Preserve unrelated dirty work.
- Do not use destructive git commands.
- Do not use `git add -A`.
- Do not widen into source, tests, scripts, runtime DBs, or archive bodies.
- Do not make Code Review Graph authority.
- Do not make archives default-read.

## Expected implementation moves

- rewrite root `AGENTS.md`
- rewrite `workspace_map.md`
- rewrite `docs/README.md` and `docs/AGENTS.md`
- create `docs/archive_registry.md`
- slim `docs/operations/current_state.md`
- tighten `docs/operations/AGENTS.md`
- minimally update `architecture/topology.yaml`
- create/update packet docs under `docs/operations/task_2026-04-20_workspace_authority_reconstruction/`

## Validation

Run these commands exactly or with equivalent `--changed-files` formatting if local CLI syntax requires quoting:

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

## Staging

Stage only the allowed files.
Leave unrelated dirty work unstaged.
Show a staged diff summary before proposing commit.

## Output required from you

1. concise summary of edits made
2. validation results
3. staged file list
4. any local deviations from the package, labeled `LOCAL_ADAPTATION`
5. proposed commit message using the package's Lore Commit Protocol text
