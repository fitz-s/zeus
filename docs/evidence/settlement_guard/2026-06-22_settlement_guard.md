# Settlement Guard Report — 2026-06-22T09:15:00.150369+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 53 | 35 | 66.0% | [51.7%, 78.5%] | $-0.91 | YES |
| 30d | 53 | 35 | 66.0% | [51.7%, 78.5%] | $-0.91 | YES |

## Overall (all settled traded fills)

- settled trades: **53** (wins 35, losses 18)
- win-rate: **66.0%** (raw 66.0%), 95% CI [51.7%, 78.5%]
- after-cost PnL: **$-0.91** on $362.17 cost basis (ROI -0.3%)
- calibration: entry q not captured on filled rows (q_live NULL) — Brier/log-loss unavailable

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 50 | 34 | 68.0% | [53.3%, 80.5%] | $-5.97 |
| buy_yes | 3 | 1 | n/a | [0.8%, 90.6%] | $+5.06 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Chengdu|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-7.29 |
| Denver|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+6.82 |
| Helsinki|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Hong Kong|high | 4 | 3 | n/a | [19.4%, 99.4%] | $+1.62 |
| Houston|high | 2 | 0 | n/a | [0.0%, 84.2%] | $-6.49 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Karachi|high | 3 | 0 | n/a | [0.0%, 70.8%] | $-33.18 |
| Kuala Lumpur|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-6.03 |
| London|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.05 |
| London|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.81 |
| Lucknow|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.83 |
| Madrid|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+3.60 |
| Manila|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.95 |
| Milan|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+3.88 |
| Munich|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-3.10 |
| Paris|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.92 |
| Paris|low | 2 | 2 | n/a | [15.8%, 100.0%] | $+6.66 |
| Qingdao|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.32 |
| San Francisco|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.35 |
| Seattle|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.54 |
| Seoul|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.59 |
| Seoul|low | 2 | 0 | n/a | [0.0%, 84.2%] | $-13.49 |
| Shanghai|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-2.97 |
| Shenzhen|high | 4 | 4 | n/a | [39.8%, 100.0%] | $+15.60 |
| Singapore|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-0.99 |
| Tokyo|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+3.25 |
| Tokyo|low | 2 | 2 | n/a | [15.8%, 100.0%] | $+8.46 |
| Warsaw|high | 4 | 3 | n/a | [19.4%, 99.4%] | $-1.30 |
| Wellington|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+0.60 |
| Wuhan|high | 2 | 1 | n/a | [1.3%, 98.7%] | $-6.59 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

None. No city's rolling win-rate CI upper bound is below 50%.

## Notes

- fee coverage gap: 50 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
