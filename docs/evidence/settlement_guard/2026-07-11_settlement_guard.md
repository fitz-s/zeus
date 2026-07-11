# Settlement Guard Report — 2026-07-11T09:15:00.025701+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 0 | 0 | n/a | [0.0%, 100.0%] | $+0.00 | — |
| 30d | 78 | 38 | 48.7% | [37.2%, 60.3%] | $-87.28 | no |

## Overall (all settled traded fills)

- settled trades: **101** (wins 57, losses 44)
- win-rate: **56.4%** (raw 56.4%), 95% CI [46.2%, 66.3%]
- after-cost PnL: **$-68.01** on $728.79 cost basis (ROI -9.3%)
- calibration: mean entry q=0.702 vs realized win-rate=56.4%; Brier=0.2490, log-loss=0.7718 (n_with_q=43)

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 89 | 56 | 62.9% | [52.0%, 72.9%] | $-62.88 |
| buy_yes | 12 | 1 | n/a | [0.2%, 38.5%] | $-5.12 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Buenos Aires|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+12.18 |
| Cape Town|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-7.09 |
| Chengdu|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-7.29 |
| Chicago|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+8.44 |
| Chongqing|high | 4 | 2 | n/a | [6.8%, 93.2%] | $-19.11 |
| Dallas|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-2.37 |
| Denver|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+6.82 |
| Guangzhou|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.08 |
| Helsinki|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Hong Kong|high | 6 | 4 | n/a | [22.3%, 95.7%] | $+3.90 |
| Hong Kong|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+9.18 |
| Houston|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-25.93 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Karachi|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-33.18 |
| Kuala Lumpur|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+2.48 |
| London|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+7.64 |
| London|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.81 |
| Lucknow|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-6.63 |
| Madrid|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-0.78 |
| Manila|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-22.58 |
| Milan|high | 4 | 2 | n/a | [6.8%, 93.2%] | $+14.60 |
| Moscow|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.07 |
| Munich|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-5.81 |
| Panama City|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.99 |
| Paris|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.92 |
| Paris|low | 3 | 2 | n/a | [9.4%, 99.2%] | $+0.06 |
| Qingdao|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.32 |
| San Francisco|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.35 |
| Sao Paulo|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.35 |
| Seattle|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.54 |
| Seoul|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-2.06 |
| Seoul|low | 3 | 1 | n/a | [0.8%, 90.6%] | $-7.60 |
| Shanghai|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-0.01 |
| Shenzhen|high | 7 | 6 | n/a | [42.1%, 99.6%] | $+22.71 |
| Singapore|high | 3 | 1 | n/a | [0.8%, 90.6%] | $+2.51 |
| Taipei|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-9.46 |
| Tel Aviv|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-13.63 |
| Tokyo|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+3.25 |
| Tokyo|low | 5 | 3 | n/a | [14.7%, 94.7%] | $+3.21 |
| Warsaw|high | 6 | 3 | n/a | [11.8%, 88.2%] | $-14.39 |
| Wellington|high | 5 | 2 | n/a | [5.3%, 85.3%] | $-18.99 |
| Wuhan|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-5.17 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

None. No city's rolling win-rate CI upper bound is below 50%.

## Notes

- fee coverage gap: 54 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
