# Live Authority / Shadow / Risk Follow-up Analysis

Created: 2026-05-21
Last reused or audited: 2026-05-21
Authority basis: User-provided current-main secondary audit, baseline `origin/main` `a4707d1beb`.

This file preserves the operator-provided analysis for this task packet. Before entering any finding implementation, reread the corresponding section here and update `task.md` with the current status and evidence.

## 0. 当前二次审计结论

**Verdict: SAFE ONLY AFTER P0/P1 FIXES；不建议 normal live。**

这轮我把之前的 PR 地图只当成“审计导航”，没有把 PR 描述当证据。所有结论都来自当前 `main` 代码。当前最新可见主线已经推进到 `a4707d1...` 附近，且你说上一轮提出的问题已实现；我重点验证了上一轮的 P0/P1，并继续沿 live-money path 找新的断裂点。

核心判断：

1. **上一轮最关键的 side-effect P0 已经明显修复。** ACK 持久化失败后不再返回 `pending/filled`，而是进入 post-submit persistence failure 处理；redeem 在 adapter contact 前也无条件 commit `REDEEM_SUBMITTED`。
2. **新的主要风险不再是“没有对象”，而是“新对象与旧兼容入口/影子策略/风险度量之间的边界仍有错位”。**
3. **normal live 仍不应开放，除非下面列出的 live entry legacy override、post-submit rejected persistence、shadow candidate provenance、shoulder/correlation risk authority 等问题关闭。**

## 1. 已验证：上一轮关键修复确实落入当前 runtime path

### 1.1 `_live_order` / `execute_exit_order` ACK persistence failure 已修复

当前 `executor.py` 新增 `_mark_post_submit_persistence_failure()`：在 SDK 已可能发生 side effect 后，如果 ACK/order/trade fact 持久化失败，它会先 `conn.rollback()`，再写最小 `REVIEW_REQUIRED` 事件，payload 标记 `side_effect_boundary_crossed=True`、`sdk_submit_returned_order_id`、`requires_recovery=True`，最后读取 durable command state。

`_live_order()` 的 ACK 持久化失败分支现在会调用该函数，并返回 `OrderResult(status="unknown_side_effect", command_state=durable_state)`，不再继续返回正常 pending/filled。

`execute_exit_order()` 的 SELL/exit ACK 失败路径也同样调用 `_mark_post_submit_persistence_failure()`，返回 `unknown_side_effect`。

**结论：上一轮 P0-1 的主要断裂已经修掉。**

### 1.2 `submit_redeem()` pre-side-effect durability 已修复

当前 `submit_redeem()` 在写入 `REDEEM_SUBMITTED` 后，无论是否 own connection，都会 `conn.commit()`，并且注释明确说明这是 on-chain side-effect boundary；adapter contact 发生在 commit 之后。

此外，`submit_redeem()` 自开连接时现在使用 `get_trade_connection_with_world_required(write_class="live")`，并且传入外部 conn 时，如果 world ATTACH 失败，会 raise `SettlementCommandStateError`，而不是 warning 后继续。

DB helper 也已经拆成 optional / required 两种连接：`get_trade_connection_with_world_required()` 会强制 attach `world` 和 `forecasts`，失败则 close 并 raise。

**结论：上一轮 P0-2 已经修到正确方向。**

### 1.3 business-plane health 已经增强

`live_health.py` 现在不再把 `candidates=0` 的 cycle 自动当作健康。它要求 zero-candidate 有 `no_market_reason / scanner_no_market_reason / source_freshness_proof / scanner_attempted` 等证明；如果有 candidates 但没有 final intents，要求 no-trade reason proof；如果 final intents > 0 但 submit attempts = 0，degrade；submit attempts > 0 但无 ack 且无 deterministic rejection，也 degrade。

**结论：上一轮 P1-1 的“process green / business dead”问题已有实质性修复。**

