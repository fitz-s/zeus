# docs AGENTS

Documentation root. Four active subdirectories plus archives.

## Design principle

Flat mesh architecture. Each subdirectory contains only files actively referenced by the mesh network (rooted at root `AGENTS.md`). Everything historical lives in `archives/`. This keeps agent context loading fast — agents follow links, not directories.

## Navigation

Read `README.md` in this directory for the docs index with full folder descriptions.

## File registry

| Item | Purpose |
|------|---------|
| `README.md` | Docs index — folder descriptions, naming rules, active doc list |
| `known_gaps.md` | Active operational gap register (D1-D6 cross-layer epistemic gaps) |
| `authority/` | Current architecture law + delivery law + packet/autonomy/boundary governance → `authority/AGENTS.md` |
| `reference/` | Domain model, repo orientation, data inventory, quantitative research → `reference/AGENTS.md` |
| `operations/` | Live control-entry pointer + active work packets → `operations/AGENTS.md` |
| `runbooks/` | Operator runbooks → `runbooks/AGENTS.md` |
| `archives/` | Historical only — never active authority → `archives/AGENTS.md` |

## Rules

- New active docs go in `authority/`, `reference/`, `operations/`, or `runbooks/` — never directly in `docs/`
- `known_gaps.md` is the exception (top-level because it spans all zones)
- Everything that is no longer active law → `archives/`
- See root `AGENTS.md` §8 for naming conventions
