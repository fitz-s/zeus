# ZEUS REALITY-SEMANTICS ASYMMETRY AUDIT

## 1. Executive verdict

**LIVE-BLOCKING SEMANTIC RISK FOUND**

我没有把这次当作 lint / typing / generic bug review。审计路线按项目自己的 authority 文件开始：`AGENTS.md` 要求先读钱路径、probability chain、settlement mechanics、lifecycle/risk/durable rules；workspace map 也要求先读 `AGENTS.md`、topology digest 和 scoped AGENTS 后再进入具体目录。Zeus README 自述的钱路径是 forecast → calibration → posterior → FDR → Kelly → execution → monitoring，这正是本次审计的主路径。([GitHub][1])

证据边界：我能通过 GitHub web/raw 和官方 Polymarket/CLOB 文档读取项目与 venue 语义；本地 `git clone` 在容器内因 DNS 无法解析 GitHub 失败，所以我没有运行 repo 测试、没有运行全仓 grep 脚本，也没有声称已执行 topology doctor。所有结论按“已读代码路径 + 官方文档 + 项目 authority 文件”给出；无法证明的路径标为 **REVIEW_REQUIRED**。

核心裁定：Zeus 已经有若干正确方向的修复迹象，例如 `ExecutionPrice.assert_kelly_safe()`、`ExecutableMarketSnapshotV2`、venue command journal、append-only venue facts。但真实钱路径仍存在多个位置把 **probability / market prior / VWMP / executable ask / sell bid / submitted limit / fill average / settlement payout** 混成同名字段。最严重的是：Kelly sizing 仍可从 `p_market/VWMP` 派生“fee_adjusted execution price”，executor 仍可从 `p_posterior` 和 `vwmp` 重算 limit，buy-no exit 仍可用 `p_market/current_market_price` 当 sell proceeds。官方 CLOB 现实语义要求 token-specific order book；BUY 可执行价是 token 的 ask/depth，SELL 可执行价是 token 的 bid/depth，order book 带 `asset_id/token_id`、timestamp、hash、tick、min order、neg risk 等事实。([GitHub][2])

## 2. Top 10 hidden asymmetry findings

### F-01 — P0 live-blocking — Kelly sizing accepts relabeled probability/VWMP as executable cost

**Real-world object mismatch:** `entry_price` / `vwmp` is first a market-prior / VWMP-like probability-space scalar, then becomes `ExecutionPrice(...).with_taker_fee()` and passes Kelly safety as if it were executable entry cost. `ExecutionPrice` itself documents the D3 gap: `market_analysis.py` sets `entry_price=p_market[i]`, while Polymarket execution price is ask + taker fee + slippage.([GitHub][2])

**Files/functions involved:** `src/strategy/market_analysis.py::compute_edges`, `src/engine/evaluator.py::_size_at_execution_price_boundary`, `src/strategy/kelly.py::kelly_size`, `src/contracts/execution_price.py`.

**Exact fields:** `p_market`, `entry_price`, `vwmp`, `p_posterior`, `fee_rate`, `size_usd`, `kelly_fraction`.

**Forward trace:** CLOB top bid/ask → `vwmp()` → `p_market` → posterior blend → `BinEdge.entry_price = p_market` and `vwmp = p_market` → `_size_at_execution_price_boundary()` creates `ExecutionPrice(value=entry_price, price_type="implied_probability", fee_deducted=False)` → `.with_taker_fee()` → `kelly_size()`.

**Backward trace:** Actual submitted order requires selected token, BUY side, limit price, order type, size, tick, min order, fee, fresh orderbook hash, and potentially ask-depth walk. Official CLOB exposes orderbook per token with bids/asks, timestamp, hash, min order size, tick size, and neg-risk fields; `getPrice(BUY)` returns best ask and `SELL` returns best bid.([Polymarket Documentation][3])

**Where traces diverge:** `_size_at_execution_price_boundary()` relabels a scalar that originated as `p_market/VWMP` into a Kelly-safe execution price without proving ask/depth/fee/order-policy authority.([GitHub][4])

**Downstream consequence:** Wrong Kelly stake. A quote spread or depth change can change true cost without changing model belief, yet Zeus can size from a probability-like scalar. This is live-blocking because it affects `size_usd` before risk and submission.

**Minimal reproduction / counterfactual test:** Hold `p_raw`, `p_cal`, and market prior constant. Change selected token ask from `0.50` to `0.58` with shallow depth and same `p_market/VWMP`. Expected: posterior unchanged, Kelly size decreases or blocks. Current path risk: posterior/edge stays tied to `p_market`, while sizing can remain based on relabeled `entry_price`.

**Recommended fix class:** Split `MarketPriorEstimate` from `ExecutableEntryCost`. Kelly accepts only `ExecutableEntryCostCertificate` containing token_id, side=BUY, orderbook_hash, captured_at, best_ask, depth-walk VWAP for intended notional, fee basis, order_type, tick/min-order checks.

**Requires:** code change, test change, schema/live gate.

---

### F-02 — P0 live-blocking — Executor can recompute final limit from posterior/VWMP instead of consuming immutable executable cost

**Real-world object mismatch:** `limit_price` should be the final executable venue limit for selected token/side/order policy, measured after executable snapshot binding. In `create_execution_intent`, it can be derived from `compute_native_limit_price(HeldSideProbability(edge_context.p_posterior), NativeSidePrice(edge.vwmp))`, with optional dynamic ask repricing. That makes the executor a second price authority instead of a consumer of the selected economic object. The project’s own execution lifecycle reference describes this path.([GitHub][5])

**Files/functions involved:** `src/execution/executor.py::create_execution_intent`, `src/execution/executor.py::execute_intent`, `src/contracts/execution_intent.py`, `src/venue/polymarket_v2_adapter.py::create_submission_envelope`.

**Exact fields:** `limit_price`, `edge.vwmp`, `edge_context.p_posterior`, `repriced_limit_price`, `target_size_usd`, `executable_snapshot_id`.

**Forward trace:** FDR/Kelly selects edge → evaluator computes `size_usd` → executor recomputes native limit from posterior/VWMP or uses dynamic ask → intent generated → venue envelope uses that limit.

**Backward trace:** Submitted payload is token_id + side + price + size + order_type; official CLOB orders are limit orders, and even “market orders” are limit orders designed to execute immediately.([Polymarket Documentation][6])

**Where traces diverge:** The same selected hypothesis can be sized using one cost scalar and submitted using a later executor-derived limit scalar. The intent does not force immutable all-in cost basis from the same snapshot used for sizing.

**Downstream consequence:** A corrected entry-cost split can still be bypassed by legacy limit recomputation. This is exactly the “corrected entry but legacy submit fallback” class.

**Minimal reproduction / counterfactual test:** Build an edge with `p_posterior=0.62`, `vwmp=0.50`, selected ask/depth certificate max safe limit `0.54`, then call executor without `repriced_limit_price`. Expected: reject because no immutable final limit certificate. Current risk: executor derives a limit from posterior/VWMP.

**Recommended fix class:** Executor must be fail-closed unless intent carries `final_limit_price`, `cost_basis_price`, `snapshot_id`, `orderbook_hash`, `order_policy`, `fee_basis`, and a `cost_certificate_id` produced before Kelly/risk.

**Requires:** code change, test change, live gate, small schema addition.

---

### F-03 — P0 live-blocking — Buy-NO exit EV uses market/probability vector as sell proceeds

**Real-world object mismatch:** For a held NO token, sell proceeds are token-specific `SELL` executable bid/depth, not `p_market[0]`, not VWMP, and not a posterior prior. Official CLOB `getPrice(SELL)` returns the current best bid for a token; orderbook is token-specific.([Polymarket Documentation][7])

**Files/functions involved:** `src/execution/exit_triggers.py::_evaluate_buy_no_exit`, `src/state/portfolio.py::Position._buy_no_exit`, `src/engine/monitor_refresh.py::build EdgeContext`, `src/engine/cycle_runtime.py::_build_exit_context`.

**Exact fields:** `current_edge_context.p_market[0]`, `current_market_price`, `best_bid`, `last_monitor_best_bid`, `p_posterior`, `entry_price`.

**Forward trace:** Monitor fetches held token bid/ask, then computes `current_p_market` as bid in day0 or VWMP otherwise; it recomputes posterior and packages `p_market=[current_p_market]`; buy-no exit uses `current_p_market` as sell value.([GitHub][8])

**Backward trace:** To exit a held token, the economic object is the held token SELL quote: selected token_id, side=SELL, best_bid/depth, fees, order policy, timestamp, snapshot hash.

**Where traces diverge:** Buy-YES exit receives/uses `best_bid`; buy-NO exit uses `current_market_price` / `p_market` as if it were a sell bid. The code comment even asserts the symmetry is semantically OK because fee formula is symmetric, but fee symmetry does not make VWMP/probability equal executable sell bid.([GitHub][9])

**Downstream consequence:** Exit can incorrectly hold a deteriorating NO position or sell a valuable one because EV gate compares hold value against a non-executable scalar.

**Minimal reproduction / counterfactual test:** Held NO token has bid `0.42`, ask `0.62`, VWMP `0.55`, posterior `0.50`. Expected sell value uses `0.42`; current non-day0 path can use `0.55`, making sell look better than executable reality.

**Recommended fix class:** Introduce `ExitExecutableQuote(held_token_id, side=SELL, best_bid, bid_depth, fee_basis, captured_at, orderbook_hash)` and require buy-YES and buy-NO exit EV to consume the same object shape.

**Requires:** code change, test change, live gate.

---

### F-04 — P1 promotion-blocking, P0 if used live for exit/sizing — Monitor quote changes mutate posterior/evidence

**Real-world object mismatch:** `p_posterior` should represent a probability belief formed from model/calibration/market-prior evidence. Executable quote movement should affect cost, exit proceeds, and quote freshness—not silently become the market prior unless explicitly declared as a prior estimator. In `monitor_refresh`, `current_p_market` comes from held-token orderbook bid/VWMP and is then blended into `current_p_posterior = alpha * p_cal_native + (1-alpha) * current_p_market`.([GitHub][8])

**Files/functions involved:** `src/engine/monitor_refresh.py::recompute_native_probability`, `src/engine/monitor_refresh.py::build EdgeContext`, `src/execution/exit_triggers.py`, `src/state/portfolio.py`.

**Exact fields:** `current_p_market`, `current_p_posterior`, `last_monitor_prob`, `last_monitor_edge`, `forward_edge`, `p_market`.

**Forward trace:** New held-token quote → `current_p_market` → posterior recomputation → forward edge → exit trigger.

**Backward trace:** Exit should ask: “what is my belief if I hold?” and separately “what can I sell for now?” Those are not the same real-world object.

**Where traces diverge:** The monitor treats current executable-ish quote as both probability-market prior and exit quote carrier.

**Downstream consequence:** A pure liquidity/spread/depth change can alter posterior and evidence logs. Future learning/reporting may interpret a quote-state artifact as model/market-prior evidence.

**Minimal reproduction / counterfactual test:** Keep forecast/calibration constant; widen spread from bid/ask `0.49/0.51` to `0.40/0.60`. Expected: posterior unchanged if market prior unchanged; exit executable value changes. Current path can change `p_market`, `p_posterior`, and `forward_edge`.

**Recommended fix class:** Split monitor surfaces into `MarketPriorAtMonitor` and `HeldTokenExecutableSellQuote`. Posterior recompute may use a prior estimator only if it is tagged as such and not the same field as executable quote.

**Requires:** code change, test change, reporting relabel.

---

### F-05 — P1 promotion-blocking, P0 if live exit reconciliation relies on it — `market_id` changes object identity between entry and exit command paths

**Real-world object mismatch:** `market_id` alternates between Gamma/condition market identity and token identity. In entry materialization, `market_id=decision.tokens["market_id"]`; in an exit command path the executor comments that `market_id_for_cmd = intent.token_id` because `ExitOrderIntent` carries no market_id. Venue command schema has both `market_id` and `token_id`, so using token_id as market_id corrupts lineage.([GitHub][10])

**Files/functions involved:** `src/engine/cycle_runtime.py::materialize_position`, `src/execution/executor.py::execute_exit`, `src/state/db.py::venue_commands`, `src/state/venue_command_repo.py::insert_command`.

**Exact fields:** `market_id`, `condition_id`, `token_id`, `selected_outcome_token_id`, `position_id`, `decision_id`.

**Forward trace:** Selected hypothesis identifies city/date/bin/market/tokens → position stores market_id and token ids → exit intent should preserve condition/market plus selected held token.

**Backward trace:** Venue command row should prove which market condition and which token were sold.

**Where traces diverge:** Exit lacks condition/market identity and overloads `market_id` with token id.

**Downstream consequence:** Reconciliation, reporting, settlement command joins, and “same economic object” proofs can falsely link or fail to link entry/exit/settlement.

**Minimal reproduction / counterfactual test:** Two positions share no condition but token-looking IDs exist in `market_id` for exits. Query command journal by `market_id` to reconstruct condition lineage. Expected: condition/Gamma market id. Current risk: token id.

**Recommended fix class:** `ExitOrderIntent` must carry `condition_id`, `gamma_market_id`, `question_id`, `yes_token_id`, `no_token_id`, and `held_token_id`; DB invariant: `venue_commands.market_id` cannot equal `token_id` unless explicitly tagged compatibility/test.

**Requires:** code change, schema invariant/test.

---

### F-06 — P1 promotion-blocking, REVIEW_REQUIRED for live reachability — Compatibility venue envelope can fabricate market identity

**Real-world object mismatch:** A venue submission envelope should preserve venue facts: condition_id, question_id, YES token, NO token, selected token, outcome label, side, tick/min order/neg-risk/fee, and payload hashes. The V2 adapter has a compatibility `submit_limit_order` helper that creates an envelope with placeholders such as `condition_id="legacy:{token_id}"`, yes/no token both equal token, and outcome label YES. The adapter itself says these are compatibility placeholders rather than U1-certified facts.([GitHub][11])

**Files/functions involved:** `src/venue/polymarket_v2_adapter.py::submit_limit_order`, `src/data/polymarket_client.py::place_limit_order`, `src/state/venue_command_repo.py::_assert_envelope_gate`.

**Exact fields:** `condition_id`, `question_id`, `yes_token_id`, `no_token_id`, `selected_outcome_token_id`, `outcome_label`, `envelope_id`.

**Forward trace:** Correct path: executable snapshot → submission envelope → command journal → SDK post.

**Backward trace:** Actual order payload must be provable back to real CLOB token and condition. Official market data docs distinguish Event vs Market and say a market maps to a pair of CLOB token IDs, market address, question ID, and condition ID.([Polymarket Documentation][12])

**Where traces diverge:** Compatibility path can submit with fabricated identity if a live caller bypasses bound envelope. I did not prove live reachability in all modes; classify as **REVIEW_REQUIRED**, but any live reachability is blocking.

**Downstream consequence:** Venue command lineage may claim the order belongs to a fake condition; settlement and reporting cannot prove the submitted token is the selected economic object.

**Minimal reproduction / counterfactual test:** In live mode call `submit_limit_order(token_id=...)` without an executable snapshot. Expected: hard fail. Current compatibility helper can build placeholder envelope.

**Recommended fix class:** Fail-closed live gate: compatibility envelope allowed only in tests/fake venue/paper mode with explicit `compatibility_identity=true`; live requires snapshot-bound envelope.

**Requires:** code change, live gate, test change.

---

### F-07 — P1 promotion-blocking — Buy-NO complement fallback treats separate executable token as mirror price

**Real-world object mismatch:** `buy_no_market_price()` can use `1 - p_market[bin_idx]` for binary markets when `p_market_no` is unavailable. But official CLOB trading is per token; YES and NO have separate token IDs/orderbooks. Complement of a YES prior/VWMP is not proof of NO BUY ask/depth.([GitHub][13])

**Files/functions involved:** `src/strategy/market_analysis.py::buy_no_market_price`, `src/engine/evaluator.py` native NO quote probe flags, `src/execution/executor.py` token routing buy_no → `no_token_id`.

**Exact fields:** `p_market`, `p_market_no`, `buy_no_quote_available`, `no_token_id`, `entry_price`.

**Forward trace:** If native NO quote unavailable and bins <= 2 → `p_market_no = 1 - p_market` → `entry_price=p_market_no` → edge/Kelly/intent.

**Backward trace:** A live buy-NO order needs `no_token_id` BUY ask/depth/freshness.

**Where traces diverge:** Strategy can infer a NO “market price” from YES-side scalar while executor can route to NO token. Executor capability is mistaken for upstream economic evidence.

**Downstream consequence:** Buy-NO can pass edge/sizing using a price that is not executable on the NO token.

**Minimal reproduction / counterfactual test:** YES VWMP `0.40`, NO best ask `0.68`, NO best bid `0.50`. Complement says NO price `0.60`. Expected live buy-NO blocks or uses NO ask/depth `0.68`; current fallback can size at `0.60`.

**Recommended fix class:** Complement allowed only as diagnostic prior with `economic_authority=false`. Live buy-NO requires native NO executable snapshot.

**Requires:** code change, live gate, test change.

---

### F-08 — P1 promotion-blocking — Order policy changes execution economics after sizing

**Real-world object mismatch:** `order_type` and `post_only` are not cosmetic. Polymarket docs distinguish GTC/GTD resting limit orders from FOK/FAK marketable orders, and post-only is only valid for GTC/GTD and rejected if it crosses. Fees are applied at match time and can be maker/taker-market-specific.([Polymarket Documentation][14])

**Files/functions involved:** `src/execution/executor.py`, `src/venue/polymarket_v2_adapter.py::create_submission_envelope`, `src/state/venue_command_repo.py`, risk allocator order policy path.

**Exact fields:** `order_type`, `post_only`, `limit_price`, `size`, `target_size_usd`, `fee_rate`, `fill_quality`.

**Forward trace:** Strategy/Kelly sizes trade → risk/executor selects or passes order policy → adapter submits with order_type/post_only.

**Backward trace:** Actual order economics depend on whether the order rests, crosses, fills immediately, partially fills, or is canceled.

**Where traces diverge:** Sizing/cost basis is computed before order policy is proven as part of the executable cost object.

**Downstream consequence:** Changing from passive GTC to FOK/FAK should change fill/cost telemetry and required depth proof; it must not leave the same Kelly size untouched unless cost certificate explicitly says economics are identical.

**Minimal reproduction / counterfactual test:** Same token, same posterior, same top ask. Compare GTC post-only non-crossing vs FOK crossing the ask with shallow depth. Expected: order policy changes cost certificate and possibly size; posterior unchanged. Current path does not make this contract explicit upstream.

