# Task Packet — Execution-State Truth Upgrade

## Objective

Prepare downstream implementation of the Execution-State Truth Re-Architecture by starting with a narrow P0 hardening packet, then progressing through P1/P2/P3 only after each phase closes.

## Required authority reads

1. `AGENTS.md`
2. `workspace_map.md`
3. `docs/operations/AGENTS.md`
4. `docs/operations/current_state.md`
5. Scoped routers for any source package touched:
   - `src/engine/AGENTS.md`
   - `src/execution/AGENTS.md`
   - `src/state/AGENTS.md`
   - `src/riskguard/AGENTS.md`
   - `src/data/AGENTS.md`
   - `tests/AGENTS.md`
6. Run topology navigation for the exact changed files before editing.

## Single task objective for the first implementation packet

Implement **P0 hardening only**:

- degraded export cannot be `VERIFIED`
- live entries remain blocked when execution authority is degraded/unknown
- V2 preflight failure blocks live order placement
- stale authority tests/comments are corrected or demoted
- direct live placement outside the approved boundary is statically guarded

## Likely files for P0

- `src/state/portfolio.py`
- `src/engine/cycle_runner.py`
- `src/execution/executor.py`
- `src/data/polymarket_client.py`
- `architecture/invariants.yaml`
- `architecture/negative_constraints.yaml`
- current AST/static-rule surface
- targeted tests under `tests/`

## Likely files for later phases

P1/P2 may touch:

- `src/execution/command_bus.py`
- `src/execution/command_recovery.py`
- `src/state/venue_command_repo.py`
- `src/state/authority_state.py`
- `src/state/db.py`
- `src/engine/cycle_runtime.py`
- `src/execution/executor.py`
- `src/state/chain_state.py`
- `src/state/chain_reconciliation.py`
- `src/riskguard/riskguard.py`
- observability/status surfaces

P3 may touch evaluator/market-selection/family-budget files only after P1/P2 close.

## Invariants that must not move

- DB/event truth outranks projections.
- `positions.json` is projection only.
- No side effect before persisted command once P1 starts.
- Unknown truth blocks new entry.
- RED must de-risk through authoritative work.
- Gateway-only placement is mandatory.
- Preserve unrelated dirty work.

## Not-now list

- no Bayesian/model upgrade
- no queue-position/impact simulator
- no broad market expansion
- no automated quarantine self-heal
- no unrestricted live entry
- no P3 alpha/eligibility work before P1/P2 truth closes

## Verification required by phase

Use `verification_plan.md`. P0 must include focused tests for degraded export, V2 preflight, entry blocking, stale artifact cleanup, and direct-placement guard. P1/P2 must include crash/replay drills.

## Rollback note

Rollback by narrowing runtime behavior: `NO_NEW_ENTRIES`, `EXIT_ONLY`, or recovery-only. Do not drop command journals, delete forensic rows, or reset unrelated work.

## Stop conditions

- Scope crosses phase boundary.
- New DB schema is needed during P0.
- Final command grammar is unresolved during P1.
- Lifecycle enum changes are needed during P2 without architecture approval.
- External V2 facts cannot be verified against official documentation.
