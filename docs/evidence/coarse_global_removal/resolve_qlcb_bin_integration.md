# resolve_qlcb_bin_integration.md
# Created: 2026-06-18
# Authority basis: live code audit — src/probability/joint_q.py, joint_q_band.py,
#   src/calibration/emos_q_builder.py, emos.py, qlcb_provenance.py,
#   src/data/replacement_forecast_materializer.py (_build_fused_q_bounds),
#   src/engine/qkernel_spine_bridge.py, src/contracts/settlement_semantics.py

---

## 1. Bin Integration — where and how q[bin] is computed

**Primary site:** `src/probability/joint_q.py::build_joint_q`
(lines 217–321)

**Formula (verbatim):**
```python
# For each bin b in omega.bins:
p = bin_probability_settlement(
    mu,
    sigma,
    b.lower_native,
    b.upper_native,
    rounding_rule=rule,      # EXPLICITLY threaded — never defaults to WMO
)
# After all bins:
q = np.clip(np.asarray(probs, dtype=float), 0.0, None)
total = float(q.sum())
q = q / total               # ONE normalization; Sigma q == 1 by construction
```

`bin_probability_settlement` (src/calibration/emos.py:580–657) computes:
```python
low_offset, high_offset = settlement_preimage_offsets(rounding_rule, half_step=half_step)
# wmo_half_up  -> (-0.5, +0.5)  [symmetric]
# oracle_truncate/floor -> (0.0, +1.0)  [HK asymmetric]

cdf_low  = scipy.norm.cdf((bin_low  + low_offset  - mu) / sigma)
cdf_high = scipy.norm.cdf((bin_high + high_offset - mu) / sigma)
return max(0.0, cdf_high - cdf_low)
```

**Settlement-preimage source:**
`src/contracts/settlement_semantics.py::settlement_preimage_offsets`
(lines 57–103) — a single declarative contract; never re-derived in the integrator.

**NORMAL family:** pure normal CDF integrated over the settlement preimage of each bin label.
No mixture with uniform; no non-standard transform. Rounding rule (wmo_half_up vs oracle_truncate
for HK) is resolved from `omega.resolution.rounding_rule` — the same per-city settlement rule
that governs grading.

**DAY0 families (DAY0_HIGH_MAX_NORMAL / DAY0_LOW_MIN_NORMAL):** the bin's preimage comes from
`day0_bin_preimage_native(lo, hi, rounding_rule=rule)` and the mass from
`probability_high_day0_bin` / `probability_low_day0_bin`, which integrate Y=max(obs_high, X)
or Y=min(obs_low, X) under the same underlying Gaussian CDF.

**Secondary (legacy replacement path):** `src/data/replacement_forecast_materializer.py::_build_fused_q_bounds`
(lines 1632–1725) — fused-posterior q path. Uses the SAME `bin_probability_settlement` formula
vectorized over (N draws × M bins) with `scipy.special.ndtr`. This path does NOT per-row
normalize before taking percentiles (known issue, addressed in joint_q_band.py below).

---

## 2. q_lcb — where and how the conservative lower bound is computed

**Two distinct q_lcb construction paths exist:**

### Path A — Joint Q Band (src/probability/joint_q_band.py, lines 363–445)
Used by the qkernel spine (FamilyDecisionEngine via qkernel_spine_bridge).

**Algorithm (verbatim):**
```python
for k in range(n_draws):       # DEFAULT_N_DRAWS = 4000
    mu_k    = rng.normal(loc=pd.mu_native, scale=center_parameter_se_native)
    sigma_k = max(rng.normal(loc=pd.sigma_native, scale=sigma_draw_dispersion), floor)
    pd_k    = replace(pd, mu_native=float(mu_k), sigma_native=float(sigma_k))
    q_k     = build_joint_q(pd_k, omega).q   # already on the simplex (q/q.sum())
    samples[k, :] = q_k

q_lcb = np.quantile(samples, alpha, axis=0)   # DEFAULT_ALPHA = 0.05
q_ucb = np.quantile(samples, 1 - alpha, axis=0)
```

- mu-draw SE = `pd.sigma_components.center_parameter_se_native` (file: sigma_authority.py)
- sigma-draw floor = `max(realized_floor_native, 1e-6)` (never sub-realized)
- Every row in `samples` is already a normalized probability simplex (sum==1 within 1e-9)
- q_lcb is the **alpha-quantile of COHERENT joint distributions**
- basis = `"PARAMETER_POSTERIOR_SIMPLEX_V1"`

