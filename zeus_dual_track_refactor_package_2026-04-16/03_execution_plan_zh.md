# 03. 分阶段实施计划：每一步改什么、怎么改、何时停

## 总体顺序

```text
Phase 0  文档宪法
Phase 1  Metric spine / typed contracts
Phase 2  Source authority 合一
Phase 3  World DB v2
Phase 4  High track (mx2t6 local-calendar-day max)
Phase 5  Low track (mn2t6 local-calendar-day min)
Phase 6  GRIB -> snapshots ingest
Phase 7  Canonical rebuild / Platt split
Phase 8  Day0 low runtime
Phase 9  Shadow / parity / activation
```

---

## Phase 0 — 先把文档变成系统宪法

### 目的
在动代码之前，把 Zeus 的“交易机身份”写死，防止 refactor 过程中每个人脑中的系统不同。

### 必补文档

1. `AGENTS.md`
   - 把概率链从 `daily max` 改成 `daily extrema by metric`
   - 明确 high / low 共用几何、不共用训练身份

2. `docs/authority/zeus_current_architecture.md`
   - 增加 dual-track system charter
   - 增加 external source matrix
   - 增加 fallback non-trainable law

3. `docs/reference/zeus_domain_model.md`
   - 在 world data 中明确 `temperature_metric` 轴
   - 增加 forecast track / calibration family / runtime fallback family

4. `docs/operations/data_rebuild_plan.md`
   - 把 rebuild 明确拆成 high-v2 / low-v2 两段
   - 明确 low Day0 不属于纯 forecast rebuild 的第一阶段

### 验收
- 文档中不再把 Zeus 描述成单一 `daily high` 系统
- 文档中出现显式 `temperature_metric` / `physical_quantity` / `training_allowed`

---

## Phase 1 — 安装 metric spine

### 目的
让系统从“到处散落着 high/low 字符串”升级成“显式类型+显式契约”。

### 新文件

- `src/types/temperature_metric.py`
- `src/contracts/forecast_track.py`
- `src/data/observation_contracts.py`

### 改造文件

- `src/data/market_scanner.py`
- `src/engine/evaluator.py`
- `src/signal/ensemble_signal.py`
- `src/signal/day0_window.py`

### 关键动作

#### 1. 定义 `TemperatureMetric`
```python
class TemperatureMetric(StrEnum):
    HIGH = "high"
    LOW = "low"
```

#### 2. 定义 `ForecastTrack`
它至少包含：
- metric
- physical_quantity
- observation_field
- aggregation_contract
- data_version
- source_family
- geometry_version

#### 3. `market_scanner` 只负责**发现** metric，不负责最终授权
scanner 可以继续用文本推断作为入口，但产物必须是 typed metric，
并且后续 evaluator / loader / calibrator 都要显式核对。

### 验收
- 新代码不再以裸 `"high"` / `"low"` 字符串散布为主
- 所有核心入口函数签名里都能看到 `track` 或 `temperature_metric`

### 停止条件
- 如果 evaluator 还能在没有 metric alignment assert 的情况下继续跑，停止推进

---

## Phase 2 — Source authority 合一

### 目的
把运行时 observation source / station / unit 的主权收回到一个地方，防止 source drift。

### 现在的问题
`config/cities.json` 已经声明 settlement source 可能变，但 `daily_obs_append.py` 仍保留
硬编码 registry。这种双主权状态在 high/low 双轨里风险会翻倍。

### 行动

1. 新增 `src/data/city_source_registry.py`
   - 统一从 `config/cities.json` 加载
   - 暴露 runtime 可调用的 source resolver

2. 替换硬编码映射
   - `src/data/daily_obs_append.py`
   - 任何 backfill / audit / runtime fetch 中的 station map

3. 把 `manifest_hash` / `source_kind` 贯穿到 TIGGE extract 和 snapshot ingest

### 验收
- 运行时与离线 manifest 不再维护两套城市 source 事实
- 改一个城市 source，只需要改 `cities.json`

