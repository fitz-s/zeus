# Stage 3 — Center Builder + Envelope Invariant (implementation report)

**Module:** `stage3_center`
**Date:** 2026-06-14
**Worktree:** `/Users/leofitz/zeus/.claude/worktrees/qkernel-rebuild` (isolated; live daemon runs a different tree)

## Goal

μ\* tracks fresh debiased consensus. A served forecast center can never leave the
fresh debiased member envelope (Tokyo 26°C is mathematically impossible when every
fresh debiased member sits in 20–23°C), absent a day0 observation (the day0
envelope license is owned by the separate `day0_conditioner`, not this module).

## What I built

### `src/forecast/center.py` (NEW)

Symbols:

- `CenterEstimate` — frozen dataclass, EXACT field names from spec lines 224–234
  (`mu_native`, `raw_consensus_native`, `debiased_consensus_native`,
  `debiased_member_min_native`, `debiased_member_max_native`, `center_method`,
  `center_status`, `weights_by_model`, `reason`). `center_method` Literal
  `{"WEIGHTED_HUBER_CONSENSUS","SHRUNK_EMOS","RAW_FALLBACK"}`; `center_status`
  Literal `{"OK","ENVELOPE_FALLBACK","DAY0_CLAMPED","REFUSED"}` — verbatim.
- `walk_forward_model_weights(case, members) -> np.ndarray` — spec line 246. Returns
  non-negative weights summing to 1, shrunk toward equal by n/SE. Reuses the
  `bayes_precision_fusion` EB / inverse-variance primitives (`KAPPA`, `MIN_TRAIN`,
  `LOWN_INFLATE`, `SIGMA_FLOOR`) rather than reinventing the weighting math.
- `weighted_huber_location(values, weights) -> float` — spec line 247. Weighted
  Huber M-estimator (IRLS). Each step forms `sum(w_eff·x)/sum(w_eff)` with
  `w_eff >= 0`, so the estimate is a CONVEX COMBINATION of the member values and
  provably lies in `[min, max]`. This is the in-envelope anchor the invariant
  rests on. Final result is numerically pinned into the member hull so float drift
  can't escape by an ULP.
- `shrink(value, *, toward, strength) -> float` — spec line 254.
  `(1-s)·toward + s·value`, `s` clamped to `[0,1]`.
- `build_center(case, models, debias_authority, *, use_emos=True) -> CenterEstimate`
  — the center algorithm, spec lines 236–270, EXACTLY.
- Helpers `_emos_center`, `_emos_oos_strength`, `_weighted_quantile`.

The algorithm (spec lines 236–270):
1. Members are the fresh set for `case` (`models`).
2. `DebiasAuthority.apply(case, models)` ONCE → debiased member values.
3. `weights = walk_forward_model_weights(case, members)`;
   `mu_consensus = weighted_huber_location(debiased_values, weights)`.
4. Optional EMOS enters ONLY as a shrinkage residual:
   `mu_emos = emos_predictive(...)` (the `a + b·xbar` mean);
   `mu_candidate = shrink(mu_emos, toward=mu_consensus, strength=oos)`.
5. Envelope as a TRANSFORMATION (spec lines 256–268):
   `lo = min(debiased) ; hi = max(debiased)`;
   `if not lo <= mu_candidate <= hi: mu_candidate = mu_consensus ; center_status = "ENVELOPE_FALLBACK"`;
   `assert lo <= mu_candidate <= hi`.

### `tests/forecast/test_center_envelope.py` (NEW)

The three spec-named RED-on-revert tests plus two helper-property proofs:
- `test_mu_star_inside_debiased_member_envelope`
- `test_emos_slope_cannot_push_mu_outside_envelope`
- `test_tokyo_26_impossible_when_members_are_20_to_23`
- `test_weighted_huber_location_is_inside_value_hull` (convex-combination proof)
- `test_shrink_is_convex_combination_when_strength_in_unit_interval`

## Why this is the corrected TRANSFORMATION, not a gate/cap

The envelope is enforced by CONSTRUCTION, not by catching a bad value:

- `mu_consensus` is a weighted Huber location with non-negative weights summing to
  1 → a convex combination of the debiased member values → provably in `[lo, hi]`.
- EMOS can only ENTER as a shrink residual toward `mu_consensus`. If the shrunk
  candidate lands outside `[lo, hi]`, the ONLY value the transform can serve is
  `mu_consensus` (in-envelope by construction). An out-of-envelope μ is never a
  reachable output.
- The `assert lo <= mu_candidate <= hi` is a PROOF OBLIGATION (the construction
  must hold), not the enforcement mechanism — it fires only if a debiased member
  is non-finite or the consensus math regresses, which is a hard error, never a
  served center.

