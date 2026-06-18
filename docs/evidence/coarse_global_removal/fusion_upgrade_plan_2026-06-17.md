# Fusion alignment upgrade — complete plan + proof protocol (2026-06-17)

- Created: 2026-06-17
- Authority: operator ("write the complete plan and execute the proof in parallel"). Built on the
  ChatGPT Pro consult (`/tmp/cgc_answer_REQ-20260617-160815-8ee567.txt`) + live-code verification
  (`fusion_alignment_full_findings_2026-06-17.md` §6b). RAW-first, no-de-bias, settlement-graded.

## The single thesis (theory + empirics + live code converge)
The live spine center is **RAW but equal-weight (1/n)** — the precision seam (`walk_forward_model_weights`)
is unwired (`walk_forward_se_native` never set). The optimal RAW center is the **nonnegative,
sum-to-1, no-intercept GLS** `w ∝ Ω⁻¹1` on the **raw error second-moment** Ω=E[rrᵀ]=Σ+bbᵀ (downweights
persistently-bad models by their raw error — NOT a de-bias, no offset subtracted). My US test measured the
gap: equal-weight 0.963 vs precision 0.875 point-MAE; this plan PROVES it on the settlement-BIN objective
before any deploy.

## Phase 0 — PROOF (offline, runs now, gates everything else)
Settlement-bin-NLL candidate bake-off. Decides if the upgrade is real on the objective the spine prices.
- **Metric:** primary = settlement-bin negative log-likelihood on the REAL market bins (`pm_bin_lo/hi`,
  F→C); secondary = point-MAE, bias, q_lcb-style lower-bound coverage, PIT. Point-MAE is a DIAGNOSTIC only.
- **CV:** rolling-origin, train strictly < test date; width fit per-method on that method's train residuals.
- **Center candidates:** (1) equal-weight [live], (2) diagonal inverse-variance, (3) diagonal raw
  2nd-moment 1/E[r²] [RAW-optimal diagonal], (4) family-block Ω-GLS (Ω⁻¹1, shrunk, nonneg-projected)
  [consult recommendation], (5) robust median, (6) anchor-only [baselines].
- **Set ablations** (on the winning center): fine-only vs +coarse(gfs_global/gem_global/jma) vs
  +icon_seamless — proves the coarse-drop and the de-dup ON THE BIN objective (consult flagged both as
  unproven on bins).
- **Null:** paired block-bootstrap on the NLL delta vs live equal-weight; report CI excluding 0 or not.
- **Stratify:** pooled + US (HRRR/NBM-rich) vs non-US.
- **Promotion rule:** a center is promotable ONLY if it beats live equal-weight on bin-NLL with a
  bootstrap CI excluding 0 AND does not worsen q_lcb coverage, in BOTH strata (no single-tail-city carry).
- Artifact: `docs/evidence/coarse_global_removal/bin_nll_bakeoff_2026-06-17.md` + script `/tmp/bin_bakeoff.py`.

## Phase 1 — Activate spine precision weighting (only if Phase 0 promotes it)
- Thread per-model walk-forward raw 2nd-moment onto `RawModelMember` (`walk_forward_se_native`/`_n` →
  rename to raw-2nd-moment basis); `center.walk_forward_model_weights` computes `w ∝ Ω⁻¹1` with
  provider-family-block (or Ledoit-Wolf) shrinkage of Ω, nonneg simplex, shrink-to-equal at low n.
- Keep envelope-lock + robust-Huber + ZERO-shift (RAW). No offset. INV-C1 (convex combination) preserved.
- RED-on-revert: a test pinning that with divergent per-model raw-error the weights diverge from 1/n in
  the proven direction (precise model upweighted), and collapse to 1/n when history is thin.

## Phase 2 — Resolve residual EB de-bias (forecast_posteriors path)
- `bayes_precision_fusion_capture._eb_corrected` still feeds `z = x − b̂` into `forecast_posteriors`,
  consumed by `event_reactor_adapter`/`monitor_refresh`/`position_belief` (the #135 entry/exit asymmetry).
- Adjudicate per operator law: either (a) disable `_eb_corrected` (pass raw `z = x`, full RAW everywhere)
  and re-point the fusion onto the raw 2nd-moment Ω weighting, OR (b) route those lanes to the RAW spine
  belief. Phase 0 candidate set includes T2-with-EB vs T2-without-EB so the cost of removing EB is measured.
- Provenance: stamp `raw_center=true, debiased_center=false` on posteriors; no silent "RAW" mislabel.

## Phase 3 — σ_pred width + objective calibration
- Calibrate predictive width (realized residual variance + member dispersion + representativeness variance
  term, pooled by domain/lead) and SELECT on settlement-bin log-loss, not point error.
- Prove PIT uniformity, bin reliability, central-interval coverage, and q_lcb lower-bound hit-rate
  out-of-fold. A center win without width calibration is NOT promotable (consult).

## Phase 4 — Deploy gate + rollback
- Feature-flag old (equal-weight) vs new (precision) center; versioned provenance (`fusion_version`,
  weights, Ω method, shrinkage, width method, model_set_hash). Dry-run backfill; shadow before live.
- Deploy = operator daemon restart. Rollback = flag flip (both centers coexist behind the flag).

## Out of scope / parked
- icon_seamless de-dup is landing separately (executor running) — Phase 0 confirms it on the bin objective.
- Per-city free weights are NOT deployed (sample-inadequate at n=17-33; consult). Pooling/family weights
  only until effective-n clears a declared threshold.

## Execution order
Phase 0 runs NOW (parallel with the de-dup executor). Phases 1-4 are gated on Phase 0's settlement-graded
verdict + operator sign-off. Pipeline per operator law: implement (opus) → verify (sonnet, RED-on-revert +
money-path) → critic (opus). Writer ≠ reviewer.
