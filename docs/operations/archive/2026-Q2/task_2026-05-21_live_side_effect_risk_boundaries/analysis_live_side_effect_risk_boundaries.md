# Live Side-Effect / Risk Boundary Analysis

Captured: 2026-05-21

Source: operator-provided current-main code audit in chat. This file is the source analysis for `task.md`; before starting any P0/P1/P2/P3 repair, reread the corresponding section here.

## 0. 当前代码级结论

Verdict: NOT SAFE FOR NORMAL LIVE；PAPER / REPLAY / SHADOW 可以继续。

这次我不再用 PR 描述作为证明，而是按当前 main 的代码对象审计。结论比上一轮更精细：上一轮我担心的两个点已经被代码修掉了，但现在暴露出更深的三类 live-money blocker：

1. 订单/赎回 side-effect 后的持久化失败仍可能被“日志化后继续返回成功/待成交”吞掉。 这是最危险的：venue side effect 已发生，但 durable truth 没有落库，函数仍返回 pending/filled。
2. redeem 的 persist-before-side-effect 合同只在自开连接时成立；调用方传入连接时，REDEEM_SUBMITTED 可在未 commit 的事务中就触发 adapter side effect。
3. shoulder / cluster risk cap 目前像“存在的对象”，但不像“接入的风险约束”：ledger writer 在生产路径中看不到调用，cap 在 sizing 前用 proposed_notional=0.0 检查，异常还 fail-open。

所以现在不是“系统没有修复”。相反，很多核心语义已经明显进步：negRisk tradeability object、snapshot executable gate、no-trade schema live fail-closed、money-path replay 都已经落地。但顶级架构审查的判断是：当前 main 仍存在会导致真实钱路径状态分裂的 P0 缺口。

## 1. 先确认已经修好的关键点

### 1.1 negRisk scanner → snapshot executable seam 已经比上一轮强很多

上一轮我指出 scanner 和 ExecutableMarketSnapshotV2.assert_snapshot_executable() 可能对 closed/active 语义不一致。当前 main 已经引入 ExecutableTradeabilityStatus，把 Gamma parent/child active/closed 与 accepting_orders、CLOB archived、CLOB orderbook enable 分离；snapshot hash 也把 tradeability_status 纳入 identity。assert_snapshot_executable() 现在检查的是 snapshot.tradeability_status.executable_allowed，不再直接用 raw closed 阻断。

scanner 侧也明确把 child active=False 视为 negRisk routing label，而不是 executable authority；child tradable gate 是 acceptingOrders=True && enableOrderBook=True，随后 snapshot capture 再取 CLOB market/orderbook 并构建同一个 normalized tradeability object。

判断：这个 seam 从 P0 降级为“需要保持集成测试”的 P1。

### 1.2 no_trade_events 的 live schema 兼容降级问题已经被修补

上一轮我担心 no-trade reason 在旧 schema 下降级为 UNCATEGORIZED 后被 live learning 当成真实分类。当前 main 已经加了 NoTradeEventsSchemaCompatibilityError 和 assert_no_trade_events_schema_current_for_live()；默认 allow_schema_compatibility_downgrade=False，live 写入会 fail-closed，兼容降级必须显式打开，并会写 schema_compatibility='degraded'。

schema 侧也已经把 schema_compatibility 作为表字段，并把 schema_version CHECK 扩展到 23。

判断：这个问题已基本修复；剩余要求是 reporting/learning 必须排除 schema_compatibility='degraded'。

### 1.3 当前 main 已经有 integrated money-path replay，但覆盖还不够

现在代码库中已经有 tests/test_money_path_lifecycle_replay.py，覆盖 decision/no-trade、command、order fact、trade fact、position lot、settlement command、submit_redeem、reconcile_pending_redeems 的一条集成路径。

但是这个 replay 主要直接调用 insert_command / append_event / append_order_fact / append_trade_fact 等 repository seam，而不是实际驱动 _live_order() / execute_exit_order() / real adapter side-effect boundary。因此它证明“状态表之间能收敛”，但没有证明 side-effect 后 ACK 持久化失败时 runtime 会 fail closed。这正好是我这轮发现的 P0-1。

## 2. P0 Findings

### P0-1 — venue side-effect 后 ACK 持久化失败被吞掉，函数仍返回 pending/filled

Severity: P0 live-money blocker
Current code: `src/execution/executor.py::_live_order`, `execute_exit_order`
Money path segment: execution → order lifecycle → position state

代码证据：

