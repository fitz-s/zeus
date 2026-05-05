# Phase 1 §2.1 — Hard Floor Calibration Results

Created: 2026-05-03 (executed)
Authority: PLAN.md §2.1 + operator Ruling 3 (2026-05-03)
Source: `wu_icao_history` primary only (fallbacks are not authoritative for settlement-floor detection)
Train window: 2025-07-01 → 2025-12-31; Test window: 2026-01-01 → 2026-04-30
Directional window: peak_hour ± 3 (HIGH track default)

## Per-city directional coverage (HIGH window, primary source only)

| city | peak_hr | n train | min | P05 | P10 | P25 | P50 | mean | zero days | n test | test min | test P05 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Lagos | 14.0 | 183 | 0.286 | 0.443 | 0.571 | 0.857 | 1.000 | 0.873 | 0 | 97 | 0.143 | 0.143 |
| Shenzhen | 14.3 | 184 | 0.429 | 0.714 | 0.714 | 0.857 | 1.000 | 0.926 | 0 | 120 | 0.286 | 1.000 |
| Jakarta | 13.5 | 184 | 0.143 | 0.714 | 0.757 | 0.857 | 1.000 | 0.934 | 0 | 120 | 0.286 | 1.000 |
| Amsterdam | 14.5 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Ankara | 15.2 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Atlanta | 17.6 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 119 | 0.857 | 0.986 |
| Auckland | 14.0 | 183 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Austin | 17.7 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.998 | 0 | 119 | 0.429 | 0.714 |
| Beijing | 15.0 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Buenos Aires | 15.5 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.998 | 0 | 120 | 0.857 | 1.000 |
| Busan | 14.5 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.997 | 0 | 120 | 0.857 | 1.000 |
| Cape Town | 14.5 | 184 | 0.714 | 0.857 | 1.000 | 1.000 | 1.000 | 0.991 | 0 | 120 | 0.857 | 0.857 |
| Chengdu | 15.0 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.999 | 0 | 120 | 0.714 | 0.857 |
| Chicago | 16.4 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 119 | 0.857 | 1.000 |
| Chongqing | 15.2 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.999 | 0 | 120 | 0.571 | 1.000 |
| Dallas | 17.7 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 119 | 0.571 | 0.714 |
| Denver | 16.1 | 180 | 0.429 | 1.000 | 1.000 | 1.000 | 1.000 | 0.990 | 0 | 119 | 0.286 | 0.571 |
| Guangzhou | 14.5 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Helsinki | 14.0 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Houston | 17.4 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.998 | 0 | 119 | 0.429 | 0.714 |
| Jeddah | 14.5 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 0.714 | 0.993 |
| Karachi | 15.0 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 0.857 | 1.000 |
| Kuala Lumpur | 14.5 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 0.857 | 1.000 |
| London | 14.0 | 184 | 0.714 | 1.000 | 1.000 | 1.000 | 1.000 | 0.998 | 0 | 120 | 0.857 | 1.000 |
| Los Angeles | 18.1 | 184 | 0.714 | 1.000 | 1.000 | 1.000 | 1.000 | 0.998 | 0 | 119 | 0.429 | 0.714 |
| Lucknow | 14.8 | 183 | 0.286 | 1.000 | 1.000 | 1.000 | 1.000 | 0.984 | 0 | 119 | 0.714 | 1.000 |
| Madrid | 16.0 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Manila | 14.0 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.997 | 0 | 120 | 0.857 | 0.993 |
| Mexico City | 15.5 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.996 | 0 | 120 | 0.857 | 0.857 |
| Miami | 17.3 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 119 | 0.571 | 0.843 |
| Milan | 15.0 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Munich | 14.8 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| NYC | 15.8 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 119 | 0.714 | 0.857 |
| Panama City | 14.0 | 184 | 0.714 | 0.857 | 1.000 | 1.000 | 1.000 | 0.989 | 0 | 114 | 0.429 | 0.950 |
| Paris | 14.5 | 184 | 0.429 | 1.000 | 1.000 | 1.000 | 1.000 | 0.993 | 0 | 120 | 0.857 | 1.000 |
| San Francisco | 16.7 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.999 | 0 | 119 | 0.714 | 0.857 |
| Sao Paulo | 15.0 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 0.714 | 0.993 |
| Seattle | 13.8 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 119 | 0.571 | 0.714 |
| Seoul | 14.8 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 0.857 | 1.000 |
| Shanghai | 14.6 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Singapore | 14.0 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Taipei | 14.5 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 0.857 | 1.000 |
| Tokyo | 14.7 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Toronto | 15.5 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 119 | 0.857 | 1.000 |
| Warsaw | 14.8 | 184 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Wellington | 14.0 | 183 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 120 | 1.000 | 1.000 |
| Wuhan | 15.0 | 184 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.999 | 0 | 120 | 0.571 | 1.000 |

