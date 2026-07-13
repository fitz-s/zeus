# Local-Ledger Excision — 本地记账摘除计划(2026-07-12)

**Operator law(逐字):** 消除所有的本地记账。这些东西和 quarantine 一样是疾病。不允许本地记账,不允许本地计算链上已经有的内容;需要的信息直接也仅从链上同步。不符合第一性原理的全部执行。

**Status: ADJUDICATED — census 4 路收口;consult 深评已裁(2026-07-13,GPT-5.6 Pro,答案原文 `/tmp/cgc/answer_REQ-20260713-004546-1e919f.txt`,thread 6a541c54-4bec-83ea-8db1-a6c55bfafbc1);计划已按裁决重写;待操作员裁 §Operator forks 后开工。**
方法论 = `quarantine_excision_2026-07-11.md` 同型:疾病定义 → 全量 census → 摘除目标 T-系列 → consult 裁决 → wave 排包 → 逐包对抗 verifier。

## Disease definition(什么算病)

一条本地持久化的值是病,当且仅当它是**链上已知事实的拷贝或本地推导**:

- 链上已知事实(the test):CTF ERC-1155 token 余额;fills(价格/数量/tx hash/订单关联);USDC 余额;condition resolution payout 向量;redemption 收付。
- 由这些事实 + Zeus 自己的提交身份(order attribution)可推导的任何数:shares、avg price、cost basis、realized P&L、exit price(作为 payout)、proceeds、余额镜像列 —— 全部 CHAIN-DERIVABLE = 病。
- 病理机制与 quarantine 同构:**平行账本必然漂移** → 漂移养出 reconcile/recovery 机器山 → 机器自身产生撒谎判词(realized_pnl=0.0 的隐形亏损)→ 更多修补机器。杀死平行账本,整座山失去存在理由。

**Exhibit A(今天的活体证据):** settled-clobber bug —— economically_closed 已入账的 realized_pnl_usd/exit_price 被结算重投影清零(portfolio.py:2490 hydration 缺失 → lifecycle_events.py:184 last_exit_at 代理误判 → upsert 0.0 覆盖真值,~28 行 7 天)。**如果 P&L 从不本地存储、永远从链上 fills 推导,这个 bug 类别在表示层就不可能存在。** 这不是又一个要修的 bug,是账本本身是 bug。

## First-principles target shape(终态)

1. **链事实表(sole authority for chain-knowable)**:append-only 同步自 venue/chain —— trades/fills(带订单关联键)、balances、resolutions/redemptions。同步是**唯一写者**;没有第二个写者,没有"修正"通道。
2. **推导即读(derive-on-read)**:P&L、持仓规模、cost basis = 链事实上的确定性纯函数(view 或读时计算),按 Zeus order attribution 过滤(共享钱包法:foreign fills 是预期,靠 venue_commands 身份链 join 归属)。**没有 stored realized_pnl_usd 列可以被 clobber。**
3. **真本地真相(KEEP,不在病域)**:
   - EPISTEMIC:决策证书(冻结 q,walk-forward 法)、posteriors、校准、settlement_outcomes(WU 温度 = WORLD 真相,非链事实)。
   - INTENT:venue_commands、idempotency keys、submission envelopes、exposure obligations、ReviewWorkItem。
   - VENUE-not-chain:open orders / CLOB book 状态(链看不见未成交单)—— venue API 是权威,本地只许 cache。
   - RISK/CONTROL:riskguard、kill switch、HALT —— 操作员域。
4. **公理不动(§C6 继承)**:fail-closed freshness 分向(入场 fail closed;exit 在 venue 真相充分时继续);3-DB split 定法;结算值只经 SettlementSemantics;Zeus 永不提交 redeem tx(但可读 resolution/redemption 事实);no shadow modes;绝不动 kelly/caps/风险姿态。

## 已知硬问题(census/consult 必须回答)

