# 06. TIGGE 双轨实施说明：怎样把数据链和 Zeus 真正接起来

## 你上传的三份计划文件，抽象成 Zeus 可执行版本后，核心就是一句话

```text
two independent physical quantities
+ one shared local-calendar-day geometry
+ separate track identity all the way into DB/calibration/runtime
```

## 0. 共享什么，隔离什么

### 共享
- city manifest 生成逻辑
- local-day window 计算
- step horizon 计算
- coverage key 结构
- metadata / provenance 格式
- 验证框架

### 隔离
- raw roots
- tmux session names
- status files
- GRIB validators
- extract outputs
- data_version
- calibration families
- readiness reports

---

## 1. 高温 lane（先做）

### Raw lane
```text
raw/tigge_ecmwf_ens_regions_mx2t6/
```

### Extract lane
```text
raw/tigge_ecmwf_ens_mx2t6_localday_max/
```

### 关键升级
不要再围绕 `peak_window` 建训练产品。  
训练产品必须是：

```text
tigge_mx2t6_local_calendar_day_max_v1
```

### 共享工具
把时间几何抽到公共模块，例如：

- `scripts/tigge/common_geometry.py`
- `scripts/tigge/common_manifest.py`
- `scripts/tigge/common_steps.py`

高温 extractor 调用：

```python
member_value = max(value_native_unit for selected_local_day_buckets)
```

### 高温 lane 的角色
它不是“最终目标”，而是 dual-track spine 的第一条验收线。  
只有 high-v2 跑通，才能证明新 schema / ingest / rebuild 是真的。

---

## 2. 低温 lane（后做，但从一开始就按独立产品建设）

### Raw lane
```text
raw/tigge_ecmwf_ens_regions_mn2t6/
```

### Extract lane
```text
raw/tigge_ecmwf_ens_mn2t6_localday_min/
```

### 产品身份
```text
temperature_metric = low
physical_quantity = mn2t6_local_calendar_day_min
data_version = tigge_mn2t6_local_calendar_day_min_v1
```

### 低温核心难点
不是 min 聚合本身，而是**六小时桶最小值的边界泄漏**。

如果一个 boundary bucket 的最小值真正发生在 local day 外部，
你却把它直接用来训练，那么 low 产品会带有 look-ahead contamination。

### V1 规则
- inner buckets 才可直接用作 clean evidence
- 如果 boundary bucket 可能赢过 inner min
- 整个 snapshot 标记 `training_allowed=false`

这会损失样本，但不会污染系统。

---

## 3. shared local-calendar-day geometry

这部分必须抽成真正公共代码，而不是 high / low 各自复制一份。

### 建议公共函数

```python
def compute_local_day_window(issue_utc, target_local_date, timezone_name):
    ...
```

```python
def compute_required_max_step(issue_utc, target_local_date, timezone_name):
    ...
```

```python
def classify_bucket(window_start_utc, window_end_utc, local_day_start_utc, local_day_end_utc):
    ...
```

### 注意
- `lead_day` 必须锚定 `issue_utc.date()`
- 不要锚定 issue local date
- 不要把 `180` 烧死到代码里

---

## 4. 先做 high raw redownload，再做 low pilot，不要反过来

### 推荐顺序

#### Step A
修高温 raw lane：
- manifest refresh
- dynamic step patch
- 006..204 or computed max step
- local-calendar-day extract

#### Step B
让 high-v2 跑通 ingest / rebuild / parity

#### Step C
再开 low pilot：
- 小城市集
- 冬夏各一组
- DST 邻近样本
- quarantine rate 报告

#### Step D
low pilot 通过后，才允许 low full-history

### 原因
如果你先开 low full-history，但 DB/ingest/calibration 还没 metric-safe，
那你只是更快地把系统推进“污染状态”。

---

## 5. ingest 设计：不要让 raw/extract 直写 legacy 表

TIGGE dual-track 的正确入口应该是：

```text
raw GRIB
-> validated extract JSON
-> coverage / readiness scan
-> ingest_grib_to_snapshots.py
-> ensemble_snapshots_v2
-> rebuild_calibration_pairs_canonical.py
-> platt_models_v2
```

而不是：

```text
raw GRIB
-> 直接往 legacy snapshots/calibration 表里塞
```

---

## 6. readiness 不是一个数

这是 low 最容易被工程上“偷懒”的地方。

必须分别报：

### high readiness
- forecastable slots
- ok slots
- missing slots

### low readiness
- forecastable slots
- ok slots
- causal_na_slots
- boundary_ambiguous_slots
- training_allowed_slots
- quarantine_rate by city

你必须能在 dashboard / report 里一眼看到：

```text
城市 A：high fully ready；low only partially ready
```

否则 low 的实际劣化会被隐藏。

---

## 7. low Day0 runtime：不属于历史 pure-forecast Platt 的第一批工作

这一步最容易顺手做错。

### 正确顺序
1. 先完成 low historical archive + extract + training_allowed framework
2. 再完成 low Day0 nowcast 设计
3. 再 shadow runtime
4. 再考虑真钱

### 不正确顺序
“先把 low 市场跑起来，后面再补 nowcast”

这会让系统在最脆弱的 slot family 上默认查错模型。

---

## 8. 你现在应该如何安排实际执行

### 我建议的实际日程切片

#### Slice 1
只做文档 + schema + typed contracts + source authority

#### Slice 2
高温 lane v2 跑通

#### Slice 3
实现 ingest / rebuild / refit 的 dual-track identity

#### Slice 4
低温 lane pilot + scanner + quarantine report

#### Slice 5
低温历史数据全量

#### Slice 6
低温 Day0 nowcast + runtime shadow

这样做的好处是，任何阶段失败，你都知道失败发生在：
- spine
- high v2
- low archive
- low runtime

而不是全部搅在一起。

## 结论

TIGGE 双轨不是“下载两份 GRIB”那么简单。  
它真正要求的是：

> 让 `mx2t6` 和 `mn2t6` 从 raw acquisition 开始，一直到 calibration / runtime，
> 都在同一套共享时间几何下保持物理身份独立。
