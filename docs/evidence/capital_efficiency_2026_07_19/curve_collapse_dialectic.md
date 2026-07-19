# Curve-Collapse Dialectic — Jensen Hypothesis Test on the High-q Center Bias

Investigator: read-only agent, 2026-07-19. All DB access via read-only sqlite3
`file:...?mode=ro` against `state/zeus-forecasts.db`. No writes, no code edits.
Scripts at `/private/tmp/claude-501/-Users-leofitz-zeus/7589dc75-d443-4b7f-8e2b-24945ef3038c/scratchpad/{jensen_test,jensen_test2,emos_check,tail_test}.py`.

## Verdict up front

**The central hypothesis (Jensen on the max functional: `E[max(X_t)] ≥ max(E[X_t])`,
i.e. the served center is biased because it collapses an ensemble-**mean** curve
to its extreme instead of averaging per-member extremes) is REFUTED by direct
data test, not confirmed.** The literal mean-of-member-daily-maxima quantity
(`ensemble_member_mean_c`, already computed and persisted in every fused
posterior's provenance) is **more** biased toward climatology than the served
center (`anchor_value_c`/μ*), not less — the opposite of what the hypothesis
predicts. The center bias itself is real and reconfirmed at 20x the original
sample size (n=4025 high / n=498 low vs the source report's n=142/21) with the
same sign pattern (high cold-biased, low warm-biased, ~0.3–0.4°C), but it
**persists at essentially unchanged magnitude in the newest "current-evidence
shape" posteriors** (rows tagged `ensemble_center_disagreement_v1`, materialized
2026-07-15 through 07-18 — the live route, not a legacy artifact). No collapse
point examined here explains the magnitude; the two other named collapse-point
suspects (EMOS/NGR affine center calibration, and the 2026-07-19 sub-hourly
remaining-window fix) are also ruled out or shown to be structurally unable to
touch the day-ahead/whole-day construction that feeds μ*. §6 names the
mechanism the evidence actually points toward — walk-forward EB shrinkage
toward a zero-bias prior — as the most likely residual driver, untested here in
full (out of scope for a Jensen investigation) and flagged for a dedicated
follow-up.

---

## 1. Collapse-point map — live q chain, day-ahead and Day0

| # | Where | Input curve | Collapse operator | Curve type (mean vs member) | Notes |
|---|---|---|---|---|---|
| C1 | `src/data/openmeteo_ecmwf_ifs9_anchor.py:610-619` `extract_openmeteo_ecmwf_ifs9_localday_anchor` | Open-Meteo `hourly.temperature_2m` **point samples** for the target local day, single deterministic IFS run | `max()`/`min()` over discrete hourly points | Single deterministic-model curve (not an ensemble mean) | Feeds `raw_anchor_value_c` → the T2 fusion anchor prior μ₀. No sub-hourly interpolation of the whole day; a true peak between two hourly points is invisible to this collapse. |
| C2 | Same extraction pattern, once per instrument, for each of the K de-biased likelihood models (`gfs_global`, `icon_global`, `gem_global`, `jma_seamless`, `ukmo_*`, `icon_eu`, `icon_d2`, `arome`, `ncep_nbm_conus`) | Each model's own Open-Meteo hourly point-sample curve | `max()`/`min()` per model | K independent single-deterministic-model curves | Same collapse mechanism as C1, replicated once per instrument, all upstream of `bayes_precision_fusion.py`. |
| C3 | `src/forecast/bayes_precision_fusion.py:82-114` (T2 fusion, per `docs/authority/replacement_final_form_2026_06_09.md` §1b) | K+1 already-collapsed daily-max scalars (C1+C2 outputs, each walk-forward de-biased) | precision-weighted mean across **models**, not across time | operates on already-scalar per-model maxima | This is a cross-model average of independent point estimates of the same true max, not a time-average-then-max; Jensen's max-of-mean does not apply to this step in the form hypothesized (see §3). |
| C4 | `51 source data/scripts/extract_open_ens_localday.py:511-559` | ECMWF Open-Data ENS GRIB, native `mx2t3` (3h rolling max, **not** point-sampled) per member | `max()` of a member's own inner-window 3h-block maxima across the day | **Per-member** curve, collapsed member-by-member before any averaging | This is the Jensen-favorable order (mean-of-maxes, not max-of-mean) and uses a higher-fidelity native rolling-max field, not point samples. |
| C5 | `src/forecast/bayes_precision_fusion.py` §1d (`current_evidence_shape`), persisted as `ensemble_member_mean_c` | The 51 per-member maxima from C4 | `mean()` across members | mean of per-member extremes | This IS the literal "mean-of-member-maxes" quantity the mandate asked to test against μ*. It feeds σ_within/σ_between/δ_ens (**variance only**), never the point estimate μ*. |
| C6 | `src/data/replacement_forecast_materializer.py:575-689` `_day0_remaining_center_delta_c` (commits `a23ee5c2f`/`ce15b0f30`, landed 2026-07-19 00:47) | `day0_hourly_vectors` — anchor family (`ecmwf_ifs`) hourly point-sample curve, SAME source as C1 | whole-day `max()` (unchanged) minus a **sub-hourly-interpolated** remaining-window `max()` | Single deterministic curve | Only the **remaining-window** side gets sub-hourly (piecewise-linear knot) treatment; the **whole-day** side is still the plain hourly `max()` from C1. Only invoked when `_day0_obs_extreme_c is not None`, i.e. Day0 post-obs-extreme only — never for day-ahead forecasts or pre-peak Day0. |
| C7 | `src/data/replacement_forecast_materializer.py:1299-1305` EMOS/NGR affine center calibration | μ* point value | `μ' = a + b·μ` | scalar affine, not a curve collapse | Included for completeness since it is the only OTHER named "center-shaping" step downstream of fusion; tested in §4, found inert (identity) 98–100% of the time on the live route. |

**Answer to mandate Q1:** μ* (the served center) never touches an ensemble MEAN
curve at any step. Every deterministic-model contribution (C1/C2) is a
single-model curve maxed on its own; the one place a true multi-realization
mean-then-collapse could occur (the ENS members, C4/C5) already collapses
per-member first (C4) and only then averages (C5) — the correct order — and
that quantity (`ensemble_member_mean_c`) is not consumed as a point estimate at
all; it is diagnostic/variance input only.

---

## 2. Jensen test on data

Method: joined every `openmeteo_ecmwf_ifs9_bayes_fusion` posterior to its
settled `settlement_outcomes` truth (VERIFIED rows, F→C converted), reading
`anchor_value_c` (μ*, served) and `bayes_precision_fusion.current_evidence_shape.ensemble_member_mean_c`
(μ_ens, mean-of-51-member-maxima) directly from `provenance_json` — no
reconstruction needed, both are already persisted per posterior.

**All matched rows (n=4523, any lead/computed_at):**

| Center | mean bias (y−center) | mean abs err | stdev | closer-to-truth win rate |
|---|---|---|---|---|
| μ* (served) | +0.207 | 1.211 | 1.525 | 51.3% |
| μ_ens (mean-of-member-maxes) | +0.546 | 1.525 | 1.851 | 29.9% |

**One row per city/date/metric, most lead-distant (day-ahead-style) forecast
(n=191):**

| Center | mean bias | mean abs err | win rate |
|---|---|---|---|
| μ* | +0.215 | 1.207 | 66.0% |
| μ_ens | +0.804 | 1.740 | 34.0% |

μ_ens is worse on every cut tested (all rows, same-cycle-only n=41, split by
metric, latest-per-target, earliest-per-target). For the high metric
specifically, μ_ens shows an even larger cold bias (+0.65 to +0.99°C
depending on cut) than μ* (+0.23 to +0.36°C) — i.e. the quantity the mandate
hypothesized would be the *de-biased* one is in fact *more* biased in the
*same direction*. The most plausible reading: ECMWF ENS runs at materially
coarser physical resolution than the deterministic HRES-class models feeding
μ* (a well-documented representativeness gap, independent of aggregation
order), and that resolution gap dominates whatever Jensen-favorable ordering
C4/C5 already apply. **This is a negative result for the central hypothesis,
not a refutation of the center-bias finding itself** — re-run below at larger
n confirms the bias is real.

**Reconfirmation of the original PIT finding at 20x n, stratified by
`semantics_revision`** (the tag distinguishing legacy pre-current-evidence rows
from the live 2026-07-15+ "current evidence shape" route):

| semantics_revision | computed_at range | n (high) | bias (high) | n (low) | bias (low) |
|---|---|---|---|---|---|
| NONE (legacy, pre-2026-07-15) | 06-18 → 07-15 | 28322 | +0.364 | 4445 | +0.006 |
| `ensemble_center_disagreement_v1` (live, same-cycle) | 07-15 → 07-18 | 3220 | +0.340 | 407 | −0.401 |
| `ensemble_anomaly_transport_v1` (live, stale-shape-translated) | 07-17 → 07-18 | 766 | +0.083 | 85 | −0.651 |
| `ensemble_center_scenarios_v2` (brand new, just deployed) | 07-18 14:11 → 20:16 | 27 | +0.081 | 2 | — |

The magnitude is essentially unchanged between the legacy rows and the current
live-route rows tagged `ensemble_center_disagreement_v1` (+0.36 vs +0.34 for
high) — **this rules out "it's just stale/legacy posteriors" as an
explanation.** `ensemble_center_scenarios_v2` is a version bump that landed in
the last few hours before this investigation (`CURRENT_EVIDENCE_SEMANTICS_REVISION`
at `src/data/replacement_forecast_cycle_policy.py:93`); n=27/2 is far too thin
to judge, and by construction this revision only changes the **variance**
(current_evidence_shape) construction, not μ* — it cannot be a fix for the
center bias regardless of outcome.

---

## 3. Why C3 (multi-model fusion) is not the Jensen mechanism, mechanically

Jensen's `E[max(X_t)] ≥ max(E[X_t])` applies when the SAME stochastic process
is observed at multiple times `t` and you compare "expectation of the max" to
"max of the expectation" of that one process. C3 does something different: it
averages K **independent point estimates**, each already the max of its own
model's curve, of the **same single true quantity** (the day's actual max).
Averaging independent unbiased estimators of one number reduces variance and
does not, by itself, introduce a directional bias — precision-weighted
averaging across models is the "reduce estimator noise" operation, not the
"collapse-then-average vs average-then-collapse" operation the mandate's
hypothesis targets. The place that operation genuinely occurs (many
realizations — ENS members — of one physical event) is C4/C5, already ordered
correctly (member-collapse before mean) and tested directly in §2.

