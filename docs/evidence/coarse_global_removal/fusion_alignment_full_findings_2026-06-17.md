# Fusion model-alignment & data-fitting: complete findings (2026-06-17)

- Created: 2026-06-17
- Authority basis: operator investigation — "prune to the best high-precision models, RAW (no de-bias),
  per-city; root-cause the residual to source; precision-weight rather than one-pot." Independent
  methodology critic (`methodology_critique.md`) + own OOS reproductions.
- Data: `/tmp/unbiased_test_forecasts.json` (51 cities, 33 dates 2026-05-15..06-16, lead-1 RAW day-ahead
  high, 14 models, fetched at **precise post-2026-06-17 coords + cell_selection="land"** — verified §5).
  Settlements: `state/zeus-forecasts.db settlements` (`temperature_metric='high'`, `authority='VERIFIED'`,
  F→C), opened `immutable=1` (read-only).
- Live decision center is **RAW** (`qkernel_spine_bridge` reads the raw `raw_model_forecasts` envelope);
  `forecast_posteriors` materialize via **T2 Bayesian precision fusion** (inverse-variance weighting over
  decorrelated providers). De-bias is being removed from the forward center per operator law.

## 0. The question
Given K individually well-tuned short-range forecast models, each with its own grid-cell
representativeness error at a settlement station, what is the **provably-optimal** way to combine (and
correct) them to predict the settlement **bin** — without overfitting, RAW, per-city? And does pruning
the coarse-cell globals (gfs_global 0.25°, gem_global 0.15°) + jma help?

## 1. What was committed (99d244b99c → fix/opportunity-book-selector)
Dropped `gfs_global`, `gem_global`, `jma_seamless` from the fusion vocabulary
(`model_selection.DECORR_GLOBALS`) and made the provider-family completeness contract **domain-AND-lead
aware** (`replacement_fusion_upgrade_trigger.expected_provider_families_for_city(lat,lon,lead)`): a family
is "expected" for a city only if a member is servable there at the city-local lead (nests are lead-capped
gfs_hrrr=2, ncep_nbm=3, gem_hrdps=2). Families now {NCEP, DWD, CMC, UKMO} (+ ECMWF 9km anchor prior).
This kills the phantom-PARTIAL / re-enqueue loop for non-CONUS and far-lead cities.

## 2. Independent methodology critic verdict (`methodology_critique.md`)
Reproduced the prior per-city RAW best-subset claim exactly (selected pooled MAE 0.990 vs one-pot 1.121,
32/49, gfs_global in 28). Then attacked it:
- **Real but mis-specified.** The point-MAE win survives a permutation null (real −0.130 at 0th pct) and
  rolling-origin CV — but it is **de-biasing in disguise**: a single per-city scalar bias correction of the
  full ensemble equals/beats the entire 2^14 subset search (0.971 vs 0.990, p=0.617).
- **Does NOT transfer to the trading objective.** On a settled-bin log-loss proxy the MAE-selected subset
  is a coin flip vs one-pot (1.696 vs 1.655, p=0.44). Optimizing point-MAE of a mean is a category error.
- **gfs_global=28 is a confound** (eligibility: globals eligible in 51 cities, regionals 4–12; + noise
  floor ~11; + error-cancellation). Eligibility+null-normalized, gfs_global excess +0.34 is **below**
  fine regionals (meteofrance +0.57, ukmo_uk_2km +0.49, icon_d2 +0.35, gem_hrdps +0.34). The raw count
  inverts the real ranking → favors fine-resolution regionals, consistent with the precision/cell-distance
  hypothesis.
- icon_seamless is a literal alias dup (=icon_global 79%, =icon_d2 100%).

## 3. Own OOS reproductions (the corrections)
**3a. Single-best-model per-city selection OVERFITS out-of-sample.** Train ≤05-31 select min-MAE fine
model, test 06-01..16:

| estimator | test MAE | test \|bias\|<0.5 |
|---|---|---|
| fine-basket simple mean | **1.079** | — |
| one-pot all (incl coarse) | 1.090 | — |
| single-best fine (selected) | 1.152 | 22/49 |
| anchor ecmwf alone | 1.569 | — |

In-sample the best-fine model is near-zero-bias in 42/50 cities; OOS that collapses to 22/49 — a selection
artifact. **Do not build a per-city model selector.** Fine-basket averaging beats it and the single anchor.

**3b. The "−1 to −2°C systematic cold" is anchor+coarse, not the fine basket.** At precise coords the
fine-basket systematic bias is modest (mean |bias| 0.63°C); MAE ~1.1 is mostly irreducible day-to-day
error. The residual is **per-city, not a constant** (range −2.1 Seoul … +1.3 LA) → not de-biasable by one
global offset, not a metric/timing bug.

