# Day0 Unlock Evaluation — 2026-06-11 ~12:05Z

<!-- Created: 2026-06-11 -->
<!-- Authority basis: operator directive 2026-06-11 ~12:04Z "做day0解锁测试评估".
     Bar (settings note, operator 2026-06-10): promotion to live day0 = separate operator
     decision on the comparator's settled evidence, >51% after-cost win-rate, ~150-270
     settled samples. Method: PURE READ-ONLY (mode=ro), snapshot while live trading runs. -->

## VERDICT: NOT UNLOCKABLE — and the binding constraint is EVIDENCE PRODUCTION, not waiting time.

Settled gradeable day0 samples: **0 of ~150 required**. Unchanged from the 08:25Z study
(docs/evidence/day0/2026-06-11_retired_day0_no_submit_scope_accuracy_profitability.md) — but today's
numbers sharpen WHERE the pipeline loses the evidence, in three stacked layers:

| Layer | Measured now (12:04Z) | Loss |
|---|---|---|
| Candidate-bearing receipts | 10 of 1864 total (0.5%); post-deploy (≥06-10T22:14Z): 6 of 546 (1.1%) | ~99% of day0 receipts are BARE scope-gate stamps (no direction/q/bin) |
| Fill-economics population | `hypothetical_fill_price`, `c_fee_adjusted`, `would_have_won` = **0%**, including on all 10 candidates | The fill-simulation half of the grading writer was never built (module docstring: "The fill-price half is a ..." — declared, not implemented) |
| Settlement join | 06-11 VERIFIED settlement_outcomes = **0** (Asia/Oceania local days ended; verification lags hours-days) | Even existing candidates cannot grade today |

Rate arithmetic: candidate production ≈ 6-10/day post remaining-day-q flip. Even with a
perfect grader, 150 settled samples ≈ **15-25 days**. With the current 0%-economics layer,
the clock has not started at all.

## Production-wiring discrepancy (flag for fix, not fixed here)

The #12 enrichment ("full decision content at the day0 submit boundary",
268d09d535, test-pinned) is NOT reflected in production receipts: since the
remaining-day-q flip, 532 of 546 day0 receipts are bare EXECUTOR_EXPRESSIBILITY rows and
`edli_no_submit_receipts` held ZERO day0 rows at the 08:25Z audit. Tests pass, production
writes bare — the enriched-writer path is not wired into the live emitting site
(src/main.py:5878 final-submit boundary writes the bare receipt). Same class as today's
"K-decision executed at one site of three".

## First-principles review preconditions (operator's own gate for scope return)

| Item | Status |
|---|---|
| Remaining-day q indicators | ON (b2c052f8c6, 2026-06-10T22:14Z) — feeding shadow receipts |
| WU per-city publication latency 30-40min | NO evidence of a fix landed (no commit/doc found) |
| Panic-sell-on-transition incident fix | NO evidence of a fix landed |

## Recommendation

1. **Do not unlock.** Gradeable evidence = 0; the settings-note bar is not approachable.
2. The unlock-critical work is THREE fixes to evidence production, in order of leverage:
   a. Wire the enriched day0 receipt writer into the live submit-boundary emitter
      (kills the 99%-bare layer; candidate-content per event already exists in the lane).
   b. Implement the fill-simulation half (book snapshot is already captured per receipt;
      simulate marketable fill + fee law 0.05·p·(1−p)·shares; populate
      hypothetical_fill_price / c_fee_adjusted / would_have_filled).
   c. Grading backfill is already idempotent — once (a)+(b) land and 06-11/06-12
      settlements VERIFY, the 09:25Z tick starts accruing real graded samples.
3. Re-evaluate ~7 days after (a)+(b) deploy: expected 70-150 graded samples (day0 event
   volume is ample — 1864 receipts/2 days; the funnel, not the flow, is the constraint).
