# EB Shrinkage Residual Test — Does κ=8.0 Under-Correction Explain the ~0.3–0.4°C Center Bias?

Investigator: read-only agent, 2026-07-19. DB access via `sqlite3 -readonly` /
`file:...?mode=ro` against `state/zeus-forecasts.db` only. No writes, no code
edits.

## Verdict up front

**REFUTED — not by weak correlation, but because the mechanism under test does
not exist on the live route.** The walk-forward EB shrinkage formula named in
`docs/authority/replacement_final_form_2026_06_09.md` §1a
(`b̂_s = λ_s·r̄_s + (1−λ_s)·0`, `λ_s = n_s/(n_s+κ)`, `κ=8.0`) is implemented as
`eb_bias()` at `src/forecast/bayes_precision_fusion.py:68-79` but **has zero
call sites anywhere in `src/`** (`grep -rn "eb_bias(" src` returns only its own
`def` line). It is dead code. The center-construction paths that actually
produce the served `forecast_posteriors.anchor_value_c` (μ*) — both the T2
Bayes-precision fusion capture (`src/data/bayes_precision_fusion_capture.py`)
and the currently-dominant fixed-weight source-clock scheme
(`src/strategy/live_inference/source_clock_city_weights.py`, method
`SOURCE_CLOCK_FIXED_WEIGHT`) — consume **raw, uncorrected instrument values**
(`z = x`, not `z = x − b̂`). This is a deliberate, tested, operator-ratified
change ("RAW NO-DE-BIAS LAW", 2026-06-18, commit series `raw-fusion S1`–`S7`,
esp. `5c084023b` "unify exit/monitor belief onto RAW" and `a4fa0a16d`
"replace T2 BLUE center with RAW diagonal in forecast_posteriors"), not a bug.
Since `λ_s` is never computed and `b̂_s` is never subtracted from anything that
reaches μ*, there is no `(1−λ_s)` fraction left over to correlate against —
the residual bias **is** the raw, 100%-uncorrected instrument bias, for every
instrument regardless of `n_s`. The predicted signature (bias shrinking with
`n_s`) is empirically absent for exactly this reason, confirmed below.

**No parameter change is possible here** — `κ` (and the walk-forward window)
are inert with respect to the served center on the live route; changing them
would have zero effect on `anchor_value_c`. Recommending a κ retune would
mean recommending re-introducing a center de-bias the operator explicitly
removed. §5 states this and names the actual (out-of-scope-for-this-mandate)
options.

---

## 1. Locate the de-bias implementation

**The formula exists, is well-documented, and is dead:**

