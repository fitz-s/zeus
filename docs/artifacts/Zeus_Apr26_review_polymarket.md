# ZEUS × POLYMARKET × LIVE-MONEY FORENSIC TRIBUNAL

## 1. Executive tribunal verdict

**Zeus 当前不符合 Polymarket / CLOB live-money execution reality。**

**Zeus 不具备真钱交易上线资格。**
不是因为它没有使用官方 SDK；它确实使用了 `py-clob-client`，也有不少 weather provenance 和 position reconciliation scaffolding。真正的问题更危险：**Zeus 把“交易意图 → 外部副作用 → 本地状态”的顺序做反了，并且没有生产级 order lifecycle。**

最严重的 live-money blocker 是：

1. **外部下单副作用发生在持久化 command / order journal 之前。** `execute_discovery_phase()` 先调用 `execute_intent()` 下单，再 `materialize_position()`，再写 DB / canonical events。也就是说，CLOB 可能已经收到订单，但 Zeus 还没有 durable command / local position / exchange order event。这个顺序在真钱系统里是 S0。代码路径见 `src/engine/cycle_runtime.py` 的 entry execution flow，以及 `src/execution/executor.py` 的 `_live_order()`。([GitHub][1])

2. **submit timeout after side effect 被错误归类为 `rejected`。** `_live_order()` 捕获所有异常后返回 `OrderResult(status="rejected")`，没有 `SUBMIT_TIMEOUT_UNKNOWN`、没有 exchange reconciliation escalation、没有 duplicate-submit protection。Polymarket CLOB 的订单是 EIP-712 signed order，经 offchain matching operator 接收和撮合；网络 timeout 不能等同于“订单未提交”。([GitHub][2])

3. **没有 idempotent order command / client order identifier / exchange reconciliation loop。** Zeus 有一个本地 `idempotency_key` 字段，但它不是交易所侧幂等键，也不是 durable pre-submit command。官方 SDK 暴露 `get_orders`、`get_order`、`get_trades`、cancel、balance/allowance 等接口，生产系统必须用这些接口把 timeout / partial fill / cancel / restart 后的状态重新对齐。Zeus 只做了弱 order polling 和 position-level reconciliation。([GitHub][3])

4. **partial fill、resting order、cancel failure、cancel-replace 没有完整状态机。** 当前 `OrderResult` 只有 `filled / pending / cancelled / rejected`，没有 `PARTIALLY_FILLED`、`CANCEL_REQUESTED`、`CANCEL_FAILED`、`SUBMIT_TIMEOUT_UNKNOWN`、`REVIEW_REQUIRED`。这不是命名问题，是资金真实性问题。([GitHub][2])

5. **Zeus docs 声称 RED 会 cancel pending / sweep positions，但 runtime 没做到。** README / AGENTS 把 RED 描述成强制取消挂单、清仓、链上对账；实际 `cycle_runner` 的 RED path 主要是把 active position 标记为 force-exit，让 normal exit lane 后续处理，并不等同于立即 cancel all pending orders。这个 authority drift 会误导未来 coding agent 修改错误 subsystem。([GitHub][4])

**单一最危险 mismatch：**
Zeus 的 entry path 是 **“post order first, persist truth later”**。这违反真钱交易系统最基本的 command discipline。只要发生网络 timeout、进程崩溃、SDK 异常、DB 写入失败，Zeus 就可能本地认为没有订单，交易所却已有挂单或成交。

**最可能静默亏钱机制：**
网络 timeout after submit → Zeus 标记 `rejected` → 下个周期再次提交 → Polymarket 上已有第一笔挂单或成交 → Zeus 本地没有 durable trace → exposure 翻倍或库存错误。

**最可能 state-corruption 机制：**
partial fill 后 Zeus 把 order 当成 filled / pending 的粗状态处理，没有 remaining shares / trade events / cancel acknowledgement。风险引擎和 exit lifecycle 使用错误 size。

**最可能 AI-agent-induced regression：**
未来 agent 读 README / AGENTS，以为 `chain_reconciliation` 和 RED policy 已经是 production truth，于是只改策略或风险文档，不修 `executor.py`、`cycle_runtime.py`、`fill_tracker.py`、`exit_lifecycle.py` 的真钱状态机。

**上线前必须修：**
禁止自动 live order；建立 order command journal；所有 external side effect 前先持久化；timeout 必须进入 UNKNOWN；实现 active-order/trade/position reconciliation；实现 partial-fill/cancel-failure 状态机；移除 Gamma-as-executable fallback；修 docs authority drift；补真实 failure-mode tests。

---

## 2. External benchmark evidence map

| Source category                      | What it proves                                                                                                                                                          | Implementation lesson for Zeus                                                                                             | Audit questions generated                                                          |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Polymarket CLOB overview / lifecycle | Polymarket 是 hybrid CLOB：订单 offchain signed / matched，settlement onchain；订单生命周期不是单次 HTTP 成功/失败。([Polymarket Documentation][5])                                          | Local state must separate intent, signed order, submitted order, accepted/resting order, trade/fill, position, settlement. | Zeus 是否把 submit exception 当成 unknown？是否能 reconcile open orders/trades？             |
| Auth docs                            | L2 auth 需要 API key/passphrase/HMAC headers；create order 仍需要 private key 签 EIP-712 order。signature types 有 EOA / proxy / safe。([Polymarket Documentation][5])            | Auth config must be explicit, validated, not hardcoded blindly.                                                            | Zeus 是否硬编码 signature type？是否验证 funder/proxy/allowance？                             |
| CLOB order docs / SDK                | `create_order()` needs token id, side, price, size, tick size, negRisk; `post_order()` accepts order type; market orders have distinct amount semantics.([GitHub][3])   | Gateway must know tick/min size/order type/market amount semantics.                                                        | Zeus 是否 preflight tick/min size/order type？是否区分 BUY market amount and SELL shares？ |
| Order types                          | GTC rests until filled/cancelled; GTD expires; FOK must fully fill immediately; FAK fills immediately then cancels remainder.([Polymarket Documentation][6])            | State machine needs resting, partial, filled, cancelled, expired, remainder.                                               | Zeus 是否支持 FAK/FOK/GTD？是否知道 partial fill?                                           |
| Cancel / orders / trades endpoints   | Official SDK/API exposes cancel, cancel-all, get open orders, get order, get trades, balance/allowance.([Polymarket Documentation][7])                                  | Reconciliation cannot stop at local pending rows.                                                                          | Zeus 是否 queries open orders/trades after crash/timeout/cancel?                     |
| Error/rate-limit docs                | 425 matching engine restart and 429 rate limit require retry/backoff; errors are not semantic rejection.([Polymarket Documentation][8])                                 | API errors must be typed: retryable, unknown, terminal, review-required.                                                   | Zeus 是否 collapses exceptions to rejected?                                          |
| WebSocket docs                       | Polymarket supports market/user channels for orderbook, trades, personal order activity.([Polymarket Documentation][9])                                                 | Production bot should subscribe to user order/trade stream or compensate with tight polling reconciliation.                | Zeus 是否 has user WS?                                                               |
| Public position watcher              | Serious projects distinguish order filled, trade confirmed, position updated, sellable on-chain; conflating them overestimates inventory.([GitHub][10])                 | Zeus must not treat accepted/fill/position as one state.                                                                   | Zeus 是否 separates accepted, fill, position, sellable balance?                      |
| NautilusTrader Polymarket adapter    | Professional adapter has user WS, order/trade reconciliation, market-order semantic guards, dynamic tick precision, active order/position reconciliation.([GitHub][11]) | Zeus should benchmark against execution-client architecture, not script-level SDK use.                                     | Zeus 是否 has execution adapter semantics or only wrapper?                           |
| Market-maker bots                    | Real bots implement cancel/replace, stale order cancellation, inventory/exposure limits, continuous monitoring.([GitHub][12])                                           | Resting orders are living state, not one-off function calls.                                                               | Zeus 是否 has cancel-replace lifecycle and stale order guard?                        |

---

## 3. Zeus repository x-ray

Public source tree shows Zeus has `src`, `tests`, `scripts`, `architecture`, `docs`, `state`, `data`, `config`, `raw`, `.agents`, `.claude`, `README.md`, `AGENTS.md`, `requirements.txt`, and `workspace_map.md`.([GitHub][13])

Runtime-relevant map:

| Path                                              | Runtime role                                                                                                                             |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `src/main.py`                                     | Daemon entrypoint, code-authoritative live runtime state, startup wallet check.([GitHub][14])                                            |
| `src/data/polymarket_client.py`                   | Main Polymarket CLOB/Data API adapter: SDK init, orderbook, limit order, cancel, order status, positions, balance, redeem.([GitHub][15]) |
| `src/data/market_scanner.py`                      | Gamma market discovery, event parsing, outcome / token extraction, Gamma price fallback.([GitHub][16])                                   |
| `src/engine/evaluator.py`                         | Converts candidate weather markets into edge decisions; uses CLOB best bid/ask and fee-aware sizing.([GitHub][17])                       |
| `src/engine/cycle_runtime.py`                     | Actual entry execution path, materialization, chain sync, orphan order cleanup.([GitHub][1])                                             |
| `src/execution/executor.py`                       | Execution intent and live order placement; core money path.([GitHub][2])                                                                 |
| `src/execution/fill_tracker.py`                   | Pending order polling and local fill/void transitions.([GitHub][18])                                                                     |
| `src/execution/exit_lifecycle.py`                 | Exit / sell lifecycle, retry, stale order handling.([GitHub][19])                                                                        |
| `src/execution/collateral.py`                     | Sell collateral / side balance checks.([GitHub][20])                                                                                     |
| `src/state/chain_reconciliation.py`               | Data API position reconciliation; local vs exchange position alignment.([GitHub][21])                                                    |
| `src/state/chronicler.py`                         | Append-only chronicle / event logging, but not sufficient as pre-submit order journal.([GitHub][22])                                     |
| `src/state/db.py`                                 | SQLite schema: market events, token prices, trade decisions, observations, settlement, coverage ledgers.([GitHub][23])                   |
| `architecture/2026_04_02_architecture_kernel.sql` | Intended canonical event schema; missing several production order states.([GitHub][24])                                                  |
| `src/contracts/settlement_semantics.py`           | Weather settlement semantics: source, unit, precision, rounding.([GitHub][25])                                                           |
| `src/execution/harvester.py`                      | Resolved-market settlement harvester; UMA-resolved gate and source-correct observation lookup.([GitHub][26])                             |
| `src/data/observation_instants_v2_writer.py`      | Strong observation provenance writer: authority, revision, source-tier, units, time basis.([GitHub][27])                                 |
| `src/riskguard/*`                                 | Risk level enum, policy, RED/DATA_DEGRADED semantics, alerts.([GitHub][28])                                                              |
| `tests/*`                                         | Execution, settlement, risk, scanner, lifecycle tests; current execution tests miss key live failures.([GitHub][29])                     |
| `scripts/live_smoke_test.py`                      | Standalone live order smoke test; directly places a real Polymarket order when enabled.([GitHub][30])                                    |
| `scripts/force_lifecycle.py`                      | Can create fake/mock portfolio state; useful but dangerous for provenance if misused.([GitHub][31])                                      |
| `README.md`, `AGENTS.md`, `workspace_map.md`      | Authority docs for humans/agents; several claims drift from runtime truth.([GitHub][4])                                                  |