### Cities with no `wu_icao_history` data in train window

- Hong Kong (peak_hour=14.5)
- Istanbul (peak_hour=15.0)
- Moscow (peak_hour=15.0)
- Tel Aviv (peak_hour=14.5)

## Proposed `hard_floor_for_settlement` per city

**Rule used**: hard_floor = train P10 (90% of routine days are above this).
Per Ruling 3, `cities.json` reserves a `hard_floor_for_settlement` field as override interface; default `null` → uses this data-derived value.

| city | data-derived floor (train P10) | sanity vs test min |
|---|---|---|
| Lagos | 0.571 | 0.143 ⚠ test min 0.143 < floor — outage detected in test |
| Shenzhen | 0.714 | 0.286 ⚠ test min 0.286 < floor — outage detected in test |
| Jakarta | 0.757 | 0.286 ⚠ test min 0.286 < floor — outage detected in test |
| Amsterdam | 1.000 | 1.000 |
| Ankara | 1.000 | 1.000 |
| Atlanta | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Auckland | 1.000 | 1.000 |
| Austin | 1.000 | 0.429 ⚠ test min 0.429 < floor — outage detected in test |
| Beijing | 1.000 | 1.000 |
| Buenos Aires | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Busan | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Cape Town | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Chengdu | 1.000 | 0.714 ⚠ test min 0.714 < floor — outage detected in test |
| Chicago | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Chongqing | 1.000 | 0.571 ⚠ test min 0.571 < floor — outage detected in test |
| Dallas | 1.000 | 0.571 ⚠ test min 0.571 < floor — outage detected in test |
| Denver | 1.000 | 0.286 ⚠ test min 0.286 < floor — outage detected in test |
| Guangzhou | 1.000 | 1.000 |
| Helsinki | 1.000 | 1.000 |
| Houston | 1.000 | 0.429 ⚠ test min 0.429 < floor — outage detected in test |
| Jeddah | 1.000 | 0.714 ⚠ test min 0.714 < floor — outage detected in test |
| Karachi | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Kuala Lumpur | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| London | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Los Angeles | 1.000 | 0.429 ⚠ test min 0.429 < floor — outage detected in test |
| Lucknow | 1.000 | 0.714 ⚠ test min 0.714 < floor — outage detected in test |
| Madrid | 1.000 | 1.000 |
| Manila | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Mexico City | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Miami | 1.000 | 0.571 ⚠ test min 0.571 < floor — outage detected in test |
| Milan | 1.000 | 1.000 |
| Munich | 1.000 | 1.000 |
| NYC | 1.000 | 0.714 ⚠ test min 0.714 < floor — outage detected in test |
| Panama City | 1.000 | 0.429 ⚠ test min 0.429 < floor — outage detected in test |
| Paris | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| San Francisco | 1.000 | 0.714 ⚠ test min 0.714 < floor — outage detected in test |
| Sao Paulo | 1.000 | 0.714 ⚠ test min 0.714 < floor — outage detected in test |
| Seattle | 1.000 | 0.571 ⚠ test min 0.571 < floor — outage detected in test |
| Seoul | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Shanghai | 1.000 | 1.000 |
| Singapore | 1.000 | 1.000 |
| Taipei | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Tokyo | 1.000 | 1.000 |
| Toronto | 1.000 | 0.857 ⚠ test min 0.857 < floor — outage detected in test |
| Warsaw | 1.000 | 1.000 |
| Wellington | 1.000 | 1.000 |
| Wuhan | 1.000 | 0.571 ⚠ test min 0.571 < floor — outage detected in test |

