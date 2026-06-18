# Consult sign-off — final execution flow (round-2, REQ-...65dd60, 2026-06-17)

- Created: 2026-06-17
- Source: ChatGPT Pro follow-up consult REQ-20260618-060956-65dd60 (the consult completed; the local waiter
  failed with exit 5 — consult ≠ waiter). Synthesized with the local settlement-graded proofs + resolution
  ledger. Caveat: the consult was NOT given the real source files (no gist/upload), so its citations are
  symbol-level not file:line — a properly-file-delivered round is still owed for line-level audit.

## Verdict (consult, high confidence to deploy)
Deploy `w_m ∝ 1/Ê[(x_m−Y)²]` — **diagonal RAW second-moment precision** — as the spine center. It is
settlement-proven superior to live equal-weight AND theoretically optimal **within the RAW, diagonal-error,
fixed-width class**; it is NOT proven globally optimal over all correlated/distributional fusers (stacking,
BMA, full/family-Ω). Confidence: high to deploy now, medium for "provably optimal" beyond that class.
Consult REVISED its prior full-Ω preference: at n≈17 the diagonal has the best proven bias-variance
tradeoff; full-Ω is the asymptotic target after more data.

## Theory confirmations (match the local proofs)
- **Derivation:** RAW center risk = w'Ωw, Ω=E[rr']; diagonal minimizer w_m ∝ 1/E[r_m²] = 1/(var+bias²).
  Inverse demeaned-variance ignores bias² → invalid under RAW (biased models aren't corrected pre-fusion).
  Exactly why diag_raw2mom beat diag_invvar (1.676 vs 1.715).
- **Center-dominance at this regime:** bin-NLL curvature coefficient A(a)≈0.47–0.49 ≈ ½ at σ≈1.1°C,
  bin≈0.56–1.0°C (h/σ≈0.5–0.9) → the center winner wins the bin score; width is not the lever here.
- **Width:** cover90 0.897 ≈ nominal 0.90 ⇒ k≈1, no scale gain; fitting k on short windows chases noise
  (cover dropped to 0.858). Width becomes the lever only at ~25–30% scale miscalibration, or for
  tail/open/boundary bins, or longer leads with under/over-dispersion.
- **diagonal vs full-Ω crossover:** n* ≈ d_F/g_Ω (d_F ≈ inversion-instability × effective-correlated-rank;
  g_Ω = oracle correlation gain V_D/V_F − 1). g_Ω=0.03→n*≈200; 0.05→120; 0.10→60. At n≈17 full-Ω needs
  g_Ω ≳ d_F/17 (≈30–45% oracle gain) to win — not present → diagonal wins now. Keep full-Ω shadowed.

## Key DELTAS the consult adds beyond the prior plan
1. **The two-center split is a BLOCKER, not a dormant seam.** Entry on RAW spine + exit/monitor on
   EB-corrected `forecast_posteriors` is a *lifecycle incoherence*: a strategy must not enter on one belief
   center and manage/exit on another. Fix = ONE canonical belief center.
2. **The coarse +ablation must be REDONE under the DEPLOYED diagonal center.** The local +coarse win
   (1.680→1.648) was measured with full-Ω-GLS (which can weight a correlated coarse model to ~0); the
   diagonal 1/E[r²] gives every finite-error model positive weight, so a correlated coarse model can be
   OVERWEIGHTED. Do not re-add coarse to production until diagonal+coarse passes the bin gate.
3. **EB-resolution sign-off:** for the RAW strategy, REMOVE EB from every CONSUMED center. Keep EB only as a
   separately-named `calibrated_eb_shadow` posterior. Never let `forecast_posteriors` carry EB centers into
   reactor/monitor/exit while entry is RAW diagonal. (This is the answer to the operator's law question:
   the EB win is honest MOS, but it is a different product — it must not silently feed the RAW strategy.)

## THE SIGNED-OFF EXECUTION FLOW (each step gated by a settlement-graded check)
0. **Freeze baseline.** Flag `RAW_SECOND_MOMENT_CENTER_V1=false`; persist live outputs as
   `fusion_version=live_equal_raw_v0`, `posterior_version=eb_t2_v0`. Reproduce live equal-weight bin-NLL
   1.719 ± tol, cover90 0.910 before any change.
1. **Raw residual training dataset.** Date-aligned rolling table (city, target_date, lead, model, raw
   forecast, settlement, winning_bin, eligibility); residual = `raw_forecast_c − settlement_c` (NOT x−b̂,
   NOT demeaned); target_date strictly prior. Unit test: residual byte-equals raw−settlement, no EB in the
   call graph.
2. **Diagonal raw-2nd-moment weights.** Wire `center.walk_forward_model_weights` precision basis to
   `m̂_m = max((1/n)Σ_{τ<t}(x_τm−Y_τ)², m_floor)`, `w_m = m̂_m⁻¹ / Σ m̂_j⁻¹`, present-eligible models only,
   keep the certified fine-only set. No demean, no var-alone, no bias subtract. Backtest == research script
   within tolerance per city-date.
3. **Low-n/floor.** Preserve the research estimator that produced 1.676; add a conservative floor +
   min-count fallback to equal/family-m0 when n<n_min; stronger hierarchical shrinkage in shadow only.
4. **Train-RMSE normal width.** σ = rolling train RMSE of the fused RAW center residuals + floor; no k-fit,
   no tuned Student-t. Reproduce bin-NLL 1.676, cover90 0.897.
5. **Unify posteriors with the spine (BLOCKER).** RAW posterior writer materializes the SAME μ, σ, bin
   probs, q_lcb inputs, model set, weights as the spine; disable `_eb_corrected` for any `forecast_posteriors`
   consumed by the RAW strategy; EB T2 survives only as `posterior_version=calibrated_eb_shadow`.
   Integration test: reactor/monitor/exit never read EB posteriors when the RAW flag is active.
6. **Provenance + invariants.** Persist `center_method=raw_second_moment_diag_v1`, `center_raw=true`,
   `debias_applied=false`, `width_method=train_rmse_normal_v1`, `model_set_hash`, `training_cutoff`,
   `n_train_by_model`, `winning_bin_schema_version`; reject writes missing these.
7. **Settlement-graded gate (production code, not notebook).** Require bin-NLL improvement vs live equal
   ≤ −0.035 pooled, 95% city-block CI upper bound < 0, cover90 ∈ [0.88,0.92], no stratum catastrophe.
8. **Coarse-candidate decision.** After fine-only deploy verified, run 4 production shadows
   (fine-only / +coarse / +icon_seamless / +both) under the EXACT diagonal center; promote +coarse only if
   it wins bin-NLL with CI not crossing 0 (or a predeclared EV/q_lcb benefit, no calibration loss).
9. **Full-Ω shadow + switch rule.** Keep nonneg full-Ω + family-Ω shadow; estimate g_Ω, d_F, n* per domain;
   switch only when it beats diagonal on bin-NLL CI-excl-0 AND q_lcb calibration intact.
10. **q_lcb + EV validation.** After unification, q_lcb reliability by probability bucket + after-cost EV by
    bin class; a bucket with q_lcb≥q must realize ≥q within tolerance.
**Rollback:** ONE flag controls BOTH the spine center AND the consumed posterior version; rollback restores
both together. Never roll back only the spine (leaving RAW posteriors) or only posteriors (leaving precision
spine) — that re-creates the entry/exit incoherence.

## Still unproven (consult)
- Global optimality vs stackers/BMA/family-Ω (keep in shadow, settlement-prove before promotion).
- diagonal+coarse final answer (the +coarse win was full-Ω; redo under diagonal — step 8).
- exact n* per domain (needs per-domain Ω spectra: V_D, V_F, g_Ω, d_F, n_eff).
- width outside lead-1 (repeat by lead/bin schema).
- **line-level source audit** — the consult had no file:line because the real files were never delivered;
  owe one properly-gisted round for line-level verification of steps 2/5/6 against the actual code.
