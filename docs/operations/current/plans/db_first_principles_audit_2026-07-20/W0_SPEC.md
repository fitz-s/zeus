# W0 钱路修复 — 实现 spec(consult 攻击目标)

2026-07-21 · 落 worktree `db-impl-2026-07-21` · **编写完成即发 consult 对撞;对撞过 + verifier 证伪后,gated 运行步骤呈操作员。**
W0 目标:解冻 `trade_decisions`,恢复受损的 filled-entry 修复通道,零结算身份风险,不跨 K1 法。

## 事实基线(实测,2026-07-21)
- 物理表(live zeus_trades.db):`trade_id INTEGER PRIMARY KEY AUTOINCREMENT` + `forecast_snapshot_id INTEGER REFERENCES ensemble_snapshots(snapshot_id)` ← **内联列级 FK,真实存在**。
- db.py 现行 CREATE 意图:同列注释 "v1.F20 soft ref, no FK constraint" ← 代码与物理分叉;物理是 F20(2026-05-18)前旧 schema。
- 规模:3106 行,rowid 13–4645,**零索引,零触发器**(trade_decisions 不在触发器清单)。
- 症状:`foreign_keys=ON`(db.py:263)下,本地 `ensemble_snapshots` 已被 F20 删除(canonical 移入 forecasts.db),FK 目标在 main schema 不存在 → 每条 INSERT/UPDATE 编译期报 `no such table: main.ensemble_snapshots`。表自 07-02 冻结(last rowid 4645 @ 2026-07-02T00:10Z)。
- 血域:`command_recovery.py:6500` filled-entry lot 修复门 = `EXISTS(SELECT 1 FROM trade_decisions WHERE runtime_trade_id=cmd.position_id OR ...)`;07-02 后进场仓位无 trade_decisions 行 → 永过不了门 → position_lots 修复被静默跳过。
- 写路径:`get_trade_connection_with_world()`(db.py:1141)ATTACH forecasts as 'forecasts';迁移候选连接需决定是否复用此路径。

---

## W0-a — trade_decisions 去 FK 重建(危险:活库 schema 迁移)

SQLite 无 ALTER DROP 列级 FK → 必须表重建。单库(仅 trades main schema),小表。

**拟定过程**(单事务原子,官方 12 步的单库特化):
```
-- 前置:获取 trades 写者锁(序列化,禁其他写者并发);记录迁移前 sqlite_sequence(trade_decisions) 值
PRAGMA foreign_keys=OFF;              -- 连接局部;12 步必需
BEGIN IMMEDIATE;
CREATE TABLE trade_decisions_new (... 逐列复制,唯一改动:forecast_snapshot_id INTEGER  -- 去掉 REFERENCES ...);
INSERT INTO trade_decisions_new SELECT * FROM trade_decisions;   -- 显式保留 trade_id(rowid 连续)
DROP TABLE trade_decisions;
ALTER TABLE trade_decisions_new RENAME TO trade_decisions;
-- 保 AUTOINCREMENT 续号:确认 sqlite_sequence 有 trade_decisions 行且 seq>=4645(RENAME 保留;若丢则显式重置)
COMMIT;
PRAGMA foreign_keys=ON;
PRAGMA foreign_key_check;             -- 必须空
PRAGMA integrity_check;              -- 小表,可跑,必须 ok
```

**未决问题(consult 攻击点)**:
1. **fence vs writer-lock**:表已冻结(无成功并发写),是否只需 trades 写者锁即可,还是需 T5 式全 daemon fence(`--operator-confirms-fenced` + 进程扫描)?冻结这一事实是否让"仅写者锁"足够安全?
2. **连接选择**:走 db.py 常规 WAL 连接(单库原子已足),还是 T5 那种专用 `journal_mode=DELETE` 连接?T5 用非 WAL 是为跨库崩溃原子性——本例单库,WAL 单事务已原子,是否 DELETE 连接反而多余?
3. **AUTOINCREMENT/sqlite_sequence**:`INSERT ... SELECT *` 显式带 trade_id 是否足以让新表续号 >4645?RENAME 后 sqlite_sequence 行是否自动保留?需不需要显式 `INSERT INTO sqlite_sequence` 兜底?
4. **列漂移风险**:物理表有 `p_calibrated`,而 db.py 某些 INSERT 不含它。重建用**物理表的 `SELECT sql`** 为准(逐列照抄减 FK),不用 db.py 的 CREATE(避免引入代码侧漂移)。这是否正确策略?
5. **ATTACH 状态**:迁移连接是否应**不** ATTACH forecasts(避免任何跨库残留),纯 trades 单库操作?