- `src/forecast/bayes_precision_fusion.py:51` — `KAPPA = 8.0` ("EB shrink:
  lam = n/(n+kappa)").
- `src/forecast/bayes_precision_fusion.py:68-79` — `eb_bias(resids,
  parent_bias)`: `rbar = mean(resids); lam = n/(n+KAPPA); return lam*rbar +
  (1-lam)*parent_bias`. Exactly the formula in the authority doc.
- `grep -rn "eb_bias(" src --include="*.py"` → **one hit, the `def` line
  itself.** No caller in `src/` (production or otherwise) invokes it.

**What actually feeds the live μ*:**

- `src/data/bayes_precision_fusion_capture.py:44-50` — the import block
  explicitly does **not** import `eb_bias`, with an inline note: `# NOTE
  (single-serving-rule §4): eb_bias is deliberately NOT imported — the
  consumed posterior center is RAW (z = x), so the EB shift primitive must
  never reach this path.`
- `src/data/bayes_precision_fusion_capture.py:321-341` —
  `_raw_instrument(model, raw_value, history, parent_bias)` returns `(z =
  raw_value, n_train)` unconditionally; `parent_bias` is accepted only to
  keep the call signature byte-stable and is discarded (`del parent_bias  #
  RAW law: no de-bias shift on the consumed center.`). Docstring: "under the
  operator RAW no-de-bias law the consumed-posterior instrument center is the
  RAW model value `z = x` — NOT the EB-corrected `z = x − b̂`... The
  walk-forward `history` is RETAINED but consumed ONLY for width / provenance
  ... it NEVER shifts the center."
- `src/data/replacement_forecast_materializer.py:3769-3784` — the ANCHOR side
  is pinned the same way: `bias_shift_c: float | None = None` is hardcoded
  (comment: "RAW NO-DE-BIAS LAW (2026-06-18 ... operator 'NO fitted forward
  per-city de-bias'); ... It is forced to None here (fail-closed) ...
  `anchor_value_corrected_c == raw_anchor_value_c` (zero shift)"). The
  preceding comment block documents that a per-city EB correction was tried
  and **deleted** on 2026-06-12 ("Wave-2 item 7... settlement-refuted as a
  wrong-set over-correction... fit on the thin live single_runs anchor... net
  WORSE per `percity_corrected_oos.md`").
- **Live route in current provenance is not even the T2 Bayes fusion** —
  every posterior row sampled from the last few days carries
  `method: "SOURCE_CLOCK_FIXED_WEIGHT"` (set at
  `src/data/replacement_forecast_materializer.py:2759`), a per-city fixed
  linear combination of raw sources
  (`src/strategy/live_inference/source_clock_city_weights.py`, artifact
  `grid_aware_retest_20260625` / walk-forward-refit `city_weights_*.json`).
  This scheme computes `mu_c` as a weighted mean of raw `forecast_value_c`
  values with **no bias term at all** — not even the `(1−λ)` degenerate case.

**Granularity of "instrument" `s`:** per (city, metric, model) — i.e. one
`ModelInstrument` per model per city per metric, NOT per family. `n_s` =
`n_train` = count of walk-forward residuals for that exact (city, metric,
model, lead-bucket) from `ModelHistory.n_train`
(`bayes_precision_fusion_capture.py:190-191`, threaded from the injected
`history_provider`).

**Persistence:** `n_train`/`λ_s`/`b̂_s` are **not persisted** in
`forecast_posteriors.provenance_json` for the live route. Sampled a current
row's full `bayes_precision_fusion` provenance block (`current_evidence_shape`,
`current_value_serving`, `source_clock_one_scheme`, `used_models`,
`dropped_models`, `emos_center_a/b`, etc.) — no `n_train`, `lambda`, `b_hat`,
or `rbar` field exists anywhere in it. This is consistent with the code
finding: there is nothing to persist because nothing is computed.

**Regression test pinning this as intentional, not accidental:**
`tests/test_raw_unify_forecast_posteriors.py` — builds a synthetic instrument
with a deep walk-forward history (`n=40 >= MIN_TRAIN`, so under the OLD EB
formula `λ≈40/48≈0.83`) carrying a **+3.0°C systematic warm bias**, and
asserts:
- `test_eb_bias_primitive_not_imported_into_capture` — `eb_bias` is not even
  importable from the capture module namespace.
- the instrument's `z` equals the **raw** +3.0°C-biased value, not the
  EB-shrunk (~lam-weighted) one — i.e. the OLD formula would have left the
  center ~0.51°C off (`(1−0.83)·3.0`); the RAW law leaves it the full 3.0°C
  off, by design, and the test fails (RED) if `eb_bias` is ever reimported.

## 2–3. Per-instrument reconstruction and the correlation test

Since `b̂_s`/`λ_s` are not computed on the live route, there is no `(1−λ_s)`
quantity to regress the residual against — plugging the *documented* formula
into the *actual* served center is a category error (the formula is not in
that data path). The honest test is: does the residual bias shrink with `n_s`
the way partial-shrinkage predicts? It should not, and does not:

Raw single-model day-ahead (`lead_days=1`, `endpoint='single_runs'`) bias for
`ecmwf_ifs` against `settlement_outcomes` (VERIFIED, unit-converted to °C),
**with no correction applied at all** — this is the true `r̄_s` for one
instrument at essentially its full available `n_s`:

| metric | n | mean bias (y − x, °C) | MAE |
|---|---|---|---|
| high | 6480 | **+0.488** | 1.351 |
| low | 1021 | **+0.422** | 1.002 |

`n_s=6480` is enormous — under the documented formula `λ_s = 6480/6488 ≈
0.9988`, i.e. **99.88% shrinkage toward zero**, predicting a served residual
of `(1−λ_s)·0.488 ≈ 0.0006°C` for this instrument specifically. The actual
measured residual bias on the served, multi-instrument μ* for `high` is
**+0.340 to +0.364°C** (curve-collapse dialectic §2, `semantics_revision`
legacy and live rows alike) — i.e. essentially the **full, un-shrunk**
magnitude of a single raw model's own bias survives into the combined center,
not the ~0.06% a working EB shrink at this `n_s` would leave. This is the
signature of **zero correction**, not **99.88% correction with a residual**.
Consistent across metrics: `low` raw `ecmwf_ifs` (deterministic, `n=1021`,
`λ≈0.9922`) carries +0.422°C in this crude single-model cut (sign convention:
forecast too cold on average); the *served* μ* residual for `low` on the live
route is −0.34 to −0.65°C (opposite sign, reflecting the different multi-model
mix used for `low`), which is exactly what you'd expect from a fixed-weight
combination of several **raw, uncorrected** instruments with heterogeneous own
biases — not from a mis-calibrated shrink toward zero of one number.

**Direct check "does bias shrink with n_s as predicted": REFUTED.** Both
`ecmwf_ifs` (`n_s` in the thousands) and any thin regional/global with `n_s`
near `MIN_TRAIN=25` carry their **full own raw bias** into the fusion/weighted
center with no attenuation — `n_s` cannot suppress a bias that no code path
ever multiplies by `(1−λ_s)`. The near-constant ~0.3–0.4°C residual across
`semantics_revision` cohorts spanning legacy (pre-2026-06-18, when the old
per-city EB correction had already been deleted per Wave-2 item 7) through
the current live route (post-RAW-unify) is the same observation from a
different angle: the magnitude never moved because no shrink mechanism has
touched the served center since at least 2026-06-12.

## 4. Counterfactual κ sweep

**Not meaningful to run.** `κ` only parameterizes `eb_bias()`, which is called
nowhere in the path from raw instrument to `forecast_posteriors.anchor_value_c`
on either the T2-fusion route or the (currently dominant)
`SOURCE_CLOCK_FIXED_WEIGHT` route. Sweeping `κ ∈ {2,4,8,16}` inside
`bayes_precision_fusion.py` would change the output of a pure function that
nothing downstream reads. Any counterfactual "what would residual bias have
been under κ=X" is answerable only as a hypothetical re-introduction of a
deleted mechanism, which is a design decision (whether to bring EB center
de-bias back at all, and at what κ) — not a parameter-tuning question inside
the current architecture. Doing that math would misrepresent an architectural
proposal as a parameter fit.

## 5. Fix specification

**No one-parameter fix exists inside the current architecture** — there is no
`κ` or window-length knob currently wired to the served center. Two honest
paths forward, both bigger than a parameter change and outside a "confirm the
κ hypothesis" mandate:

1. **Leave RAW as-is (operator's ratified position, 2026-06-18).** The
   residual ~0.3–0.4°C is then a known, accepted property of raw NWP center
   values, to be handled downstream — exactly what
   `docs/evidence/capital_efficiency_2026_07_19/highq_overconfidence.md` §5
   already does (walk-forward q-shrink in `settlement_coverage_hierarchy.py`,
   correcting **q** against settled history downstream of μ*, never μ* itself).
   `curve_collapse_dialectic.md` §7 already recommends this and nothing here
   changes that recommendation.
2. **If the operator wants to re-open the RAW law:** re-introduce a
   *validated* center de-bias — the 2026-06-12 deletion (Wave-2 item 7) and
   the 2026-06-18 RAW unify were themselves responses to a per-city EB
   correction that was measured to be **net worse** (`percity_corrected_oos.md`)
   when fit on thin per-city history. Any revival would need the same
   walk-forward, no-look-ahead, do-no-harm gate `debias_authority.py` already
   specifies for a *different* (qkernel spine) surface (`N_SIGMA_BIAS=2.0`
   magnitude band, `MIN_N=30`, `CRPS_TOLERANCE=0.02` no-harm gate) — i.e. the
   in-repo blueprint for "how to safely re-admit a center shift" already
   exists, just not wired to this μ* path. This is a build task, not a
   κ retune.

**Invariant test worth adding regardless of the above:** a provenance
assertion that `forecast_posteriors.anchor_value_c` bias tracks raw
model-mix bias with **zero attenuation from n_s** as long as the RAW law
holds — i.e. a regression test (parallel to
`tests/test_raw_unify_forecast_posteriors.py`) that fails loudly the moment
any center-construction path (T2 fusion, fixed-weight scheme, or a future
one) starts importing `eb_bias` or otherwise subtracting a per-instrument
walk-forward residual from an instrument's `z` before it reaches
`anchor_value_c`, unless that path is explicitly gated through
`DebiasAuthority`-style validation. This turns "no silent second de-bias
surface" (already a stated goal in `debias_authority.py`'s docstring) into an
enforced invariant for the μ* path specifically, which today has no such
guard of its own (only the qkernel spine entry does, via `_NoOpDebiasAuthority`).

## Caveats

- The raw `ecmwf_ifs` single-model cut in §2–3 mixes lead/time-of-day the same
  way `curve_collapse_dialectic.md` §8 flags for its own Jensen test; it is
  used here only to establish an order-of-magnitude sanity check ("a single
  raw model's own bias is comparable to the served residual"), not as a
  precise per-instrument decomposition of μ* (which is not reconstructable
  from persisted provenance since the fixed-weight scheme's own inputs are
  not separately walk-forward-residual-tagged in `forecast_posteriors`).
- This does not re-litigate whether RAW-no-debias is the right law — that
  question was already decided and tested (2026-06-12 deletion,
  2026-06-18 unify, `test_raw_unify_forecast_posteriors.py`). This report
  only establishes that the κ=8.0 EB-shrinkage mechanism named in the
  mandate's hypothesis is not present in the code path that produces the
  measured residual, so it cannot be its cause, partial or otherwise.
