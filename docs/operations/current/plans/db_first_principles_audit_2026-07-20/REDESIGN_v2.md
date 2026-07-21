# DB 跃迁设计 v2 — consult 对撞后的定稿

2026-07-21。v1 (`REDESIGN.md`) 经 GPT-5.6 Pro 第二轮对撞(conversation 6a5f1dda…,判决全文见线程/答案存档)。
本版是**吸收判决后的设计**。v1 被驳倒的部分明说,接受的改动落进结构;拒绝为零——本轮无一条判决被我们用"它不知道的本地事实"推翻,唯一保留项是治理层标注(§6)。

## 0. v1 被打掉的三根柱子(承认并修正)

1. **三个 hot 库 = 把跨文件原子性缺陷带进新架构。** 域边界 ≠ 事务边界;SQLite WAL 下跨 ATTACH 文件事务在宿主崩溃时可只提交子集。16 条 chain-settled 无 settlement_outcomes 的缺口就是这个类。→ **单一 `money-hot.db`** 承载所有必须同事务提交的钱状态;world/forecast 只留可重建 current 缓存。
2. **"learning spine" 是范畴错误。** calibration_pairs 48.16M 行 ≈ 每 settlement 3,923 行——训练展开关系,可重建,不是原子真相。三层重划为**五类**:money-hot / 运维工作态(队列·租约·outbox)/ 不可变 money ledger / 可重建 learning marts / 原始证据。
3. **E1 放 W0 会烧掉结算身份。** zlib_b64 字段可能在 certificate/receipt/settlement 哈希 preimage 内;改存储形态 = 改 JSON 字节 = 毁 v1 身份。→ E1+E4 合并为**证书协议 v2 迁移**:先冻结 v1 序列化 golden vectors,后 v2 版本化 envelope + content-addressed payload,历史 v1 字节永不语义重写(base64 解码为精确压缩字节可以,解压重压不行)。

## 1. 目标形态 v2

```
state/money-hot.db          # 全部同事务钱状态: position_current, collateral 当前值, 活动 order/command, 修复态
state/world-current.db      # 可重建 current-world 缓存(丢了能重建)
state/forecast-current.db   # 可重建 current-forecast 缓存
work/operations.db          # 有界队列/outbox/retry(pending+leased+短完成视界; 历史→证据)
ledger/money-ledger.db      # 不可变: 选中/graded 证书 envelope+edges, position_events, venue/order facts,
                            #   settlements+outcomes, 规范观测 revisions, 决策所用 posterior/ensemble digest
spool/<stream>-active.db    # 每流小活动着陆文件(SQLite, crash-recoverable)
evidence/<retention>/<stream>/<epoch>   # 按流×保留类密封; UTC日或4-8GiB先到; 扫描主导流转 Parquet
learning/<model>/<version>/<window>     # marts: 从 ledger+证据可重建; canonical 事实是窄 graded_predictions
evidence/catalog.db         # 可丢弃索引; epoch 自描述 manifest 才是权威
```

- 耐久分级:money-hot + ledger = FULL+fullfsync;current 缓存/证据 = NORMAL;epoch 密封时全耐久屏障。
- 备份一致性:每份备份 manifest 记 ledger sequence watermark + active epoch id;恢复后按 watermark 对账。
- epoch 生命周期状态机:OPEN→CLOSING→SEALED→EXPORTED→RETENTION_ELIGIBLE,每跳有 crash 恢复;文件名不是删除资格。
- 跨库不变量不"蒸发",变成**交付契约**:每条 hot→ledger/evidence 边显式分类(禁丢/可对账/可丢),одно durable outbox + 单调序列 + 幂等消费者。这是 16 条缺失结算的架构级答案。

## 2. 分类判决表(consult 修正后)

