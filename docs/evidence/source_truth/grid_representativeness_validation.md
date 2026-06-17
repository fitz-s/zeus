# v3 Grid-Representativeness — Settlement Validation + Walk-Forward Fit

- Created: 2026-06-17
- Authority basis: operator "finish v3" (2026-06-17); zeus_grid_coordinate_precision_upgrade_v3.md rule 4 (station/grid mean shift) + rule 5 (sigma_repr^2 added to the fusion Sigma diagonal).
- Deploy flag: `config/settings.json` edli.`replacement_0_1_grid_representativeness_enabled` (currently **false**; this run flips NOTHING).
- Scripts: `scripts/validate_grid_representativeness_fusion.py` (the GATE), `scripts/fit_grid_representativeness.py` (the fit).
- Artifacts written: `state/repr_variance_fit.json`, `state/station_shift_fit.json`.
- DB: `state/zeus-forecasts.db` opened read-only (`?mode=ro`). No DB writes, no flag flips, no commit.

## VERDICT: HOLD the flag (neither cold-start nor fitted clears a PROMOTE bar)

- **ON cold-start: HOLD — it DEGRADES the pooled center** (ΔMAE +0.031, worsens 186 cells vs improves 151 of 357).
- **ON fitted: HOLD as "neutral, not an improvement"** — ΔMAE is within noise (+0.0022 at 7d, +0.0095 at 14d), it trims the (already tiny) pooled cold bias (−0.055 → +0.025), and it improves more cells than it worsens (180 vs 157). But the GATE requires "no degrade AND reduce the cold drag"; fitted is non-degrading but its center improvement is statistically nil at the pooled level. It is SAFE to promote (non-degrading) but does not, on this evidence, *earn* promotion.

The decisive reason (subset split below): sigma_repr is a **variance** (direction-agnostic). It helps the cells where the coarse cell runs cold and hurts the cells where it runs warm by almost exactly the same amount, so the net pooled effect cancels. Only rule 4's **mean** shift (b_grid = +0.545 degC) could selectively cure cold bias, and that mean lever is NOT wired into this flag — the flag wires rule 5 (variance) only.

## Deliverable 1 — settlement-validation replay (the GATE)

Holdout window = last 7 days, lead-1, DISJOINT from the [-60d, -7d] fit window (no leakage). Fused mu* compared to VERIFIED settlement in native unit. n = 357 fused cells, 0 skipped.

| variant | bias (degC/F) | MAE | ΔBias | ΔMAE | improved | worsened |
|---|---:|---:|---:|---:|---:|---:|
| OFF (baseline, byte-identical) | -0.0548 | 1.0952 | — | — | — | — |
| ON cold-start | -0.0404 | 1.1260 | +0.0145 | **+0.0308** | 151 | 186 |
| ON fitted | +0.0248 | 1.0974 | +0.0797 | **+0.0022** | 180 | 157 |

Robustness — refit [-90d,-14d] / validate disjoint last 14d (n=413): OFF MAE 1.0586; cold-start ΔMAE +0.0308; fitted ΔMAE +0.0095. **Same sign and magnitude in both windows** — cold-start degrades, fitted neutral.

### Direction-split (why the pooled effect cancels) — holdout 7d, fitted

| subset | n | OFF bias | OFF MAE | FIT bias | FIT MAE | FIT ΔMAE |
|---|---:|---:|---:|---:|---:|---:|
| OFF center COLD vs settlement | 196 | -1.047 | 1.047 | -0.939 | 0.978 | **-0.069** |
| OFF center WARM vs settlement | 161 | +1.153 | 1.153 | +1.199 | 1.243 | **+0.089** |

On the cold-drag cells the operator's thesis HOLDS (fitted cuts MAE -0.069 and lifts the cold center). But the variance widening cuts the warm cells the same way it cuts the cold ones, so the warm subset worsens +0.089 and the two cancel to ~neutral pooled.