- **Q1 归属键完整性**:chain/data-api trade 记录 ↔ Zeus order_id 的 join 是否端到端无损?(已知一处 lossy:tx_hash 聚合 vs child trade IDs,2026-07-11 已修过 2x 计数。)无损归属是整个 derive-on-read 的地基。
- **Q2 延迟窗**:链/venue 同步的新鲜度能否支撑 exit 决策节奏?现 M5/ws-gap/heartbeat 已量测什么 SLO?同步降级时的行为 = 入场 fail closed、exit 按 venue 真相继续(公理),推导层怎么表达"数据不新鲜"而不复活本地镜像?
- **Q3 结算语义分工**:结算 VALUE(温度)= WORLD 真相不上链;但 won/lost + payout 链上有(resolution payout 向量)。grading 的哪一半改读链、哪一半留 WU?两者不一致时 = DISPUTED lane(T2b 已建),不是本地再算一份。
- **Q4 pending 态**:pending_entry/pending_exit 是 INTENT(链上还没有),合法本地态;但其"économics 预估"列不许假装是账。phase enum 里哪些态编码 intent、哪些编码链可推导态 —— census-local-truth 在答。
- **Q5 in-flight 资金预留**:collateral reservation(CAS 账本)是防止超卖的 intent 态还是链余额的平行账?W1.1 CAS 是刚验收过的机器 —— 判死前要 consult 对撞。
- **Q6 历史语料**:校准/学习要 settled 证书×结算 join。历史 P&L 语料从链事实重导 vs 冻结存档(证书是冻结的 epistemic 真相,不是账)—— 边界要划清。

## 与在飞工作的关系

- **settled-clobber fix(branch `claude/agent-a6f2441fe93b8c506` @ 320a4b903,MERGE_PENDING)**:止血件,修的是将死器官上的活跃出血。按 R0-a 先例(先止血后摘除)排队给操作员;它的 antibody 测试在摘除波会随 realized_pnl_usd 列一起退役,test 退役与列退役同 commit(§C3 删旧法)。backfill/repair 脚本同理 = 过渡期工具,摘除后死。
- **quarantine excision(已收口)**:T4 fill_tracker、T2b DISPUTED lane、ReviewWorkItem 机器是本计划的直接前置资产 —— review-not-mint 的形已立。
- **R2 reconcile lane(src/reconcile/)**:chain_truth + fill_dedup 是链同步 spine 的种子(KEEP/长成);local_truth 比较面随本地账本死。R5 单写者 projection 的"单写者"在本法令下升级为"链同步是唯一写者"。
- **R2-c 判决继承**:command_recovery 的恢复 pass 今天仍在 money path 发火 —— 摘除顺序必须是**先立链同步 spine、证等价、再杀账本、最后杀靠账本吃饭的恢复机器**。绝不先删。

## Census(4 路,2026-07-12 派发;报告落盘 `docs/rebuild/census_local_ledger/`)

