# 01. 架构诊断：为什么现在必须做双轨大改

## 结论先行

Zeus 现在已经有了很强的“治理/拓扑/权威性”骨架，但真正决定交易结果的数据面、
校准面、Day0 运行时面，仍然是**单指标 high-first 系统**。  
也就是说，它已经不是“散乱代码仓库”，但还不是“高低温双轨交易机器”。

现在正好是最适合改造的时机，因为：

- 上游 TIGGE 数据本来就要重下
- raw -> DB ingest 仍未完成
- `ensemble_snapshots` 还是空的
- data rebuild 还在被 TIGGE/ETL 链路阻塞

这意味着你现在做结构级改动，代价远小于等到新数据已经灌进 world DB 之后再返工。

## 当前仓库已经变强的地方

### 1) 治理和拓扑层已经像一个“系统”了

仓库已经把 `architecture/`、`docs/authority/`、`docs/reference/`、`docs/operations/`
提升成一级结构，active docs 也明确围绕 current architecture / domain model /
runbook / known gaps 组织。  
这说明 Zeus 已经不再只是“代码集合”，而是在往拓扑编译器/系统宪法方向走。

### 2) 系统边界和不变量已经比较清楚

- world / decision / process 三层已经明确
- DB 被定义成 canonical truth surface
- `strategy_key` 被提升成唯一治理键
- live-only、append-first、risk must change behavior 这些原则已经明确

这非常关键，因为它意味着你的 refactor 不需要从零定义秩序，而是要把 low 接到一条
已经存在但尚未完成的数据-概率-执行主权链上。

## 当前最危险的断裂点

### 1) 低温只是“部分穿线”，不是闭环系统

仓库里已经有一些 low 迹象：

- `market_scanner` 会从市场文本中推断 `temperature_metric=low`
- `ensemble_signal` 已经可以对 low 取 per-member min
- evaluator 已经开始携带 `temperature_metric`

但闭环没有打通：

- `observation_client` 只把 `high_so_far` 当成 Day0 运行时契约
- `day0_signal.py` 的主概率逻辑仍然是 `final_high = max(obs_high, remaining_ens_max)`
- canonical rebuild 仍然只读 `high_temp`
- `p_raw_vector_from_maxes(...)` 和 rebuild script 的语义还是 max-first
- `platt_models` / `calibration_pairs` / `ensemble_snapshots` 还没有 metric 级隔离主键

结果就是：**代码表面上出现了 low 字样，但系统语义并没有真正 dual-track。**

### 2) DB 还没有 metric spine

虽然 observations 表已经有 `high_temp` 和 `low_temp`，但核心训练与推断基座并没有
把 `temperature_metric`、`physical_quantity`、`observation_field`、`data_version`
作为一等公民。  
这会导致高低温一旦进入同一训练基座，就只能靠“人记得不要混”，这在大 refactor 中
一定会出事。

### 3) Day0 low 还没有正确的因果模型

low Day0 和 high Day0 不是镜像关系：

- high 的关键约束是“已观测高点形成 hard floor”
- low 的关键约束是“已观测低点形成 hard ceiling / downside clamp”，但它又受到
  夜间/清晨时段、日界边界、剩余时长、当前温度轨迹的强影响

现在的 Day0 substrate 仍然是 high-centric：
- `observation_instants` 只有 `running_max`
- `day0_residual_fact` 只有 `running_max` 和 `residual_upside`
- `day0_signal.py` 虽然收到了 `observed_low_so_far` 和 `member_mins_remaining` 参数，
  但主逻辑仍然调用 `day0_blended_highs(...)`

这说明 low Day0 还没有独立模型，只是被“勉强穿进了接口”。

### 4) 训练链仍然是 high-only

`scripts/rebuild_calibration_pairs_canonical.py` 现在还是：

- 查 observations.high_temp
- 读 snapshots.members_json 作为 `member_maxes`
- 调 `p_raw_vector_from_maxes(...)`

而 `scripts/refit_platt.py` 的 bucket 仍然主要按 `cluster + season` 聚合，缺少
metric / physical_quantity / track identity。  
如果在这种状态下把 low 数据接进来，最坏情况不是“不工作”，而是“悄悄污染”。

### 5) TIGGE ingest 是最大机会，也是最大风险点

`scripts/ingest_grib_to_snapshots.py` 仍然是 placeholder，而且里面保留的允许数据版本
还是 `tigge_mx2t6_local_peak_window_max_v1` 这一代旧想法。  
这说明**最适合做大改的切口，就是现在**：在 raw->DB 入口还没正式打开前，把新 row
contract 一次性定对。

## 真正的系统需求（第一性原理）

预测交易机器不是“能算出概率就行”，它至少需要这五件东西：

1. **目标函数**
   - 系统最终优化什么
   - 哪些损失是不可接受的（污染、泄漏、错轨、错 source）

2. **事实主权**
   - 哪个数据源说了算
   - 外部 source 变了怎么办
   - fallback 是否可训练、是否只能运行时使用

3. **可验证状态模型**
   - 哪些表是 canonical
   - 哪些状态可重建、哪些不可重建
   - 任何概率是从哪条因果链来的

4. **动作隔离**
   - high 和 low 在何处共享
   - 在何处必须硬隔离
   - 何时 fail-closed

5. **迁移证明**
   - 改完之后如何证明 high 没被破坏
   - low 是不是 shadow 成熟后才允许接入真钱

Zeus 现在第 2 和第 4 点做了一半，第 3 和第 5 点对 dual-track 还没有完成。

## 诊断结论

你的 refactor 不能从“补 low 功能”开始。  
必须从**安装 metric spine** 开始，然后再把数据、校准、runtime 接上去。

一句话总结：

> 现在的 Zeus 已经像一座有宪法的城市，但天气概率工厂仍然只为“daily high”造机器。
> 低温不能作为附属功能接进来，必须作为第二条独立物理生产线建设。
