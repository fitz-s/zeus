# Honest q_lcb on the bias-corrected domain — coverage re-measurement (#89)

Created: 2026-06-03
Authority basis: operator pre-arm blocker 2026-06-03 (#89 coverage license). Iron rules 5+6
(make the certain-NO category impossible; overconfidence = ruin).

## What changed (the two structural fixes)

1. **Full forward predictive σ_repr** (`src/calibration/ens_error_model.py`
   `full_predictive_residual_sd` / `predictive_heterogeneity_var`). The live q_lcb inflater
   now reads `model_bias_ens.total_residual_sd_c` (`src/engine/event_reactor_adapter.py:4046`+,
   was `residual_sd_c`). The producers (`scripts/write_promoted_edli_bias.py`,
   `scripts/write_d7_rolling_edli_bias.py`) and the in-place backfill
   (`scripts/backfill_edli_total_residual_sd.py`) stamp
   `total_residual_sd_c = σ_resid·sqrt(1 + 1/n)` and `heterogeneity_var_c2 = σ_resid²/n` — the
   honest predictive σ for one out-of-window day (in-sample scatter + mean-estimation drift).
   This is the SMALLEST defensible widening; the seasonal fit↔serve drift is NOT manufactured.

2. **"No certain NO" ceiling** (`src/strategy/market_analysis.py`
   `_no_certain_yes_floor`, applied at `_bootstrap_bin_no` ~L992). On the corrected domain the
   NO ceiling is tightened from the legacy `1 - p_posterior` to `1 - YES_floor`, where
   `YES_floor = P(settlement ∈ bin | mean = member mean, σ = σ_repr)` is the honest Gaussian
   mass (point/closed bins expanded by half the settlement precision so the rounding interval
   is integrated, not a zero-width point). σ_repr=0 → floor=0 → byte-identical legacy. A
   corrected-domain q_no_lcb of exactly 1.0 is now UNCONSTRUCTABLE (verified: max q_new across
   all 62 settled candidates = 0.99999, 0 reach 1.0).

The point q (q_live / p_posterior) is unchanged — only the lower bound widens.

## Coverage on the 62 existing settled buy_no candidates (no new data)

buy_no WINS = settlement did NOT land in the bin. honest = claimed q_lcb ≤ realized win-rate.

### BEFORE (receipted q_lcb, in-sample, no ceiling)
| bucket | n | claimed | realized | gap | honest? |
|---|---|---|---|---|---|
| [0.85,0.95) | 11 | 0.927 | 0.545 | -0.382 | NO |
| [0.95,0.99) | 14 | 0.973 | 0.571 | -0.402 | NO |
| [0.99,1.00) | 28 | 1.000 | 0.750 | -0.250 | NO |
| **POOLED** | 62 | 0.936 | 0.645 | **-0.291** | NO |
| DEEP ≥0.95 | 42 | 0.991 | 0.690 | -0.301 | NO |

### AFTER (full predictive σ + no-certain-NO ceiling)
| bucket | n | claimed | realized | gap | honest? |
|---|---|---|---|---|---|
| [0.70,0.85) | 22 | 0.813 | 0.591 | -0.222 | NO |
| [0.85,0.95) | 32 | 0.893 | 0.688 | -0.206 | NO |
| [0.95,0.99) | 4 | 0.977 | 0.750 | -0.227 | NO |
| [0.99,1.00) | 2 | 1.000 | 0.500 | -0.500 | NO |
| **POOLED** | 62 | 0.862 | 0.645 | **-0.216** | NO |
| DEEP ≥0.95 | 6 | 0.984 | 0.667 | -0.318 | NO |

The fix did its structural job: the ≥0.99 "certain-NO" bucket collapsed from 28 → 2, the deep
≥0.95 tail from 42 → 6, and pooled claimed q dropped 0.936 → 0.862. But the residual gap is
still ~22 pts: even the HONEST q_lcb over-states the realized win-rate.

## Edge-survival (honest q_lcb): min(q_lcb, q_live) − c95 − 0.01 > 0

| q_lcb variant | clears cost | win-rate of survivors |
|---|---|---|
| receipted | 62/62 | 0.645 (40/62) |
| OLD recomputed (σ=0) | 60/62 | 0.633 (38/60) |
| **NEW honest** | **40/62** | **0.550 (22/40)** |

40 candidates still clear the edge gate with the honest q_lcb — but their realized win-rate
(0.550) is barely above the ~0.55 implied breakeven and BELOW their claimed confidence. The
honest book is weak and still over-confident, not a clean alpha slice.

## LICENSE VERDICT: **DENIED**

After the principled fix (genuine total residual uncertainty + honest Gaussian ceiling, no σ
inflation, no tuned ceiling), the q_lcb is STILL over-confident on the existing settled data:
pooled claimed 0.862 vs realized 0.645 (−22 pts); deep tail claimed 0.984 vs realized 0.667
(−32 pts). q_lcb ≤ realized holds in NO bucket.

Per the anti-p-hacking rule, this is the truthful outcome: the deep buy_no trades have
**negative honest edge** — the irreducible representativeness σ is genuinely too small to make
these far bins "certain not-settle", and the realized losses (buy_no on the bin that settled)
confirm the over-confidence was real, not a CI artifact. The 28 receipted-certain (q=1.0)
candidates realized only 0.75; the fix correctly demotes them, and the edge gate correctly
sheds 22 of 62. **A weak/empty honest book is the honest answer; do NOT arm on this.**

Caveats: n=62, all pre-#135-gate, only 3 settled days, per-city n=1-9. The measurement
recomputes q_lcb on the actual causal snapshots (members reachable for all 62), not a
simulation. The remaining over-confidence is dominated by directional/bias error (buy_no fired
on bins that matched reality), which is the #135 mainstream-agreement gate's job, not the CI's.