**Recommended fix class:** `OrderPolicy` becomes an input to `ExecutableEntryCostCertificate` before Kelly/risk. Fee/fill assumptions must be tagged maker/taker/unknown.

**Requires:** code change, test change, live gate.

---

### F-09 — P1 promotion-blocking / P2 evidence-corrupting — Position materialization falls back through submitted/edge price and target size

**Real-world object mismatch:** A live position’s `entry_price`, `shares`, and `cost_basis_usd` should be fill-derived or explicitly marked optimistic exposure. `materialize_position()` uses `result.fill_price or result.submitted_price or decision.edge.entry_price`; `shares = result.shares or decision.size_usd / entry_price`; `cost_basis_usd = decision.size_usd`. Fill tracker later updates actual cost when a confirmed/partial fill is observed, but the initial position can exist as pending/optimistic with economics derived from submitted or edge price.([GitHub][10])

**Files/functions involved:** `src/engine/cycle_runtime.py::materialize_position`, `src/execution/fill_tracker.py::_mark_entry_filled`, `_record_partial_entry_observed`, `src/state/portfolio.py::Position`.

**Exact fields:** `entry_price`, `fill_price`, `submitted_price`, `shares`, `size_usd`, `cost_basis_usd`, `entry_fill_verified`.

**Forward trace:** Executor result → position materialized → monitor/exit/settlement may read position fields.

**Backward trace:** Venue trade facts and position lots are the authoritative fill/cost facts once available; before that, exposure should be optimistic and gated.

**Where traces diverge:** The same `entry_price` field can mean fill average, submitted limit, or legacy edge price.

**Downstream consequence:** Exit EV and settlement P&L can operate on target/submitted economics before fill truth lands, especially if partial fills or delayed status occur.

**Minimal reproduction / counterfactual test:** Submit limit `0.55`, partial fill 20 shares at `0.53`, remaining canceled. Expected position cost basis = actual filled notional and shares; any pre-fill row is not eligible for corrected exit/settlement economics. Current initial materialization can start with target size and submitted/edge price.

**Recommended fix class:** Split `entry_price_submitted`, `entry_price_avg_fill`, `cost_basis_target_usd`, `cost_basis_filled_usd`, `shares_submitted`, `shares_filled`, `entry_economics_authority`.

**Requires:** schema change, code change, report/test change.

---

### F-10 — P2 evidence-corrupting, P1 for strategy promotion — Backtest/report/replay can mix corrected and legacy economics

**Real-world object mismatch:** Historical `trade_decisions.price`, `entry_price`, `p_market`, `p_posterior`, token price logs, and `recent_exits` are not all point-in-time executable economics. The schema stores `trade_decisions.price` and probability traces, but there is no mandatory `pricing_semantics_version` / `execution_cost_basis_version` split in the decision table. `profit_validation_replay.py` reconstructs `entry_price`, `size_usd`, and `p_posterior` from recent exits or `trade_decisions`, then simulates exit with token ticks; `harvester.py` computes settlement P&L from `shares = pos.size_usd / pos.entry_price`.([GitHub][15])

**Files/functions involved:** `scripts/profit_validation_replay.py`, `scripts/equity_curve.py`, `src/execution/harvester.py`, `src/state/db.py::trade_decisions`, `probability_trace_fact`, `selection_hypothesis_fact`, `strategy_health`.

**Exact fields:** `trade_decisions.price`, `entry_price`, `size_usd`, `p_market_json`, `p_posterior_json`, `settlement_edge_usd`, `pnl`.

**Forward trace:** Strategy emits selection/economics → persisted decision/report rows → replay/report/health metrics.

**Backward trace:** Corrected live economics require point-in-time executable orderbook/depth and fill facts; model-only diagnostics cannot substitute.

**Where traces diverge:** Reports/replays can recover rows from legacy `price` or `entry_price` and treat them as economic truth.

**Downstream consequence:** Promotion decisions can be based on evidence that mixes model skill, quote prior, submitted limit, and actual fill economics.

**Minimal reproduction / counterfactual test:** Two cohorts: legacy rows with `entry_price=p_market` and corrected rows with executable cost certificate. Expected reports hard-fail mixed aggregation unless explicitly split. Current schema/report paths do not prove that hard split.

**Recommended fix class:** Add cohort/version tags and report hard gates: `pricing_semantics_version`, `execution_cost_basis_version`, `exit_semantics_version`, `fill_authority`, `depth_snapshot_available`.

**Requires:** schema change, report/test change, promotion gate.

## 3. Semantic aliasing table

| field/object                                                      | meaning A                           | meaning B                             | meaning C if present            | first drift point                                               | downstream authority misuse                     | proposed split / contract                                                          | required invariant test                                                                  |
| ----------------------------------------------------------------- | ----------------------------------- | ------------------------------------- | ------------------------------- | --------------------------------------------------------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `p_market`                                                        | market prior / VWMP-like scalar     | current held-token quote proxy        | sell value proxy in buy-NO exit | `market_analysis.compute_edges`; `monitor_refresh`; buy-NO exit | posterior, edge, exit EV all consume same name  | `market_prior_probability`, `quote_mid_or_vwmp_diagnostic`, `sell_executable_bid`  | changing quote changes exit value, not posterior, unless prior estimator version changes |
| `entry_price`                                                     | `p_market` at decision              | submitted limit                       | fill average                    | `market_analysis` → `cycle_runtime.materialize_position`        | Kelly, shares, settlement P&L                   | `entry_prior_price`, `submitted_limit_price`, `avg_fill_price`, `cost_basis_price` | corrected position cannot have `entry_price_authority in {edge,p_market}`                |
| `vwmp`                                                            | weighted midpoint diagnostic        | native-side market price              | executor limit input            | `BinEdge.vwmp` → executor                                       | final limit recomputation from diagnostic       | `market_prior_vwmp` vs `executable_depth_vwap`                                     | executor rejects `vwmp` as limit source                                                  |
| `limit_price`                                                     | derived from posterior/VWMP         | repriced ask-bound limit              | venue payload price             | `create_execution_intent`                                       | final submit differs from selected/sized cost   | `final_limit_price` with `cost_certificate_id`                                     | executor never recomputes limit from posterior/VWMP                                      |
| `size_usd`                                                        | Kelly target notional               | cost basis                            | filled notional                 | evaluator → position → settlement                               | shares/P&L derived from target                  | `target_notional_usd`, `submitted_notional_usd`, `filled_cost_basis_usd`           | partial fill updates cost basis; reports exclude target-only rows                        |
| `market_id`                                                       | Gamma/market/condition identity     | token id in exit command              | compatibility placeholder       | exit command path / compat envelope                             | joins/reconciliation/settlement lineage corrupt | `gamma_market_id`, `condition_id`, `selected_token_id`                             | DB rejects `market_id == token_id` for live commands                                     |
| `current_market_price`                                            | held-token VWMP                     | held-token bid in day0                | native probability prior        | `monitor_refresh` / `Position._buy_no_exit`                     | sell value and posterior mixed                  | `held_token_sell_bid`, `held_token_quote_mid`, `market_prior_at_monitor`           | buy-NO EV must use explicit SELL bid                                                     |
| `p_posterior`                                                     | calibrated belief with market prior | monitor belief partly from live quote | hold value                      | `monitor_refresh`                                               | liquidity change mutates belief evidence        | `posterior_belief`, `hold_payoff_probability`                                      | quote-only counterfactual cannot change posterior                                        |
| `p_market_no`                                                     | native NO executable-ish quote      | complement fallback `1-p_market`      | absent quote diagnostic         | `buy_no_market_price()`                                         | buy-NO can be sized without NO orderbook        | `no_market_prior`, `no_executable_quote`, `no_complement_diagnostic`               | live buy-NO rejects missing native NO snapshot                                           |
| `snapshot_id` / `decision_snapshot_id` / `executable_snapshot_id` | forecast ensemble snapshot          | executable market snapshot            | decision family id component    | evaluator / executor / command repo                             | one “snapshot” word hides incompatible objects  | `forecast_snapshot_id`, `executable_market_snapshot_id`, `decision_snapshot_id`    | command snapshot must reference executable snapshot, not forecast snapshot               |
| `fee` / `fee_rate`                                                | taker fee formula input             | market info field                     | assumed fee-deducted price      | evaluator and ExecutionPrice                                    | fee-adjusted relabel from non-executable price  | `fee_rate_source`, `fee_applies_to`, `fee_adjusted_executable_cost`                | fee adjustment only after executable base price                                          |
| `price` in reports/schema                                         | trade decision price                | token tick price                      | venue order price               | `trade_decisions.price`, token logs, envelope                   | reports aggregate unlike economics              | versioned price columns                                                            | mixed `price_semantics_version` hard-fails                                               |

## 4. False symmetry register

| assumed symmetry                             | why it is false in reality                                                         | code path relying on it                        | consequence                                                   | required explicit asymmetry                                                     |
| -------------------------------------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| YES price ↔ `1 - NO price`                   | Separate outcome tokens have separate orderbooks, spreads, depth, and freshness    | `buy_no_market_price()` complement fallback    | buy-NO sized at non-executable price                          | live NO requires NO token BUY ask/depth                                         |
| Buy-YES exit ↔ Buy-NO exit                   | YES path uses `best_bid`; NO path can use `current_market_price`/VWMP              | `exit_triggers`, `Position._buy_no_exit`       | wrong exit EV                                                 | both directions consume `ExitExecutableQuote(side=SELL)`                        |
| Market prior ↔ executable quote              | Market prior is evidence; quote is venue liquidity                                 | `monitor_refresh` posterior recompute          | quote change mutates belief                                   | prior estimator object separate from quote object                               |
| Entry ↔ exit economics                       | Entry buys ask/depth; exit sells bid/depth; fees/order policy differ               | executor/exit                                  | corrected entry can still have legacy exit                    | separate entry/exit cost certificates                                           |
| GTC/GTD ↔ FOK/FAK                            | Resting vs immediate/marketable behavior differs; post-only only valid for GTC/GTD | venue adapter/order policy                     | cost basis and fill assumptions drift                         | `OrderPolicy` part of cost certificate                                          |
| Submitted limit ↔ fill average               | Partial fills and improved fills change actual cost                                | `materialize_position`, `fill_tracker`         | P&L and shares wrong pre-confirmation                         | target/submitted/fill fields separate                                           |
| Live ↔ backtest price                        | Historical tick/VWMP may not include executable depth or point-in-time quote       | replay/report scripts                          | promotion evidence corrupt                                    | executable economics requires depth snapshot                                    |
| Condition/market id ↔ token id               | Venue market maps to two tokens; token is not condition                            | exit command path                              | lineage joins corrupt                                         | `condition_id` and `selected_token_id` immutable                                |
| Fee formula symmetry ↔ price object symmetry | Fee may be symmetric in `p*(1-p)`, but bid/ask/depth are not                       | buy-NO exit comment                            | sell value overestimated                                      | fee can be symmetric; quote cannot                                              |
| High ↔ low weather track                     | Daily high and low have different observation fields/source semantics              | project docs acknowledge dual-track separation | settlement/learning can cross-contaminate if identity missing | carry `temperature_metric`, `physical_quantity`, `observation_field` everywhere |

## 5. Time-state and lifecycle drift register

| value                     | valid time                               | reused time                           | freshness/snapshot issue                            | persistence issue                                                                        | required lineage                                          |
| ------------------------- | ---------------------------------------- | ------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `p_market` from orderbook | quote snapshot time                      | decision, monitor, exit, replay       | quote freshness may differ from forecast/prior time | probability trace stores vector but not executable authority                             | source timestamp, orderbook hash, prior-estimator version |
| `p_posterior`             | decision time                            | monitor, hold EV, settlement learning | monitor recomputes from live quote                  | persisted as scalar without belief-source split                                          | posterior version + evidence envelope                     |
| `limit_price`             | intent creation / submit                 | envelope, command, reports            | can be recomputed after sizing                      | command stores price but not cost certificate                                            | cost certificate id + final-limit authority               |
| `size_usd`                | target sizing time                       | position, settlement, P&L             | partial fill can change cost                        | initial position can store target before fill                                            | target/submitted/filled notional split                    |
| `entry_price`             | edge time, submit time, or fill time     | exit and settlement                   | same field crosses lifecycle phases                 | no universal authority tag                                                               | `entry_price_authority` enum                              |
| `best_bid` / `best_ask`   | orderbook capture time                   | exit decision / submit                | monitor may use VWMP instead                        | last monitor fields not enough for corrected EV                                          | held token, side, depth, timestamp, hash                  |
| `snapshot_id`             | forecast snapshot or executable snapshot | command journal / report              | name collision                                      | schema has both `decision_snapshot_id` and executable snapshot but report paths can omit | typed IDs                                                 |
| `fee_rate`                | market-info fetch time                   | Kelly/submit/fill reports             | fee may be unavailable or stale                     | fee stored in envelope; not always in sizing proof                                       | fee source/hash/applies_to                                |
| `order_type`              | risk/executor policy time                | venue submit/fill                     | chosen after sizing                                 | envelope stores it; Kelly proof may not                                                  | order policy in cost certificate                          |
| settlement `shares`       | fill-confirmed state                     | settlement P&L/redeem                 | computed from `size_usd/entry_price` if no fill     | harvester uses fallback                                                                  | lot/fill facts or REVIEW_REQUIRED                         |

## 6. Venue/API mismatch register

| local abstraction                   | venue/API reality                                                                            | missing fact                         | money impact              | required contract/gate                |
| ----------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------ | ------------------------- | ------------------------------------- |
| `p_market` as price                 | CLOB orderbook is per `token_id`, with bids/asks, timestamp, hash, min order, tick, neg-risk | selected token BUY ask/depth         | wrong cost/size           | executable quote certificate          |
| `SELL value = current_market_price` | `getPrice(SELL)` returns best bid for token                                                  | bid/depth side                       | wrong exit EV             | exit quote object                     |
| `NO price = 1 - YES scalar`         | YES/NO are token IDs inside a market, each tradeable via CLOB                                | native NO quote                      | false buy-NO edge         | live NO quote required                |
| `order_type` late binding           | FOK/FAK are marketable/immediate policies; post-only only for GTC/GTD                        | order policy before sizing           | fill/cost mismatch        | policy-aware cost basis               |
| fee-adjusted relabel                | Polymarket fee is applied at match time and market fee rates can be queried                  | fee applicability maker/taker        | incorrect Kelly price     | fee basis in executable cost          |
| `market_id` token fallback          | market/condition/question/token identities are distinct                                      | condition/question on exit           | reconciliation corruption | identity invariant                    |
| compatibility envelope              | real envelope should preserve condition, question, yes/no token ids                          | real market facts                    | false provenance          | live-disable compatibility            |
| top of book only                    | execution for size may walk depth                                                            | depth for target notional            | slippage/partial fill     | depth-walk VWAP or max-notional proof |
| min order/tick as submit-only       | CLOB exposes min order and tick size in market/orderbook info                                | proof before sizing/rounding         | rejected/rounded orders   | tick/min-order in cost certificate    |
| negative risk as metadata           | negative-risk markets can convert NO into YES shares in other markets                        | neg-risk payoff/settlement semantics | payoff identity drift     | explicit neg-risk settlement contract |

Official venue reality checked: CLOB orderbooks are token-specific and include `asset_id`, `timestamp`, `hash`, bids, asks, min order size, tick size, and neg-risk; docs specify bids descending and asks ascending. `getPrice(BUY)` returns best ask; `getPrice(SELL)` returns best bid. Orders are limit orders; FOK/FAK differ from resting GTC/GTD, and post-only has constraints. Fees are applied at match time and are market-specific.([Polymarket Documentation][3])

## 7. Monitor/exit symmetry audit

**Can every corrected entry be exited under corrected semantics?**
No. Not with current evidence. Buy-YES has a path that uses `best_bid` in EV gating, but buy-NO exit uses `current_market_price` / `p_market` as the sell value. A corrected entry-cost certificate does not guarantee corrected exit semantics unless exit consumes a held-token SELL quote certificate.

**Does monitor use held-token executable quote, or does it fall back to probability/market vector?**
It fetches held-token bid/ask, but then repackages the result into `current_p_market`, recomputes posterior with it, and builds an `EdgeContext` whose `p_market` can later be used as exit value. That is a fallback to a probability/market vector interface, not a clean held-token executable quote interface.([GitHub][8])

**Can exit EV distinguish hold value from sell executable value?**
Partially for buy-YES, not sufficiently for buy-NO. Hold value is `shares * posterior` adjusted optionally by `HoldValue`; sell value must be `shares * held_token_sell_bid` minus fees/slippage. Buy-NO currently lets `current_market_price` play both quote/probability roles.

**Are legacy positions segregated from corrected positions?**
Not proven. `Position.entry_price` can be fill, submitted, or edge fallback; reports/replay can recover `entry_price` from recent exits or `trade_decisions`; no universal hard cohort tag was found in the money path evidence I read.

**Is exit symmetry live-blocking?**
Yes. Any live system that can enter buy-NO or monitor corrected entries must not exit via a legacy `p_market/current_market_price` EV path.

## 8. Backtest/reporting evidence integrity audit

**Can historical rows reconstruct point-in-time executable cost?**
No, not generally. `trade_decisions` stores `price`, probabilities, edge, size, status, fill fields, and probability traces, but corrected executable economics require point-in-time selected-token ask/depth/fee/order-policy/fill facts. `ExecutableMarketSnapshotV2` and venue facts help for newer paths, but legacy rows and replay scripts cannot infer missing depth from scalar prices.([GitHub][15])

**Are mixed pricing/economics cohorts hard-failed or only warned?**
I did not find proof of a universal hard fail. The project has tombstone/derived-audit language for backtest DB and has append-only provenance structures, but report/replay scripts still reconstruct from `entry_price`, `size_usd`, ticks, or `trade_decisions`. Treat as **REVIEW_REQUIRED / promotion-blocking** until hard cohort gates are demonstrated.([GitHub][16])

**Can model-only diagnostics be mistaken for live economics?**
Yes. `p_market`, `entry_price`, `price`, `current_market_price`, and `p_market_vector` appear in decision/report/replay contexts where the field names do not force whether the scalar is prior, VWMP, submitted limit, fill average, or executable quote.

**Which reports must be blocked, split, or relabeled?**
Block or split: `profit_validation_replay`, equity curve inputs, strategy health, settlement P&L summaries, shadow replay reports, any report reading `trade_decisions.price`, `Position.entry_price`, `token_price_log.price`, `last_monitor_market_price`, or `p_market_json` as economics. Relabel model-only reports as “diagnostic probability evidence,” not “executable economics,” unless every row has executable snapshot + fill/lot authority.

## 9. Required invariant test suite

