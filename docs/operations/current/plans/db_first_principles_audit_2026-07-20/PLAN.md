# DB 第一性原理全面体检 — 调查 spec(只查不改)

Created: 2026-07-20 · Status: INVESTIGATING · Owner: main thread(integrator)
Scope: `state/*.db` 全部文件 + `src/state/**` 数据层 + 所有写入/解码/连接路径。
铁律:本 packet **只调查、只产出 findings,不实现任何修复**。实现按 §5 worktree/packet 流程另开。

## 0. 为什么(第一性原理)

DB 是 money path 的 truth 载体:`canonical DB/event truth outranks derived JSON`(root AGENTS §2)。
每一字节、每一次 decode、每一次 lock wait 都应有存在理由。本审计从零假设出发:
**任何表、索引、写入、编码、连接模式,除非能证明其服务于
`contract → source → signal → calibration → edge → execution → settlement → learning` 链上的某个节点,否则视为待清除的熵。**

## 1. 基线普查(2026-07-20 23:02 实测,read-only)

| 文件 | 大小 | journal | tables/indexes | 备注 |
|---|---|---|---|---|
| `zeus_trades.db` | **94 GB** | WAL(85MB wal) | 80 / 194 | **无 sqlite_stat1 — 从未 ANALYZE** |
| `zeus-world.db` | **84 GB** | WAL(4.1MB wal) | 113 / 249 | WORLD_CLASS 名义上只管 markets/provenance,84GB 反常;2026-06-16 已有 bloat-prune 前科 |
| `zeus-forecasts.db` | **40 GB** | WAL(656KB wal) | 45 / 118 | 有 sqlite_stat1(1 行) |
| `cycle_phase_study.db` | 477 MB | **delete** | 12 / 39 | 6-11 后未动,study 遗物 |
| `risk_state.db` | **390 MB** | WAL | **1 / 0** | 单表无索引,与 12KB 的 `risk_state-live.db` 成对,疑无界追加 |
| `zeus_backtest.db` | 56 MB | WAL | 3 / 5 | freelist 6395/14287 ≈ 45% 空页 |
| `zeus.db` | 1 MB | WAL | **74 / 157** | 有人在错误路径跑过全量 schema init |
| `zeus_forecasts.db` / `zeus_world.db` / `zeus-trades.db` / `zeus_live.db` / `zeus-live.db` | 0–4 KB | — | — | 命名孪生幽灵(dash/underscore 混淆),mtime 6-17~6-28 |

共同 PRAGMA:page_size=4096,auto_vacuum=0,freelist≈0(三大 DB)。
连接层(`src/state/db.py`):WAL + foreign_keys=ON + cache 1GB + **mmap 32GB 上限(< 每个大 DB)** + busy_timeout(env 可调);单写者由 `db_writer_lock.py`(~900 行 allowlist)执法;跨 DB 只有 INV-37 两个 sanctioned helper。
磁盘:`/System/Volumes/Data` 926GB,已用 87%,余 119GB;state/ 共 223GB。

## 2. 问题象限(全流程调查方向)

### Q1 已知已知(证据在手,待量化定级)
1. **容量跑道**:87% 已用;三大 DB 若维持增速,ENOSPC → SQLITE_FULL → RED 是确定性事件,只差日期。
2. **trades 无统计信息**:94GB、194 索引、query planner 盲飞 — 最高杠杆的已知问题。
3. **world.db 84GB 身份不符**:名义 markets/provenance,疑 snapshot/event 类 payload 挤占;有 2026-06-16 prune 前科(`prune_terminal_opportunity_events.py` 是唯一 retention 脚本)。
4. **幽灵 DB 文件**:错误路径 opener 至少存在过;`zeus.db` 74 表 = 全量 schema init 曾走错路。**若 opener 还活着,某组件可能在静默读空库**(fail-soft orphan 模式,记忆中有同类前科)。
5. **无回收机制**:auto_vacuum=0 + 唯一 prune 脚本只覆盖一张表;删除不还盘。
6. **risk_state.db 390MB 单表无索引** + `-live` 孪生:命名分裂 + 无界增长嫌疑。
7. **遗物库**:cycle_phase_study(477MB)、backtest(45% 空页)、zeus.db — 死库候选。
8. **mmap 32GB < 单库体积**:K3 cold-cache 疤痕(db.py 注释)在 94GB 时代是否复发。
9. world.db 内 trade-owned position 表 = legacy ghost shells(root AGENTS §2 已定性)— 死表候选,治理已背书。

### Q2 已知未知(问题已命名,答案要测)
- 每表/每索引字节分布:218GB 到底在哪几张表?
- 每表日增速 → 精确 ENOSPC ETA。
- 重复写入地图:同一 fact 写几份?(trades snapshot vs world "empty shadow" 已知一例)
- 死表清单:代码零读者 ∪ 近 N 天零写入 ∪ ownership yaml 与 sqlite_master 双向 diff。
- 解码成本:JSON/blob 列有哪些、单 cycle 解码量、最热反序列化路径、压缩空间。
- 热路径 query plan 健康度;194 索引里多少从未被用;缺失索引。
- WAL checkpoint 健康:频率、BUSY 率、峰值 WAL、reader-pin 饥饿(db.py:719,759 有疤)。
- 连接生命周期:每 cycle 建连数、busy_timeout 命中、lock wait 分布。
- synchronous 值(耐久 vs 吞吐从未被审过)。
- integrity_check/quick_check 从未跑过?备份/恢复姿势(218GB 活库怎么备)?

