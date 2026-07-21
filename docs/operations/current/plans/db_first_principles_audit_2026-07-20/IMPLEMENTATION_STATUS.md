# DB 重构实现状态 — 操作员行动图

2026-07-21 · worktree `db-impl-2026-07-21` · **live checkout 零接触**。全部代码编译过、fixture 测试绿(30+ antibody);活库运行是操作员门。

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
