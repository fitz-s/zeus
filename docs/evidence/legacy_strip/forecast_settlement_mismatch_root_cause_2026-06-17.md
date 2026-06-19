# Forecast-vs-settlement mismatch — ROOT CAUSE: the cold AIFS prior owns the center, not the precise data (2026-06-17)

```
# Created: 2026-06-17
# Authority basis: operator directive "找到数据和数学究竟有什么问题" (find what is actually wrong
#   with the DATA and the MATH vs settlement). Settlement-graded, read-only DB analysis.
```

## Question
Operator: the strip/carrier plumbing does NOT fix the real problem — the forecast still does not match
settlement. Find the actual DATA + MATH defect.

## Method
Compared, at the decision lead (lead-1), the live data products against WU settlement, n=222–260 settled
city-targets (`state/zeus-forecasts.db`): raw multi-model members (`raw_model_forecasts`), the served
posterior q (`forecast_posteriors`, product `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1`), the
ifs9 anchor, and the AIFS-sampled prior — all vs `settlements.settlement_value`.

## Finding 1 — the DATA (raw multi-model) is fine
Raw member-mean − settled at lead-1: **mean +0.07 °C, median −0.50 °C** (n=222). The precise raw
multi-model consensus tracks settlement. The earlier "−8 °C cold" per-city numbers were a LEAD-POOLING
artifact (pooling leads 0–7); at the actual decision lead the raw center is calibrated. **The inputs are
not the problem.**

## Finding 2 — the MATH injects the cold: the AIFS prior owns the center
Served posterior q vs settlement (n=260, lead-1):
- center: q-mean − settled mean −0.02, **median −0.53** (cold)
- **PIT badly miscalibrated: 42.3 % of settlements land in the TOP decile** of the predictive CDF
  (uniform = 10 %); only 9 % in the bottom. Mode-match (argmax bin == settled bin) just **20 %**.
- → the served distribution is **too cold and too narrow on the warm side**; settlement keeps landing in
  its thin upper tail.

Decomposition — the served q mean **equals the AIFS-sampled prior mean, NOT the anchor and NOT the raw
fusion**, in every probed family:

| city | settled | ifs9 anchor | AIFS prior mean | served q mean | AIFS−settled |
|------|--------:|------------:|----------------:|--------------:|-------------:|
| Karachi 06-11 | 40.0 | 43.5 | **36.0** | 36.6 | **−4.0** |
| Milan 06-09   | 28.0 | 26.6 | **24.8** | 24.9 | **−3.2** |
| Madrid 06-15  | 32.0 | 30.2 | **29.1** | 29.3 | **−2.9** |
| Lucknow 06-11 | 36.0 | 34.9 | 34.5 | 34.6 | −1.5 |

Karachi is the cleanest proof: all 5 raw models say 39.4–43.3 (mean 41.8), the ifs9 anchor is 43.5,
settlement is 40 — **but the served q puts 0.50 on bin 36 and 0.32 on bin 37** (82 % of mass 4–6 °C below
every raw model and below settlement). Bins extend to 44, so it is NOT clipping.

