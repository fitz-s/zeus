# DB 跃迁 — 执行控制器(四象限重分类 + 修正流 + 派发矩阵)

2026-07-21 · Opus max · 输入:census 799/799、consult 两轮(6a5f1dda…)、REDESIGN_v2、G1/G2 闭环。
**铁律**:live checkout 零接触;所有产物走 linked worktree(`db-impl-2026-07-21`);任何对 live DB 的**运行**(迁移/回填)是操作员逐步确认门,本控制器只授权 worktree 内**编写 + 读态探针**。

---

## 1. 四象限重分类(扫盲区 = 追踪象限迁移)

盲区不是靠一次枚举扫掉,是靠**追踪每条工作在两轮 consult 后从哪个象限迁到哪个象限**——迁移本身暴露了当初的盲。

### KK 已知已知(证据在手,直接执行)
| 项 | 证据 | 落波 |
|---|---|---|
| G2 dangling FK → trade_decisions 自 07-02 冻结,修复通道受损 | 物理表 `REFERENCES ensemble_snapshots`;3106 行;command_recovery.py:6500 EXISTS 门 | **W0** |
| ENOSPC ~34 天 @ 3.38 GiB/day | growth lane;df 119GB | W1 预留 + W3 止血 |
| 零外部备份;唯一 raw-copy 漏 -wal | ops_posture;source_contract_auto_convert.py | **W1** |
| registry 把活表标成待删 ghost(decision_log 8.16GB/最快增长) | dead_tables;registry drop-date | W2 权威矩阵 |
| 4 套不兼容写锁 → database-is-locked 风暴(13,749+) | connections lane | W1 写者统一 |
| 3 冗余索引 ~1.04GiB drop-only | census 字节同尺寸对 | W1/W5 |
| E1/E2/E4/E5/E7 解码·双写具体数字 | decode+duplicate lanes(consult 已纠错) | W2/W3/W5 |

### KU 已知未知(已命名,决策前必测)— **本轮探针关闭**
| 项 | 为何仍未知 | 谁关 |
|---|---|---|
| G4 census `cells` 在 overflow 表高估行数(snap 真 ~10.3M 非 17.99M) | dbstat cells ≠ rows | **探针 A**(本轮) |
| G3 fullfsync/checkpoint_fullfsync 实际 pragma + macOS 断电耐久 | grep 0 命中,未证 | **探针 A** → W1 |
| G5 world.db 热查询计划未审 + world 独有 stat1/stat4 | W9 只覆盖 trades/forecasts | **探针 A** → W2 |
| G6 mmap 32GB 映射冷头非热尾 | 未测 | **探针 A** → 连接层 |
| E1/E2/E4/E5 精确字节省幅 | consult 要 byte-weighted p50/90/99 + overlap 证明 | W2 基准 |
| 证书 v1 preimage:哪些字段进结算/receipt/edge 哈希 | 未枚举 | **子代理 C**(本轮,门控 E1/E4) |
| feasibility 双库精确 overlap/各库独有 | 未 join 证 | W2(E5 前) |

### UK 未提出已知(consult 揭出,原计划漏)— 盲区主体
| 项 | 原为何盲 | 落点 |
|---|---|---|
| **跨文件崩溃原子性清单(G8)** — 16 条 chain-settled 缺 settlement_outcomes 疑为 partial-commit | 只当孤儿,没连到原子性 | **子代理 D**(本轮)→ W2 outbox |
| **capture policy — 少存而非只压小,5-20x > E1-E8 总和** | 自算 4000:1 却仍给"全存"降价 | **子代理 E**(本轮)→ W2 spec |
| forecast_posteriors 70.8KiB/行 payload | E1-E8 整个漏了它 | 探针 A 附带 → W2 |
| opportunity_event_processing 11M 队列尸体 ~5.8GiB | 当证据,实为可清运维态 | W2/W3 |
| 密封证据贫索引化 ~20-30GiB | 只想压表不想砍索引 | W5 |
| epoch 按流×保留类分区,非按旧库名 | 沿用三库名做 epoch | W2 协议 |
| **money-hot 合并跨 K1 三库分权法** | 当工程细节 | **操作员门(W4 前)** |