No James-Stein shrinkage, no market-price shrinkage, no explicit z formula. The width comes
from the parameter posterior of (mu, sigma), both drawn together.

### Path B — Fused-center bootstrap (replacement_forecast_materializer.py, lines 1632–1725)
Used by the legacy reactor path (replacement_0_1 / bundle q_lcb materialized into the DB).

**Algorithm (verbatim):**
```python
rng = np.random.default_rng(0x5EED_F09)   # FIXED SEED — deterministic
mu_draws = rng.normal(loc=mu_star, scale=center_sigma_c, size=200)   # CENTER ONLY
sigma = float(predictive_sigma_c)           # FIXED — NOT drawn per-sample

z_low  = (lows[None,:]  - mu_draws[:,None]) / sigma   # (200, M)
z_high = (highs[None,:] - mu_draws[:,None]) / sigma
probs  = np.clip(ndtr(z_high) - ndtr(z_low), 0.0, 1.0)  # (200, M) — NOT row-normalized

q_lcb_vec = np.percentile(probs, 5.0, axis=0)   # per-bin 5th pct of UN-normalized rows
# Defensive clip: q_lcb in [0, q_point]
lcb = min(max(lcb, 0.0), max(q_pt, 0.0))
```

`center_sigma_c` = `bayes_precision_fusion.fused.sd` (posterior SD of the fused center, from
multi-model Bayes fusion). `predictive_sigma_c` = `sqrt(fused.sd^2 + sigma_resid^2)` (total
predictive spread). Draws are **center-only** (sigma held fixed), not full parameter posterior.

**Note:** This path (Path B) takes percentiles of UN-normalized per-draw rows — the known
defect that `joint_q_band.py` replaces for the qkernel spine path. For the fused-center
bootstrap (Path B), the defensive clip `q_lcb <= q_point` prevents q_lcb from exceeding the
point estimate but does NOT restore simplex coherence across bins.

### Calibration source vocabulary (src/calibration/qlcb_provenance.py:43):
```python
CalibrationSource = Literal[
    "FORECAST_BOOTSTRAP",    # Path B — family hypothesis-scan percentile CI
    "EMOS_ANALYTIC",         # EMOS analytic CI (licensed override)
    "SETTLEMENT_ISOTONIC",   # Re-grounded against realized settlement win-rate (K3)
]
```
The typed carrier `QlcbByDirection` (dict subclass) refuses bare-float assignment at write time.
`n_settlement_observations` and `coverage_ratio` travel with the value.

---

## Answers to Specific Questions

---

### (a) Is q_lcb built from OUT-OF-FOLD calibration residuals or the same in-sample posterior precision?

**RESOLVED-OK — for the qkernel spine (Path A).**

The mu-draw SE is `sigma_components.center_parameter_se_native` (src/forecast/sigma_authority.py).
This is the parameter SE of the fused multi-model ensemble center — the standard error of the
mean across the ~7-13 NWP models. It is NOT a fit-on-training-set posterior precision reused
in-sample; it is the **cross-model spread** (roughly: spread-of-forecasts / sqrt(n_members)).
This is a genuine OOF-equivalent uncertainty: the models are trained independently on different
data, and the cross-model disagreement is the live estimate of center uncertainty.

**CONFIRMED-ISSUE — for the legacy fused-center bootstrap (Path B).**

Path B uses `center_sigma_c = bayes_precision_fusion.fused.sd` (the posterior SD of the
Bayes-fused center estimate). This IS computed from model agreement at decision time — it is
the Bayesian posterior uncertainty of the center given the current model ensemble, NOT OOF
settlement residuals. The comment at line 1447 explicitly says "we do NOT re-add σ_resid here"
and the sigma_resid term (the OOF settlement residual spread) is omitted from the bootstrap
draw uncertainty. The bounds therefore capture only center-location uncertainty, not the full
out-of-sample predictive uncertainty — the ChatGPT consult's concern is partially valid for
Path B (legacy fused-center bootstrap), but NOT for Path A (qkernel spine JointQBand), which
uses the parameter posterior including a sigma draw that is floored at `realized_floor_native`
(the walk-forward realized settlement error — genuine OOF).