`_live_order()` 的 persist-before-side-effect 前半段做得是对的：它先 `insert_command()`，再 `append_event(... SUBMIT_REQUESTED ...)`，再 `_reserve_collateral_for_buy()`，然后 `conn.commit()`，之后才进入 `client.place_limit_order(...)`。

问题出在 SDK 返回之后。代码进入 ACK phase 后，尝试：

* `append_event(... SUBMIT_ACKED ...)`
* `append_order_fact(...)`
* 可能 `append_trade_fact(...)`
* 可能 `append_event(... FILL_CONFIRMED / PARTIAL_FILL_OBSERVED ...)`
* `conn.commit()`

但这一整个 ACK 持久化块被一个大 try/except 包住；如果里面任何一步失败，代码只 `logger.error(...)`，然后继续构造 `OrderResult(status="filled" 或 "pending", command_state="FILLED/PARTIAL/ACKED")` 返回。

`execute_exit_order()` 也有同类结构：exit ACK/order fact 写入失败后记录错误，但仍继续返回 posted/pending 结果。

为什么这是错的：

这是典型 live-money 状态分裂：

* CLOB side effect 已经发生；
* local DB 没有 ACK / order fact / trade fact；
* 函数返回值却告诉上游“pending/filled”；
* 上游可能 materialize position、写 decision_events、刷新 portfolio；
* recovery 只看到 SUBMIT_REQUESTED 或 SUBMITTING，而不是真实 ACK/fill。

这比“直接 crash”更危险，因为 crash 至少会进入 recovery；这里是带着错误状态继续运行。

失败场景：

1. `client.place_limit_order()` 成功返回 orderID。
2. `append_order_fact()` 因 DB lock、schema drift、constraint、JSON serialization 失败。
3. `_live_order()` 记录 error，但返回 `OrderResult(status="pending", command_state="ACKED")`。
4. 上游记录 entry submitted / position pending。
5. 下次 recovery 找不到 canonical order fact；exposure 可能被低估、重复提交、或卡在 UNKNOWN。

Required fix：

ACK persistence failure after side effect 必须进入 first-class state，不能只 log：

* 如果 SDK returned order id，但 ACK persistence failed：立即写一个最小 `SUBMIT_ACK_PERSISTENCE_FAILED_REVIEW_REQUIRED` 事件，或者至少把 command transition 到 REVIEW_REQUIRED / SUBMIT_UNKNOWN_SIDE_EFFECT。
* 如果连这个最小事件都写不了：函数必须返回 unknown_side_effect，不能返回 pending/filled。
* `OrderResult.command_state` 必须与 durable DB row 一致。不能用内存推断的 ACK/FILLED 覆盖 DB truth。

Required tests：

1. monkeypatch `append_order_fact` 抛错，在 `client.place_limit_order` 成功后触发。
2. assert `_live_order()` 返回 `unknown_side_effect` 或 `review_required`，不是 pending/filled。
3. assert `venue_commands.state` 不是 ACKED/FILLED，除非 ACK facts durable。
4. 同样覆盖 exit path。

### P0-2 — redeem 的 persist-before-side-effect 合同只在 own_conn=True 时成立

Severity: P0 live-money blocker
Current code: `src/execution/settlement_commands.py::submit_redeem`
Money path segment: settlement → redeem → chain side effect

代码证据：

函数 docstring 写得很明确：“durable REDEEM_SUBMITTED event is committed before adapter contact”。代码确实先 `_transition(... REDEEM_SUBMITTED ...)`，但只有 `own_conn` 时才 `conn.commit()`。如果调用方传入 `conn`，它不会 commit，而是继续进入 adapter redeem 逻辑。

后续代码会解析 winning_index_set、查 snapshot/Gamma negRisk authority，然后调用 `adapter.redeem(...)`。

为什么这是错的：

这违反了 live-money 最核心的 side-effect rule：

side effect 之前，intent/submitted state 必须 durable。

如果 conn 是外部事务连接，REDEEM_SUBMITTED 只是内存/未提交事务里的状态。adapter 可能已经广播 Safe / NegRisk redeem tx，但 crash 或外层 rollback 会让本地 ledger 回到 REDEEM_INTENT_CREATED 或 REDEEM_RETRYING。

失败场景：

1. scheduler 打开 trade connection，传入 `submit_redeem(conn=conn)`。
2. `submit_redeem()` 写 REDEEM_SUBMITTED，但不 commit。
3. adapter 广播 tx 或返回 tx hash。
4. 进程在 `_transition(... REDEEM_TX_HASHED ...)` 前 crash。
5. DB 没有 durable submitted/tx hash。
6. 下轮 submitter 可能重复 redeem 或误判 operator state。

Required fix：

`submit_redeem()` 需要把 side-effect boundary 显式化：

