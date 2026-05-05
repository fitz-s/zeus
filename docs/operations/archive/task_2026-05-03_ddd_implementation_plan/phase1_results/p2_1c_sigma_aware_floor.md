# Phase 1 §2.1c — σ-aware Floor Recalibration

Created: 2026-05-03 (executed)
Authority: PLAN.md §2.1 + operator reminder 2026-05-03 (σ-band rule)

## Why this pass exists

p2_1b counted any train day below the candidate floor as a 'false positive'.
But §6 formula uses `shortfall = max(0, floor - cov - 1*σ)`, so a day only
'fires' DDD when `cov < floor - σ`. The σ-band absorbs Poisson noise (e.g.,
a single 1-hour drop on a 7-hour window = 1-(6/7) = 0.143 below 1.0). p2_1b's
naive FP counting was overly conservative and pulled some recommendations
downward unnecessarily.

This pass redefines FP rate as `% days with cov < floor - σ_train` and
re-runs the recommendation. Differences vs p2_1b are highlighted.

## Per-city σ-aware recommendation (sorted by train_min)

| city | n_days | min | mean | σ_train | p2_1b floor | σ-aware floor | Δ |
|---|---|---|---|---|---|---|---|
| Jakarta | 184 | 0.143 | 0.934 | 0.127 | 0.35 | **0.35** | — |
| Lagos | 183 | 0.286 | 0.873 | 0.178 | 0.35 | **0.45** | **+0.10** |
| Lucknow | 183 | 0.286 | 0.984 | 0.085 | 0.40 | **0.50** | **+0.10** |
| Denver | 180 | 0.429 | 0.990 | 0.064 | 0.55 | **0.60** | **+0.05** |
| Paris | 184 | 0.429 | 0.993 | 0.056 | 0.55 | **0.60** | **+0.05** |
| Shenzhen | 184 | 0.429 | 0.926 | 0.122 | 0.40 | **0.55** | **+0.15** |
| Cape Town | 184 | 0.714 | 0.991 | 0.038 | 0.85 | **0.85** | — |
| London | 184 | 0.714 | 0.998 | 0.021 | 0.85 | **0.85** | — |
| Los Angeles | 184 | 0.714 | 0.998 | 0.023 | 0.85 | **0.85** | — |
| Panama City | 184 | 0.714 | 0.989 | 0.041 | 0.85 | **0.85** | — |
| Austin | 184 | 0.857 | 0.998 | 0.018 | 0.85 | **0.85** | — |
| Buenos Aires | 184 | 0.857 | 0.998 | 0.015 | 0.85 | **0.85** | — |
| Busan | 184 | 0.857 | 0.997 | 0.021 | 0.85 | **0.85** | — |
| Chengdu | 184 | 0.857 | 0.999 | 0.011 | 0.85 | **0.85** | — |
| Chongqing | 184 | 0.857 | 0.999 | 0.011 | 0.85 | **0.85** | — |
| Houston | 184 | 0.857 | 0.998 | 0.015 | 0.85 | **0.85** | — |
| Manila | 184 | 0.857 | 0.997 | 0.021 | 0.85 | **0.85** | — |
| Mexico City | 184 | 0.857 | 0.996 | 0.023 | 0.85 | **0.85** | — |
| San Francisco | 184 | 0.857 | 0.999 | 0.011 | 0.85 | **0.85** | — |
| Wuhan | 184 | 0.857 | 0.999 | 0.011 | 0.85 | **0.85** | — |
| Amsterdam | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Ankara | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Atlanta | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Auckland | 183 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Beijing | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Chicago | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Dallas | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Guangzhou | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Helsinki | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Jeddah | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Karachi | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Kuala Lumpur | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Madrid | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Miami | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Milan | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Munich | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| NYC | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Sao Paulo | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Seattle | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Seoul | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Shanghai | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Singapore | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Taipei | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Tokyo | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Toronto | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Warsaw | 184 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |
| Wellington | 183 | 1.000 | 1.000 | 0.000 | 0.85 | **0.85** | — |

## Cities where σ-band materially raised the recommendation

These cities had Poisson-noise days (1-hour drops) that pulled the naive floor down. The σ-band correctly recognizes those drops as routine variance, allowing a higher (more sensitive) floor.

- **Shenzhen**: 0.40 → 0.55 (σ_train=0.122, train_min=0.429)
- **Lagos**: 0.35 → 0.45 (σ_train=0.178, train_min=0.286)
- **Lucknow**: 0.40 → 0.50 (σ_train=0.085, train_min=0.286)
- **Denver**: 0.55 → 0.60 (σ_train=0.064, train_min=0.429)
- **Paris**: 0.55 → 0.60 (σ_train=0.056, train_min=0.429)

## Final recommended hard_floor_for_settlement values

Group cities by recommended floor:

### Floor = 0.35 (1 cities)

Jakarta

### Floor = 0.45 (1 cities)

Lagos

### Floor = 0.50 (1 cities)

Lucknow

### Floor = 0.55 (1 cities)

Shenzhen

### Floor = 0.60 (2 cities)

Denver, Paris

### Floor = 0.85 (41 cities)

Amsterdam, Ankara, Atlanta, Auckland, Austin, Beijing, Buenos Aires, Busan, Cape Town, Chengdu, Chicago, Chongqing, Dallas, Guangzhou, Helsinki, Houston, Jeddah, Karachi, Kuala Lumpur, London, Los Angeles, Madrid, Manila, Mexico City, Miami, Milan, Munich, NYC, Panama City, San Francisco, Sao Paulo, Seattle, Seoul, Shanghai, Singapore, Taipei, Tokyo, Toronto, Warsaw, Wellington, Wuhan

## Catastrophic test-day detection

Total test days with cov < 0.35 (absolute physics): 15

All catastrophic days have cov < 0.35; they are caught by Day-0 §7 rail 2 (absolute kill at 0.35) AND by §6 historical DDD (any floor ≥ 0.35 detects them).