**Verdict by path:**
- Path A (qkernel spine, JointQBand): RESOLVED-OK — sigma floor is realized OOF settlement error.
- Path B (legacy fused-center bootstrap, replacement_0_1): CONFIRMED-ISSUE — center-only draws omit OOF σ_resid.

---

### (b) Is there any clip/floor/nonstandard transform that would BREAK the monotonicity assumption?

**CONFIRMED-ISSUE — for Path B (fused-center bootstrap); RESOLVED-OK for Path A (qkernel spine).**

**Path A (qkernel spine):** No clip on the q_lcb array itself. The only clips are:
1. `q_k = np.clip(probs, 0.0, None)` inside `build_joint_q` — clips negative masses (never
   occurs for a valid Gaussian CDF difference). This is structurally sound.
2. `samples[k,:] = build_joint_q(pd_k, omega).q` — every row is renormalized (q/q.sum()) before
   stacking. q_lcb = `np.quantile(samples, 0.05, axis=0)` over simplex rows — no post-hoc clip.
3. `assert_valid()` checks invariants but does NOT renormalize; it would raise, not silently clip.

Result for Path A: monotonicity is preserved — a better-calibrated bin probability (higher q)
strictly translates to a higher q_lcb (the quantile of the coherent simplex draws shifts up).

**Path B (fused-center bootstrap, lines 1716–1717):**
```python
lcb = min(max(lcb, 0.0), max(q_pt, 0.0))
```
This clips q_lcb to `[0, q_point]`. If a draw shift (better calibration of the center) moves
q_point UP but the bootstrap 5th percentile does NOT move proportionally (because the draws are
UN-normalized rows and the 5th percentile is over raw bin masses), then q_lcb could be **held at
q_point** by the upper clip even as the true lower bound is above q_point. This breaks strict
monotonicity in the edge case where the percentile noise from unnormalized rows exceeds q_point.

Additionally, Path B uses `probs = np.clip(ndtr(z_high) - ndtr(z_low), 0.0, 1.0)` per-draw per-bin
(lines 1702), where rows are NOT normalized. This means draws near the bin shoulder accumulate
truncation mass at shoulder bins, and interior bins see lower raw mass than they should — the
known modal-collapse defect documented in `joint_q_band.py` lines 43–49.

The live decision layer (`event_reactor_adapter`, lines 8405–8434) reads q_lcb from the bundle
DB first, then falls back to Wilson (member-vote lower bound). The Wilson fallback is itself not
monotone with the Normal-predictive q — it is a binomial bound on AIFS member votes, a
fundamentally different quantity.

**Verdict:** Path B has two clips that can break strict monotonicity for near-modal bins; Path A
does not. The spine (Path A) is the RESOLVED-OK path. The reactor legacy (Path B) has
CONFIRMED-ISSUE for modal bins.

---

### (c) Does the LIVE trade decision price off the spine center (raw_model_forecasts, raw equal-weight) or off forecast_posteriors (EB-debiased T2)?

**RESOLVED-OK — qkernel spine uses raw_model_forecasts, zero de-bias.**

From `src/engine/qkernel_spine_bridge.py` (lines 159–175, 509–553):

```
# SINGLE TRUTH (legacy bias-maze strip 2026-06-17): the spine center IS the raw precise
# multi-model fused center from raw_model_forecasts (~7-13 decorrelated NWP providers,
# latest cycle per model). There is NO settlement-residual de-bias layer and NO bias flag.
# _spine_debias_authority is UNCONDITIONALLY the identity _NoOpDebiasAuthority:
# build_center / build_sigma run on the RAW fused members with ZERO shift.
```

The `_spine_debias_authority` function (line 168) unconditionally returns `_NoOpDebiasAuthority`
which applies **zero shift** (`aggregate_shift_native=0.0`, `per_member_shift_native=(0.0, ...)`).

The EMOS mu-offset correction (src/calibration/emos_q_builder.py lines 107–109) IS applied in
`build_emos_q` for the legacy replacement path (Path B), but the qkernel spine bypasses
`emos_q_builder.py` entirely — it calls `PredictiveDistributionBuilder` directly over the raw
multi-model members.

The `forecast_posteriors` / EB-debias (T2) path was DELETED per the wiring audit 2026-06-09
(commit ff7f33dd5b): "the per-city EB bias-correction of the center was DELETED — settlement-
refuted as a wrong-set over-correction." The materializer comment at line 1735 confirms this.

