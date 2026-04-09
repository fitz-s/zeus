# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-TRAILING-LOSS-REFERENCE-FRESHNESS-WINDOW acceptance sync`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-TRAILING-LOSS-REFERENCE-FRESHNESS-WINDOW`
- State: `ACCEPTED_LOCAL / POST_CLOSE_PENDING`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Make trailing 24h/7d loss use a trustworthy near-cutoff reference row instead of silently falling back to an arbitrarily older consistent slice.

## Allowed files

- `work_packets/BUG-TRAILING-LOSS-REFERENCE-FRESHNESS-WINDOW.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/riskguard/riskguard.py`
- `tests/test_riskguard.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/state/**`
- `src/observability/**`
- `src/control/**`
- `src/supervisor_api/**`
- `migrations/**`
- `src/execution/**`
- `src/engine/**`
- `tests/test_architecture_contracts.py`
- `tests/test_truth_surface_health.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_runtime_guards.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no runtime artifact refresh in this packet
- no `src/observability/status_summary.py` parity work yet
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- packet-bounded trailing-loss evidence now passes, but post-close critic + verifier are still required before the next packet may freeze
- fresh direct probe now shows 24h reference degradation (`insufficient_history`) rather than accepting a row `13.73h` older than the cutoff
- runtime artifact refresh remains explicit follow-up work and must be handled by a new packet instead of widening this accepted boundary

## Immediate checklist

- [x] `BUG-TRAILING-LOSS-REFERENCE-FRESHNESS-WINDOW` frozen
- [x] too-old trailing-loss reference reproduced with packet-bounded evidence
- [x] trailing loss refuses references outside the allowed freshness window
- [x] packet-bounded trailing-loss tests pass
- [x] runtime artifact refresh stays explicit follow-up work

## Next required action

1. Run post-close critic + verifier on the accepted trailing-loss boundary.
2. Freeze the next bounded packet instead of widening this one.