### 1.4 shoulder cap 已从 pre-sizing 0.0 检查改为 post-sizing notional 检查

Evaluator 现在在 sizing 后调用 `_shoulder_cluster_cap_rejection(... proposed_notional=float(size))`，不再传 `0.0`。

`_shoulder_cluster_cap_rejection()` 现在会构造 cluster context，并调用 `check_shoulder_cluster_cap()`；如果 ledger/table/import 不可用，会返回 rejection reason，而不是静默 fail-open。

**结论：上一轮 P0-3 的 sizing 前 0 notional 检查已经修掉，但新的 ledger timing 风险还存在，见下文。**

## 2. 新 P0/P1 Findings

### Finding 1 — legacy `ExecutionIntent` live escape hatch 仍可通过环境变量进入 `_live_order`

**Severity:** P1；如果 live 环境中允许该 env，则升为 P0
**Current file/function:** `src/execution/executor.py::execute_intent`, `src/execution/venue_adapter.py::VenueAdapterExecutor._do_submit`
**Money path:** executable edge → execution → order lifecycle

#### 代码证据

`execute_intent()` 现在默认会阻止 legacy path，这是正确方向。但它允许以下环境变量组合绕过：

```text
ZEUS_ALLOW_LEGACY_EXECUTION_INTENT=1
ZEUS_LEGACY_EXECUTION_INTENT_SCOPE=paper
```

只要这两个条件成立，函数继续计算 shares、调用 `_assert_cutover_allows_submit(IntentKind.ENTRY)`，然后进入 `_live_order(...)`。也就是说，一个名义上 `paper` scope 的 override，实际仍然会进入 live venue submit path。

同时 `VenueAdapterExecutor._do_submit()` 仍接受 `ExecutionIntent`，如果不是 `FinalExecutionIntent`，它会调用 `execute_intent(order, ...)`。

#### 为什么这是错的

环境变量名字表达的是“允许 legacy paper”，但代码行为是“在当前 runtime 中允许 legacy object 进入 `_live_order()`”。这违反 live-money object authority：

* live entry 应只接受 immutable `FinalExecutionIntent`；
* legacy `ExecutionIntent` 包含上游 fair-value / repricing 余留语义；
* paper override 不应能触发 live side effect。

#### live failure scenario

1. operator 或 CI 为 paper/debug 设置 `ZEUS_ALLOW_LEGACY_EXECUTION_INTENT=1` 和 `ZEUS_LEGACY_EXECUTION_INTENT_SCOPE=paper`；
2. daemon 实际运行在 `ZEUS_MODE=live`；
3. 某个兼容调用通过 `VenueAdapterExecutor.submit(ExecutionIntent)`；
4. `execute_intent()` 因 env 放行，进入 `_live_order()`；
5. live submit 绕过 `FinalExecutionIntent` 的 immutable cost-basis / no-recompute object contract。

#### Required fix

* `execute_intent()` 内部必须检查 `get_mode()`：
  * `get_mode()=="live"` 时，无论 env 如何，都 hard fail；
  * legacy override 只能在 non-live mode 下 route 到 shadow/paper executor，不能调用 `_live_order()`。
* `VenueAdapterExecutor._do_submit()` 在 live mode 下拒绝 `ExecutionIntent`，只接受 `FinalExecutionIntent`。

#### Required tests

* `ZEUS_MODE=live` + override env set + `ExecutionIntent` → raise `LEGACY_EXECUTION_INTENT_LIVE_BLOCKED`，无 command row，无 client contact。
* `ZEUS_MODE=paper` + override env set → route to paper/shadow，不触发 `_gate_runtime_check("live_venue_submit")` 或 `_live_order()`。

### Finding 2 — post-SDK terminal rejection persistence failure 仍可返回 `rejected`，但 DB 可能停在 SUBMITTING

**Severity:** P1；如果在生产中出现 DB lock/schema drift，则 P0
**Current file/function:** `src/execution/executor.py::execute_exit_order`；entry sibling 应做同类审计
**Money path:** execution → order lifecycle → recovery