* 方案 A：无论 own_conn 与否，REDEEM_SUBMITTED transition 后都 commit。这个函数已经是 live side-effect seam，必须拥有自己的 transaction boundary。
* 方案 B：如果调用方传入 active transaction，不允许 side effect，直接 raise SettlementCommandTransactionBoundaryError，要求调用方改用该函数自开连接。
* 方案 C：引入 `requires_autocommit_before_side_effect(conn)` guard，检测 `conn.in_transaction`，live 模式下 fail closed。

Required tests：

1. 外部 conn 传入，设置 fake adapter，在 `adapter.redeem` 内部读取第二个连接验证 REDEEM_SUBMITTED 已经可见。
2. 如果不可见，test fail。
3. crash simulation：REDEEM_SUBMITTED 后 adapter 抛异常，assert DB 不回滚到旧 state。

### P0-3 — shoulder cluster cap 是“存在但未形成 live risk authority”的对象

Severity: P0 for shoulder live enablement；P1 if shoulder strategy remains shadow-only
Current code: `src/engine/evaluator.py`, `src/strategy/shoulder_cluster_cap.py`, `src/state/shoulder_exposure_ledger.py`
Money path segment: sizing / risk / correlated exposure

代码证据：

`shoulder_cluster_cap.py` 的设计是两道门：

1. 同 cluster + same side 已经有不同 city exposure，则拒绝；
2. existing_total + proposed_notional > `SHOULDER_CLUSTER_HARD_CAP_USD` 则拒绝。

ledger 表和 writer 都存在，`write_shoulder_exposure_entry()` 会 append exposure row。 schema 也存在，当前 schema version 已到 23。

但当前 runtime 搜索 `write_shoulder_exposure_entry` 只返回 ledger module、source rationale、测试文件，没有生产调用点。

更严重的是 evaluator 在 sizing 前调用 cap 时传的是 `proposed_notional=0.0`，注释写着 “pre-sizing check — notional TBD”；异常还被捕获为 fail-open。

为什么这是错的：

这个 risk cap 在当前形态下不是真正的 risk cap：

* 如果 ledger 没有生产写入，Gate 1 永远看不到历史 exposure。
* `proposed_notional=0.0` 让 Gate 2 永远无法阻止“这笔新单使 cluster 超过 hard cap”的情况。
* `sqlite3.OperationalError / ImportError / AttributeError` fail-open 让 schema 缺失、表缺失、导入缺失时直接放行。这对风险上限是不合格的。

失败场景：

1. heat dome cluster 下已经有 $1900 same-side shoulder exposure。
2. 新候选 sizing 后会下 $500。
3. evaluator 在 sizing 前以 0.0 调 cap：1900 + 0 <= 2000，通过。
4. 后续下单后也没有 production writer 写 ledger。
5. 实际 cluster exposure 到 $2400，但系统认为 cap 仍可用。

Required fix：

* 把 shoulder cap 移到 sizing 之后，以真实 `proposed_notional=size` 检查。
* 对 live 模式改成 fail-closed：ledger/table/schema/read error 必须拒绝 shoulder live entry。
* 在 accepted decision 或 command persisted 之后写 `write_shoulder_exposure_entry()`，并把 decision_event_id/command_id/snapshot_id 纳入 ledger。
* ledger rows 必须有 lifecycle semantics：pending order、partial fill、cancel、void、settled 是否计入 exposure，需要 reducer，不应只 append raw notional。

Required tests：

1. existing exposure $1900 + proposed $500 → reject。
2. same cluster other city same-side → reject。
3. same city accumulation below cap → allow；above cap → reject。
4. missing ledger table in live → reject；paper/shadow 可 warn。
5. accepted shoulder decision writes ledger row before submit or at command-persist boundary。
6. cancel/void/redeem 后 exposure reducer correctly removes or changes state。

## 3. P1 Findings

### P1-1 — business-plane health still allows “zero progress but healthy”

Current code: `src/control/live_health.py`

`_business_plane_surface()` 要求 `status_summary.cycle` 存在、cycle 不 failed/skipped、并且有 candidates 字段。但它把 `candidate_evaluated` 设成 `candidates > 0 or "candidates" in cycle`。也就是说，只要字段存在，哪怕 `candidates=0`，business surface 仍然 `ok=True`。`final_intent_built`、`submit_attempted`、`venue_ack_observed` 可以全是 false，但不影响 health。

`compute_composite_live_health()` 只在 `business_surface["ok"]` 为 false 时 degrade，因此这种“process alive + cycle ran + zero business progress”的状态会被评为 healthy。

