# ENS Route 6 Experiment — Day-Specific Δ Transport β

Created: 2026-05-25
Authority: ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md §3 Route 6
DB: /tmp/ens_refit/full.db (read-only)
Metric: HIGH

## Data Coverage Assessment

Route 6 requires PAIRED F25 (opendata) and F50 (TIGGE) ensemble snapshots
per (city, target_date, lead_day). Coverage in full.db:

| City | TIGGE days | Paired days | Route 6 testable? |
| ---- | ---------- | ----------- | ----------------- |
| Amsterdam | 7554 | 4 | Marginal (<5 pairs) |
| Ankara | 7550 | 0 | NO |
| Atlanta | 7544 | 0 | NO |
| Auckland | 7458 | 0 | NO |
| Austin | 7544 | 0 | NO |
| Beijing | 7569 | 1 | Marginal (<5 pairs) |
| Buenos Aires | 7544 | 0 | NO |
| Busan | 7568 | 0 | NO |
| Cape Town | 7545 | 0 | NO |
| Chengdu | 7569 | 1 | Marginal (<5 pairs) |
| Chicago | 7550 | 6 | Yes |
| Chongqing | 7569 | 1 | Marginal (<5 pairs) |
| Dallas | 7544 | 0 | NO |
| Denver | 7544 | 0 | NO |
| Guangzhou | 7568 | 0 | NO |
| Helsinki | 7545 | 0 | NO |
| Hong Kong | 7568 | 0 | NO |
| Houston | 7544 | 0 | NO |
| Istanbul | 7545 | 0 | NO |
| Jakarta | 7568 | 0 | NO |
| Jeddah | 7553 | 8 | Yes |
| Karachi | 7568 | 0 | NO |
| Kuala Lumpur | 7573 | 5 | Yes |
| Lagos | 7545 | 0 | NO |
| London | 7545 | 0 | NO |
| Los Angeles | 7544 | 0 | NO |
| Lucknow | 7568 | 0 | NO |
| Madrid | 7545 | 0 | NO |
| Manila | 7569 | 1 | Marginal (<5 pairs) |
| Mexico City | 7544 | 0 | NO |
| Miami | 7545 | 1 | Marginal (<5 pairs) |
| Milan | 7545 | 0 | NO |
| Moscow | 7545 | 0 | NO |
| Munich | 7545 | 0 | NO |
| NYC | 7544 | 0 | NO |
| Panama City | 7544 | 0 | NO |
| Paris | 7545 | 0 | NO |
| Qingdao | 46 | 1 | Marginal (<5 pairs) |
| San Francisco | 7544 | 0 | NO |
| Sao Paulo | 7544 | 0 | NO |
| Seattle | 7544 | 0 | NO |
| Seoul | 7569 | 1 | Marginal (<5 pairs) |
| Shanghai | 7568 | 0 | NO |
| Shenzhen | 7569 | 1 | Marginal (<5 pairs) |
| Singapore | 7568 | 0 | NO |
| Taipei | 7568 | 0 | NO |
| Tel Aviv | 7545 | 0 | NO |
| Tokyo | 7576 | 8 | Yes |
| Toronto | 7544 | 0 | NO |
| Warsaw | 7545 | 0 | NO |
| Wellington | 7455 | 1 | Marginal (<5 pairs) |
| Wuhan | 7568 | 0 | NO |

### §4.1 Catastrophic Regression Cities

| City | Paired days | Route 6 status |
| ---- | ----------- | -------------- |
| Hong Kong | 0 | UNTESTABLE — no paired overlap |
| Miami | 1 | MARGINAL — 1 paired days only |
| Moscow | 0 | UNTESTABLE — no paired overlap |

## Experiment Design

**Model**: b_{25,i} = b_50 + μ_Δ + β(Δ_i − μ_Δ), β~N(0,1/λ²) ridge.
  In Gaussian terms: μ_new_i = μ_fit_i + β·(Δ_i − μ_Δ).