- **census_local_truth.md ✅** — KEEP 15 / BORDERLINE 5 / CACHE-OK 6。核心:A1-A6 typed vocabulary(canonical_lifecycle.py:57-168)已画出大半边界 —— A5 phase 自称 "PROJECTION, not a source";order_state_predicates.py:1 已是摘除原则的先例("storing a recomputable classification would create a stale copy")。BORDERLINE 五问:B1 phase 列可否纯 derive(intent 相位的非链重建源要先证);B2 chain-mirror event_types 留作观察回执但禁读当前值;B3 collateral_ledger 哪些列是 reservation-intent vs 钱包余额 cache;B4 结算 resolution-payout 半边归 CACHE(WU 温度 VALUE 归 KEEP);B5 ReviewWorkItem 重建性(推荐 KEEP)。
- **census_chain_sources.md ✅** — 四族充分性判决:fills+order 归属 **今天已充分**(order_id 全链路 through-key,foreign fill 以 order_id∉venue_commands 隔离 —— 共享钱包 Zeus-only P&L 可推导的机制已在);balances **充分**(pUSD 30s + CTF balanceOf 精确,唯 token 枚举集来自 wallet-aggregate data-api);open orders **充分**(order_id→command join 同解);**resolution payouts 需新 ingester**(payoutNumerators 今天只在 redeem-preflight 摸过,从未接入结算权威;_json_rpc_call 管道已在,缺 ingester+接线)。两处 lossy:data-api /positions 无 order 键(wallet-aggregate,永不可做 per-strategy 账);tx_hash 聚合 vs child trade IDs 靠 dedup CTE 缓解未根除。
- **census_ledgers.md ✅(finalized,写者全量核实)** — ~23 面、三 DB 列级分类。爆炸半径 Top-3:①position_current 经济学+chain_* 镜像列(1002 行);②collateral_ledger_snapshots(**101,291 行纯链余额镜像**,authority_tier='CHAIN',还在增长 —— 最大的平行账本);③venue_trade_facts→position_lots fill spine(636+205,本地重建 cost basis 喂 ①)。关键细分:settlement_outcomes 是 MIXED 非平行账本 —— winning_bin 链可推导、settlement_value(温度)是链不知道的 WORLD 真相;TRADE/WORLD 的 settlements 表是 0 行死壳。
  - **精化 #1:position_current 经济学并非单写者。** INSERT 单 funnel(projection.py:658),但 chain_*/exit_price/realized_pnl 另有 **~11 个直接 UPDATE 旁路**:position_duplicate_consolidator.py:186/369、command_recovery.py:2461/6215/8282/8387/8618/8683、exit_lifecycle.py:4680、exchange_reconcile.py:1418/1813、edli_position_bridge.py:1003 —— 全是摘除所预言的漂移修复机器;derive-on-read 层把列和这批 repairer 一起删。
  - **精化 #2:新病灶面 `edli_live_profit_audit.pnl_usd` + `realized_edge`** —— 结算时本地重算的存储 P&L(settlement_skill_attribution.py:1124 计算、:1128 writeback;live_profit_audit.py:238 insert),与 position_current.realized_pnl_usd 同 clobber 类。列入 ranking runner-up,归 LX-T3 同波处置。
- **census_drift_machinery.md ✅** — ~27 单元(command_recovery 42 def 分 4 漂移族;exchange_reconcile 分 5),毛重 ≈36K 行,漂移核心 ≈24-27K。dies-when 三簇:**D1+D2**(本地命令态 + position_current cache 退位权威)≈24-27K —— C2+REAL 恢复族、M5 sweep、restart-preflight、chain_reconciliation 写回 appenders、reconcile/local_truth+diff_engine、fill_tracker、~8 修复脚本;**D3**(EDLI 双账本移除)≈2.5K;**D4+D5**(booked close-economics 列 + 结算本地权威死)≈2.4K(含今天 clobber fix 的 guard 本体)。**KEEP spine**:chain_mirror_reconciler(目标内核)、chain_truth.py、fill_dedup canonical+economic-identity reducer(收编 3-4 拷贝)、classify_chain_state、rule-3 ChainOnlyFact、check_collateral_identity + 共享钱包 ghost/operator-close 处理器。**split-not-delete**:projection.py(杀 P&L guard/_preserve_* 但留 F109/NullConditionId)、fill_tracker(mint 与仲裁混体)、diff_engine(report-only 谓词死,reservation-orphan writer 活)。

### 已到的 scout 原文(盘上,task output;compaction 后从这里取)

目录:`/private/tmp/claude-501/-Users-leofitz-zeus/0547f81f-5b6b-442f-977a-67ae75a4b915/tasks/`