1. `test_executable_quote_change_changes_cost_size_limit_not_posterior` — Same model/calibration/prior; change selected token ask/depth. Expected posterior unchanged; cost, size, limit, and execution evidence change.

2. `test_market_prior_change_changes_posterior_not_selected_token_snapshot` — Same executable token quote; change market-prior estimator. Expected posterior/diagnostics change; selected token, executable snapshot, and final limit do not change unless explicit re-decision is created.

3. `test_order_policy_change_changes_cost_basis_not_model_belief` — GTC post-only vs FOK/FAK on same token. Expected cost/fill certificate changes; posterior unchanged.

4. `test_token_side_change_changes_route_not_payoff_semantics_incorrectly` — Buy-YES to buy-NO switches selected token and route; payoff probability is held-side probability, not executable price complement.

5. `test_corrected_executor_rejects_missing_immutable_final_limit_cost_basis` — Executor fails if intent lacks cost certificate id, final limit, fee basis, snapshot hash, and order policy.

6. `test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp` — Monkeypatch `compute_native_limit_price`; corrected path must not call it for live submission.

7. `test_corrected_entry_cannot_use_legacy_buy_no_exit_fallback` — A corrected buy-NO position with no held-token SELL bid certificate must not evaluate exit via `p_market/current_market_price`.

8. `test_buy_no_exit_uses_best_bid_not_vwmp` — Given bid `0.42`, ask `0.62`, VWMP `0.55`, sell EV must use `0.42`.

9. `test_reports_hard_fail_mixed_pricing_semantics_cohorts` — Legacy `entry_price=p_market` row plus corrected cost-certificate row cannot aggregate into one P&L/economics report.

10. `test_backtests_without_point_in_time_depth_excluded_from_corrected_executable_economics` — Historical ticks without depth snapshot cannot be labeled corrected execution economics.

11. `test_live_buy_no_rejects_complement_price_without_native_no_orderbook` — Binary complement fallback allowed diagnostics only; live money path fails closed.

12. `test_venue_command_market_id_not_token_id_for_live_exit` — Live exit command must have condition/market identity distinct from selected token id.

13. `test_compatibility_envelope_rejected_in_live` — `legacy:{token_id}` envelope cannot be persisted/submitted under live mode.

14. `test_position_entry_price_authority_required_before_exit_or_settlement` — Exit/settlement rejects `entry_price_authority in {edge_price, submitted_limit_without_fill}` unless explicitly optimistic and non-economic.

15. `test_partial_fill_updates_size_cost_basis_and_report_authority` — Partial fill changes `shares_filled` and `filled_cost_basis_usd`; reports exclude target notional.

## 10. Minimal repair packet

### For F-01 / F-02 — Entry sizing and final limit

* **Smallest safe contract change:** Add `ExecutableEntryCostCertificate` with `token_id`, `side=BUY`, `order_type`, `post_only`, `snapshot_id`, `orderbook_hash`, `captured_at`, `best_ask`, `depth_vwap_for_target`, `fee_rate`, `fee_basis`, `final_limit_price`, `cost_basis_price`.
* **Smallest hard gate:** `kelly_size` and live executor reject any cost object derived from `p_market`, `vwmp`, or `implied_probability`.
* **Smallest test:** `test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp`.
* **Smallest schema addition:** `execution_cost_certificate_id`, `execution_cost_basis_version`, `entry_price_authority`.
* **Files likely touched:** `src/engine/evaluator.py`, `src/strategy/market_analysis.py`, `src/strategy/kelly.py`, `src/execution/executor.py`, `src/contracts/execution_intent.py`, `src/contracts/executable_market_snapshot_v2.py`, `src/state/db.py`.
* **Rollout mode:** fail-closed for live; shadow-only for diagnostics.
* **Rollback strategy:** disable live entry; keep probability diagnostics and shadow reports.

### For F-03 / F-04 — Monitor and exit

* **Smallest safe contract change:** Add `ExitExecutableQuote` and `MarketPriorAtMonitor` as separate objects.
* **Smallest hard gate:** buy-NO exit cannot run EV gate without held-token SELL bid/depth certificate.
* **Smallest test:** `test_buy_no_exit_uses_best_bid_not_vwmp`.
* **Smallest schema addition:** `exit_quote_snapshot_id`, `exit_quote_authority`, `exit_semantics_version`.
* **Files likely touched:** `src/engine/monitor_refresh.py`, `src/execution/exit_triggers.py`, `src/state/portfolio.py`, `src/engine/cycle_runtime.py`.
* **Rollout mode:** live-disabled for buy-NO exits until gate passes; operator opt-in only for forced exits.
* **Rollback strategy:** revert to hold-only/no-new-buy-NO mode; allow manual/operator exit.

### For F-05 / F-06 — Venue identity lineage

* **Smallest safe contract change:** Exit intents carry `condition_id`, `gamma_market_id`, `question_id`, `yes_token_id`, `no_token_id`, `held_token_id`.
* **Smallest hard gate:** live command insertion rejects placeholder/fabricated venue identity.
* **Smallest test:** `test_venue_command_market_id_not_token_id_for_live_exit` and `test_compatibility_envelope_rejected_in_live`.
* **Smallest schema addition:** optional `identity_semantics_version`; constraint or repo validator for `market_id != token_id`.
* **Files likely touched:** `src/execution/executor.py`, `src/contracts/venue_submission_envelope.py`, `src/venue/polymarket_v2_adapter.py`, `src/state/venue_command_repo.py`.
* **Rollout mode:** fail-closed live; compatibility path fake/test only.
* **Rollback strategy:** route live submissions only through snapshot-bound envelope path.

### For F-07 / F-08 — Buy-NO native quote and order policy

* **Smallest safe contract change:** Mark complement-derived NO values as `diagnostic_only`; make `OrderPolicy` part of cost certificate.
* **Smallest hard gate:** live buy-NO rejects missing native NO token orderbook snapshot.
* **Smallest test:** `test_live_buy_no_rejects_complement_price_without_native_no_orderbook`.
* **Smallest schema addition:** `quote_source_type in {native_orderbook, complement_diagnostic}` and `order_policy_id`.
* **Files likely touched:** `src/strategy/market_analysis.py`, `src/engine/evaluator.py`, `src/execution/executor.py`, `src/venue/polymarket_v2_adapter.py`.
* **Rollout mode:** shadow-only complement; live-disabled buy-NO unless native quote.
* **Rollback strategy:** buy-YES only live path.

### For F-09 / F-10 — Position/report cohort integrity

* **Smallest safe contract change:** Split target/submitted/fill economics and require authority enum before exit/settlement/report.
* **Smallest hard gate:** reports hard-fail mixed cohorts; settlement learning excludes target-only/legacy rows from corrected economics.
* **Smallest test:** `test_reports_hard_fail_mixed_pricing_semantics_cohorts`.
* **Smallest schema addition:** `pricing_semantics_version`, `entry_price_authority`, `fill_authority`, `execution_cost_basis_version`, `exit_semantics_version`.
* **Files likely touched:** `src/engine/cycle_runtime.py`, `src/execution/fill_tracker.py`, `src/execution/harvester.py`, `scripts/profit_validation_replay.py`, `scripts/equity_curve.py`, `src/state/db.py`.
* **Rollout mode:** report exclusion first; corrected economics opt-in only.
* **Rollback strategy:** label old reports diagnostic, not promotion evidence.

## 11. Not-now list

Do **not** implement these yet:

* Full fill-probability or adverse-selection model.
* Large parallel venue model duplicating CLOB.
* Strategy promotion based only on model skill or posterior lift.
* Corrected historical executable economics without point-in-time depth snapshots.
* Market-prior estimator promotion before quote/prior split is enforced.
* Complex maker/taker optimization before `OrderPolicy` is merely carried as authority.
* Negative-risk conversion optimizer before base token/settlement identity is proven.
* Full cross-position portfolio alpha attribution before entry/exit/fill cohort tags are hard.
* Broad historical backfill that fabricates missing executable cost basis.
* New live buy-NO expansion until native NO quote and exit SELL-bid invariants pass.

## 12. Main-thread self-check

1. **Did I accidentally do ordinary code review instead of real-world semantic audit?**
   No. Findings are about object meaning drift across selection, sizing, execution, monitor, exit, settlement, and reporting, not style or local defects.

2. **Did I confuse downstream capability with upstream evidence?**
   I explicitly flagged executor capability to route NO tokens as insufficient when strategy uses complement-derived NO price.

3. **Did I let local abstractions override venue/API reality?**
   No. Venue-facing conclusions were checked against official CLOB docs for token-specific orderbooks, bid/ask side semantics, order type behavior, fees, tick/min-order, and negative risk.

4. **Did I treat unknowns as known?**
   No. Compatibility live reachability, universal cohort gating, and full test coverage are marked **REVIEW_REQUIRED** where I could not prove them from read evidence.

5. **Did I propose overbuild instead of minimal safety contracts?**
   The repair packet is contract/gate/test/schema minimal: cost certificate, exit quote object, identity invariant, cohort tags. I did not recommend full fill-probability or adverse-selection modeling now.

6. **Did I check entry, exit, persistence, and reporting symmetry?**
   Yes. The highest-risk divergences are entry Kelly cost, executor final limit, buy-NO exit EV, position materialization, settlement P&L, and replay/report cohort mixing.

7. **Did I preserve uncertainty and classify REVIEW_REQUIRED where proof is missing?**
   Yes. I did not claim local tests passed or that every path was executable; I reported evidence from code/docs and separated proven semantic bugs from reachability unknowns.

[1]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/AGENTS.md "raw.githubusercontent.com"
[2]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/contracts/execution_price.py "zeus/src/contracts/execution_price.py at plan-pre5 · fitz-s/zeus · GitHub"
[3]: https://docs.polymarket.com/api-reference/market-data/get-order-book "Get order book - Polymarket Documentation"
[4]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/src/engine/evaluator.py "raw.githubusercontent.com"
[5]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/docs/reference/zeus_execution_lifecycle_reference.md "raw.githubusercontent.com"
[6]: https://docs.polymarket.com/concepts/order-lifecycle "Order Lifecycle - Polymarket Documentation"
[7]: https://docs.polymarket.com/api-reference/market-data/get-market-price "Get market price - Polymarket Documentation"
[8]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/engine/monitor_refresh.py "zeus/src/engine/monitor_refresh.py at plan-pre5 · fitz-s/zeus · GitHub"
[9]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/src/state/portfolio.py "raw.githubusercontent.com"
[10]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/src/engine/cycle_runtime.py "raw.githubusercontent.com"
[11]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/venue/polymarket_v2_adapter.py "zeus/src/venue/polymarket_v2_adapter.py at plan-pre5 · fitz-s/zeus · GitHub"
[12]: https://docs.polymarket.com/market-data/overview "Overview - Polymarket Documentation"
[13]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/strategy/market_analysis.py "zeus/src/strategy/market_analysis.py at plan-pre5 · fitz-s/zeus · GitHub"
[14]: https://docs.polymarket.com/trading/orders/overview "Overview - Polymarket Documentation"
[15]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/src/state/db.py "raw.githubusercontent.com"
[16]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/architecture/source_rationale.yaml "raw.githubusercontent.com"


# ZEUS REALITY-SEMANTICS REPAIR PACKET

## 1. Executive architecture verdict

1. **Zeus 必须采用四个物理隔离 plane：Belief / Market-Prior / Executable Cost-Quote / Lifecycle-Fill-Persistence。** 任何 price-like scalar 单独出现都不能成为 live-money authority。

2. **Corrected baseline 的 posterior 默认是 `model_only_v1`。** Raw quote、VWMP、midpoint、sparse monitor vector 不能直接进入 posterior；只有 `MarketPriorDistribution` 可以进入 posterior fusion，且 live 必须 `validated_for_live=True`。Probability/executable split spec 明确说 API/orderbook price 是 observation，不是 prior by default；market prior 必须有 estimator lineage、freshness、family completeness、de-vig、liquidity、negative-risk policy 和 validation。

3. **`BinEdge` / `EdgeContext` 不再是 live economic authority。** 它们可以作为 transition shim 携带 legacy diagnostics，但最终 money path authority 必须是 `ExecutableTradeHypothesis = candidate/bin/direction + selected_token_id + payoff_probability + executable_snapshot + cost_basis + order_policy + FDR_family + immutable_intent`。

4. **Kelly 只能吃 `ExecutableEntryCostBasis.fee_adjusted_execution_price`。** 当前 branch 仍存在从 `entry_price/p_market/vwmp` 进入 `ExecutionPrice` 再进 Kelly 的路径；`ExecutionPrice` 文件自己指出 D3 gap：`entry_price=p_market` 与真实 Polymarket ask + fee + slippage 不是同一对象。([GitHub][1])

5. **Executor corrected path 只能 validate / submit / reject。** 它不能从 `p_posterior`、`edge.vwmp`、`entry_price`、`p_market`、`best_ask` 重新发明 final limit。当前 `create_execution_intent()` 仍从 `edge_context.p_posterior` 和 `edge.vwmp` 计算 limit，并有 dynamic ask jump；这是 live blocker。([GitHub][2])

6. **Corrected entry 未修 corrected exit 之前不得 live。** Monitor 必须分成 posterior refresh 和 held-token sell quote refresh；exit EV 必须比较 `hold_value = held-side payoff probability` 与 `sell_value = held token SELL bid/depth after fee`。当前 buy-NO exit 仍把 `current_market_price` 当 sell value，且注释用 fee formula symmetry 为其辩护；这不是可接受的现实对象证明。([GitHub][3])

7. **Position economics 必须拆 target / submitted / filled / settlement。** `entry_price` 不能同时代表 edge price、submitted limit、fill average。当前 materialization 仍用 `fill_price or submitted_price or decision.edge.entry_price`，并把 `cost_basis_usd=decision.size_usd`，这会污染 fill/PnL/settlement evidence。([GitHub][4])

8. **Report/backtest/promotion 必须 cohort hard-fail。** Legacy rows、model-only diagnostics、submitted-limit rows、filled-economics rows、corrected executable-cost rows不能混合聚合；旧 rows 不能 backfill 成 corrected economics。Spec 明确要求 additive fields、历史 depth 缺失时不得重建 corrected executable economics、mixed cohort 必须 hard-fail 或 segregate，warning-only 不够。

9. **不创建 parallel venue model。** 复用现有 `ExecutionPrice`、`ExecutionIntent`、`ExecutableMarketSnapshotV2`、`VenueSubmissionEnvelope`，新增 sidecar contracts 和 gates。Spec 已经要求 reuse existing contracts，不得创建 parallel venue model。

10. **修复顺序裁决：tests/gates → contracts/schema additive → posterior/prior split → executable cost + hypothesis/FDR → executor hardening → buy-NO/exit symmetry → fill/lot → reporting/promotion → cleanup。** 不能先 enable corrected mode 再补 exit；不能先大改 architecture 再补 invariant tests。

证据基于重新读取 `plan-pre5` branch 的 authority/context、关键 money-path 文件、Probability And Executable Price Split Spec，以及官方 Polymarket/CLOB 文档；这里不声称已经在本地跑过 Zeus 测试。Root `AGENTS.md` 把 Zeus 定义为 live quantitative trading engine，要求先读 authority/context，并把 money path 明确到 evaluator、executor、monitor、exit、settlement/learning。([GitHub][5])

---

## 2. Reality model

### 2.1 Belief Plane

**表示对象：** settlement probability / posterior belief。
**单位：** payoff probability in `[0,1]` over weather bin settlement.
**允许知道：** `p_raw_yes`、`p_cal_yes`、`p_posterior_yes`、calibration version、forecast snapshot、temperature metric、settlement source。
**禁止知道：** token quote、bid/ask、depth、fee、tick、min order、order policy、limit price、venue order type、fill status。

Live payoff semantics:

```text
buy_yes payoff_probability(bin i) = P_posterior_yes[i]
buy_no  payoff_probability(bin i) = 1 - P_posterior_yes[i]
```

`P_no = 1 - P_yes` 只在 payoff probability plane 成立。它不能推出 NO-token BUY ask，也不能推出 held NO-token SELL bid。Spec 明确将 settlement probability 与 executable token quote 分离。

### 2.2 Market-Prior Plane

**表示对象：** 由 market observation 构造的 named prior estimator。
**允许知道：** quote source hashes、family completeness、vig treatment、freshness、liquidity/spread filter、negative-risk policy、estimator version、validation status。
**禁止知道：** executable order policy、final limit、Kelly size、submitted/fill status。

Allowed modes:

```text
model_only_v1
legacy_vwmp_prior_v0
yes_family_devig_v1_shadow
```

`legacy_vwmp_prior_v0` 是 quarantine transition mode：可以用于 diagnostics 或 operator-ack legacy path，不能作为 promotion-grade economics。`yes_family_devig_v1_shadow` 必须 shadow-only，直到 OOS Brier + ROI + liquidity/negative-risk validation 通过。Spec 对这些模式已经做了明确要求。

### 2.3 Executable Cost / Quote Plane

**表示对象：** token-side executable economics。
**BUY authority：** selected token ask/depth + final limit + worst-case fee + tick/min-order + order policy + quote snapshot freshness。
**SELL authority：** held token bid/depth + exit policy + fee + freshness。
**禁止知道：** posterior construction internals、market-prior estimator math、report cohort eligibility。

Polymarket/CLOB 现实：market/condition/question/token identity 是不同对象；market maps to a pair of CLOB token IDs, question ID, condition ID; orderbook is token-specific and includes `asset_id`, timestamp, hash, bids, asks, min order size, tick size, and negative-risk fields.([Polymarket Documentation][6])

### 2.4 Lifecycle / Fill / Persistence Plane

**表示对象：** submitted command、venue order、fill facts、partial fill、cancel remainder、position lot、settlement payout、report cohort。
**允许知道：** command idempotency, envelope hash, submitted limit, venue order id, fill avg price, filled shares, remaining shares, cancel status, settlement condition and payout。
**禁止做：** 用 target notional 当 filled cost、用 submitted limit 当 fill average、用 legacy `entry_price` 当 corrected economics、用 model-only probability report 推 live economics promotion。

官方 CLOB 订单现实：orders are expressed as limit orders; GTC/GTD can rest, FOK/FAK execute immediately against resting liquidity, post-only is constrained, and open order objects carry market condition ID, asset token ID, side, original size, matched size, price, and order type. Fees are charged at match time and are not simply “included in order price.”([Polymarket Documentation][7])

---

## 3. Finding-to-repair map