#### 代码证据

在 `execute_exit_order()` 中，如果 SDK 返回 `success=False`，代码尝试写 `SUBMIT_REJECTED` 并 `conn.commit()`。但是如果这一写入失败，只是 `logger.error(...)`，然后仍返回：

```python
OrderResult(status="rejected", command_id=command_id)
```

missing `order_id` 分支也是同样结构。

#### 为什么这是错的

这和上一轮 ACK/pending 问题类似，只是更隐蔽：

* SDK call 已经跨过 side-effect boundary；
* SDK 返回 deterministic rejection 或 no order id；
* 本地 terminal rejection 没有 durable；
* 函数返回 `rejected`，上游认为已终结；
* DB 可能仍处于 SUBMITTING 或其他 pre-terminal state。

即使 venue 没创建 order，**本地 command lifecycle 也不能撒谎**。一个“内存 rejected / DB 未 rejected”的 split 会污染 recovery、riskguard 和 health。

#### failure scenario

1. SELL submit 返回 `success=False`。
2. `append_event(SUBMIT_REJECTED)` 或 commit 因 DB lock 失败。
3. 函数返回 `rejected`。
4. DB command 仍在 SUBMITTING。
5. recovery 后续把它当 unknown/in-flight 或 stale，造成 duplicate cancel/retry 或 stuck finding。

#### Required fix

统一 post-SDK terminal persistence policy：

* 对 `success=False` 和 `missing_order_id`，如果 `SUBMIT_REJECTED` 持久化失败，也必须调用 `_mark_post_submit_persistence_failure()`。
* 返回 `unknown_side_effect` 或 `review_required`，不能返回 `rejected`。
* entry `_live_order()` 的同类分支也需要逐项审计，保证 success_false / missing_order_id / final envelope failure / ACK fact failure policy 一致。

#### Required tests

* fake client returns `{"success": False}`；monkeypatch `append_event` 或 `conn.commit` 失败；assert 返回 `unknown_side_effect` / durable REVIEW_REQUIRED。
* fake client returns success-like payload but no order id；同样验证。
* entry + exit 都要测。

### Finding 3 — shadow candidate 直接写 `decision_events.source='phase0_backfill'`，污染 runtime/shadow provenance

**Severity:** P1
**Current files:** `src/strategy/candidates/stale_quote_detector.py`, `src/strategy/candidates/liquidity_provision_with_heartbeat.py`, likely sibling shadow candidates
**Money path:** monitoring / learning / report trust

#### 代码证据

`StaleQuoteDetector` 在 shadow enter path 直接 `INSERT INTO decision_events`，并写入：

```python
schema_version = SCHEMA_VERSION
source = "phase0_backfill"
```

并且 `polymarket_end_anchor_source` 默认值是 `"gamma_explicit"`。

`LiquidityProvisionWithHeartbeat` 同样直接插入 `decision_events`，也使用 `source="phase0_backfill"`，anchor 默认 `"gamma_explicit"`。

但 `decision_events.py` 的模型明确区分 live rows 与 backfill rows：PR-6 timing fields 对 backfill 可 nullable，live rows 则由 writer enforcing required fields。

同时 registry 明确这些 Phase 4 candidates 是 shadow，不是 historical phase0 backfill。

#### 为什么这是错的

这是 provenance object 混淆：

* `phase0_backfill` 是历史修复/回填来源；
* shadow candidate 是 runtime research signal；
* 两者在 report/learning 中应完全分开；
* 直接默认 `gamma_explicit` 会在没有明确 anchor proof 时制造 settlement anchor provenance。

#### failure scenario

1. shadow candidate 在 live daemon 中运行并写 `decision_events`；
2. 报告统计 `source='phase0_backfill'` 作为 backfill completeness；
3. shadow runtime rows 被误认为 historical recovered rows；
4. `gamma_explicit` 默认让 rows 看起来有明确 Polymarket endDate anchor；
5. 后续 promotion/research 证据被污染。