**Antibody(测试,必须先于运行绿)**:
- 崩溃矩阵(仿 `tests/test_t5_quarantine_phase_retirement_migration.py`):在 BEGIN 后/DROP 后/RENAME 后/COMMIT 前各 kill point,验证要么旧表完整、要么新表完整,永不半态。
- FK 目标解析全库 antibody:遍历三库每条 FK 边,断言目标表存在于**同 schema**(会一并抓出任何其它 F20 遗留)。
- 回归:迁移后 `foreign_keys=ON` 下对 trade_decisions 成功 INSERT 一行;`foreign_key_check` 空。
- 幂等:迁移脚本二次运行是 no-op(检测已无 FK 则跳过)。

---

## W0-b — 修复门重锚(command_recovery.py:6500,money-path)

consult round-2 裁定:"修复门锚定权威已提交事件,不依赖 fail-soft 的 trade_decisions;把 trade_decisions 降级为投影。"

**现行谓词**:`EXISTS(trade_decisions WHERE runtime_trade_id=cmd.position_id OR CAST(trade_id AS TEXT)=cmd.position_id OR =cmd.decision_id)` —— 语义是"这是个真实决策产生的仓位"。
**重锚候选**(择权威 ledger 事实):
- `position_current` 存在该 position 且有 confirmed entry;或
- `venue_trade_facts` 存在对应 fill(该 cmd 已 MATCHED/MINED/CONFIRMED —— 但外层 SQL 已 join fact.state,故 EXISTS 内应换成**决策/仓位存在性**而非 fill 存在性,避免自反);或
- `position_events` 有该 position 的 ENTRY 事件。

**未决(consult 攻击点)**:哪个谓词最准确表达"合法进场且应修复其 lot",且不重新依赖任何 fail-soft 表?会不会放宽/收紧修复资格导致误修或漏修?需给出精确谓词 + 为何它是权威。

**Antibody**:构造(a)有 trade_decisions 行的老仓位、(b)07-02 后无 trade_decisions 行但有权威进场事件的新仓位、(c)真无进场的杂散 cmd;断言新谓词对 (a)(b) 放行、对 (c) 拒绝。

---

## W0-c — 07-02→now 缺口对账(幂等)

FK 修复后新写恢复,但缺口期仓位仍无 trade_decisions 行。consult:"跑被跳过的 lot-repair 对账。"
- 方案:W0-b 重锚后,缺口仓位已能过修复门 → 跑一次幂等 lot-repair 扫描修复其 position_lots。
- 不选:批量 synthesize trade_decisions 回填(consult 倾向降级 trade_decisions 为投影,回填是给投影补数据,非权威修复;保留为 fallback)。
- **未决**:是否还需为可观测性回填 trade_decisions(仅作投影)?还是纯靠 W0-b 重锚即闭环?

## W0-d — E3 orderbook parse-once(热路径,低风险)
10 个静态 `json.loads(orderbook_depth_json)` 点(executor/cycle_runtime/event_reactor_adapter/qkernel_spine_bridge...)。挂一个**不可变、cycle 作用域**的懒解码对象到 snapshot,共享一次解析。4-8x 数字由运行时计数证实(不预设)。独立小 PR,不阻塞 W0-a/b。

## W0-e — source_id 启动 pin(G1 残留)
G1 已证 live 全走 venv 3.53.2(安全)。残留价值:启动门 pin 批准的 `sqlite_source_id()` 允许清单,防未来解释器悄换回退 3.51.2;禁 3.51.2 CLI 碰活库。纯新增校验。

---

## 运行门(RUN GATE — 非本轮)
W0-a 的**运行**(对 live zeus_trades.db 执行迁移)与 W0-c 对账扫描是操作员逐步确认动作:呈报时附 (1) consult 对撞结论、(2) verifier 证伪结果、(3) 崩溃矩阵测试绿证据、(4) 回滚点(迁移前该表 `.dump` 到 worktree 外只读留存 —— 注意 DB backup guard 禁整库备份,单表 dump 允许)、(5) 预期停机窗口(写者锁持有时长,3106 行毫秒级)。**编写与测试在 worktree;运行等操作员令。**
