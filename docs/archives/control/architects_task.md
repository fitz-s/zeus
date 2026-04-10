> Historical archive surface. No longer active control authority; use `CURRENT_STATE.md` and the current work packet for live control entry.

# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex GOV-PACKET-ENTRY-CONTROL-SURFACE freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `GOV-PACKET-ENTRY-CONTROL-SURFACE`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Archive the legacy root/architects ledgers as active control surfaces and make the current work packet the live control entry surface.

## Allowed files

- `work_packets/GOV-PACKET-ENTRY-CONTROL-SURFACE.md`
- `AGENTS.md`
- `architecture/self_check/authority_index.md`
- `docs/README.md`
- `WORKSPACE_MAP.md`
- `root_progress.md`
- `root_task.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `docs/archives/**`
- `CURRENT_STATE.md`

## Forbidden files

- `docs/governance/**`
- `architecture/kernel_manifest.yaml`
- `architecture/invariants.yaml`
- `architecture/zones.yaml`
- `architecture/negative_constraints.yaml`
- `docs/zeus_FINAL_spec.md`
- `docs/architecture/zeus_durable_architecture_spec.md`
- `docs/known_gaps.md`
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
- no unrelated docs cleanup beyond control-surface replacement/archive
- no new long-lived ledger file unless proven unavoidable
- no team runtime launch

## Current blocker state

- the user has explicitly directed that the `root_*` and `architects_*` ledgers should no longer remain active
- fresh reference scans show those ledger paths are still heavily referenced across work packets and docs
- a replacement live control entry surface must be defined before archive moves can proceed safely

## Immediate checklist

- [x] `GOV-PACKET-ENTRY-CONTROL-SURFACE` frozen
- [ ] replacement live control entry surface defined
- [ ] root/architects ledgers demoted or archived
- [ ] top routing files updated to the replacement surface
- [ ] reference scans clean enough for review

## Next required action

1. Use the current work packet as the replacement live control entry surface.
2. Update routing files and archive/demote the superseded ledgers.
3. Verify references before review.