**3c. Pure physical lapse-rate elevation correction does NOT help** (0 fitted params, 0.0065°C/m, using
`config/grid_representativeness.json` cell elevations): corr(residual bias, lapse-pred) = −0.09;
corr(bias, cell-distance d_eff) = −0.25; applying the correction makes |bias| slightly worse
(0.63→0.65). Cells are already close (precise coords; d_eff 2–9 km, Δz < 25 m). The gross
coordinate/elevation representativeness error was already removed by the 2026-06-17 coord fix.

## 4. The optimal estimator on US cities (richest fine-model set) — the key result
US cities carry the full open-meteo high-res lineup: ECMWF 9km anchor + icon_global + ukmo_global (fine
globals) + **gfs_hrrr 3km HRRR + ncep_nbm 2.5km NBM** (+ gem_hrdps 2.5km for Chicago/NYC/Seattle).
Rolling-origin OOS, RAW, 207 pooled predictions across 11 US cities:

| estimator | pooled MAE | pooled bias | beats anchor |
|---|---|---|---|
| anchor ecmwf alone | 1.862 | +0.29 | — |
| single-best fine (selected) | 1.125 | +0.09 | 11/11 (but overfits vs mean) |
| one-pot incl coarse (mean) | 0.926 | −0.10 | — |
| fine-all (mean) | 0.963 | +0.12 | 11/11 |
| reg-only mean (HRRR+NBM[+gem]) | 1.044 | −0.17 | 10/11 |
| **inverse-MAE precision-weighted fine** | **0.875** | **+0.05** | **11/11** |

**Findings:**
1. **Precision weighting (inverse-error-variance, ≈ the live T2 fusion) is the best estimator (0.875)** —
   beats simple fine mean (0.963), single-best selection (1.125, overfit), and one-pot-with-coarse (0.926).
   The operator's "each model is precise, don't one-pot" is correctly realized by **continuous precision
   weighting** (which automatically down-weights imprecise models), NOT by hard model dropping/selection.
2. **Hard-dropping coarse from a simple mean is marginal/negative in US** (one-pot 0.926 ≤ fine-mean
   0.963) — but precision-weighted fine-only (0.875) beats the coarse-included mean. The real lever is the
   **weighting**, not the hard drop. (The committed coarse drop still stands: it cuts download + removes
   the worst contributors; it is just not the primary accuracy lever.)
3. **Los Angeles proves the fine-regional value:** anchor 9km = 5.68 MAE (the land-snap-inland-hot cell),
   but reg-only (HRRR 3km / NBM 2.5km) = 0.667, invmae = 0.729. Fine regionals represent KLAX vastly
   better than any global cell. This is the operator's thesis, quantified.
4. bin-proxy log-loss (de-biased fine-all center) = 1.629 (reference; the live objective is the bin, and
   it must be the scoring metric — see §6).

## 5. Source verification (answering "is the test anchor at land or the wrong point?")
The test data was fetched at **cell_selection="land" + precise coords** — verified by re-fetching the
anchor at both selections and matching the stored values: all 33 sampled (city,date) rows match the
**land** re-fetch exactly. Live config default is `BAYES_PRECISION_FUSION_CELL_SELECTION = "land"`.

**land vs nearest cell_selection (root-cause probe):**
- Coastal cold cluster (Seoul, Guangzhou, Taipei, KL, Jakarta, Manila): **land == nearest (Δ≈0)** — the
  airport already sits on a land cell; both return the same value and it is still imperfect vs settlement.
  Their residual is genuine model error + occasional big-miss days, **not** a sea-cell-snap artifact;
  flipping cell_selection cannot fix them.
- **Los Angeles is the one real cell_selection effect:** land = +8.0°C hotter than nearest (land snaps
  inland-hot +4.5 vs settlement; nearest snaps to ocean −3.5; settlement sits between). KLAX is in a
  land-sea gradient no single global cell captures — a genuine per-city source problem (needs the fine
  regional, a blend, or special handling; "nearest" is not the answer either). The fine US regionals
  already solve LA (§4.3).

## 6. Open questions for deep review (physics / math / statistics)
1. **Provably-optimal combination.** Inverse-variance weighting is BLUE only under *independent* errors.
   The ICON family (icon_global/icon_d2/icon_eu/icon_seamless) and the global models are **correlated**;
   the optimal linear unbiased estimator is GLS with the full error covariance Σ (`w ∝ Σ⁻¹ 1`). What is
   the correct, regularized estimate of Σ from ~17–33 days without overfitting (shrinkage / Ledoit-Wolf /
   factor structure by provider family)? Does the live T2 fusion use the diagonal (independent) or the
   full Σ?
2. **The objective.** Point-MAE of a center is a category error vs the settlement **bin / q_lcb** payoff.
   What is the correct loss to *select and weight* on — settled-bin log-loss, Brier on the bin, or
   directly after-cost EV by bin class — and does optimizing it change the weights vs MAE-optimal?
3. **Per-station representativeness without de-bias.** The residual is per-city, structured by coastal/
   microclimate geography, and survives coord+land+elevation correction. Operator forbids a fitted
   per-city de-bias. Is there a *physical* (not fitted) correction — land-sea fraction, urban-canopy,
   diurnal-phase, sea-breeze — that removes it at source? Or is hierarchical partial-pooling (global model
   menu + shrunk per-city offset) the statistically-defensible minimum, and does that count as "de-bias"?
