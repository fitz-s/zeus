# Codex P3 Execute Prompt

Execute **P3 — Historical Compression and Residual Hygiene** only after P0 is stable.

## Read first

1. `05_archives_policy.md`
2. `06_topology_debt_triage.md`
3. `07_execution_packets.md`
4. `08_patch_blueprints/p3_patch_blueprint.md`

## Allowed files

- workspace_map.md
- docs/README.md
- docs/AGENTS.md
- docs/archive_registry.md
- architecture/history_lore.yaml
- architecture/context_budget.yaml
- tests/test_topology_doctor.py
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json

## Forbidden files

- src/**
- scripts/** except tests needed for lore routing
- .code-review-graph/graph.db
- docs/archives/** bodies
- state/**
- raw/**
- .omx/**
- .omc/**

## Mission

Improve the visible historical layer without re-ingesting archives wholesale.

## Rules

- treat every archive-derived claim as `[Archive evidence]`
- do not copy raw archive bodies into active docs
- sanitize before promotion
- keep history compressed and sparse

## Validation

```bash
python scripts/topology_doctor.py --history-lore --json
python scripts/topology_doctor.py --docs --json
pytest -q tests/test_topology_doctor.py -k "history or docs or archive"
git diff --cached --check
```

## Output required

- which historical lessons were promoted and why
- what remained archive-only and why
- validation results
- staged diff summary
- proposed Lore Commit Protocol message