#### Required fix

* 增加明确 source：`shadow_decision` / `candidate_shadow`。
* 增加 canonical writer：`write_shadow_decision_event()`。
* 禁止 candidate strategy 直接 INSERT `decision_events`。
* anchor source 缺失时必须写 `unknown_legacy` 或 empty，并标明 `anchor_source_provenance="missing"`；不能默认 `gamma_explicit`。

#### Required tests

* shadow enter row source 必须是 `shadow_decision`，不是 `phase0_backfill`。
* missing `polymarket_end_anchor_source` 不得变成 `gamma_explicit`。
* reporting query 中 `phase0_backfill` 与 `shadow_decision` 分离。

### Finding 4 — candidate no-trade writer 绕过了 `write_no_trade_event()` 的 live schema guard

**Severity:** P1
**Current file/function:** `src/strategy/candidates/__init__.py::write_candidate_no_trade_row`
**Money path:** no-trade learning / report trust

#### 代码证据

`write_candidate_no_trade_row()` 直接 `INSERT INTO no_trade_events`，手工分配 seq，并直接写 `schema_version=SCHEMA_VERSION`。它没有调用 `write_no_trade_event()`，也没有调用 `assert_no_trade_events_schema_current_for_live()`，也没有显式写 `schema_compatibility`。

但当前 `no_trade_events.py` 已经专门实现了 live schema current guard，默认 `allow_schema_compatibility_downgrade=False`。

#### 为什么这是错的

这相当于修好了 canonical no-trade writer，但又在 shadow candidate framework 里开了一个 bypass writer。即使这些策略是 shadow，shadow learning/reporting 也不能绕过 schema/provenance discipline。

#### failure scenario

1. live DB schema 落后或 no_trade CHECK 尚未包含新 reason；
2. canonical `write_no_trade_event()` 会 fail-closed；
3. candidate writer 直接 insert，可能失败、默认 `schema_compatibility='current'`，或写出无法区分的 shadow no-trade row；
4. reports 认为 no-trade evidence 是 current schema。

#### Required fix

* `write_candidate_no_trade_row()` 调用 `write_no_trade_event()`，或者建立 `write_shadow_no_trade_event()`，但必须复用 schema guard / compatibility marking。
* source/provenance 增加 `candidate_strategy_key`、`shadow_runtime=true`、`schema_compatibility`。
* 对 in-memory test conn 可以单独 fixture，但不应复制生产 writer 逻辑。

#### Required tests

* old-schema no_trade_events + candidate no_trade → fail or degraded explicitly。
* current schema + candidate no_trade → `schema_compatibility='current'` 且 source/provenance 为 shadow，不是隐式默认。

### Finding 5 — shoulder exposure ledger 仍是 post-submit fail-soft；风险账本失败后交易已存在

**Severity:** P1；如果 `shoulder_sell` promoted live，则 P0
**Current files:** `src/engine/evaluator.py`, `src/engine/cycle_runtime.py`, `src/strategy/shoulder_cluster_cap.py`
**Money path:** sizing / correlated risk / execution

#### 代码证据

当前 evaluator 已在 sizing 后用真实 `proposed_notional=float(size)` 调 `shoulder_cluster_cap`。

`check_shoulder_cluster_cap()` 通过 ledger 读取 cluster+side exposure，检查 cross-city presence 和 hard cap。

但是 cycle runtime 是在 submit 被接受后才调用 `_record_submitted_shoulder_exposure(...)`。如果 ledger 写失败，只是 warning、`summary["degraded"]=True`，然后继续 `artifact.add_trade(...)`。

#### 为什么这是错的

对于 correlated exposure cap，ledger 不是普通 telemetry；它是 risk authority 的输入。交易已经 submit 后 ledger 写失败，会导致下一次 cap evaluation undercount exposure。

