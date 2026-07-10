# runtime_claim_contention -- Plan

Date: 2026-07-10
Branch: `agent/runtime-throughput-first-principles`
Status: active

## Background

The loaded daemon exposed repeated world-writer contention: eight claim bounces
spent about 24 seconds on the loaded SHA. The same shape on current HEAD spends
6.035 seconds because every event independently waits the full 750 ms claim
budget even though all events share one world-write authority.

This slice sits at `execution` admission. It consumes the process-local world
writer mutex and SQLite claim authority before any venue side effect. On
re-decision, a bounced event remains pending exactly as before.

## Scope

_See sibling scope.yaml for machine-readable scope._

## Deliverables
- Allow one normal claim wait per contention episode, then probe later events
  nonblocking within the same cycle.
- Restore the normal wait immediately after any successful claim probe.
- Preserve pending/retry counts, transaction rollback, risk gates, and zero
  venue side effects on a claim miss.
- Register a deterministic claim-storm relationship antibody.

## Verification
- Deterministic mutex timeout-sequence tests; no timing-only assertion.
- Existing claim-storm and reactor-cycle tests.
- Before/after eight-event contention microbenchmark.
- Independent runtime-risk review before commit.

## Authority Boundaries

- Invariants referenced: INV-28 submit persistence ordering remains unchanged;
  INV-05 risk behavior remains unchanged.
- `architecture/test_topology.yaml` changes only test trust metadata. It does
  not amend an invariant, schema, lifecycle, or runtime authority.
- No live DB write, venue action, config change, restart, or deployment.
