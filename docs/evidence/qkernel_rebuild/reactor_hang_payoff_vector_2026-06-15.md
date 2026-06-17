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

## Live signal — CONFIRMED (post-deploy, PID 21155)
- Reactor cycle results now land every ~1-2 min (was frozen 10-13 min); CPU off the 100% peg
  (53% during warm boot vs the old 103-106% spin).
- The spine reaches real economic verdicts on live forecast families (`NO_POSITIVE_EDGE_CANDIDATE`
  at 08:33/08:35) — the first continuous live spine pricing.
- **FIRST LIVE ORDER IN 3 DAYS** at 08:49:25 local (13:49 UTC): `LIVE ORDER: buy_no @ 0.700,
  8.08 shares` on a 06-17 forecast-lead family (gamma 2549710, NO token 817703737212…). The
  reactor cycle reported `processed=1 proof_accepted=1 rejected=0 reasons=[]` — the first
  accepted proof of the session. Prior live order was 06-12 08:04 (67 total ever).
- The order carries the full certificate chain (PreSubmitRevalidation → ExecutionCommand →
  ExecutionReceipt, LIVE mode, authority edli.final_intent_executor_bou) — a properly
  revalidated submission, not a fluke. State: ACKED/resting (limit, 900 s timeout).

The un-hang was the proximate cause: 660 s cycles staled every snapshot before its family
could fill; un-hung, the decision path carries through to a certified live submission again.

CONTINUITY CONFIRMED (not a one-off): a SECOND spine order at 09:30:32 local —
`buy_no @ 0.74, 9.81 sh` on another 06-17 forecast family (gamma 2549499). Two accepted
proofs / two live buy_no orders in ~40 min, both edge_lcb>0-gated.

FILL — the goal's currency (09:38:26 local): the second order **FILLED** — `buy_no 9.81 sh
@ 0.74` (~$7.26), state ACKED→FILLED 8 min after submit; on-chain confirmed at 09:43:08
(`TOKEN_BLOCK_AUTO_CLEARED ... chain_bal=9.8100` — 9.81 NO shares held on-chain). This is a
real, on-chain-verified alpha position from the rebuilt spine, the direct end-to-end result
of the un-hang fix (3 days of dead submission → priced → submitted → FILLED). It settles
06-17; the NO leg pays $9.81 vs $7.26 cost if the bin does not hit (the spine's edge_lcb>0
asserted NO prob > 0.74). The first order (`buy_no @ 0.70`) remains resting as a maker bid
below the 0.77 NO ask. SUCCESS BAR not yet fully met: needs the 06-17 settlement to grade
positive AND continued continuous positive-after-cost fills — but the money path is live
end-to-end again, with a filled on-chain position to settlement-grade.

## Still open (the goal is NOT met by one resting order)
- This order must FILL and settle positive-after-cost (06-17 settlement).
- Continuity: the reactor must keep producing accepted proofs (most current cycles still hit
  legacy `TRADE_SCORE_NON_POSITIVE` / `FDR_REJECTED` on day0 families — the spine forecast lane
  is the edge source and must be reached more often).
- The success bar is unchanged: continuous settlement-graded positive-after-cost fills, not
  one order.

## Next systemic suppressor — band over-dispersion (unified root; under settlement-grading)
The edge diag + the live book surfaced ONE root behind two suppressions:

1. **YES-tail point-edge suppressed** (`SPINE_NOTRADE_EDGE_DIAG`, family 95571e6b): positive-edge
   YES candidates (`edge_lcb=+0.041`, `dU=+0.0027`, pt_ev=+0.091, cost 0.090; and `+0.012`,
   cost 0.031) on NON-modal bins are killed at `_select` step 2 by `direction_law_ok`
   (`family_decision_engine.py:405` — YES legal only on the modal bin). NO is legal on any
   non-modal bin (why the live `buy_no @0.70` order was allowed).

2. **NO maker-bid fill suppressed** (live CLOB book for the order's token): our `buy_no @0.70`
   is the TOP no-bid; the lowest NO ask is 0.77. The order passed `edge_lcb>0` ⇒ q_lcb_no>0.70,
   and the point q_no is higher still — so the ask (0.77) plausibly sits BETWEEN our conservative
   q_lcb_no (0.70) and our point q_no. We rest a maker bid at the conservative q_lcb instead of
   crossing to a point-edge taker fill, so it never fills.

Both trace to an **over-dispersed predictive band**: a too-wide σ widens the q_lcb↔point gap,
simultaneously inflating point-q on the tails (item 1) and crushing the conservative q_lcb bound
(item 2). Prime knob: `sigma_authority.build_sigma` serves `σ = max(global_lead_bucket_floor =
1.31 + 0.10·lead_days, realized/fused width)`; the global floor likely binds above realized at
2-day lead (≥1.51 °C). **Do NOT retune blind** (operator law: settlement-validated calibration,
no fixed numbers). Background analyst is settlement-grading the over-dispersion factor per lead
bucket (non-modal-bin q calibration + a taker-cross counterfactual) → docs/evidence/qkernel_rebuild/
nonmodal_bin_calibration_2026-06-15.md. The calibrated σ-floor / band correction deploys on that
verdict. Tasks: #121 (this), cross-ref #98 (σ over-dispersion), #91 (q_lcb caps).

## LIVE-spine confirmation: direction-law is the active suppressor (5 diag samples, 2026-06-15)
Across 5 `SPINE_NOTRADE_EDGE_DIAG` families today the top candidate consistently carries
POSITIVE conservative edge — `edge_lcb` +0.032..+0.043, `pt_ev` +0.08..+0.09, `dU`>0 — yet the
family is `NO_POSITIVE_EDGE_CANDIDATE` because those are YES on NON-MODAL bins, killed at
`_select` step 2 by `direction_law_ok`; the direction-law-LEGAL candidates have ~zero edge
(`edge_lcb` +0.0004, `dU`<0). The live spine's OWN 5th-percentile bound clears the killed
candidates → on these families the band is NOT the binding suppressor; the direction-law is,
overriding the spine's positive-edge verdict. This is the LIVE-path confirmation of the analyst's
shadow finding (#121: non-modal q calibrated). Relaxation (admit edge_lcb>0 non-modal YES) stays
gated on the live spine's non-modal SETTLEMENT calibration — the diag log accrues the killed-
candidate set for 06-17+ grading — but the suppressor is confirmed on the live decision path, not
just shadow. NOTE the wider-σ caveat: the live spine's build_sigma floor is wider than the shadow
chain, so its non-modal tail q is higher; whether that is still calibrated (relax is safe) or
over-inflated (direction-law correctly guards) is the exact thing the 06-17+ settlement of these
diag candidates resolves.
