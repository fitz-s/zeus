# Settlement Guard Report — 2026-06-20T09:15:00.121372+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 36 | 23 | 63.9% | [46.2%, 79.2%] | $-10.86 | no |
| 30d | 36 | 23 | 63.9% | [46.2%, 79.2%] | $-10.86 | no |

## Overall (all settled traded fills)

- settled trades: **36** (wins 23, losses 13)
- win-rate: **63.9%** (raw 63.9%), 95% CI [46.2%, 79.2%]
- after-cost PnL: **$-10.86** on $240.30 cost basis (ROI -4.5%)
- calibration: entry q not captured on filled rows (q_live NULL) — Brier/log-loss unavailable

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 33 | 22 | 66.7% | [48.2%, 82.0%] | $-15.92 |
| buy_yes | 3 | 1 | n/a | [0.8%, 90.6%] | $+5.06 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Chengdu|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-13.45 |
| Denver|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+6.82 |
| Helsinki|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Hong Kong|high | 4 | 3 | n/a | [19.4%, 99.4%] | $+1.62 |
| Houston|high | 2 | 0 | n/a | [0.0%, 84.2%] | $-6.49 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Karachi|high | 2 | 0 | n/a | [0.0%, 84.2%] | $-25.26 |
| Kuala Lumpur|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-6.03 |
| London|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.05 |
| Lucknow|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.83 |
| Madrid|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+3.60 |
| Manila|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.95 |
| Milan|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+3.88 |
| Munich|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-3.10 |
| Paris|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.92 |
| San Francisco|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.35 |
| Seoul|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.59 |
| Seoul|low | 1 | 0 | n/a | [0.0%, 97.5%] | $-7.28 |
| Tokyo|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+3.25 |
| Tokyo|low | 2 | 2 | n/a | [15.8%, 100.0%] | $+8.46 |
| Warsaw|high | 4 | 3 | n/a | [19.4%, 99.4%] | $-1.30 |
| Wellington|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+0.60 |
| Wuhan|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.45 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

None. No city's rolling win-rate CI upper bound is below 50%.

## Notes

- fee coverage gap: 33 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