Why it matters：

对于 live trading system，health 不是“daemon 没死”。健康至少要分出：

* no market found 是正常无机会？
* scanner/source stale？
* evaluator all rejected due true economics？
* structural stall？
* submit path blocked？
* reconcile/redeem stuck？

当前 health surface 没有把 “0 candidate / 0 intent / 0 submit / 0 reconcile progress” 当作可疑状态。

Required fix：

引入 mode-aware business liveness policy：

* market_discovery: 最近 N 分钟必须有 scanner attempt + explicit no-market reason。
* opening_hunt/imminent/day0: 如果连续 K cycles candidates=0，必须有 source freshness proof；否则 degraded。
* 如果 candidates>0 但 final_intents=0，必须记录 top no-trade reasons。
* 如果 final_intents>0 但 submit_attempts=0，必须 degrade。
* 如果 submit_attempts>0 但 venue_acks=0 且无 deterministic rejection，必须 degrade。

### P1-2 — same-cycle cluster exposure projection 是死变量 / 错 key

Current code: `src/engine/evaluator.py`

Evaluator 初始化了 `projected_cluster_exposure_usd: dict[str, float] = defaultdict(float)`，但 cluster throttle 用的是 `cluster_exposure_for_bankroll(portfolio, city.cluster, sizing_bankroll)`，没有把本轮前面已经 accepted 的 candidate 加进去。

最后成功 decision 后，代码更新的是 `projected_cluster_exposure_usd[city.name] += size`，key 用 city name，不是 city.cluster，而且搜索结果显示这个变量只有 evaluator 里出现。

Why it matters：

同一个 cycle 内多个城市、同一个 weather cluster 的候选会同时通过风险 throttle，因为 throttle 只看 portfolio 旧状态，不看本 cycle 已选 candidate。这个问题和 shoulder cluster cap 的失效叠加，会让 correlated exposure 超出预期。

Required fix：

* `projected_cluster_exposure_usd` key 必须是 `city.cluster`。
* `current_cluster_exp` 应该是 `cluster_exposure_for_bankroll(...) + projected_cluster_exposure_usd[city.cluster] / bankroll`。
* 成功 accepted 后更新 `projected_cluster_exposure_usd[city.cluster] += size`。
* 增加 same-cycle 2-city same-cluster fixture，第二个 candidate 必须被 throttle 或 reject。

### P1-3 — money-path replay 存在，但绕过了真实 executor side-effect seam

当前 integrated replay 是非常好的方向，但它直接 append repository facts，而不是让 `_live_order()` 走真实 “persist → submit → ACK persist” 路径。

这意味着它没有覆盖我上面 P0-1 发现的 failure class：SDK 成功返回后 ACK/order/trade fact 持久化失败。

Required fix：

把 replay 拆成两层：

1. State convergence replay：保留当前测试，证明 reducer/state tables 能收敛。
2. Runtime boundary replay：fake `PolymarketClient.place_limit_order` 成功返回，同时 monkeypatch `append_order_fact`/`insert_submission_envelope`/`conn.commit` 在不同点失败，验证 `_live_order()` 不会返回 pending/filled。

### P1-4 — FinalExecutionIntent seam 强，但 legacy execute_intent() 仍是风险口

`execute_final_intent()` 明确消费 `FinalExecutionIntent`，并验证 snapshot/cost identity；这是正确方向。

但 `execute_intent()` 仍然存在，直接从 `ExecutionIntent` 计算 shares 并进入 `_live_order()`。 `_live_order()` 里确实会检查 corrected execution identity，但 legacy intent 会被 corrected_execution_identity 组件标成 legacy；当前实现对 legacy 是否 allowed 要看 `_capability_component` 默认值，这需要强测试保障。

Required fix：

* Live entry 只允许 `execute_final_intent()`。
* `execute_intent()` 在 live mode 必须 hard fail，除非 explicit `ZEUS_ALLOW_LEGACY_EXECUTION_INTENT=1` 且 paper-only。
* 所有 live callers 迁移到 `FinalExecutionIntent`。

## 4. P2/P3 Findings

### P2-1 — get_trade_connection_with_world() ATTACH 失败是 non-fatal

`get_trade_connection_with_world()` 在 ATTACH world 或 forecasts 失败时只是 warning，然后返回 connection。

这在一些 read-only/diagnostic 情况下可以接受，但 live-money authority path 不应拿“可能没有 attach 的连接”继续运行。

改进方向：

* 拆成 `get_trade_connection_with_world_optional()` 和 `get_trade_connection_with_world_required()`
* live execution/redeem/reconcile 使用 required 版本；
* required 版本 attach 失败直接 raise。