---

## 4. Zeus Code Excavation Ledger

| File / Module                                     | Role                          | Why inspected                   | Key functions/classes                                                                                                                             | Findings / Notes                                                                                                                                                                              |
| ------------------------------------------------- | ----------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `README.md`                                       | Public architecture claim     | Detect authority drift          | Lifecycle / RED / reconciliation claims                                                                                                           | Claims RED cancels pending and sweeps positions; runtime is weaker.([GitHub][4])                                                                                                              |
| `AGENTS.md`                                       | Future coding-agent authority | Agent-use safety                | Key-file routing, canonical truth claims                                                                                                          | Overstates canonical reconciliation and RED behavior; dangerous for AI agents.([GitHub][32])                                                                                                  |
| `workspace_map.md`                                | Navigation authority          | Determines likely edit path     | Repo routing                                                                                                                                      | Correctly says code/DB truth outranks docs, but docs still overclaim runtime behavior.([GitHub][33])                                                                                          |
| `requirements.txt`                                | Dependency truth              | SDK version and official client | `py-clob-client>=0.25`                                                                                                                            | Zeus uses official SDK dependency, not pure homemade signing.([GitHub][34])                                                                                                                   |
| `src/main.py`                                     | Live daemon entrypoint        | Accidental live trading guard   | `_startup_wallet_check`, scheduler                                                                                                                | Code-authoritative live runtime state; startup balance check fail-closes. Good but insufficient.([GitHub][14])                                                                                |
| `src/data/polymarket_client.py`                   | Polymarket adapter            | Core API boundary               | `_ensure_client`, `get_orderbook`, `place_limit_order`, `cancel_order`, `get_order_status`, `get_open_orders`, `get_positions_from_api`, `redeem` | Uses SDK for signing/posting. Hardcodes `signature_type=2`. Direct `/book` orderbook fetch has no raw persistence/stale guard. Exceptions not typed at adapter boundary.([GitHub][15])        |
| `src/data/market_scanner.py`                      | Gamma discovery               | Token identity / metadata truth | `_get_active_events_snapshot`, `_fetch_events_by_tags`, `_parse_event`, `_extract_outcomes`, `get_current_yes_price`                              | Parses `clobTokenIds` and outcome labels; has some YES/NO protection. But discovery uses Gamma metadata and has Gamma price fallback; no executable CLOB market-state snapshot.([GitHub][16]) |
| `src/engine/evaluator.py`                         | Signal → executable candidate | Tradability check               | CLOB best bid/ask, VWMP, fee sizing                                                                                                               | Better than docs-only discovery: uses CLOB book before entry. Still lacks book raw snapshot, exchange timestamp, stale-age guard.([GitHub][17])                                               |
| `src/engine/cycle_runtime.py`                     | Orchestration / entry path    | Actual ordering of side effects | `execute_discovery_phase`, `materialize_position`, `run_chain_sync`, `cleanup_orphan_open_orders`                                                 | S0: calls `execute_intent()` before durable materialization/logging. Position sync exists, order sync incomplete.([GitHub][1])                                                                |
| `src/execution/executor.py`                       | Live order submission         | Money-path truth                | `OrderResult`, `create_execution_intent`, `execute_intent`, `_live_order`, `execute_exit_order`                                                   | S0: exceptions become `rejected`; no `UNKNOWN`. OrderResult lacks partial/cancel-failure states.([GitHub][2])                                                                                 |
| `src/execution/fill_tracker.py`                   | Pending entry polling         | Fill lifecycle                  | `check_pending_entries`, `_mark_entry_filled`, timeout cancel path                                                                                | Polling exists, but partial fill / remaining size / cancel failure are not production-grade states.([GitHub][18])                                                                             |
| `src/execution/exit_lifecycle.py`                 | Sell / exit lifecycle         | Exit semantics                  | `_execute_live_exit`, `check_pending_exits`, stale cancel/retry                                                                                   | Logs exit intent before sell, which is better than entry. But cancel failure and partial fill remain weak.([GitHub][19])                                                                      |
| `src/execution/collateral.py`                     | Sell-side risk                | Side-balance protection         | conditional token balance checks                                                                                                                  | Fail-closed sell collateral checks exist. Buy allowance/proxy config still weak.([GitHub][20])                                                                                                |
| `src/state/chain_reconciliation.py`               | Position reconciliation       | Exchange truth alignment        | local vs chain position reconciliation                                                                                                            | Useful position-level mitigation, but too late for order lifecycle. It cannot reconstruct unknown submits without order command IDs.([GitHub][21])                                            |
| `src/state/chronicler.py`                         | Append-only evidence          | Audit trail                     | chronicled event writes                                                                                                                           | Helpful but not used as mandatory pre-side-effect order journal.([GitHub][22])                                                                                                                |
| `src/state/db.py`                                 | Persistence schema            | Data provenance / order storage | market, token, trade, observation, settlement tables                                                                                              | Weather provenance is strong. Polymarket order/trade raw payload storage is weak/missing.([GitHub][23])                                                                                       |
| `architecture/2026_04_02_architecture_kernel.sql` | Intended canonical schema     | State-machine design            | `position_events`, `execution_fact`                                                                                                               | Missing `SUBMIT_TIMEOUT_UNKNOWN`, `PARTIALLY_FILLED`, `CANCEL_FAILED`, `RESTING`, `REVIEW_REQUIRED`, `REDEEMED`.([GitHub][24])                                                                |
| `src/contracts/settlement_semantics.py`           | Weather settlement contract   | Unit/rounding/source            | `SettlementSemantics`, `assert_settlement_value`, `for_city`                                                                                      | One of the stronger subsystems: explicit source/unit/rounding.([GitHub][25])                                                                                                                  |
| `src/execution/harvester.py`                      | Settlement truth harvester    | Resolved-market validation      | `_fetch_settled_events`, `_find_winning_bin`, `_write_settlement_truth`                                                                           | Stronger than execution: UMA-resolved gate, source-correct obs, QUARANTINED path. Still hardcoded around high-temperature market semantics.([GitHub][26])                                     |
| `src/data/observation_instants_v2_writer.py`      | Observation provenance        | Timestamp/unit/revision safety  | typed writer validation                                                                                                                           | Strong: rejects missing authority/provenance, validates source tier, units, timestamp basis, revisions.([GitHub][27])                                                                         |
| `src/riskguard/risk_level.py`                     | Risk semantics                | RED/DATA_DEGRADED truth         | `RiskLevel` enum/actions                                                                                                                          | Docs say RED cancels pending and exits all positions. Runtime does not fully implement immediate behavior.([GitHub][28])                                                                      |
| `src/riskguard/riskguard.py`                      | Risk process                  | Degraded-state behavior         | risk process writer                                                                                                                               | Risk process writes state/actions; not sufficient to guarantee order cancellation.([GitHub][35])                                                                                              |
| `tests/test_executor.py`                          | Execution tests               | Live failure coverage           | exit rounding, missing order id                                                                                                                   | retired diagnostic execution path skipped; no timeout/partial/cancel failure/duplicate submit tests.([GitHub][36])                                                                                          |
| `tests/test_executor_typed_boundary.py`           | Typed boundary tests          | Price validation                | malformed limit price                                                                                                                             | Useful but tiny. Does not test exchange semantics.([GitHub][37])                                                                                                                              |
| `tests/test_harvester_dr33_live_enablement.py`    | Settlement tests              | Weather settlement              | UMA resolved gate, VERIFIED/QUARANTINED                                                                                                           | Better settlement coverage than execution coverage.([GitHub][38])                                                                                                                             |
| `tests/test_force_exit_review.py`                 | Risk flag test                | RED behavior                    | review flag getter                                                                                                                                | Tests review flag, not actual cancel/sweep.([GitHub][39])                                                                                                                                     |
| `scripts/live_smoke_test.py`                      | Manual live smoke             | Accidental live order risk      | direct limit order/cancel                                                                                                                         | Places real order when enabled; not lifecycle-safe and bypasses daemon.([GitHub][30])                                                                                                         |
| `scripts/force_lifecycle.py`                      | Lifecycle debug script        | State pollution risk            | mock/fake position creation                                                                                                                       | Can mutate local state using fake positions; should be quarantined from production DB.([GitHub][31])                                                                                          |

---

## 5. Zeus actual runtime map

### 5.1 Market discovery path

Current path:

```text
Gamma /events
  -> src/data/market_scanner.py
     -> _fetch_events_by_tags()
     -> _parse_event()
     -> _extract_outcomes()
        parses outcomes + clobTokenIds
        attempts YES/NO label validation/swap
  -> MarketCandidate / outcome rows
  -> src/engine/evaluator.py
     -> CLOB get_best_bid_ask(token_id)
     -> VWMP / edge / fee-aware sizing
  -> ExecutionIntent
```

Assessment: **PARTIAL**. Zeus does not blindly trade only from Gamma; evaluator does hit CLOB orderbook before entry, which is good. But Gamma remains too authoritative in discovery and monitor fallback, and Zeus does not persist a raw executable snapshot containing `conditionId`, `clobTokenIds`, outcome labels, CLOB token tradability, tick size, min size, negRisk, and book timestamp/hash.([GitHub][16])

### 5.2 API call path

```text
src/data/polymarket_client.py
  -> _ensure_client()
       ClobClient(CLOB_BASE, key, chain_id=137, signature_type=2, funder=...)
       create_or_derive_api_creds()
  -> get_orderbook()
       raw httpx GET /book
  -> place_limit_order()
       OrderArgs(price, size, side, token_id)
       create_order()
       post_order()
  -> cancel_order()
       client.cancel(order_id)
  -> get_order_status()
       client.get_order() or get_orders()
  -> get_positions_from_api()
       Data API /positions
```

Assessment: **PARTIAL**. SDK usage exists. But `signature_type=2` is hardcoded, order type is implicit, raw payloads are not stored, and API errors are not classified into retryable/unknown/terminal. Official docs and SDK show distinct auth/signing and order-management operations that Zeus should model explicitly.([GitHub][15])

### 5.3 Order lifecycle path

```text
Discovery/evaluator creates intent
  -> cycle_runtime.execute_discovery_phase()
     -> executor.execute_intent()
        -> _live_order()
           -> PolymarketClient.place_limit_order()
              -> SDK create_order()
              -> SDK post_order()
        -> returns OrderResult(pending/rejected/filled/cancelled)
     -> materialize_position() only if pending/filled
     -> log_trade_entry / execution_report / canonical write
  -> fill_tracker polls get_order_status()
  -> mark filled/void/timeout cancel attempt
  -> chain_reconciliation syncs positions from Data API
```

Assessment: **FAIL**. The side effect comes before durable command. The lifecycle has no `SUBMITTING_UNKNOWN`, no partial fill, no cancel failure, no accepted/resting/fill separation.([GitHub][1])

### 5.4 State persistence path

Zeus stores portfolio/trade decisions/canonical events and weather artifacts, but not a complete order-command journal. The SQL kernel has intended events like `ENTRY_ORDER_POSTED` and `ENTRY_ORDER_FILLED`, but lacks production states for unknown submit, partial fill, cancel requested/failed, exchange reconciliation, and redeem.([GitHub][24])

Assessment: **PARTIAL → S0 blocker for execution**.

### 5.5 Reconciliation path

Current:

