# W0 修正 runbook — consult 对撞后的 GO-after-correction 版

2026-07-21 · 取代 `W0_SPEC.md` 的缺陷部分。consult round-3 全文见 `consult_W0_verdict.md`。
**判决:W0-a as originally specified = NO-GO;修正 5 个 blocker 后 = GO。不改 journal mode,不需 reader fence。**
状态:编写完成;**运行是操作员逐步确认门**(见末尾)。

## consult 抓出的 5 个 blocker(我原 spec 的真缺陷)

| # | 我原 spec 的错 | consult 修正 | 危害若不改 |
|---|---|---|---|
| B1 锁覆盖 | 只取"trades 写者锁" | 需**全写者维护 fence**——4 套不兼容写方案并存;表冻结只说明对**该表**写失败,position_current/commands/lots 等同库其它表仍可写 | 其它锁命名空间的写者收 SQLITE_BUSY,可能丢 fail-soft money-path 写 |
| B2 SELECT * | `INSERT ... SELECT *` | 两侧**显式审阅列清单**(从审阅后目标 DDL 生成),含 p_calibrated,按 trade_id ORDER | 物理列序未来漂移 → 值静默错位;p_calibrated 漂移已证非假设 |
| B3 AUTOINCREMENT | "seq ≥ 4645" | **错不变量**。AUTOINCREMENT 承诺新 ID 超过**历史曾用最大值**(存 sqlite_sequence)。DROP 删旧 seq 行,复制 ID 只按当前 max(4645)重建 → 若曾插入>4645 后删除,rebuild **降低高水位、重用已消费 ID** → 把已删历史决策别名到新决策。必须存精确 old_seq 并 RENAME 后显式 UPDATE 回填 | trade_id 被当永不复用(甚至 `CAST(trade_id AS TEXT)` 比对 command id)→ 别名旧决策,污染 idempotency/audit join |
| B4 post-check 范围 | `PRAGMA integrity_check` + `foreign_key_check` 无限定 | **意外全库**:integrity_check 扫整个 93.9GB;foreign_key_check 扫全库无关子表。用 `PRAGMA main.integrity_check('trade_decisions')` + 三个有界 FK 检查 | 微型 hotfix 变成活库 94GB 扫描 |
| B5 W0-c 幂等 | "跑幂等对账扫描" | **不是实现**。需 DB 强制 `UNIQUE(source_venue, source_fill_id, lot_role)` 或等价;position-level `NOT EXISTS` 错(一个 partial fill 掩盖另一个)。position_lots 无 per-fill 身份则 W0-c NO-GO | check-then-insert 无 UNIQUE ≠ crash/retry 幂等 |

## 确认/修正的架构点
- **交易模型**:单库 WAL 单事务 `BEGIN IMMEDIATE...COMMIT` 已崩溃原子;**不需** DELETE journal(那是给跨库的,本例单库多余),**不需** reader fence(WAL reader 留旧快照,崩溃只暴露旧或新已提交 schema)。撤回我早先"要不要 T5 式 fence"的纠结:要 **全写者 fence**,不要 reader fence。
- **耐久**:迁移连接开始前设 `synchronous=FULL` + `fullfsync=ON`(呼应 probe-A G3:daemon 已 FULL 但 fullfsync OFF,macOS 断电仍丢)+ `wal_autocheckpoint=0`(免这次微型 commit 意外触发大 PASSIVE checkpoint)。
- **foreign_keys=OFF 位置**:必须在 `BEGIN IMMEDIATE` **之前**执行并断言(事务内改是 no-op);COMMIT/ROLLBACK 后再开回并断言回到 autocommit。
- **schema 权威**:物理 `sqlite_schema.sql` + `table_xinfo` 定义**须保留什么**;db.py 注释定义**意图改动**(soft ref);审阅后的目标 DDL 成为新权威,**同一次代码改动也要替换 db.py 里漂移的 CREATE**。
- **rollback**:主机制是事务本身(任何断言失败 → 显式 ROLLBACK,无需恢复表)。额外:fence 持有期间、BEGIN 前,用 venv 3.53.2(非禁用的 3.51.2 CLI)导出**单表 rollback capsule**(独立小 SQLite 文件 + 元数据 + SHA-256,非 CSV、非整库——合规 DB-backup guard)。

## W0-b 精确谓词(consult 定,money-path)
重锚到"**这条精确 ENTRY command 有至少一个 canonical、非 reverted、正 venue fill,且该 fill 的经济量尚未在 position_lots 表示**"。删除 trade_decisions 的 EXISTS,强化既有行谓词(不新增 venue_trade_facts EXISTS——外层已 join,重复即 tautology):
```
cmd.<role>='ENTRY' AND cmd.position_id IS NOT NULL
AND fact.command_id = cmd.command_id           -- 精确等值,无 OR 别名链
AND fact.<qty> > 0 AND fact.<state> IN ENTRY_LOT_MATERIALIZATION_FINAL_STATES  -- 复用正常 lot writer 的共享谓词,不新拷
AND fact 是当前 canonical/非 reverted
AND fact.token_id = cmd.token_id
AND (fact.position_id IS NULL OR fact.position_id = cmd.position_id)
AND position_lots 中该精确不可变 fill 身份的经济量 < canonical filled 量
```
错误锚点的失败模式(consult 表):position_current confirmed→漏修(投影可能同崩溃缺失)+过修(乐观/管理/链创建);position_events ENTRY→过修(ENTRY 是意图非终态)+漏修+重复 lot;裸 venue_trade_facts→过修(exit/reverted/错 token);trade_decisions→漏修(现案已证)。**唯一正确 = 精确 ENTRY command + 精确 canonical 正 fill,幂等键锚定 fill 身份。**
**必须先测的运行事实**:venue_trade_facts 是 **fill-grain(一 fill 一不可变行)还是 cumulative-snapshot-grain**?这一条决定最终 SQL(fill-grain→按 stable fill id 建 lot;cumulative→取 canonical 最新累计事实,只插正 shortfall)。→ W0-b 前置探针。