### 停止条件
- 只要还有 hardcoded 城市 source 表未收拢，就不要启动 full low run

---

## Phase 3 — 建 World DB v2

### 目的
在训练世界里建立不会串轨的 canonical substrate。

### 推荐策略
新建 v2 表，而不是在旧表上补丁式延长寿命：

- `ensemble_snapshots_v2`
- `calibration_pairs_v2`
- `platt_models_v2`
- `ensemble_coverage_v2`

### 同时补 observation/day0 substrate

#### 必加字段
- `observation_instants.running_min`
- `day0_residual_fact.running_min`
- `day0_residual_fact.residual_downside`
- `day0_residual_fact.has_downside`

### 为什么这一步必须早做
因为 low Day0 如果没有 `running_min` 和 downside substrate，
后面一定会有人偷懒复用 high 逻辑。

### 验收
- high / low 两类 snapshot 可以写入同一 DB，但永不共享主键空间
- low calibration pair 不可能误连到 high platt model

### 停止条件
- 如果 `platt_models` 仍没有 metric 维度，不准进入 rebuild 阶段

---

## Phase 4 — High track：把 max 改成 local-calendar-day max

### 目的
先把现有 high 系统从旧几何迁到正确几何，同时验证 v2 spine 不破坏现有策略。

### 关键动作

1. 实现/重写 high lane 脚本
   - manifest refresh
   - dynamic step policy
   - raw validation
   - local-calendar-day extraction
   - coverage scanner

2. 数据身份
```text
temperature_metric = high
physical_quantity = mx2t6_local_calendar_day_max
data_version = tigge_mx2t6_local_calendar_day_max_v1
```

3. 先做 high-v2 parity
   - 同期选样本城市
   - 比较 old high runtime vs high-v2 output shape
   - 验证策略入场/拒单没有异常漂移

### 建议原因
不要一上来就同时改 high + low + runtime。  
先把 high 做成 v2，可以验证 metric spine 和新 DB 不是纸上方案。

### 验收
- high-v2 可以独立跑通 `raw -> extract -> coverage -> ingest -> rebuild -> refit`
- high-v2 与旧 high 在方向性上可解释

### 停止条件
- 如果 high-v2 parity 过不了，不要启动 low full-history

---

## Phase 5 — Low track：单独建设 mn2t6 local-calendar-day min

### 目的
把 daily low 作为第二条物理生产线，而不是 high 的子分支。

### 关键动作

1. 单独 downloader / validator / extract / coverage
2. 单独 raw root / status / logs / ok markers
3. 边界泄漏 quarantine 规则
4. Day0 causality N/A 规则
5. 城市级 quarantine rate 报告

### 不可退让的规则

#### 1. boundary-ambiguous snapshot 不进训练
不是“尽量不用”，而是 V1 明确排除。

#### 2. `N/A_CAUSAL_DAY_ALREADY_STARTED` 不是 missing
coverage scanner 必须识别这个状态。

#### 3. 不允许和 high 共用 readiness scalar
每个城市都要分别报：
- high readiness
- low readiness
- low quarantine rate

### 验收
- `mn2t6` pilot 通过
- 有 per-city quarantine 报告
- 输出 JSON 不再带 `peak_window` 词汇

### 停止条件
- 某城市 quarantine rate 高得离谱但没有被显式审核，不准全量推进

---

## Phase 6 — 实现 GRIB -> snapshots ingest

### 目的
把 raw/extract 结果正式纳入 world DB v2。

### 改造重点

`scripts/ingest_grib_to_snapshots.py` 需要从 placeholder 变成真正 producer。

### ingest 必做检查

1. data_version 白名单
2. metric 与 physical_quantity 对齐
3. member_count = 51
4. high/low observation_field 正确
5. low track 的 `training_allowed` / `boundary_ambiguous` 入库
6. fallback rows 标成不可训练