```text
cycle_runtime.run_chain_sync()
  -> PolymarketClient.get_positions_from_api()
  -> chain_reconciliation reconciles local position cache
```

There is also orphan open order cleanup that queries open orders and cancels exchange orders not known locally. That is not a full reconciliation loop; it cannot safely resolve timeout-after-submit, partial fill, trade confirmation, cancel failure, or client-command identity.([GitHub][1])

Assessment: **PARTIAL, not production-grade**.

### 5.6 Data ingestion path

Weather data path is much stronger than trading data path. Observation writer separates authority/provenance/revisions/source tier/unit/time basis, while trading path does not preserve raw orderbook/order/trade payloads equivalently.([GitHub][27])

Assessment: **weather PARTIAL/PASS; execution provenance FAIL**.

### 5.7 Settlement path

Current settlement harvester:

```text
Gamma closed/resolved events
  -> require UMA resolution
  -> infer winning bin from outcomePrices
  -> fetch source-family-correct observation
  -> SettlementSemantics.assert_settlement_value()
  -> write VERIFIED or QUARANTINED settlement truth
```

Assessment: **substantially better than execution**, but still needs resolved-market corpus tests, low/high separation, and exchange resolution snapshot preservation.([GitHub][26])

### 5.8 Test / mocking path

Execution tests are not live-failure tests. The retired diagnostic execution path is skipped; boundary tests validate malformed price only; settlement tests are more meaningful than execution tests.([GitHub][36])

Assessment: **TESTS-INSUFFICIENT**.

### 5.9 Docs / agent guidance path

Docs are not runtime truth. Here, docs are actively dangerous because they claim RED cancels pending / sweeps positions and every cycle reconciles local against chain, while runtime only implements fragments.([GitHub][4])

Assessment: **MISLEADING-DOCS**.

---

## 6. Full forced audit matrix

|  # | Axis                                            | Verdict            | Severity | External Benchmark                                                                                                                | Zeus Evidence                                                                                                                 | Failure Mechanism                                                                                  | Required Fix                                                                                | Confidence  |
| -: | ----------------------------------------------- | ------------------ | -------- | --------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ----------- |
|  1 | Polymarket API endpoint correctness             | PARTIAL            | S1       | CLOB/Data/Gamma have distinct roles; authenticated order management differs from public metadata.([Polymarket Documentation][40]) | Uses CLOB `/book`, SDK order/cancel, Data API positions.([GitHub][15])                                                        | Endpoint use exists, but order/trade reconciliation endpoints and user channel are not integrated. | Add gateway with typed endpoint roles and reconciliation use of orders/trades/positions.    | High        |
|  2 | Official SDK usage / signing / auth correctness | PARTIAL            | S1       | SDK signs EIP-712 order and uses L2 creds; signature types matter.([Polymarket Documentation][5])                                 | `ClobClient(... signature_type=2, funder=...)`, `create_or_derive_api_creds()`.([GitHub][15])                                 | Hardcoded signature type can mismatch wallet/proxy; no explicit validation.                        | Configurable signature type; startup auth/allowance/funder verification.                    | High        |
|  3 | Gamma/Data/CLOB boundary correctness            | FAIL               | S1       | Gamma/Data are public metadata/data; executable truth is CLOB/order state.([Polymarket Documentation][40])                        | Gamma scanner and Gamma price fallback exist; evaluator does CLOB book but not final executable state snapshot.([GitHub][16]) | Metadata can say active while CLOB is stale/closed/not tradable.                                   | No trade without fresh CLOB market/orderbook snapshot. Remove Gamma price as trading truth. | High        |
|  4 | Market discovery and metadata freshness         | PARTIAL            | S1       | Fresh metadata must not substitute executable order state.                                                                        | Scanner has authority/stale snapshot, but legacy path loses authority and no CLOB tradability snapshot.([GitHub][16])         | Stale Gamma metadata can route wrong market/token.                                                 | Persist raw Gamma snapshot plus executable CLOB snapshot; enforce freshness.                | Medium-high |
|  5 | Event/market/outcome/token identity mapping     | PARTIAL            | S1       | Token IDs are executable assets; condition/event/slug are not interchangeable.                                                    | `_extract_outcomes()` parses outcomes and `clobTokenIds`; DB stores reduced fields.([GitHub][16])                             | Wrong identifier can buy wrong side/market.                                                        | Store raw event/market, conditionId, market id, slug, question, token map snapshot.         | High        |
|  6 | YES/NO inversion protection                     | PARTIAL            | S0/S1    | Outcome-token mapping must be explicit and validated.                                                                             | Scanner has label check/swap; order path trusts token fields.([GitHub][16])                                                   | Label/order drift can invert exposure silently.                                                    | Add invariant tests and persisted signed token/outcome snapshot at command creation.        | Medium-high |
|  7 | Closed/inactive/resolved market detection       | FAIL               | S1       | Closed/resolved markets must not accept new strategy entries.                                                                     | Gamma closed filter exists; no authoritative CLOB executable close check before post.([GitHub][16])                           | Zeus can submit into non-tradable or stale market.                                                 | Preflight CLOB market status/book freshness; block on resolved/closed/inactive.             | High        |
|  8 | Orderbook freshness and tradability             | PARTIAL            | S1       | Orderbook timestamp/hash/min size/tick are relevant. SDK orderbook has timestamp/min/tick/hash fields.([GitHub][41])              | Zeus normalizes bids/asks floats; empty book blocks.([GitHub][15])                                                            | Stale non-empty book can pass and misprice.                                                        | Store book timestamp/hash; max-age guard; require min size/tick.                            | High        |
|  9 | Price/tick/precision handling                   | PARTIAL            | S1       | SDK resolves tick size and validates price.([GitHub][3])                                                                          | SDK `create_order()` used, but Zeus preflight/tick tests are thin.([GitHub][15])                                              | Runtime rejection or wrong rounding near tick boundary.                                            | Explicit tick/min-size preflight and tests.                                                 | High        |
| 10 | Size/amount/notional/share semantics            | PARTIAL            | S1       | Limit `size` is shares; market BUY `amount` is USDC, SELL amount is shares.([GitHub][41])                                         | Limit shares computed from USD/price. Market orders not implemented.([GitHub][2])                                             | Future market-order addition likely wrong; min size not enforced.                                  | Encode order semantic type; tests for size/notional/amount.                                 | High        |
| 11 | BUY/SELL semantics                              | PARTIAL            | S1       | Side and token id jointly define exposure.                                                                                        | Entry BUY; exit SELL; sell collateral check exists.([GitHub][2])                                                              | No reduce-only guarantee; token inversion still possible.                                          | Reduce-only sell guard and outcome-token invariant.                                         | Medium-high |
| 12 | Limit order semantics                           | PARTIAL            | S1       | Default GTC rests; accepted order may not fill immediately.([Polymarket Documentation][6])                                        | Limit order placed, result becomes `pending`.([GitHub][2])                                                                    | No resting/accepted/partial separation.                                                            | Explicit order type and state machine.                                                      | High        |
| 13 | Market order semantics                          | NOT-IMPLEMENTED    | S2       | Market orders are marketable limits; BUY amount means USDC.([GitHub][42])                                                         | Zeus appears limit-only.                                                                                                      | Not current live bug, but future trap.                                                             | Keep disabled until semantics tests exist.                                                  | High        |
| 14 | FOK/FAK/GTC/GTD/GTD expiry handling             | NOT-IMPLEMENTED    | S2       | Order types differ materially.([Polymarket Documentation][6])                                                                     | No explicit order type in Zeus placement path.([GitHub][15])                                                                  | Default GTC may rest unexpectedly; no expiry discipline.                                           | Add explicit order_type, expiry, post_only policy.                                          | High        |
| 15 | Preflight checks before order                   | PARTIAL            | S1       | Need token/tradability/tick/min/allowance/balance checks.                                                                         | Some price/wallet/book checks; no full CLOB state/allowance gate.([GitHub][14])                                               | Orders fail live or hit stale book.                                                                | Preflight object with all pass/fail fields persisted.                                       | High        |
| 16 | Persist-before-side-effect command discipline   | FAIL               | S0       | Trading systems must durably record command before external side effect.                                                          | Entry path posts order before position/log/canonical write.([GitHub][1])                                                      | Timeout/crash creates untracked exchange order.                                                    | `order_command` journal before signing/posting.                                             | Very high   |
| 17 | Idempotency / duplicate submit prevention       | FAIL               | S0       | Retry after unknown needs idempotency or reconciliation.                                                                          | Local idempotency key not exchange-proven; no pre-submit command.([GitHub][2])                                                | Duplicate exposure after retry.                                                                    | Durable command id, client order id/signature hash, single-flight lock.                     | High        |
| 18 | Timeout-after-submit UNKNOWN handling           | FAIL               | S0       | HTTP failure after side effect is unknown, not rejected.                                                                          | `_live_order()` exception returns `rejected`.([GitHub][2])                                                                    | Duplicate order / missing position.                                                                | `SUBMIT_TIMEOUT_UNKNOWN` and forced reconciliation.                                         | Very high   |
| 19 | Accepted order vs filled trade separation       | PARTIAL            | S1       | Order accepted/resting is not filled; trade/position differ.([GitHub][10])                                                        | `pending` exists, but model is too coarse.([GitHub][2])                                                                       | Risk treats coarse state incorrectly.                                                              | Separate accepted/resting/trade/position records.                                           | High        |
| 20 | Partial fill support                            | FAIL               | S0/S1    | Partial fill is normal CLOB behavior.                                                                                             | No partial state; fill tracker collapses statuses.([GitHub][18])                                                              | Wrong size/exposure, bad cancel/retry.                                                             | Track filled/remaining per trade.                                                           | High        |
| 21 | Cancel / cancel failure handling                | FAIL               | S1       | Cancel response is a state transition; failure may mean still live.                                                               | Direct cancel exists; failure not first-class lifecycle.([GitHub][15])                                                        | Believes order gone while exchange order remains.                                                  | `CANCEL_REQUESTED`, `CANCELLED`, `CANCEL_FAILED`, reconciliation.                           | High        |
| 22 | Cancel-replace lifecycle                        | PARTIAL            | S1       | Market makers cancel/replace with stale-order discipline.([GitHub][12])                                                           | Exit path cancels stale order before retry but ignores hard failure.([GitHub][19])                                            | Overlapping exits or orphan orders.                                                                | Atomic cancel-replace state machine.                                                        | Medium-high |
| 23 | Active order reconciliation                     | PARTIAL            | S1       | Official SDK exposes open orders.([Polymarket Documentation][7])                                                                  | Orphan cleanup exists, but no full reconciliation.([GitHub][1])                                                               | Unknown/resting orders remain unresolved.                                                          | Periodic open-order reconciliation keyed by command/order IDs.                              | High        |
| 24 | Trade/fill reconciliation                       | FAIL               | S1       | SDK has `get_trades`; user WS can emit trade activity.([GitHub][3])                                                               | No full trade reconciliation loop found.                                                                                      | Fills missed; positions drift.                                                                     | Poll trades + user websocket + raw trade table.                                             | High        |
| 25 | Position truth and exchange truth alignment     | PARTIAL            | S1       | Position truth must be reconciled with exchange/onchain.                                                                          | Data API position sync exists.([GitHub][21])                                                                                  | Position-only reconciliation is too late; threshold may hide dust.                                 | Reconcile order→trade→position, not position alone.                                         | High        |
| 26 | Balance/allowance/proxy/funder handling         | PARTIAL            | S1       | SDK exposes balance/allowance; proxy/funder matters.([GitHub][42])                                                                | Startup balance check and sell collateral checks exist; signature type hardcoded.([GitHub][14])                               | Live post fails or trades under wrong funder.                                                      | Config validation, allowance gate, signature-type tests.                                    | High        |
| 27 | Crash recovery mid-order                        | FAIL               | S0       | Recovery needs durable command + exchange reconciliation.                                                                         | No command before entry post; no client id.([GitHub][1])                                                                      | Restart cannot link exchange order to intent.                                                      | Event-sourced execution journal and recovery scanner.                                       | Very high   |
| 28 | Execution journal / ledger completeness         | PARTIAL            | S1/S0    | Journal must include every side-effect attempt and raw result.                                                                    | Chronicler/canonical events exist but missing critical states.([GitHub][22])                                                  | Audit cannot reconstruct order truth.                                                              | Add order-command/order-event/trade-fill ledger.                                            | High        |
| 29 | Risk caps and kill switch                       | PARTIAL            | S1       | Kill switch must stop entry and manage live orders.                                                                               | GREEN gate exists; RED runtime incomplete.([GitHub][43])                                                                      | RED leaves pending orders/resting exposure.                                                        | Immediate cancel-all + review state + exit plan.                                            | High        |
| 30 | RED/degraded risk behavior                      | MISLEADING-DOCS    | S1       | Docs claim cancel pending / sweep positions.                                                                                      | Runtime marks positions for exit; not immediate cancel sweep.([GitHub][4])                                                    | Operators think risk has acted when it has not.                                                    | Align docs and runtime; test RED behavior.                                                  | Very high   |
| 31 | Simulated/live venue parity                              | FAIL               | S1       | Simulated venue evidence must encode spread/liquidity/partial/precision.                                                                   | Obsolete simulated execution was skipped/removed in execution test.([GitHub][36])                                                   | Happy-path simulated confidence, live failure.                                                                 | Build fake CLOB harnesses or explicitly forbid simulated evidence from authorizing live confidence.    | High        |
| 32 | Raw API payload storage                         | FAIL/PARTIAL       | S2       | Postmortem requires raw request/response/orderbook/trades.                                                                        | Weather raw/provenance strong; CLOB order/trade raw weak.([GitHub][23])                                                       | Cannot prove what was traded or why.                                                               | Raw CLOB payload tables.                                                                    | High        |
| 33 | Normalized schema correctness                   | PARTIAL            | S2       | Normalization must preserve identifiers and versioning.                                                                           | Market/trade/position schemas exist but incomplete for execution.([GitHub][23])                                               | Identifier conflation and unreplayable history.                                                    | Add schema-versioned normalized execution entities.                                         | Medium-high |
| 34 | Derived feature provenance                      | PARTIAL            | S2       | Derived features must link to raw source snapshots.                                                                               | Weather/probability artifacts stronger than trading microstructure.([GitHub][23])                                             | Edge decision cannot be replayed exactly.                                                          | Link every decision to market/book/weather snapshots.                                       | Medium      |
| 35 | Timestamp separation                            | PARTIAL            | S2       | Observation/exchange/fetch/local timestamps differ.                                                                               | Weather timestamps strong; exchange timestamps weak.([GitHub][27])                                                            | Stale data and settlement/replay ambiguity.                                                        | Separate exchange_ts, fetch_ts, local_ts, decision_ts.                                      | High        |
| 36 | Weather forecast ingestion truth                | PARTIAL            | S2       | Forecast issue/valid/available/fetch times matter.                                                                                | DB schema suggests forecast timing separation.([GitHub][23])                                                                  | Incomplete verification from static inspection.                                                    | Add fixture-based forecast provenance tests.                                                | Medium      |
| 37 | Weather observation truth                       | PASS/PARTIAL       | S2       | Observations need authority, source tier, unit, revisions.                                                                        | Writer enforces authority/provenance/unit/time/revision.([GitHub][27])                                                        | Remaining risk is external source behavior, not obvious code gap.                                  | Keep source-specific resolved-market validation.                                            | Medium-high |
| 38 | Settlement source truth                         | PARTIAL/PASS       | S1/S2    | Settlement source may differ from forecast/observation source.                                                                    | Harvester uses source-family-correct observations and QUARANTINED path.([GitHub][26])                                         | Needs more resolved-market corpus and low/high expansion.                                          | Store exchange resolution snapshots and resolved fixtures.                                  | Medium-high |
| 39 | Unit semantics: Fahrenheit/Celsius              | PASS               | S2       | Unit must be explicit; rounding differs by source.                                                                                | `SettlementSemantics` encodes measurement_unit and rounding.([GitHub][25])                                                    | No obvious unit conflation in inspected settlement code.                                           | Maintain unit assertions in all writers.                                                    | High        |
| 40 | Timezone/local-day semantics                    | PARTIAL            | S2       | Weather market settlement is local-day sensitive.                                                                                 | Observation writer validates local/UTC/time basis.([GitHub][27])                                                              | Needs more resolved-market fixtures for local-day boundaries.                                      | Add DST/local-day resolved tests.                                                           | Medium-high |
| 41 | Rounding/bin/discrete settlement semantics      | PARTIAL/PASS       | S2       | Bin containment depends on rounding rule.                                                                                         | WMO half-up and source-specific rules encoded.([GitHub][25])                                                                  | Not fully proven across historical markets.                                                        | Corpus tests with official resolved outcomes.                                               | Medium-high |
| 42 | Daily max vs daily low separation               | PARTIAL            | S2       | High/low markets have different settlement metrics.                                                                               | Harvester path is high/localday-max oriented.([GitHub][26])                                                                   | Low-market expansion could reuse wrong metric.                                                     | Separate metric contract per market type.                                                   | Medium      |
| 43 | Resolved-market validation tests                | PARTIAL            | S2       | Historical resolved markets are required regression fixtures.                                                                     | Harvester tests exist, but not broad external corpus.([GitHub][38])                                                           | Synthetic tests miss edge settlement semantics.                                                    | Add resolved-market fixture suite.                                                          | Medium      |
| 44 | API error/retry/backoff/rate-limit handling     | FAIL               | S1       | 425/429 require retry/backoff; errors are not semantic rejects.([Polymarket Documentation][8])                                    | Exceptions collapse to `rejected`; no typed backoff in money path.([GitHub][2])                                               | False rejection, duplicate submit, rate-limit storms.                                              | Typed errors, exponential backoff, unknown state.                                           | High        |
| 45 | Websocket/polling design                        | FAIL               | S1       | User WS gives order/trade activity; serious adapters use it.([Polymarket Documentation][9])                                       | No production user WS path found.                                                                                             | Missed fills/order updates.                                                                        | Add WS user channel plus polling fallback.                                                  | High        |
| 46 | Logging/audit/explainability                    | PARTIAL            | S2       | Audit trail must contain raw side effects.                                                                                        | Chronicler exists; raw CLOB payloads missing.([GitHub][22])                                                                   | Cannot reconstruct after loss.                                                                     | Raw request/response/trade/book event log.                                                  | High        |
| 47 | Secret/wallet/key safety                        | PARTIAL            | S2/S1    | Keys/funder/signature must match account type.                                                                                    | Keychain/funder used; startup balance check exists; signature type hardcoded.([GitHub][15])                                   | Wrong signer/funder or allowance failure.                                                          | Configurable signature/funder, dry auth verification, no hardcoded live smoke token.        | High        |
| 48 | CLI/script accidental live-trading guard        | PARTIAL            | S2       | Live scripts need explicit blast-radius guard.                                                                                    | `live_smoke_test.py` posts real order when enabled; `force_lifecycle.py` mutates state.([GitHub][30])                         | Accidental live order or polluted DB.                                                              | Script-level confirmation, isolated DB, max notional, no hardcoded production tokens.       | High        |
| 49 | Tests realism                                   | TESTS-INSUFFICIENT | S1       | Tests must cover timeout/partial/cancel/crash/stale metadata.                                                                     | Current execution tests miss these; obsolete simulated execution skipped.([GitHub][36])                                                              | CI proves happy path only.                                                                         | Add failure-mode suite before live.                                                         | Very high   |
| 50 | Docs/authority/agent-use safety                 | MISLEADING-DOCS    | S1       | Docs must route agents to runtime truth and forbid unsafe shortcuts.                                                              | README/AGENTS overclaim RED/reconciliation/lifecycle.([GitHub][4])                                                            | Future agent edits wrong layer and preserves S0 bugs.                                              | Docs must state live-disabled blockers and true runtime gaps.                               | Very high   |