| Finding                                                   |                                 Re-verification | Root cause                                                        | Repair object                                                       |                Phase | Test                                                                     | Gate                                                      | Schema impact                                               | Live impact                                     |                               |
| --------------------------------------------------------- | ----------------------------------------------: | ----------------------------------------------------------------- | ------------------------------------------------------------------- | -------------------: | ------------------------------------------------------------------------ | --------------------------------------------------------- | ----------------------------------------------------------- | ----------------------------------------------- | ----------------------------- |
| F-01 Kelly accepts relabeled probability/VWMP             |                                   **confirmed** | `p_market/entry_price/vwmp` wrapped as execution price            | `ExecutableEntryCostBasis` + `ExecutionPrice` from certificate only |                  3–5 | `test_executable_quote_change_changes_cost_size_limit_not_posterior`     | Kelly rejects non-certificate cost                        | add `execution_cost_basis_version`, `cost_basis_id/hash`    | live entry fail-closed                          |                               |
| F-02 Executor recomputes limit                            |                                   **confirmed** | executor remains price authority                                  | `CorrectedExecutionIntent` immutable final fields                   |                    6 | `test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp`  | corrected executor cannot call limit compute              | add `final_limit_price`, `order_policy_id` metadata         | no corrected live until pass                    |                               |
| F-03 Buy-NO exit uses probability vector as sell proceeds |                                   **confirmed** | exit quote and belief conflated                                   | `ExitExecutableQuote` / `ExitCostBasis`                             |                    8 | `test_buy_no_exit_uses_best_bid_not_vwmp`                                | corrected entry cannot use legacy exit                    | add `exit_quote_snapshot_id/hash`, `exit_semantics_version` | buy-NO live blocked                             |                               |
| F-04 Monitor quote mutates posterior                      |                                   **confirmed** | quote observation fed into posterior                              | `MarketPriorDistribution                                            | None`; split monitor | 4,8                                                                      | `test_monitor_quote_changes_exit_value_but_not_posterior` | raw quote cannot enter posterior                            | add `market_prior_version`, trace lineage       | corrected monitor shadow only |
| F-05 `market_id` identity drift                           |                         **partially confirmed** | prior exact branch not fully re-located; identity risk remains    | venue identity contract in intent/envelope                          |                    6 | `test_venue_command_market_id_not_token_id_for_live_exit`                | live command rejects token-as-market                      | add identity semantics fields                               | live exit blocked if identity missing           |                               |
| F-06 compatibility envelope fabricates identity           |                                   **confirmed** | adapter compatibility helper creates `legacy:{token_id}` identity | envelope live certification gate                                    |                    6 | `test_compatibility_envelope_rejected_in_live`                           | compatibility envelope test/fake only                     | add `is_compatibility_envelope`, `live_certified`           | live submit fail-closed                         |                               |
| F-07 buy-NO complement fallback                           |                         **partially confirmed** | multi-bin guarded, binary complement still executable-risky       | native NO quote requirement                                         |                    7 | `test_live_buy_no_rejects_complement_price_without_native_no_orderbook`  | complement diagnostic only                                | add `quote_source_type`                                     | buy-NO live disabled without native quote       |                               |
| F-08 order policy after sizing                            |                                   **confirmed** | order policy absent from cost certificate                         | `OrderPolicy`                                                       |                3,5,6 | `test_order_policy_change_changes_cost_basis_not_model_belief`           | cost basis requires order policy                          | add `order_policy_id`                                       | no implicit adapter policy                      |                               |
| F-09 position materialization fallback                    |                                   **confirmed** | one `entry_price` field spans target/submitted/fill               | `PositionLot`, `EntryEconomicsAuthority`, `FillAuthority`           |                    9 | `test_position_entry_price_authority_required_before_exit_or_settlement` | settlement/report uses fill facts only                    | add split fields or `position_lots`                         | promotion evidence blocked until fill authority |                               |
| F-10 report/backtest mixed cohorts                        | **confirmed / REVIEW_REQUIRED for all scripts** | no universal cohort hard gate proven                              | `ReportingCohort`, `PricingSemanticsVersion`                        |                   10 | `test_reports_hard_fail_mixed_pricing_semantics_cohorts`                 | mixed cohorts hard fail                                   | add cohort columns                                          | no promotion from mixed evidence                |                               |

Key code evidence: `MarketAnalysis` still computes posterior from `p_cal` and `p_market`, buy-YES `entry_price/vwmp` from `p_market`, buy-NO complement paths, and executor/runtime still mutate or recompute price/size after decision.([GitHub][8])

---

## 4. Target object model

### 4.1 `MarketPriorDistribution`

**File:** `src/contracts/market_prior.py`
**Style:** frozen dataclass, no pydantic unless existing repo standard requires otherwise.
**Authority:** may enter `compute_posterior()`. Raw quote floats may not.

```python
# src/contracts/market_prior.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Sequence
import hashlib, json


class MarketPriorEstimatorVersion(StrEnum):
    MODEL_ONLY_V1 = "model_only_v1"
    LEGACY_VWMP_PRIOR_V0 = "legacy_vwmp_prior_v0"
    YES_FAMILY_DEVIG_V1_SHADOW = "yes_family_devig_v1_shadow"


class VigTreatment(StrEnum):
    NONE = "none"
    LEGACY_UNKNOWN = "legacy_unknown"
    YES_FAMILY_NORMALIZE = "yes_family_normalize"
    EXPLICIT_DEVIG = "explicit_devig"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class NegRiskPolicy(StrEnum):
    BLOCK_UNSUPPORTED = "block_unsupported"
    STANDARD_ONLY = "standard_only"
    AUGMENTED_BLOCKED = "augmented_blocked"
    SUPPORTED_WITH_CONVERSION_MODEL = "supported_with_conversion_model"


@dataclass(frozen=True)
class MarketPriorDistribution:
    values: tuple[float, ...]
    estimator_version: MarketPriorEstimatorVersion
    source_quote_hashes: tuple[str, ...] = ()
    family_complete: bool = False
    vig_treatment: VigTreatment = VigTreatment.NONE
    freshness_status: FreshnessStatus = FreshnessStatus.UNKNOWN
    liquidity_filter_passed: bool = False
    spread_filter_passed: bool = False
    neg_risk_policy: NegRiskPolicy = NegRiskPolicy.BLOCK_UNSUPPORTED
    validated_for_live: bool = False
    estimator_notes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("MarketPriorDistribution.values cannot be empty")
        if any(p < 0.0 or p > 1.0 for p in self.values):
            raise ValueError("market prior values must be probabilities")
        if self.estimator_version == MarketPriorEstimatorVersion.MODEL_ONLY_V1:
            if self.values:
                raise ValueError("model_only_v1 should be represented as None, not quote prior")
        if self.validated_for_live:
            self.assert_live_eligible()

    def assert_live_eligible(self) -> None:
        if self.estimator_version == MarketPriorEstimatorVersion.LEGACY_VWMP_PRIOR_V0:
            raise ValueError("legacy_vwmp_prior_v0 is never promotion-grade live prior")
        if not self.family_complete:
            raise ValueError("live prior requires complete family")
        if self.freshness_status != FreshnessStatus.FRESH:
            raise ValueError("live prior requires fresh quotes")
        if not self.liquidity_filter_passed or not self.spread_filter_passed:
            raise ValueError("live prior requires liquidity/spread filters")
        if self.neg_risk_policy not in {
            NegRiskPolicy.BLOCK_UNSUPPORTED,
            NegRiskPolicy.STANDARD_ONLY,
        }:
            raise ValueError("unsupported negative-risk prior policy")

    @property
    def prior_hash(self) -> str:
        payload = {
            "values": list(self.values),
            "estimator_version": str(self.estimator_version),
            "source_quote_hashes": list(self.source_quote_hashes),
            "family_complete": self.family_complete,
            "vig_treatment": str(self.vig_treatment),
            "freshness_status": str(self.freshness_status),
            "liquidity_filter_passed": self.liquidity_filter_passed,
            "spread_filter_passed": self.spread_filter_passed,
            "neg_risk_policy": str(self.neg_risk_policy),
            "validated_for_live": self.validated_for_live,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
```

`compute_posterior()` target signature:

```python
def compute_posterior(
    p_cal_yes: Sequence[float],
    *,
    market_prior: MarketPriorDistribution | None,
    posterior_mode: MarketPriorEstimatorVersion,
    alpha: float,
) -> tuple[float, ...]:
    if posterior_mode == MarketPriorEstimatorVersion.MODEL_ONLY_V1:
        if market_prior is not None:
            raise ValueError("model_only_v1 cannot consume market prior")
        return tuple(p_cal_yes)

    if market_prior is None:
        raise ValueError(f"{posterior_mode} requires MarketPriorDistribution")

    if posterior_mode == MarketPriorEstimatorVersion.LEGACY_VWMP_PRIOR_V0:
        # Allowed only legacy/shadow. Caller gate decides live eligibility.
        return tuple(alpha * c + (1.0 - alpha) * m for c, m in zip(p_cal_yes, market_prior.values))

    market_prior.assert_live_eligible()
    return tuple(alpha * c + (1.0 - alpha) * m for c, m in zip(p_cal_yes, market_prior.values))
```

### 4.2 `OrderPolicy`

**File:** `src/contracts/order_policy.py`
**Initial supported corrected policy:** `LIMIT_MAY_TAKE_CONSERVATIVE`.

```python
# src/contracts/order_policy.py
from dataclasses import dataclass
from enum import StrEnum


class OrderPolicyKind(StrEnum):
    LIMIT_MAY_TAKE_CONSERVATIVE = "limit_may_take_conservative"
    POST_ONLY_PASSIVE_LIMIT = "post_only_passive_limit"              # future only
    MARKETABLE_LIMIT_DEPTH_BOUND = "marketable_limit_depth_bound"    # future only


@dataclass(frozen=True)
class OrderPolicy:
    policy_id: str
    kind: OrderPolicyKind
    venue_order_type: str       # "GTC" or "GTD" near-term; not FOK/FAK baseline
    post_only: bool
    cancel_after_seconds: int
    fee_assumption: str         # "worst_case_taker"
    modeled_fill_probability: bool = False

    def assert_corrected_supported(self) -> None:
        if self.kind != OrderPolicyKind.LIMIT_MAY_TAKE_CONSERVATIVE:
            raise ValueError("only LIMIT_MAY_TAKE_CONSERVATIVE is supported in corrected baseline")
        if self.post_only:
            raise ValueError("corrected baseline is may-take, not post-only")
        if self.venue_order_type not in {"GTC", "GTD"}:
            raise ValueError("corrected baseline uses bounded resting/may-take limit, not FOK/FAK")
        if self.fee_assumption != "worst_case_taker":
            raise ValueError("corrected baseline requires worst_case_taker fee")
        if self.modeled_fill_probability:
            raise ValueError("fill probability is not modeled in first packet")
```

Semantics:

```text
LIMIT_MAY_TAKE_CONSERVATIVE:
- bounded limit order;
- may rest or immediately match;
- Kelly uses final submitted limit + worst-case taker fee;
- size is conditional on fill;
- no queue priority, fill probability, maker rebate, or adverse selection model;
- cancel remainder after configured timeout;
- promotion requires realized fill/maker-taker/partial/cancel telemetry.
```

Spec already defines this policy vocabulary and says future policies are separate.

### 4.3 `ExecutableEntryCostBasis` / certificate

**File:** `src/contracts/executable_cost_basis.py`
**Name decision:** use `ExecutableEntryCostBasis` for the economic object; `cost_basis_id/hash` makes it a certificate. Reuse `ExecutionPrice` for the final fee-adjusted scalar, not for raw quote/prior.

```python
# src/contracts/executable_cost_basis.py
from dataclasses import dataclass
from enum import StrEnum
from datetime import datetime, timezone
from decimal import Decimal
import hashlib, json

from src.contracts.execution_price import ExecutionPrice
from src.contracts.order_policy import OrderPolicy


class Direction(StrEnum):
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"


class ValidationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ExecutableEntryCostBasis:
    selected_token_id: str
    selected_outcome_label: str          # "YES" or "NO"
    direction: Direction
    order_policy: OrderPolicy
    final_limit_price: Decimal
    fee_adjusted_execution_price: ExecutionPrice
    worst_case_fee_rate: Decimal
    fee_source: str
    tick_status: ValidationStatus
    min_order_status: ValidationStatus
    depth_status: ValidationStatus
    quote_snapshot_id: str
    quote_snapshot_hash: str
    orderbook_hash: str
    captured_at: datetime
    cost_basis_version: str = "executable_entry_cost_basis_v1"

    def __post_init__(self) -> None:
        if not self.selected_token_id:
            raise ValueError("selected_token_id required")
        self.order_policy.assert_corrected_supported()
        if not (Decimal("0") < self.final_limit_price < Decimal("1")):
            raise ValueError("final_limit_price must be in (0,1)")
        if self.tick_status != ValidationStatus.PASS:
            raise ValueError("tick validation failed")
        if self.min_order_status != ValidationStatus.PASS:
            raise ValueError("min-order validation failed")
        if self.depth_status == ValidationStatus.FAIL:
            raise ValueError("depth validation failed")
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        self.fee_adjusted_execution_price.assert_kelly_safe()

    @property
    def cost_basis_hash(self) -> str:
        payload = {
            "selected_token_id": self.selected_token_id,
            "selected_outcome_label": self.selected_outcome_label,
            "direction": str(self.direction),
            "order_policy": self.order_policy.policy_id,
            "final_limit_price": str(self.final_limit_price),
            "fee_adjusted_execution_price": str(self.fee_adjusted_execution_price.value),
            "worst_case_fee_rate": str(self.worst_case_fee_rate),
            "fee_source": self.fee_source,
            "quote_snapshot_id": self.quote_snapshot_id,
            "quote_snapshot_hash": self.quote_snapshot_hash,
            "orderbook_hash": self.orderbook_hash,
            "captured_at": self.captured_at.isoformat(),
            "version": self.cost_basis_version,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @property
    def cost_basis_id(self) -> str:
        return f"ecb:{self.quote_snapshot_id}:{self.selected_token_id}:{self.cost_basis_hash[:16]}"
```

Derivation from `ExecutableMarketSnapshotV2`:

```python
def derive_entry_cost_basis(
    *,
    snapshot: "ExecutableMarketSnapshotV2",
    selected_token_id: str,
    selected_outcome_label: str,
    direction: Direction,
    order_policy: OrderPolicy,
    intended_notional_usd: Decimal,
    max_quote_age_seconds: int,
    now: datetime,
) -> ExecutableEntryCostBasis:
    # Use token-side ask for BUY.
    quote = snapshot.require_token_quote(selected_token_id)
    snapshot.assert_fresh(now, max_quote_age_seconds)
    order_policy.assert_corrected_supported()

    final_limit = quote.best_ask_aligned_to_tick_or_reject()
    fee_rate = snapshot.require_fee_rate(selected_token_id)

    execution_price = ExecutionPrice(
        value=final_limit,
        price_type="fee_adjusted_execution_price",
        fee_deducted=True,
        currency="probability_units",
        provenance="executable_entry_cost_basis_v1",
    ).with_worst_case_taker_fee(fee_rate)

    return ExecutableEntryCostBasis(
        selected_token_id=selected_token_id,
        selected_outcome_label=selected_outcome_label,
        direction=direction,
        order_policy=order_policy,
        final_limit_price=final_limit,
        fee_adjusted_execution_price=execution_price,
        worst_case_fee_rate=fee_rate,
        fee_source=snapshot.fee_source,
        tick_status=ValidationStatus.PASS,
        min_order_status=snapshot.min_order_status(intended_notional_usd),
        depth_status=snapshot.depth_status_for_sanity(intended_notional_usd),
        quote_snapshot_id=snapshot.snapshot_id,
        quote_snapshot_hash=snapshot.snapshot_hash,
        orderbook_hash=quote.orderbook_hash,
        captured_at=snapshot.captured_at,
    )
```

For `LIMIT_MAY_TAKE_CONSERVATIVE`, depth is a sanity/liquidity filter, not an immediate-fill guarantee; future `MARKETABLE_LIMIT_DEPTH_BOUND` would make depth-weighted ask curve the cost authority.

### 4.4 `ExitExecutableQuote` / `ExitCostBasis`

**File:** `src/contracts/exit_quote.py`

```python
# src/contracts/exit_quote.py
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.contracts.executable_cost_basis import ValidationStatus


@dataclass(frozen=True)
class ExitExecutableQuote:
    held_token_id: str
    held_outcome_label: str
    exit_side: str                         # always "SELL" for normal exit
    best_bid: Decimal
    bid_size: Decimal | None
    depth_status: ValidationStatus
    fee_rate: Decimal
    fee_source: str
    quote_snapshot_id: str
    quote_snapshot_hash: str
    orderbook_hash: str
    captured_at: datetime
    max_quote_age_seconds: int
    exit_semantics_version: str = "exit_sell_quote_v1"
    forced_manual: bool = False
    force_reason: str | None = None

    def assert_usable_for_corrected_exit(self, now: datetime) -> None:
        if self.exit_side != "SELL":
            raise ValueError("exit quote must be held-token SELL quote")
        if not (Decimal("0") <= self.best_bid <= Decimal("1")):
            raise ValueError("best_bid must be in [0,1]")
        age = (now - self.captured_at).total_seconds()
        if age > self.max_quote_age_seconds and not self.forced_manual:
            raise ValueError("stale exit quote")
        if self.depth_status == ValidationStatus.FAIL and not self.forced_manual:
            raise ValueError("exit depth failed")
```

Exit EV must be:

```python
def corrected_exit_ev(
    *,
    remaining_shares: Decimal,
    held_side_payoff_probability: Decimal,
    payout_value: Decimal,
    sell_quote: ExitExecutableQuote,
    now: datetime,
) -> Decimal:
    sell_quote.assert_usable_for_corrected_exit(now)
    hold_value = remaining_shares * held_side_payoff_probability * payout_value
    sell_value = remaining_shares * sell_quote.best_bid
    fee = sell_value * sell_quote.fee_rate  # exact fee formula may use Polymarket p*(1-p)
    return (sell_value - fee) - hold_value
```

Both buy-YES and buy-NO use the same exit quote shape. There is no `p_market` vector fallback.

### 4.5 `CorrectedExecutionIntent`

**File:** extend `src/contracts/execution_intent.py` or create `src/contracts/corrected_execution_intent.py` and import into executor.

