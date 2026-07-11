# Settlement Guard Report — 2026-07-10T09:15:00.150004+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 0 | 0 | n/a | [0.0%, 100.0%] | $+0.00 | — |
| 30d | 51 | 23 | 45.1% | [31.1%, 59.7%] | $-47.06 | no |

## Overall (all settled traded fills)

- settled trades: **73** (wins 41, losses 32)
- win-rate: **56.2%** (raw 56.2%), 95% CI [44.1%, 67.8%]
- after-cost PnL: **$-36.97** on $488.78 cost basis (ROI -7.6%)
- calibration: mean entry q=0.605 vs realized win-rate=56.2%; Brier=0.2157, log-loss=0.6081 (n_with_q=15)

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 63 | 40 | 63.5% | [50.4%, 75.3%] | $-35.71 |
| buy_yes | 10 | 1 | n/a | [0.3%, 44.5%] | $-1.26 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Buenos Aires|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+12.18 |
| Chengdu|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-7.29 |
| Chicago|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+8.44 |
| Chongqing|high | 3 | 1 | n/a | [0.8%, 90.6%] | $-23.07 |
| Denver|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+6.82 |
| Helsinki|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Hong Kong|high | 5 | 3 | n/a | [14.7%, 94.7%] | $+1.59 |
| Houston|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-25.93 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Karachi|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-33.18 |
| Kuala Lumpur|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-6.03 |
| London|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.05 |
| London|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.81 |
| Lucknow|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+2.81 |
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
| Tokyo|low | 4 | 2 | n/a | [6.8%, 93.2%] | $+0.79 |
| Warsaw|high | 5 | 3 | n/a | [14.7%, 94.7%] | $-8.29 |
| Wellington|high | 4 | 2 | n/a | [6.8%, 93.2%] | $+1.73 |
| Wuhan|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-6.59 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

None. No city's rolling win-rate CI upper bound is below 50%.

## Notes

- fee coverage gap: 54 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
