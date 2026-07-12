# Local-Ledger Excision — 本地记账摘除计划(2026-07-12)

**Operator law(逐字):** 消除所有的本地记账。这些东西和 quarantine 一样是疾病。不允许本地记账,不允许本地计算链上已经有的内容;需要的信息直接也仅从链上同步。不符合第一性原理的全部执行。

**Status: PLANNING — census 4 路在跑;consult 对撞未发;不动代码。**
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
- **census_ledgers.md ✅** — ~23 面、三 DB 列级分类。爆炸半径 Top-3:①position_current 经济学+chain_* 镜像列(1002 行,单 funnel projection.py:562 但 ~10 调用者 + chain-reconcile setters);②collateral_ledger_snapshots(**101,291 行纯链余额镜像**,authority_tier='CHAIN',还在增长 —— 最大的平行账本);③venue_trade_facts→position_lots fill spine(636+205,本地重建 cost basis 喂 ①)。关键细分:settlement_outcomes 是 MIXED 非平行账本 —— winning_bin 链可推导、settlement_value(温度)是链不知道的 WORLD 真相;TRADE/WORLD 的 settlements 表是 0 行死壳。
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

## Consult 裁决(census 齐,今日发)

送审包:本文件 + 四份 census 全文。裁决重点:Q1-Q6、BORDERLINE B1-B5、LX 排序攻击(找会让 live money path 摸黑的序)、KEEP spine 的完备性(漏了什么会在删除后才发现)、data-api /positions 无 order 键在 CTF token 枚举上的残余依赖是否构成隐性权威。
