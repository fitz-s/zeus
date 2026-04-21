# task_2026-04-15_data_math_operator_runbook

## Purpose

Run the Zeus data-math packet without confusing live authority, backtest evidence, and shadow diagnostics.

## Read order

1. `AGENTS.md`
2. `docs/authority/zeus_current_delivery.md`
3. `docs/authority/zeus_current_architecture.md`
4. `docs/operations/current_state.md`
5. `docs/authority/task_2026-04-15_data_math_delivery_constitution.md`
6. nearest scoped `AGENTS.md`
7. targeted files and targeted tests

## What Codex may do automatically

- doc-only stale claim cleanup outside `docs/authority/**` if no authority semantics change
- narrow code patches that preserve existing contracts
- targeted relationship tests
- replay/backtest diagnostic reporting
- advisory shadow metric surfacing

## What must be human-gated

- any change to `docs/authority/**`
- any change to `AGENTS.md`
- schema migration
- live cutover
- shadow -> live blocker promotion
- historical rebuild execution
- permanent control-plane behavior change

## Execution order

1. Run topology and registry checks.
2. Apply stale-authority cleanup ticket.
3. Apply calibration-lineage ticket.
4. Apply shadow advisory-surface ticket.
5. Apply replay honesty ticket.
6. Apply DST preflight/certification-boundary ticket.
7. Re-run targeted tests after each ticket.
8. Keep each rollback unit small.

## Stop immediately if

- cross-zone spill appears,
- hidden schema dependence appears,
- replay starts looking promotable without governance,
- a patch needs historical backfill to make truth coherent,
- targeted law tests fail for reasons not understood.

## First places to inspect on failure

1. `architecture/test_topology.yaml`
2. `architecture/script_manifest.yaml`
3. `src/calibration/store.py`
4. `src/execution/harvester.py`
5. `src/engine/replay.py`
6. `docs/operations/known_gaps.md`

## Closeout

Close the packet only after:
- targeted tests pass,
- replay remains diagnostic_non_promotion,
- shadow metrics remain advisory,
- no active stale authority claims remain in touched surfaces,
- rollback notes are written for each code ticket.
