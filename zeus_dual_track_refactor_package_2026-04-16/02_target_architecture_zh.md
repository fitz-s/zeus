# 02. 目标架构：共享时间几何，隔离物理轨道

## 北极星目标

把 Zeus 定义成：

> 一个 live-only、position-managed、dual-track weather trading runtime。  
> 它共享统一的 local-calendar-day 时间几何、market/bin 语义、风险与执行框架，  
> 但对 `daily high` 与 `daily low` 使用完全隔离的物理量、训练数据、Day0 模型、
> 校准族、readiness 判定和上线门控。

## 必须共享的部分

这些组件可以共用：

- 城市与 settlement source 注册表
- local-calendar-day 时间几何
- market / bin 拓扑解析
- 执行层、风险层、portfolio / chain / chronicler 拓扑
- 策略框架（settlement_capture / shoulder_sell / center_buy / opening_inertia）
- 监控、coverage、authority、rollout 机制

## 必须硬隔离的部分

这些组件**绝不能**共用同一训练身份：

- forecast physical quantity
- extracted `data_version`
- observation field
- snapshot row family
- calibration pair family
- Platt model family
- Day0 causal model
- readiness scalar / coverage scalar

也就是说：

```text
shared_geometry != shared_training_identity
```

## 统一的 metric spine

建议引入下面这组一级字段，并把它们贯穿所有 world-data 层：

- `temperature_metric`: `high | low`
- `physical_quantity`
- `aggregation_contract`
- `observation_field`
- `data_version`
- `geometry_version`
- `source_family`
- `causality_status`
- `training_allowed`
- `lead_day_anchor`
- `manifest_hash`

## 推荐的 track 定义

### Track H: daily high

```text
temperature_metric = high
physical_quantity = mx2t6_local_calendar_day_max
aggregation_contract = local_calendar_day
observation_field = high_temp
data_version = tigge_mx2t6_local_calendar_day_max_v1
source_family = tigge_ecmwf_ens
```

### Track L: daily low

```text
temperature_metric = low
physical_quantity = mn2t6_local_calendar_day_min
aggregation_contract = local_calendar_day
observation_field = low_temp
data_version = tigge_mn2t6_local_calendar_day_min_v1
source_family = tigge_ecmwf_ens
```

### Track L-Day0-Nowcast（独立于纯历史 forecast calibration）

```text
temperature_metric = low
physical_quantity = low_day0_nowcast_remaining_window
aggregation_contract = partial_local_day_nowcast
observation_field = low_temp
data_version = low_day0_nowcast_v1
source_family = runtime_nowcast
training_allowed = false  # 初期只运行时 shadow，不进入 Platt
```

## 三条硬法律

### 法律 1：任何 join 都必须带上 metric spine

候选市场、snapshot、observation、calibration pair、platt model 五者之间的 join，
必须至少同时校验：

- `temperature_metric`
- `observation_field`
- `data_version`
- `physical_quantity`

任意一项不一致，直接 fail-closed。

### 法律 2：fallback 不可训练

Open-Meteo、runtime nowcast、任何临时补洞数据：

- 可以用于 runtime degrade path
- 但默认 `training_allowed = false`
- 不得进入 canonical calibration rebuild
- 不得和 TIGGE 正式历史样本混训

### 法律 3：Day0 low 因果无效不是缺数，而是合法状态

对正时区城市的 Day0 low：

- `N/A_CAUSAL_DAY_ALREADY_STARTED` 是有效语义，不是 missing
- 这类 slot 不允许查历史 pure-forecast Platt
- 只能走 low Day0 nowcast，或直接 no-trade

## 为什么我建议 world-data 用 v2 表，而不是直接在旧表上“补几个字段”

因为这不是普通 schema 增量，而是 row contract 发生了语义跃迁：

- 从 single-metric high-only → dual-metric isolated
- 从 informal max semantics → explicit physical quantity contract
- 从 loose data_version usage → track-typed provenance

推荐做法：

- trade-facing 决策/持仓表继续沿用现有表
- world-data 中的 `ensemble_snapshots / calibration_pairs / platt_models`
  使用 v2 表
- 读路径切到 v2 后，再考虑清退旧表

这样做的好处是：

1. 不会让旧 high-only 行和新 dual-track 行混在一起
2. 可以安全做 high-v2 parity
3. low 在 shadow 阶段不会污染现网读路径
4. rollback 更简单

## 推荐的模块切分

### 1) 类型层

新增：

- `src/types/temperature_metric.py`
- `src/contracts/forecast_track.py`

### 2) Day0 层

拆分：

- `src/signal/day0_high_signal.py`
- `src/signal/day0_low_signal.py`

保留：

- `src/signal/day0_window.py` 作为 shared geometry helper

### 3) 数据层

新增：

- `src/data/city_source_registry.py`
- `src/data/observation_contracts.py`

重构：

- `src/data/observation_client.py`
- `src/data/market_scanner.py`
- `src/data/ensemble_client.py`

### 4) 校准层

新增/重构：

- `scripts/ingest_grib_to_snapshots.py`
- `scripts/rebuild_calibration_pairs_canonical.py`
- `scripts/refit_platt.py`

## 一个简单但关键的设计选择

**不要**在每个函数里到处传裸字符串 `"high" / "low"`。  
一定要把它提升成 typed object，并让 `ForecastTrack` 成为系统里“概率语义”的载体。

推荐心智模型：

```python
track = ForecastTrack(
    temperature_metric=TemperatureMetric.LOW,
    physical_quantity="mn2t6_local_calendar_day_min",
    observation_field="low_temp",
    aggregation_contract="local_calendar_day",
    data_version="tigge_mn2t6_local_calendar_day_min_v1",
    source_family="tigge_ecmwf_ens",
)
```

后面的所有函数都围绕 `track` 做显式检查，而不是依赖隐含约定。

## 目标态判断标准

只有当下面五件事同时成立，才算 dual-track 架构真正成型：

1. high / low snapshot 写入不同 track identity
2. canonical rebuild 能分别生成 high / low calibration pairs
3. refit 后 platt buckets 不能跨 metric
4. Day0 low 有独立 nowcast/拒单逻辑
5. runtime 能在同一天同时面对 high 与 low 市场而不发生数据/模型串轨
