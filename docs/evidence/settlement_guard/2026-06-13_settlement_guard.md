# Settlement Guard Report — 2026-06-13T09:15:00.087836+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 23 | 18 | 78.3% | [56.3%, 92.5%] | $+9.03 | YES |
| 30d | 23 | 18 | 78.3% | [56.3%, 92.5%] | $+9.03 | YES |

## Overall (all settled traded fills)

- settled trades: **23** (wins 18, losses 5)
- win-rate: **78.3%** (raw 78.3%), 95% CI [56.3%, 92.5%]
- after-cost PnL: **$+9.03** on $150.90 cost basis (ROI 6.0%)
- calibration: entry q not captured on filled rows (q_live NULL) — Brier/log-loss unavailable

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 21 | 18 | 85.7% | [63.7%, 97.0%] | $+11.89 |
| buy_yes | 2 | 0 | n/a | [0.0%, 84.2%] | $-2.86 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Helsinki|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Hong Kong|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+2.67 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Karachi|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-17.01 |
| London|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.05 |
| Madrid|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+3.60 |
| Manila|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.95 |
| Milan|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+3.88 |
| San Francisco|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.35 |
| Seoul|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.59 |
| Tokyo|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+3.25 |
| Tokyo|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+0.27 |
| Warsaw|high | 4 | 3 | n/a | [19.4%, 99.4%] | $-1.30 |
| Wellington|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+0.60 |
| Wuhan|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.45 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

None. No city's rolling win-rate CI upper bound is below 50%.

## Notes

- fee coverage gap: 22 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
