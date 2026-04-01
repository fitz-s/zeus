# Zeus Data Strategy

## Current Reality

旧的 `4.1%` 口径已经失效。Zeus 现在不是“几乎没吃数据”，而是：

- **时间语义主链已接上**
- **truth / daily obs / hourly obs / solar 已大规模进入 zeus.db**
- **真正的剩余问题已经从“没导入”转成“哪些数据已经 load-bearing，哪些还只是部分使用”**

## Comparable Import Coverage

下面只统计可以直接和 Rainstorm 源表做一一对比的核心数据，不把原始 GRIB / manifest / tmp 这种工作区文件混进来。

| Asset | Rainstorm / Source | Zeus | Coverage |
| --- | ---: | ---: | ---: |
| `settlements` | 1,652 | 1,399 | 84.7% |
| daily `observations` | 20,715 | 20,715 | 100.0% |
| `observation_instants` | 219,483 | 219,483 | 100.0% |
| `solar_times` / `solar_daily` | 31,198 | 31,198 | 100.0% |
| `token_price_log` | 510,439 | 340,351 | 66.7% |
| `forecasts` -> `historical_forecasts` | 171,003 | 28,006 | 16.4% |
| `settlement_forecast_ladder_backfill` -> `forecast_skill` | 53,600 | 53,581 | 100.0% |
| **Comparable core total** | **1,008,090** | **694,733** | **68.9%** |

这比旧文档里的 `4.1%` 高得多，也更接近现在的真实状态。

## What Is Actually Load-Bearing Now

### Runtime-critical and already in use

| Asset | Current role |
| --- | --- |
| `observation_instants` | Day0 / DST / local timestamp truth |
| `solar_daily` | sunrise / sunset / daylight phase |
| `diurnal_curves` | seasonal intraday temperature shape |
| `diurnal_peak_prob` | monthly post-peak confidence |
| `settlements` | calibration / replay / truth outcome |
| daily `observations` | persistence / truth-side stats |
| `token_price_log` | monitor market velocity, hindsight audit, partial replay market priors |
| `ensemble_snapshots` | decision-time forecast snapshot spine |
| `calibration_pairs` / `platt_models` | calibration chain |
| `forecast_skill` | skill / validation / alpha-related analysis |

### Partially used

| Asset | Current use | Remaining gap |
| --- | --- | --- |
| `token_price_log` | real-time monitor velocity, hindsight audit, partial replay support | 历史 replay 还没完全把它变成强 market prior surface |
| `historical_forecasts` | model-skill / ETL groundwork | 只导入了 28,006 / 171,003，覆盖仍偏薄 |
| `ensemble_snapshots` | runtime + replay spine | 历史向量兼容性仍弱 |
| `market_events` | runtime market structure | 历史 replay 的 label normalization 仍不够强 |

## Replay Coverage: The Real Bottleneck

当前 Zeus 的历史 replay 问题已经不是“逻辑太粗”，而是“历史数据兼容性”。

| Replay layer | Coverage |
| --- | ---: |
| strict decision-time replay | 15 / 1,385 = 1.1% |
| snapshot-only overlap | 254 / 1,385 = 18.3% |
| vector-compatible historical overlap | 26 / 1,385 = 1.9% |

当前结论：

- replay **不再**用 uniform prior
- replay **不再**用 flat threshold
- replay 已经走 `MarketAnalysis + FDR + Kelly`
- 剩余历史 coverage 问题主要是 **旧 snapshot 的向量 shape 和 bin structure 不兼容**

## 51 Source Data: What It Is and What It Is Not

`51 source data` 主要是工作区，不是运行库。它的价值在于：

- TIGGE 历史 ENS 原始 / 中间产物
- ECMWF Open Data 原始 / 中间产物
- manifest / docs / tmp

它不是应该被“整包导入 zeus.db”的东西。正确做法是：

1. 把需要长期、稳定、语义明确的结果 ETL 进 `zeus.db`
2. 保持原始工作区继续作为采集和再处理层

Zeus 现在已经对时间语义这么做了：

- `solar_times` -> `solar_daily`
- `observation_instants` -> `observation_instants`
- hourly stats -> `diurnal_curves` / `diurnal_peak_prob`

## Strategic Priority Now

### P0
- 提升历史 replay 的 vector compatibility，而不是继续改 replay 主逻辑
- 继续让新的 `decision_log` / `shadow_signals` 样本稳定进入库

### P1
- 扩大 `historical_forecasts` 覆盖率
- 让 token price history 在历史 replay 中发挥更强作用

### P2
- 再考虑更大规模地吃 `51 source data` 的原始资产
- 只在它们能形成 Zeus-owned schema + ETL + runtime consumer 时推进

## Bottom Line

Zeus 现在的数据状态不是“严重缺数据”，而是：

- **主运行时数据已经足够厚**
- **主时间语义数据已经正式进入主链**
- **当前最大数据问题是历史 replay compatibility，不是原始数据匮乏**
