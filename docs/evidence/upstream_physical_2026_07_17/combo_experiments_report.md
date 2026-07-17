# Data-Combination Value Experiments — Walk-Forward Settlement Accuracy

Eval window target_date >= 2026-05-01; lead_days=1 (money lead); endpoint=previous_runs; authority=VERIFIED. All temps degC (F settlements converted). Precision weights mirror src/forecast/center.py:166 (RAW inverse-2nd-moment, EB low-n shrink lam=n/(n+8), floor 0.8C, low-n target (0.8*1.5)^2). Walk-forward: residuals for target T use only settled targets < T.

Total eval instances (city,metric,target) with lead1 members and settlement: 2797
Full-pool precision-fused MAE (all cities): n=2797, MAE=1.1047 C

## E2  Leave-one-out (which models carry weight vs hurt)
delta = MAE_without_model - MAE_full, paired on (city,metric,target) where model present.
delta<0 => removing model IMPROVES => model HURTS. delta>0 => model carries real weight.

### Pooled (all cities)  (instances with >=3 members)
| model | n | mean delta (C) | SE | flag |
|---|---|---|---|---|
| knmi_harmonie_netherlands | 18 | -0.1887 | 0.0672 | n<50 SKIP |
| dmi_harmonie_europe | 90 | -0.0872 | 0.0325 | HURTS |
| jma_seamless | 2092 | -0.0201 | 0.0035 | HURTS |
| ncep_nbm_conus | 545 | -0.0110 | 0.0079 | neutral |
| icon_seamless | 332 | -0.0006 | 0.0119 | neutral |
| gem_global | 2092 | +0.0086 | 0.0047 | neutral |
| ecmwf_ifs | 2130 | +0.0112 | 0.0048 | carries-weight |
| gfs_hrrr | 118 | +0.0131 | 0.0452 | neutral |
| icon_eu | 472 | +0.0168 | 0.0049 | carries-weight |
| gfs_global | 2092 | +0.0194 | 0.0046 | carries-weight |
| ukmo_uk_deterministic_2km | 105 | +0.0206 | 0.0179 | neutral |
| meteofrance_arome_france_hd | 313 | +0.0257 | 0.0124 | carries-weight |
| icon_global | 2130 | +0.0260 | 0.0044 | carries-weight |
| ukmo_global_deterministic_10km | 2074 | +0.0334 | 0.0053 | carries-weight |
| gem_hrdps_continental | 98 | +0.0944 | 0.0543 | neutral |
| nam_conus | 116 | +0.1622 | 0.0438 | carries-weight |
| icon_d2 | 352 | +0.1648 | 0.0264 | carries-weight |
| italiameteo_icon_2i | 1 | +0.1909 | 0.0000 | n<50 SKIP |

### Region=CONUS  (instances with >=3 members)
| model | n | mean delta (C) | SE | flag |
|---|---|---|---|---|
| icon_seamless | 80 | -0.0352 | 0.0221 | neutral |
| ncep_nbm_conus | 503 | -0.0152 | 0.0083 | neutral |
| gem_global | 487 | -0.0070 | 0.0067 | neutral |
| jma_seamless | 487 | -0.0013 | 0.0059 | neutral |
| ecmwf_ifs | 516 | +0.0122 | 0.0085 | neutral |
| icon_global | 516 | +0.0125 | 0.0088 | neutral |
| gfs_hrrr | 99 | +0.0288 | 0.0448 | neutral |
| gfs_global | 487 | +0.0394 | 0.0095 | carries-weight |
| ukmo_global_deterministic_10km | 503 | +0.0463 | 0.0107 | carries-weight |
| nam_conus | 97 | +0.1089 | 0.0450 | carries-weight |
| gem_hrdps_continental | 79 | +0.1390 | 0.0571 | carries-weight |

