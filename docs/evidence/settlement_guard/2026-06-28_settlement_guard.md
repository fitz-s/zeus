# Settlement Guard Report — 2026-06-28T09:15:00.162209+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 17 | 6 | n/a | [14.2%, 61.7%] | $-23.61 | — |
| 30d | 70 | 41 | 58.6% | [46.2%, 70.2%] | $-24.51 | no |

## Overall (all settled traded fills)

- settled trades: **70** (wins 41, losses 29)
- win-rate: **58.6%** (raw 58.6%), 95% CI [46.2%, 70.2%]
- after-cost PnL: **$-24.51** on $476.33 cost basis (ROI -5.1%)
- calibration: mean entry q=0.626 vs realized win-rate=58.6%; Brier=0.1694, log-loss=0.5104 (n_with_q=12)

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 61 | 40 | 65.6% | [52.3%, 77.3%] | $-23.27 |
| buy_yes | 9 | 1 | n/a | [0.3%, 48.2%] | $-1.24 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Buenos Aires|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+12.18 |
| Chengdu|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-7.29 |
| Chicago|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+8.44 |
| Chongqing|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-16.74 |
| Denver|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+6.82 |
| Helsinki|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Hong Kong|high | 5 | 3 | n/a | [14.7%, 94.7%] | $+1.59 |
| Houston|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-25.93 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Karachi|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-33.18 |
| Kuala Lumpur|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-6.03 |
| London|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.05 |
| London|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.81 |
| Lucknow|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.83 |
| Madrid|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-0.78 |
| Manila|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.95 |
| Milan|high | 3 | 2 | n/a | [9.4%, 99.2%] | $+16.09 |
| Munich|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-0.23 |
| Paris|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.92 |
| Paris|low | 3 | 2 | n/a | [9.4%, 99.2%] | $+0.06 |
| Qingdao|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.32 |
| San Francisco|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.35 |
| Seattle|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.54 |
| Seoul|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-2.06 |
| Seoul|low | 2 | 0 | n/a | [0.0%, 84.2%] | $-13.49 |
| Shanghai|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-2.97 |
| Shenzhen|high | 4 | 4 | n/a | [39.8%, 100.0%] | $+15.60 |
| Singapore|high | 2 | 0 | n/a | [0.0%, 84.2%] | $-1.03 |
| Tokyo|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+3.25 |
| Tokyo|low | 3 | 2 | n/a | [9.4%, 99.2%] | $+6.89 |
| Warsaw|high | 5 | 3 | n/a | [14.7%, 94.7%] | $-8.29 |
| Wellington|high | 4 | 2 | n/a | [6.8%, 93.2%] | $+1.73 |
| Wuhan|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-6.59 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

None. No city's rolling win-rate CI upper bound is below 50%.

## Notes

- fee coverage gap: 54 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
