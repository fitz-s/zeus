# Unused Data

这份文件不再用“几乎所有数据都没用”这种旧叙事。

现在真正要区分的是三类：

1. **已经 load-bearing**
2. **已经导入，但还没有吃满**
3. **仍然主要停留在工作区或历史兼容层**

## No Longer Unused

下面这些数据过去可以算“未充分使用”，现在不能再这么写：

| Asset | Current status |
| --- | --- |
| `observation_instants` | 已是 Zeus 的正式时间语义主链 |
| `solar_daily` | 已是 Zeus 的正式时间语义主链 |
| hourly observations | 已通过 `observation_instants` 进入 `diurnal_curves` / `diurnal_peak_prob` |
| `settlements` | 已是 calibration / replay / truth 主链 |
| daily `observations` | 已用于 persistence / truth 相关逻辑 |
| `forecast_skill` | 已基本满载导入 |
| `token_price_log` | 已被 monitor velocity、PnL hindsight、部分 replay 审计消费 |

## Still Partially Unused

### 1. Historical forecast coverage

`historical_forecasts = 28,006 / 171,003`

这不是“完全没用”，但仍然明显没吃满。

剩余问题：

- 历史 forecast 覆盖不够厚
- 对 alpha / model skill 的长期统计还有提升空间

### 2. Historical replay overlap

历史 replay 现在的真正 unused 部分不是“没有 snapshot”，而是“有 snapshot 但不兼容”。

| Layer | Count |
| --- | ---: |
| settlement total | 1,385 |
| snapshot overlap | 254 |
| parseable bins | 254 |
| vector-compatible | 26 |

也就是说，历史 replay 未充分利用的核心不是 raw volume，而是：

- 老 snapshot 的 `p_raw_json` shape
- 老 label source 的 bin normalization
- 老 decision-time reference 的缺失

### 3. Historical token-price path as replay prior

虽然 `token_price_log` 已经被一部分功能用上，但它还没有完全转化成历史 replay 的强 market prior。

当前状态：

- runtime monitor 已使用
- hindsight audit 已使用
- replay 已不再走 uniform prior 旧逻辑

仍然未完成：

- 历史 token-price path 没有被充分提升为完整 replay prior reconstruction

## Workspace Assets Still Mostly Outside Zeus

### 51 source data raw assets

这些大多仍然不应被直接视为“Zeus 未用数据”，而应视为“工作区原材料”：

- 原始 GRIB
- 区域批量下载文件
- 逐城成员向量 JSON
- manifest / docs / tmp

它们当前仍大多停留在工作区，而不是 Zeus-owned schema。

这不是自动等于问题。只有当某个原始资产已经明确能改善 Zeus 主链，且缺少对应 ETL / schema / runtime consumer 时，它才算真正的 unused gap。

## The Real Unused Surface Right Now

当前真正值得继续追的 unused surface 只有这几个：

1. `historical_forecasts` 剩余 84% 左右未导入
2. 历史 replay 的 snapshot overlap 中，绝大多数仍不 vector-compatible
3. token price history 还没有彻底变成强历史 replay prior

## What Is Not The Main Problem Anymore

以下不应继续用“unused data”来描述：

- DST / sunrise / sunset
- hourly observation timing
- Day0 时间语义输入
- semantic snapshot spine

这些现在都已经进主链了。

## Bottom Line

现在 Zeus 的 unused data 问题，已经从“根本没吃数据”转成：

- **历史 replay compatibility**
- **历史 forecast coverage**
- **工作区原材料到 Zeus-owned schema 的选择性提升**

这和旧文档里的 `4.1%` 时代已经不是同一个问题。
