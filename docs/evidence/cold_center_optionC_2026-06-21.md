# Cold-center genuine-alpha fix (Option C) — raw-precision representativeness center warming

- Created: 2026-06-21
- Last audited: 2026-06-21
- Authority basis: Option C raw-precision representativeness center warming
  (consult REQ-20260621-033315; forecast-gap-is-data-precision). Architect
  cross-check 2026-06-21 (Form A confirmed strictly superior; index-aligned
  channel confirmed mandatory; off-default flag rejected per operator no-shadow law).
- Branch: `live/cold-center-optionC-20260621` (based on `49206f0796`,
  #416 lifecycle/phase23 head — the lineage carrying the current materializer).
- Verdict: **OPTIONC_GREEN**
- Read-only on live DBs. NOT deployed. NOT merged into a live branch by this work.

---

## 1. What was built (the core)

The SERVED traded forecast center is `_mu_diagonal = Σ w_m·z_m` — a RAW second-moment-
weighted mean (NOT `fused.mu`, which has been discarded since METHOD-UNIFY 2026-06-18
`a4fa0a16d0`). It is written to `anchor_value_c` and consumed by the live replacement
authority. Option C threads per-model grid-representativeness variance `sigma_repr²`
into the RAW precision **denominator** that produces `_mu_diagonal`, at BOTH the
materializer EXIT seam and the spine ENTRY seam, through ONE shared helper.

**Superior form (Form A — floor the residual FIRST, then add representativeness):**

```
base_m2  = EB_shrunk_raw_second_moment (or equal-precision prior at cold-start)
denom_m  = max(base_m2, floor_m2) + repr_m2_m
w_m      = (1/denom_m) / Σ_j (1/denom_j)
mu_served = Σ w_m z_m
```

`floor_m2` is the minimum *residual* error variance (the OM grid→station floor,
`SIGMA_FLOOR=0.8` degC²·u). `repr_m2` is an INDEPENDENT *observation* variance
(`representativeness_variance.py` rule 5: `Sigma_source = Sigma_resid + sigma_repr²`).
Adding repr AFTER the floor is what makes a small-but-real repr penalty on a sub-floor
residual member still bite — Form B (`max(m2+repr, floor)`) would let the floor swallow
it. Form A also keeps the repr term at full strength through the low-n EB shrink (Form B,
fed pre-thread, dilutes repr toward `equal_m2` exactly where thin/cold cells need it most
— the architect's defect-2 finding).

---

## 2. Verified exact sites (file:line, this branch)

| Concern | File:line | What changed |
|---|---|---|
| Shared weight helper | `src/forecast/center.py:257` `raw_second_moment_weights` | Added `repr_m2_by_model` kwarg; Form-A denom `max(m2_eff,floor)+repr`; cold-start rule (positive repr is a signal even with no raw m2). `repr_m2_by_model=None` → byte-identical. |
| Shared center functional | `src/forecast/center.py:373` `raw_precision_center` | NEW: returns `(weights, mu_diagonal=Σwz)` — the single served-center functional called by BOTH seams (shared center, not only shared weights). |
| Spine consumer | `src/forecast/center.py:146` `walk_forward_model_weights` | Reads `RawModelMember.representativeness_m2_native`; same Form-A denom + cold-start. |
| Carrier | `src/forecast/types.py:104` `RawModelMember.representativeness_m2_native: float = 0.0` | New field, native unit², 0.0 default (byte-identical). |
| EXIT seam (materializer) | `src/data/replacement_forecast_materializer.py:1261-1267` | `_build_sigma_repr_by_model(request.city, models)` → `raw_precision_center(..., repr_m2_by_model=...)`. degC² repr matches degC² `raw_m2` basis. |
| EXIT repr builder | `src/data/replacement_forecast_materializer.py:886` `_build_sigma_repr_by_model` | Fail-soft loader read; absent city/model → 0.0 → byte-identical. |
| EXIT provenance | `src/data/replacement_forecast_materializer.py:1278-1300, 2255` | `precision_center_basis` (per-model raw_m2/n/repr_m2/weight) + `precision_basis_hash`; persisted as `bayes_precision_fusion_precision_basis_hash`. |
| ENTRY producer (spine) | `src/engine/event_reactor_adapter.py:12878` `_repr_native_for` + `:12897` | Computes `sigma_repr_sq_for(family.city, model)` × `_c2_to_native_var` → 4-tuple `(model, m2_native, n, repr_native)` in `precision_by_index`. Model name + city in scope HERE (lost downstream). |
| ENTRY payload stash | `src/engine/event_reactor_adapter.py:9141` | `_edli_spine_repr_m2_by_index` (index-aligned, native²). |
| ENTRY lift | `src/engine/qkernel_spine_bridge.py:468,477` | Lifts `repr_m2_by_index` into the served inputs. |
| ENTRY → member | `src/engine/qkernel_spine_bridge.py:602,624` `_member_repr` | Sets `RawModelMember.representativeness_m2_native` by index. |
| Test registration | `architecture/test_topology.yaml` | 4 new test files registered in `trusted_tests`. |

**Why the spec's "edit center.py as the ENTRY seam" was insufficient (verified myself):**
`center.py:walk_forward_model_weights` is a pure CONSUMER of a pre-threaded scalar; the
downstream `RawModelMember.model_id = f"reactor_served_{i}"` (`qkernel_spine_bridge.py:593`)
LOSES the model name, so `sigma_repr_sq_for(city, model)` cannot be called there. Repr is
therefore computed at the real producer (`event_reactor_adapter.py:12860`, where the model
name + `family.city` are in scope) and carried by index — NOT folded into the raw m2
(folding pre-thread would subject repr to the floor AND the EB shrink). Architect agreed:
the separate index-aligned channel is mandatory.

---

## 3. Shared-helper design as built

`raw_precision_center(raw_m2_and_n, z_by_model, *, unit, repr_m2_by_model)` is the single
served-center functional. Both seams call it:
- EXIT: `_raw_center(_raw_m2_and_n, _z_by_model, unit=_serving_unit, repr_m2_by_model=_sigma_repr_by_model)`.
- ENTRY: the spine builds `RawModelMember`s (with `representativeness_m2_native`), and
  `walk_forward_model_weights` applies the IDENTICAL Form-A denom; the served center is the
  arithmetic `Σ w·z` (the materializer's functional), proven byte-equal by the parity test
  including an outlier fixture (Huber-vs-Σwz divergence guard) — so the #135 two-center
  split cannot reopen through either the weights OR the center functional.

**Unit basis:** repr is supplied in the SAME unit² basis as `raw_m2` by each caller
(EXIT: degC²; ENTRY: native² via `_c2_to_native_var`). The helper does no scaling — exactly
how `raw_m2` itself is handled — so the add is unit-consistent at both seams. C/F weight
invariance is asserted by test.

**Cold-start:** "no signal" is now `no raw m2 AND no positive finite repr` → exact equal
1/n. A no-history member with positive repr uses `equal_m2 + repr` as its instrument
variance, so a geometry-derived penalty warps even thin/cold cells (the architect's rec #3;
the old `have_any_signal` early-return would otherwise have suppressed cold-start warming).

---

## 4. Measured `_mu_diagonal` warming (hot-city fixture, real grid data)

Real `config/grid_representativeness.json` (54 cities present), Austin:
- near member `ncep_nbm_conus` sigma_repr² = 0.278 degC² (fine, near airport)
- far member `jma_seamless`   sigma_repr² = 29.272 degC² (coarse/distant cell)
- equal raw_m2/n, cold-far symptom z: near=33.0°C, far=31.0°C

```
mu_base = 32.0000°C   (no repr — equal weights)
mu_warm = 32.9190°C   (repr down-weights the cold far cell)
warming = +0.9190°C   (toward the warm fine cell; stays inside [31, 33] envelope)
```

Spine ENTRY end-to-end smoke (members 28/31°C, repr 4.0/0.0 degC², equal raw_m2):
`mu_base=29.5000 → mu_warm=30.5000, warming=+1.0000°C`, repr correctly threaded onto
`RawModelMember.representativeness_m2_native=[4.0, 0.0]`.

The warming is convex reallocation toward closer/finer (warmer) members — no bias offset,
no invented value (μ stays in `[min z, max z]`). Missing grid cell → repr=0.0 → byte-
identical to today.

---

## 5. predictive_sigma_c / Kelly unchanged by repr — **YES (confirmed)**

`predictive_sigma_c = max(1.0, sqrt(fused.sd² + σ_resid²))`
(`replacement_forecast_materializer.py:1397`) and `anchor_sigma_c = fused.sd` (`:1411`)
depend ONLY on `fuse_bayes_precision_posterior` (`fused.sd`) + the common-date residual
series (`_sigma_resid`). Repr enters `raw_precision_center` (the MEAN) ONLY — it never
reaches `fuse_bayes_precision_posterior`, never widens `fused.sd`, never enters
`predictive_sigma_c`. The capture WIDTH switch `apply_grid_representativeness` (which DOES
feed `ModelInstrument.sigma_repr_sq → Bayes covariance → fused.sd → predictive_sigma_c`,
the double-count seam) stays default-False and is NOT enabled by this fix.

Guard tests assert: (a) `predictive_sigma_c` / `anchor_sigma_c` source lines never
reference the repr channel; (b) the repr dict is never passed to
`fuse_bayes_precision_posterior`; (c) `apply_grid_representativeness` default stays False.
**predictive_sigma_c unchanged by repr: yes.**

---

## 6. Coherence-guard result (#135 single served center)

`tests/forecast/test_method_unify_center_coherence.py`: **8 passed** (unchanged — repr
defaults make the shared helper byte-identical to the spine for repr-free inputs).
`tests/forecast/test_method_unify_representativeness_parity.py` extends the guard WITH repr:
ENTRY == EXIT on weights AND center, incl. low-n, cold-start, and an outlier center-
functional fixture. No two-center split.

---

## 7. RED→GREEN test tail (consult §57-75 contract — all built RED-first)

RED proof (pre-implementation): `ImportError: cannot import name 'raw_precision_center'`
and signature mismatch on `raw_second_moment_weights` (helper did not exist / lacked the
repr kwarg). GREEN after implementation:

```
tests/forecast/test_raw_second_moment_representativeness.py ........... 19 passed
  (backward-compat byte-identical; direction; magnitude vs independent Form-A ref;
   no-invention equal-z/warmer-far-cools/envelope; cold-start positive-repr-breaks-equal
   & none→equal; low-n repr-after-EB-shrink & sub-floor-repr-not-swallowed [Form A vs B];
   C/F invariance; raw_precision_center (weights, mu) contract)
tests/data/test_replacement_materializer_representativeness_center.py .. 6 passed
  (_build_sigma_repr_by_model fail-soft + known-city positive; EXIT center warms;
   absent repr byte-identical; warming magnitude on hot-city fixture)
tests/forecast/test_method_unify_representativeness_parity.py ......... 5 passed
  (ENTRY==EXIT weights & center; low-n; cold-start; outlier Huber-vs-Σwz divergence)
tests/probability/test_representativeness_no_kelly_variance_double_count.py 6 passed
  (repr is mean-only; predictive_sigma_c/anchor_sigma_c never reference repr; repr dict
   never fed to fuse_bayes_precision_posterior; apply_grid_representativeness stays False)

Combined Option C suite: 36 passed.  + coherence guard 8 passed = 44 passed.
```

---

## 8. No-regression suite tail

```
tests/forecast/  (full)                                   67 passed
```

Broader affected selection (`-k 'bayes_precision or replacement or center or sigma or q_lcb'`
across `tests/`) — before/after with my work stashed are IDENTICAL:

```
BEFORE (clean base 49206f0796):  92 failed, 587 passed, 2 skipped, 6 errors
AFTER  (Option C):               92 failed, 587 passed, 2 skipped, 6 errors
```

**Zero new failures, zero new errors, zero new lint findings introduced by Option C.**
The 92 pre-existing failures + 6 collection errors are a lineage mismatch on the
`49206f0796` (phase23) base — top-level tests referencing materializer internals
(`_replacement_member_vote_smoothing_alpha`, `_QLCB_SOFT_ANCHOR_BASIS`, `TRADE_AUTHORITY_FLAG`,
a `ReplacementForecastMaterializeRequest` kwarg) that do not exist on this lineage. Verified
present with Option C stashed → not this work's regression. ruff: my added code is clean
(`center.py` all checks pass; `types.py` clean; my added line ranges in
`event_reactor_adapter.py` clean); the 1+2 ruff findings in the touched files are pre-existing
(identical on the clean base).

---

## 9. Law compliance

- **Single-truth / no-debias:** diagonal precision reweighting ONLY. No bias-offset, no hand
  weights, no de-bias layer, no shadow/parallel center. μ stays a convex combination inside
  `[min z, max z]` (INV-C1 preserved).
- **No shadow / live-direct:** NO code flag. Warming is live wherever the grid table has a
  cell; rollout is controlled by populating `config/grid_representativeness.json` + the deploy
  commit (operator no-shadow law; architect concurred the off-default flag was wrong).
- **Missing data byte-identical:** loader fail-soft → repr=0.0 → identical to today.
- **Kelly decouple:** repr in the MEAN weights only; `predictive_sigma_c`/Kelly σ untouched.
- **Provenance:** per-model precision basis + `precision_basis_hash` persisted (§7).
- **NOT deployed, NOT merged to a live branch.**
