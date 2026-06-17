# Fix: forecast-first lane interleave — un-starve the harvest lane under a 1-decision budget

- Created: 2026-06-16
- Authority basis: live observation (opportunity_event_processing attempt_count, reactor cycle
  results) + reactor.py `_fair_lane_interleave` docstring (order-only, correctness-neutral).
  GOAL #83. RULE 1: zero harvest decisions is OUR defect (lane starvation), not absent edge.

## Observed binding constraint
Live 2026-06-16 (reactor up 12min post-restart): 176 FORECAST_SNAPSHOT_READY families sat
attempt_count=0 (NEVER CLAIMED); reactor claimed ~2 day0 families total; decision_certificates=0;
`EDLI reactor cycle result: processed=0 retried=2`. The reactor's 45s per-cycle budget completes
~1 decision in the degenerate live case (a single family decision can run to ~460s). The fetch
order is Tier-0 DAY0_EXTREME_UPDATED before Tier-1 FORECAST_SNAPSHOT_READY, and
`_fair_lane_interleave` gave **day0 the first slot** — so the one decision the budget completes is
always a day0 family, and the forecast/spine harvest lane (the operator's alpha target) is never
reached. `_fair_lane_interleave` was meant to give each lane a fair half, but a fair half of a
1-decision budget is 0 for whichever lane is second.

## Fix (order-only, correctness-neutral)
`src/events/reactor.py::_fair_lane_interleave`: give the FORECAST decision lane
(FORECAST_SNAPSHOT_READY + EDLI_REDECISION_PENDING) the FIRST slot instead of day0. Under a budget
that completes only ~1 decision, the lane holding the first slot is the only one that runs, so the
harvest lane must hold it. Per-lane (per-city-fair) order preserved; cross-lane alternation stays
1:1. The docstring already states processing order does not affect decision correctness — this only
changes which lane is guaranteed budget under starvation.

## Safety
Order-only: each family is decided on its own fresh inputs; no gate, cap, or economic change. Day0
is not starved (still alternates 1:1); it simply yields the first slot to the harvest lane the
operator is targeting. Cannot produce a bad trade — all downstream gates (source-truth, executable
snapshot, riskguard, spine direction-law/edge_lcb, submit-time JIT q_lcb) are unchanged.

## Test / rollback
tests/events/test_fair_lane_interleave.py (forecast-first first slot; 1:1 alternation; per-lane
order). Rollback: `git revert`; restart daemon. Config-free; no migration.
