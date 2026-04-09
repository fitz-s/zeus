# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE post-close sync`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE`
- State: `POST_CLOSE_PASSED / AUTHORITY_BASELINE_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Amend the top authority and orientation surfaces so they reflect the current truth-mainline, current active control surfaces, and the new archive boundary.

## Allowed files

- `work_packets/GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `AGENTS.md`
- `architecture/self_check/authority_index.md`
- `docs/README.md`
- `WORKSPACE_MAP.md`
- `root_progress.md`
- `root_task.md`
- `docs/known_gaps.md`
- `docs/zeus_FINAL_spec.md`
- `docs/architecture/zeus_durable_architecture_spec.md`

## Forbidden files

- `docs/governance/**`
- `architecture/**`
- `migrations/**`
- `src/**`
- `tests/**`
- `scripts/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no runtime code changes
- no launchd/service ownership work
- no broad constitution rewrite
- no additional archive migration pass inside this packet
- no team runtime launch

## Current blocker state

- packet boundary is post-close passed
- active-vs-archive and root-vs-architects role separation is now explicit in the amended top routing surfaces
- any further file cleanup should treat this packet as the new routing baseline rather than reopening authority ambiguity

## Immediate checklist

- [x] `GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE` frozen
- [x] authority mismatch matrix recorded
- [x] active-vs-archive boundary clarified in top routing surfaces
- [x] root-vs-architects control roles clarified
- [x] stale active path references removed from top orientation files

## Next required action

1. Use this authority baseline for the next cleanup/governance packet.
2. Keep this packet closed unless a new contradiction reopens it.
