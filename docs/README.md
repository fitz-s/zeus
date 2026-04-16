# Docs Index

All docs use `lower_snake_case.md` naming unless a date prefix is required.

## Design principle

**Flat mesh architecture.** Active subdirectories plus archives. Each directory
contains only files that are actively referenced by the mesh network (rooted at
`AGENTS.md`). Everything else lives in `docs/archives/`. This keeps agent
context loading fast — agents follow links, not directories.

## Folders

| Directory | Purpose | Files |
|-----------|---------|-------|
| `authority/` | Current architecture + current delivery law + packet/autonomy/boundary governance | 7 |
| `reference/` | Domain model, technical orientation, quantitative research, data inventory, strategy, math specification notes | 8 |
| `operations/` | Live control-entry pointer + current work packets | 5 |
| `runbooks/` | Operator runbooks | 2 |
| `reports/` | Generated diagnostic reports from declared writers only; evidence only and not a default route | 1 |
| `to-do-list/` | Active checklist workbooks and audit queues, never authority | 1 |
| `artifacts/` | Active evidence artifacts and inventories, never authority | 1 |
| `archives/` | **Everything historical** — audits, findings, old specs, old governance, overlay packages, handoffs, etc. | many |

## Active top-level docs

- `../AGENTS.md` — root operating brief (read first, always)
- `reference/zeus_domain_model.md` — "Zeus in 5 minutes" domain model with WHY explanations
- `authority/zeus_current_architecture.md` — active architecture law (truth surfaces, lifecycle, risk, zones)
- `authority/zeus_current_delivery.md` — active delivery law (authority order, planning lock, packet routing)
- `known_gaps.md` — active operational gap register
- `zeus-architecture-deep-map.md` — generated deep refactor/reference map; evidence only
- `operations/current_state.md` — single live control-entry pointer; current branch / packet truth
- `runbooks/live-operation.md` — day-to-day live daemon operation runbook
- `../workspace_map.md` — directory guide and file placement rules (repo root)

## Archives

`archives/**` — historical only; never principal authority. Subdirectories include:
`architecture/`, `artifacts/`, `audits/`, `control/`, `designs/`, `findings/`,
`governance/`, `handoffs/`, `investigations/`, `math`, `memory/`,
`migration/`, `overlay_packages/`, `plans/`, `reality_crisis/`, `reference/`,
`reports/`, `research/`, `results/`, `rollout/`, `sessions/`, `specs/`,
`traces/`, `work_packets/`.

## Naming Rules (Mandatory)

- All `.md` files: `lower_snake_case.md` (exceptions: `AGENTS.md`, `README.md`)
- **New files**: Use `task_YYYY-MM-DD_name.md` format — task prefix identifies the program/packet, date is creation date
- No single-word prefixes: ❌ `data_plan.md` → ✅ `datafix_2026-04-10_improvement_plan.md`
- No generic names outside active task folders: ❌ `plan.md`, `progress.md` → ✅ `<task>_<date>_<topic>.md`
- No spaces in filenames or directory names
- Existing files keep current names (no retroactive renames)
- Date prefixes only for time-bound reports
