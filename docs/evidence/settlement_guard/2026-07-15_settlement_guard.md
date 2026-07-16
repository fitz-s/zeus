# Settlement Guard Report — 2026-07-15T09:15:00.123896+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 10 | 7 | n/a | [34.8%, 93.3%] | $-76.21 | — |
| 30d | 81 | 42 | 51.9% | [40.5%, 63.1%] | $-159.78 | no |

## Overall (all settled traded fills)

- settled trades: **111** (wins 64, losses 47)
- win-rate: **57.7%** (raw 57.7%), 95% CI [47.9%, 67.0%]
- after-cost PnL: **$-141.88** on $1003.50 cost basis (ROI -14.1%)
- calibration: mean entry q=0.747 vs realized win-rate=57.7%; Brier=0.2482, log-loss=0.9251 (n_with_q=53)

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 98 | 62 | 63.3% | [52.9%, 72.8%] | $-184.79 |
| buy_yes | 13 | 2 | n/a | [1.9%, 45.4%] | $+42.90 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Ankara|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.81 |
| Buenos Aires|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+12.18 |
| Busan|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.70 |
| Cape Town|high | 2 | 0 | n/a | [0.0%, 84.2%] | $-21.88 |
| Chengdu|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-7.29 |
| Chicago|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+8.44 |
| Chongqing|high | 4 | 2 | n/a | [6.8%, 93.2%] | $-19.11 |
| Dallas|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-2.37 |
| Denver|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+6.82 |
| Guangzhou|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-27.03 |
| Helsinki|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Hong Kong|high | 6 | 4 | n/a | [22.3%, 95.7%] | $+3.90 |
| Hong Kong|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+9.18 |
| Houston|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-25.93 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Karachi|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-33.18 |
| Kuala Lumpur|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+2.48 |
| London|high | 3 | 3 | n/a | [29.2%, 100.0%] | $+13.30 |
| London|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.81 |
| Lucknow|high | 4 | 2 | n/a | [6.8%, 93.2%] | $+41.37 |
| Madrid|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-0.78 |
| Manila|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-22.58 |
| Milan|high | 4 | 2 | n/a | [6.8%, 93.2%] | $+14.62 |
| Moscow|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.07 |
| Munich|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-5.81 |
| Panama City|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.99 |
| Paris|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.92 |
| Paris|low | 3 | 2 | n/a | [9.4%, 99.2%] | $+0.06 |
| Qingdao|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.32 |
| San Francisco|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.35 |
| Sao Paulo|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.35 |
| Seattle|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.54 |
| Seoul|high | 4 | 2 | n/a | [6.8%, 93.2%] | $+6.13 |
| Seoul|low | 3 | 1 | n/a | [0.8%, 90.6%] | $-7.60 |
| Shanghai|high | 4 | 3 | n/a | [19.4%, 99.4%] | $+7.40 |
| Shenzhen|high | 7 | 6 | n/a | [42.1%, 99.6%] | $+22.71 |
| Singapore|high | 3 | 1 | n/a | [0.8%, 90.6%] | $+0.73 |
| Taipei|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-9.46 |
| Tel Aviv|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-13.63 |
| Tokyo|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+3.25 |
| Tokyo|low | 5 | 3 | n/a | [14.7%, 94.7%] | $+3.21 |
| Warsaw|high | 6 | 3 | n/a | [11.8%, 88.2%] | $-14.39 |
| Wellington|high | 7 | 3 | n/a | [9.9%, 81.6%] | $-120.99 |
| Wuhan|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-5.17 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

None. No city's rolling win-rate CI upper bound is below 50%.

## Notes

- fee coverage gap: 54 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
