# Goal

Maximize settlement-graded net captured alpha rate in zero-sum Polymarket weather trading. For every newly committed causal weather fact, Zeus must complete the economically correct affected action before the market reprices. Work completed after the edge reverses has no alpha value, even when it improves average latency, component throughput, process liveness, or healthcheck status.

The complete race is `source available -> ingest commit -> affected-scope probability update -> current executable book -> zero-sum decision -> risk/sizing -> submit -> fill -> durable settlement/reconciliation -> settlement grade`. Optimize this end to end. A change that does not shorten an active alpha clock, increase executable capture probability, or reduce failure cost is outside the goal.

## Alpha Clocks

- **Deterministic observation reversal:** a newly committed measurement or absorbing/extreme fact can drive a held outcome toward 0 and a sibling outcome toward 1 in seconds or milliseconds. Its affected held-position SELL and exact complementary BUY preempt forecast discovery, maintenance, and unrelated work. The post-commit target is sub-second held-risk reduction and the lowest bounded latency possible for new risk, subject to fresh venue and risk authority.
- **Forecast reversal:** a new forecast issue may expose a target-bin repricing window of roughly hundreds of seconds. Source-issue detection and ingest commit must directly fan out only to affected city x metric x target-date families; those families must reach a fresh BUY/SELL/HOLD/CASH decision inside a small fraction of the measured repricing window.
- **Maintenance and recovery:** work without an expiring executable edge runs behind both money lanes unless it is required to preserve safety or complete an already-started external side effect.

Scheduling is not FIFO. Ready work is ranked by expected settlement-graded net alpha lost per second of delay, constrained by risk and authority. A newer authoritative fact supersedes stale queued computation for the same scope. Zero live orders when no positive executable edge exists is correct; failing to process a qualified opportunity before repricing is the throughput failure.

## First Principles

- Recompute and reload only state changed by the causal fact. Prefer event-driven incremental state over polling, full-universe sweeps, historical reconstruction, repeated per-row SQL, or repeated per-tick truth construction.
- Keep databases on the durability, audit, replay, recovery, and reconciliation plane. No unrelated DB read, writer wait, checkpoint, projection replay, metadata discovery, retry sleep, or report work may occupy the steady-state decision path.
- Batch and vectorize necessary work, reuse one immutable versioned fact per decision, and remove semantic or error-handling work that cannot change the action or protect capital.
- BUY, SELL, HOLD, and CASH are one zero-sum economic comparison. Actuation priority follows alpha expiry, not module order or historical queue age.
- Fresh contract, source, probability, position, capital, book, risk, and submit authority remain mandatory. Speed never authorizes stale evidence, guessed truth, or weakened fail-closed gates.

## Failure Isolation

- A stalled source, city, family, condition, event, candidate, metadata request, DB operation, lock, model, or venue command may delay only the facts and actions that depend on it. Independent scopes continue and prove forward progress.
- Every pre-side-effect unit carries one absolute monotonic deadline and a named cancellation boundary through ingest, update, decision, sizing, risk, and pre-submit validation. Timeout, retry, and backoff remain local; they cannot create global sleep, queue-head blocking, whole-universe replay, or cross-cycle duplicate work.
- Once a venue side effect may have begun, the command leaves the deadline-bound discovery lane and enters an idempotent must-complete settlement/reconciliation lane. That lane cannot hold the global decision reactor.
- Global serialization is permitted only where one canonical ordering is mathematically or durably necessary. Its critical section contains no network I/O, model computation, metadata discovery, retry sleep, or unrelated DB scan.

## Acceptance

Each optimization must be justified by current runtime evidence and replayable causal measurement. Record source availability, ingest commit, state application, decision, fresh-book observation, submit, acknowledgement, fill, and durable settlement timestamps. Report p50/p95/p99/p99.9, queue age, snapshot age, book age at submit, deadline overruns, lock/SQL/network work, stale-decision rejection, unaffected-lane progress, fill probability, executable edge, and realized PnL.

Lower median latency without bounded tails and isolation is not an efficiency gain. The final score is the decision-certificate x settlement join (`settlement_skill_attribution`; SKILL_WIN is evidence and LUCKY_WIN is a miss), net of fees, slippage, failed captures, adverse selection, and latency-induced edge loss.

Current work state lives in `docs/operations/current/plans/hourly_capital_gains_improvement_loop.md` and `plans/INDEX.md`. This file is the persistent objective, not a runtime diary or blocker log.