### Per-city movers (holdout 7d, sorted by cold-start ΔMAE; negative = ON improves)

Best movers (cold + coarse/offset anchor cells — the thesis cities):

| city | n | bias OFF | MAE OFF | MAE cold | MAE fit | ΔMAE cold |
|---|---:|---:|---:|---:|---:|---:|
| Denver | 6 | -0.562 | 2.118 | 1.732 | 1.577 | -0.386 |
| Houston | 6 | -0.691 | 2.077 | 1.865 | 1.812 | -0.212 |
| Mexico City | 6 | -1.030 | 1.147 | 0.978 | 0.896 | -0.169 |
| Seattle | 6 | -1.815 | 2.005 | 1.882 | 2.035 | -0.124 |
| Tokyo | 14 | -0.481 | 0.562 | 0.439 | 0.498 | -0.123 |
| Wuhan | 7 | -0.320 | 1.248 | 1.125 | 1.113 | -0.123 |

Worst movers (warm or thin cells — the cancelling cost):

| city | n | bias OFF | MAE OFF | MAE cold | MAE fit | ΔMAE cold |
|---|---:|---:|---:|---:|---:|---:|
| Seoul | 14 | -0.176 | 0.463 | 1.011 | 0.685 | +0.548 |
| Dallas | 6 | +1.374 | 2.404 | 2.753 | 2.838 | +0.349 |
| San Francisco | 6 | +1.763 | 4.569 | 4.804 | 4.532 | +0.235 |
| Moscow | 7 | -0.432 | 0.886 | 1.099 | 1.163 | +0.213 |
| Lucknow | 7 | +0.129 | 1.277 | 1.455 | 1.341 | +0.178 |

Note Seoul: cold-start blows it up (+0.548) but fitted contains it (0.685) — the cold-start a_d=0.04 over-penalizes, the fitted a_d=0.0072 is far gentler. This is the clearest single argument for the fit over the cold-start.

## Deliverable 2 — walk-forward fit (rule 5 + rule 4)

Fit window [-60d, -7d], lead-1, `endpoint='previous_runs'` JOIN VERIFIED settlement, pooled across 50 cities / 11 models, n_train = 16,887 rows (holdout [-7d, now] reserved for Deliverable 1).

### Rule 5 — sigma_repr^2 = (a0 + a_d*(d_eff/1000)^2 + a_z*dz^2) * regime_mult

