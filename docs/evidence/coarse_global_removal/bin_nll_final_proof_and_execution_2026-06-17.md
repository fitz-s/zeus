# Settlement-bin proof — FINAL result + complete execution list (2026-06-17)

- Created: 2026-06-17
- Authority: operator ("do the de-dup + more proof → final result + complete execution list").
- Method: rolling-origin OOS over 50 cities × ~30 lead-1 days, scored on the REAL market bin parsed from
  `settlements.winning_bin` (1°F ranges / 1°C integer bins / open tails, F→C). RAW, no de-bias. Block-
  bootstrap by city. Scripts: `/tmp/bin_bakeoff.py` (center bake-off), `/tmp/width_final.py` (center×width).
  Settlements immutable (read-only). NOT the live decision path — an offline settlement-graded gate.

## FINAL VERDICT
**Deploy `diag_raw2mom` center — `w ∝ 1/E[r²]` (raw error second-moment diagonal precision) — with the
existing train-RMSE normal width.** It is the proven, RAW-law-compatible upgrade over the live equal-weight
center.

| method (FINE set) | bin-NLL | MAE | cover90 | Δ vs live (95% CI) |
|---|---|---|---|---|
| LIVE equal-weight + rmse σ | 1.719 | 1.099 | 0.910 | — |
| **diag_raw2mom + rmse σ** | **1.676** | **1.031** | 0.897 | **−0.045 [−0.086,−0.010] ✓** |
| diag_raw2mom + k-scaled σ | 1.703 | 1.031 | 0.858 | −0.016 [−0.062,+0.027] ns |
| diag_raw2mom + Student-t σ | 1.674 | 1.031 | 0.897 | −0.046 [−0.087,−0.012] ✓ |
| diag inverse-variance | 1.715 | 1.094 | — | −0.002 ns |
| full Ω-GLS (Ω⁻¹1) | 1.680 | 1.024 | 0.869 | −0.038 [−0.089,+0.009] ns |
| robust median | 1.722 | — | — | +0.004 ns |
| anchor-only (ecmwf) | 1.999 | 1.580 | — | +0.286 worse |

### What the proof establishes
1. **The CENTER is the lever, not width.** `diag_raw2mom` gives the full significant bin-NLL gain
   (−0.045, CI excludes 0) AND ~6% MAE (1.099→1.031), coverage held (0.90). Fitting a width scale `k` on
   ~17 train points OVERFITS (−0.016 ns, coverage drops to 0.86); Student-t ν fits to near-normal (no real
   gain). **The live train-RMSE normal width is already calibrated — leave it.** (Revises the earlier
   "width is the real lever" hypothesis: empirically false at this n.)
