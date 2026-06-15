# Created: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md (PredictiveDistribution block
#   lines 344-365; [BLOCKER] forecast-authority-split lines 23, 28; sigma-missing
#   fallback lines 426-430; Stage 3 block lines 1072-1090) reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD ONLY).

# Stage 3/PD — predictive_distribution_builder implementation report

## What was built

The **single live builder** that assembles the four already-built forecast-spine
authorities into ONE `PredictiveDistribution` — the only input to q. It replaces
the [BLOCKER] forecast-authority split (spec lines 23, 28): the pre-rebuild reactor
had an EMOS lane (`build_emos_q` from the event-snapshot path) AND a fallback/day0
lane (`_maybe_apply_edli_bias_correction`) that could produce DIFFERENT mu*/sigma/q
semantics and DIFFERENT receipts for the same family. There is now ONE assembly, so
every live path returns the SAME receipt contract.

Assembly order (each authority is the single owner of its decision):

```
DebiasAuthority.apply  (ONCE, inside build_center)  -> debiased member values
    -> CenterEstimate   (envelope-enforced)         -> mu* in [min,max] debiased
    -> Day0Conditioning (may license leaving)       -> support-corrected center
    -> SigmaComponents  (realized-floored)          -> served sigma (or ineligible)
```

`distribution_family in {NORMAL, DAY0_HIGH_MAX_NORMAL, DAY0_LOW_MIN_NORMAL}`.
`live_eligible=False` with `ineligibility_reason` when the σ authority is missing
(or the center is REFUSED). Every live path returns a `PredictiveDistribution` with
a populated `identity_hash` and every provenance sub-object — eligible or not.

## Files written (new only; no live file touched)

- `src/forecast/predictive_distribution_builder.py`
  - `PredictiveDistribution` (frozen dataclass) — EXACT spec field names (lines
    348-365): `case, mu_native, sigma_native, debiased_members_native,
    member_min_native, member_max_native, center, debias, day0, sigma_components,
    distribution_family, live_eligible, ineligibility_reason, identity_hash`.
  - `PredictiveDistributionBuilder` (holds the one `DebiasAuthority`); public
    method `build(case, models, obs=None, *, use_emos=True, fused_center_sd_native,
    sigma_resid_native, has_fusion_capture=True) -> PredictiveDistribution`.
  - `build_predictive_distribution(...)` — module-level one-shot wrapper.
  - Helpers: `_identity_hash` (receipt anchor, computed on EVERY path),
    `_distribution_family`, `_empty_sigma_components`, `_no_obs`.
- `tests/forecast/test_single_predictive_distribution_authority.py`
  - `test_every_live_path_returns_same_receipt_contract`
  - `test_mu_star_cannot_select_tokyo_26_when_fresh_members_are_20_to_23`
  - `test_pd_live_eligible_false_when_sigma_authority_missing`
  - (+ helper `_assert_full_receipt_contract`, plus a contrast-control assertion in
    each test proving the broken behavior would fail.)

## Symbols consumed (imported and assembled; all already built)

- `src/forecast/center.py`: `CenterEstimate`, `build_center`
- `src/forecast/day0_conditioner.py`: `Day0Conditioning`, `Day0ObservationState`,
  `condition_day0`
- `src/forecast/debias_authority.py`: `AppliedDebias`, `DebiasAuthority`
- `src/forecast/sigma_authority.py`: `SigmaComponents`, `SigmaDecision`, `build_sigma`
- `src/forecast/types.py`: `ForecastCase`, `FreshModelSet`

## How the headline invariant is implemented (transformation, not a gate)

`mu* cannot select Tokyo 26 when the fresh debiased members are 20-23` holds by the
ASSEMBLY ORDER, not a downstream clamp. The only two things that set `mu_native`:

1. `CenterEstimate.mu_native` from `build_center`, PROVEN by the center module to
   lie in `[debiased_member_min_native, debiased_member_max_native]` (it constructs
   the center as a convex combination of the debiased members and falls back to the
   in-envelope consensus when EMOS proposes outside). So absent day0, 26 is
   unreachable for 20-23 members.
2. `Day0Conditioning.center_after_native` — but ONLY when an observed running
   extreme was actually resolved (`day0.active`). For a HIGH market it is
   `max(center_before, observed_high)`: the center may leave the envelope UPWARD
   only toward `observed_high`, a value that was physically measured today. With no
   day0 observation the conditioner returns the identity transform
   (`center_after == center_before`), so the envelope-enforced center is served.

