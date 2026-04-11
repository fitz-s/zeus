# docs/operations AGENTS

Live control surface — current state pointer, active work packets. This is where agents find what's in progress and what's next.

## File registry

| File | Purpose |
|------|---------|
| `current_state.md` | Single live control-entry pointer — current branch, active packet, what to read |

## Packet lifecycle

1. **Active** → packet file lives here in `docs/operations/`; listed in this file registry; `current_state.md` points to it
2. **Completed** → remove from this directory, move to `docs/archives/<program>/` (program-named subfolder, e.g., `governance_doc_restructuring/`); remove from this file registry; update `current_state.md`
3. **No active packet** → `current_state.md` says so explicitly; this registry lists only `current_state.md`

Don't accumulate dead packets here. The live surface must reflect only what's actually in progress.

## Rules

- `current_state.md` is always current — update when switching packets/branches
- New packets use `task_YYYY-MM-DD_name.md` naming
- Archive to a program-named subfolder under `docs/archives/`, not the flat `work_packets/` dump