- `a83b5f3ab43244e4f.output` — balances 读取全图:pUSD 30s 心跳(post-trade-capital);CTF per-token 按需(exit submit);CTF 全枚举不自动刷;exit_lifecycle.py:1945 直接 eth_call balanceOf(dust 检测)。判决:USDC 30s 确定性同步,CTF 持仓可读但不主动同步。
- `a126e4103662c3a6a.output` — daemon mesh/job 注册全图:5 进程分工 + 各 job cadence(collateral 30s、chain_sync_read 2min、harvester 1hr、fill bridge 1-2min、venue-heartbeat 5s)。
- `a01b125c29f7bb8d0.output` — chain_mirror_reconciler.py + chain_reconciliation.py 全函数枚举(分类器/minters/freshness 维护/`_materialize_chain_only_position_if_resolvable` 已随 T5 删)。
- `a35e2ac676cbedcad.output` — freshness/latency 仪表全图:三独立轴(forecast input→q 只测不门;venue ws-gap 30s/heartbeat 8s fail-closed;chain_seen_at 30min 重观察);无统一 chain-to-local lag 指标。
- `a45ae0eaa5c2a7726.output` — resolution/payout 读取判决:Zeus 不读 payoutNumerators;resolution 走 Gamma `closed`+`outcomePrices`;结算值来自 WU observations;redemption 只读(balanceOf + data-api redeemable)。
- `ae05f8d3616bfd2b0.output` — command_recovery 全 recovery/reconcile pass 枚举(~40 pass,表写清单)。
- `ada3a0f2bf5507d49.output` — exchange_reconcile.py 全函数枚举 + 漂移检测逻辑(LOCAL journal vs VENUE positions 比对面全图)。
- `a64fd3a49ca0ae3c3.output` — 漂移修复脚本 census:24 个(17 可重复 reconciler + 7 one-shot migration),主修 settlement_outcomes/position_current。
- `a0deebf74bf0435f9.output` — settlement/fill 写者清单:settlement_outcomes(db.py:7965 log_settlement + drain 脚本)、venue_trade_facts/venue_order_facts(venue_command_repo.py:3120/:3028 append-only)、trade_decisions 三写者(harvester:2729/synthesizer:105/db.py:10286+10369)、settlement_commands/ctf_conversion/wrap_unwrap 命令表、edli_live_profit_audit(live_profit_audit.py:238,含 pnl_usd/realized_edge 本地计算列)。

## Excision targets(census 定稿;T-系列沿 quarantine excision 惯例命名 LX-)

- **LX-T1 — payout ingester(唯一新建件,前置)**:ConditionalTokens payoutNumerators / redemption 事件读取(经既有 `_json_rpc_call`)+ 接入结算权威;与 WU 温度判定不一致 → DISPUTED lane(T2b 机器复用),不本地再算。census-chain-sources 判其为唯一缺失 ingester。
- **LX-T2 — collateral_ledger_snapshots 退位**(101,291 行最大纯镜像):余额读改 read-through(30s 刷新已在 post-trade-capital),快照表停写→退役;collateral_reservations(intent,KEEP K10)按 B3 拆分。
- **LX-T3 — position_current 经济学列 derive-on-read**:shares/cost_basis/entry_price(推导自 attributed fills)、realized_pnl_usd/exit_price/settlement_price(推导自 fills+payout)全部改读推导层;chain_* 镜像列降级 CACHE 或随 spine 死。phase 列按 B1 裁决。
- **LX-T4 — fill spine 收编**:venue_trade_facts 保留为**观察回执**(append-only、含 order 归属键 = KEEP);position_lots 及 cost-basis 重建机器死;fill_dedup 收编 3-4 拷贝为唯一 canonical+economic-identity reducer(KEEP spine)。
- **LX-T5 — 漂移机器随葬**(D1+D2 ≈24-27K):command_recovery C2+REAL 族、exchange_reconcile M5 sweep+materializers、restart-preflight、chain_reconciliation 写回 appenders、reconcile/local_truth+diff_engine report-only 谓词、fill_tracker mint 半边、~8 修复脚本、projection.py P&L guard/_preserve_* 卫兵。**先立 spine 证等价再杀(R2-c 教训:这些 pass 今天还在发火)。**
- **LX-T6 — EDLI 双账本移除**(D3 ≈2.5K,继承 R2-c 既判死期)。
- **LX-T7 — 结算本地权威收窄**(D4+D5):settlement_outcomes 按 MIXED 拆 —— 温度 VALUE 留 WORLD 真相(SettlementSemantics 法不动),winning_bin/payout 半边改读 LX-T1;drain/backfill/rebuild 结算脚本族死。
- **LX-T8 — residue sweep**:zero-grep 词表(realized_pnl_usd、chain_avg_price、chain_cost_basis_usd、cost_basis 镜像、settlement_price-as-payout…),registry/测试/AGENTS 法面同批。

