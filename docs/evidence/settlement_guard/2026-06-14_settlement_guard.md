# Settlement Guard Report — 2026-06-14T09:15:00.057906+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 28 | 21 | 75.0% | [55.1%, 89.3%] | $+12.32 | YES |
| 30d | 28 | 21 | 75.0% | [55.1%, 89.3%] | $+12.32 | YES |

## Overall (all settled traded fills)

- settled trades: **28** (wins 21, losses 7)
- win-rate: **75.0%** (raw 75.0%), 95% CI [55.1%, 89.3%]
- after-cost PnL: **$+12.32** on $187.81 cost basis (ROI 6.6%)
- calibration: entry q not captured on filled rows (q_live NULL) — Brier/log-loss unavailable

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 25 | 20 | 80.0% | [59.3%, 93.2%] | $+7.26 |
| buy_yes | 3 | 1 | n/a | [0.8%, 90.6%] | $+5.06 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Denver|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+6.82 |
| Helsinki|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Hong Kong|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+2.67 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Karachi|high | 2 | 0 | n/a | [0.0%, 84.2%] | $-25.26 |
| Kuala Lumpur|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-6.03 |
| London|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.05 |
| Lucknow|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.83 |
| Madrid|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+3.60 |
| Manila|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.95 |
| Milan|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+3.88 |
| Paris|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+7.92 |
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

- fee coverage gap: 27 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