| 对象 | v1 说 | v2 判决 |
|---|---|---|
| calibration_pairs (11.96+19.95GiB, 8 棵 B 树) | spine | **learning mart**;canonical 只留窄 graded_predictions 事实(prediction_id, cert_id, city/date/metric/lead, model 版本, p, y, 权重, 时间戳);聚合"充分统计量"不预设——充分性 per-trainer 证明 |
| book_hash_transitions (2.13+2.55GiB) | 未分类 | head 一行进 hot;历史→证据或删(若可由快照重导) |
| decision_certificates world(1.35M 行) vs trades(58K 行) | E4 双写选一 | **23.2x = candidate 粒度 vs selected 粒度,不是重复**。selected/graded envelope→ledger;落选全量→证据/mart。本地验证:一日 schema+natural key+hash anti-join |
| execution_feasibility_evidence 两库 | E5 双写 ~40GB 回收 | **纠正:两群体 25.58M vs 12.98M 行,非镜像;最多回收 13.46 或 28.59GiB**。先 overlap 证明 + 中立 feasibility_event_id + 读切换,后停写 |
| opportunity_events (30+11.28GiB) | 证据 | **垂直拆**:不可变 envelope→证据;lease/retry/pending 投影→work 库 |
| opportunity_event_processing (5.84GiB) | 未提 | **遗漏命中**:11M 历史处理行是队列尸体;只留 pending+leased+短视界,~5GiB 级回收 |
| forecast_posteriors (3.30GiB, ~70.8KiB/行) | 未提 | **遗漏命中**:E1 同款字节解剖,decision 输入/复用 artifact/诊断数组三分 |
| decision_log 行 | E1 整表证据化 | **按行内权威拆**:诊断 BLOB→证据;envelope+preimage 承诺字节→ledger。可删 epoch 永不做已结算证书验证字节的唯一居所 |
| observation_instants/revisions/hourly | 未分类 | 规范观测事实→**ledger**(结算真相输入);原始源 artifact→证据。"spine 5GB 十年"口号作废,改为按事实类年度字节预算 |

## 3. E1-E8 修正后的处置

| # | v2 处置 | 关键修正 |
|---|---|---|
| E3 parse-once | **W0 唯一效率项**。decode 对象不可变、cycle 作用域;4-8x 数字改由运行时计数证实 | 共享可变 dict 会让重复 parse 掩盖的交叉突变浮出 |
| E1 base64→BLOB | W2 定 v2 协议,W3 新写走 v2,W5 历史重写 | 省幅 21.4% 非 25%;单行 121.9KiB vs 表均 41.9KiB,先做 24h/7d byte-weighted 分布 |
| E4 证书摘要 | 并入证书 v2:共享 opportunity_set payload 一份 + 选中全量 + 全 candidate 紧凑 score/reject + top-K/边界内全量 + Merkle 承诺 | (id,score,reason) 不足以重评历史选择;93→8-12KiB 算术不成立(仅压缩 book 已 ~13KiB) |
| E2 book 形态 | W2 四方基准(对象JSON/+压缩/数组形态/+压缩,不许 40%×4.43x 相乘);tick 整数 + 显式端序 + 版本号,不过 float | 密封流用块级 zstd + 时间 delta,见 §4 |
| E5 feasibility | W2 等价证明→W3 单写切换 | 回收上限修正如上表 |
| E6 calibration | 先杀错误粒度(W5 learning 重建),索引后谈;covering 2-3GiB 目标无 DDL 支撑作废 | WITHOUT ROWID 仅 clone 实测后用 |
| E7 provenance | 并入 envelope v2;非跑道项 | 内嵌值可能是"决策当时所见"唯一记录——先证同权威再去重 |
| E8 members 二进制 | **最后**。显式 LE binary64(408B)或证明过阈值等价的量化;array('f') 作废 | float32 会动决策阈值;306B 低端行 binary64 反而更大——是 CPU 项不是存储项 |

## 4. Orderbook 压缩:负结论收窄 + 新基准