## Ordering(定稿)

LX-0 立 spine + 归属证明:LX-T1 ingester + 全量历史 fills→venue_commands join replay 一次(分歧全落 DISPUTED/Review,不修账);tx_hash 别名 dedup 收口(LX-T4 前半)。
LX-1 derive-on-read 层 + cent-equivalence 门(旧列 vs 推导值,分歧即 STOP 升裁决 —— oracle 原则继承 §E2.2:旧账本是行为参照,不是基座)。
LX-2 读者迁移(逐消费族一包):bankroll/riskguard/monitor/exit/grading 读点 → 推导层。
LX-3 写者断供 + guard 反转(写经济学列才是错);LX-T2 快照停写。
LX-4 列/表退役 + LX-T5/T6 机器随葬 + LX-T7 结算收窄;测试与 registry 同 commit(§C3 删旧法)。
LX-5 LX-T8 sweep 到 zero-grep 门。

每包:provenance-audit 先行;TDD;对抗 verifier(money-path opus);测试 diff 零新增;registries 同 commit;PREPARE 级排队操作员,绝不自主 merge money-path。

## Consult 裁决(2026-07-13 已裁;原文入库 `docs/rebuild/consult_answers/local_ledger_excision_adjudication_2026-07-13.txt`)

**总判:原 LX-0..5 序 NO-GO(confidence 0.93);摘除目标本身 GO,条件 = fenced truth-epoch cutover + 持续源同步 + sync-owned collateral head + 保真本地 protocol facts。** 核心架构令(采纳为本计划宪法):

> 业务工作流永不创建或修复持久化的派生经济权威。外部真相只能以不可变 observation 或 sync-owned current head 落地。本地第一性事实留本地。派生视图可算可物化,但必须 disposable、可重建、永非权威。

### 逐 T 判决(全部采纳)

- **LX-T1 GATED**:payout observer 要 reorg-aware(block hash/number+log index 键)、UNKNOWN/UNRESOLVED/RESOLVED_ZERO/RESOLVED_NONZERO 四态、**缺数据 = PENDING 永不为 0**、无签名权、不在 grading 关键路径上(WU grade 与 chain realization 互不覆盖)。
- **LX-T2 WRONG-as-stated → 重构**:101k 行快照史死,但其 latest-state 职能不死 —— 换 `wallet_balance_head`(sync-owned 单行)+ 可选 observation 史;`available = head − reservations − venue-observed commitments − unsettled − margin`;同一 command 不得在 reservation 和 observed-order 两桶同时扣;**RiskGuard/preflight/bankroll/Kelly 同一 activation epoch 原子切换,先停写后切读 = 不安全**。共享钱包下本地 CAS 无法防人类 co-trader —— 保守 freshness + 安全边际 + submit-time 复验 + fail-closed 入场是无隔离下的最强姿态。
- **LX-T3 GATED**:economics 列停当权威 ✓,但 derive-on-read 的默认形 = **物化 read model(在 trades DB 内,非第四 DB)**,纯 SQL view 只有 production-copy benchmark 证 p99/锁行为后才许。门:单一确定性 reducer+版本、单一 economic-fill identity、fees/partial/cancel/reorg 全覆盖、发布 generation/coverage vector、cent-equivalent 历史 replay、所有 money reader 一起切、切后无 legacy 回退。
- **LX-T4 GATED**:venue_trade_facts 是 append-only observation log = **正确架构,留**;需 economic identity/alias graph(provider trade IDs × child fills × tx hash/log identity × order/command IDs)、原始 alias 保留(canonicalization 是 reducer 派生结果)、每 economic fill(含 fee)恰好贡献一次、归属只经久性 Zeus intent 证据、外部 fill 留 observation 但不入 Zeus equity、**同步器持续跑 + durable coverage watermark(一次性 replay 不够 —— replay 后 reader 切换前落的 fill 是 Attack A)**。
- **LX-T5 WRONG → 拆分**:命令态非链可知。KEEP:intent/idempotency/envelope/POST-before-ACK 未知态/cancel 协议/lease/restart preflight/external-close 证据/归属链/review。DELETE:派生经济学修复工作流、纯投影 lifecycle 列、"本地派生拷贝 vs 本地派生拷贝"的 reconcile 环。
- **LX-T6 GATED**:single-home 证明(全读写者 census + 不可变 ID 保引用 + 无跨 DB mutation)。
- **LX-T7 GATED**:结算拆两域记录 —— **world grade**(冻结证书 × WU 证据 × SettlementSemantics 版本 × graded receipt)与 **chain realization**(attributed fills × fees × payout observations);互不覆盖,分歧 = dispute;历史重建永不查今天的 WU/chain head。
- **LX-T8 WRONG → 语义门**:zero-grep 换成:无 runtime write 到已摘除经济学(migration 白名单外)、money 面无 runtime read、旧字段只许出现在 archival adapter/migration、drop-and-rebuild 集成测试、stale-binary capability fencing、grep+AST+SQL-inventory+runtime 访问检查带 allowlist。