### Region=EU  (instances with >=3 members)
| model | n | mean delta (C) | SE | flag |
|---|---|---|---|---|
| knmi_harmonie_netherlands | 18 | -0.1887 | 0.0672 | n<50 SKIP |
| dmi_harmonie_europe | 90 | -0.0872 | 0.0325 | HURTS |
| jma_seamless | 437 | -0.0235 | 0.0046 | HURTS |
| ukmo_global_deterministic_10km | 425 | -0.0122 | 0.0043 | HURTS |
| gfs_global | 437 | -0.0065 | 0.0045 | neutral |
| gem_global | 437 | +0.0036 | 0.0057 | neutral |
| ecmwf_ifs | 437 | +0.0104 | 0.0063 | neutral |
| icon_global | 437 | +0.0113 | 0.0052 | carries-weight |
| icon_seamless | 68 | +0.0119 | 0.0111 | neutral |
| icon_eu | 437 | +0.0167 | 0.0051 | carries-weight |
| ukmo_uk_deterministic_2km | 105 | +0.0206 | 0.0179 | neutral |
| meteofrance_arome_france_hd | 313 | +0.0257 | 0.0124 | carries-weight |
| icon_d2 | 352 | +0.1648 | 0.0264 | carries-weight |
| italiameteo_icon_2i | 1 | +0.1909 | 0.0000 | n<50 SKIP |

### Region=Asia  (instances with >=3 members)
| model | n | mean delta (C) | SE | flag |
|---|---|---|---|---|
| jma_seamless | 807 | -0.0228 | 0.0072 | HURTS |
| icon_seamless | 126 | -0.0058 | 0.0243 | neutral |
| gfs_global | 807 | +0.0138 | 0.0085 | neutral |
| ecmwf_ifs | 807 | +0.0161 | 0.0091 | neutral |
| gem_global | 807 | +0.0197 | 0.0100 | neutral |
| icon_global | 807 | +0.0363 | 0.0084 | carries-weight |
| ukmo_global_deterministic_10km | 786 | +0.0520 | 0.0096 | carries-weight |

## E3  Greedy forward selection per city (top 12 by eval-instance count)
| city | n | best-single | greedy(models) | greedy MAE | frozen MAE | allpool MAE | equal MAE |
|---|---|---|---|---|---|---|---|
| Paris | 134 | gfs_global=0.820 | 3 [gfs_global+icon_eu+ecmwf_ifs] | 0.714 | 0.821 | 0.914 | 1.003 |
| London | 131 | icon_d2=0.850 | 3 [icon_d2+meteofrance_arome_france_hd+gfs_global] | 0.762 | 0.859 | 0.782 | 0.810 |
| NYC | 131 | gfs_global=0.943 | 2 [gfs_global+jma_seamless] | 0.740 | 1.139 | 0.991 | 1.025 |
| Miami | 112 | icon_global=0.974 | 3 [icon_global+gfs_global+ecmwf_ifs] | 0.695 | 0.868 | 0.791 | 0.974 |
| Tokyo | 76 | icon_global=1.000 | 3 [icon_global+ukmo_global_deterministic_10km+gfs_global] | 0.744 | 1.000 | 0.784 | 0.924 |
| Seoul | 74 | gem_global=1.379 | 2 [gem_global+icon_global] | 1.120 | 1.632 | 1.366 | 2.047 |
| Shanghai | 74 | ukmo_global_deterministic_10km=0.967 | 1 [ukmo_global_deterministic_10km] | 0.967 | 1.175 | 1.123 | 1.283 |
| Munich | 70 | meteofrance_arome_france_hd=0.814 | 3 [meteofrance_arome_france_hd+gem_global+icon_global] | 0.692 | 0.988 | 0.999 | 1.135 |
| Amsterdam | 69 | icon_eu=0.790 | 3 [icon_eu+jma_seamless+meteofrance_arome_france_hd] | 0.728 | 0.799 | 0.996 | 1.304 |
| Seattle | 68 | ncep_nbm_conus=1.516 | 3 [ncep_nbm_conus+gfs_global+icon_global] | 1.324 | 1.556 | 1.418 | 1.389 |
| Toronto | 68 | ukmo_global_deterministic_10km=1.005 | 2 [ukmo_global_deterministic_10km+icon_global] | 0.928 | 1.442 | 1.200 | 1.307 |
| Austin | 66 | ukmo_global_deterministic_10km=1.014 | 4 [ukmo_global_deterministic_10km+icon_global+jma_seamless+gfs_global] | 0.800 | 1.085 | 1.033 | 1.146 |

Mean(frozen - greedy)  = +0.2625 C across 12 cities.
Mean(allpool - greedy) = +0.1820 C  (positive => greedy beats using-all).
Mean(frozen - allpool) = +0.0805 C  (HONEST, both peek-free; positive => full precision pool beats frozen 3-model scheme).
Mean greedy subset size = 2.7 of 7.8 candidates.

