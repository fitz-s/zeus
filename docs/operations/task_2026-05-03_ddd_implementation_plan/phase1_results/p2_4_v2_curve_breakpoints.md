# §2.4 Metric-Specific Binning + Bootstrap CIs — v2

Created: 2026-05-03  Authority: RERUN_PLAN_v2.md C2-C3

> Paris pending workstream A resync; rerun for Paris after A completes.

## Fixes

- H2: HIGH errors binned by HIGH-cov shortfall; LOW by LOW-cov shortfall
- Structural: shortfall = max(0, floor - cov)  [NO sigma]

## LOW Windows Derived

| city | source | low_peak_hour |
|---|---|---|
| Amsterdam | empirical | 5 |
| Ankara | empirical | 5 |
| Atlanta | empirical | 6 |
| Auckland | empirical | 6 |
| Austin | empirical | 6 |
| Beijing | empirical | 5 |
| Buenos Aires | empirical | 6 |
| Busan | empirical | 6 |
| Cape Town | empirical | 6 |
| Chengdu | empirical | 6 |
| Chicago | empirical | 5 |
| Chongqing | empirical | 6 |
| Dallas | empirical | 6 |
| Denver | empirical | 5 |
| Guangzhou | empirical | 6 |
| Helsinki | empirical | 4 |
| Hong Kong | heuristic_fallback | 2 |
| Houston | empirical | 6 |
| Istanbul | heuristic_fallback | 3 |
| Jakarta | empirical | 5 |
| Jeddah | empirical | 6 |
| Karachi | empirical | 6 |
| Kuala Lumpur | empirical | 7 |
| Lagos | empirical | 7 |
| London | empirical | 5 |
| Los Angeles | empirical | 5 |
| Lucknow | empirical | 5 |
| Madrid | empirical | 7 |
| Manila | empirical | 6 |
| Mexico City | empirical | 6 |
| Miami | empirical | 6 |
| Milan | empirical | 6 |
| Moscow | heuristic_fallback | 3 |
| Munich | empirical | 5 |
| NYC | empirical | 5 |
| Panama City | empirical | 6 |
| San Francisco | empirical | 5 |
| Sao Paulo | empirical | 6 |
| Seattle | empirical | 5 |
| Seoul | empirical | 6 |
| Shanghai | empirical | 4 |
| Shenzhen | empirical | 5 |
| Singapore | empirical | 6 |
| Taipei | empirical | 5 |
| Tel Aviv | heuristic_fallback | 2 |
| Tokyo | empirical | 5 |
| Toronto | empirical | 6 |
| Warsaw | empirical | 5 |
| Wellington | empirical | 6 |
| Wuhan | empirical | 6 |

### HIGH Bins

| shortfall bin | N | error_mean | error_std |
|---|---|---|---|
| exact 0 | 4,796 | 0.7653 | 0.2338 |
| (0, 0.05) | 3 | 0.9526 | 0.0427 |
| [0.05, 0.10) | 0 | n/a | n/a |
| [0.10, 0.20) | 106 | 0.7590 | 0.2564 |
| [0.20, 0.30) | 44 | 0.7459 | 0.2313 |
| [0.30, 0.50) | 32 | 0.7677 | 0.2597 |
| [0.50, 1.0] | 11 | 0.8599 | 0.2078 |

### LOW Bins

| shortfall bin | N | error_mean | error_std |
|---|---|---|---|
| exact 0 | 2,259 | 0.7051 | 0.2955 |
| (0, 0.05) | 3 | 0.6674 | 0.3914 |
| [0.05, 0.10) | 0 | n/a | n/a |
| [0.10, 0.20) | 56 | 0.7648 | 0.2429 |
| [0.20, 0.30) | 19 | 0.7445 | 0.2436 |
| [0.30, 0.50) | 15 | 0.7069 | 0.3440 |
| [0.50, 1.0] | 9 | 0.9070 | 0.1569 |

---

## Bootstrap CIs

### HIGH — Bootstrap 95% CIs

| bin | n_groups | n_obs | mean_err | CI_lo | CI_hi | overlaps 0 |
|---|---|---|---|---|---|---|
| exact 0 | 4796 | 4,796 | 0.7653 | 0.7585 | 0.7719 | no |
| (0, 0.05) | 3 | 3 | 0.9526 | 0.9173 | 1.0000 | no |
| [0.05, 0.10) | 0 | 0 | n/a | n/a | n/a | no |
| [0.10, 0.20) | 106 | 106 | 0.7590 | 0.7113 | 0.8058 | no |
| [0.20, 0.30) | 44 | 44 | 0.7459 | 0.6800 | 0.8136 | no |
| [0.30, 0.50) | 32 | 32 | 0.7677 | 0.6761 | 0.8530 | no |
| [0.50, 1.0] | 11 | 11 | 0.8599 | 0.7351 | 0.9585 | no |

### LOW — Bootstrap 95% CIs

| bin | n_groups | n_obs | mean_err | CI_lo | CI_hi | overlaps 0 |
|---|---|---|---|---|---|---|
| exact 0 | 2259 | 2,259 | 0.7051 | 0.6931 | 0.7178 | no |
| (0, 0.05) | 3 | 3 | 0.6674 | 0.2155 | 0.8989 | no |
| [0.05, 0.10) | 0 | 0 | n/a | n/a | n/a | no |
| [0.10, 0.20) | 56 | 56 | 0.7648 | 0.6962 | 0.8237 | no |
| [0.20, 0.30) | 19 | 19 | 0.7445 | 0.6357 | 0.8470 | no |
| [0.30, 0.50) | 15 | 15 | 0.7069 | 0.5400 | 0.8689 | no |
| [0.50, 1.0] | 9 | 9 | 0.9070 | 0.7990 | 0.9850 | no |

### Adjacent indistinguishable pairs
**HIGH indistinguishable**: [0.10, 0.20) ↔ [0.20, 0.30), [0.20, 0.30) ↔ [0.30, 0.50)
**LOW indistinguishable**: [0.10, 0.20) ↔ [0.20, 0.30), [0.20, 0.30) ↔ [0.30, 0.50)