---

## 7. Major findings

### F-001 — External side effect occurs before durable command

**Severity:** S0
**Pre-live blocker:** yes

**External benchmark:** CLOB orders are signed, submitted to an operator, matched offchain, and settled onchain; submit is a real external side effect, not a local calculation.([Polymarket Documentation][5])

**Zeus evidence:** `execute_discovery_phase()` calls `execute_intent()` before `materialize_position()` and before DB/canonical event writes. `execute_intent()` calls `_live_order()`, which calls `PolymarketClient.place_limit_order()`.([GitHub][1])

**Observed behavior:** order submission can happen before any durable command or local position exists.

**Expected behavior:** `INTENT_CREATED → COMMAND_PERSISTED → PREFLIGHT → SIGNING → SUBMITTING → ACK/UNKNOWN`.

**Failure mechanism:** crash/timeout/DB failure after external post but before local persistence creates untracked live order.

**Realistic scenario:** Zeus posts BUY YES, HTTP returns timeout or process dies; local DB has no position/order; next cycle sees opportunity again and posts second BUY.

**Why normal review misses it:** The code has logging and later materialization, so it “looks structured.” The fatal detail is the order of side effect vs persistence.

**Fix design:** Add `order_command` table and event `COMMAND_PERSISTED` before SDK signing/posting. A command cannot be retried unless its previous exchange state is reconciled.

**Tests required:** timeout-after-submit, crash-after-submit-before-materialize, DB-write failure after post, duplicate-submit prevention.

**Confidence:** very high.

---

### F-002 — Submit exceptions become semantic rejection

**Severity:** S0
**Pre-live blocker:** yes

**External benchmark:** Polymarket API errors include retryable/operational states such as 425 and 429; HTTP failure does not prove no side effect.([Polymarket Documentation][8])

**Zeus evidence:** `_live_order()` catches exceptions and returns `OrderResult(status="rejected", reason=str(e))`.([GitHub][2])

**Observed behavior:** unknown network state is collapsed into terminal local rejection.

**Expected behavior:** timeout / transport failure after submit attempt becomes `SUBMIT_TIMEOUT_UNKNOWN`, forces no duplicate submit, and triggers active order/trade reconciliation.

**Failure mechanism:** Zeus may submit again because it believes prior order was rejected.

**Fix design:** Typed exceptions: `SIGNING_FAILED`, `PRE_SUBMIT_FAILED`, `SUBMIT_TIMEOUT_UNKNOWN`, `POST_SUBMIT_ERROR_UNKNOWN`, `TERMINAL_REJECTED`. Only pre-submit deterministic validation errors can be terminal rejection.

**Tests required:** monkeypatch SDK `post_order` to raise after synthetic side-effect; assert UNKNOWN, not rejected.

**Confidence:** very high.

---

