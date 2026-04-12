# Repo + DB 深度审计

这份审计不是从 spec 想象 Zeus，而是从**当前 repo 结构 + 你上传的 DB 快照**反推当前真实状态。

## 1. 我确认到已经真实落地的 foundation

从 repo 结构和 DB 两边同时看，foundation 已经不是“待实现”，而是已经进入运行表面：

- migration 已经存在 `migrations/2026_04_02_architecture_kernel.sql`
- `src/state/ledger.py`, `src/state/lifecycle_manager.py`, `src/engine/cycle_runner.py`, `src/riskguard/policy.py` 都已经进入主运行路径
- DB 中已经存在并有数据的 canonical / fact surfaces：
  - `position_events`: 72
  - `position_current`: 24
  - `opportunity_fact`: 43
  - `execution_fact`: 12
  - `outcome_fact`: 19
  - `availability_fact`: 1
  - `risk_actions`: 1

这说明你最近做的 huge merge 并不是“文档合并”，而是**kernel 级落地**。

## 2. 当前真实数据版图

当前 DB 快照（`zeus-shared.db`）核心表规模：

| 表 | 行数 |
|---|---:|
| token_price_log | 4,091,736 |
| observation_instants | 1,107,567 |
| hourly_observations | 993,425 |
| forecasts | 658,945 |
| market_price_history | 340,351 |
| forecast_skill | 53,581 |
| settlements | 34,198 |
| observations | 30,171 |
| calibration_pairs | 22,781 |
| diurnal_peak_prob | 13,248 |
| ensemble_snapshots | 8,884 |
| market_events | 7,926 |
| diurnal_curves | 4,416 |
| temp_persistence | 915 |
| platt_models | 36 |

## 3. 当前数学链条已经不浅

当前 repo 里，Zeus 已经实现了以下链条：

1. **ENS member -> Monte Carlo settlement distribution**
2. **Extended Platt**：`sigmoid(A * logit(p_raw) + B * lead_days + C)`
3. **model / market posterior fusion**
4. **bootstrap CI + p-value**
5. **FDR filter**
6. **dynamic Kelly sizing**
7. **Day0 hard floor + seam**

结论：Zeus 现在不是缺数学 ideas，而是缺**更强的数据/统计 substrate**。

## 4. 我在 DB 里看到的几个关键统计问题

### 4.1 calibration 的独立样本量被高估

`calibration_pairs` 当前是 bin-level pair rows，而不是 decision-level sample units。

按城市汇总：

| 城市 | pair_rows | decision_groups | rows/group |
|---|---:|---:|---:|
| Miami | 5794 | 534 | 10.85 |
| Munich | 5786 | 526 | 11.00 |
| Paris | 5772 | 532 | 10.85 |
| NYC | 5171 | 481 | 10.75 |
| London | 55 | 15 | 3.67 |
| Atlanta | 54 | 14 | 3.86 |
| Dallas | 54 | 14 | 3.86 |
| Chicago | 52 | 12 | 4.33 |
| Seattle | 43 | 13 | 3.31 |

这意味着：

- 很多 bucket 的 `n_samples` / maturity gate 更接近“pair 行数”，不是“独立预测事件数”
- bootstrap / regularization / fallback 判断如果继续按 pair rows 走，会系统性过于乐观

### 4.2 当前 probability truth surface 还是裂开的

DB 里能看到一个很重要的 seam：

- `opportunity_fact.p_raw`：0 / 43 非空
- `opportunity_fact.p_cal`：35 / 43 非空
- `trade_decisions.p_calibrated`：0 / 145 非空
- `shadow_signals`：却完整保留了 `p_raw_json / p_cal_json`

这说明概率链条虽然在运行时存在，但**没有被统一沉淀到同一个 canonical fact surface**。

### 4.3 active Platt inventory 已经领先于本地 calibration_pairs inventory

当前 active `platt_models = 36`，而 `calibration_pairs` 只覆盖 9 个城市、21 个 cluster×season bucket。
并且存在：

- active model bucket 在当前 calibration_pairs 里没有对应 pair rows
- 当前 calibration_pairs 里有 bucket，但 active model inventory 没有对应 active bucket

这不一定意味着逻辑错误，但说明你已经进入一个阶段：
**model inventory / training source / current DB snapshot / reporting surface 的 provenance 需要被显式记录。**

### 4.4 bias correction 和 forecast-skill coverage 还不对齐

- `model_bias`：38 个城市 × 4 季 × 5 source = 760 rows
- `forecast_skill`：只有 10 个城市，53,581 rows

这说明 bias surface 已经比 skill evaluation surface 更广，但 evaluation cube 还没完全铺开。

### 4.5 Day0 的学习价值被低估了

从 `observation_instants + settlements` 直接构造出来的样本里：

- 可用样本：219,483
- `residual_upside = settlement_value - running_max`
- 到当地 23 点，`P(residual_upside > 0)` 仍然约 0.70
- 到当地 12 点，这个概率仍然约 0.92

这告诉我们两件事：

1. Day0 “高点已经定了”的判断不能太激进
2. 现在的 day0 seam 非常值得被**数据化学习**，而不该继续只靠 heuristic

## 5. 我对当前阶段的判断

Zeus 已经跨过“项目基础没打稳”的阶段，进入“如何让已存在的知识结构持续进化”的阶段。

因此，下一阶段不应该再以“加某个 feature”作为中心，而应以这三个面作为中心：

- **Data truth**：到底发生了什么，数据是否 point-in-time、是否同一事实面
- **Statistical truth**：模型是否真的 OOS 好，而不是表面上样本很多
- **Trading truth**：选择、风控、执行是否和统计结论一致