---

## 4. Alternative mechanism: EMOS/NGR affine center calibration (C7)

Checked whether the `μ' = a + b·μ` affine correction (`replacement_forecast_materializer.py:1299-1305`)
is doing hidden shrinkage-toward-climatology work that could explain the
symmetric high-cold/low-warm pattern (shrinkage toward a common center would
produce exactly that signature).

| metric | n | identity (a=0, b=1) | non-identity mean a | non-identity mean b | non-identity mean Δc |
|---|---|---|---|---|---|
| high | 17683 | 98.3% | 2.538 | 0.942 | **+0.739** |
| low | 2317 | 100.0% | — | — | — |

The affine step is a no-op for the overwhelming majority of live rows, and
where it is active (1.7% of high rows), its mean shift is **positive** (warms
the center), the opposite sign needed to explain a cold bias. **Ruled out** as
the active mechanism on the current live route.

---

## 5. Cross-check: does the sub-hourly fix (`a23ee5c2f`, landed 2026-07-19
00:47, today) touch this?

No. `_day0_remaining_center_delta_c` (`replacement_forecast_materializer.py:575-689`)
is invoked only inside the Day0 branch, gated on `_day0_obs_extreme_c is not None`
(`replacement_forecast_materializer.py:3974`) — i.e. only after an observed
extreme already exists for the target local day. It corrects the **remaining-window**
side of the delta (via piecewise-linear interpolation at the exact
`computed_at` instant, a genuinely different and correctly-scoped fix for a
different bug: post-peak over-dispersion of "new extreme beyond obs"
probability). The **whole-day** side of that same delta computation
(`max(whole_values)` at `replacement_forecast_materializer.py:678-682`) is
still the plain discrete hourly max — the same collapse as C1 — and this fix
never runs for day-ahead forecasts or for the walk-forward de-bias training
residuals (which are computed from previous-run day-ahead-style forecasts,
`docs/authority/replacement_final_form_2026_06_09.md` §1a). This fix is
correctly scoped to the bug it targets and is not a candidate explanation for,
or a fix for, the center bias measured here.