### F-003 — No exchange-proven idempotency

**Severity:** S0
**Pre-live blocker:** yes

**External benchmark:** Production trading retries require idempotency or reconciliation by order identifier/signature/open orders/trades. Official SDK exposes order lookup, open orders, and trades.([GitHub][3])

**Zeus evidence:** `OrderResult` has an `idempotency_key`, but there is no evidence it is posted to exchange or used to deduplicate after unknown submit.([GitHub][2])

**Observed behavior:** local idempotency exists in name only.

**Expected behavior:** durable command ID, deterministic order hash/signature, exchange order ID once known, and a single-flight lock.

**Failure mechanism:** duplicate order after timeout/restart.

**Fix design:** persist `command_id`, `client_order_id`, signed order hash, nonce, raw signed payload before post; retry only by reconcile-first policy.

**Tests required:** duplicate submit retry test and restart test.

**Confidence:** high.

---

### F-004 — Partial fill is not a first-class state

**Severity:** S0/S1
**Pre-live blocker:** yes

**External benchmark:** CLOB orders can rest and partially fill; FAK explicitly fills available size then cancels remainder, and GTC may rest indefinitely.([Polymarket Documentation][6]) Public Polymarket position tooling warns that order filled, trade confirmed, position updated, and sellable on-chain are distinct states.([GitHub][10])

**Zeus evidence:** `OrderResult` lacks partial state. Fill tracker polls order status and marks filled/void using coarse status categories.([GitHub][2])

**Observed behavior:** no separate `filled_size`, `remaining_size`, `resting_size`, `trade_ids`, `cancelled_remaining`.

**Expected behavior:** `RESTING → PARTIALLY_FILLED → CANCEL_REQUESTED → CANCELLED_REMAINDER` or `FILLED`.

**Failure mechanism:** risk and exit use original order size or coarse filled state; exposure is wrong.

**Fix design:** add `order_event` and `trade_fill` tables; reconcile `filled_size` from trades, not only order status.

**Tests required:** partial fill then cancel, partial fill then market close, partial fill then restart.

**Confidence:** high.

---

### F-005 — Cancel failure is not modeled as dangerous

**Severity:** S1
**Pre-live blocker:** yes

**External benchmark:** Cancel endpoints are authenticated order operations; cancel failure may mean order remains live.([Polymarket Documentation][44])

**Zeus evidence:** `cancel_order()` directly calls SDK cancel; exit lifecycle cancels stale order before retry but failure is not a full lifecycle state.([GitHub][15])

**Observed behavior:** no robust `CANCEL_REQUESTED / CANCELLED / CANCEL_FAILED / REVIEW_REQUIRED`.

**Failure mechanism:** Zeus posts replacement sell while old sell remains live, or believes risk was reduced when order still rests.

**Fix design:** cancel request event, raw cancel response, active-order reconciliation after cancel, no replacement until old state is terminal or explicitly reviewed.

**Tests required:** cancel returns error; cancel timeout; cancel partial remainder.

**Confidence:** high.

---

### F-006 — Reconciliation is position-level, not execution-level

**Severity:** S1/S0 depending on incident
**Pre-live blocker:** yes

**External benchmark:** Official SDK exposes open orders and trades; serious adapters maintain user channels and reconcile active orders, trades, and positions.([GitHub][3])

**Zeus evidence:** `chain_reconciliation.py` reconciles positions from Data API; `cleanup_orphan_open_orders` is orphan cancellation, not full execution reconciliation.([GitHub][21])

**Observed behavior:** Zeus can eventually notice positions, but cannot reconstruct why/how an order was accepted, partially filled, cancelled, or duplicated.

**Failure mechanism:** after crash/timeout, Zeus may quarantine or cancel orphan orders, but it cannot safely link them to strategy commands or explain fills.

**Fix design:** exchange reconciliation service with runs: active orders, order details, trades, positions, balances. Compare to journal, generate repair events.

**Tests required:** unknown submit recovered via open order; unknown submit recovered via trade; unknown submit unresolved → review.

**Confidence:** high.

---

### F-007 — Gamma/Data/CLOB boundary is leaky

**Severity:** S1
**Pre-live blocker:** yes

**External benchmark:** Gamma/Data are metadata/data APIs; CLOB is executable order venue.([Polymarket Documentation][40])

**Zeus evidence:** scanner uses Gamma discovery and `get_current_yes_price` fallback from Gamma event data; evaluator does use CLOB book, but Zeus does not persist CLOB executable market snapshot.([GitHub][16])

**Observed behavior:** Gamma metadata can still influence executable decisions without a hard CLOB truth boundary.

**Failure mechanism:** Gamma says open/active or has price-like fields while CLOB book is stale, closed, or non-tradable.

**Fix design:** create `ExecutableMarketSnapshot` from CLOB only; no trading decision can pass without it.

**Tests required:** Gamma active / CLOB closed; Gamma price available / CLOB empty; stale book age.

**Confidence:** high.

---

### F-008 — YES/NO protection is partial, not command-level

**Severity:** S0/S1
**Pre-live blocker:** yes for live scale

**External benchmark:** In prediction markets, token ID is the executable asset. Outcome labels and token IDs must be frozen together.

**Zeus evidence:** `_extract_outcomes()` checks outcome labels and may swap Yes/No mapping; but order command does not persist a full raw mapping snapshot before side effect.([GitHub][16])

**Observed behavior:** discovery has guardrails, execution trusts already-derived token ids.

**Failure mechanism:** stale/malformed market metadata or future parser edit can invert YES/NO silently.

**Fix design:** command must include `condition_id`, question, outcome, token_id, no_token_id, full `clobTokenIds`, raw outcomes, and invariant hash.

**Tests required:** outcome order `[No, Yes]`, malformed labels, missing token, changed token map after discovery.

**Confidence:** medium-high.

---

### F-009 — Precision/tick/min-size discipline is delegated but not owned

**Severity:** S1
**Pre-live blocker:** yes

**External benchmark:** Official SDK `create_order()` retrieves tick size and negRisk and validates price; orderbook summary includes `min_order_size`, `tick_size`, `neg_risk`, timestamp/hash.([GitHub][3])

**Zeus evidence:** Zeus uses SDK `create_order()`, but preflight does not persist tick/min-size/negRisk or test boundary cases deeply.([GitHub][15])

**Observed behavior:** precision correctness is partly outsourced to SDK; local decision/risk layer may not know why an order would fail.

**Failure mechanism:** live rejection, wrong rounding, or false confidence in execution size.

**Fix design:** preflight must fetch and persist tick/min/order size/negRisk; local rounding must be deterministic and tested.

**Tests required:** tick 0.001 vs 0.01, min order size, rounding edge, price outside bounds.

**Confidence:** high.

---

### F-010 — RED risk behavior is authority-drifted

**Severity:** S1
**Pre-live blocker:** yes

**External benchmark:** A kill switch must do what the operator thinks it does: block new orders, cancel live orders, and enter review for unknown states.

**Zeus evidence:** docs claim RED cancels all pending and sweeps positions; runtime RED path mainly marks active positions for force exit and does not immediately post sell/cancel in-cycle.([GitHub][4])

**Observed behavior:** risk language is stronger than money-path behavior.

**Failure mechanism:** operator believes exposure was stopped; live orders remain.

**Fix design:** implement actual RED executor: cancel all open orders, block all entries, reconcile, classify unknowns, then controlled exits.

**Tests required:** RED with pending entry, RED with resting exit, RED cancel failure, RED unknown submit.

**Confidence:** high.

---

### F-011 — Polymarket trading raw payloads are not preserved like weather data

**Severity:** S2/S1
**Pre-live blocker:** yes for auditability

**External benchmark:** Postmortem requires raw order request/response, raw book, raw trade, raw position snapshot.

**Zeus evidence:** weather observation writer has rich provenance and raw payload/revision controls; CLOB order path normalizes and discards too much.([GitHub][27])

**Observed behavior:** weather data is treated forensically; trading data is treated operationally.

**Failure mechanism:** after loss, Zeus cannot prove whether wrong token, stale book, SDK rejection, partial fill, or duplicate submit caused it.

**Fix design:** raw CLOB snapshots and event log tables.

**Tests required:** assert raw payload exists for every decision/order/reconciliation event.

**Confidence:** high.

---

### F-012 — Tests prove happy paths, not live-money safety

**Severity:** S1
**Pre-live blocker:** yes

**External benchmark:** Live trading CI must inject exchange failures, partial fills, stale metadata, precision errors, crash/restart, and simulated/live venue divergence.

**Zeus evidence:** `test_executor.py` skips obsolete simulated execution and tests exit rounding/missing order id; typed boundary test only validates malformed limit price; settlement tests are stronger than execution tests.([GitHub][36])

**Observed behavior:** CI can pass while S0 execution bugs remain.

**Fix design:** add deterministic fake CLOB with side-effect ledger, failure injection, restart simulation, and resolved-market fixtures.

**Confidence:** very high.

---

## 8. Order lifecycle forensic reconstruction

### 8.1 Current Zeus lifecycle, as code actually behaves

```text
Gamma discovery
  -> parse event / outcomes / token ids
  -> CLOB orderbook check in evaluator
  -> create ExecutionIntent in memory
  -> execute_intent()
      -> compute shares
      -> _live_order()
          -> PolymarketClient.place_limit_order()
              -> SDK create_order()
              -> SDK post_order()
          -> if orderId: OrderResult(status="pending")
          -> if exception: OrderResult(status="rejected")
  -> only after result:
      -> materialize_position()
      -> log_trade_entry()
      -> log_execution_report()
      -> canonical/chronicler writes
  -> fill_tracker polls status
      -> mark filled / void / timeout cancel
  -> chain_reconciliation syncs Data API positions
  -> settlement harvester later processes resolved markets
```

Fatal property: **the first durable order state can occur after the external order post.**

### 8.2 Production-grade Polymarket lifecycle

```text
INTENT_CREATED
  -> MARKET_IDENTITY_SNAPSHOT_PERSISTED
  -> PREFLIGHT_STARTED
  -> PREFLIGHT_PASSED / PREFLIGHT_FAILED
  -> COMMAND_PERSISTED
  -> SIGNING_STARTED
  -> SIGNING_FAILED / SIGNED_ORDER_PERSISTED
  -> SUBMITTING
  -> SUBMIT_TIMEOUT_UNKNOWN / ACCEPTED / TERMINAL_REJECTED
  -> RESTING
  -> PARTIALLY_FILLED
  -> FILLED
  -> CANCEL_REQUESTED
  -> CANCELLED / CANCEL_FAILED
  -> RECONCILIATION_REQUIRED
  -> RECONCILED
  -> POSITION_CONFIRMED
  -> SETTLEMENT_PENDING
  -> SETTLED
  -> REDEEM_REQUESTED
  -> REDEEMED / REDEEM_FAILED / REVIEW_REQUIRED
```

### 8.3 Gap list

Missing or insufficient transitions:

```text
COMMAND_PERSISTED before submit
SIGNED_ORDER_PERSISTED
SUBMIT_TIMEOUT_UNKNOWN
ACCEPTED vs RESTING
PARTIALLY_FILLED
REMAINING_CANCEL_REQUESTED
CANCEL_FAILED
CANCEL_REPLACE_BLOCKED
CLOSED_MARKET_UNKNOWN
TRADE_CONFIRMED
POSITION_CONFIRMED_FROM_EXCHANGE
RECONCILED_BY_OPEN_ORDERS
RECONCILED_BY_TRADES
RECONCILED_BY_POSITION
REVIEW_REQUIRED
REDEEM_REQUESTED
REDEEMED
```