| coeff | cold-start | **fitted** | move |
|---|---:|---:|---|
| a0 (degC^2 floor) | 0.25 | **3.5856** | up 14x — pooled E[resid^2] is dominated by the irreducible forecast-error floor, NOT representativeness |
| a_d (per km^2) | 0.04 | **0.007204** | **DOWN 5.5x** — the cold-start a_d over-penalizes distance (confirms the operator's expectation) |
| a_z (per m^2) | 2.5e-5 | **0.0** | dz is one value per city, so within the pooled design it carries no identifiable slope -> dropped (engine identifiability rule) |
| regime mults | 1.5/1.5/1.25 | carried from base (not refit) | the coarse-stratification multipliers ride the cold-start; the fit refines a0/a_d/a_z only |

Effect of the shrink: the cold-start makes sigma_repr^2 *differentiate sharply* by distance (Tokyo ecmwf 2.47, jma 0.46; Amsterdam jma 28.6 at 26.6km), so the down-weight is steep and noisy. The fitted a0=3.586 makes sigma_repr^2 ~3.6-4.0 for nearly every cell with only a +-0.4 distance spread — a near-UNIFORM widening, which is close to weight-neutral. That is exactly why fitted is benign (ΔMAE ~0) while cold-start is disruptive (ΔMAE +0.031).

### Rule 4 — station/grid mean shift (FITTED, NOT WIRED into the flag)

| coeff | fitted | reading |
|---|---:|---|
| beta_alt | -0.005046 degC/m | mild, physically-signed elevation sensitivity (station-below-cell warms) |
| b_grid | **+0.5452 degC** | settlement runs ~0.55 degC WARMER than the raw pooled model — the cold-drag, as a MEAN, lives here |

b_grid = +0.545 is the honest size of the cold bias **as a mean correction**. It is the lever that would actually cut the cold drag — but rule 4 (station_correction of the mean) is NOT part of `replacement_0_1_grid_representativeness_enabled`, which wires only rule 5 (the Sigma-diagonal variance). This validation therefore cannot show the flag curing cold bias, because the flag does not touch the mean.

## Reconstruction faithfulness — assumptions & scope (read before trusting the numbers)

The replay reconstructs the live capture path (`src/data/bayes_precision_fusion_capture.py` + `bayes_precision_fusion_history_provider.py` + the materializer's fusion block) as exactly as the persisted DB allows. Assumptions I had to make:

1. **Anchor center = raw single_runs ecmwf_ifs value, NO de-bias.** The live `anchor_value_corrected_c = raw - bias_shift_c`, and `bias_shift_c = get_city_debias_c(...)` returns None when `state/anchor_representativeness_debias.json` is ABSENT — it is absent now (verified). So the current live posture applies no anchor shift, and OFF/ON share an identical anchor center. If the operator later drops that de-bias artifact in, the anchor center moves for BOTH OFF and ON equally; the OFF-vs-ON delta this report measures is unaffected.
2. **"Current" decision value = persisted single_runs latest-cycle (MAX source_cycle_time), lead-1, per model.** Same reconstruction as `scripts/measure_fusion_aifs_drop_performance.py`. The live q is built from the persisted single_runs capture (materializer BLOCKER 5), never a network fetch — faithful.
3. **Walk-forward history = endpoint='previous_runs' lead-1 JOIN VERIFIED, strict target_date < cell date.** Byte-identical to `BayesPrecisionFusionHistoryProvider`'s SQL (no-leak). residual = forecast_c - settle_c (F settlement converted first).
4. **Anchor tau0 bridged ifs025 -> ifs9.** `anchor_history_requires_bridge(stored_model_name='ecmwf_ifs025')` is True, so tau0 = `bridge_anchor_tau0(stdev(anchor residuals))` — matches the live capture (the OM previous-runs ECMWF feed is the 0.25 product).
5. **icon_seamless alias dedup passed alias_series=None.** Verified: select_models drops icon_seamless identically whether passed None or an identical value series (it is bit-identical to icon_d2 in the EU nest), and icon_seamless is never an instrument anyway — no divergence from live.
6. **Regime flags all False in the pooled fit.** The cold-start regime multipliers (coastal/orography/urban) ride `base_fit`; the live `sigma_repr_sq_for(city, model)` is called with default regime=False (the loader's current default), so OFF/ON/fit all use regime=False consistently — faithful to the current loader call site.

**SCOPE LIMIT (stated honestly):** the replay only covers cells whose single_runs "current" capture still exists in the DB. That is ~357 cells at a 7-day holdout (0 skipped) but drops to 413/745 at 14d (332 skipped — older single_runs rows are pruned). The verdict rests on the 7d window (full coverage) corroborated by the 14d window (partial). Money-path imports all resolved; no import or money-path breakage encountered.

## Bottom line

The v3 rule-5 wire is **correct and safe** (flag-OFF byte-identical confirmed; flag-ON fitted is non-degrading). But on settlement evidence it does **not improve the center** — because a representativeness *variance* is direction-blind and cannot selectively undo a cold *mean* bias. The cold drag is real and sized at b_grid = +0.545 degC, but curing it needs rule 4 (the mean shift), which this flag does not wire. **Recommendation: HOLD `replacement_0_1_grid_representativeness_enabled`.** If the goal is the cold-bias cure the operator described, wire rule 4's station_correction (mean) — that is where the +0.545 degC lives — rather than promoting the rule-5 variance flag.