### B1-B5 答案(采纳)

B1 phase 列可降 CACHE(确定性投影),但**转移证据不删**(历史 phase timing 无法从已剪枝事实重建);B2 chain-mirror 回执 KEEP,契约 = "源 S 在 T 对范围 X 返回 P"(带 block/coverage/supersession),业务逻辑永不编辑;B3 reservation 留本地权威,链余额归 sync-owned head,手递手状态机(pre-visibility reservation → venue-observed obligation → terminal release)带"单一承诺单桶扣除"不变量;B4 WU=world grade / chain payout=money realization,互不近似互不覆盖;B5 ReviewWorkItem KEEP,但 drop 属主表前先把 subject 迁到稳定 ID(command/fact-set/condition/dispute ID)。

### 排序攻击(7 个,全部实锤原序)

A fill 落在 replay 后 reader 切换前;B RiskGuard 补偿器双计/漏计(补偿器与 position 读必须同 epoch,最好消灭补偿器);C 快照先停写 → 陈旧余额喂 Kelly;D 结算落在 payout ingester 未齐窗口(→ PENDING 非零、WU grade 独立可用);E 滚动部署下旧 daemon 复写已摘除投影(→ truth-epoch + capability fence,旧 build 拿不到 lease);F /positions 漏 token = 幻零仓(→ durable token registry 从 Zeus commands/fills/topology/transfer obs 构建,/positions 只做 discovery,**absence 永不证零**);G reservation 与 observed-obligation 双扣(→ identity+watermark 手递手)。

### 修订执行序(LX-0R..5R,取代原 LX-0..5)