#### failure scenario

1. shoulder order submit 成功；
2. `_record_submitted_shoulder_exposure()` 因 DB/schema/lock 失败；
3. 系统只标记 degraded，但 position/order 已存在；
4. 下一轮 cap 读取 ledger，认为 exposure 不存在；
5. 继续允许 same cluster/same side exposure。

#### Required fix

二选一：

**更强方案：pre-submit reservation**

* 在进入 submit 前写 `shoulder_exposure_ledger` pending row；
* submit success 后转 active；
* submit rejected/cancel 后转 terminal；
* cap 读取 pending + active。

**最小方案：post-submit write failure freezes entries**

* 如果 live submit 成功但 shoulder ledger write 失败，必须触发 hard riskguard / entries pause；
* recovery 从 `venue_commands` / `position_current` 重建 shoulder exposure ledger。

#### Required tests

* ledger write failure after submit → entries paused / riskguard hard degraded。
* ledger reconstruction from command/position truth。
* cap reads pending exposure, not only active/settled exposure。

### Finding 6 — variance-based cluster exposure 替换 notional-sum 可能放松风险 cap

**Severity:** P1
**Current files/functions:** `src/state/portfolio.py::cluster_exposure_for_bankroll`, `src/engine/evaluator.py` risk throttle
**Money path:** sizing / correlated exposure

#### 代码证据

`cluster_exposure_for_bankroll()` 在 regime correlation store 存在时返回：

```python
sqrt(wᵀΣw)
```

注释还说明在 positive correlations 下它 `≤ total_notional / bankroll`。

Evaluator 把这个结果作为 `current_cluster_exp`，如果 `> 0.10` 才做 `regime_throttled_50pct`。

#### 为什么这是错的

旧逻辑是 gross notional heat；新逻辑是 portfolio volatility / variance heat。两者不是同一个经济对象：

* gross notional cap 控制最大损失/资金占用；
* variance heat 控制相关风险波动；
* `sqrt(wᵀΣw)` 小于 notional sum 并不代表“更严格”，反而可能让 gross notional 更容易过阈值；
* 如果 threshold 仍沿用 `0.10`，语义已经变了。

#### failure scenario

1. cluster 下两个城市各 8% bankroll exposure；
2. gross notional heat = 16%，旧逻辑 throttle；
3. variance heat 可能 <10%，新逻辑不 throttle；
4. 真实 max-loss exposure 增大，但系统认为 risk 更低。

#### Required fix

* 返回结构化对象：`ClusterExposureResult(gross_heat, variance_heat, method, fallback_reason)`。
* live risk cap 至少用 `max(gross_heat, variance_heat)`，或明确配置单独 `variance_heat_threshold`。
* 报告中必须显示本次使用的是 gross 还是 variance。

#### Required tests

* 构造 old notional cap 会触发、variance cap 不触发的组合；明确 assert policy。
* 阈值必须按 method 分开配置，不允许同一个 `0.10` 静默换语义。

### Finding 7 — `RegimeCorrelationStore.get()` 信任 DB JSON matrix，缺少矩阵约束验证

**Severity:** P1
**Current files:** `src/strategy/regime_correlation_store.py`, `src/state/schema/regime_correlation_cache_schema.py`
**Money path:** risk / correlated exposure

#### 代码证据

`RegimeCorrelationStore.get()` 从 DB 读取 `cities_json` 和 `matrix_json`，转成 numpy array，然后按 city subset 切片返回。没有验证：

* matrix 是否 square；
* matrix size 是否等于 cities 数；
* values 是否 finite；
* diagonal 是否为 1；
* 是否 symmetric；
* 是否在 [-1,1]；
* 是否 PSD 或至少 eigenvalue floor。

schema 侧只是 `TEXT NOT NULL`，没有结构约束。

#### 为什么这是错的