### Q3 已知未提(证据推得出、尚未被提出的问题)
- **空库静默读**:谁创建了 zeus.db/zeus_live.db?creator 代码路径今天还在不在?(比磁盘更急 — 这是 correctness 风险)
- executable snapshots 双 schema 残留(trades 实体 vs world 空影)清除。
- page_size 4096 对 100GB 级库是否仍最优(8192 的 IO/溢出页权衡)。
- 大 payload 溢出页占比(overflow-page ratio)— 4KB 页装 JSON 大对象的隐性放大。
- sqlite CLI 3.51.2 与 Python 内置 driver 版本/行为差异。
- 审计自身安全:长读扫描 pin WAL floor — 见 §4 安全法。
- append-only 表的 supersession(`append_only_supersession.py`)是否真的有配套 retention。

### Q4 未知未知(方向性方法,不预设答案)
- 外部对撞:GPT-5.6 Pro consult(方法论 + 100GB 级 SQLite 陷阱清单)已后台发射,答案落地后并入本 packet。
- 反常扫描:bytes/row 离群表(体积与用途不符者自动上榜)。
- 日志挖掘:`logs/*.err` 里静默的 SQLITE_BUSY/IOERR/schema-changed/disk 类错误。
- 时间戳反常:未来时间、epoch-0、UTC/local 漂移(logs=local、DB=UTC 是已知记忆)。
- 跨库悬挂引用:condition_id/position id 孤儿。
- 页级健康:fill factor、碎片、溢出页 — dbstat aggregate 全表扫描出分布。

## 3. 调查 lanes(workflow 派发,全部 read-only)

| Lane | 内容 | 产出 |
|---|---|---|
| W1 | trades 94GB 普查:dbstat aggregate 每表/索引字节、top 表行数(max(rowid))、schema 抽查 | findings/census_trades.md |
| W2 | forecasts 40GB 普查(同法)+ sqlite_stat1 现状 | findings/census_forecasts.md |
| W3 | world 84GB 普查(同法)+ 与"markets/provenance"身份的偏差定量 | findings/census_world.md |
| W4 | 杂库审判:幽灵文件 creator 溯源(git+rg)、risk_state 对、study/backtest/zeus.db 判决(CURRENT/STALE/DEAD) | findings/stray_dbs.md |
| W5 | 连接/吞吐/日志:db.py+db_writer_lock+connection_pair 逐行、synchronous 值、checkpoint 谁在跑、logs/*.err DB 错误挖掘 | findings/connections_throughput.md |
| W6 | 解码/存储编码:JSON/blob 列清单、热路径反序列化、单 cycle 解码量、压缩/编码空间 | findings/decode_serialization.md |
| W7 | 重复写入地图:同 fact 多表/多库、snapshot 双 schema、append-only 无 retention 表 | findings/duplicate_writes.md |
| W8 | 死表清除候选:ownership yaml ↔ sqlite_master ↔ 代码读者三向 diff、近 30 天零写表、ghost shells | findings/dead_tables.md |
| W9 | 计算效率:cycle_runner/evaluator/monitor 热查询 EXPLAIN QUERY PLAN、无 stat1 影响、索引冗余/缺失 | findings/query_efficiency.md |
| W10 | 增长率/保留:大表按日行数增速、ENOSPC ETA、retention 现状全景 | findings/growth_retention.md |
| W11 | 完备性批评家:漏了什么方向? | findings/completeness_critique.md |
| W12 | 综合:全部 findings → 按(money-path 风险 > 磁盘风险 > 性能)排序 + 象限标注 + 每条附修复方向/爆炸半径/owning surface | FINDINGS.md |

## 4. 审计安全法(census agent 必守 — 库在 24/7 交易)

1. 只读:`sqlite3 -readonly "file:<path>?mode=ro"`,首句 `PRAGMA query_only=ON`。
2. 一次调用一条查询,禁止长驻交互会话(长读 pin WAL floor)。
3. 体积用 dbstat **aggregate 模式**(`dbstat('main',1)`),逐表短事务,不做全库单事务扫描。
4. 行数优先 `max(rowid)`;`count(*)` 仅限 dbstat 先证实 <5M 行的表。
5. 永禁:VACUUM / ANALYZE / wal_checkpoint / integrity_check(大库)/ 任何写 PRAGMA / 任何 DML。
6. 三大库全表扫描**串行**(trades → forecasts → world),不并行抢 IO/page cache(K3 冷缓存疤)。
7. 每 lane 前后记录 `-wal` 文件大小;WAL 超 1GB 立即中止该 lane 并上报。
8. 扫描期间 `logs/zeus-live.err` 出现 DATA_DEGRADED 激增 → 停手上报。
9. 不写任何 `state/` 文件、不改代码、不 commit;findings 只写入本 packet 目录。

## 5. 验收与后续门

- 每条 finding 必附证据:query 原文 + 数字,或 file:line。
- FINDINGS.md 完成后:操作员审阅 → 按 §5 change control 逐条开 implement packet(ANALYZE、retention、死表清除、幽灵 opener 修复等皆属实现,不在本 packet)。
- Consult 答案落地后由 main thread 并入 §2-Q4 与 FINDINGS.md 附录。

## 6. Non-goals

不改 schema、不删表、不跑维护命令、不动 `src/**`、不开 PR。本 packet 终点 = 一份可直接切 implement packet 的裁决书。