## 激活顺序(consult 定;答我原问题 f)
1. 实现 W0-b,**shadow/report-only** 跑新旧谓词对比(不插 lot)。
2. 落地目标 db.py DDL + 迁移脚本 + antibody + schema 指纹。
3. 取**全 trades 写者 fence** + 暂停显式 checkpoint owner。
4. 采集并校验 rollback capsule。
5. 执行修正版 W0-a。
6. 新连接验 schema/data/sequence + 回滚版编译 smoke test(EXPLAIN 或 savepoint no-op UPDATE 回滚——**不提交合成假决策行到活库**)+ 重启/重开每个 fenced 写者连接。
7. 释放写者,验真实 trade_decisions 写恢复且无新 `main.ensemble_snapshots` 错。
8. 激活 W0-b 的 fill-authority 谓词。
9. W0-c dry-run → 操作员审 manifest → apply → 第二次跑须零修复。

**为何 A 先于 active-B**:W0-a 恢复现有 gate 给新决策,停止再制造投影缺失;未充分验证的 B 先行会立刻过修真钱。A-first 只留已知历史缺口一小段,不造双破态。

## W0-c(consult:repair lots,不伪造决策)
- fill→lot 对账(非合成 trade_decisions 回填);先 dry-run manifest(source_fill_id/command_id/position_id/token/state/qty/cost/fee/已表示 lot 量/proposed delta/eligibility/quarantine)。
- 只读检测全历史,首个变更 cohort = 已知 07-02→cutover 缺口;按精确 source key apply,发 repair-audit 事件,重跑须零。
- **拒绝合成历史 trade_decisions**;记显式 projection-gap 区间;日后重建须每字段源自权威且标 `recovered_projection`。
- **前置**:grep 分类 trade_decisions 全部 runtime reader(ledger 权威/投影报表/诊断/死码)——任何第二个 money-path reader 须先重锚才能宣缺口无害。
- **unfreeze backlog**:去 FK 后所有原失败写者立即成功——释放 fence 前须证无 durable retry/outbox 会把 07-02→今写当作当前重放,且重复 lifecycle/exit-audit 写有稳定幂等。

## 强制绿 antibody 矩阵(运行前必过)
`tests/test_*trade_decisions_fk_rebuild*.py`:
- **crash matrix**:BEGIN 后/CREATE 后/copy 中/DROP 后/RENAME 后/COMMIT 前各 kill,另进程重开须恰旧态或恰新态,无 `_new` 残留、无混行。(kill -9 证应用崩溃,非断电;活跑仍靠 FULL+fullfsync)
- **drift matrix**:p_calibrated、意外多列、改 default、incoming view/FK、预存 `_new`、源 DDL 哈希不符——每个异常须 DROP 前 abort。
- **sequence matrix**:old_seq=max_id、old_seq>max_id、有 gap、二次 no-op(必测 ID 9000 插后删、max=4645、seq=9000 → 迁移后下一 ID 须 9001 非 4646)。
- **concurrency matrix**:长 reader 跨迁移证旧/新快照;未协调写者证脚本在 fence/BEGIN abort 非续行。
- **recovery matrix**(`tests/*command_recovery*`):旧 pre-gap fill、07-02 后无投影 fill、多 partial fill、重复调用、exit fill、零 fill、reverted fill、token/position 不符、缺 position_current、乐观 ENTRY 无 fill、fill 无 ENTRY——只精确 canonical entry-fill shortfall 可建 lot。

## 前置探针已闭(实测 schema,2026-07-21)
- **W0-b 粒度确认 = cumulative-observation-grain**:`venue_trade_facts` `UNIQUE(trade_id, local_sequence)` + `state∈{MATCHED,MINED,CONFIRMED,RETRYING,FAILED}` + `confirmation_count` → 同一 `trade_id` 随确认推进多行。W0-b/W0-c 必须取每 `trade_id` 的 canonical 最新事实(最高 local_sequence / CONFIRMED),只插正 shortfall,**不能一行一 lot**。稳定 fill 身份 = venue `trade_id`,非行。
- **W0-c 前置 NOT MET(须先加 schema)**:`position_lots` 唯一约束是 `UNIQUE(position_id, local_sequence)`;`source_trade_fact_id` 仅有**非唯一** `idx_position_lots_trade`。**无 DB 强制 per-fill 幂等**。→ 印证 consult B5:W0-c NO-GO 直到引入确定性身份。选项:(a) 加 `UNIQUE(position_id, source_trade_fact_id)` 或按 venue `trade_id` 的确定性 cumulative-repair 键;(b) 该 schema 变更本身是 money-path DDL,走自己的 antibody + 操作员门。W0-c 依赖此前置先落。

## 运行门(RUN GATE — 非本轮)
呈操作员:此 runbook + antibody 全绿证据 + rollback capsule 就绪 + 预期 fence 时长(3106 行毫秒级 + 全写者 fence 编排)。W0-b 前置探针(venue_trade_facts 粒度)与 W0-c 前置(position_lots per-fill UNIQUE 是否已存,无则先建)须先闭。**代码编写 + antibody 在 worktree;活库运行等操作员令。**
