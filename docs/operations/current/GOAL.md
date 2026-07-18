# Goal

Maximize settlement-graded net captured alpha by acting on new causal weather truth before the market reprices. Process liveness, throughput, a green healthcheck, an order, or a merged PR is not completion. Average cycle speed is not the target; the target is the elapsed time from one newly committed causal fact to its economically correct affected action.

The optimized chain is `source available -> ingest commit -> current q -> executable book -> risk -> submit -> fill -> settlement grade`. Work that does not shorten this chain, increase executable edge capture, or reduce its failure cost is outside the objective.

Two alpha clocks govern priority:

- Forecast reversal: when a new forecast materially moves one target-bin probability before the book reacts, recompute only affected families and reach a current executable BUY/SELL/HOLD decision inside a small fraction of the measured market-repricing window.
- Deterministic observation reversal: when a newly committed absorbing/extreme fact drives one held outcome to zero or a sibling outcome to one, prioritize the affected held-position SELL and exact complementary BUY over unrelated discovery or full-universe rebuilding. The internal post-commit target is sub-second for held-risk reduction and bounded low seconds for new risk, subject to current venue and risk authority.

Fault containment is a co-equal objective, because unrelated work consuming an expiring alpha clock is an economic loss:

- A stalled source, city, family, event, candidate, metadata request, DB query, lock, or venue command may delay only the state and action that depend on it. Independent families and already-authoritative actions continue.
- Every pre-submit unit has a bounded monotonic deadline and a named cancellation boundary. Timeout, retry, and backoff remain local; they must not create global sleep, queue-head blocking, repeated whole-universe work, or cross-cycle duplicate work.
- After an external side effect may have started, that command leaves the deadline-bound discovery lane and enters idempotent must-complete settlement/reconciliation. It must not hold the global decision reactor.
- Global serialization is allowed only where one canonical ordering is mathematically or durably required. Its critical section contains no network I/O, model computation, metadata discovery, retry sleep, or unrelated DB scan.

Steady-state money-path constraints:

- No unrelated full-table reconstruction, synchronous metadata discovery, or repeated lock wait may sit between a causal fact and its affected action.
- BUY, SELL, HOLD, and CASH remain one zero-sum economic comparison, but actuation priority follows alpha expiry: current deterministic exits outrank forecast entries and maintenance.
- Missing current probability, book, position, capital, settlement, or submit authority fails closed for the affected action. One unavailable family cannot erase independent executable opportunities.
- Each optimization must report causal-fact queue age, fact-to-decision, decision-to-submit, deadline overrun, and unaffected-lane progress. A lower median without bounded tail latency and isolation is not an efficiency gain.
- The only score that counts is the decision-certificate x settlement join (`settlement_skill_attribution`; SKILL_WIN is evidence, LUCKY_WIN is a miss), including fees, slippage, failed captures, and latency-induced edge loss.

Current work state lives in `docs/operations/current/plans/hourly_capital_gains_improvement_loop.md` (forward journal) and `plans/INDEX.md`. This file carries no blocker narrative; read the journal for current evidence.