### 推荐写法
做成 track-aware dispatcher：

```python
if track.temperature_metric is HIGH:
    ingest_high_snapshot(...)
elif track.temperature_metric is LOW:
    ingest_low_snapshot(...)
```

不要做成一堆 `if metric == "low"` 的支路污染老 high 逻辑。

### 验收
- v2 snapshots 写入成功
- `training_allowed=false` 的 low rows 不会进入 calibration rebuild 查询

---

## Phase 7 — Canonical rebuild / Platt split

### 目的
把训练世界真正拆成 high / low 两族。

### 改造文件

- `scripts/rebuild_calibration_pairs_canonical.py`
- `scripts/refit_platt.py`

### 重构方式

#### 1. rebuild 改为 track dispatcher
```python
for snapshot in snapshots_v2:
    if snapshot.temperature_metric == "high":
        process_high_snapshot(snapshot)
    else:
        process_low_snapshot(snapshot)
```

#### 2. observation field 分离
- high 读 `observations.high_temp`
- low 读 `observations.low_temp`

#### 3. bucket key 必须带 metric
例如：

```text
low:cluster=coastal:season=winter:bin=canonical:space=raw:data=tigge_mn2t6_local_calendar_day_min_v1
high:cluster=coastal:season=winter:bin=canonical:space=raw:data=tigge_mx2t6_local_calendar_day_max_v1
```

### 验收
- `calibration_pairs_v2` 中同一 `decision_group_id` 不跨 metric
- `platt_models_v2` 中 low 不可能覆盖 high

### 停止条件
- 任何 refit 结果如果 bucket key 不含 metric，停止上线

---

## Phase 8 — Day0 low runtime

### 目的
让 low Day0 从“接口上好像支持”变成真正能做出正确拒单/推断的运行时能力。

### 关键动作

1. 拆 Day0 类
   - `day0_high_signal.py`
   - `day0_low_signal.py`

2. `observation_client.py`
   - 增加 `low_so_far`
   - 契约里同时返回 `current_temp / high_so_far / low_so_far / obs_time`

3. `day0_low_signal.py`
   - 不能调用 `day0_blended_highs(...)`
   - 必须有 downside / hard-ceiling 语义
   - 必须识别 pre-sunrise / post-sunrise / remaining-hours 结构

4. `evaluator.py`
   - 若 low Day0 且 `low_so_far` 缺失 → 拒单
   - 若 low Day0 且 slot 为 causal N/A → 走 nowcast 或拒单
   - 绝不回退到 high Day0 路径

### 初版上线策略
先 shadow，不直接真钱。

### 验收
- low Day0 能给出自洽 p-vector 或明确 reject reason
- 没有任何路径会把 low 候选市场交给 high Day0 算法

### 停止条件
- 任何 low Day0 还能穿过 `day0_blended_highs(...)`，立即停止

---

## Phase 9 — Shadow / parity / activation

### 目的
确保 refactor 不是“写完了”，而是“被证明了”。

### 推荐 rollout

#### Stage A — high-v2 shadow
- 旧 high 与新 high-v2 并行
- 比较概率向量、拒单率、策略触发率

#### Stage B — high-v2 cutover
- 高温正式切换到 v2
- 旧 high 保留回滚开关

#### Stage C — low historical shadow
- 不开真钱
- 只跑 low track 数据、校准和信号

#### Stage D — low runtime shadow
- 对真实 low 市场做 full evaluation，但不下单

#### Stage E — low controlled activation
- 先白名单城市
- 再白名单策略
- 再全量开放

### 必须具备的 kill-switch

- metric mismatch
- missing low observation
- source registry drift
- quarantine rate 爆表
- fallback rows 意外进入训练
- high/low platt bucket 碰撞

## 总结

这套顺序的核心不是“工程上看起来整齐”，而是：

> 先让系统拥有不能串轨的骨架，再让数据进入骨架，再让 low 在 shadow 中证明自己。
