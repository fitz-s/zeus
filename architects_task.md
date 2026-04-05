# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7.2 post-close stop boundary`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `P7.2 COMPLETE / AWAITING PARITY-SUPPORTED NEXT FREEZE`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

No live packet. `P7.2-M2-PARITY-REPORTING` is complete and its post-close gate passed, but actual parity evidence does not yet support freezing a DB-first/cutover-prep packet.

## Allowed files

- future packet surfaces only after a new parity-supported freeze

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/**`
- `tests/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no DB-first/cutover-prep freeze until parity evidence supports it
- no ad hoc migration leap beyond the current reporting truth
- no team launch

## Current blocker state

- actual current-state parity output reports `staged_missing_canonical_tables`
- specifically: `position_current` is missing in the local repo state targeted by the parity report
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7.2 packet frozen
- [x] parity/reporting surface upgraded from placeholder output
- [x] targeted parity/reporting tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P7.2 accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Stop at the completed `P7.2` boundary.
2. Reassess parity evidence before freezing any later P7 packet.
