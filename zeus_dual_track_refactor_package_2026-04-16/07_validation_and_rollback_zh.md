# 07. 验证、回滚、上线清单

## 你应该要求系统证明什么

大型 refactor 成功，不是因为“代码写完了”，而是因为下面三件事被证明了：

1. high 没被破坏
2. low 没被污染
3. 运行时不会在 source drift / causal N/A / fallback 条件下偷偷做错事

---

## A. 单元测试清单

### 类型与契约
- `test_temperature_metric_alignment.py`
- `test_bucket_key_includes_metric.py`
- `test_snapshot_training_gate_respects_authority.py`

### 城市 source 主权
- `test_runtime_source_registry_matches_cities_json.py`
- `test_manifest_hash_embedded_in_extract_outputs.py`

### 时间几何
- `test_lead_day_anchor_uses_issue_utc_date.py`
- `test_dynamic_step_can_exceed_180.py`
- `test_local_calendar_day_geometry_dst_transition.py`

### high/low 隔离
- `test_high_snapshot_never_joins_low_platt.py`
- `test_low_snapshot_never_joins_high_platt.py`
- `test_rebuild_queries_observation_field_by_metric.py`

### low boundary quarantine
- `test_mn2t6_boundary_ambiguous_snapshot_rejected.py`
- `test_low_quarantine_rate_reported_by_city.py`

### Day0
- `test_observation_client_returns_low_so_far.py`
- `test_day0_low_never_calls_high_blender.py`
- `test_day0_low_causal_na_rejects_or_nowcasts.py`

### fallback
- `test_openmeteo_fallback_not_trainable.py`
- `test_runtime_nowcast_rows_never_enter_rebuild.py`

---

## B. 集成测试清单

### high-v2 parity
输入同一城市/日期/市场：

- old high path
- new high-v2 path

比较：
- p-vector 形状
- 拒单原因分布
- strategy 触发分布
- position sizing 漂移

### low historical shadow
对 low markets：
- 只做 signal / calibration lookup / no-trade shadow
- 不下单
- 检查 coverage 与 quarantine

### mixed-day mixed-metric test
同一天同时跑：
- 同一城市 high 市场
- 同一城市 low 市场

证明：
- 加载的是不同 track
- 用的是不同 model family
- observation field 没串轨

---

## C. rollout gates

### Gate 1 — 文档 gate
- dual-track charter 已落文档
- data rebuild plan 已更新
- source authority matrix 已更新

### Gate 2 — schema gate
- v2 tables 已可写
- old 表不会被误写
- metric alignment asserts 已上线

### Gate 3 — high-v2 gate
- high-v2 ingest / rebuild / refit / parity 通过

### Gate 4 — low archive gate
- low pilot 通过
- quarantine report 可解释
- readiness 不是单一标量

### Gate 5 — low runtime shadow gate
- low Day0 有独立 reject / nowcast
- 无 high fallback contamination
- shadow 行为稳定

### Gate 6 — money gate
- 先白名单城市
- 再白名单策略
- 再全量开放

---

## D. kill-switch 列表

满足任一条，立即 fail-closed：

1. snapshot metric != candidate metric
2. snapshot data_version != platt model data_version
3. low market 读取到了 high observation field
4. low Day0 缺 `low_so_far`
5. `N/A_CAUSAL_DAY_ALREADY_STARTED` 被当成 missing 或被送入 pure-forecast Platt
6. fallback row 被标记为 `training_allowed=true`
7. source registry 与 `cities.json` 漂移
8. low quarantine rate 爆表且未人工审核
9. any bucket key collision across high and low
10. evaluator 仍能把 low candidate 送进高温 Day0 算法

---

## E. 回滚策略

### 回滚原则
不要只做“代码回滚”，要做**读路径回滚**。

### 推荐设计
- world-data 使用 v2 表
- 旧读路径保留一段时间
- 切换用 feature flag / reader switch 控制

### 回滚级别

#### Level 1
只回滚 low runtime，保留 high-v2

#### Level 2
回滚 high-v2 reader 到 old high path

#### Level 3
停用 v2 读路径，只保留写入审计

### 回滚前提
每次 cutover 前必须保存：
- row counts by metric
- active bucket keys
- model counts
- coverage report
- top reject reasons

---

## F. 最终上线判断

我建议你只在下面这句话为真时，才允许把 low 正式接入真钱：

> 我可以证明 low 的观测、时间几何、snapshot、calibration pair、Platt model、
> Day0 runtime 和 high 完全隔离；并且我可以在任何时刻回退 low，而不影响 high。
