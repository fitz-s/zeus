# DB 跃迁设计 — 第一性原理重构(非修补)

2026-07-21 · 基于 census 799/799 实测 + 主线程解剖探针。前置:FINDINGS.md 是缺陷账;本文回答"本来应该长什么样"。

## 0. 第一性拷问:这台机器需要持久化什么?

money chain 每个节点真正需要的持久化,只有三类:

| 角色 | 内容 | 真实体量 | 访问模式 |
|---|---|---|---|
| **热真相 (hot state)** | position_current、snapshot heads(`*_latest`)、risk state、readiness、collateral | **~1-2 GB** | 每 cycle 读写,必须事务、必须耐久 |
| **学习脊柱 (learning spine)** | settlements、graded decision certificates、position lifecycle、calibration 对(重构后) | **~5 GB 级** | walk-forward join,按 (city,date,metric) 读 |
| **证据流水 (evidence firehose)** | snapshots 全史、feasibility evidence、opportunity/no-trade/regret events、decision_log artifacts | **~180 GB = 83%** | **写一次,几乎永不读**;读时按时间段 |

当前病根:三类被搅在 3 个巨型 WAL 单文件里,导致 checkpoint/备份/锁/缓存/retention 全部互相绑架。218GB 里 83% 是证据流水,却让 1GB 的热真相陪它一起不可备份、锁风暴、ENOSPC 倒计时。

**判据(实测)**:`executable_market_snapshots` 17.99M 行 vs `trade_decisions` 历史总量 4,645 行——**存储/需求比 ≈ 4000:1**。FC-03 本来就要求 submit 时重新拉快照;决策时刻的 book 真相已冻进 decision certificate。全史逐 20s 存 book 是在为不存在的用例付 50GB。

## 1. 目标形态(跃迁后)

```
state/
  hot/trades-hot.db        # position_current, heads, collateral, readiness  (~500MB, sync=FULL+fullfsync)
  hot/world-hot.db         # market topology heads, 当前 opportunity 状态     (~300MB)
  hot/forecasts-hot.db     # 当前 posteriors, 当前 ensemble heads             (~200MB)
  spine/learning.db        # settlements + graded certs + lifecycle + calib   (~5GB, 单文件够用十年)
  evidence/epoch-YYYYWW-{trades,world,forecasts}.db   # 密封周 epoch,zstd 列
  evidence/catalog.db      # epoch 目录: role, 时间界, checksum, state
```

- 热库小到可以**连续在线备份**(SQLite backup API 秒级);epoch 密封后 rsync 一次即离机。
- retention = **删除整个过期 epoch 文件**,O(1),永别 ENOSPC 倒计时(现在 34 天)。
- INV-37 跨库写基本蒸发:证据写 = append 到 epoch(无跨库事务);热真相事务在单个小文件内。
- 4 套写锁方案的矛盾自然消解:争抢的 90% 是证据 append(去 epoch 车道),热写极小。
- crash 恢复、integrity_check、restore drill 全部从"对 94GB 不可行"变成"对 500MB 例行"。

## 2. 立即可取的效率跃迁(全部实测背书,不等重构)

### E1. decision_log:base64 反膨胀 —— 最快增长表的 25% 即刻砍除
实测解剖(rowid 116739,124,808 B/行):`summary.candidate_evaluations_delta_zlib_b64` 61,910 + `book_native_side_delta_zlib_b64` 40,642 + `holding_..._zlib_b64` 4,374 ≈ **86% 的行体是"已 zlib 再 base64 塞回 JSON 字符串"**——base64 纯膨胀 33%,zlib 再压只得 1.39x(因为已压过)。
**改法**:这三个字段改存 BLOB 列(zlib bytes 直存),artifact_json 只留元数据。2.34 GB/day → ~1.75 GB/day,一张表砍掉全 fleet 增速的 ~16%。hot-fix 体量。
**再问一层**(第一性):190K 行、每 cycle 一个 124KB 全景 artifact,谁读?若只有事后诊断读,它整体属于 evidence 车道,epoch 化后热库归零。

### E2. orderbook 存储形态:array-of-objects → 平行数组
每档 `{"price":"0.003","size":"100"}` 把字面量 `"price"`/`"size"` 重复 N+M 次。改 `[[p,s],...]`:原始字节先砍 ~40%,再叠 zlib-6(实测 4.43x)。对 46.3GB 的最大单表,新写入即刻变瘦;冷行迁移进 epoch 时统一转。
**内容去重实测 1.01x(2000 行样本)——book 每次捕获真的不同,hash 去重不值得做**;省字节靠形态+压缩,不靠 dedupe。

### E3. 单 cycle 重复解码:同一 book JSON 每评估被 parse 4-8 次
W6 静态枚举 10 个 `json.loads(orderbook_depth_json)` 调用点(executor/cycle_runtime/event_reactor_adapter/qkernel_spine_bridge 都在每 cycle 路径)。**parse 一次挂到 snapshot 对象上共享**,每 20s tick 省 270KB-1.6MB 纯重复解码 + GC 压力。纯代码改动,零 schema。