2. **`1/E[r²]` beats both the live equal-weight AND the full Ω-GLS.** At n≈17 the full covariance is too
   noisy to estimate (consult's small-n caution confirmed); the diagonal raw second-moment is the sweet
   spot. `E[r²]=var+bias²` downweights persistently-biased models **without subtracting any offset** →
   RAW-law-compatible (it is exactly "prune to the precise models" done continuously and optimally).
3. **Stratified:** pooled significant; non-US significant (−0.035 [−0.071,−0.001]); US point-estimate the
   largest (−0.077, MAE 1.012→0.929) but the 11-city bootstrap CW is wide (CI includes 0) — pooled
   certifies it, US alone can't on n=11. No single-tail-city carries it.
4. **coarse-drop is NOT a bin-objective win.** Under `diag_raw2mom`/GLS weighting (which down-weights them)
   adding gfs_global/gem_global/jma back is marginally BETTER (NLL 1.680→1.648, MAE 1.024→0.985). The
   committed drop is justified only by download/complexity savings + protecting an equal-weight mean — once
   the center is precision-weighted, the drop is accuracy-neutral-to-slightly-negative. Keep the drop (cost
   savings, harmless) but do not claim it as an accuracy lever.
5. **icon_seamless de-dup is bin-neutral** (1.680→1.673) — correct cleanup, landed (commit 56aa5e176c,
   44 fusion tests green), not an accuracy lever.

### Honest caveats
- Width treatment is consistent across methods (each center's train-RMSE); a heavier EMOS width model was
  not beaten by the simple RMSE here. lead-1 only; n≈17–33. The T2-with-EB vs T2-without-EB cost was NOT
  scored in the final run (the center bake-off used RAW diagonal/GLS, not the anchor-prior T2 form) — that
  comparison stays a Phase-2 verification item.

---

## COMPLETE EXECUTION LIST

### DONE
- [x] icon_seamless de-dup landed — commit `56aa5e176c` on `live/iteration-2026-06-13`; 44 fusion tests
      green; report `icon_seamless_dedup_report.md`. (Bin-neutral; correct cleanup.)
- [x] Settlement-bin proof — `diag_raw2mom` center is the proven upgrade (this doc).

### PHASE 1 — Activate spine precision weighting (the proven upgrade)  [implement → verify → critic]
1. **Locate the spine member producer** that builds `RawModelMember`s fed to `center.build_center`
   (qkernel_spine_bridge ← event_reactor_adapter payload ← the forecast member-envelope reader). Confirm
   where per-model recent settlement residuals are (or can be) computed — the capture path already computes
   per-model residual histories for `eb_bias`; reuse that residual source, do NOT recompute a parallel one.
2. **`src/forecast/types.py`** — add to `RawModelMember` a raw second-moment field
   `walk_forward_e2_native: float | None` (= mean of recent per-model raw errors², settlement-graded,
   strictly-prior) + `walk_forward_n: int`. (Repurpose the dormant `walk_forward_se_native` slot.)
3. **Producer (step 1's file)** — thread per-model `E[r²]` + `n` onto each member from the strictly-prior
   walk-forward residuals. Fail-soft: member with no history → `e2=None` → equal weight (current behavior).
4. **`src/forecast/center.py::walk_forward_model_weights`** — precision basis `1/max(E[r²], SIGMA_FLOOR²)`
   (raw second-moment, NOT demeaned variance, NOT inverse-MAE); keep nonneg/sum-to-1, shrink-to-equal at
   `n<MIN_TRAIN` via the existing `lam=n/(n+KAPPA)`, keep the envelope-lock + robust-Huber + ZERO-shift
   downstream. No offset, no center shift (INV-C1 convex-combination preserved).
5. **RED-on-revert test** — divergent per-model `E[r²]` ⇒ weights diverge from `1/n`, upweighting the lowest
   `E[r²]` member in the proven direction; thin/absent history ⇒ collapses to `1/n`. Reverting the basis to
   equal/`se` flips it RED.
6. **Verify:** money-path smoke green (known emos_serve + public_http_timeout fails excluded); the offline
   bin-NLL gate reproduces ≤ live equal-weight on the held set.

### PHASE 2 — Resolve residual EB de-bias (forecast_posteriors path)  [adjudicate → implement]
7. **Adjudicate per operator law:** the T2 `forecast_posteriors` still feeds `z = x − b̂`
   (`bayes_precision_fusion_capture._eb_corrected`) and is read by `event_reactor_adapter` /
   `monitor_refresh` / `position_belief`. Either (a) disable `_eb_corrected` (pass raw `z = x`) so the
   posteriors are RAW too — and re-point that fusion onto the `1/E[r²]` weighting; OR (b) route those lanes
   onto the RAW spine belief (closes the #135 entry/exit asymmetry). First SCORE T2-with-EB vs T2-without-EB
   on the bin objective so the cost of removing EB is known before choosing.
8. **Provenance:** stamp `raw_center=true, debiased_center=false` on posteriors; never silently label EB
   output "RAW".

### PHASE 3 — Width  → NO ACTION
9. Proof shows the train-RMSE normal width is already calibrated (coverage 0.90); scaling/Student-t overfit
   at this n. Keep `build_sigma` realized-floor. Revisit only with materially more data.

### PHASE 4 — Deploy gate + rollback
10. Feature-flag the precision-weight center (old equal-weight ↔ new `1/E[r²]`); versioned provenance
    (`fusion_version`, weights, precision basis, `model_set_hash`). Shadow → live = operator daemon restart;
    rollback = flag flip (both centers coexist).

### PARKED (not in this change)
- coarse-drop: keep (download savings); not an accuracy lever — no action.
- per-city free weights: NOT deployed (sample-inadequate; pooling/family only until effective-n clears a
  declared threshold — consult).
- full Ω-GLS / hierarchical family-block Ω: parked until n supports covariance estimation (diagonal wins now).

Pipeline per operator law: implement (opus) → verify (sonnet, RED-on-revert + money-path) → critic (opus).
Writer ≠ reviewer. Deploy = operator restart.