### P2-2 — health / replay / tests 已经开始覆盖 money path，但 release gate 仍应按“故障注入”而不是“happy path replay”定义

当前 money-path replay 是 happy/convergence path。顶级架构要求不是“有一条路径能跑通”，而是“关键边界失败时状态不会撒谎”。

需要补的 fault injection matrix：

| Boundary | Failure injection | Expected behavior |
| --- | --- | --- |
| before command insert | DB fail | no venue side effect |
| after SUBMIT_REQUESTED commit, before SDK | client init fail | terminal rejected, no unknown |
| after SDK success, before ACK event | append_event fail | UNKNOWN/REVIEW_REQUIRED, not pending |
| after ACK event, before order fact | append_order_fact fail | REVIEW_REQUIRED |
| after order fact, before trade fact | append_trade_fact fail | partial/filled not returned unless durable |
| redeem submitted uncommitted | adapter called | forbidden |
| receipt JSON serialization | AttributeDict/HexBytes | event durable or review-required |
| settlement confirm event fail | local state cannot become confirmed |

## 5. Money-path Integrity Table, current code only

| Segment | Code-level verdict | Reason |
| --- | --- | --- |
| contract semantics | PASS/PARTIAL | ExecutableTradeabilityStatus is strong; legacy snapshot fallback still requires guard |
| source truth | PARTIAL | scanner source contract checks exist, but CLOB unreachable fallback policy still needs live-mode separation |
| forecast signal | PARTIAL | not deeply re-audited in this pass; earlier repaired, but not current proof |
| calibration | PARTIAL | evaluator rejects no Platt/raw probability entries; good, but calibration report trust not fully checked |
| market prior | PARTIAL | p_market comes from CLOB quote loop; display/substitution still needs live microstructure proof |
| executable edge | PARTIAL | FinalExecutionIntent seam strong; legacy execute_intent still risky |
| sizing | PARTIAL/FAIL for shoulder | EffectiveKellyContext exists; cluster/shoulder cap is broken/inert |
| execution | FAIL for live | ACK persistence failure after venue side effect can be swallowed |
| monitoring | PARTIAL | business-plane surface exists but zero-progress can be healthy |
| settlement | FAIL for live | submit_redeem external-conn path violates pre-side-effect commit |
| learning | PARTIAL | no_trade schema live guard fixed; decision/no-trade completeness depends on runtime wiring |

## 6. Repair Order

1. 修 `_live_order / execute_exit_order` ACK persistence failure
2. 修 `submit_redeem` 外部连接事务边界
3. 把 shoulder cluster cap 变成真实 live risk authority
4. 修 same-cycle cluster exposure projection
5. 重定义 live_health 的 business-plane PASS 条件
6. 关闭 live legacy `execute_intent()` 入口

## 7. Release Gate

当前 main 不应 normal live。允许范围：

| Gate | Status |
| --- | --- |
| Paper / replay | YES |
| Shadow live-read-only | YES |
| Tiny live | NO，直到 P0-1/P0-2/P0-3 修完 |
| Normal live | NO |
| Report trust | PARTIAL；需要排除 degraded no_trade rows 和 unknown legacy provenance |

Before tiny live:

1. ACK persistence fault injection green。
2. Redeem external-conn pre-side-effect commit proof green。
3. Shoulder live strategy disabled or cap fully wired。
4. Same-cycle cluster exposure test green。
5. Business-plane health zero-progress degraded。
6. Current schema v23 verified on live DB。
7. Loaded SHA / current main attestation fresh。
8. No unresolved UNKNOWN_SIDE_EFFECT / REVIEW_REQUIRED rows except explicitly whitelisted。

## 8. 最重要的架构判断

Zeus 当前最大的问题已经不再是“没有类型”“没有表”“没有测试”“没有状态机”。相反，现在的问题是更高级的：

对象已经被建出来了，但一些对象还没有成为唯一 runtime authority；一些测试证明了状态能收敛，但没有证明 side-effect boundary 失败时系统不会撒谎。

顶级架构师下一步不应该继续加更多 docs、更多 schema、更多 antibody 名称；应该集中做三件事：

1. 把每个 live side-effect boundary 改成不可撒谎：失败就 UNKNOWN/REVIEW_REQUIRED，不能 log 后继续。
2. 把每个 risk object 接到真实 money path：特别是 shoulder ledger / cluster exposure / family exposure。
3. 把 release proof 从 happy path replay 升级成 fault-injection replay。

修完这三类，Zeus 才从“很多局部修复的 live trading system”进入“可以被审计地承受真实交易故障”的阶段。
