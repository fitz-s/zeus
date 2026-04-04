# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P5.4 repair pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P5.4-QUARANTINE-SEMANTICS-HARDENING`
- State: `ACCEPTED / PUSHED / RENEWED POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Reopened P5.4 to repair the missing explicit proof that `quarantine_expired` positions stay outside open/exposure semantics before honest P5 family closeout.

## Allowed files

- work_packets/P5.4-QUARANTINE-SEMANTICS-HARDENING.md
- tests/test_runtime_guards.py
- architects_progress.md
- architects_task.md
- architects_state_index.md

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
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

- no next-phase work until renewed P5.4 post-close gate passes
- no schema changes
- no dashboard/observability/control-plane widening
- no cutover
- no team launch

## Current blocker state

- renewed post-close gate is pending because `quarantine_expired` exposure exclusion proof was missing from the accepted P5.4 boundary
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
- [ ] `quarantine_expired` exposure exclusion proof added
- [ ] renewed post-close critic review passed
- [ ] renewed post-close verifier review passed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Commit the missing `quarantine_expired` exposure proof inside the reopened P5.4 boundary.
2. Rerun the renewed post-close critic + verifier on accepted `P5.4-QUARANTINE-SEMANTICS-HARDENING`.
3. Re-record P5 family closeout only after that renewed gate passes.