- **LX-0R 契约+激活控制**:冻结 fact schema/canonical identity/单位/reducer 版本/结算双域名;trades-DB truth epoch(LEGACY→PREPARE→ACTIVE_NEW)+ 进程 capability 广告 + 入场/money-read 要求 active epoch。**(既有资产:T5 migration 的 schema_epoch×3 + startup mixed-epoch refusal 是同型先例,扩展非新造。)** 不做三 DB 分布式事务。
- **LX-1R 源 spine 上线**:持续 fill/order 同步、payout observer+backfill、balance head、token registry+精确 CTF 读、coverage/finality 元数据、alias graph、supersession facts。**激活前就跑 —— 采集生产事实非影子决策,不违 no-shadow 公理。**
- **LX-2R reducer/read-model 建成+证明**:回填四类读契约(position/collateral/phase/settlement);time-boxed replay + cent-equivalence 门;新 binary 全网部署但旧 epoch 仍权威(不按消费族分批启用)。
- **LX-3R 单次 fenced activation**:PREPARE(入场 fail closed,exit 按充分 venue 真相继续)→ 在飞 entry 清干净分类 → 全 daemon lease 广告新 epoch → 源 freshness+coverage 达标 → 发布 ACTIVE_NEW 一次。此界后:所有 money reader 用新契约、业务停写旧经济学、旧 build 失去 command admission。**永不双写竞争投影。**
- **LX-4R 隔离旧 schema + 只删病灶**:旧经济学列物理保留但不读不写(有界回滚窗);只删混合模块的 derived-value 修复半边;restart recovery/unknown-side-effect/cancel 协议/归属/lease/review/external-close 全留。
- **LX-5R 破坏性 drop**:replay+cent-equivalence 过、源 freshness 稳、restart+stale-daemon 测试过、payout/bankroll 断供下 exit 仍走、结算故障注入证双域独立、read model 从零重建过 —— 全绿后才 drop。**drop 后回滚 = 从事实 forward rebuild,永不恢复归档账本当权威。**

Exit 充分真相路(激活后不依赖 Kelly/bankroll/payout ingester):`safe_exit_qty = max(0, min(zeus_attributed_unclosed_shares, wallet_token_balance − observed_open_sell_remainder))`;证据陈旧/歧义 → ReviewWorkItem,不做不安全卖出。

### KEEP-spine 完备性补遗(consult 攻击出的漏项,census 未列全)

同步控制态(durable cursors/watermarks/pagination coverage/finality/supersession);token-discovery 史(五源:intent/topology/attributed fills/transfer logs/direct balance obs);reservation 手递手身份关系;归属图+歧义证据(foreign/ambiguous 留 observation 不丢);冻结 walk-forward 输入全清单(证书×WU 证据 hash×semantics 版本×单位/舍入版本×fills×fees×payouts×reducer 版本);read-model lineage(generation×coverage vector×input fingerprint —— RiskGuard 组合不同 watermark 的仓位必须可检测);units/fees/topology 映射;历史 codec(归档解码器里的旧字段名非残余权威);reorg/供应商更正 supersession;降级态显式化(**timeout/空响应/缺页/stale 永不编码为 0**)。

### Read-model 诚实不变量(何以不是平行账本)

`ReadModel[g] = Reducer_v(ImmutableFacts through coverage vector C[g])`,且:外部事实只有同步器写;read model 只有 reducer 写;删全表不失真相;确定性重建 cent-equivalent;全 lineage;无反馈权威(结果看着不对不能改源事实);无手工经济修复(更正 = superseding fact + 重算);无 stale 回退;reorg-aware 重算;定期离线全量 replay 审计;**portfolio 发布按完整 generation,不按 per-row latest**。

### Bankroll/Kelly 三量分立(激活后)

钱包可花 collateral(head−承诺,管新单硬可行性)≠ **Zeus strategy equity**(显式配额策略 × attributed fills/fees/exposure/payouts,Kelly 的正确基数)≠ 钱包总 equity(含操作员资产,只做遥测/全局安全限)。/positions 的 currentValue/avgPrice/cashPnl 永不做 Zeus 权威;归属查询失败不默认全归 Zeus;入场 sizing 在 Zeus-equity 不完整时 fail closed。

### Round-2 delta 裁决(2026-07-13;原文 `docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt`;confidence 0.97)

11 旁路写者 + edli_live_profit_audit 不翻任何 LX-T 判决,但加严五个门:

