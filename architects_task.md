# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-07 America/Chicago`
- Last updated by: `Codex BUG-MONITOR-SHARED-CONNECTION-REPAIR freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-MONITOR-SHARED-CONNECTION-REPAIR`
- State: `FROZEN / IMPLEMENTATION_VERIFIED`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Repair the monitoring / exit-context seam so runtime can read trade truth plus shared world truth through an explicit attached connection instead of the legacy monolithic seam.

## Allowed files

- `work_packets/BUG-MONITOR-SHARED-CONNECTION-REPAIR.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/engine/cycle_runner.py`
- `src/engine/cycle_runtime.py`
- `src/engine/monitor_refresh.py`
- `src/state/db.py`
- `tests/test_runtime_guards.py`
- `tests/test_live_safety_invariants.py`
- `tests/test_pnl_flow_and_audit.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/control/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `migrations/**`
- `tests/test_architecture_contracts.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no RiskGuard packet work in this packet
- no migration-script execution or daemon cutover claim
- no bankroll semantics redesign
- no team runtime launch

## Current blocker state

- current runtime monitoring seam has been repaired and targeted tests passed
- pre-close critic + verifier review still needs to run before local acceptance
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] `BUG-MONITOR-SHARED-CONNECTION-REPAIR` frozen
- [ ] architecture/code-review/test map captured for the packet
- [x] runtime seam repaired in code
- [x] targeted tests pass
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] packet accepted locally
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Map the packet into bounded architecture / code-review / adversarial-test slices.
2. Repair the runtime monitoring connection seam and targeted test contract only.
3. Do not widen into migration, retirement, or bankroll work without a new packet.