- 保留:**整书 exact CAS 否决**(1.01x,且 9.72M hash transitions ≈ 10.32M snapshots 佐证)。
- 收回:"dedupe 不值得做"的全称结论。仍需基准的三级:price-ladder 因式分解(阶梯稳定时 10-35%)、per-side 复用(低成本)、**per-market keyframe+delta 块**(32-128 张/块,块级 zstd,链不过 epoch 边界;目标 1.3-3x,>20% 增益才采纳)。
- 否决:per-level CAS DAG(元数据反噬)。
- 基准法:≥100K byte-weighted 快照、同市场连续窗口、按活跃/波动分层;验收含随机点查 p99、重建深度、语义 hash 往返、损坏血域。

## 5. 最大的漏项(v1 承认):capture policy——少存,而非只把存的压小

4000:1 是 v1 自己算出的,但 v1 仍在给"每张都存"降价。v2 把**价值感知采集**列为最大单项工程:
决策/submit/fill/cancel/repair 边界全量存;近阈值 candidate 全量;定期 replay keyframe;其余 cycle 存 hash+top-K+经济向量 delta;普通无动作 cycle 可配置采样。**目标 5-20x 新证据缩减,大于 E1-E8 总和**;上线前对 replay 需求验证。

## 6. 波次 v2

| 波 | 内容 |
|---|---|
| W0 钱路修复 | trade_decisions 去死 FK 重建 + FK 同 schema 解析 antibody;**修复门重锚**:command_recovery 改锚定权威 ledger 事件,trade_decisions 降级为投影;7-02 起缺口 backfill/对账 + 幂等 lot-repair 扫描;E3(不可变);source_id 允许清单 |
| W1 生存 + 写控 | 外部备份+restore drill(manifest 带 watermark);**写者统一提前到此**(4 锁方案→1,epoch 路由前);耐久分级落地;磁盘硬预留 + 证据 shedding 水位 |
| W2 契约先于移动 | 表/行权威矩阵;事件 ID 命名空间;outbox/幂等协议;**证书 v1 preimage 冻结(golden vectors, 从持久化字节出发)+ v2 格式**;codec 注册表;epoch 密封协议;capture policy 规格;E2/E5 基准与证明 |
| W3 止血 | 纯 append 流**原字节**改道 spool/epoch + **同步启用有界本地保留或离卷导出**(仅改道≠止血);capture policy 生效;证书 v2 新写;E5 证明后单写 |
| W4 原子核切换 | money-hot.db + money-ledger.db;旧源事务内 outbox 复制→watermark 追平→短暂 fence 翻转权威;禁独立双写 |
| W5 历史重写 + 退役 | 逐流外卷重写(密封 SQLite 贫索引/Parquet;§4 codec);外部目标容量是 gate(119GB 本地装不下 180GB 重写);calibration 按 §2 粒度重建;monolith 退役 |

字节量级账(不可机械相加):capture 5-20x(新增流)、贫索引密封 20-30GiB(历史)、queue 清史 ~5GiB、feasibility 单写 13.5-28.6GiB、已证索引冗余 1.04(或 ~1.5)GiB、E1 v2 新写 ~21%、posteriors/regret 解剖 3-8GiB(调查目标)。

## 7. 治理标注(唯一留给操作员的叉)

`money-hot.db` 合并跨越 K1 三库分权法(root AGENTS §2,db_table_ownership.yaml 机器执法,INV-37 双 helper)。这不是工程细节,是**架构法变更**:需 authority doc 修订 + ownership manifest 重写 + INV-37 语义收窄(合并后跨库写面大幅缩小,法应随之简化而非叠加)。W0-W3 不触碰此法,可先行;W4 前此叉必须由操作员裁决落法。

## 8. 状态

设计=收敛(两轮对撞,第二轮零反驳残留)。实现=未开始,零 live 改动。全部产物在 scratchpad;进 repo 走 worktree+审批。