## Root cause (one sentence)
**Despite `anchor_weight=0.80`, the served forecast center is fully controlled by the COLD AIFS-sampled-2t
prior (−2 to −4 °C vs settlement); the soft-anchor (σ=3.0) is too weak to override a sharp cold prior, so
the precise data — the ifs9 anchor and the raw multi-model consensus, both of which match settlement — is
discarded as the center.** (Consistent with the standing AGENTS.md note: "legacy AIFS member-vote shape put
zero probability on the winning bin in 28 % of settled cells.")

## Finding 3 — the DEEPER why (data-precision at source): the AIFS samples 11–16 km FROM THE AIRPORT
The settlement station IS the airport (Karachi OPKC, Lucknow VILK, Beijing ZBAA, ...). We request the
airport coordinate, but each model returns its NEAREST native grid cell; a coarse model snaps far away.
Live Open-Meteo cell-distance probe (2026-06-17), distance from the airport coordinate:

| city | AIFS / 0.25° cell dist | fine (icon) cell dist | coarse→fine tmax |
|------|----------------------:|----------------------:|------------------|
| Karachi | **15.6 km** | 3.3 km | 35.5 → 34.6 |
| Milan   | **13.3 km** | 1.8 km | 32.0 → 32.8 |
| Beijing | **12.6 km** | 5.1 km | 29.7 → 29.9 |
| Lucknow | **11.1 km** | 1.9 km | 37.6 → **39.5** |
| Madrid  | 6.0 km | 6.0 km | 35.6 → 34.7 |

The AIFS-sampled prior (ECMWF AIFS ENS = 0.25° ≈ 28 km native) samples the cell **11–16 km from the
airport** — it is the COARSEST source, forecasting the wrong location, yet it OWNS the served center
(Finding 2). The fine models sample 1.8–5 km from the airport. Lucknow is the clean illustration: the
near-airport fine cell is **+1.9 °C** vs the distant coarse cell — the airport (tarmac/urban) runs hotter
than a cell 11 km away, which is exactly the hot-city cold gap. The representativeness sign is
terrain-dependent (Karachi's distant NE cell is warmer), but the DISTANCE defect is uniform: the center is
built from a point ~13 km from where settlement is measured.

So the cold is DATA-PRECISION at its root (a coarse cell far from the settlement station) AMPLIFIED by the
MATH (that coarse-distant AIFS owns the center). Operator law: "fusion = add finer stations CLOSER to the
airport, not farther."

## Why it loses at settlement (the live symptom)
A cold center makes the market's center bin (near settlement) look unlikely → the live lane **floods
buy_no on `bin_type='center'`** (observed: ~20 near-identical `buy_no @0.74` on center bins in the last
hours of `trade_decisions`). Settlement lands AT that center → the buy_no loses. The cold AIFS q reaches the
live decision either as the replacement-authority q directly, or as the spine fallback when
`SPINE_INPUTS_UNAVAILABLE` (spine, when its raw inputs ARE threaded, builds center from `raw_model_forecasts`
and is correct — that is the fix already proven on one lane).

## Fix direction (operator law: "fusion = add finer stations CLOSER to the airport, not farther")
The center must be built from the data sampled CLOSEST to the airport at the FINEST resolution — the
settlement-faithful precise consensus — NOT the coarse AIFS cell 13 km away. Concretely:
1. **Demote the coarse-distant AIFS from owning the center.** The AIFS 0.25° cell (11–16 km off-airport)
   must not control μ*. The live spine already does this (`qkernel_spine_bridge`: `build_center` over
   `raw_model_forecasts`, the fine near-airport models); the `replacement_forecast_materializer` /
   `_replacement_authority_probability_and_fdr_proof` lane must match — take the center from the fine raw
   fusion and let AIFS contribute distributional SHAPE only, or give the anchor real (hard) authority.
2. **ADD finer near-airport sources to the fusion** (the operator's directive) — globally, not just CONUS.
   Prefer, per city, the highest-resolution model whose cell is nearest the airport; weight by
   (resolution × inverse cell-distance-to-airport). For airports with sub-km regional coverage add it
   (AROME 1.3 km, ICON-D2 2 km, HRRR 3 km); for Asian airports use the finest global (icon/gfs at 2–5 km),
   never the AIFS 0.25° cell as the center. The settlement station's own METAR is the closest "station" of
   all and belongs in the near-airport set for nowcast/day0.
3. Do NOT re-add a per-city statistical de-bias — the gap is a wrong-LOCATION/coarse-cell defect, not a
   bias to subtract; fix it by sampling the right place (the airport) at the right resolution.

## Finding 4 — the design is PER-CITY BEST SOURCE; the implementation uses a fixed combo + AIFS-fixed center
Operator design intent: AIFS is a FALLBACK (no regional best); each city uses the source whose grid cell is
CLOSEST to its airport METAR at the FINEST resolution; fusion is a per-city best combination, NOT a fixed
blend. The implementation distorts this: (a) `model_selection.py`'s per-city regional polygon gate covers
ONLY Central-EU (icon_d2 2km) / France (arome) / UK / CONUS (gfs_hrrr) / N-America (gem_hrdps) — **all of
Asia, Spain, S-America, Africa, Oceania have NO regional → they fall through to the coarse global/AIFS**;
and (b) the `forecast_posteriors` materializer center is the FIXED `ecmwf_ifs9_aifs_sampled_2t_soft_anchor`
AIFS prior — AIFS owns the center always, it is NOT a fallback.

Per-city regional eligibility (lead-1, verified): Milan → `icon_d2`+`arome` ✓; Madrid, Beijing, Lucknow,
Karachi, Tokyo, Shanghai → **NONE**.

Settlement-graded per-city BEST model (lead-1, raw vs settlement, min-MAE; this is the per-city truth the
fusion must select):

| city | best model | bias °C | MAE °C | worst model MAE |
|------|-----------|--------:|-------:|----------------:|
| Karachi | icon_global | +0.3 | 0.60 | gfs 1.42 |
| Madrid | ecmwf_ifs (9km) | −0.3 | 0.55 | icon 0.94 |
| Milan | ukmo_10km | +0.6 | 0.70 | gfs 2.21 |
| Taipei | jma_seamless | −0.6 | 0.71 | gfs 2.18 |
| Qingdao | gfs_global | +0.7 | 0.81 | ecmwf 1.77 |
| Singapore | ukmo_10km | 0.0 | 0.85 | jma 1.84 |
| Lucknow | ecmwf_ifs | −0.3 | 0.95 | **gfs 6.71** |
| Hong Kong | gfs_global | +0.3 | 1.35 | jma 1.93 |
| Guangzhou | ecmwf_ifs | −0.5 | 1.25 | ukmo 1.91 |
| Beijing | icon_global | 0.0 | 1.33 | gfs 2.74 |
| Tokyo | gfs_global | +2.4 | 3.15 | ukmo 4.14 |
| Seoul | icon_global | +3.5 | 3.63 | jma 4.24 |
| Shanghai | jma_seamless | +2.9 | 4.15 | gfs 5.62 |

The best model differs per city (icon/ecmwf/gfs/jma/ukmo each win somewhere) → no fixed combo; a
settlement-faithful per-city forecast already EXISTS (best often <1 °C MAE, ~0 bias). A blind blend is
poisoned by the per-city worst (Lucknow gfs 6.71). Tokyo/Shanghai/Seoul (coastal E-Asia) have NO good source
(best 3–4 °C MAE) → these need a finer near-airport source.

## Implementation (per the design)
1. `model_selection` + the materializer center → select the **per-city best near-airport source**
   (cell-nearest-airport × finest-resolution, settlement-MAE-validated), not a fixed reps+polygon blend.
2. **AIFS = fallback ONLY** (city has no near-airport source); never the fixed center.
3. **Complete coverage**: add the best near-airport source per uncovered region; for coastal E-Asia
   (Tokyo/Shanghai/Seoul) add a finer airport-resolving cell/source (the 3–4 °C MAE gap).

## Post-fix acceptance (settlement-graded)
Re-run this analysis: served q mean must track raw/settlement (not the AIFS mean); PIT flattens toward
uniform (top-decile share → ~10 %); mode-match rises; the buy_no-on-center flood stops. ARM only when the
served center matches settlement WITHOUT a statistical correction.

## Note
`src/data/replacement_forecast_materializer.py` and `_replacement_authority_probability_and_fdr_proof` are
in the operator's live uncommitted edits — the cold enters exactly there. `anchor_representativeness_debias.json`
does NOT exist → the per-city anchor de-bias (`get_city_debias_c`) returns None (inert) — it is NOT the cold
source; the cold is the AIFS-prior-dominated soft-anchor fusion.
