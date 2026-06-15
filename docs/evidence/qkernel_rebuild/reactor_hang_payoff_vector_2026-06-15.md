# Reactor hang root-cause + fix — payoff_vector ΔU stake sweep (2026-06-15)

Authority basis: live diagnosis (faulthandler SIGUSR1 stack dump of the hung daemon) +
direct equivalence/timing measurement. Commit `93999d9d9b` on `live/iteration-2026-06-13`.

## Symptom
After the q-kernel spine went live (Wave 5B), the EDLI reactor cycle ran **~660 s at
100% CPU** against a 45 s budget. apscheduler skipped every reactor instance
(`maximum number of running instances reached (1)`), `last_cycle` froze for 10-13 min at
a time, and `live_health_composite` went `DEGRADED: STATUS_SUMMARY_STALE`. Every priced
forecast family's executable snapshot expired before the cycle reached it →
`MONEY_PATH_HORIZON_EXPIRED:...:EXECUTABLE_SNAPSHOT_STALE` → **zero fills**. The hang was
the proximate cause of no-fills, not a belief/edge problem.

## Diagnosis (how)
`py-spy` needs root on macOS; the daemon registers `faulthandler` on SIGUSR1
(`src/main.py:47`). `kill -USR1 <pid>` dumped all thread stacks to `zeus-live.err`. The
reactor thread `ThreadPoolExecutor-1_0` was spinning in:

```
utility_ranker.py:550 _delta_u_at_stake
payoff_vector.py     robust_delta_u           (per-draw Python loop)
payoff_vector.py     optimize_vector_stake    (396-point stake grid)
family_decision_engine.py:795 _score_route
qkernel_spine_bridge.py:836 decide_family_via_spine
```

## Root cause
`optimize_vector_stake` is a coarse→fine 1-D grid: `_COARSE_STEPS=200` + `_REFINE_PASSES=3`
× `_REFINE_STEPS=64` = **396 stake points**. At EVERY point, `robust_delta_u` rebuilt the
full `DEFAULT_N_DRAWS=4000`-draw effective-π set (`_draw_to_pi` + `effective_outcome_pi`)
and summed the draws in a **pure-Python `for k in range(4000)`** loop calling
`_delta_u_at_stake`. That is **396 × 4000 ≈ 1.6M `_delta_u_at_stake` calls per candidate**,
times candidates × routes × families per cycle. Measured: **~71 s per candidate**.

Two redundancies: (a) the per-draw effective-π is **stake-INDEPENDENT** yet was recomputed
at all 396 grid points; (b) ΔU is **linear in π** so the 4000-draw Python loop is just a
matmul.

## Fix (commit 93999d9d9b)
`_PreparedSizing`: precompute the `(4000 × n_outcomes)` effective-π matrix **once per
candidate**; each stake evaluation = one per-outcome growth vector `g_y(s) = log(A_y+R_y(s))
− log(A_y)` (n_outcomes `matrix.payoff` walks) + a single `Π @ g` matmul + quantile. The
draw loop is gone.

Behavior-preserving (NOT a cap/haircut — operator law): same `effective_outcome_pi`, same
Decimal-wealth ruin rule (a draw with positive mass on a ruinous/infeasible outcome → −inf,
matching the per-draw `if p<=0: continue` skip), same alpha-quantile.

## Verification
- Equivalence vs the per-draw `_delta_u_at_stake` reference loop on a 4000-draw fixture:
  **max |Δ| = 5.5e-17** (machine epsilon, float-associativity only).
- Timing: `optimize_vector_stake` **71 s → 51 ms per candidate (~1400×)**.
- `tests/decision/test_vector_sizing_authority.py` 3/3 pass.
- Money-path: 328 passed; the only 3 failures are the pre-existing
  `test_finding_b_free_cash_bound.py` cases (free-cash bankroll binding, unrelated).

## Live signal to confirm
Reactor cycle results landing every ~45 s (not every 10-13 min); CPU off the 100% peg;
forecast families reaching a spine price decision instead of universal
`EXECUTABLE_SNAPSHOT_STALE`. The success bar remains unchanged: a real settlement-graded
positive-after-cost fill, not one forced order.
