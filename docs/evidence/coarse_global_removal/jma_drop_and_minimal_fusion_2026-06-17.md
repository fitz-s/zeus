# Coarse-global + JMA drop, domain-AND-lead-aware completeness, minimal-fusion probe

**Date:** 2026-06-17
**Authority:** operator-directed ("no 25km model; if live fuses what it doesn't use that's bug
risk"; "drop jma now; remaining combos tested later; these past data may have coordinate bias").
**Settlement truth:** `state/zeus-forecasts.db settlements` (VERIFIED), high metric, lead-1 day-ahead,
recent settled window (~2026-05-20 .. 2026-06-16). Residual = forecast_value_c − settlement (F→C
normalized).

## 1. What was removed from the live T2 fusion (fused AND downloaded)
- `gfs_global` (0.25°/25km) — raw settlement MAE **1.696** (worst global).
- `gem_global` (~15km GDPS) — coarse-cell cold, 12h cadence.
- `jma_seamless` — raw settlement MAE **2.124**, bias **−1.46** — the single worst declared model.

The fusion vocabulary (`model_selection.DECORR_GLOBALS`) is now `(icon_global, ukmo_global)`; the
provider-family contract (`replacement_fusion_upgrade_trigger.DECORRELATED_PROVIDER_FAMILIES`) is
now the **4 families {NCEP, DWD, CMC, UKMO}** (+ the ECMWF 9km anchor as prior). NCEP is nest-only
(gfs_hrrr/ncep_nbm, CONUS), CMC nest-only (gem_hrdps, N-America); the pure globals are icon_global
(DWD) and ukmo_global (UKMO).

## 2. JMA per-city settlement performance (answers "does jma have a per-city / Japan advantage?")
**JMA is the single best model in 0/49 cities — including Japan.** It is the WORST model (7/7) in
Tokyo. Selected rows (lead-1 high):

| city | jma_bias | jma_MAE | anchor(ecmwf_ifs)_MAE | city best | jma rank |
|------|---------:|--------:|----------------------:|-----------|---------:|
| Tokyo (Japan) | −0.98 | 1.53 | 1.23 | icon_seamless 0.94 | 7/7 |
| Seoul | −4.95 | 4.95 | 2.17 | icon_seamless 0.84 | 6/7 |
| Shanghai | −3.18 | 3.28 | 1.53 | ukmo_global 1.18 | 7/7 |
| Singapore | −2.55 | 2.55 | 1.13 | icon_seamless 0.84 | 7/7 |
| Busan | −0.43 | 1.17 | 1.09 | gfs_global 0.84 | 3/7 |
| Hong Kong | −0.81 | 1.22 | 1.43 | ukmo_global 0.87 | 3/7 |

The extreme cold on coastal Asian cities (Seoul −4.95, Singapore −2.55) is the coarse-cell
offshore-snap. There is **no city (incl. Japan) where keeping JMA is justified** → global drop, not
a domain-gated keep.

**COORDINATE-BIAS CAVEAT (operator-flagged):** these historical residuals were measured at the
PRE-2026-06-17 coords (the config-coordinate fix landed today). Part of jma's cold may be
offshore-snap at the wrong cell. The drop stands (jma is worst by a wide margin regardless), but the
finer model-subset combinations (§4) MUST be re-tested on clean post-coord-fix settled data
(operator-deferred; clean data settles in a few days).

## 3. Minimal-fusion probe (operator: "openmeteo + best 1-2, not everything-in")
Two graded centers vs settlement (lead-1 high; raw window n=556, de-biased window n=692):

RAW envelope (equal-weight mean — what the live spine reads):
| variant | bias | MAE |
|---------|-----:|----:|
| everything-in | −0.530 | 1.101 |
| drop coarse (gfs/gem) | −0.594 | 1.096 |
| drop coarse + jma | **−0.318** | **1.017** |
| anchor + best-1 | −0.422 | 1.047 |
| anchor only | −0.421 | 1.250 |

DE-BIASED + inverse-variance precision-weighted (walk-forward):
| variant | bias | MAE |
|---------|-----:|----:|
| everything-in | +0.062 | **0.914** |
| anchor + best-2 | +0.054 | **0.911** |
| anchor + best-1 | +0.049 | 0.953 |
| anchor only | +0.031 | 1.101 |

**Verdict (honest):** in the RAW path the cold models (esp. jma) drag the center −0.53°C → dropping
them is a clear win (operator right). In the DE-BIASED precision-weighted fusion, everything-in
(0.914) ≈ anchor+best-2 (0.911) — minimal MATCHES, doesn't beat. So "openmeteo + best 1-2" is a
valid SIMPLIFICATION (same accuracy, far fewer models/downloads); whether it is an accuracy WIN
depends on whether the live decision center de-biases. **These are PROXY tests (self-contained
de-bias, not the production `fuse_bayes_precision_posterior`); the definitive per-city minimal-vs-
full replay with the real fusion code is deferred and must run on clean post-coord-fix data.**

## 4. Transition caveat from the drop
- CONUS NCEP is IMPROVED: ncep_nbm (MAE 1.277, 1376 rows of history) replaces gfs_global (1.696).
- N-America CMC is temporarily THIN: gem_hrdps_continental has only ~12 rows (added 2026-06-17), so
  NA cities lose a usable CMC rep until it accrues ~2 days of history. The domain-aware contract
  marks CMC absent there meanwhile (honest, not phantom).
- Decorrelation diversity narrows: JMA was the only non-Western global. Accepted pending §4 re-test.

## 5. Critic resolution (the REVISE → fixed)
- **HIGH (lead-0 expected-set):** `expected_provider_families_for_city` now takes `lead_days` and
  evaluates nest eligibility at the REAL city-local lead (nests are lead-capped: gfs_hrrr=2,
  ncep_nbm=3, gem_hrdps=2). Both call sites (materializer completeness verdict; trigger scope
  comparison) pass the real lead. Kills the far-lead phantom-PARTIAL + revived upgrade loop.
  RED-on-revert proven: forcing lead 0 flips `test_conus_far_lead_does_not_over_expect_lead_capped_nests`.
- **MEDIUM (dead surface):** forward-dead maps trimmed (`OPENMETEO_MODEL_IDS`,
  `SINGLE_RUNS_UNSERVABLE_MODELS`). The de-bias-HISTORY routing (`OPENMETEO_PREVIOUS_RUNS_SOURCE_ID`,
  registry `*_previous_runs` specs) is RETAINED + explicitly documented (resolves aging-out history
  rows; same class as the kept ecmwf_previous_runs). `MODEL_PUBLISH_CYCLE_HOURS` kept as accurate
  provider-cadence reference with a DORMANT note (its only members were dropped).
- **LOW (docstring):** the false "lead 0 is most permissive — eligible at any longer lead" claim
  scrubbed; replaced with the correct lead-cap semantics.

## 6. Test evidence
Contract + money-path: 442 passed; only the 2 known pre-existing money-path fails
(`public_http_timeout`, warm-cycle) + 1 unrelated pre-existing (`test_backfill` onboarding
`_verification_tables` tuple bug — `onboard_cities` not in this diff). RED-on-revert proven for both
the domain gate AND the lead gate.
