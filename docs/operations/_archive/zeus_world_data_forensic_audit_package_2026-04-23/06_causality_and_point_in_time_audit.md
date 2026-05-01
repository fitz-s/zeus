# 06 Causality and Point-in-Time Audit

## Ruling

The uploaded DB does not support causal forecast replay or training. The tables that would prove point-in-time forecast availability are empty, and code paths exist that can reconstruct availability rather than preserving source-issued availability. That is unacceptable for a trading machine unless clearly segregated as non-canonical evidence.

## DB-confirmed absence

| Table | Rows |
|---|---:|
| `calibration_pairs` | 0 |
| `calibration_pairs_v2` | 0 |
| `decision_log` | 0 |
| `ensemble_snapshots` | 0 |
| `ensemble_snapshots_v2` | 0 |
| `forecasts` | 0 |
| `historical_forecasts` | 0 |
| `historical_forecasts_v2` | 0 |
| `market_events` | 0 |
| `market_events_v2` | 0 |
| `outcome_fact` | 0 |
| `platt_models` | 0 |
| `platt_models_v2` | 0 |
| `probability_trace_fact` | 0 |
| `replay_results` | 0 |
| `trade_decisions` | 0 |

## Code-confirmed risks

- Open-Meteo previous-runs forecast append logic can produce rows with `forecast_issue_time` missing.
- Historical forecast ETL can reconstruct `available_at` from model-delay assumptions; that may be useful evidence but is not authoritative point-in-time truth.
- v2 TIGGE extractors appear designed for high/low local-day forecasts, causality status, and training flags; however, the uploaded DB contains zero rows from those tables.
- Day0 live observations can fall back across source classes. That can be acceptable for monitoring, but not for settlement labels or training unless role-filtered.

## Point-in-time requirements not satisfied by current DB

- True forecast `issue_time` preserved.
- True `available_at` preserved from source or source manifest.
- `fetch_time` preserved and compared to decision time.
- Market order book/price history preserved around decisions.
- Market rules and tokens captured per market.
- Calibration snapshot versions tied to decision traces.

## Required containment

- Consumers must fail closed when forecast/ensemble/calibration tables are empty.
- Any historical forecast row with reconstructed availability must have `authority != VERIFIED` or `causality_status != OK` unless independently validated.
- Training queries must join only rows where `training_allowed=1`, `causality_status='OK'`, `authority='VERIFIED'`, and source-role is eligible.