```python
# src/contracts/corrected_execution_intent.py
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime

from src.contracts.executable_cost_basis import ExecutableEntryCostBasis, Direction
from src.contracts.order_policy import OrderPolicy


@dataclass(frozen=True)
class CorrectedExecutionIntent:
    decision_id: str
    candidate_id: str
    fdr_family_id: str
    hypothesis_id: str

    condition_id: str
    gamma_market_id: str
    question_id: str
    yes_token_id: str
    no_token_id: str

    selected_token_id: str
    selected_outcome_label: str
    direction: Direction
    payoff_probability: Decimal

    target_notional_usd: Decimal
    final_limit_price: Decimal
    order_policy: OrderPolicy
    entry_cost_basis: ExecutableEntryCostBasis

    executable_snapshot_id: str
    executable_snapshot_hash: str
    cost_basis_id: str
    cost_basis_hash: str

    idempotency_key: str
    created_at: datetime
    pricing_semantics_version: str = "corrected_executable_cost_v1"

    # legacy transition fields are allowed only for logging, never for price authority
    legacy_edge_vwmp: Decimal | None = None
    legacy_p_market: Decimal | None = None

    def assert_submit_ready(self) -> None:
        if self.selected_token_id != self.entry_cost_basis.selected_token_id:
            raise ValueError("intent token does not match cost basis token")
        if self.final_limit_price != self.entry_cost_basis.final_limit_price:
            raise ValueError("intent limit does not match immutable cost basis")
        if self.executable_snapshot_hash != self.entry_cost_basis.quote_snapshot_hash:
            raise ValueError("intent snapshot does not match cost basis snapshot")
        if self.cost_basis_hash != self.entry_cost_basis.cost_basis_hash:
            raise ValueError("intent cost basis hash mismatch")
        if self.gamma_market_id == self.selected_token_id:
            raise ValueError("market_id/token_id collision")
        if self.condition_id.startswith("legacy:"):
            raise ValueError("compatibility envelope identity forbidden in corrected live")
        self.order_policy.assert_corrected_supported()
```

Executor corrected signature:

```python
def execute_final_intent(intent: CorrectedExecutionIntent) -> "VenueCommandResult":
    intent.assert_submit_ready()
    # create validated VenueSubmissionEnvelope from intent
    # write venue_command journal before side effect
    # submit or reject; never compute price/size/policy/token
```

### 4.6 `CorrectedVenueSubmissionEnvelope`

Do not replace `VenueSubmissionEnvelope`; add validation method:

```python
def assert_corrected_live_certified(envelope: VenueSubmissionEnvelope) -> None:
    if envelope.condition_id.startswith("legacy:"):
        raise ValueError("legacy compatibility envelope cannot be live certified")
    if envelope.yes_token_id == envelope.no_token_id:
        raise ValueError("collapsed yes/no token ids cannot be live certified")
    if envelope.selected_outcome_token_id not in {envelope.yes_token_id, envelope.no_token_id}:
        raise ValueError("selected token not in market token pair")
    if envelope.market_id == envelope.selected_outcome_token_id:
        raise ValueError("market_id/token_id collision")
    if not envelope.fee_source or envelope.tick_size is None or envelope.min_order_size is None:
        raise ValueError("missing venue execution facts")
```

Current V2 adapter compatibility helper explicitly fabricates `condition_id="legacy:{token_id}"`, `yes_token_id=no_token_id=token_id`, and `outcome_label="YES"`; this path must remain test/fake/legacy-only and never live-certified.([GitHub][9])

### 4.7 `PositionLot` / `FillAuthority` / `EntryEconomicsAuthority`

**File:** `src/contracts/position_economics.py`

```python
from dataclasses import dataclass
from enum import StrEnum
from decimal import Decimal
from datetime import datetime


class EntryEconomicsAuthority(StrEnum):
    LEGACY_UNKNOWN = "legacy_unknown"
    MODEL_EDGE_PRICE = "model_edge_price"                 # non-promotion
    SUBMITTED_LIMIT = "submitted_limit"                   # target/submitted only
    AVG_FILL_PRICE = "avg_fill_price"                     # fill-grade
    CORRECTED_EXECUTABLE_COST_BASIS = "corrected_executable_cost_basis"


class FillAuthority(StrEnum):
    NONE = "none"
    OPTIMISTIC_SUBMITTED = "optimistic_submitted"
    VENUE_CONFIRMED_PARTIAL = "venue_confirmed_partial"
    VENUE_CONFIRMED_FULL = "venue_confirmed_full"
    CANCELLED_REMAINDER = "cancelled_remainder"
    SETTLED = "settled"


@dataclass(frozen=True)
class PositionLot:
    position_id: str
    decision_id: str
    condition_id: str
    market_id: str
    held_token_id: str
    held_outcome_label: str
    direction: str

    target_notional_usd: Decimal
    submitted_notional_usd: Decimal | None
    filled_notional_usd: Decimal
    submitted_limit_price: Decimal | None
    avg_fill_price: Decimal | None

    shares_submitted: Decimal | None
    shares_filled: Decimal
    shares_remaining: Decimal

    entry_cost_basis_id: str | None
    entry_cost_basis_hash: str | None
    entry_economics_authority: EntryEconomicsAuthority
    fill_authority: FillAuthority
    pricing_semantics_version: str
    corrected_executable_economics_eligible: bool

    created_at: datetime
    updated_at: datetime

    def assert_promotion_grade(self) -> None:
        if self.pricing_semantics_version != "corrected_executable_cost_v1":
            raise ValueError("legacy position is not corrected promotion evidence")
        if self.entry_economics_authority not in {
            EntryEconomicsAuthority.AVG_FILL_PRICE,
            EntryEconomicsAuthority.CORRECTED_EXECUTABLE_COST_BASIS,
        }:
            raise ValueError("entry economics are not fill/cost-basis authoritative")
        if self.fill_authority not in {
            FillAuthority.VENUE_CONFIRMED_PARTIAL,
            FillAuthority.VENUE_CONFIRMED_FULL,
            FillAuthority.CANCELLED_REMAINDER,
            FillAuthority.SETTLED,
        }:
            raise ValueError("position has no venue fill authority")
        if self.filled_notional_usd <= 0 or self.shares_filled <= 0:
            raise ValueError("no filled exposure")
```

### 4.8 `ReportingCohort` / `PricingSemanticsVersion`

**File:** `src/contracts/reporting_cohort.py`

```python
from dataclasses import dataclass
from enum import StrEnum


class PricingSemanticsVersion(StrEnum):
    LEGACY_PRICE_PROBABILITY_CONFLATED = "legacy_price_probability_conflated"
    MODEL_ONLY_DIAGNOSTIC_V1 = "model_only_diagnostic_v1"
    SHADOW_EXECUTABLE_COST_V1 = "shadow_executable_cost_v1"
    CORRECTED_EXECUTABLE_COST_V1 = "corrected_executable_cost_v1"


@dataclass(frozen=True)
class ReportingCohort:
    pricing_semantics_version: PricingSemanticsVersion
    execution_cost_basis_version: str | None
    exit_semantics_version: str | None
    fill_authority_required: bool
    allow_mixed_versions: bool = False

    def assert_aggregate_allowed(self, row_versions: set[str]) -> None:
        if not self.allow_mixed_versions and len(row_versions) != 1:
            raise ValueError(f"mixed pricing semantics cohort: {row_versions}")
```

---

## 5. Architecture decision records

### ADR-001 — Four-plane separation

* **Decision:** Separate belief, market-prior, executable cost/quote, lifecycle/fill/persistence.
* **Alternatives considered:** Rename `p_market` / `entry_price`; add comments; patch buy-NO only.
* **Why rejected:** Naming does not stop the same scalar crossing posterior/Kelly/executor/exit/report boundaries.
* **Real-world trading reason:** Settlement probability, token quote, submitted order, and fill fact are different economic objects.
* **Code-level implication:** New contracts + gates; `BinEdge` not final authority.
* **Migration implication:** Add semantics version fields; legacy rows classified as conflated.
* **Test implication:** Counterfactual tests must change quote without changing posterior and change posterior without changing executable snapshot.
* **Rollback implication:** Disable corrected live; diagnostics continue.
* **Does NOT solve:** market-prior estimator quality or fill probability.

### ADR-002 — `MarketPriorDistribution` is optional and not raw quote

* **Decision:** `compute_posterior()` accepts `MarketPriorDistribution | None`, not raw `p_market` floats.
* **Alternatives:** Keep `p_market` vector and add freshness fields.
* **Why rejected:** A vector does not prove family completeness, de-vig, liquidity, negative-risk policy, or validation.
* **Real-world reason:** Quote observation is not a prior unless estimator lineage proves it.
* **Code implication:** `model_only_v1` default; `legacy_vwmp_prior_v0` explicit legacy.
* **Migration:** `market_prior_version`, `market_prior_hash`, source quote hashes in probability trace.
* **Test:** raw quote cannot enter posterior; legacy prior not live-eligible.
* **Rollback:** use model-only posterior.
* **Does NOT solve:** whether market priors improve OOS ROI.

### ADR-003 — Kelly accepts only executable cost basis

* **Decision:** Kelly consumes `ExecutableEntryCostBasis.fee_adjusted_execution_price`.
* **Alternatives:** Keep `ExecutionPrice` wrapper around `entry_price`.
* **Why rejected:** Current wrapper can launder implied probability/VWMP into fee-adjusted price.
* **Real-world reason:** Stake is determined by executable cost, not model belief or prior.
* **Code implication:** `kelly_size()` corrected overload rejects floats and `BinEdge.entry_price`.
* **Migration:** Store cost basis id/hash and authority.
* **Test:** quote ask/depth changes size; posterior unchanged.
* **Rollback:** no live entry; keep model diagnostics.
* **Does NOT solve:** queue/fill probability.

### ADR-004 — Executor validates immutable final intent and cannot reprice from belief

* **Decision:** Corrected executor signature is `execute_final_intent(CorrectedExecutionIntent)`.
* **Alternatives:** Keep `create_execution_intent(edge, edge_context, ...)`.
* **Why rejected:** Executor becomes hidden price authority.
* **Real-world reason:** Submitted order must be same economic hypothesis selected/sized.
* **Code implication:** No `compute_native_limit_price()` or best-ask jump in corrected path.
* **Migration:** envelope metadata carries cost basis/final limit/order policy.
* **Test:** AST gate and unit test prove no recompute.
* **Rollback:** live disabled; legacy path explicitly opt-in and non-promotion.
* **Does NOT solve:** venue outage handling.

### ADR-005 — Exit EV uses held-token SELL quote, not `p_market`

* **Decision:** Exit compares hold payoff value vs held-token SELL executable quote.
* **Alternatives:** Continue using `current_market_price` or sparse `p_market`.
* **Why rejected:** Current monitor quote/probability vector is not sell proceeds.
* **Real-world reason:** Exiting a held token sells that token into bid/depth.
* **Code implication:** `ExitExecutableQuote` required for corrected exit.
* **Migration:** `exit_quote_snapshot_id/hash`, `exit_semantics_version`.
* **Test:** buy-NO exit uses best bid, not VWMP.
* **Rollback:** manual forced exits only; no promotion evidence.
* **Does NOT solve:** optimal liquidation under thin books.

### ADR-006 — Position economics split target/submitted/fill

* **Decision:** Split target notional, submitted limit, filled notional, avg fill, remaining shares, settlement payout.
* **Alternatives:** Continue `entry_price` and `size_usd`.
* **Why rejected:** One field cannot represent target, submit, fill, and settlement.
* **Real-world reason:** Partial fills and cancel remainders are normal CLOB states.
* **Code implication:** `PositionLot`, `FillAuthority`, `EntryEconomicsAuthority`.
* **Migration:** additive columns or new `position_lots` table.
* **Test:** partial fill then cancel remainder updates filled facts only.
* **Rollback:** legacy positions non-promotion, monitor-only.
* **Does NOT solve:** chain reconciliation completeness.

### ADR-007 — Reports hard-fail mixed semantics cohorts

* **Decision:** Reports/promotion cannot aggregate mixed `pricing_semantics_version`.
* **Alternatives:** Warning-only.
* **Why rejected:** Operators and future agents will treat warning reports as economic truth.
* **Real-world reason:** Model skill, submitted-limit economics, and fill economics are not comparable.
* **Code implication:** report query gate.
* **Migration:** classify legacy rows; do not backfill corrected.
* **Test:** mixed cohort raises.
* **Rollback:** report as diagnostic only.
* **Does NOT solve:** historical depth reconstruction.

### ADR-008 — Complement math diagnostic-only for executable NO price

* **Decision:** `1 - P_yes` allowed for payoff probability only; not for NO executable cost.
* **Alternatives:** Allow binary complement for price.
* **Why rejected:** Separate NO token orderbook has independent bid/ask/depth/freshness.
* **Real-world reason:** Polymarket trades token IDs, not abstract complement prices.
* **Code implication:** live buy-NO requires native NO snapshot.
* **Migration:** `quote_source_type`.
* **Test:** missing native NO quote fail-closed.
* **Rollback:** buy-YES-only live.
* **Does NOT solve:** future negative-risk conversion economics.

### ADR-009 — `OrderPolicy` is part of executable cost basis

* **Decision:** Cost basis includes order policy.
* **Alternatives:** Let adapter choose GTC/GTD/FOK/FAK/post-only late.
* **Why rejected:** Policy changes fill/cost/fee/depth assumptions.
* **Real-world reason:** GTC/GTD/FOK/FAK/post-only have different venue semantics.
* **Code implication:** `OrderPolicy.assert_corrected_supported()`.
* **Migration:** `order_policy_id`.
* **Test:** changing policy changes cost basis, not posterior.
* **Rollback:** only `LIMIT_MAY_TAKE_CONSERVATIVE`.
* **Does NOT solve:** maker/taker optimization.

### ADR-010 — Legacy rows cannot be backfilled as corrected economics

* **Decision:** Old rows default to `legacy_price_probability_conflated`.
* **Alternatives:** Infer corrected economics from `entry_price`, token ticks, or later quotes.
* **Why rejected:** Missing point-in-time depth/order policy/fill facts cannot be reconstructed honestly.
* **Real-world reason:** Historical executable cost is path-dependent and quote-time specific.
* **Code implication:** reports split/hard-fail; backtests diagnostic unless executable snapshot exists.
* **Migration:** additive default legacy flags.
* **Test:** backtests without depth excluded from corrected economics.
* **Rollback:** keep old reports labeled model-only/legacy.
* **Does NOT solve:** future archive with full depth snapshots.

---

## 6. Phased repair plan

### Phase 0 — Live freeze / semantic guardrails

| Item                | Detail                                                                                                                                                            |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Goal                | Prevent unsafe live entry/exit while repair is incomplete.                                                                                                        |
| Why now             | Current path still conflates price/probability/cost; live blocker.                                                                                                |
| Files to read       | `AGENTS.md`, `architecture/invariants.yaml`, `architecture/negative_constraints.yaml`, `src/config/*`, `src/engine/cycle_runtime.py`, `src/execution/executor.py` |
| Files to modify     | config flags, live gate, status summary/operator warning, authority docs                                                                                          |
| New files           | none required                                                                                                                                                     |
| Tests               | `test_live_legacy_semantics_fail_closed_by_default`                                                                                                               |
| Gate                | `CORRECTED_PRICING_LIVE_ENABLED=false`; `ALLOW_LEGACY_VWMP_PRIOR_LIVE=false`; `ALLOW_COMPATIBILITY_ENVELOPE_LIVE=false`                                           |
| Hidden branches     | config typo, operator override, existing open positions                                                                                                           |
| Rollback            | keep all new flags false; diagnostics continue                                                                                                                    |
| Completion evidence | boot/status shows live corrected disabled and legacy live requires explicit ack                                                                                   |

Implementation steps:

```python
def require_live_pricing_gate(config: Config, *, pricing_semantics_version: str) -> None:
    if pricing_semantics_version == "legacy_price_probability_conflated":
        if not config.ALLOW_LEGACY_VWMP_PRIOR_LIVE:
            raise LiveGateError("legacy price/probability conflated semantics are live-disabled")
        raise LiveGateError("legacy semantics cannot generate promotion evidence")

    if pricing_semantics_version == "corrected_executable_cost_v1":
        if not config.CORRECTED_PRICING_LIVE_ENABLED:
            raise LiveGateError("corrected executable pricing is shadow-only")
```

Authority update: add invariants equivalent to “Probability/Quote/Cost Separation”, “Named Market Prior Only”, “ExecutableCostBasis Before Kelly”, “Executor No-Recompute”, and “Exit Symmetry”. Existing invariants already say point-in-time truth beats hindsight, missing data is first-class truth, and Kelly requires executable-price distribution rather than bare static entry price.([GitHub][10])

---

### Phase 1 — Invariant tests and static gates first

| Item                | Detail                                                                                              |
| ------------------- | --------------------------------------------------------------------------------------------------- |
| Goal                | Make bad repairs fail before rewiring money path.                                                   |
| Why now             | Prevent local patches that hide semantic drift.                                                     |
| Files to read       | all files in required search list; tests layout                                                     |
| Files to modify     | tests only, CI/scripts                                                                              |
| New files           | `tests/test_reality_semantics_invariants.py`, `scripts/semantic_gates/check_corrected_semantics.py` |
| Tests               | required unit/static test names from section 8                                                      |
| Gate                | CI fails on known forbidden patterns                                                                |
| Hidden branches     | agent patches docs only, BinEdge god object, executor legacy path alive                             |
| Rollback            | remove CI gate only if false positive blocks unrelated emergency                                    |
| Completion evidence | tests fail on current branch for semantic reasons                                                   |

Static gate skeleton:

```python
# scripts/semantic_gates/check_corrected_semantics.py
FORBIDDEN = [
    ("src/execution/executor.py", "compute_native_limit_price", "corrected executor must not compute limit"),
    ("src/execution/executor.py", ".vwmp", "corrected executor must not use edge.vwmp as price authority"),
    ("src/execution/executor.py", "p_posterior", "corrected executor must not use belief to derive limit"),
    ("src/strategy/kelly.py", "entry_price", "corrected Kelly must consume cost basis"),
    ("src/strategy/market_analysis.py", "1.0 - self.p_market", "NO complement cannot be executable cost"),
    ("src/venue/polymarket_v2_adapter.py", "legacy:{token_id}", "compat envelope cannot be live-certified"),
]
```

Use AST for corrected-path call graphs, not only grep, where practical.

---

### Phase 2 — Add contracts and additive schema, no money-path rewiring

| Item                | Detail                                                                                                                            |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Goal                | Introduce objects without changing live behavior.                                                                                 |
| Why now             | Later phases need typeable authority objects and DB fields.                                                                       |
| Files to read       | `src/contracts/*`, `src/state/db.py`, `src/contracts/venue_submission_envelope.py`                                                |
| Files to modify     | contracts, schema migrations, DB row serializers                                                                                  |
| New files           | `market_prior.py`, `order_policy.py`, `executable_cost_basis.py`, `exit_quote.py`, `position_economics.py`, `reporting_cohort.py` |
| Tests               | contract validation tests                                                                                                         |
| Gate                | contracts reject invalid authority                                                                                                |
| Hidden branches     | old rows missing fields, schema dry-run                                                                                           |
| Rollback            | additive fields ignored; no breaking migration                                                                                    |
| Completion evidence | new contracts import; DB migration dry-run shows defaults classify legacy                                                         |

