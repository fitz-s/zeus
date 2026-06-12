# Settlement Guard Report — 2026-06-10T09:15:00.067364+00:00

**GOAL:** stable AFTER-COST settlement win-rate > 51% on traded markets.

## GOAL line — rolling after-cost win-rate vs 51% bar

| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |
|---|---|---|---|---|---|---|
| 7d | 15 | 12 | n/a | [51.9%, 95.7%] | $-0.91 | — |
| 30d | 15 | 12 | n/a | [51.9%, 95.7%] | $-0.91 | — |

> Small-n honesty: n=15 < 20; win-rate shown as a CI, never a point claim.

## Overall (all settled traded fills)

- settled trades: **15** (wins 12, losses 3)
- win-rate: **n/a** (raw 80.0%), 95% CI [51.9%, 95.7%]
- after-cost PnL: **$-0.91** on $127.84 cost basis (ROI -0.7%)
- calibration: entry q not captured on filled rows (q_live NULL) — Brier/log-loss unavailable

## By direction

| direction | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| buy_no | 15 | 12 | n/a | [51.9%, 95.7%] | $-0.91 |

## By city + metric

| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |
|---|---|---|---|---|---|
| Helsinki|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Hong Kong|high | 2 | 2 | n/a | [15.8%, 100.0%] | $+2.67 |
| Istanbul|high | 2 | 1 | n/a | [1.3%, 98.7%] | $+1.13 |
| Karachi|high | 1 | 0 | n/a | [0.0%, 97.5%] | $-17.01 |
| London|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.05 |
| Milan|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+4.94 |
| San Francisco|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.35 |
| Seoul|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+2.59 |
| Tokyo|high | 1 | 1 | n/a | [2.5%, 100.0%] | $+1.55 |
| Tokyo|low | 1 | 1 | n/a | [2.5%, 100.0%] | $+0.27 |
| Warsaw|high | 3 | 2 | n/a | [9.4%, 99.2%] | $-3.00 |

## Regression sentinels — SUSPEND_CANDIDATE (report-only)

None. No city's rolling win-rate CI upper bound is below 50%.

## Notes

- fee coverage gap: 14 fill(s) had a NULL fee envelope; treated as 0.0 fees (after-cost PnL is an UPPER bound for those).
- strategy attribution unavailable in v1 (order_policy NULL on filled audit rows); all rows bucketed as strategy='unknown'. Direction breakdown is authoritative.
