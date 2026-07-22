# DB 重构实现状态 — 操作员行动图

2026-07-21 · worktree `db-impl-2026-07-21` · **live checkout 零接触**。全部代码编译过、fixture 测试绿(40+ antibody);活库运行是操作员门。

## 2026-07-21 续跑进度(39 commit 领先 origin/live)

**本续跑新交(worktree 已提交,live 零接触)**
- `scripts/ops/reconcile_settlement_outcomes.py` + 抗体(4 测试绿,commit 63ce61a1)— 只读跨库对账门(§3.5 anti-join)。**活库只读实测确证**:settled-无-outcome gap 从审计时 16 增至 **27**,27/27 全 `VENUE_RESOLVED`(系统性路径分歧、非 partial-commit;根治=W4 durable outbox 操作员门)。修了 brief 的 schema 误设(`settlement_authority` 非 position_current 列、在 position_events.payload_json)。

**在途(本 session 并行)**
- Agent `capture-track-a`:capture-policy Track-A 首个可落地增量(additive-only:`capture_trigger` 幂等 ALTER + compact 表 DDL + 单写点分支打标 + Track-A log-only hydration 断言 + 抗体)。设计 `implementation/capture_policy_spec.md`。
- Agent `cert-v1-freeze`:证书 v1 golden-vector 冻结抗体(护结算身份 preimage 字节)。设计 `implementation/certificate_v1_freeze.md`。
- Consult `REQ-20260721-204133-420956`:4 锁+336 零锁写者统一 cutover 对撞(bridge-lock 增量 vs fenced big-bang;connection-factory pushdown;BULK-yield 保 K3;world 进程内锁跨进程折叠)。答案落 `/tmp/cgc/answer_REQ-20260721-204133-420956.txt`。

**连接层已确认(consult 落地即可实现)**:`world_write_mutex`(scheme 4)= 进程内 threading.Lock、只跨线程、**不跨进程**;`world_write_lock` 对已开事务幂等(re-entrancy 模式);checkpoint 助手与写锁子系统正交(W5-2/3 独立)。待批:W5-2 false-green alert(`busy==0` 恒真→WARNING 死码;真信号 `checkpointed<log`)、W5-3 TRUNCATE-vs-PASSIVE 张力(需判断/consult)、W5-8 fail-open rwc(`no such table` 源)。

## 已授权 + 已测试(operator-gated 运行)

| # | 交付 | 文件 | 测试 | 运行前置 |
|---|---|---|---|---|
| 1 | **W0-a** trade_decisions 去悬挂 FK 重建(解冻,2026-07-02 起冻) | `scripts/migrations/202607_trade_decisions_drop_dangling_fk.py` | 16/16 | 停全 daemon(fence)→ dry-run → 跑 → 验;consult+verifier 双过 |
| 2 | **悬挂 FK 抗体** + 检测器 | `tests/test_no_dangling_foreign_keys.py` | 5/5 | 三库清零后转启动门 |
| 3 | **regret_decompositions** 去死 FK(→removed shadow_experiments) | `scripts/migrations/202607_regret_decompositions_drop_dead_fk.py` | 4/4 | 同 fence(world 库,0 行) |
| 4 | **W1 P0 外部备份**(SQLite backup API,含 WAL,restore-drill) | `scripts/ops/backup_canonical_dbs.py` | 4/4 | 操作员给**外卷**路径 |
| 5 | **F15 冗余索引删除**(2 个,减写放大) | `scripts/migrations/202607_drop_redundant_trade_indexes.py` | 5/5 | 同 fence |

**运行序(1/3/5 共用 fence)**:`launchctl bootout` 全 `com.zeus.*` → 各 migration `--dry-run` 审 → `--operator-confirms-fenced` 跑 → 重启 daemon。备份(4)独立,给外卷即跑。

## 撤销 / 降级(实测推翻计划)
- **W0-b(去 command_recovery 候选门)= 撤销 no-op**:position_lots.position_id keyed 到 trade_decisions.trade_id,apply 的 resolve 照样需 trade_decisions 行 → 去门修不了缺口。consult 不知此耦合。
- **W0-c 降级低紧迫**:267 缺口仓位全有 position_current、82% 已结算 → 缺 position_lots 是二级明细账,非 live exposure 少算(RiskGuard 走 position_current + unprojected-fill 网)。可后排。

## 未做(需操作员判断,非拖延)
- **durability fullfsync=ON**:probe-A 实测 synchronous=FULL 但 fullfsync OFF → macOS 断电可丢 trade DB 已提交事务。但 blanket fullfsync 影响证据写(每 20s snapshot)延迟——consult 建议**按数据类分级耐久**(money/ledger=fullfsync,evidence=NORMAL)。需连接层区分 DB 用途,是延迟/耐久权衡,操作员定。
- **money-hot 合并**:跨 K1 三库分权法(root AGENTS §2),W4 前操作员裁决。
- **W0-c 合成 recovered_projection**:低紧迫,待排。

## 设计层(全 3 轮对撞 + critic 收敛,见 REDESIGN_v2 + implementation/)
6 类权威矩阵(critic 修正证书权威反转)、capture policy(5-20x,replay 前提实证解锁)、outbox 契约(16 缺失结算=by-design 非 partial-commit)、证书 v1 冻结(E1 SAFE/E4 需 v2,harness 活库 26/26 验)、跨库原子性清单。波序:W0→W1(生存+写控,写者统一提前)→W2(契约先于移动)→W3(止血)→【操作员门】→W4(原子核)→W5(历史重写)。

## 下一波可立即开工(无需操作员资源)
W1 写者统一(WriteCoordinator 完成)、W2 权威矩阵驱动的 registry 真相化(manifest-rot 门:`legacy_archived` 标签 drop 前须 writer/reader 复核)、capture policy Track-A 断言实现。