Test 2 proves all three faces: (a) no obs -> mu* stays in [20,23], `ENVELOPE_FALLBACK`,
26 unreachable; (b) observed high 24.0 -> mu* clamps to 24.0 (the observed extreme),
NOT the EMOS-invented 26; (c) a stale −4.847 EDLI-style artifact is `MAGNITUDE_REFUSED`
by DebiasAuthority so it never shifts the members.

## Live eligibility (spec lines 426-430)

`build_sigma`'s `live_eligible` IS the gate. When it returns `live_eligible=False`
(no fusion capture AND no realized floor -> `PREDICTIVE_SIGMA_AUTHORITY_MISSING`),
the builder propagates `live_eligible=False` + that reason and serves `sigma=0.0` —
no width-less member-vote q. A REFUSED center (no fresh members) is likewise
`live_eligible=False` (`CENTER_REFUSED: ...`). Both still carry the full receipt
contract (identity_hash present). Test 3 + Test 1 PATH C/D prove this.

## Drift resolved and how

This module is GREENFIELD (drift ledger: no live edits — it is wired into the reactor
at integration/Wave 5, not now). The dependency types were already reconciled to live
in their own stages; this builder consumes them as-is. Specific resolutions:

- **`build_center` does not return the debiased member vector.** The spec
  `PredictiveDistribution` needs `debiased_members_native`. Resolved toward the live
  `center.py` interface (it returns only `CenterEstimate`, which carries the debiased
  envelope min/max but not the per-member vector). The builder recovers the vector by
  re-calling `DebiasAuthority.apply(case, models)` — which is deterministic and pure
  (no I/O, no mutation; verified in `debias_authority.py`), so it is the SAME single
  de-bias decision the center used, recovered for the receipt and the debiased-member
  tuple. There is still exactly ONE de-bias decision; no second center-shift surface
  is introduced.
- **`condition_day0` is keyword-only and takes `metric/obs/center_before_native`,
  not a predictive object.** Resolved by passing `center.mu_native` as
  `center_before_native` and the case `metric`; the served `mu_native` is
  `day0.center_after_native` (== `center.mu_native` when day0 inactive). This keeps
  day0 the sole owner of the envelope-leaving license.
- **`build_sigma` returns a `SigmaDecision` wrapper** (live shape) rather than a bare
  `SigmaComponents` the spec dataclass field implies. Resolved: the PD stores
  `sigma_decision.components` in `sigma_components` (the spec field) and reads
  `sigma_native` / `live_eligible` / `ineligibility_reason` from the decision wrapper.
- **REFUSED-center path needs a `SigmaComponents` even though `build_sigma` is not
  called** (no members to width). Resolved with a zeroed `_empty_sigma_components()`
  so the receipt contract field is always populated; `live_eligible=False`.
- **Empty-member `FreshModelSet` min/max.** The REFUSED path is reached with no
  members; the test fixture sets `min_native/max_native` to NaN for an empty set
  (matching `center.py`, which returns NaN envelope bounds on REFUSED). The builder
  never asserts on these for an ineligible distribution.

## Constraint compliance (operator law)

- **Corrected transformation, no detector.** The envelope invariant is owned by
  `center.py` (convex-combination construction + fallback) and the day0 license is
  owned by `day0_conditioner.py` (support transform toward an observed value). The
  builder does NOT add a gate/cap/clamp/sanity-check that catches a bad mu* — it
  composes authorities that make the bad value unreachable. 26 is not detected and
  rejected; it is never produced absent an observed extreme.
- **New files only.** No live file edited.
- **No commit / no git add by the agent** (orchestrator commits; self-merge script
  handles the worktree landing per the worktree protocol).

## Full test results

PD authority file (the 3 spec-named RED-on-revert tests):

```
tests/forecast/test_single_predictive_distribution_authority.py ...      [100%]
3 passed in 0.81s
```

Full forecast directory (3 new + 19 existing Stage 1/2/3/4/5 tests):

```
......................                                                   [100%]
22 passed in 0.84s
```

Money-path + live-inference (no regression; module is greenfield):

```
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
...........................................                              [100%]
331 passed in 4.21s
```