Schema: additive nullable columns, defaults legacy, no backfill to corrected. Details in section 7.

---

### Phase 3 — Posterior / market-prior split

| Item                | Detail                                                                                          |
| ------------------- | ----------------------------------------------------------------------------------------------- |
| Goal                | Stop raw quote/VWMP from silently becoming posterior evidence.                                  |
| Why now             | Belief plane must be clean before executable economics.                                         |
| Files to read       | `src/strategy/market_analysis.py`, posterior/calibration modules, probability trace persistence |
| Files to modify     | posterior function signature, market analysis adapter layer, probability trace facts            |
| New files           | maybe `src/strategy/posterior.py`                                                               |
| Tests               | `test_market_prior_change_changes_posterior_not_selected_token_snapshot`                        |
| Gate                | raw float vector rejected unless wrapped as `MarketPriorDistribution`                           |
| Hidden branches     | legacy reports expecting `p_market`, monitor sparse vector                                      |
| Rollback            | `model_only_v1` posterior; legacy mode explicit                                                 |
| Completion evidence | no corrected path call to `compute_posterior(p_cal, p_market_float)`                            |

Transition shim:

```python
def build_legacy_vwmp_prior_for_diagnostics(values: list[float], hashes: list[str]) -> MarketPriorDistribution:
    return MarketPriorDistribution(
        values=tuple(values),
        estimator_version=MarketPriorEstimatorVersion.LEGACY_VWMP_PRIOR_V0,
        source_quote_hashes=tuple(hashes),
        family_complete=False,
        vig_treatment=VigTreatment.LEGACY_UNKNOWN,
        freshness_status=FreshnessStatus.UNKNOWN,
        validated_for_live=False,
    )
```

---

### Phase 4 — Executable cost basis + order policy

| Item                | Detail                                                                                                                                |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Goal                | Build token-side executable entry cost before edge/Kelly.                                                                             |
| Why now             | Kelly and FDR must operate on executable hypothesis, not scalar edge.                                                                 |
| Files to read       | `src/contracts/executable_market_snapshot_v2.py`, quote ingestion, evaluator, `market_analysis_family_scan.py`, `selection_family.py` |
| Files to modify     | evaluator, hypothesis builder, cost basis derivation                                                                                  |
| New files           | `src/execution/executable_hypothesis.py`                                                                                              |
| Tests               | quote change changes cost/size/limit but not posterior; order policy changes cost basis not belief                                    |
| Gate                | cost basis required for live economic hypothesis                                                                                      |
| Hidden branches     | stale quote, tick/min-order after sizing, negative-risk metadata                                                                      |
| Rollback            | shadow-only cost basis; no submit                                                                                                     |
| Completion evidence | executable hypotheses contain token_id/snapshot_hash/cost_basis_hash/order_policy                                                     |

Hypothesis identity:

```text
candidate_id
+ bin_id
+ direction
+ selected_token_id
+ executable_snapshot_id
+ executable_snapshot_hash
+ cost_basis_id
+ cost_basis_hash
+ order_policy_id
+ pricing_semantics_version
```

---

### Phase 5 — Live economic FDR and Kelly split

| Item                | Detail                                                                          |
| ------------------- | ------------------------------------------------------------------------------- |
| Goal                | Make FDR-selected row identical to sized executable hypothesis.                 |
| Why now             | Current runtime can mutate decision edge/size after FDR.                        |
| Files to read       | `src/engine/evaluator.py`, `src/engine/cycle_runtime.py`, FDR/selection modules |
| Files to modify     | evaluator, FDR input builder, decision materialization                          |
| New files           | maybe `src/strategy/live_economic_fdr.py`                                       |
| Tests               | `test_executable_fdr_identity_includes_cost_basis`                              |
| Gate                | no late snapshot/cost mutation after FDR                                        |
| Hidden branches     | reprice after FDR, snapshot changed between FDR and submit                      |
| Rollback            | keep legacy FDR diagnostic-only                                                 |
| Completion evidence | decision cannot be repriced without invalidating hypothesis                     |

Current `_reprice_decision_from_executable_snapshot` mutates `decision.edge`, `p_market`, `entry_price`, `vwmp`, and `size_usd` after decision; corrected semantics must reject or recompute FDR on the new fixed executable family.([GitHub][4])

---

### Phase 6 — Executor immutable intent hardening

| Item                | Detail                                                                                                                                    |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Goal                | Remove executor as hidden price authority.                                                                                                |
| Why now             | No live corrected mode before executor no-recompute.                                                                                      |
| Files to read       | `src/execution/executor.py`, `src/contracts/execution_intent.py`, `src/venue/polymarket_v2_adapter.py`, `src/state/venue_command_repo.py` |
| Files to modify     | executor corrected path, intent/envelope validation, command repo gates                                                                   |
| New files           | `src/contracts/corrected_execution_intent.py`                                                                                             |
| Tests               | executor rejects missing cost basis; never recomputes limit; compatibility envelope rejected                                              |
| Gate                | corrected executor accepts only `CorrectedExecutionIntent`                                                                                |
| Hidden branches     | command journal before side effect, crash recovery, fake venue                                                                            |
| Rollback            | corrected live disabled; legacy path opt-in only                                                                                          |
| Completion evidence | AST gate proves corrected executor no `compute_native_limit_price`                                                                        |

Venue command sequence:

```text
intent.assert_submit_ready()
-> envelope = adapter.create_submission_envelope_from_corrected_intent(intent)
-> envelope.assert_corrected_live_certified()
-> venue_command_repo.insert_intent_before_side_effect(envelope, idempotency_key)
-> submit to SDK
-> append venue result event
```

---

### Phase 7 — Buy-NO native quote and complement diagnostic-only

| Item                | Detail                                                                                |
| ------------------- | ------------------------------------------------------------------------------------- |
| Goal                | Prevent NO executable cost from YES complement.                                       |
| Why now             | buy-NO is the seed bug; entry and exit must both be asymmetric.                       |
| Files to read       | `src/strategy/market_analysis.py`, token discovery, evaluator, executor token routing |
| Files to modify     | buy-NO quote builder, live gate                                                       |
| New files           | none required                                                                         |
| Tests               | `test_live_buy_no_rejects_complement_price_without_native_no_orderbook`               |
| Gate                | live buy-NO requires native NO snapshot/cost basis                                    |
| Hidden branches     | binary markets, negative risk, missing no_token_id                                    |
| Rollback            | buy-YES-only corrected live                                                           |
| Completion evidence | complement values only tagged diagnostic                                              |

Rule:

```python
if direction == Direction.BUY_NO and quote_source_type != "native_no_orderbook":
    raise LiveGateError("buy_no executable cost requires native NO token quote")
```

---

### Phase 8 — Monitor / exit quote-belief split

| Item                | Detail                                                                                                                     |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Goal                | Corrected entries cannot exit through legacy quote/probability vector.                                                     |
| Why now             | Entry fixed but exit legacy remains live unsafe.                                                                           |
| Files to read       | `src/engine/monitor_refresh.py`, `src/execution/exit_triggers.py`, `src/state/portfolio.py`, `src/engine/cycle_runtime.py` |
| Files to modify     | monitor refresh split, exit trigger signatures, position methods                                                           |
| New files           | `src/contracts/exit_quote.py`                                                                                              |
| Tests               | buy-NO exit uses best bid; quote changes exit value not posterior; corrected entry cannot use legacy exit fallback         |
| Gate                | corrected position exit requires `ExitExecutableQuote`                                                                     |
| Hidden branches     | stale quote, manual forced exit, RED sweep                                                                                 |
| Rollback            | hold/manual exit; no promotion evidence                                                                                    |
| Completion evidence | no corrected exit path reads `p_market` as sell proceeds                                                                   |

Split monitor:

```text
monitor_probability_refresh(position)
  -> posterior/payoff probability only

monitor_quote_refresh(position)
  -> ExitExecutableQuote(held_token_id, SELL best_bid/depth/fee/snapshot)
```

Current monitor writes quote back into `current_p_market`, recomputes posterior, and builds sparse `p_market` arrays; corrected path must not do this.([GitHub][11])

---

### Phase 9 — Position lot / fill authority split

| Item                | Detail                                                                                                                 |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Goal                | Make fill/partial/cancel/settlement facts explicit.                                                                    |
| Why now             | Reports/settlement cannot trust `entry_price/size_usd`.                                                                |
| Files to read       | `src/engine/cycle_runtime.py`, `src/execution/fill_tracker.py`, `src/execution/harvester.py`, `src/state/portfolio.py` |
| Files to modify     | position materialization, fill tracker, harvester                                                                      |
| New files           | `src/contracts/position_economics.py`                                                                                  |
| Tests               | partial fill updates size/cost; settlement uses filled lot facts only                                                  |
| Gate                | promotion-grade economics require fill authority                                                                       |
| Hidden branches     | delayed fill, crash before materialization, partial exit                                                               |
| Rollback            | legacy positions non-promotion                                                                                         |
| Completion evidence | no corrected PnL from target notional alone                                                                            |

Settlement rule:

```python
def corrected_settlement_pnl(lot: PositionLot, resolved_payout: Decimal) -> Decimal:
    lot.assert_promotion_grade()
    payout = lot.shares_filled * resolved_payout
    return payout - lot.filled_notional_usd
```

Polymarket settlement/redeem reality is token payout: winning tokens worth 1.00, losing tokens worth 0; condition ID and payout vector matter.([Polymarket Documentation][12])

---

### Phase 10 — Reporting/backtest/promotion cohort segregation

| Item                | Detail                                                                                              |
| ------------------- | --------------------------------------------------------------------------------------------------- |
| Goal                | Prevent mixed evidence from becoming promotion evidence.                                            |
| Why now             | Once corrected rows exist, mixed aggregation becomes dangerous.                                     |
| Files to read       | `scripts/profit_validation_replay.py`, `scripts/equity_curve.py`, report scripts, `src/state/db.py` |
| Files to modify     | reports, replay, strategy health, promotion gates                                                   |
| New files           | `src/contracts/reporting_cohort.py`                                                                 |
| Tests               | mixed cohorts hard fail; no-depth backtest excluded                                                 |
| Gate                | promotion report requires single eligible cohort                                                    |
| Hidden branches     | legacy rows, model-only diagnostics, shadow economics                                               |
| Rollback            | label all as diagnostic                                                                             |
| Completion evidence | report output includes cohort and eligibility proof                                                 |

Hard fail:

```python
def assert_report_cohort(rows: list[dict], *, require_corrected: bool) -> None:
    versions = {r["pricing_semantics_version"] for r in rows}
    if len(versions) != 1:
        raise ReportCohortError(f"mixed pricing semantics versions: {versions}")
    if require_corrected and versions != {"corrected_executable_cost_v1"}:
        raise ReportCohortError("promotion report requires corrected executable economics")
```

---

### Phase 11 — Shadow run, telemetry, canary policy

| Item                | Detail                                                                                |
| ------------------- | ------------------------------------------------------------------------------------- |
| Goal                | Observe corrected semantics before tiny live canary.                                  |
| Why now             | Correctness of object identity is necessary but not sufficient for trading promotion. |
| Files to read       | runtime loop, status summary, Discord/operator alerting, risk caps                    |
| Files to modify     | shadow runner, telemetry, promotion gate                                              |
| Tests               | corrected buy_yes/buy_no shadow integration                                           |
| Gate                | canary only after all prior gates + operator ack + caps                               |
| Hidden branches     | fill quality, maker/taker status, adverse selection                                   |
| Rollback            | kill switch disables live; keep shadow                                                |
| Completion evidence | shadow report comparing legacy vs corrected decisions                                 |

Shadow flow:

```text
collect executable snapshots
-> build cost bases
-> build executable hypothesis family
-> live economic FDR
-> final intents
-> do not submit
-> compare legacy vs corrected selection, cost, size, rejection reasons
```

Spec says corrected semantics must ship shadow-only first; canary requires explicit operator flag, tiny caps, kill switch, and promotion requires realized fill quality, maker/taker status, partial fills, cancel remainder, realized fees/slippage, adverse-selection telemetry, settlement reconciliation, and OOS evidence.

---

### Phase 12 — Cleanup legacy shims / docs / authority files

| Item                | Detail                                                          |
| ------------------- | --------------------------------------------------------------- |
| Goal                | Prevent future agents from treating legacy fields as authority. |
| Why now             | After gates pass, old docs become dangerous.                    |
| Files to read       | docs/reference, AGENTS scoped files, math spec, known gaps      |
| Files to modify     | authority docs, deprecations, comments                          |
| Tests               | static docs/gates check forbidden claims                        |
| Gate                | no doc claims `edge = posterior - p_market` as live economics   |
| Hidden branches     | docs updated but gates not, Codex local patch                   |
| Rollback            | keep old docs under `archive/deprecated` with warning           |
| Completion evidence | authority docs point to four-plane model                        |

Existing negative constraints already prohibit ad hoc complement, bare `entry_price` to Kelly, direct venue side effects without gateway, and require append-only venue commands/idempotency; this phase extends them to the corrected pricing split rather than replacing them.([GitHub][13])

---

## 7. Migration and persistence plan

### 7.1 Migration style

* **Additive fields only.**
* New columns nullable except safe legacy defaults.
* No destructive migration in first packet.
* No historical backfill to corrected economics.
* Old rows default to `legacy_price_probability_conflated`.
* Unknown rows are not “probably corrected”; they are `legacy_unknown` / ineligible.
* Dry-run migration before production DB mutation.

### 7.2 Fields to add

#### `trade_decisions`

```sql
ALTER TABLE trade_decisions ADD COLUMN pricing_semantics_version TEXT NOT NULL DEFAULT 'legacy_price_probability_conflated';
ALTER TABLE trade_decisions ADD COLUMN market_prior_version TEXT;
ALTER TABLE trade_decisions ADD COLUMN execution_cost_basis_version TEXT;
ALTER TABLE trade_decisions ADD COLUMN entry_price_authority TEXT NOT NULL DEFAULT 'legacy_unknown';
ALTER TABLE trade_decisions ADD COLUMN entry_cost_source TEXT;
ALTER TABLE trade_decisions ADD COLUMN quote_snapshot_id TEXT;
ALTER TABLE trade_decisions ADD COLUMN quote_snapshot_hash TEXT;
ALTER TABLE trade_decisions ADD COLUMN cost_basis_id TEXT;
ALTER TABLE trade_decisions ADD COLUMN cost_basis_hash TEXT;
ALTER TABLE trade_decisions ADD COLUMN order_policy_id TEXT;
ALTER TABLE trade_decisions ADD COLUMN legacy_price_probability_conflated INTEGER NOT NULL DEFAULT 1;
ALTER TABLE trade_decisions ADD COLUMN corrected_executable_economics_eligible INTEGER NOT NULL DEFAULT 0;
```

#### `probability_trace_fact`

```sql
ALTER TABLE probability_trace_fact ADD COLUMN posterior_mode TEXT;
ALTER TABLE probability_trace_fact ADD COLUMN market_prior_version TEXT;
ALTER TABLE probability_trace_fact ADD COLUMN market_prior_id TEXT;
ALTER TABLE probability_trace_fact ADD COLUMN market_prior_hash TEXT;
ALTER TABLE probability_trace_fact ADD COLUMN market_prior_validated_for_live INTEGER NOT NULL DEFAULT 0;
ALTER TABLE probability_trace_fact ADD COLUMN source_quote_hashes_json TEXT;
```

#### `venue_commands`

```sql
ALTER TABLE venue_commands ADD COLUMN pricing_semantics_version TEXT;
ALTER TABLE venue_commands ADD COLUMN cost_basis_id TEXT;
ALTER TABLE venue_commands ADD COLUMN cost_basis_hash TEXT;
ALTER TABLE venue_commands ADD COLUMN quote_snapshot_id TEXT;
ALTER TABLE venue_commands ADD COLUMN quote_snapshot_hash TEXT;
ALTER TABLE venue_commands ADD COLUMN order_policy_id TEXT;
ALTER TABLE venue_commands ADD COLUMN identity_semantics_version TEXT;
ALTER TABLE venue_commands ADD COLUMN is_compatibility_envelope INTEGER NOT NULL DEFAULT 0;
ALTER TABLE venue_commands ADD COLUMN live_certified INTEGER NOT NULL DEFAULT 0;
```

#### `positions` / current position table

```sql
ALTER TABLE positions ADD COLUMN pricing_semantics_version TEXT NOT NULL DEFAULT 'legacy_price_probability_conflated';
ALTER TABLE positions ADD COLUMN entry_price_authority TEXT NOT NULL DEFAULT 'legacy_unknown';
ALTER TABLE positions ADD COLUMN fill_authority TEXT NOT NULL DEFAULT 'none';
ALTER TABLE positions ADD COLUMN entry_cost_basis_id TEXT;
ALTER TABLE positions ADD COLUMN entry_cost_basis_hash TEXT;
ALTER TABLE positions ADD COLUMN submitted_limit_price REAL;
ALTER TABLE positions ADD COLUMN avg_fill_price REAL;
ALTER TABLE positions ADD COLUMN target_notional_usd REAL;
ALTER TABLE positions ADD COLUMN submitted_notional_usd REAL;
ALTER TABLE positions ADD COLUMN filled_notional_usd REAL;
ALTER TABLE positions ADD COLUMN shares_submitted REAL;
ALTER TABLE positions ADD COLUMN shares_filled REAL;
ALTER TABLE positions ADD COLUMN shares_remaining REAL;
ALTER TABLE positions ADD COLUMN exit_quote_snapshot_id TEXT;
ALTER TABLE positions ADD COLUMN exit_quote_snapshot_hash TEXT;
ALTER TABLE positions ADD COLUMN exit_semantics_version TEXT;
ALTER TABLE positions ADD COLUMN corrected_executable_economics_eligible INTEGER NOT NULL DEFAULT 0;
```

#### Optional new table: `position_lots`

Use if existing `positions` table is too overloaded.

