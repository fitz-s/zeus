# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex mainline coordination pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P1.7I-CHAIN-ONLY-QUARANTINE-EXCLUSION-FOLLOW-THROUGH`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Make the P1.7H exclusion explicit and non-silent by preserving the quarantined runtime object and emitting an explicit exclusion warning, without any canonical write, invented `strategy_key`, or widened governance/schema surface.

## Allowed files

- `work_packets/P1.7I-CHAIN-ONLY-QUARANTINE-EXCLUSION-FOLLOW-THROUGH.md`
- `src/state/chain_reconciliation.py`
- `tests/test_runtime_guards.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files
- `AGENTS.md`
- `migrations/**`
- `src/engine/**`
- `src/execution/**`
- `src/riskguard/**`
- `src/control/**`
- `src/supervisor_api/**`
- `src/state/db.py`
- `src/state/chronicler.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime cutover surfaces
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no canonical lifecycle write for chain-only quarantines
- no invented, inferred, or borrowed `strategy_key`
- no new attribution field / enum / schema surface / reader contract
- no broader reconciliation migration
- no cutover
- no team launch

## Current blocker state

- no human-decision blocker remains for this packet
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] define the exact exclusion boundary inside the touched runtime path
- [ ] preserve the quarantined runtime object and emit an explicit exclusion warning
- [ ] prove no canonical write or invented attribution is introduced
- [ ] run adversarial review before acceptance

## Next required action

1. Read the packet-required runtime/test surfaces.
2. Land the narrow exclusion-visibility behavior.
3. Verify with targeted tests and explicit adversarial review.
4. Keep out-of-scope dirt excluded from any commit.