这是 risk math 的 source-of-truth table。如果 DB row 被旧 migration、manual operator edit、bad fit、JSON corruption 污染，risk cap 会用坏矩阵算 sizing。

#### failure scenario

1. `matrix_json` 是非对称矩阵或 diag 不为 1；
2. `cluster_exposure_for_bankroll()` 计算出过低 variance heat；
3. evaluator 不 throttle；
4. correlated exposure 超过预期。

#### Required fix

* `fit()` 和 `get()` 都验证 matrix invariants。
* invalid matrix 时 live fail-closed，或 fallback gross notional with explicit alert/counter。
* schema 可以加 metadata hash / fit_run_id / validation_status。

#### Required tests

* non-square matrix；
* finite violation NaN/Inf；
* diag != 1；
* asymmetric matrix；
* correlation outside [-1,1]；
* negative eigenvalue；
* repeated cities。

## 3. Code-level Integrity Matrix

| Money path segment | Current status | Code-level notes |
| --- | --- | --- |
| contract semantics | PASS/PARTIAL | `ExecutableTradeabilityStatus` 已经把 raw Gamma routing 与 executable authority 分离 |
| source truth | PARTIAL | forecast/source evidence checks强；shadow candidates 仍默认 `gamma_explicit` |
| forecast signal | PARTIAL | `_entry_forecast_evidence_errors()` 有 causality checks；未完整重审 ECMWF ingest |
| calibration | PARTIAL | no raw probability entry without Platt 的 gate 存在；transfer sigma missing route returns 0 |
| market prior | PARTIAL | CLOB executable snapshot强；shadow stale quote策略还不应混入 live report |
| executable edge | PARTIAL | FinalExecutionIntent 是正确方向；legacy ExecutionIntent env escape 是风险 |
| sizing | PARTIAL | Kelly executable context强；variance cap语义需要分离 gross vs variance |
| execution | PARTIAL | ACK persistence主要已修；terminal rejection persistence failure仍需处理 |
| monitoring | PASS/PARTIAL | business-plane health大幅改善 |
| settlement/redeem | PARTIAL | redeem pre-side-effect commit已修；Gamma fallback/negative risk仍需 external authority watch |
| learning/reporting | PARTIAL | no_trade canonical guard强；shadow writers绕过 canonical writer |

## 4. Release Gate 更新

### 当前允许

| 操作 | 建议 |
| --- | --- |
| paper / replay | 可以 |
| shadow candidate research | 可以，但必须隔离 source/provenance |
| tiny live | 仅在下面 hard gates 满足时可考虑 |
| normal live | 暂不建议 |
| report trust | 只能 partial，必须排除 shadow/backfill 混淆 rows |

### Before tiny live

必须验证：

1. `ZEUS_ALLOW_LEGACY_EXECUTION_INTENT` 未在 live daemon 环境中设置；或者代码已改成 live mode 永不允许 legacy。
2. post-SDK `success=False` / missing order id 的 persistence failure 会进入 UNKNOWN/REVIEW_REQUIRED。
3. `shoulder_sell` 保持 shadow；如果要 live，必须先修 ledger reservation/recovery。
4. variance cluster cap 不替代 gross notional cap，或使用单独 threshold。
5. shadow candidate rows 不再写 `source='phase0_backfill'`。
6. candidate no-trade writer 不再绕过 canonical no_trade schema guard。
7. live health composite 对连续 zero-progress 有 hard degraded。
8. current live DB schema version / pinned hash 与 main 一致。

## 5. Repair Packet

### Step 1 — 永久关闭 live legacy `ExecutionIntent`

* **Objective:** live submit 只接受 `FinalExecutionIntent`。
* **Files:** `src/execution/executor.py`, `src/execution/venue_adapter.py`, tests around `submit_order`.
* **Invariant:** live entry cannot use recomputable / legacy fair-value object.
* **Acceptance:** `ZEUS_MODE=live` 下 `ExecutionIntent` 永远 fail closed，即使 env override set。
* **Tests:** env override + live mode + fake ExecutionIntent must not create command row or touch client.

