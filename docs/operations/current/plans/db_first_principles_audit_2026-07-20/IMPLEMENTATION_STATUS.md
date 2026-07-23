# DB 重构实现状态 — 操作员行动图

2026-07-21 · worktree `db-impl-2026-07-21` · **live checkout 零接触**。全部代码编译过、fixture 测试绿(40+ antibody);活库运行是操作员门。

## 2026-07-21 续跑进度(39 commit 领先 origin/live)

**本续跑新交(worktree 已提交,live 零接触)**
- `scripts/ops/reconcile_settlement_outcomes.py` + 抗体(4 测试绿,commit 63ce61a1)— 只读跨库对账门(§3.5 anti-join)。**活库只读实测确证**:settled-无-outcome gap 从审计时 16 增至 **27**,27/27 全 `VENUE_RESOLVED`(系统性路径分歧、非 partial-commit;根治=W4 durable outbox 操作员门)。修了 brief 的 schema 误设(`settlement_authority` 非 position_current 列、在 position_events.payload_json)。

**本 session 并行三 agent — 全部已交付+主线程亲验+提交(81 抗体全绿 @ 40760942f)**
- `capture-track-a` → **capture-policy Track-A**(commit 40760942f,25 测试)。additive-only:`capture_trigger` 幂等 ALTER+CHECK、`executable_market_snapshot_compact` 表(创建但未用)、每个 insert_snapshot 写点打标(JIT→JIT_SUBMIT、bulk→PRIORITY_MARKER/DISCOVERY_SWEEP)、`_snapshot_from_row` 单入口 log-only hydration check(`try/except pass` 永不 raise)。diff 逐行确认零控制流改动、零既有读者改动。**[已过时 — PR #436 评审后:compact 表、CHECK、hydration check 三者全部移除未落地;taxonomy 校验改在 insert_snapshot 写边界(PR #438)。见 residue_dissolve_2026-07-23.md。]**
  - **操作员落地要点**:①落 live 前须 `python scripts/check_schema_fingerprint.py --write-pin`(新增列+表→schema 指纹变,full-conftest 会 SCHEMA DRIFT 硬退)②Track-A check 在 money-path 读 DISCOVERY_SWEEP 行时 log warning = 设计要测的 false-negative 信号,可能高频,部署留意。
  - **named assumptions**(留后续增量):全 flattened-priority 记 PRIORITY_MARKER(无法在此还原 held/open-rest 子因,不影响 Track-A 安全/路由);未造 NEAR_THRESHOLD/KEYFRAME 机制(env var 零命中,非"给既有捕获打标"范畴)。
- `cert-v1-freeze` → **证书 v1 golden-vector 冻结**(commit 3d7bf34d1,9 测试)。pin 真实生产路径(build_certificate/canonicalization.stable_hash/ledger.insert_idempotent/no_submit_receipts)的 payload/cert/receipt hash 为硬编码字面量 → E1/E4/任何 canonicalization 改动移动 v1 preimage 字节即 loud fail。证 edge 顺序 load-bearing、timestamp reparse 陷阱、E4 双身份 mutation。**诚实 gap**:global-auction receipt_hash(E1)未 pin(需活 conn+大量内部态,重构不可信)。
- `outbox-recon` → **settlement 对账只读监控**(commit 63ce61a1,4 测试)。见上。

**连接层 findings-fix + E4 契约(第二波并行,已交付+亲验+提交)**
- `checkpoint-fix` → **W5-2/3/4 checkpoint false-green 修复**(commit c4be91179,90 抗体全绿)。`_wal_checkpoint_is_starved(log,ckpt)` 提取纯 helper 替换死码 `busy==0`(PASSIVE busy 恒 0);真信号 `ckpt<log AND log>131072帧(512MiB)`;新增 `checkpoint_forecasts_wal()` 孪生(W5-4,forecasts 之前无 backstop)。**合法纠错**了一个既有 RED 测试(旧断言"PASSIVE 截断"= SQLite 假语义)。**操作员注意**:落 live 新增定时任务 `_forecasts_wal_checkpoint_cycle`@偏移150s。**KEEP-PASSIVE**,TRUNCATE-vs-PASSIVE 张力正确留给操作员/consult(未冒险实现阻塞 TRUNCATE)。
- `cert-v2-design`(opus)→ **证书 v2 envelope spec**(commit cb04f536e,设计-only)。核心最小:仅 `schema_version==2 时 payload_hash=stable_hash(identity_view(payload))` 剥离**仅** `_diagnostics` 键;`certificate_hash_for` 不动(已验 schema_version 在其 preimage 内 = discriminator hash-bound)。揪出前设计 4 真错(§3 混淆 identity/retention;freeze §4 重造 header 破坏 verifier by-name 读;`_audit_existing_payload_hash` 对 v2 break = 格式落 W2 之因;receipt schema_version 未 hash-bound)。W2 最小改 = 4 处(identity_view + 版本感知 audit + 版本感知 receipt + v2 golden vectors),无 DDL。W3 写、W5 relocate-never-summarize。authority 方向对(world=live)。

**在途**
- Consult `REQ-20260721-204133-420956`:4 锁+336 零锁写者统一 cutover 对撞(bridge-lock 增量 vs fenced big-bang;connection-factory pushdown;BULK-yield 保 K3;world 进程内锁跨进程折叠)。**GPT-5.6 Pro 深推超 60min 首个 await 窗,daemon 自身 resume 检索中(pid 27389/27390,conversation 6a6011c7),已重新 await 挂接**。答案落 `/tmp/cgc/answer_REQ-20260721-204133-420956.txt`。W5-8 fail-open connect 设计就绪(scratchpad),排此 consult 后(同处 `_connect`)。

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
