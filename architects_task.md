# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P5 family closeout re-pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `P5 FAMILY COMPLETE / AWAITING NEXT PHASE FREEZE`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

No live packet. The P5 family is complete under current repo truth.

## Allowed files

- future packet surfaces only after a new non-P5 freeze

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/control/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/supervisor_api/**`
- `tests/test_architecture_contracts.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_replay_time_provenance.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no next-phase work without a new frozen packet
- no schema changes
- no dashboard/observability/control-plane widening
- no cutover
- no team launch

## Current blocker state

- no active blocker inside the completed P5 family boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P5.1 packet frozen
- [x] lifecycle kernel surface installed
- [x] targeted lifecycle-kernel architecture tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.1 accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.2 packet frozen
- [x] touched canonical builder fold legality implemented
- [x] targeted lifecycle fold-legality architecture tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.2 accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.3A packet frozen
- [x] touched exit-lifecycle hotspot routed through lifecycle kernel
- [x] targeted exit-lifecycle hotspot tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.3A accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.3B packet frozen
- [x] touched day0 hotspot routed through lifecycle kernel
- [x] targeted day0 hotspot tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.3B accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.3C packet frozen
- [x] touched reconciliation hotspot routed through lifecycle kernel
- [x] targeted reconciliation hotspot tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.3C accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.3D packet frozen
- [x] touched terminal-state hotspot routed through lifecycle kernel
- [x] targeted terminal-hotspot tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.3D accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.3E packet frozen
- [x] touched entry hotspot family routed through lifecycle kernel
- [x] targeted entry-hotspot tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.3E accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.4 packet frozen
- [x] targeted quarantine-semantics proof installed
- [x] targeted quarantine-semantics tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.4 accepted and pushed
- [x] renewed missing-proof blocker identified
- [x] `quarantine_expired` exposure exclusion proof added
- [x] renewed post-close critic review passed
- [x] renewed post-close verifier review passed

## Next required action

1. Stop at the completed P5 family boundary.
2. If continuing later, freeze the next non-P5 packet before any implementation.