---

## 6. What the evidence actually points toward (not fully proven; flagged for
follow-up)

A quick conditional test (bucketing by `|μ* − monthly climatology|` and by
`predictive_sigma_c` terciles, n=1586, `tail_test.py`) shows the high-metric
cold bias is roughly **constant** across near-climatology and extreme-anomaly
days (+0.36, +0.49, +0.32°C by anomaly-magnitude tercile) rather than growing
with anomaly size — arguing against a purely tail-conditional NWP-conservatism
story for `high`, though `low` does show a growing warm-bias with anomaly
magnitude (+0.06 → +0.13 → −0.28°C). A roughly constant per-instrument bias
that survives walk-forward de-biasing is the signature predicted by the
documented EB shrinkage formula itself
(`docs/authority/replacement_final_form_2026_06_09.md` §1a):
`b̂_s = λ_s·r̄_s + (1−λ_s)·parent`, `parent = 0.0`, `λ_s = n_s/(n_s+κ)`,
`κ=8.0`. By construction this shrinks every instrument's estimated bias toward
**zero**, not toward its true value — for a model with a genuine persistent
~1–1.5°C conservatism on temperature extremes (a documented, unrelated-to-Zeus
NWP phenomenon) and modest walk-forward `n_s`, `(1−λ_s)` of that true bias is
left uncorrected by design. This is a plausible, mechanically well-grounded
candidate for the residual ~0.3–0.4°C, but it was **not tested here** (it
requires per-instrument `n_s`/`λ_s` provenance not pulled in this pass) and is
out of scope for a curve-collapse investigation — flagged as the concrete next
step rather than claimed as proven.

