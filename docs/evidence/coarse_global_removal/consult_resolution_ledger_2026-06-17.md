# Consult gap-list resolution ledger — local verification (2026-06-17)

- Created: 2026-06-17
- Authority: operator — "the consult delivery missed the file-upload form so many items were unverifiable,
  but every question both consults raised is factual and must be solved one by one." Claude Code is the
  verification source of truth (the consult could not read live code). Verdicts: RESOLVED-OK /
  CONFIRMED-ISSUE / NEEDS-FIX / PENDING.
- Delivery note: consults were fed `--context-file` (pasted summaries), not the skill's file-delivery
  (gist/GitHub link); the agent cannot auto-upload, but could have gisted/pushed the real files — it did
  not, so the consult's "verify locally" caveats are resolved here against the actual code.

## BLOCKERS
1. **Objective proved on real settlement bin** — RESOLVED. Bake-off scores on the REAL market bin parsed
   from `settlements.winning_bin` (1°F/1°C/tail), rolling-origin, block-bootstrap. `diag_raw2mom` beats
   live equal-weight significantly (−0.045 bin-NLL, CI[−0.086,−0.010]). (`/tmp/bin_bakeoff.py`)
2. **RAW law vs EB de-bias** — CONFIRMED-ISSUE. Live spine center is RAW (`build_center` ZERO-shift) but
   currently EQUAL-WEIGHT (precision seam `walk_forward_se_native` never set → 1/n). The T2
   `fuse_bayes_precision_posterior` path still feeds `z = x − b̂` (`_eb_corrected`) into
   `forecast_posteriors`, consumed by `event_reactor_adapter`/`monitor_refresh`/`position_belief`. Two
   centers: RAW spine entry vs EB posteriors (the #135 asymmetry).
3. **Covariance object: demeaned Σ vs raw 2nd-moment Ω** — CONFIRMED-ISSUE. `shrink_cov` uses
   `np.cov(resid_mat)` (DEMEANED) → estimates Σ, discards `bb'`. Under RAW (no EB) the optimal basis is the
   raw 2nd-moment `Ω=E[rr']=(R'R)/n` (includes bias²). Empirically confirmed: `diag_raw2mom` (1/E[r²])
   beats `diag_invvar` (1/demeaned-var). FIX: precision/covariance basis = raw 2nd-moment under RAW.
4. **Shrinkage target for correlated families** — RESOLVED. Full Ω-GLS (`Ω⁻¹1`, Ledoit-Wolf shrink) does
   NOT beat the diagonal raw-2nd-moment at n≈17 (1.680 vs 1.676, ns) — the covariance estimation variance
   exceeds its bias-reduction at small n. Diagonal is correct now; provider-family-block Ω parked until n
   supports it.
5. **Anchor independence** — RESOLVED for the deploy path. `bayes_fuse` treats ecmwf as an independent
   prior (`mu0/tau0`, no cross-covariance with Σ) — a real approximation in the T2 `forecast_posteriors`
   path. BUT the live spine `build_center` includes ecmwf as a MEMBER (not a separate prior), so the
   anchor-independence issue does NOT affect the chosen spine `1/E[r²]` deploy. (If T2 is kept on the
   posteriors path, address it there.)
6. **σ_pred / PIT / q_lcb honesty** — CONFIRMED-ISSUE (decision-changing). PIT = Φ((s−μ)/σ) on the proof
   set: LIVE equal χ²=75, diag_raw2 χ²=67, T2-noEB χ²=67 — all BADLY miscalibrated (crit 16.9) despite
   central-90 coverage ≈0.90. Only **T2 +EB χ²=17 (≈calibrated)**. The miscalibration is the uncorrected
   per-city LOCATION bias (a mixture of per-city-shifted PITs), which WIDTH calibration cannot fix — only a
   center correction (EB or a real source fix) fixes location. So under the RAW law the predictive
   distribution is miscalibrated and q_lcb honesty is compromised. (`/tmp/t2_eb_pit.py`) q_lcb *formula*
   read: PENDING (investigator ab479a8).
7. **inverse-MAE ≠ precision** — RESOLVED. The US point-MAE test used `1/(MAE+0.3)`; the bin proof uses
   proper `1/var` and `1/E[r²]`. The bin verdict stands on correct precision.

## THE EB COST (consult: "report the cost of the no-debias law separately; do not sneak EB into RAW")
Settlement-graded, OOS, real bins:
| center | bin-NLL | MAE | PIT χ² | law |
|---|---|---|---|---|
| LIVE equal-weight | 1.719 | 1.099 | 75 | RAW (current) |
| diag_raw2mom (1/E[r²]) | 1.676 | 1.031 | 67 | RAW (law-compliant, deployable) |
| T2 +EB (z=x−b̂, walk-forward) | 1.648 | 0.974 | 17 | de-biased (forbidden) |
| T2 noEB (raw Ω) | 1.695 | 1.043 | 67 | RAW |
EB is the single biggest lever AND the only center that calibrates the PIT. Cost of the no-de-bias law:
≈+0.026–0.071 bin-NLL vs EB AND a miscalibrated PIT (χ²67 vs 17) that width cannot repair. The per-city
bias survives coord+land+lapse source correction → no source fix in hand. **Operator decision required:
RAW (diag_raw2mom, law-compliant, miscalibrated PIT) vs allow EB (calibrated, forbidden by current law).**

## MAJOR
- Per-city free weights sample-inadequate — RESOLVED (pooling/diagonal only; no per-city free weights).
- US not globally proven — RESOLVED (stratified: non-US significant −0.035; pooled certifies; no tail-city carry).
- coarse-drop not proven on bin — CONFIRMED. Under precision weighting, +coarse is marginally BETTER
  (1.680→1.648). Drop is justified by download/complexity only, not accuracy. Consider retaining coarse as
  low-weight candidates (the follow-up consult q(c)).
- JMA removal on bin — PARTIAL: bundled in the coarse ablation, not isolated. TODO isolate jma-only.
- icon_seamless de-dup — RESOLVED (landed 56aa5e176c, bin-neutral 1.680→1.673, 44 fusion tests green).
- representativeness root-cause — RESOLVED: residual survives coord+land(=nearest for cold cluster)+lapse
  (corr −0.09); it is sub-grid coastal microclimate (LA land-vs-nearest +8°C); no global source fix; the
  residual is exactly what EB corrects.
- q_lcb formula not read — PENDING (investigator ab479a8).

## MEDIUM
- residuals_by_date threading — RESOLVED-OK. `_common_window_residual_matrix` builds the covariance on the
  INTERSECTION of per-instrument target_date sets (no cross-date row); capture threads
  `residuals_by_date=h.residual_by_target_date` (capture:453,461). The dangerous positional fallback is
  test-only. Production covariance is date-aligned.
- settlement window / station alignment — PENDING (investigator a843c53).
- materialization snapshot / concurrency — PENDING (investigator a843c53).
- missing-data / provider completeness — RESOLVED (domain-AND-lead-aware expected-family contract, prior work).
- rollback / historical comparability — Phase 4 plan (feature-flag + versioned model_set_hash).

## LOW
- Normal tails — RESOLVED: Student-t ν fits to near-normal; no bin-NLL gain over normal.
- Lapse one Γ — RESOLVED: tested 0.0065°C/m on config elevations, corr −0.09, correction slightly worsens.

## PENDING (in flight)
- Investigator ab479a8: q_lcb formula + bin integration (real bins? out-of-fold lower bound? clip/floor?).
- Investigator a843c53: settlement high-window/station alignment + materializer snapshot/concurrency/leakage.
- Follow-up consult REQ-...65dd60: theory derivation (n* crossover, width-lever condition, coarse-retain,
  EB/two-center coherence) + sign-off execution flow.
