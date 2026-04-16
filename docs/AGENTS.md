# docs AGENTS

Documentation root. Active subdirectories plus archives.

## Design principle

Flat mesh architecture. Each subdirectory contains only files actively referenced by the mesh network (rooted at root `AGENTS.md`). Everything historical lives in `archives/`. This keeps agent context loading fast — agents follow links, not directories.

## Navigation

Read `README.md` in this directory for the docs index with full folder descriptions.

## File registry

| Item | Purpose |
|------|---------|
| `README.md` | Docs index — folder descriptions, naming rules, active doc list |
| `known_gaps.md` | Active operational gap register (D1-D6 cross-layer epistemic gaps) |
| `zeus-architecture-deep-map.md` | Generated deep refactor/reference map; evidence only, not authority |
| `authority/` | Current architecture law + delivery law + packet/autonomy/boundary governance → `authority/AGENTS.md` |
| `reference/` | Domain model, repo orientation, data inventory, quantitative research → `reference/AGENTS.md` |
| `operations/` | Live control-entry pointer + active work packets → `operations/AGENTS.md` |
| `runbooks/` | Operator runbooks → `runbooks/AGENTS.md` |
| `reports/` | Generated diagnostic reports from declared writers only; evidence only → `reports/AGENTS.md` |
| `to-do-list/` | Active checklist workbooks and audit queues; not authority → `to-do-list/AGENTS.md` |
| `artifacts/` | Active evidence artifacts and workbooks; not authority → `artifacts/AGENTS.md` |
| `archives/` | Historical only — never active authority → `archives/AGENTS.md` |

## Rules

- New active docs go in `authority/`, `reference/`, `operations/`, `runbooks/`, `to-do-list/`, or `artifacts/` — never directly in `docs/`
- Generated diagnostic reports from declared writers may go in `reports/`; it is not a general authoring surface
- `known_gaps.md` and `zeus-architecture-deep-map.md` are the top-level exceptions
- Everything that is no longer active law → `archives/`