### 8.4 Proposed state machine

```text
INTENT_CREATED
  -> PREFLIGHT_FAILED
  -> COMMAND_PERSISTED
  -> SIGNING_FAILED
  -> SIGNED
  -> SUBMITTING
      -> SUBMIT_TIMEOUT_UNKNOWN
      -> ACCEPTED
      -> TERMINAL_REJECTED
  -> RESTING
      -> PARTIALLY_FILLED
      -> FILLED
      -> CANCEL_REQUESTED
          -> CANCELLED
          -> CANCEL_FAILED
  -> CLOSED_MARKET_UNKNOWN
  -> RECONCILED
      -> POSITION_CONFIRMED
      -> POSITION_DRIFT
      -> REVIEW_REQUIRED
  -> SETTLED
  -> REDEEMED
```

### 8.5 Proposed event schema

Minimum event fields:

```text
event_id
event_type
sequence_no
schema_version

command_id
client_order_id
exchange_order_id
signed_order_hash
idempotency_key

market_id
condition_id
event_slug
market_slug
question
token_id
no_token_id
clob_token_ids_raw
outcome
side
order_type

price
size
notional
filled_size
remaining_size
fee_rate_bps

status
previous_status
verdict
confidence

raw_request
raw_signed_order
raw_response
raw_orderbook_snapshot
raw_trade_payload
raw_position_payload
error_payload

created_at
preflight_at
signed_at
submitted_at
exchange_timestamp
fetch_timestamp
local_timestamp
reconciled_at

source
api_endpoint
sdk_version
signature_type
funder
wallet_address
```

---

## 9. Data storage / provenance audit

### What Zeus stores now

Zeus stores substantial weather and strategy provenance: forecast snapshots, observations, observation instants, coverage ledgers, settlement truth, position/trade decision summaries, token price logs, and canonical position events. Weather observation writer enforces authority, provenance, data version, source-tier, units, timestamp basis, and revision behavior.([GitHub][23])

### What Zeus fails to store for trading

Missing or insufficient:

```text
raw Gamma event snapshot used for each command
raw CLOB orderbook snapshot with timestamp/hash/min_order_size/tick_size/negRisk
raw signed order payload
raw SDK post_order response
raw cancel request/response
raw get_order response
raw get_trades response
raw user websocket event
raw Data API position snapshot used for reconciliation
order command before side effect
client/exchange order identity linkage
schema version for exchange payloads
```

### Raw vs normalized vs derived separation

Weather subsystem mostly honors this:

```text
raw observation / revision
  -> normalized observation instant
  -> settlement truth / derived feature
```

Trading subsystem does not:

```text
CLOB book / post_order / status
  -> normalized small OrderResult
  -> local position
```

That is not enough for forensic replay.

### Proposed tables/entities

| Entity                       | Purpose                                                                                                      |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `gamma_market_snapshot`      | Raw Gamma event/market payload; conditionId, slug, clobTokenIds, outcomes, active/closed flags.              |
| `clob_market_snapshot`       | CLOB executable book and metadata: token_id, bids/asks, timestamp, hash, tick_size, min_order_size, negRisk. |
| `order_command`              | Durable pre-side-effect command. One row before signing/posting.                                             |
| `order_event`                | Every lifecycle transition with raw request/response/error.                                                  |
| `trade_fill`                 | Exchange trade/fill events, partial fills, fees, timestamps.                                                 |
| `exchange_position_snapshot` | Raw Data API / position snapshot, balance, sellable amount.                                                  |
| `reconciliation_run`         | Every run comparing local orders/trades/positions vs exchange truth.                                         |
| `settlement_source_snapshot` | External weather/UMA/exchange resolution payload for resolved markets.                                       |

### Replay implication

Current Zeus can replay weather reasoning better than it can replay trading truth. In a live incident, it can often explain the weather value, but not prove whether a CLOB order was accepted, partially filled, cancelled, duplicated, or associated with the correct command.

---

## 10. Polymarket integration audit

### Endpoint correctness

Zeus hits the right broad surfaces: CLOB for book/order/cancel/status, Data API for positions, Gamma for market discovery. That is not sufficient. The boundary must be enforced: Gamma cannot be executable truth, Data API positions cannot substitute order/trade reconciliation, and CLOB orderbook must carry freshness/tradability metadata.([Polymarket Documentation][40])

### SDK correctness

Using `py-clob-client` is the correct starting point. The SDK itself handles order construction, tick validation, negRisk, posting, cancel, orders, trades, and balances.([GitHub][3]) Zeus fails at the wrapper/state layer, not mainly at raw signing.

### Auth correctness

Hardcoded `signature_type=2` is unacceptable for general live-money readiness. Polymarket distinguishes EOA, proxy, and safe signature types.([Polymarket Documentation][45]) Zeus must validate that configured private key/funder/signature type matches the actual wallet/proxy.

### Order type correctness

Zeus effectively runs limit orders with implicit default behavior. It does not own GTC/FOK/FAK/GTD semantics. This is survivable only if live trading remains disabled until GTC resting order lifecycle is implemented.

### Market discovery correctness

Token extraction has some YES/NO defense, but the command path does not persist the full identity snapshot. That is insufficient.

### Rate limit/backoff correctness

Official docs identify retry/backoff requirements for engine restart and rate limits.([Polymarket Documentation][8]) Zeus’s money path collapses errors into `rejected`; that is wrong.

### WebSocket/polling choice

No production user WebSocket was found. Given Polymarket supports user order/trade channels and serious adapters use WS plus polling, Zeus needs either WS or a documented polling loop with equivalent guarantees.([Polymarket Documentation][9])

### Recommended wrapper structure

```text
src/execution/polymarket_gateway.py
  - typed API calls only
  - no strategy logic
  - no local position mutation
  - returns typed GatewayEvent

src/execution/order_journal.py
  - command persistence
  - lifecycle events
  - raw payload storage

src/execution/reconciler.py
  - open orders
  - order details
  - trades
  - positions
  - balances
  - unresolved unknowns

src/execution/order_state.py
  - state machine
  - transition guards
```

---

## 11. Weather settlement audit

Weather settlement is not the main reason Zeus fails live-money readiness. It is materially stronger than the execution subsystem.

### Strengths

`SettlementSemantics` encodes resolution source, measurement unit, precision, rounding rule, and finalization semantics. It validates finite settlement values and applies source-specific rules.([GitHub][25])

The harvester requires UMA-resolved status, checks outcome label order, uses source-family-correct observations, and writes VERIFIED or QUARANTINED settlement truth with provenance.([GitHub][26])

The observation writer rejects missing provenance/authority, validates units and timestamp basis, handles revision tracking, and enforces source-tier consistency.([GitHub][27])

### Remaining risks

1. **Daily high vs daily low separation is not fully proven.** Current harvester logic appears high/localday-max oriented. Low-market expansion must not reuse it blindly.
2. **Resolved-market corpus is insufficient.** Synthetic tests are useful but not enough. Zeus needs many historical resolved Polymarket weather markets with official resolution payloads.
3. **Exchange resolution snapshot preservation must be explicit.** Zeus should store raw Gamma/UMA resolution payload, not only reconstructed settlement.
4. **Local-day/DST edge cases need regression tests.** The writer has the right fields; correctness must be proven against resolved edge cases.

Verdict: **weather provenance PARTIAL/PASS; execution provenance FAIL**.

---

## 12. Mandatory failure drills

### Drill 1 — Timeout after order post

**Code path traced:**

```text
cycle_runtime.execute_discovery_phase()
  -> executor.execute_intent()
  -> executor._live_order()
  -> PolymarketClient.place_limit_order()
  -> SDK post_order()
  -> exception
  -> OrderResult(status="rejected")
```

**Zeus currently does:** marks exception as `rejected`; if result is rejected, entry materialization does not create a durable local position/order.([GitHub][1])

**Correct behavior:** persisted command already exists; state becomes `SUBMIT_TIMEOUT_UNKNOWN`; no duplicate submit; reconciliation queries open orders/order details/trades/positions.

**Survives?** **No. S0 fail.**

**Missing:** pre-submit command table, unknown state, reconcile-by-command/order hash, duplicate-submit lock, tests.

---

### Drill 2 — Partial fill then cancel

**Code path traced:** live order → `pending` → fill tracker polls order status → coarse filled/void handling; exit lifecycle has cancel/retry but no partial state.

**Zeus currently does:** no first-class `PARTIALLY_FILLED` with remaining amount.([GitHub][2])

**Correct behavior:** filled shares update position; remaining order tracked separately; cancel confirmation captured; risk uses filled quantity only.

**Survives?** **No. S1/S0 fail.**

**Missing:** trade-fill table, remaining size, cancel remainder state, active order reconciliation, tests.

---

### Drill 3 — Wrong YES/NO token

**Code path traced:** Gamma scanner parses outcomes and `clobTokenIds`; execution intent carries `token_id` / `no_token_id`; order path uses token id.

**Zeus currently does:** some label checking/swap in scanner, but no command-level raw snapshot/invariant.([GitHub][16])

**Correct behavior:** explicit outcome-token validation; snapshot question/rules/outcomes; no index-only inference; tests catch inversion.

**Survives?** **Partial only. Not live-safe.**

**Missing:** persisted token-map snapshot and tests for `[No, Yes]`, malformed labels, stale metadata.

---

### Drill 4 — Gamma says market open, CLOB not executable

**Code path traced:** scanner discovers via Gamma; evaluator checks CLOB book; monitor has Gamma price fallback.

**Zeus currently does:** CLOB book check is good, but no CLOB market-state snapshot/freshness guard; Gamma fallback can still influence runtime.([GitHub][16])

**Correct behavior:** executable truth must come from CLOB; stale/empty/non-tradable CLOB blocks order.

**Survives?** **Partial; fail as production gate.**

**Missing:** CLOB tradability status, book timestamp max-age, no Gamma trading price fallback.

---

### Drill 5 — Market close while resting order exists

**Code path traced:** order pending/resting; chain sync positions; orphan open order cleanup may cancel unknown open orders; settlement harvester later processes resolved events.

**Zeus currently does:** no dedicated “market closed while resting order exists” state transition. Orphan cleanup is not equivalent.([GitHub][1])

**Correct behavior:** detect close, cancel if possible, mark closed-market unknown/cancelled, stop entries, reconcile fills/positions, activate settlement path.

**Survives?** **No for full lifecycle; partial mitigation only.**

**Missing:** market-close watcher tied to open orders, CLOB state check, terminal/review states.

---

### Drill 6 — Process crash during pending order

**Code path traced:** crash can happen after SDK post but before `materialize_position()` and event writes.

**Zeus currently does:** after restart, position reconciliation may eventually find position if filled; open-order cleanup may see orphan. But there is no durable command/order identity to link the exchange order back to the strategy intent.([GitHub][1])

**Correct behavior:** journal reconstructs command and unknown submit; reconciliation restores active order/trade/position; no duplicate.

**Survives?** **No. S0 fail.**

**Missing:** event-sourced order journal, client order id/signature hash, restart reconciliation.

---

### Drill 7 — Weather settlement correction

