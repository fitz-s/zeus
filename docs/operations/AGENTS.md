# docs/operations AGENTS

Live control surface — current state pointer, active work packets. This is where agents find what's in progress and what's next.

## File registry

| File | Purpose |
|------|---------|
| `current_state.md` | Single live control-entry pointer — current branch, active packet, what to read |
| `task_2026-04-13_topology_compiler_program.md` | Active topology compiler program packet tracker |
| `task_2026-04-13_remaining_repair_backlog.md` | Active backlog of remaining repair items after non-DB small-package loop; separates DB/rebuild dependencies from larger strategy/design packets |
| `data_rebuild_plan.md` | Active upstream data-rebuild plan; do not execute from topology packets |
| `phase1live_2026-04-11_plan.md` | Phase 1 Live-Only Reorientation master plan; supporting runtime notes are not repo authority |

## Excluded Active Package

`docs/data improve large pack/` is an active data-improvement package currently being read by other agents. Packet 3 must not move, rename, edit, archive, or normalize that package. It is excluded from docs-mode topology checks until a dedicated package-coordination packet handles it.

## Packet lifecycle

1. **Active** → packet file lives here in `docs/operations/`; listed in this file registry; `current_state.md` points to it
2. **Completed** → remove from this directory, move to `docs/archives/<program>/` (program-named subfolder, e.g., `governance_doc_restructuring/`); remove from this file registry; update `current_state.md`
3. **No active packet** → `current_state.md` says so explicitly; this registry lists only `current_state.md`

Don't accumulate dead packets here. The live surface must reflect only what's actually in progress.

## Rules

- `current_state.md` is always current — update when switching packets/branches
- New packets use `task_YYYY-MM-DD_name.md` naming
- Archive to a program-named subfolder under `docs/archives/`, not the flat `work_packets/` dump