### Step 2 — 统一 post-SDK terminal rejection persistence failure policy

* **Objective:** SDK returned rejection / missing order id 后，如果 durable rejection event 失败，不能返回 `rejected`。
* **Files:** `src/execution/executor.py`.
* **Invariant:** memory status cannot outrank durable command state after side-effect boundary.
* **Acceptance:** append/commit failure → `unknown_side_effect` or `review_required`。
* **Tests:** entry + exit success_false / missing_order_id fault injection。

### Step 3 — Shadow candidate provenance split

* **Objective:** runtime shadow decisions 不再伪装成 `phase0_backfill`。
* **Files:** `src/strategy/candidates/*`, `src/state/decision_events.py`, possible schema enum/source CHECK if present.
* **Invariant:** historical backfill、live_decision、shadow_decision 是三个不同 source objects。
* **Acceptance:** shadow rows have `source='shadow_decision'`, explicit `live_executable=false`, no fabricated gamma anchor。
* **Tests:** stale quote / liquidity provision shadow enter rows verify source + anchor provenance。

### Step 4 — Candidate no-trade canonical writer

* **Objective:** candidate no-trade 复用 canonical schema guard。
* **Files:** `src/strategy/candidates/__init__.py`, `src/state/no_trade_events.py`.
* **Invariant:** no_trade schema compatibility policy is single-source。
* **Acceptance:** old schema fails/degrades explicitly; current schema writes `schema_compatibility='current'`。
* **Tests:** old-schema no_trade candidate writer test。

### Step 5 — Shoulder exposure ledger reservation/reducer

* **Objective:** shoulder cap 不是 telemetry，而是 risk authority。
* **Files:** `src/engine/cycle_runtime.py`, `src/engine/evaluator.py`, `src/state/shoulder_exposure_ledger.py`, `src/strategy/shoulder_cluster_cap.py`.
* **Invariant:** accepted live shoulder exposure cannot exist without ledger/recoverable exposure truth。
* **Acceptance:** submit success + ledger write fail freezes entries or creates recovery-required state。
* **Tests:** ledger write failure after submit, reconstruction from position/command, pending exposure included in cap。

### Step 6 — Split gross heat and variance heat

* **Objective:** risk cap 不静默换经济对象。
* **Files:** `src/state/portfolio.py`, `src/engine/evaluator.py`.
* **Invariant:** max-loss/gross exposure and correlation variance exposure are separate risk dimensions。
* **Acceptance:** cluster exposure result carries `gross_heat`, `variance_heat`, `method`; live cap uses configured policy。
* **Tests:** gross cap triggers while variance does not; policy behavior explicit。

### Step 7 — Validate regime correlation matrices

* **Objective:** DB JSON matrix cannot corrupt live sizing。
* **Files:** `src/strategy/regime_correlation_store.py`, schema/tests。
* **Invariant:** correlation matrix finite/symmetric/diag=1/bounded/PSD before use。
* **Acceptance:** invalid matrix rejected or conservative fallback with alert。
* **Tests:** NaN, non-square, asymmetric, diag mismatch, out-of-range, non-PSD。

## 6. 总体架构评价

当前 main 的成熟度已经明显高于上一轮：

* executable snapshot authority 更清晰；
* command/redeem side-effect boundary 更严格；
* no-trade schema guard 更强；
* business-plane health 更可信；
* money-path replay 已经出现；
* FinalExecutionIntent 已成为 live 入口主线。

但顶级架构视角下，系统还差最后一层：**所有“兼容入口、shadow writer、risk experimental model、post-side-effect terminal branch”都必须服从同一个 live-money authority discipline。**

最短路径不是继续加大规模 refactor，而是按上面的 repair packet 精准关闭这些错位。修完后，Zeus 才能从“很多边界已经强约束”进入“没有旁路能绕过边界”的状态。