**Code path traced:** observation writer stores provenance/revisions; settlement harvester uses source-correct observations and QUARANTINED/VERIFIED.

**Zeus currently does:** this is one of the better areas. Revisions and source correctness are explicitly modeled.([GitHub][27])

**Correct behavior:** forecast, observation, settlement, exchange resolution separated; revisions stored; degraded/unverified blocks VERIFIED.

**Survives?** **Mostly, with remaining verification needs.**

**Missing:** broad resolved-market corpus, raw exchange resolution snapshots, high/low split tests.

---

### Drill 8 — Unit/timezone/bin error

**Code path traced:** `SettlementSemantics` encodes units/rounding; observation writer validates local/UTC/time basis.

**Zeus currently does:** has strong typed handling for F/C, rounding, local/UTC timestamp fields.([GitHub][25])

**Correct behavior:** explicit local-day semantics, unit normalization, exact market rounding rule, tests against resolved markets.

**Survives?** **Partial. Code is promising; proof corpus is incomplete.**

**Missing:** DST/local-day resolved fixtures, one-bin boundary tests, low-market tests.

---

### Drill 9 — Simulated venue evidence passes, live fails

**Code path traced:** execution test marks obsolete simulated execution skipped; live path uses real SDK semantics but tests do not simulate spread/liquidity/partial/precision.

**Zeus currently does:** simulated execution is not a meaningful safety gate.([GitHub][36])

**Correct behavior:** simulated venue evidence encodes CLOB semantics enough to prevent false confidence.

**Survives?** **No. S1 fail.**

**Missing:** fake exchange with orderbook, partial fills, cancel failures, tick/min-size, latency, rate-limit, restart.

---

### Drill 10 — AI agent edits wrong subsystem

**Code path traced:** README/AGENTS route agents through canonical DB/risk/lifecycle docs; runtime truth is in executor/cycle_runtime/fill_tracker/exit_lifecycle.

**Zeus currently does:** docs overclaim production behaviors and do not prominently state live blockers.([GitHub][4])

**Correct behavior:** docs must route agents to true money path and forbid treating docs as runtime truth.

**Survives?** **No. MISLEADING-DOCS.**

**Missing:** red-box “live disabled until order journal/reconciler exists,” file ownership map, forbidden shortcuts, docs drift tests.

---

## 13. Tests and CI gates required

| Test                            | File Path                                              | Purpose                                          | Fixture Needed                                  | Failure Condition                              | Priority |
| ------------------------------- | ------------------------------------------------------ | ------------------------------------------------ | ----------------------------------------------- | ---------------------------------------------- | -------- |
| Token ID mapping                | `tests/execution/test_polymarket_token_identity.py`    | Verify condition/question/outcome/token snapshot | Gamma event with `clobTokenIds`, outcomes       | Missing snapshot or wrong token used           | P0       |
| YES/NO inversion                | `tests/execution/test_yes_no_inversion.py`             | Catch `[No, Yes]` and malformed outcome order    | Realistic Gamma payload variants                | BUY YES uses NO token                          | P0       |
| Stale metadata / CLOB closed    | `tests/execution/test_market_state_preflight.py`       | Gamma active but CLOB stale/closed blocks order  | Fake Gamma + fake CLOB state                    | Order posted                                   | P0       |
| Order precision                 | `tests/execution/test_order_precision.py`              | Tick/min-size/price rounding                     | Book with tick 0.001/0.01, min size             | SDK/post called with invalid price/size        | P0       |
| Market order semantics          | `tests/execution/test_market_order_semantics.py`       | Guard future market-order addition               | BUY amount USDC, SELL amount shares             | Market BUY interprets amount as shares         | P1       |
| Timeout after submit            | `tests/execution/test_submit_timeout_unknown.py`       | Unknown side-effect handling                     | Fake SDK records post then raises timeout       | Status becomes rejected or retry allowed       | P0       |
| Duplicate submit                | `tests/execution/test_duplicate_submit_idempotency.py` | Ensure retry does not duplicate unknown command  | Same command restarted                          | Second post before reconciliation              | P0       |
| Partial fill                    | `tests/execution/test_partial_fill_lifecycle.py`       | Track filled/remaining                           | Fake order status/trade payload                 | Risk uses original size as filled              | P0       |
| Cancel failure                  | `tests/execution/test_cancel_failure_review.py`        | Cancel failure stays dangerous                   | Fake cancel error/timeout                       | Order marked safe/cancelled                    | P0       |
| Crash recovery                  | `tests/execution/test_crash_recovery_pending_order.py` | Restart after post-before-ack                    | Durable command + fake open order               | Command duplicates or cannot link              | P0       |
| Active order reconciliation     | `tests/execution/test_active_order_reconciliation.py`  | Align local journal with exchange open orders    | Open-order payloads                             | Unknown exchange order ignored                 | P0       |
| Trade reconciliation            | `tests/execution/test_trade_reconciliation.py`         | Derive fills from trades                         | Fake `get_trades` payloads                      | Fill not recorded or duplicated                | P0       |
| Market close with resting order | `tests/execution/test_market_close_resting_order.py`   | Close detection/cancel/review                    | Market close + open order                       | Strategy keeps entering or state stays pending | P0       |
| Simulated/live venue parity               | `tests/execution/test_simulated_venue_live_parity.py`  | Simulator matches core CLOB semantics            | Fake orderbook, partial, precision              | Simulated venue fills impossible live order              | P1       |
| Settlement rounding             | `tests/settlement/test_resolved_market_rounding.py`    | Historical resolved bin validation               | Resolved market corpus                          | Reconstructed bin differs from exchange        | P1       |
| Timezone/local day              | `tests/settlement/test_timezone_local_day.py`          | Local-day/DST correctness                        | Station near UTC boundary                       | Wrong target local day                         | P1       |
| Degraded data blocks entry      | `tests/risk/test_degraded_data_blocks_execution.py`    | Ensure unverified source blocks entry            | DATA_DEGRADED risk state                        | Order posted                                   | P0       |
| Docs authority drift            | `tests/docs/test_authority_drift.py`                   | Prevent docs claiming unsupported behavior       | README/AGENTS text + runtime capability markers | Docs say RED cancels but test capability false | P1       |

---

## 14. Remediation roadmap

### Phase 0 — Live-money stop conditions

**Scope:** prevent new live orders until execution journal exists.

**Files likely touched:**

```text
src/main.py
src/engine/cycle_runtime.py
src/execution/executor.py
scripts/live_smoke_test.py
README.md
AGENTS.md
```

**Implementation design:**

```text
- Add ZEUS_LIVE_EXECUTION_ENABLED=0 default.
- Block execute_intent() unless order journal feature gate active.
- Keep read-only discovery, weather, settlement, and reconciliation allowed.
- live_smoke_test requires explicit max notional, isolated DB, manual confirmation env.
```

**Acceptance criteria:**

```text
No automatic live CLOB post can occur through daemon without explicit gate.
All docs state live execution is blocked pending order journal/reconciler.
```

**Tests:** `test_live_execution_gate_blocks_order`.

**Rollback/blast radius:** low; blocks trading only.

**Do not touch:** strategy model, probability calibration, weather ingestion.

---

### Phase 1 — API / SDK / market identity alignment

**Scope:** make market identity and CLOB executable truth explicit.

**Files likely touched:**

```text
src/data/polymarket_client.py
src/data/market_scanner.py
src/engine/evaluator.py
src/state/db.py
```

**Implementation design:**

```text
- Add ExecutableMarketSnapshot.
- Persist raw Gamma and raw CLOB book metadata.
- Validate condition_id, clobTokenIds, outcome labels, token_id, no_token_id.
- Fetch tick_size, min_order_size, negRisk.
- Configurable signature_type/funder validation.
```

**Acceptance criteria:**

```text
No order command can be created without persisted token/outcome snapshot and fresh CLOB book.
```

**Tests:** token mapping, YES/NO inversion, stale metadata, tick/min-size.

**Do not touch:** settlement harvester except to share snapshot patterns.

---

### Phase 2 — Execution truth re-architecture

**Scope:** journal/state machine/reconciliation.

**Files likely touched / added:**

```text
src/execution/order_state.py
src/execution/order_journal.py
src/execution/polymarket_gateway.py
src/execution/reconciler.py
src/execution/executor.py
src/execution/fill_tracker.py
src/execution/exit_lifecycle.py
src/state/db.py
```

**Implementation design:**

```text
- Persist COMMAND before signing/posting.
- Store signed order payload/hash.
- Submit transitions to SUBMITTING.
- Timeout becomes SUBMIT_TIMEOUT_UNKNOWN.
- Accepted order becomes RESTING unless immediate fill proven.
- Trades update filled_size/remaining_size.
- Cancel has request/success/failure states.
- Reconciler repairs unknowns from open orders/trades/positions.
```

**Acceptance criteria:**

```text
Timeout-after-submit never becomes rejected.
No duplicate submit without reconciliation.
Partial fill then cancel produces correct filled and remaining state.
Crash/restart test passes.
```

**Rollback/blast radius:** high; core money path. Run against fake CLOB first.

**Do not touch:** model thresholds or strategy expansion.

---

### Phase 3 — Data / provenance hardening

**Scope:** raw payloads, schema versioning, snapshots.

**Files likely touched:**

```text
src/state/db.py
architecture/*.sql
src/state/chronicler.py
src/execution/order_journal.py
```

**Implementation design:**

```text
- Add raw JSON payload columns/tables.
- Add schema_version/source/api_endpoint/sdk_version.
- Link every decision to weather snapshot + market snapshot + orderbook snapshot.
```

**Acceptance criteria:**

```text
Every order event has raw_request/raw_response or explicit null reason.
Every edge decision is replayable.
```

**Tests:** raw-payload existence and replay.

---

### Phase 4 — Weather settlement validation

**Scope:** prove settlement semantics against real resolved markets.

**Files likely touched:**

```text
src/execution/harvester.py
src/contracts/settlement_semantics.py
tests/settlement/*
fixtures/resolved_markets/*
```

**Implementation design:**

```text
- Build resolved-market fixture corpus.
- Store raw exchange/UMA resolution payload.
- Split HIGH_LOCALDAY_MAX and LOW_LOCALDAY_MIN contracts.
- Add DST/local-day/bin-boundary tests.
```

**Acceptance criteria:**

```text
Resolved market corpus reconstructs winning bin exactly or QUARANTINED with explanation.
```

**Do not touch:** execution state machine.

---

### Phase 5 — Simulated/Live Venue Parity And CI Gates

**Scope:** fake exchange simulator and failure injection.

**Files likely touched:**

```text
tests/execution/fakes/fake_clob.py
tests/execution/*
src/execution/*
```

**Implementation design:**

```text
- Fake CLOB with orderbook, matching, partial fills, cancel failure, rate limits.
- Fake CLOB harness uses the same order state machine shape as live-facing execution tests.
- CI fails on docs/runtime authority drift.
```

**Acceptance criteria:**

```text
All mandatory failure drills are executable tests.
Simulated venue evidence cannot fill impossible live orders or authorize live confidence.
```

---

### Phase 6 — Agent docs and authority hardening

**Scope:** prevent future AI-agent drift.

**Files likely touched:**

```text
README.md
AGENTS.md
workspace_map.md
src/execution/AGENTS.md
docs/live_readiness.md
tests/docs/test_authority_drift.py
```

**Implementation design:**