4. **Spread / calibration.** Subset/weight choices that improve the center can collapse the member spread;
   the bin needs both center and width calibrated (σ_pred). How should the fusion set the predictive width
   (realized residual variance vs member dispersion) to keep q_lcb honest?
5. **Sample adequacy.** 17–33 lead-1 days. What can and cannot be estimated per-city at this n, and what
   is the minimum data / pooling design to make per-city model-weight claims defensible?
6. **Completeness.** What surface is unproven or missing from this analysis?

## 6b. ChatGPT Pro consult + LIVE-CODE verification (2026-06-17)
The consult (REQ-...8ee567, full answer `/tmp/cgc_answer_REQ-20260617-160815-8ee567.txt`) verdict:
replace "T2 as the answer" with a RAW-centered, **no-intercept**, hierarchical distributional fusion:
center = nonnegative simplex-constrained GLS on the **RAW error second-moment** Ω=E[rrᵀ]=Σ+bbᵀ (NOT the
demeaned-residual covariance), width calibrated on settlement-bin log-loss; keep T2 as a candidate until
it wins a settlement-graded proof. Optimal estimator under the bin payoff is fit by a **proper score**
(log-loss/Brier/CRPS), not point-MAE (Gneiting-Raftery) — confirms B§Attack-3.

Local-code verification of the consult's BLOCKERS (Claude Code = source of truth):
- **The live spine center is RAW but EQUAL-WEIGHT (precision weighting DORMANT).** `qkernel_spine_bridge`
  ships `center.build_center` over the `raw_model_forecasts` envelope with **ZERO shift** (gate-0
  bias-maze strip → `raw==debiased`, no de-bias) — law honored. BUT `center.walk_forward_model_weights`
  collapses to `1/n`: its precision basis `1/σ²` reads `member.walk_forward_se_native`, a field that **does
  not exist on `RawModelMember` and is never set anywhere in `src/`**. So the live center is an equal-weight
  robust-Huber mean, envelope-locked. The nonneg/sum-to-1/robust/shrink-to-equal skeleton the consult
  prescribes is present but the **precision seam is unwired**. My US test (§4) measured exactly this gap:
  equal-weight fine mean 0.963 vs precision-weighted 0.875 (~9% left on the table).
- **EB de-bias is NOT fully gone.** The T2 `fuse_bayes_precision_posterior` path still feeds instruments
  `z = x − b̂` (`bayes_precision_fusion_capture._eb_corrected`, `eb_bias`) into `forecast_posteriors`, and
  `forecast_posteriors` IS consumed by live decision modules (`event_reactor_adapter`, `monitor_refresh`,
  `position_belief`). So the no-de-bias law is honored on the spine entry center but the EB-de-biased
  posteriors still reach the reactor/monitor/exit lanes (the #135 entry/exit-source asymmetry).

Convergence: consult theory + US empirics + live code all point to ONE change — **activate the dormant
precision weighting in the spine using the RAW error second-moment Ω (family-block shrinkage for ICON/
global correlation), no offset.** This is RAW-law-compatible (downweights persistently-bad models by their
raw error, never subtracts a center offset = exactly Ω⁻¹1) and is the ~9% lever.

Actionable plan (settlement-graded proof BEFORE deploy, per the consult protocol):
1. Activate spine precision weighting: thread per-model walk-forward raw second-moment onto members;
   `walk_forward_model_weights` uses `1/E[r²]` with provider-family-block shrinkage. (de-dup icon_seamless
   is a sub-case — diagonal/equal weighting double-counts the ICON family.)
2. Resolve the residual EB de-bias: disable `_eb_corrected` in the `forecast_posteriors` path (full RAW) OR
   route reactor/monitor/exit to the RAW spine (closes #135). Adjudicate per operator law.
3. σ_pred width + objective: calibrate width and SELECT on settlement-bin log-loss; prove PIT/reliability/
   q_lcb coverage. The center win is not promotable without width calibration.
4. Proof protocol: rolling-origin nested CV, settlement-bin NLL primary, paired block-bootstrap null,
   candidate bake-off {equal-weight (live), diagonal-precision, raw-2nd-moment Ω, family-block Ω-GLS, T2
   with/without EB}; promote only if it beats live-equal-weight on bin log-loss without q_lcb undercoverage.

## 7. Deployable conclusion (current)
Keep the committed coarse-global drop; **de-dup icon_seamless** (alias — operator-confirmed); fuse the
fine set by **precision weighting** (the live T2 architecture, validated best here); **no fitted de-bias,
no per-city model selector, no one-pot-with-coarse.** The residual tail (LA-class coastal microclimate) is
a per-city source problem, chased individually, not a global algorithm change. Caveat: all §3–4 numbers
are point-MAE/bias centering diagnostics; the binding proof is live settlement-graded **bin/q calibration**.