- **激活单元修正(BLOCKER)**:切换单元 = **禁写列本身的所有权**,不是 projection funnel。LX-0R 增列:禁写列集中定义 + 生成式写者/读者 manifest(静态+runtime);LX-3R 加 **DB 级 BEFORE INSERT/UPDATE 列防火墙**(defense-in-depth,不是迁移机制 —— 正常恢复写被拒 = cutover 失败,保持 entries 关闭)。
- **混合事务先拆(BLOCKER)**:多处 KEEP 证据写与病灶经济学写同 savepoint(command_recovery REVIEW_REQUIRED+chain 经济学;settlement grading+EDLI writeback)—— guard 一响会回滚 KEEP 半边。**防火墙上膛前必须拆分事务。**
- **selector 同迁(BLOCKER)**:pass 停写还不够 —— 候选选择/谓词仍读冻结 shares/phase 就还耦合旧账本(exchange_reconcile ghost-sell selector 是实锤)。每站点读写两侧都迁。
- **六站点特判**:duplicate_consolidator → 改 **identity-supersession facts**(POSITION_IDENTITY_SUPERSEDED),在 read-model 回填前跑,否则 reducer 双计;exchange_reconcile:1418 → 保命令重建,**禁用 order price 冒充 fill_price 记 P&L**(现行是错账),exact trade facts 到前 P&L=UNKNOWN;exit_lifecycle:4680 → exit 防重卖必须直读 command/trade facts(不等 read-model 追上);edli_position_bridge:1003 → **LX-3R 前**(非时刻)换成 canonical fact bridge,否则新 EDLI fill 搁浅;exchange_reconcile:1813 → 外部平仓留 observation+disposition,归零由 reducer 从精确 token 真相推导;command_recovery 六站 → 一个迁移包,event payload 必须含 reducer 所需全部源证据(从 position_current 抄的 after-state 不算)。
- **edli_live_profit_audit 裁决**:**逻辑摘除在 LX-T3**(pnl_usd/realized_edge/settlement_outcome/promotion_eligible 停写停权威;writeback 从 grading batch 摘出),**物理删除留 R7**(冻结只读+归档 adapter)。先 rehome:position/command→decision_certificate_hash 永久归属记录(**在决策时写**,替换现行 settlement 时 (condition,direction)→latest-row 推断 —— 14 对多证书歧义已实锤,歧义标 UNATTRIBUTABLE 不选 latest);realized_edge 概念保留,改 append-only versioned `execution_quality_receipt`;pnl_usd 更名 `world_grade_pnl_usd` 进 `settlement_learning_receipt`(它是 hold-to-settlement world-grade 标签,不是钱包实际 P&L —— 现名撒谎)。walk-forward 验收门:target receipts 重建历史语料,证书身份/skill category/world-grade P&L 全等,歧义显式化。
- **LX-2R 增前置**:world-DB learning 迁移完成于 LX-2R(不塞进 trade-DB 原子切换 —— 三 DB 无共享事务);dual-capable fact-first 代码全网部署(LEGACY 分支保旧行为,ACTIVE_NEW 分支只 append facts —— 单权威 per epoch,不违 no-shadow);激活后立即重跑捕获的 unresolved-command 集(接住跨界 venue 副作用)。
- **新增 KEEP 义务**:mutable learning UPSERTs(live_profit_audit insert_record、settlement_attribution persist_grade)→ append-only versioned receipts with supersession(rerun 不得覆写历史语料)。

## Operator forks(裁决后仅剩的操作员决定)

1. **Zeus 资本配额**:共享钱包下 Zeus-specific Kelly 数学上未定义,除非 ①显式 Zeus capital-allocation policy(一个数/一条规则,操作员定)或 ②钱包隔离。Consult 判两者必居其一 —— 你选哪个?(现状 bankroll 读钱包聚合值 = 把操作员资产喂进 Zeus Kelly,是 HIGH 级错账。)
2. **修订计划批准**:LX-0R..5R 取代原序 + §Excision targets 按逐 T 判决执行 —— 批准即开 LX-0R 契约包(纯契约/schema/epoch 定义,PREPARE 级)。
3. **回滚窗长度**(LX-4R 旧列隔离期):建议 ≥7 个交易日再 LX-5R drop。