```text
- Add "docs are not runtime truth" and live blockers at top.
- Route money-path edits to executor/cycle_runtime/order_journal/reconciler.
- List forbidden shortcuts.
- Add docs drift tests.
```

**Acceptance criteria:**

```text
No doc claims RED/order/reconciliation behavior unless runtime capability test exists.
```

---

## 15. Coding-agent implementation packet

```text
Branch:
  forensic/live-execution-safety-gates

First files to read:
  README.md
  AGENTS.md
  workspace_map.md
  src/main.py
  src/data/polymarket_client.py
  src/data/market_scanner.py
  src/engine/evaluator.py
  src/engine/cycle_runtime.py
  src/execution/executor.py
  src/execution/fill_tracker.py
  src/execution/exit_lifecycle.py
  src/execution/collateral.py
  src/state/db.py
  src/state/chain_reconciliation.py
  architecture/2026_04_02_architecture_kernel.sql
  tests/test_executor.py
  tests/test_executor_typed_boundary.py

Files to modify first:
  src/execution/executor.py
  src/engine/cycle_runtime.py
  src/state/db.py
  README.md
  AGENTS.md

Files to add:
  src/execution/order_state.py
  src/execution/order_journal.py
  src/execution/polymarket_gateway.py
  src/execution/reconciler.py
  tests/execution/test_submit_timeout_unknown.py
  tests/execution/test_duplicate_submit_idempotency.py
  tests/execution/test_partial_fill_lifecycle.py
  tests/execution/test_cancel_failure_review.py
  tests/execution/test_crash_recovery_pending_order.py
  tests/execution/test_polymarket_token_identity.py
  tests/execution/test_market_state_preflight.py

Files not to modify in this phase:
  src/strategy/*
  src/signal/*
  src/calibration/*
  src/analysis/*
  ML/model/probability threshold code
  weather forecast modeling code

Implementation sequence:
  1. Add live execution gate default-off.
  2. Add order state enum and allowed transitions.
  3. Add order_command and order_event schema.
  4. Change entry path so COMMAND_PERSISTED occurs before signing/posting.
  5. Change _live_order exception handling:
       - pre-submit validation error -> PREFLIGHT_FAILED or SIGNING_FAILED
       - post attempt timeout/transport error -> SUBMIT_TIMEOUT_UNKNOWN
       - API terminal rejection -> TERMINAL_REJECTED
  6. Add fake CLOB with side-effect ledger.
  7. Add timeout-after-submit test; make it fail first.
  8. Add duplicate submit prevention.
  9. Add active-order/trade reconciliation skeleton.
  10. Add partial fill and cancel failure state tests.
  11. Update docs to say live execution remains blocked until P0 tests pass.

Acceptance criteria:
  - No path can call SDK post_order without an existing order_command row.
  - Timeout after post never returns OrderResult(status="rejected").
  - Unknown submit cannot be retried until reconciler resolves it.
  - Partial fill stores filled_size and remaining_size.
  - Cancel failure leaves order dangerous and REVIEW_REQUIRED or CANCEL_FAILED.
  - RED risk blocks entries and attempts cancel-all through state machine.
  - Tests cover all P0 failure drills.
  - Docs do not claim live readiness.

Forbidden shortcuts:
  - Do not swallow SDK exceptions into "rejected".
  - Do not infer no side effect from network timeout.
  - Do not use Gamma price as executable trading truth.
  - Do not mark accepted order as filled without trade/position proof.
  - Do not retry unknown submit by posting another order.
  - Do not add simulated fills that ignore spread/liquidity/tick/min-size.
  - Do not update README to claim fixed behavior before tests exist.
  - Do not mutate strategy/model thresholds while fixing execution safety.

Stop conditions:
  - If command persistence cannot be made atomic before post_order, stop and report.
  - If SDK cannot expose enough identity to reconcile unknown submit, stop and design deterministic signed-order hash tracking.
  - If signature_type/funder cannot be validated, keep live disabled.
  - If partial fills cannot be obtained from order/trade APIs, keep live disabled and require manual review path.

How to report uncertainty:
  - State exact file/function inspected.
  - State exact missing external payload or credential.
  - Mark UNKNOWN-HIGH-RISK, not PASS.
```

---

## 16. Not-now list

Do not spend engineering cycles on these before Phase 0–3 are complete:

```text
fancy ML
better Bayesian calibration
cross-market optimization
market making
daily low strategy expansion
new city expansion
autonomous live trading
UI polish
Discord alert polish
PnL analytics dashboards
copy-trading features
portfolio optimization
```

These are premature because Zeus cannot yet prove order truth after timeout, partial fill, cancel failure, or crash.

---

## 17. Unknowns and required verification

### Credentials / live calls required

```text
Polymarket API credentials
wallet private key in safe test account
configured funder/proxy address
signature_type owner decision
USDC allowance state
conditional token allowance state
small isolated test market/token
```

### Live API verification required

```text
create_or_derive_api_creds works with configured wallet/funder
get_balance_allowance for collateral and conditional tokens
get_orders returns expected open orders
get_order returns full status payload shape
get_trades payload shape and fill status semantics
cancel response shape and failure behavior
Data API positions threshold/dust behavior
CLOB orderbook timestamp/hash/tick/min-size fields in production
```

### Historical data required

```text
resolved Polymarket weather markets across cities
markets with outcome order [Yes, No] and [No, Yes]
markets near bin boundary
markets near UTC/local-day boundary
markets with revised observation source
markets with low-temperature semantics
Gamma raw event payloads before and after close
```

### Owner decisions required

```text
Which wallet type is canonical: EOA, proxy, or safe?
Whether market orders are forbidden until separately implemented.
Maximum live test notional.
Whether live smoke tests may touch production DB.
Whether RED should cancel all open orders immediately or only block entries and schedule exits.
```

### What could not be verified here

I did not execute local `pytest` or credentialed live Polymarket calls. The verdict is based on public source inspection plus official Polymarket/SDK/open-source benchmarks. That limitation does not soften the core verdict: the S0 blockers are visible in static code paths.

### Evidence that would change the verdict

Only these would materially change live-readiness status:

```text
A hidden/runtime order journal that persists command before post_order.
A reconciler that resolves unknown submits through open orders/trades.
Tests proving timeout-after-submit, duplicate submit, partial fill, cancel failure, and crash recovery.
Runtime RED path that actually cancels pending/resting orders and records failures.
Raw CLOB request/response/trade/book payload storage.
Verified signature/funder/allowance configuration for the live wallet.
```

Absent that evidence, Zeus must be treated as **unsafe for live-money autonomous execution**.

[1]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/engine/cycle_runtime.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/engine/cycle_runtime.py"
[2]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/executor.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/executor.py"
[3]: https://raw.githubusercontent.com/Polymarket/py-clob-client/main/py_clob_client/client.py "https://raw.githubusercontent.com/Polymarket/py-clob-client/main/py_clob_client/client.py"
[4]: https://raw.githubusercontent.com/fitz-s/zeus/main/README.md "raw.githubusercontent.com"
[5]: https://docs.polymarket.com/trading/overview "https://docs.polymarket.com/trading/overview"
[6]: https://docs.polymarket.com/trading/orders/create "https://docs.polymarket.com/trading/orders/create"
[7]: https://docs.polymarket.com/api-reference/trade/get-user-orders "https://docs.polymarket.com/api-reference/trade/get-user-orders"
[8]: https://docs.polymarket.com/resources/error-codes "https://docs.polymarket.com/resources/error-codes"
[9]: https://docs.polymarket.com/market-data/websocket/overview "https://docs.polymarket.com/market-data/websocket/overview"
[10]: https://github.com/tosmart01/polymarket-position-watcher "https://github.com/tosmart01/polymarket-position-watcher"
[11]: https://github.com/nautechsystems/nautilus_trader/blob/develop/docs/integrations/polymarket.md "https://github.com/nautechsystems/nautilus_trader/blob/develop/docs/integrations/polymarket.md"
[12]: https://github.com/lorine93s/polymarket-market-maker-bot "https://github.com/lorine93s/polymarket-market-maker-bot"
[13]: https://github.com/fitz-s/zeus "GitHub - fitz-s/zeus · GitHub"
[14]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/main.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/main.py"
[15]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/data/polymarket_client.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/data/polymarket_client.py"
[16]: https://github.com/fitz-s/zeus/blob/main/src/data/market_scanner.py "zeus/src/data/market_scanner.py at main · fitz-s/zeus · GitHub"
[17]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/engine/evaluator.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/engine/evaluator.py"
[18]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/fill_tracker.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/fill_tracker.py"
[19]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/exit_lifecycle.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/exit_lifecycle.py"
[20]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/collateral.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/collateral.py"
[21]: https://github.com/fitz-s/zeus/raw/refs/heads/main/src/state/chain_reconciliation.py "raw.githubusercontent.com"
[22]: https://github.com/fitz-s/zeus/raw/refs/heads/main/src/state/chronicler.py "raw.githubusercontent.com"
[23]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/state/db.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/state/db.py"
[24]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/architecture/2026_04_02_architecture_kernel.sql "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/architecture/2026_04_02_architecture_kernel.sql"
[25]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/contracts/settlement_semantics.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/contracts/settlement_semantics.py"
[26]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/harvester.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/execution/harvester.py"
[27]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/data/observation_instants_v2_writer.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/data/observation_instants_v2_writer.py"
[28]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/riskguard/risk_level.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/riskguard/risk_level.py"
[29]: https://github.com/fitz-s/zeus/tree/main/tests "https://github.com/fitz-s/zeus/tree/main/tests"
[30]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/scripts/live_smoke_test.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/scripts/live_smoke_test.py"
[31]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/scripts/force_lifecycle.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/scripts/force_lifecycle.py"
[32]: https://raw.githubusercontent.com/fitz-s/zeus/main/AGENTS.md "raw.githubusercontent.com"
[33]: https://raw.githubusercontent.com/fitz-s/zeus/main/workspace_map.md "raw.githubusercontent.com"
[34]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/requirements.txt "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/requirements.txt"
[35]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/riskguard/riskguard.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/riskguard/riskguard.py"
[36]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/tests/test_executor.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/tests/test_executor.py"
[37]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/tests/test_executor_typed_boundary.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/tests/test_executor_typed_boundary.py"
[38]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/tests/test_harvester_dr33_live_enablement.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/tests/test_harvester_dr33_live_enablement.py"
[39]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/tests/test_force_exit_review.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/tests/test_force_exit_review.py"
[40]: https://docs.polymarket.com/api-reference/introduction "https://docs.polymarket.com/api-reference/introduction"
[41]: https://raw.githubusercontent.com/Polymarket/py-clob-client/main/py_clob_client/clob_types.py "https://raw.githubusercontent.com/Polymarket/py-clob-client/main/py_clob_client/clob_types.py"
[42]: https://github.com/Polymarket/py-clob-client "GitHub - Polymarket/py-clob-client: Python client for the Polymarket CLOB · GitHub"
[43]: https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/engine/cycle_runner.py "https://raw.githubusercontent.com/fitz-s/zeus/refs/heads/main/src/engine/cycle_runner.py"
[44]: https://docs.polymarket.com/api-reference/trade/cancel-single-order "https://docs.polymarket.com/api-reference/trade/cancel-single-order"
[45]: https://docs.polymarket.com/api-reference/authentication "https://docs.polymarket.com/api-reference/authentication"
