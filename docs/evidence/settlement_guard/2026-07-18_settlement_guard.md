# Settlement Guard Report — 2026-07-18T09:15:00.007167+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 91 | 65 | 71.4% | [61.0%, 80.4%] | $+102.60 | YES |
| 30d | 157 | 98 | 62.4% | [54.3%, 70.0%] | $+30.75 | YES |

## Overall (all settled traded fills)

- settled trades: **188** (wins 119, losses 69)
- win-rate: **63.3%** (raw 63.3%), 95% CI [56.0%, 70.2%]
- after-cost PnL: **$+22.92** on $1653.32 cost basis (ROI 1.4%)
- calibration: mean entry q=0.887 vs realized win-rate=63.3%; Brier=0.2529, log-loss=1.1677 (n_with_q=129)

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 173 | 117 | 67.6% | [60.1%, 74.5%] | $-14.47 |
| buy_yes | 15 | 2 | n/a | [1.7%, 40.5%] | $+37.38 |

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
| Helsinki|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+4.15 |
| Hong Kong|high | 11 | 9 | n/a | [48.2%, 97.7%] | $+26.60 |
| Hong Kong|low | 3 | 2 | n/a | [9.4%, 99.2%] | $+3.13 |
| Houston|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-25.93 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Jeddah|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-3.57 |
| Karachi|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-33.18 |
| Kuala Lumpur|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+2.48 |
| London|high | 3 | 3 | n/a | [29.2%, 100.0%] | $+13.30 |
| London|low | 2 | 1 | n/a | [1.3%, 98.7%] | $+2.86 |
| Lucknow|high | 4 | 2 | n/a | [6.8%, 93.2%] | $+41.37 |
| Madrid|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-0.78 |
| Manila|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-22.58 |
| Mexico City|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-16.08 |
| Milan|high | 4 | 2 | n/a | [6.8%, 93.2%] | $+14.62 |
| Moscow|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.07 |
| Munich|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-5.81 |
| Panama City|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.99 |
| Paris|high | 19 | 1 | n/a | [0.1%, 26.0%] | $-11.14 |
| Paris|low | 3 | 2 | n/a | [9.4%, 99.2%] | $+0.06 |
| Qingdao|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.32 |
| San Francisco|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.35 |
| Sao Paulo|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.35 |
| Seattle|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.54 |
| Seoul|high | 40 | 38 | 95.0% | [83.1%, 99.4%] | $+175.08 |
| Seoul|low | 3 | 1 | n/a | [0.8%, 90.6%] | $-7.60 |
| Shanghai|high | 5 | 4 | n/a | [28.4%, 99.5%] | $+10.29 |
| Shenzhen|high | 7 | 6 | n/a | [42.1%, 99.6%] | $+22.71 |
| Singapore|high | 3 | 1 | n/a | [0.8%, 90.6%] | $+0.73 |
| Taipei|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-9.46 |
| Tel Aviv|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-13.63 |
| Tokyo|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+3.25 |
| Tokyo|low | 5 | 3 | n/a | [14.7%, 94.7%] | $+3.21 |
| Warsaw|high | 6 | 3 | n/a | [11.8%, 88.2%] | $-14.39 |
| Wellington|high | 7 | 3 | n/a | [9.9%, 81.6%] | $-120.99 |
| Wuhan|high | 14 | 13 | n/a | [66.1%, 99.8%] | $+9.20 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

| city|metric | window | n | wins | 95% CI | reason |
|---|---|---|---|---|---|
| Paris|high | 30d | 18 | 0 | [0.0%, 18.5%] | rolling 30d win-rate CI upper bound 0.185 < 0.50 |

## Notes

- fee coverage gap: 55 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