```sql
CREATE TABLE IF NOT EXISTS position_lots (
    lot_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    held_token_id TEXT NOT NULL,
    held_outcome_label TEXT NOT NULL,
    direction TEXT NOT NULL,

    target_notional_usd REAL NOT NULL,
    submitted_notional_usd REAL,
    filled_notional_usd REAL NOT NULL DEFAULT 0,
    submitted_limit_price REAL,
    avg_fill_price REAL,

    shares_submitted REAL,
    shares_filled REAL NOT NULL DEFAULT 0,
    shares_remaining REAL NOT NULL DEFAULT 0,

    entry_cost_basis_id TEXT,
    entry_cost_basis_hash TEXT,
    entry_economics_authority TEXT NOT NULL,
    fill_authority TEXT NOT NULL,
    pricing_semantics_version TEXT NOT NULL,
    corrected_executable_economics_eligible INTEGER NOT NULL DEFAULT 0,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 7.3 Backfill policy

| Row type                                          | Classification                              | Backfill allowed? | Report eligibility                            |
| ------------------------------------------------- | ------------------------------------------- | ----------------: | --------------------------------------------- |
| Old `entry_price=p_market` decision               | `legacy_price_probability_conflated`        |                no | diagnostic only                               |
| Old submitted-limit row without fill facts        | `submitted_limit_legacy` / `legacy_unknown` |                no | not corrected economics                       |
| Old fill row with avg price but no snapshot/depth | `filled_legacy_no_cost_snapshot`            |                no | fill PnL diagnostic, not corrected executable |
| New shadow cost basis no submit                   | `shadow_executable_cost_v1`                 |     yes as shadow | shadow comparison only                        |
| New corrected submit + fill facts                 | `corrected_executable_cost_v1`              |  created natively | promotion eligible if all gates pass          |

### 7.4 Dry-run validation

```bash
sqlite3 state/zeus.db ".schema trade_decisions" > /tmp/schema.before
sqlite3 state/zeus.db "BEGIN; -- apply migration; SELECT pricing_semantics_version, COUNT(*) FROM trade_decisions GROUP BY 1; ROLLBACK;"
python scripts/validate_semantics_migration.py --db state/zeus.db --dry-run
```

Validation rules:

```text
corrected_executable_economics_eligible=1 requires:
- pricing_semantics_version='corrected_executable_cost_v1'
- cost_basis_id/hash present
- quote_snapshot_id/hash present
- order_policy_id present
- entry_price_authority in ('avg_fill_price','corrected_executable_cost_basis')
- fill_authority not in ('none','optimistic_submitted')
```

---

## 8. Test and CI gate plan

### 8.1 Unit invariant tests

| Test file                                    | Test name                                                                                 | Setup                                                         | Assertions                                                                 |
| -------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `tests/test_reality_semantics_invariants.py` | `test_executable_quote_change_changes_cost_size_limit_not_posterior`                      | same posterior, two ask/depth snapshots                       | posterior equal; cost basis hash, final limit, Kelly size differ           |
| same                                         | `test_market_prior_change_changes_posterior_not_selected_token_snapshot`                  | same executable snapshot, different `MarketPriorDistribution` | posterior differs; token/snapshot/cost basis unchanged unless new decision |
| same                                         | `test_order_policy_change_changes_cost_basis_not_model_belief`                            | same posterior/snapshot, two policies                         | posterior same; cost basis/order policy hash differs                       |
| `tests/test_corrected_executor_intent.py`    | `test_corrected_executor_rejects_missing_immutable_final_limit_cost_basis`                | intent without cost basis                                     | raises                                                                     |
| same                                         | `test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp`                   | monkeypatch `compute_native_limit_price` to raise if called   | corrected executor passes without call                                     |
| `tests/test_buy_no_native_quote.py`          | `test_live_buy_no_rejects_complement_price_without_native_no_orderbook`                   | buy-NO with `1-p_yes` quote source                            | live gate raises                                                           |
| `tests/test_monitor_exit_quote_split.py`     | `test_buy_no_exit_uses_best_bid_not_vwmp`                                                 | NO bid 0.42, ask 0.62, VWMP 0.55                              | sell value uses 0.42                                                       |
| same                                         | `test_corrected_entry_cannot_use_legacy_buy_no_exit_fallback`                             | corrected position without `ExitExecutableQuote`              | raises                                                                     |
| `tests/test_venue_identity.py`               | `test_venue_command_market_id_not_token_id_for_live_exit`                                 | command with market_id=token_id                               | live gate raises                                                           |
| same                                         | `test_compatibility_envelope_rejected_in_live`                                            | `legacy:{token_id}` envelope                                  | live certification rejects                                                 |
| `tests/test_position_fill_authority.py`      | `test_position_entry_price_authority_required_before_exit_or_settlement`                  | legacy authority                                              | corrected settlement/report rejects                                        |
| same                                         | `test_partial_fill_updates_size_cost_basis_and_report_authority`                          | partial fill then cancel                                      | filled_notional/shares filled only; target remainder not PnL               |
| `tests/test_reporting_cohorts.py`            | `test_reports_hard_fail_mixed_pricing_semantics_cohorts`                                  | legacy + corrected rows                                       | report raises                                                              |
| same                                         | `test_backtests_without_point_in_time_depth_excluded_from_corrected_executable_economics` | historical tick no depth                                      | corrected economics excluded                                               |

### 8.2 Integration money-path tests

1. `tests/integration/test_corrected_buy_yes_shadow_path.py`
   Builds model-only posterior, selected YES token snapshot, cost basis, FDR hypothesis, immutable intent; no submit.

2. `tests/integration/test_corrected_buy_no_shadow_native_quote.py`
   Same as above but selected NO token must have native NO orderbook.

3. `tests/integration/test_buy_no_missing_native_quote_fail_closed.py`
   NO complement exists, native NO quote missing; live gate raises.

4. `tests/integration/test_monitor_quote_changes_exit_value_not_posterior.py`
   Change held token bid/depth; posterior trace hash unchanged, exit quote hash changes.

5. `tests/integration/test_executor_crash_recovery_after_command_journal_insert.py`
   Journal command inserted before SDK submit; restart recovers pending/unknown idempotency state.

6. `tests/integration/test_partial_fill_then_cancel_remainder.py`
   Fill tracker records partial fill; cancel event leaves residual exposure; report uses filled lot only.

7. `tests/integration/test_mixed_report_cohort_hard_fail.py`
   Mixed legacy/corrected rows hard fail promotion report.

### 8.3 Static / semantic gates

**Script:** `scripts/semantic_gates/check_corrected_semantics.py`.

Fail conditions:

```text
- corrected executor calls compute_native_limit_price
- corrected executor references edge.vwmp, BinEdge.vwmp, p_posterior, p_market for limit
- corrected Kelly path passes p_market/vwmp/entry_price instead of ExecutableEntryCostBasis
- live buy_no path uses 1 - YES quote as executable cost
- corrected exit path uses p_market/current_market_price as sell proceeds
- report aggregates multiple pricing_semantics_version values without explicit cohort split
- live envelope has condition_id like legacy:{token_id}
- live venue command market_id equals selected token_id
- BinEdge.vwmp becomes required cost authority
- compatibility envelope marked live_certified
```

Commands:

```bash
python scripts/semantic_gates/check_corrected_semantics.py
python -m pytest \
  tests/test_reality_semantics_invariants.py \
  tests/test_corrected_executor_intent.py \
  tests/test_buy_no_native_quote.py \
  tests/test_monitor_exit_quote_split.py \
  tests/test_position_fill_authority.py \
  tests/test_reporting_cohorts.py
```

---

## 9. Live safety and operator policy

### 9.1 Flags and defaults

```text
CORRECTED_PRICING_SHADOW_ENABLED=false
CORRECTED_PRICING_LIVE_ENABLED=false
ALLOW_LEGACY_VWMP_PRIOR_LIVE=false
ALLOW_COMPATIBILITY_ENVELOPE_LIVE=false
NATIVE_BUY_NO_LIVE_ENABLED=false
STRICT_CORRECTED_EXIT_REQUIRED=true
REPORT_MIXED_COHORTS_ALLOWED=false
OPERATOR_ACK_REALITY_SEMANTICS_VERSION=<required exact hash>
```

A missing flag is unsafe, not permissive. A malformed boolean is unsafe, not permissive.

### 9.2 Operator opt-in

To enable even tiny corrected live canary:

```text
CORRECTED_PRICING_LIVE_ENABLED=true
CORRECTED_PRICING_SHADOW_ENABLED=true
STRICT_CORRECTED_EXIT_REQUIRED=true
ALLOW_COMPATIBILITY_ENVELOPE_LIVE=false
ALLOW_LEGACY_VWMP_PRIOR_LIVE=false
max_daily_notional_usd <= tiny cap
max_position_count <= tiny cap
order_policy_id = LIMIT_MAY_TAKE_CONSERVATIVE
OPERATOR_ACK_REALITY_SEMANTICS_VERSION must match current manifest hash
```

### 9.3 Fail-closed conditions

Live entry rejects when:

```text
- no ExecutableEntryCostBasis
- no final_limit_price or cost_basis_hash
- quote snapshot stale
- tick/min-order/depth validation fails
- order policy not corrected-supported
- executor would need to reprice
- selected token not in envelope YES/NO pair
- compatibility envelope identity present
- buy_no lacks native NO token quote
- corrected exit path unavailable for resulting position
```

### 9.4 Operator-facing warning

`status_summary.json` and alerts must state:

```text
REALITY_SEMANTICS_STATUS:
  live_entry: disabled | shadow | canary
  legacy_vwmp_prior_live: forbidden unless explicit non-promotion opt-in
  corrected_exit_required: true
  report_promotion_eligible: false until corrected cohort evidence exists
```

---

## 10. Monitor/exit and open-position policy

### 10.1 Existing open positions

| Position type                                  | Monitor allowed? |                                                       Exit allowed? |             Promotion evidence? | Policy                                   |
| ---------------------------------------------- | ---------------: | ------------------------------------------------------------------: | ------------------------------: | ---------------------------------------- |
| Legacy entry, no cost basis                    |  yes, diagnostic |      yes, only legacy/manual or corrected quote if held token known |                              no | tag `legacy_price_probability_conflated` |
| Legacy entry, held token known                 |              yes | corrected sell quote may support safer exit, but PnL remains legacy |                              no | preserve legacy entry authority          |
| Corrected shadow, no submit/fill               |       yes shadow |                                                        no real exit |                              no | shadow only                              |
| Corrected live submitted, no fill confirmation |  yes operational |                                       cancel/track; no economic PnL |                              no | `optimistic_submitted`                   |
| Corrected partial fill                         |              yes |                       exit remaining filled shares under sell quote |      maybe after fill authority | lot-level                                |
| Corrected full fill                            |              yes |                                                      corrected exit | eligible after all report gates | lot-level                                |

### 10.2 Corrected exit policy

* `ExitExecutableQuote` required for both buy-YES and buy-NO.
* Stale quote blocks automated exit unless forced manual.
* Forced/manual exit must persist `forced_manual=True`, `force_reason`, and is not promotion-grade unless later reconciled.
* RED risk may cancel pending orders immediately, but forced liquidation still needs held-token quote or explicit operator override.

### 10.3 Buy-YES / buy-NO symmetry

Corrected exit symmetry:

```text
buy_yes held token = YES token -> SELL YES token bid/depth
buy_no  held token = NO token  -> SELL NO token bid/depth
```

No `p_market`, no `current_market_price`, no `1 - YES quote`.

### 10.4 Open legacy positions and settlement

Legacy positions can be monitored and settled, but their settlement PnL must carry `entry_price_authority=legacy_unknown|submitted_limit|model_edge_price`. They cannot enter corrected executable economics reports.

---

## 11. Reporting/backtest/promotion policy

### 11.1 Evidence classes

| Class                                    | Meaning                                           | Can block promotion? |        Can prove promotion? |
| ---------------------------------------- | ------------------------------------------------- | -------------------: | --------------------------: |
| Model diagnostic                         | posterior/calibration skill only                  |                  yes |                          no |
| Legacy economics                         | old `price/entry_price/p_market` conflated        |                  yes |                          no |
| Shadow executable economics              | cost basis built but not submitted/fill-confirmed |                  yes |                          no |
| Submitted-only economics                 | command submitted, fill unknown                   |                  yes |                          no |
| Fill-confirmed corrected economics       | cost basis + submitted command + fill facts       |                  yes |                     partial |
| Settlement-confirmed corrected economics | fill facts + settlement payout                    |                  yes | yes, with enough sample/OOS |

### 11.2 Report hard-fail conditions

Reports hard-fail when:

```text
- multiple pricing_semantics_version values in one economics aggregate
- corrected report includes legacy_price_probability_conflated rows
- promotion report includes shadow-only or submitted-only rows
- backtest lacks point-in-time executable snapshot/depth but labels output corrected
- fill_authority is none/optimistic for PnL report
- exit_semantics_version missing for exit performance report
```

### 11.3 Backtest policy

Backtests without point-in-time executable depth/snapshot are:

```text
model_only/research diagnostics
not corrected executable economics
not live promotion evidence
```

Historical rows with token ticks can estimate diagnostic price trajectories, but cannot claim the order Zeus would have submitted, filled, partially filled, or canceled under the live policy.

---

## 12. Hidden branch register

|  # | Branch                                           | Risk                         | Affected phase | Decision                                                                 | Test/gate                          | Rollback/escalation |
| -: | ------------------------------------------------ | ---------------------------- | -------------: | ------------------------------------------------------------------------ | ---------------------------------- | ------------------- |
|  1 | Legacy live fail-open                            | unsafe live orders           |              0 | default fail-closed                                                      | live gate test                     | disable live        |
|  2 | Config typo enables unsafe path                  | typo becomes permission      |              0 | missing/malformed false                                                  | config parser test                 | halt                |
|  3 | Operator opt-in without ack                      | accidental canary            |           0/11 | require manifest hash                                                    | operator ack test                  | shadow only         |
|  4 | Existing legacy open positions                   | mixed entry semantics        |             10 | tag legacy                                                               | open position classifier           | monitor-only        |
|  5 | Corrected entry but legacy exit                  | safe buy, unsafe sell        |              8 | strict corrected exit                                                    | corrected entry cannot legacy exit | no new entry        |
|  6 | Partial fill then cancel                         | target PnL corruption        |              9 | lot-level fill                                                           | partial fill test                  | exclude report      |
|  7 | Fill status delayed                              | optimistic exposure mistaken |              9 | fill authority enum                                                      | delayed fill test                  | pending/unknown     |
|  8 | Crash after cost certificate before command      | orphan cost basis            |            6/9 | recoverable certificate state                                            | crash recovery test                | expire certificate  |
|  9 | Crash after journal before submit                | duplicate/unknown order      |              6 | idempotency before side effect                                           | command journal test               | reconcile           |
| 10 | Submit accepted, position materialization failed | ghost venue exposure         |              9 | command-to-position recovery                                             | integration test                   | quarantine          |
| 11 | Quote snapshot stale                             | stale cost basis             |          4/6/8 | max age gate                                                             | freshness test                     | reject/recompute    |
| 12 | FDR uses snapshot A, executor snapshot B         | materialization drift        |            5/6 | hypothesis hash match                                                    | snapshot mismatch test             | recompute FDR       |
| 13 | Executor late reprice                            | hidden price authority       |              6 | no-recompute                                                             | AST gate                           | reject intent       |
| 14 | Native NO quote missing                          | fake NO cost                 |              7 | fail-closed                                                              | buy-NO missing quote test          | buy-YES only        |
| 15 | Binary complement reused as quote                | false symmetry               |              7 | diagnostic only                                                          | complement gate                    | shadow only         |
| 16 | Negative-risk standard market                    | payoff coupling missing      |            4/7 | carry metadata, block unsupported                                        | neg-risk gate                      | exclude             |
| 17 | Augmented neg-risk placeholder/Other             | changing outcome set         |            4/7 | block augmented first packet                                             | augmented market test              | exclude             |
| 18 | High vs low temperature metrics                  | physical quantity drift      |           3/10 | carry `temperature_metric`                                               | metric identity test               | report split        |
| 19 | market_id/token_id collision                     | lineage corruption           |              6 | reject collision                                                         | venue identity test                | quarantine command  |
| 20 | Compatibility envelope live                      | fake market identity         |              6 | live forbid                                                              | compatibility envelope test        | fake/test only      |
| 21 | Fee unavailable or zero fallback                 | underpriced cost             |            4/6 | fee source required                                                      | fee missing test                   | reject              |
| 22 | Tick/min-order checked after sizing              | rejected orders or drift     |              4 | check before Kelly/intent                                                | tick/min-order tests               | reject              |
| 23 | Adapter silently changes order type              | cost policy drift            |              6 | policy hash in envelope                                                  | order policy match test            | reject              |
| 24 | Monitor quote changes posterior                  | evidence corruption          |              8 | split refresh                                                            | quote-only posterior test          | shadow only         |
| 25 | Partial exit fill not reducing exposure          | over/under exposure          |              9 | lot remaining shares                                                     | partial exit test                  | manual reconcile    |
| 26 | RED sweep proxy-only exit                        | emergency wrong sell value   |           8/11 | held-token quote or explicit override                                    | RED sweep gate                     | operator manual     |
| 27 | Report mixed cohorts                             | false promotion              |             10 | hard fail                                                                | mixed cohort test                  | diagnostic only     |
| 28 | Backtest no depth snapshot                       | fake corrected economics     |             10 | exclude                                                                  | no-depth test                      | model-only          |
| 29 | Agent patches `BinEdge` god object               | semantic drift preserved     |           1/12 | static gate/no authority                                                 | BinEdge authority grep             | reject PR           |
| 30 | Docs updated but gates not                       | paper architecture only      |           1/12 | tests before docs closeout                                               | CI required                        | reject PR           |
| 31 | CLOB scalar `getPrice` side ambiguity            | wrong side interpretation    |            4/8 | derive from orderbook bids/asks and envelope side, not scalar side label | quote derivation test              | reject scalar cost  |
| 32 | Model skill marketed as economics                | bad promotion                |          10/11 | promotion requires fill/settlement economics                             | promotion gate                     | block               |

Negative-risk note: Polymarket negative-risk mechanics allow NO shares in one market to convert into YES shares in every other market; augmented negative-risk markets can include placeholders/Other whose meanings change. First packet must carry/block this metadata; it must not claim solved conversion/arbitrage.([Polymarket Documentation][14])

---

## 13. Not-now list

Do **not** implement now:

1. Full fill-probability model — first fix object identity.
2. Queue priority model — requires realized telemetry after corrected cost basis.
3. Adverse-selection model — promotion telemetry dependency, not prerequisite.
4. Maker/taker optimization — first packet assumes worst-case taker.
5. `POST_ONLY_PASSIVE_LIMIT` live mode — separate policy with reject-if-cross semantics.
6. `MARKETABLE_LIMIT_DEPTH_BOUND` — requires depth-weighted curve and FAK/FOK semantics.
7. Live `yes_family_devig_v1` market prior — needs OOS Brier/ROI and negative-risk validation.
8. Negative-risk conversion/arbitrage estimator — future specialist model.
9. Corrected historical economics without depth snapshots — impossible honestly.
10. Large parallel venue model — reuse existing adapter/envelope; add contracts/gates.
11. Strategy promotion from model skill only — model skill can block, not prove economics.
12. Broad rewrite of `BinEdge` into a bigger authority object — wrong center of gravity.
13. Automatic migration of legacy open positions to corrected economics — false provenance.
14. Complex liquidation optimizer — held-token sell quote first.
15. Report dashboards that aggregate mixed cohorts with warning-only labels — hard fail required.

---

## 14. Codex execution packet

### Prompt Phase 0 — Live freeze / guardrails

```text
You are modifying Zeus plan-pre5. Scope: add fail-closed live pricing gates only.

