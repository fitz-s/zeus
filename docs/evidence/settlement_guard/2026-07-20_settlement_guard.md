# Settlement Guard Report — 2026-07-20T09:15:00.182907+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 126 | 90 | 71.4% | [62.7%, 79.1%] | $+267.81 | YES |
| 30d | 179 | 115 | 64.2% | [56.8%, 71.3%] | $+98.63 | YES |

## Overall (all settled traded fills)

- settled trades: **229** (wins 147, losses 82)
- win-rate: **64.2%** (raw 64.2%), 95% CI [57.6%, 70.4%]
- after-cost PnL: **$+93.64** on $1959.35 cost basis (ROI 4.8%)
- calibration: mean entry q=0.883 vs realized win-rate=64.2%; Brier=0.2481, log-loss=1.0437 (n_with_q=170)

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 212 | 143 | 67.5% | [60.7%, 73.7%] | $+27.01 |
| buy_yes | 17 | 4 | n/a | [6.8%, 49.9%] | $+66.62 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Ankara|high | 3 | 3 | n/a | [29.2%, 100.0%] | $+21.15 |
| Austin|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.56 |
| Buenos Aires|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+12.18 |
| Busan|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.70 |
| Cape Town|high | 2 | 0 | n/a | [0.0%, 84.2%] | $-21.88 |
| Chengdu|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-7.29 |
| Chicago|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+8.44 |
| Chongqing|high | 7 | 5 | n/a | [29.0%, 96.3%] | $-10.75 |
| Dallas|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-2.37 |
| Denver|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+6.82 |
| Guangzhou|high | 5 | 4 | n/a | [28.4%, 99.5%] | $-20.20 |
| Helsinki|high | 4 | 2 | n/a | [6.8%, 93.2%] | $-9.23 |
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
| Manila|high | 6 | 1 | n/a | [0.4%, 64.1%] | $-41.55 |
| Mexico City|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-16.08 |
| Milan|high | 6 | 2 | n/a | [4.3%, 77.7%] | $-3.41 |
| Moscow|high | 9 | 9 | n/a | [66.4%, 100.0%] | $+51.38 |
| Munich|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-5.81 |
| Panama City|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-2.90 |
| Paris|high | 20 | 1 | 5.0% | [0.1%, 24.9%] | $-24.01 |
| Paris|low | 5 | 2 | n/a | [5.3%, 85.3%] | $-10.92 |
| Qingdao|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.32 |
| San Francisco|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.35 |
| Sao Paulo|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.35 |
| Seattle|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.54 |
| Seoul|high | 41 | 39 | 95.1% | [83.5%, 99.4%] | $+176.04 |
| Seoul|low | 5 | 3 | n/a | [14.7%, 94.7%] | $-4.44 |
| Shanghai|high | 5 | 4 | n/a | [28.4%, 99.5%] | $+10.29 |
| Shenzhen|high | 7 | 6 | n/a | [42.1%, 99.6%] | $+22.71 |
| Singapore|high | 4 | 1 | n/a | [0.6%, 80.6%] | $-4.40 |
| Taipei|high | 2 | 0 | n/a | [0.0%, 84.2%] | $-30.11 |
| Tel Aviv|high | 4 | 3 | n/a | [19.4%, 99.4%] | $+13.75 |
| Tokyo|high | 5 | 5 | n/a | [47.8%, 100.0%] | $+22.38 |
| Tokyo|low | 5 | 3 | n/a | [14.7%, 94.7%] | $+3.21 |
| Toronto|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.98 |
| Warsaw|high | 6 | 3 | n/a | [11.8%, 88.2%] | $-14.39 |
| Wellington|high | 8 | 4 | n/a | [15.7%, 84.3%] | $-82.38 |
| Wuhan|high | 14 | 13 | n/a | [66.1%, 99.8%] | $+9.20 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

| city|metric | window | n | wins | 95% CI | reason |
|---|---|---|---|---|---|
| Paris|high | 30d | 19 | 0 | [0.0%, 17.6%] | rolling 30d win-rate CI upper bound 0.176 < 0.50 |

## Notes

- fee coverage gap: 55 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
