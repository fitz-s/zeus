# Settlement Guard Report — 2026-07-16T09:15:00.151121+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 44 | 19 | 43.2% | [28.3%, 59.0%] | $-74.81 | no |
| 30d | 112 | 52 | 46.4% | [37.0%, 56.1%] | $-163.21 | no |

## Overall (all settled traded fills)

- settled trades: **141** (wins 73, losses 68)
- win-rate: **51.8%** (raw 51.8%), 95% CI [43.2%, 60.3%]
- after-cost PnL: **$-154.49** on $1132.30 cost basis (ROI -13.6%)
- calibration: mean entry q=0.825 vs realized win-rate=51.8%; Brier=0.3896, log-loss=1.8150 (n_with_q=82)

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 127 | 71 | 55.9% | [46.8%, 64.7%] | $-193.83 |
| buy_yes | 14 | 2 | n/a | [1.8%, 42.8%] | $+39.33 |

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
| London|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.81 |
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
| Seoul|high | 5 | 3 | n/a | [14.7%, 94.7%] | $+10.09 |
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
| Wuhan|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-5.17 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

| city|metric | window | n | wins | 95% CI | reason |
|---|---|---|---|---|---|
| Paris|high | 30d | 18 | 0 | [0.0%, 18.5%] | rolling 30d win-rate CI upper bound 0.185 < 0.50 |

## Notes

- fee coverage gap: 55 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