Read first:
- AGENTS.md
- architecture/invariants.yaml
- architecture/negative_constraints.yaml
- src/engine/cycle_runtime.py
- src/execution/executor.py
- src/config/*

Allowed files:
- config/live flags files
- status summary/operator warning code
- architecture/invariants.yaml
- architecture/negative_constraints.yaml
- tests for gates

Forbidden:
- do not rewire pricing, Kelly, executor, or reports yet
- do not enable corrected live
- do not mutate production DB

Implement:
- CORRECTED_PRICING_LIVE_ENABLED default false
- CORRECTED_PRICING_SHADOW_ENABLED default false
- ALLOW_LEGACY_VWMP_PRIOR_LIVE default false
- ALLOW_COMPATIBILITY_ENVELOPE_LIVE default false
- STRICT_CORRECTED_EXIT_REQUIRED default true
- missing/malformed flags fail closed
- operator-facing status warning

Tests:
- test_live_legacy_semantics_fail_closed_by_default
- test_config_typo_cannot_enable_unsafe_live
- test_operator_ack_required_for_corrected_live

Run:
- python -m pytest tests/test_live_pricing_gates.py

Closeout:
- list exact files changed
- paste failing-before/passing-after test output
- state no money-path rewiring was done
```

### Prompt Phase 1 — Tests and static gates

```text
Scope: create semantic invariant tests and static gates. Tests may fail before implementation.

Read:
- src/strategy/market_analysis.py
- src/engine/evaluator.py
- src/execution/executor.py
- src/engine/monitor_refresh.py
- src/state/portfolio.py
- src/venue/polymarket_v2_adapter.py
- scripts/report/replay files

Allowed:
- tests/*
- scripts/semantic_gates/*
- CI config if present

Forbidden:
- do not modify production code to make tests pass in this phase

Create tests:
- test_executable_quote_change_changes_cost_size_limit_not_posterior
- test_market_prior_change_changes_posterior_not_selected_token_snapshot
- test_order_policy_change_changes_cost_basis_not_model_belief
- test_corrected_executor_rejects_missing_immutable_final_limit_cost_basis
- test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp
- test_live_buy_no_rejects_complement_price_without_native_no_orderbook
- test_buy_no_exit_uses_best_bid_not_vwmp
- test_corrected_entry_cannot_use_legacy_buy_no_exit_fallback
- test_venue_command_market_id_not_token_id_for_live_exit
- test_compatibility_envelope_rejected_in_live
- test_position_entry_price_authority_required_before_exit_or_settlement
- test_partial_fill_updates_size_cost_basis_and_report_authority
- test_reports_hard_fail_mixed_pricing_semantics_cohorts
- test_backtests_without_point_in_time_depth_excluded_from_corrected_executable_economics

Create:
- scripts/semantic_gates/check_corrected_semantics.py

Closeout:
- report which tests currently fail and why
- do not mark failures as regressions; they are expected red tests
```

### Prompt Phase 2 — Contracts and schema additive

```text
Scope: add contracts and additive schema fields without rewiring live money path.

Read:
- src/contracts/*
- src/state/db.py
- migration patterns
- tests from Phase 1

Allowed:
- src/contracts/market_prior.py
- src/contracts/order_policy.py
- src/contracts/executable_cost_basis.py
- src/contracts/exit_quote.py
- src/contracts/position_economics.py
- src/contracts/reporting_cohort.py
- src/state/db.py additive migration only
- tests for contracts/migration

Forbidden:
- no deletion/rename of existing DB columns
- no backfill of legacy rows to corrected
- no executor/evaluator rewiring

Implement:
- MarketPriorDistribution
- OrderPolicy
- ExecutableEntryCostBasis
- ExitExecutableQuote
- PositionLot / authority enums
- ReportingCohort / PricingSemanticsVersion
- additive DB columns with legacy defaults

Run:
- python -m pytest tests/test_contracts_reality_semantics.py tests/test_migration_semantics_fields.py

Closeout:
- schema diff
- prove old rows default legacy/ineligible
```

### Prompt Phase 3 — Posterior/prior split

```text
Scope: make corrected posterior consume MarketPriorDistribution | None, never raw quote floats.

Read:
- src/strategy/market_analysis.py
- posterior/calibration modules
- probability trace persistence

Allowed:
- posterior functions
- market analysis adapter layer
- probability trace writer
- tests

Forbidden:
- do not change executor
- do not change Kelly
- do not enable live

Implement:
- compute_posterior(p_cal_yes, market_prior, posterior_mode, alpha)
- model_only_v1 default corrected baseline
- legacy_vwmp_prior_v0 explicit transition wrapper
- yes_family_devig_v1_shadow shadow-only validation
- reject raw quote vector in corrected path

Tests:
- market prior changes posterior not token/snapshot
- quote ask/depth change does not alter posterior
- legacy prior not live eligible

Run:
- python -m pytest tests/test_reality_semantics_invariants.py tests/test_market_prior_distribution.py

Closeout:
- grep/callgraph showing corrected compute_posterior has no raw p_market float
```

### Prompt Phase 4 — Executable cost basis + order policy

```text
Scope: build ExecutableEntryCostBasis from ExecutableMarketSnapshotV2 plus OrderPolicy.

Read:
- src/contracts/executable_market_snapshot_v2.py
- src/engine/evaluator.py
- src/strategy/market_analysis.py
- selection/FDR modules

Allowed:
- cost basis derivation code
- executable hypothesis builder
- evaluator shadow path
- tests

Forbidden:
- no live submit
- no executor final rewiring yet
- no parallel venue model

Implement:
- derive_entry_cost_basis(snapshot, selected_token_id, direction, order_policy, intended_notional)
- LIMIT_MAY_TAKE_CONSERVATIVE mapping and validation
- cost_basis_id/hash
- tick/min-order/depth/freshness checks
- executable hypothesis identity includes token/snapshot/cost_basis/order_policy

Tests:
- executable quote change changes cost/size/limit not posterior
- order policy changes cost basis not belief
- stale quote rejects cost basis

Run:
- python -m pytest tests/test_executable_cost_basis.py tests/test_reality_semantics_invariants.py

Closeout:
- example cost basis object with hash
```

### Prompt Phase 5 — Live economic FDR / no late mutation

```text
Scope: ensure selected FDR row is same executable hypothesis that is sized.

Read:
- src/engine/evaluator.py
- src/engine/cycle_runtime.py
- market_analysis_family_scan / selection_family modules
- Phase 4 executable hypothesis builder

Allowed:
- FDR input builder
- decision materialization
- shadow-only live economic FDR path
- tests

Forbidden:
- no live submit
- do not mutate decision.edge after FDR in corrected path
- do not make BinEdge authority

Implement:
- fixed executable family before FDR
- hypothesis id includes cost_basis_id/hash
- if snapshot/cost changes after FDR, reject or recompute FDR
- corrected path never calls _reprice_decision_from_executable_snapshot

Tests:
- FDR identity includes cost_basis
- late reprice invalidates hypothesis
- runtime cannot mutate decision edge/size after corrected FDR

Run:
- python -m pytest tests/test_live_economic_fdr_identity.py

Closeout:
- before/after trace of one shadow hypothesis selected and materialized
```

### Prompt Phase 6 — Executor immutable final intent

```text
Scope: corrected executor validates immutable final intent and submits/rejects only.

Read:
- src/execution/executor.py
- src/contracts/execution_intent.py
- src/contracts/venue_submission_envelope.py
- src/venue/polymarket_v2_adapter.py
- src/state/venue_command_repo.py

Allowed:
- corrected execution intent contract
- executor corrected method
- envelope validation
- venue command repo gates
- tests/static gate

Forbidden:
- do not remove legacy path
- do not let corrected path compute limit from posterior/vwmp
- do not allow compatibility envelope live

Implement:
- execute_final_intent(CorrectedExecutionIntent)
- intent.assert_submit_ready()
- envelope.assert_corrected_live_certified()
- command journal before side effect
- reject market_id/token_id collision
- reject legacy:{token_id}

Tests:
- corrected executor rejects missing final limit/cost basis
- corrected executor never recomputes limit
- compatibility envelope rejected in live
- market_id/token_id collision rejected

Run:
- python scripts/semantic_gates/check_corrected_semantics.py
- python -m pytest tests/test_corrected_executor_intent.py tests/test_venue_identity.py

Closeout:
- AST/static gate output proving no recompute
```

### Prompt Phase 7 — Buy-NO native quote

```text
Scope: live buy_no executable cost requires native NO token quote.

Read:
- src/strategy/market_analysis.py
- token discovery
- evaluator/cost basis builder

Allowed:
- buy_no quote source tagging
- live gate
- tests

Forbidden:
- do not use 1 - YES quote as executable NO cost
- do not enable buy_no live

Implement:
- quote_source_type enum
- complement diagnostic-only tag
- live gate requires native_no_orderbook for buy_no
- binary complement remains payoff-probability only

Tests:
- live buy_no rejects complement without native NO quote
- buy_no payoff probability still equals 1 - posterior_yes
- native NO quote accepted into cost basis

Run:
- python -m pytest tests/test_buy_no_native_quote.py

Closeout:
- show buy_no diagnostic vs executable path separation
```

### Prompt Phase 8 — Monitor/exit split

```text
Scope: split monitor posterior refresh from held-token sell quote; corrected exit uses ExitExecutableQuote.

Read:
- src/engine/monitor_refresh.py
- src/execution/exit_triggers.py
- src/state/portfolio.py
- src/engine/cycle_runtime.py

Allowed:
- monitor quote refresh
- exit quote contract integration
- exit trigger signatures
- tests

Forbidden:
- no p_market/current_market_price fallback for corrected exit
- no 1 - YES quote as NO sell quote
- no entry_price as current exit value

Implement:
- monitor_probability_refresh()
- monitor_quote_refresh()
- ExitExecutableQuote for buy_yes and buy_no
- corrected_exit_ev()
- stale quote blocks automated corrected exit
- forced manual exit persisted and non-promotion

Tests:
- buy_no exit uses best_bid not vwmp
- corrected entry cannot use legacy buy_no exit fallback
- monitor quote changes exit value but not posterior

Run:
- python -m pytest tests/test_monitor_exit_quote_split.py

Closeout:
- trace one buy_no position exit EV using held NO bid
```

### Prompt Phase 9 — Position lot / fill authority

```text
Scope: split target/submitted/fill/cancel/settlement economics.

Read:
- src/engine/cycle_runtime.py
- src/execution/fill_tracker.py
- src/execution/harvester.py
- src/state/portfolio.py
- DB schema

Allowed:
- position materialization
- fill tracker
- settlement harvester
- position_lots if needed
- tests

Forbidden:
- do not compute corrected PnL from target size or edge entry_price
- do not mark optimistic submitted as promotion eligible

Implement:
- target_notional_usd
- submitted_notional_usd
- filled_notional_usd
- submitted_limit_price
- avg_fill_price
- shares_submitted / filled / remaining
- EntryEconomicsAuthority
- FillAuthority
- partial fill then cancel remainder

Tests:
- position entry price authority required before exit/settlement
- partial fill updates size/cost/report authority
- settlement uses filled lot facts only

Run:
- python -m pytest tests/test_position_fill_authority.py

Closeout:
- sample partial fill lot before/after cancel
```

### Prompt Phase 10 — Reporting/backtest cohort gates

```text
Scope: reports/backtests/promotion cannot mix semantics cohorts.

Read:
- scripts/profit_validation_replay.py
- scripts/equity_curve.py
- report modules
- strategy health/promotion gates
- state DB schema

Allowed:
- report cohort contract
- report query filters
- backtest labeling
- tests

Forbidden:
- no warning-only mixed cohort behavior
- no backfill old rows as corrected
- no model-only report named live economics

Implement:
- ReportingCohort hard fail
- corrected report requires corrected_executable_economics_eligible=1
- historical rows with missing depth excluded
- output labels: diagnostic, shadow, corrected executable

Tests:
- reports hard fail mixed pricing semantics cohorts
- backtests without point-in-time depth excluded

Run:
- python -m pytest tests/test_reporting_cohorts.py

Closeout:
- example report failure and corrected cohort report
```

### Prompt Phase 11 — Shadow / canary telemetry

```text
Scope: corrected semantics shadow runner and tiny canary gates.

Read:
- runtime loop
- status summary
- risk caps
- operator runbook
- telemetry/report modules

Allowed:
- shadow runner
- telemetry fields
- canary gating
- status output
- tests

Forbidden:
- no promotion claims
- no canary unless all gates pass
- no default live enable

Implement:
- shadow corrected flow without submit
- comparison report legacy vs corrected
- canary preflight requiring all flags/tests/caps
- telemetry: fill quality, maker/taker, partial/cancel, realized fee, slippage, exit behavior

Tests:
- corrected buy_yes shadow path
- corrected buy_no shadow native quote path
- canary preflight rejects missing telemetry gates

Run:
- python -m pytest tests/integration/test_corrected_buy_yes_shadow_path.py tests/integration/test_corrected_buy_no_shadow_native_quote.py

Closeout:
- shadow report artifact and canary preflight state
```

### Prompt Phase 12 — Cleanup docs / authority

```text
Scope: demote legacy docs and align authority files with implemented gates.

Read:
- AGENTS.md
- scoped AGENTS files
- docs/reference/zeus_math_spec.md
- docs/reference/zeus_execution_lifecycle_reference.md
- architecture/invariants.yaml
- architecture/negative_constraints.yaml

Allowed:
- docs/architecture/reference authority files
- tests checking docs/gates consistency

Forbidden:
- no code behavior changes unless tests expose doc/code contradiction
- no deletion without archive/deprecated marker

Implement:
- four-plane model in authority docs
- mark legacy VWMP edge formula diagnostic-only
- mark old entry_price semantics deprecated
- document corrected money path and promotion gates
- add doc static checks for forbidden live claims

Tests:
- doc/gate consistency check
- semantic gate still passes

Run:
- python scripts/semantic_gates/check_corrected_semantics.py
- python -m pytest tests/test_docs_semantic_authority.py

Closeout:
- list deprecated docs/sections and new authority path
```

---

## 15. Final self-check

1. **Did I solve the real live-money semantic bug or only rename fields?**
   This packet solves the object boundary: belief, prior, executable cost/quote, and lifecycle facts become separate contracts with gates. It does not merely rename `p_market`.

2. **Did I preserve quote/prior/probability/cost/lifecycle separation?**
   Yes. Raw quote cannot enter posterior; posterior cannot become cost; cost cannot become fill; fill cannot become report cohort without authority.

3. **Did I prevent executor from becoming price authority?**
   Yes. Corrected executor accepts immutable `CorrectedExecutionIntent`; static and unit gates ban recompute from posterior/VWMP.

4. **Did I make exit symmetry as strong as entry?**
   Yes. Corrected entry is not live-eligible unless corrected held-token SELL quote exit exists for buy-YES and buy-NO.

5. **Did I prevent report/backtest cohort contamination?**
   Yes. Mixed `pricing_semantics_version` aggregation hard-fails; historical no-depth rows cannot become corrected economics.

6. **Did I avoid overbuilding fill probability/adverse selection too early?**
   Yes. First packet assumes `LIMIT_MAY_TAKE_CONSERVATIVE`, worst-case taker fee, conditional-on-fill Kelly, and records telemetry for later promotion.

7. **Did I make bad agent execution fail tests/gates?**
   Yes. Static gates catch executor recompute, Kelly scalar cost, complement executable NO, compatibility live envelope, token/market collision, and mixed reports.

8. **Did I identify irreversible decisions and rollback paths?**
   Yes. The only irreversible-ish move is schema additive classification of old rows as legacy/ineligible; rollback is safe because fields are additive and live flags remain false.

9. **Did I preserve uncertainty where reachability is not proven?**
   Yes. F-05 exact previous branch remains partially confirmed; all compatibility/identity risks are still gated because they are live-money unsafe if reachable.

10. **Is this architecture actually consistent with real quant trading and Polymarket venue semantics?**
    Yes. It treats settlement probability, market-prior estimator, token-side orderbook cost, submitted order, fill facts, and settlement payout as different real-world objects, matching CLOB token/orderbook/order/fee realities rather than Zeus’s overloaded local scalars.

[1]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/src/contracts/execution_price.py "raw.githubusercontent.com"
[2]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/execution/executor.py "zeus/src/execution/executor.py at plan-pre5 · fitz-s/zeus · GitHub"
[3]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/state/portfolio.py "zeus/src/state/portfolio.py at plan-pre5 · fitz-s/zeus · GitHub"
[4]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/engine/cycle_runtime.py "zeus/src/engine/cycle_runtime.py at plan-pre5 · fitz-s/zeus · GitHub"
[5]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/AGENTS.md "raw.githubusercontent.com"
[6]: https://docs.polymarket.com/market-data/overview "Overview - Polymarket Documentation"
[7]: https://docs.polymarket.com/trading/orders/overview "Overview - Polymarket Documentation"
[8]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/strategy/market_analysis.py "zeus/src/strategy/market_analysis.py at plan-pre5 · fitz-s/zeus · GitHub"
[9]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/venue/polymarket_v2_adapter.py "zeus/src/venue/polymarket_v2_adapter.py at plan-pre5 · fitz-s/zeus · GitHub"
[10]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/architecture/invariants.yaml "raw.githubusercontent.com"
[11]: https://github.com/fitz-s/zeus/blob/plan-pre5/src/engine/monitor_refresh.py "zeus/src/engine/monitor_refresh.py at plan-pre5 · fitz-s/zeus · GitHub"
[12]: https://docs.polymarket.com/trading/ctf/redeem "Redeem Tokens - Polymarket Documentation"
[13]: https://raw.githubusercontent.com/fitz-s/zeus/plan-pre5/architecture/negative_constraints.yaml "raw.githubusercontent.com"
[14]: https://docs.polymarket.com/advanced/neg-risk "Negative Risk Markets - Polymarket Documentation"