---

## 7. Fix specification

Given §§2–5 refute or rule out every curve-collapse-shaped mechanism examined:

1. **Do not touch anchor/fusion/day0 collapse machinery** (C1–C7) as a fix for
   this bias — none of it reproduces the measured signature, and re-shaping
   any of it risks the exact "second ad hoc center correction" double-counting
   `replacement_final_form_2026_06_09.md` §4a already warns against.
2. **The already-identified fix stands**: `docs/evidence/capital_efficiency_2026_07_19/highq_overconfidence.md`
   §5's `settlement_coverage_hierarchy.py` walk-forward q-shrink is the
   correctly-layered, already-built answer for the *served-q* consequence of
   this bias (it corrects q against settled history downstream of μ*, exactly
   where a residual, non-collapse-shaped center bias belongs per U4). Nothing
   in this investigation changes that recommendation.
3. **New, narrower follow-up worth opening**: pull `n_s`/`λ_s` per instrument
   from the walk-forward de-bias step and test whether bias-per-instrument
   correlates with `(1−λ_s)` — if confirmed, the fix is raising `κ` (slower EB
   shrink) or lengthening the walk-forward window for climatologically
   extreme-prone instruments, not touching any curve-collapse code.

## 8. Caveats

- The Jensen test (§2) uses whatever `forecast_posteriors` row exists per
  target — this mixes leads and times of day; the "same-cycle-only" (n=41)
  and "earliest/latest-per-target" (n=191) cuts are attempts to control for
  this but are all much smaller than the full n=4523/37274 samples.
- `ensemble_member_mean_c` reflects ECMWF ENS specifically; the mandate's
  broader question ("if member curves don't exist for the anchor family, test
  on whatever family has them") is answered — ENS is the only member-level
  family available in this schema, and it is available only as pre-collapsed
  per-member daily extrema (`51 source data/scripts/extract_open_ens_localday.py`
  discards the underlying 3h-block values once the per-member max/min is
  taken), so a literal "max of the mean 3-hourly curve" reconstruction from
  raw member timesteps was not possible from `ensemble_snapshots` alone within
  this pass; the comparison actually run (mean-of-member-maxes vs μ*) is the
  strongest version of the hypothesis testable from persisted data.
- §6 is a named, evidence-consistent lead, not a proven mechanism — flagged
  explicitly as unproven per the operator's own "no fixed number, only
  math/stats prove it" standard.