**Baseline**: β=0 (Gaussian, same μ_fit from full_transport p_raw).
**Ridge λ**: 1.0 (strong shrinkage per roadmap).
**OOS**: 5-fold blocked on sorted decision_group_id.
**Groups**: 33 paired distributions.

## Global Result (paired cities only)

| Metric | Baseline (Gaussian) | Route 6 | Δ | Verdict |
| ------ | ------------------- | ------- | -- | ------- |
| Brier | 0.8885 | 1.0240 | +0.1355 | FAIL |
| LogLoss | 2.7104 | 7.1780 | +4.4677 | FAIL |
| RPS | 1.8754 | 4.3195 | +2.4440 | FAIL |

*MC p_raw reference: Brier=0.8874, LL=2.7372, RPS=1.8835*

**Note**: This result covers 33 paired distributions,
which do NOT include HK or Miami (zero paired overlap).
This test validates Route 6 methodology on cities where it IS testable,
but CANNOT speak to the §4.1 catastrophic regression acceptance gate.


### Coastal vs Inland

| Cohort                 |      n |  Δ Brier |  Δ LogLoss |    Δ RPS |   mean β | Verdict |
| ---------------------- | ------ | -------- | ---------- | -------- | -------- | ------- |
| coastal                |      7 |  -0.0063 |    +2.9315 |  +2.0792 |  -1.4693 | R6 wins 1/3 |
| inland                 |     26 |  +0.1736 |    +4.8813 |  +2.5422 |  -1.6137 | R6 loses all |

### Temperature Unit

| Cohort                 |      n |  Δ Brier |  Δ LogLoss |    Δ RPS |   mean β | Verdict |
| ---------------------- | ------ | -------- | ---------- | -------- | -------- | ------- |
| unit=°C                |     29 |  +0.1475 |    +4.9120 |  +2.5715 |  -1.6221 | R6 loses all |
| unit=°F                |      4 |  +0.0483 |    +1.2461 |  +1.5195 |  -1.2998 | R6 loses all |

### Per City

| Cohort                 |      n |  Δ Brier |  Δ LogLoss |    Δ RPS |   mean β | Verdict |
| ---------------------- | ------ | -------- | ---------- | -------- | -------- | ------- |
| Amsterdam              |      4 |  +0.4397 |   +11.0745 |  +3.5332 |  -1.7487 | R6 loses all |
| Beijing                |      1 |  +0.0528 |    +0.6579 |  +3.3953 |  -1.8381 | R6 loses all |
| Chengdu                |      1 |  +0.0058 |    +0.0405 |  +0.1531 |  -1.2222 | R6 loses all |
| Chicago                |      4 |  +0.0483 |    +1.2461 |  +1.5195 |  -1.2998 | R6 loses all |
| Chongqing              |      1 |  -0.0323 |    -0.1268 |  -0.2084 |  -1.0456 | R6 wins all |
| Jeddah                 |      6 |  +0.0029 |    +0.0380 |  +0.2605 |  -1.5265 | R6 loses all |
| Kuala Lumpur           |      4 |  +0.3134 |   +11.2642 |  +5.4038 |  -2.4110 | R6 loses all |
| Manila                 |      1 |  +0.1605 |    +2.4398 |  +2.6943 |  -1.2084 | R6 loses all |
| Qingdao                |      1 |  +0.0503 |    +0.4642 |  +1.0616 |  -1.2222 | R6 loses all |
| Seoul                  |      1 |  +0.1861 |   +16.6008 | +11.2409 |  -1.8381 | R6 loses all |
| Shenzhen               |      1 |  +0.0266 |    +0.1530 |  +0.4250 |  -1.2084 | R6 loses all |
| Tokyo                  |      7 |  -0.0063 |    +2.9315 |  +2.0792 |  -1.4693 | R6 wins 1/3 |
| Wellington             |      1 |  +0.8415 |   +12.1161 |  +3.9479 |  -1.3754 | R6 loses all |

## Final Verdict

FAIL or PARTIAL on testable subset (LL/RPS wins: 0/2).
Route 6 does not meet §3 gate even on cities where it is testable.
Combined with UNTESTABLE status on HK/Miami: Route 6 is not warranted.
