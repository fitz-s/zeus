# Docs Index

All docs use `lower_snake_case.md` naming unless a date prefix is required.

## Design principle

**Flat mesh architecture.** Three active subdirectories plus archives. Each directory contains only files that are actively referenced by the mesh network (rooted at `AGENTS.md`). Everything else lives in `docs/archives/`. This keeps agent context loading fast — agents follow links, not directories.

## Folders

| Directory | Purpose | Files |
|-----------|---------|-------|
| `authority/` | Architecture specs + governance — constitutions, packet discipline, autonomy gates, boundary law, decision register, architecture specs | 13 |
| `reference/` | Domain model, technical orientation, workspace map, quantitative research, data inventory, strategy | 10 |
| `operations/` | Live control-entry pointer + current work packets | 3 |
| `archives/` | **Everything historical** — audits, findings, old specs, old governance, overlay packages, handoffs, etc. | many |

## Active top-level docs

- `../AGENTS.md` — root operating brief (read first, always)
- `reference/zeus_domain_model.md` — "Zeus in 5 minutes" domain model with WHY explanations
- `authority/zeus_durable_architecture_spec.md` — architecture spec (DB schema, event spine, truth surfaces)
- `authority/target_state_spec.md` — target-state spec (P9-P11, endgame clause)
- `known_gaps.md` — active operational gap register
- `operations/current_state.md` — single live control-entry pointer
- `reference/workspace_map.md` — directory guide and file placement rules

## Archives

`archives/**` — historical only; never principal authority. Subdirectories include: `architecture/`, `artifacts/`, `audits/`, `control/`, `designs/`, `findings/`, `governance/`, `handoffs/`, `investigations/`, `math/`, `memory/`, `migration/`, `overlay_packages/`, `plans/`, `reality_crisis/`, `reference/`, `reports/`, `research/`, `results/`, `rollout/`, `sessions/`, `specs/`, `traces/`, `work_packets/`.

## Naming Rules (Mandatory)

- All `.md` files: `lower_snake_case.md` (exceptions: `AGENTS.md`, `README.md`)
- No generic names: ❌ `plan.md`, `progress.md` → ✅ `<topic>_plan.md`
- No spaces in filenames or directory names
- Date prefixes only for time-bound reports
