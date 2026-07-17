# Goal

Maximize settlement-graded net captured alpha by acting on new causal weather truth before the market reprices. Process liveness, throughput, a green healthcheck, an order, or a merged PR is not completion.

The optimized chain is `source available -> ingest commit -> current q -> executable book -> risk -> submit -> fill -> settlement grade`. Work that does not shorten this chain, increase executable edge capture, or reduce its failure cost is outside the objective.

Two alpha clocks govern priority:

- Forecast reversal: recompute only affected families and reach a current executable decision inside a small fraction of the measured market-repricing window.
- Deterministic observation reversal: a newly committed absorbing/extreme fact must prioritize held-position SELL and exact complementary BUY over unrelated market discovery or full-universe rebuilding. The internal post-commit target is sub-second for a held-position action and bounded low seconds for a new-risk action, subject to current venue and risk authority.

Steady-state money-path constraints:

- No unrelated full-table reconstruction, synchronous metadata discovery, or repeated lock wait may sit between a causal fact and its affected action.
- BUY, SELL, HOLD, and CASH remain one zero-sum economic comparison, but actuation priority follows alpha expiry: current deterministic exits outrank forecast entries and maintenance.
- Missing current probability, book, position, capital, settlement, or submit authority fails closed for the affected action. One unavailable family cannot erase independent executable opportunities.
- The only score that counts is the decision-certificate x settlement join (`settlement_skill_attribution`; SKILL_WIN is evidence, LUCKY_WIN is a miss), including fees, slippage, failed captures, and latency-induced edge loss.

Current work state lives in `docs/operations/current/plans/hourly_capital_gains_improvement_loop.md` (forward journal) and `plans/INDEX.md`. This file carries no blocker narrative; read the journal for current evidence.