### UU 未知未知(结构消解,非枚举)
- 消解机制:epoch 边界钳制任意损坏血域;小热库让 integrity_check 从"不可行"变日常可测;outbox+对账让 partial-commit 浮现;语义 hash 往返抓静默证据损坏。
- 残余探针(设计内建,非本轮):大规模时间戳反常、APFS 快照 CoW 迁移期占空、**capture policy 的 replay 充分性(削减前必须对 replay 验证)**、任何序列化改动的证书哈希稳定性(golden vectors 是 antibody)。

**盲区扫除结论**:两轮后 KU→KK 两项闭环(G1/G2);UU→UK 两项被 consult 点名(跨文件原子性、capture policy)——**这两项是原计划最大的盲**,现进 W2 正列。本轮探针关掉剩余 4 个 KU 数字盲(G3/G4/G5/G6)。UU 靠结构消解,不靠再派枚举代理。

---

## 2. 修正执行流(consult 定序)

```
W0 钱路修复 ──▶ W1 生存+写控 ──▶ W2 契约先于移动 ──▶ W3 止血
                                                          │
                                          ┌───────────────┘
                              【操作员门:money-hot 合并 = K1 法变更】
                                          │
                                          ▼
                              W4 原子核切换 ──▶ W5 历史重写+learning 重建
```

硬依赖(consult 裁定,不可乱序):
- W2 必须先于任何数据移动(边界未定不能路由 epoch)。
- 写者统一从 W4 提前到 **W1**(锁风暴中加 epoch 车道 = 加乱)。
- E1 从 W0 除名 → W2 定证书 v2 协议后,W3 新写走 v2,W5 历史重写。
- W3 仅改道 ≠ 止血:必须同步有界保留/离卷导出。
- W4 跨法,操作员门之后才动。

---

## 3. 派发矩阵(本轮)

**安全边界**:下列全部是 worktree 内**编写文档/spec** 或 **只读探针**;零 live 写、零代码运行、零 schema 变更。探针只读用 `.venv/bin/python`(3.53.2)+ `file:...?mode=ro` + `mmap_size=0`,一次一查询。子代理产出经 final report 回主线程,由我持久化进本 worktree(避免跨 tenant 写)。

| # | 代理 | 模型 | 类型 | 产出(回传) | 门控下游 |
|---|---|---|---|---|---|
| A | 数字盲扫(G3/G4/G5/G6 + posteriors 解剖) | sonnet | 只读探针 | probe_measurements | 全部 sizing/W1 耐久/W2 |
| B | 权威矩阵(5 类 × 全表) | sonnet | 设计 | authority_matrix | W2/W3/W4/W5 总控 |
| C | 证书 v1 preimage 冻结 + golden-vector harness 设计 | **opus** | 只读代码分析+设计 | certificate_v1_freeze | E1/E4(结算身份) |
| D | 跨文件原子性清单 + outbox 契约(G8) | **opus** | 设计 | atomicity_outbox_contract | money-hot + 16 缺失结算 |
| E | capture policy spec(最大杠杆) | sonnet | 设计 | capture_policy_spec | W2/W3 |
| — | **W0 spec**(本控制器作者写) | 本线程 opus | 设计 | W0_SPEC.md | consult 攻击 |
| — | **consult**(攻击 W0 spec) | GPT-5.6 | 外部对撞 | followup 6a5f1dda | W0 落地前最后一道 |

**验收(验收 = 每件独立复核)**:
- W0_SPEC → consult 对撞(危险的活库迁移,落地前必过)。
- 权威矩阵(B,总控件)→ critic 代理复核分类边界。
- 探针 A 数字 → 我方 spot-check(一张表 row-count 用 max(rowid) 复算核对)。
- 每波实现完成 → verifier 代理(fresh context,试图证伪)+ antibody 测试绿。

**未派(明确不做)**:
- 跑任何 live 迁移/回填 → 操作员逐步确认门。
- money-hot 合并任何代码 → K1 法变更,操作员裁决门。
- W1-W5 代码 → 依赖 W0 落地 + 本轮 spec 收敛,下一轮再派。

---

## 4. 本轮之后

consult + 5 代理回来 → 我合成:定稿 W0 代码(本 worktree 分支)+ verifier 证伪 + 把 gated 的**运行**步骤连同回滚点呈报操作员。W1+ 待 W0 落地按同法逐波推进。零 live 改动贯穿始终。