There is NO clamp/haircut/sanity-check that leaves a broken transform in place:
"EMOS becomes μ" is structurally removed; EMOS is demoted to a residual that must
re-pass the envelope proof (spec line 269).

## Spec lines implemented

- 224–234 — `CenterEstimate` dataclass (exact fields).
- 236–270 — the center algorithm (read fresh members → debias once → weighted
  Huber consensus → optional EMOS as shrink residual → envelope transform).
- 246–247 — `walk_forward_model_weights` + `weighted_huber_location` seam.
- 253–254 — `mu_emos = a + b·xbar`; `shrink(...)`.
- 256–268 — envelope enforcement (the `lo/hi`, ENVELOPE_FALLBACK, `assert`).
- 1072–1090 — Stage 3 file `src/forecast/center.py`, the three RED-on-revert test
  names, and the live signal `member_min <= mu <= member_max`.

`predictive_distribution_builder.py` (spec line 1079) was intentionally NOT built
— it is the separate follow-on step, and is not imported here (per the brief).

## Drift resolved (and how)

1. **GREENFIELD — no live edits.** Spec lines 1080–1082 list `modify
   src/calibration/emos.py usage` / `modify emos_q_builder.py`. Per the drift
   ledger ("Always prefer the Actual-live column"; this module is GREENFIELD,
   wired into the reactor later) I touched NO live file. `center.py` only IMPORTS
   `emos_predictive` (read-only reuse), never modifies it. Resolution: new files
   only; the live `emos_q_builder` wiring is the later integration step.

2. **Envelope is a TRANSFORMATION, not a cap.** Spec line 263 shows the fallback
   as an `if`-guard; the ledger directs that `mu_candidate` be CONSTRUCTED so the
   bound holds (ENVELOPE_FALLBACK to `mu_consensus`, terminated by `assert`).
   Resolved by making `mu_consensus` a provable convex combination (weighted
   Huber), so the fallback target is always in-envelope and the `assert` is a
   proof obligation, not a clamp.

3. **`walk_forward_model_weights` / `weighted_huber_location` / `shrink` have no
   live implementation** (grep: only `shrink_cov` exists in
   `bayes_precision_fusion`, which is covariance-specific and not reusable for
   scalar shrinkage). Resolved by implementing all three in `center.py`, reusing
   the `bayes_precision_fusion` EB / inverse-variance / low-n-inflation CONSTANTS
   and concept for the weights (no reinvention of the precision math).

4. **EMOS unit: cells are fit in °C; debiased values are in the settlement native
   unit (possibly °F).** Resolved by passing the debiased member array to
   `emos_predictive` and documenting the °C assumption at the `_emos_center` seam.
   This is SAFE for correctness of the envelope invariant regardless of unit: even
   a wrong-unit EMOS mean can only enter as a shrink residual and is re-proved
   against the envelope, so it can never produce an out-of-envelope μ. The default
   `emos_oos_strength` is `0.0` (EMOS contributes nothing unless a later contract
   supplies a fitted OOS strength), so no live μ depends on the EMOS unit today.
   The proper F-unit EMOS thread is an integration-step concern (the live
   `emos_q_builder` already owns unit handling upstream).

5. **`RawModelMember` carries no per-model walk-forward SE/n** (Stage-1 contract,
   `src/forecast/types.py`). Resolved: with no per-model precision signal the EB
   shrink collapses to EQUAL weights (the conservative shrink-to-equal posture the
   spec comment "shrink to equal weights by n/SE" names). `walk_forward_model_weights`
   is the single seam where a future `(se, n)` field makes precisions diverge;
   ANY non-negative weights summing to 1 preserve INV-C1 (convexity), so the
   envelope proof is independent of the weighting detail.

## Test results

### Stage 3 (`tests/forecast/test_center_envelope.py`)

```
.....                                                                    [100%]
5 passed in 0.79s
```

### RED-on-revert verification

I reverted the transform in a throwaway copy (removed the ENVELOPE_FALLBACK body,
weakened the `assert`, set `EMOS_OOS_STRENGTH_DEFAULT = 1.0` so EMOS becomes μ —
the broken design). Result: the three spec-named tests FAILED (the Tokyo case
produced exactly `mu_native=26.0` with members 20–23), the two helper-property
tests stayed green (unaffected by the envelope revert). Restored the correct file
(byte-identical to pre-revert) and re-ran: 5 passed. This confirms each spec test
is genuinely RED-on-revert.

### Money path (`tests/money_path tests/strategy/live_inference`)

```
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
...........................................                              [100%]
331 passed in 4.29s
```

No regressions — `center.py` is new-file-only and not wired into the reactor
(integration is a later stage), so the money path is unaffected as expected.

## Files written

- `src/forecast/center.py` (new)
- `tests/forecast/test_center_envelope.py` (new)
- `docs/rebuild/impl_stage3_center.md` (this report)
