# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE`
- State: `FROZEN / IMPLEMENTATION_READY`
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

- top routing/orientation surfaces still point at stale or ambiguous active paths
- current archive cleanup has moved historical material, but the highest guidance files do not yet encode that boundary clearly
- root-vs-architects control surface roles are still implied rather than explicitly harmonized

## Immediate checklist

- [x] `GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE` frozen
- [ ] authority mismatch matrix recorded
- [ ] active-vs-archive boundary clarified in top routing surfaces
- [ ] root-vs-architects control roles clarified
- [ ] stale active path references removed from top orientation files

## Next required action

1. Amend the bounded authority/orientation surfaces listed above.
2. Verify routing/reference scans and then run pre-close review.
