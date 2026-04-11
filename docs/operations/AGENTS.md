# docs/operations AGENTS

Live control surface — current state pointer, active work packets. This is where agents find what's in progress and what's next.

## File registry

| File | Purpose |
|------|---------|
| `current_state.md` | Single live control-entry pointer — current branch, active packet, what to read |
| `GOV-FAST-ARCHIVE-SWEEP.md` | Work packet: fast archive sweep (docs consolidation) |
| `GOV-TOP-LAW-EXPANSION.md` | Work packet: top-law expansion (AGENTS.md enrichment) |

## Rules

- `current_state.md` is always current — update when switching packets/branches
- Completed work packets move to `docs/archives/work_packets/`
- New packets use `task_YYYY-MM-DD_name.md` naming