### E4. ActionableTradeCertificate:一行 93KB,其中 opportunity_book 92.8KB
选中 candidate 的同一份 economics dict 在一行里出现 **3 次**,22 个落选 candidate 每个整份 dict 全存;`receipt_hash` 一行重复 25 次。每个决策固定链 23-24 个 overflow 页。
**改法**:落选 candidates 只存 (id, score, reject_reason) 摘要 + 完整 book 的 zlib BLOB(实测 7.12x);选中者存一份。行体 93KB → 约 8-12KB。
**双写纠偏**:decision_certificates 同时在 world(3.13GB)和 trades(0.22GB)——归属裁决一处。

### E5. execution_feasibility_evidence:同一概念存了两个库
trades 19.03GB + world 10.83GB = **~30GB(加索引 ~40GB)一份事实两份账**。canonical 归属裁决 + 另一份停写,是全 fleet 最大的单笔字节回收。
同类:probability_trace_fact 两库都有;hourly_observations(forecasts 类)躺在 world/trades。

### E6. calibration_pairs:索引是表的 1.66 倍
表 11.96GB,**7 条索引共 ~19.9GB**,全部压在 48M 行上;idx 前缀高度重叠(group_lookup / group_lookup_lead / decision_group / city_date_metric / bucket / calibration_bucket)。walk-forward 只按少数几条访问路径 join。
**改法**:访问路径审计 → 索引收敛到 2-3 条覆盖索引;48M 行本身要问——每决策组保留全对,还是按 (city,date,metric,lead) 聚合后的充分统计量?后者是学习脊柱的形态,量级直接掉两个数量级。

### E7. provenance_json 双编码 + 冗余
实测:`provenance_json` 内嵌 `"forecast_window_block_reasons_json": "[]"`(字符串化 JSON),同值已是同表顶层列——一行写两遍,其中一遍双编码。ensemble 表里 provenance(1276-1362B)比 members(306-1017B)还大,本末倒置。写路径去重即可。

### E8. ensemble members_json → 定长二进制
51 个 float 的向量存 JSON 文本(~300-1017B)。`array('f')` BLOB = 204B 定长,解码零 parse(memoryview 直读)。2.32x 压缩比之上再省 parse CPU。学习/trigger 每 tick 都读它——这是热路径解码税。

## 3. 象限重排(设计后视角)

- **已知已知**:E1-E8 全部有实测数字,直接执行。
- **已知未知**:epoch 粒度(周 vs 5-20GB 定长)、老 snapshot 是否存在时间段读者(动 retention 前唯一必答题,rg 一天可答)、calibration 充分统计量形态(要 replay 对拍)。
- **未知未知消解**:分层后,证据车道的任何未知损坏血域被 epoch 边界钳制;热库小到 integrity_check 例行化——"未知"从不可测变成日常可测。

## 4. 执行序(与 FINDINGS.md 六 packet 的合并)

| 波 | 内容 | 体量 | 依赖 |
|---|---|---|---|
| W0 hot-fix | G2 dangling FK;E1 decision_log BLOB 化;E3 单次 parse | 小 | 无 |
| W1 | PKT-2 外部备份(先保命)+ E5/E4 双写裁决停写 | 中 | 无 |
| W2 | PKT-3 连接层 fail-closed + durability 分级(为 hot/ 分层铺路) | 中 | W1 |
| W3 | evidence epoch 车道落地:新写入转向 epoch;三巨表停止增长(consult 的 arrest-growth-first,不做 94GB 原地 VACUUM) | 大 | W1,W2 |
| W4 | 热库拆出(hot/*.db);写锁收敛到 WriteCoordinator 单方案 | 大 | W3 |
| W5 | 冷史重写进密封 epoch(E2 形态转换 + zstd),老单文件退役;E6 calibration 重构 | 大 | W3,W4 |

每波独立可回滚;W0/W1 本周可落;W3 之后 ENOSPC 议题永久关闭。

## 5. 反对意见(自我对撞)

- "epoch 拆文件丢跨史事务"——证据流水本无跨行事务需求(全是 append-only 触发器保护的表,实测 35 条 no_update/no_delete 触发器);需要事务的热真相留在热库。
- "重构风险 > 修补收益"——W0-W2 就是修补,先落;W3 起每步 additive(新写入转向,旧文件只读退役),无一步破坏性迁移;consult 已论证 no-downtime 序列。
- "SQLite 换引擎?"——不换。热真相/学习脊柱正是 SQLite 甜区;流水去 epoch 后单文件永不再破 20GB。换 Postgres/ClickHouse 引入的运维失效面在单机单写者拓扑下纯属负资产。