CAVEAT (statistical honesty): greedy SELECTS the model subset on the same eval window it is scored on (in-sample selection; weights inside are walk-forward but the subset choice peeks). Greedy MAE is therefore an OPTIMISTIC upper bound on achievable accuracy — it proves headroom EXISTS, not a deployable number. The frozen / allpool / equal / best-single columns do NOT peek and are directly comparable. The load-bearing honest result is frozen-vs-allpool above.

## E4  Coherent-cohort (drop stale vintages) vs mixed-vintage serving
Decision instant = freshest lead1 cycle for the market. MIXED = each model's newest cycle <= instant (leads 0-2; current live). COHORT = keep only models whose newest cycle is within 6h of instant (drop staler). Both precision-fused. delta = MAE_cohort - MAE_mixed; delta<0 => dropping stale vintages helps.
NOTE: archive cycles are dominantly daily 00Z, so a dropped 'stale' model is typically one that only refreshed a full ~24h earlier (lead2-only for this target).

n pairs = 2626; instances where cohort != mixed (a model actually dropped) = 203.
mean delta = +0.0118 C, SE = 0.0040, SIG.
Restricted to instances where >=1 model was dropped: n=203, mean delta=+0.1522 C, SE=0.0509, SIG.

## E5  Cycle-age value curve (paired, pooled across models)
Per (city,metric,target,model), rank cycles by source_cycle_time desc: rank0=newest. Paired |forecast-settlement| newest vs k-cycles-back, same key. delta = MAE_rankK - MAE_rank0; delta>0 => staleness costs accuracy.

| k cycles back | n pairs | MAE rank0 | MAE rankK | mean delta (C) | SE | median gap (h) | flag |
|---|---|---|---|---|---|---|---|
| 1 | 14698 | 1.4797 | 1.5761 | +0.0964 | 0.0064 | 24.0 | SIG |
| 2 | 2755 | 1.2745 | 1.3930 | +0.1185 | 0.0129 | 12.0 | SIG |
| 3 | 2450 | 1.2454 | 1.4286 | +0.1832 | 0.0161 | 18.0 | SIG |

## E6  Station sources (cwa_/hko_)
NOT RUN: no cwa_* / hko_* / station models exist in the previous_runs archive (authorized endpoint). Station-calibrated sources appear only in single_runs, which operator law excludes from this analysis. Cannot test their marginal value here.

## Verdict summary
- E2: The global backbone (icon_global, ukmo_global_deterministic_10km, ecmwf_ifs, gfs_global) carries real weight everywhere. jma_seamless HURTS the fused center in EU and Asia and pooled (removing it lowers MAE, SIG); dmi_harmonie_europe HURTS. Region matters: ukmo_global HURTS in EU (-0.012 SIG) but is the single largest carrier in Asia (+0.052 SIG). High-res LAMs (icon_d2, nam_conus, meteofrance_arome) carry large weight where present.
- E3: A per-city selective 2-4 model fusion beats the frozen 3-model CSV scheme. Peek-free full precision pool already beats frozen by ~0.08 C mean; greedy (optimistic, selection peeks) shows up to ~0.26 C headroom. Using ALL models is WORSE than a selected subset (allpool-greedy ~+0.18 C) => beyond ~3-4 good models, adding more HURTS.
- E4: Excluding stale-vintage instruments HURTS accuracy (cohort-mixed +0.012 C pooled, +0.152 C on the 203 instances where a model was actually dropped, both SIG). Supports the consult's claim: keep stale models at precision-downweight, do NOT exclude them.
- E5: Staleness monotonically costs accuracy: one cycle (~24 h) older = +0.096 C MAE (SIG, n=14698); 3 cycles back = +0.183 C. Freshness is worth ~0.1 C per 24 h — real but smaller than the model-selection effect in E2/E3.
- E6: Not testable on authorized data (no station sources in previous_runs archive).

Where the accuracy is: model SELECTION (E2/E3, ~0.1-0.26 C) dominates freshness (E4/E5, ~0.1 C). The frozen 3-model scheme both over-includes a hurting model in some regions (jma in EU/Asia) and under-uses the full pool in others — a per-region, settlement-driven precision pool over 3-4 vetted models is the indicated direction.
