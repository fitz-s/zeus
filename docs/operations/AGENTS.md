# docs/operations AGENTS

Live control surface — current state pointer, active work packets. This is where agents find what's in progress and what's next.

## File registry

| File | Purpose |
|------|---------|
| `current_state.md` | Single live control-entry pointer — current branch, active packet, what to read |
| `task_2026-04-13_topology_compiler_program.md` | Active topology compiler program packet tracker |
| `task_2026-04-13_remaining_repair_backlog.md` | Active backlog of remaining repair items after non-DB small-package loop; separates DB/rebuild dependencies from larger strategy/design packets |
| `task_2026-04-14_session_backlog.md` | End-of-session backlog snapshot from the calibration-refactor session (df13308/45745ba/854cf5d/ed13310); groups open tasks by unblocking condition |
| `task_2026-04-15_topology_enforcement_hardening/` | Active package for topology enforcement hardening: report-surface normalization, closeout compiler, route receipts, and evidence-budget telemetry |
| `task_2026-04-16_dual_track_metric_spine/` | Active dual-track metric spine refactor packet and evidence |
| `data_rebuild_plan.md` | Active upstream data-rebuild plan; do not execute from topology packets |
| `phase1live_2026-04-11_plan.md` | Phase 1 Live-Only Reorientation master plan; supporting runtime notes are not repo authority |

## Packet lifecycle

1. **Active** → packet file or directory lives here in `docs/operations/`; listed in this file registry; `current_state.md` points to it
2. **Completed** → remove from this directory, archive by git lineage under `docs/archives/work_packets/branches/<branch>/...`; remove from this file registry; update `current_state.md`
3. **No active packet** → `current_state.md` says so explicitly; this registry lists only `current_state.md`

Don't accumulate dead packets here. The live surface must reflect only what's actually in progress.

## Rules

- `current_state.md` is always current — update when switching packets/branches
- Non-trivial repo-changing work updates a short work record before closeout; prefer `work_log.md` inside the active task folder
- New single-file packets use `task_YYYY-MM-DD_name.md` naming
- Active multi-file solution packages use `task_YYYY-MM-DD_name/` under `docs/operations/`
- Archive completed work packets under `docs/archives/work_packets/branches/<branch>/<program_domain>/YYYY-MM-DD_slug/`
- Use `trees/<tree-name>/` instead of `branches/<branch>/` only when a package is tied to a persistent git worktree rather than a normal branch
- Keep complete solution packages atomic; do not split their internal `docs/`, `src/`, `scripts/`, `migrations/`, or `tests/` folders across archive categories