**Legacy reactor path (Path B):** reads from `raw_model_forecasts` for the member envelope
(source-corrected 2026-06-16 per the spine-source rewire), THEN applies EMOS calibration
(emos_q_builder.py) for mu-offset + sigma-floor. NOT EB-debiased T2.

**Verdict:**
- Qkernel spine: raw_model_forecasts, zero de-bias, raw equal-weight fused center. RESOLVED-OK.
- Legacy reactor: raw_model_forecasts members + EMOS calibration (not EB-T2). RESOLVED-OK.
- EB-debiased T2 path: DELETED (not live). No action required.

---

### (d) Are the bins used for q the same settlement-preimage bins as the market's winning_bin?

**RESOLVED-OK — bins are the per-city settlement-preimage bins, derived from the same contract.**

From `src/probability/joint_q.py` (lines 56–83) and `src/contracts/settlement_semantics.py`:

The bins in `OutcomeSpace.bins` are `OutcomeBin` objects each carrying `rounding_rule` from
`omega.resolution.rounding_rule`, which is the per-city settlement rule set at
`event_resolution_for_city` (the same rule that governs grading and winning_bin determination).

The integrator `bin_probability_settlement` uses `settlement_preimage_offsets(rounding_rule)`
to derive the preimage interval — the SAME single declarative source that settlement grading uses.

For Hong Kong (oracle_truncate): `[bin_label, bin_label + 1.0)` — asymmetric preimage.
For standard WMO cities (wmo_half_up): `[bin_label - 0.5, bin_label + 0.5)` — symmetric.

The bin_id keying in `build_joint_q` and the q_by_bin_id dict are derived from
`OutcomeBin.bin_id` which is the same stable hash used in the reactor's
`_candidate_bin_id(proof)` (qkernel_spine_bridge.py lines 278–291 — verbatim same hash inputs).

There is NO generic grid independent of the market's bin topology: `OutcomeSpace.validate()`
enforces MECE completeness and the bins come from `family.candidates` (the market's own bins).

**Verdict:** RESOLVED-OK. Bins for q are precisely the settlement-preimage bins of the market,
using the per-city rounding rule. A generic/mismatched grid is unconstructable.

---

## Summary Table

| Question | Verdict | Notes |
|---|---|---|
| (a) q_lcb from OOF calibration residuals? | Path A: RESOLVED-OK; Path B: CONFIRMED-ISSUE | Spine uses realized floor (OOF); legacy bootstrap uses in-sample fused.sd (center-only) |
| (b) Clips/transforms that break monotonicity? | Path A: RESOLVED-OK; Path B: CONFIRMED-ISSUE | Legacy bootstrap clips q_lcb<=q_point over unnormalized rows; spine has no such clip |
| (c) Decision prices off raw_model_forecasts or EB-T2? | RESOLVED-OK | Spine: raw_model_forecasts, zero de-bias. EB-T2 DELETED. Legacy: raw members + EMOS calibration |
| (d) q bins == settlement-preimage bins? | RESOLVED-OK | Per-city rounding rule threaded from single contract; MECE validated |

---

## Key File:Line References

- Bin integration entry point: `src/probability/joint_q.py:217` (`build_joint_q`)
- Settlement preimage formula: `src/calibration/emos.py:580` (`bin_probability_settlement`)
- Settlement preimage contract: `src/contracts/settlement_semantics.py:57` (`settlement_preimage_offsets`)
- JointQBand (path A q_lcb): `src/probability/joint_q_band.py:363` (`build_joint_q_band`)
- Path A q_lcb formula: `src/probability/joint_q_band.py:429` (`q_lcb = np.quantile(samples, alpha, axis=0)`)
- Path B q_lcb (fused bootstrap): `src/data/replacement_forecast_materializer.py:1704` (`q_lcb_vec = np.percentile(probs, 5.0, axis=0)`)
- Path B defensive clip: `src/data/replacement_forecast_materializer.py:1717` (`lcb = min(max(lcb, 0.0), max(q_pt, 0.0))`)
- Spine de-bias identity: `src/engine/qkernel_spine_bridge.py:168` (`_spine_debias_authority`)
- Raw members source: `src/engine/qkernel_spine_bridge.py:509` (`build_fresh_model_set`)
- EB-debias deletion note: `src/data/replacement_forecast_materializer.py:1735`
- QlcbProvenance carrier: `src/calibration/qlcb_provenance.py:50` (`QlcbProvenance`)
