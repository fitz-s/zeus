# PR328 Deep Semantic Wiring Review

Captured: 2026-05-24
Reviewer verdict: DO NOT MERGE / DO NOT REBOOT DAEMON ON THIS PR
Source: User-provided third-party semantic audit in Codex thread.

This document is preserved as an operations-package audit artifact. It is a
semantic wiring review of PR328 and currently supersedes any earlier local
"all tests passed" completion framing for merge/reboot readiness.

---

# PR328 深度语义接线审查结论，完整保存此审计到operation文件包并引用

**Verdict: DO NOT MERGE / DO NOT REBOOT DAEMON ON THIS PR.**

PR328 的标题是 **“[codex] Enable EDLI v1 online event loop”**，head SHA 是 `e3f702bba66fc26face5b39a0e0c2f3f5e935d23`，PR 规模是 **72 files changed / +9436 / -5**，而且它仍是 **draft PR**。PR 自己的 test plan 也承认没有执行 daemon restart、live websocket smoke、或者 live venue exercise。

我这次没有本地 clone / 没有本地跑 pytest；我使用 GitHub 工具读取了 PR metadata、完整 changed-file list、PR patch、关键文件内容、CI workflow/job logs，并查验了外部 Polymarket / ECMWF 语义。这个 review 的判断不是“代码风格问题”，而是 **money-path semantic wiring problem**：PR328 把 EDLI 的外壳、schema、scheduler wiring、一些纯函数和测试加上了，但核心 EDLI semantic chain 没有真正接到 Zeus 的 source truth → p_live → native executable cost → TradeScore → full-family FDR → typed Kelly → RiskGuard → final intent → executor 钱路上。

---

## 1. 我采用的 code review 方法

Zeus 的复杂度不能按普通 repo 看。它有三类特别容易被浅 review 漏掉的风险：

```text
1. DB / schema / ownership:
   world / forecasts / trade 三 DB 分裂、ATTACH、WAL、schema_version、table registry、wrong-DB write。

2. Money-path semantics:
   source truth、forecast causality、Day0 settlement authority、FDR family denominator、
   typed ExecutionPrice / Kelly、RiskGuard、final intent、executor / venue side-effect boundary。

3. Runtime liveness:
   scheduler jobs、daemon reboot、market channel / user channel separation、
   heartbeat、existing maintenance jobs、CI semantic classifier。
```

所以我没有按“新增文件能 import 就行”审。我的检查顺序是：

```text
A. PR metadata / diff scale / full changed-file list
B. CI status and failure logs
C. schema_version / DB table ownership / new table registration
D. daemon boot and scheduler wiring
E. reactor source truth / executable snapshot / FDR / Kelly / RiskGuard / submit path
F. forecast trigger causality and completeness
G. Day0 trigger source/station/local-date/rounding authority
H. market-channel vs user-channel and quote/fill semantics
I. executable cost / TradeScore / ExecutionPrice
J. no-trade regret / existing no_trade_events compatibility
K. tests: whether they prove the actual semantic contract or only string/synthetic behavior
```

我读取了完整 PR changed-file list 和 patch，并单独打开了所有 load-bearing files：`src/main.py`、`src/events/**`、`src/events/triggers/**`、`src/engine/event_reactor_adapter.py`、`src/strategy/live_inference/**`、`src/state/db.py`、新增 schema modules、`architecture/db_table_ownership.yaml`、`architecture/money_path_*`、`config/settings.json`、核心 tests 和 CI logs。

---

# 2. High-level verdict

PR328 **不等于 EDLI v1 implementation**。它目前更像：

```text
EDLI event tables
+ event scanner / market-channel online data service
+ a scheduler job
+ a reactor shell
+ synthetic tests
+ config enabled=true
```

但它没有实现真正的：

```text
event -> hydrate event-specific causal state
      -> LiveBinInferenceState / p_live
      -> Day0 K_t / forecast LLR
      -> native quote / executable cost / TradeScore
      -> full-family FDR
      -> typed Kelly
      -> RiskGuard
      -> final execution intent
      -> executor
```

最严重的是，PR328 的 reactor 把 `fdr_gate` 和 `kelly_gate` 接成了永远返回 `True` 的 placeholder，然后用 event 去触发一次旧的 `run_cycle(mode)`。这不是 event-sourced opportunity reactor；这是 **event-triggered cron-cycle rerun**。`submit_existing_cycle_for_event()` 只检查旧 cycle summary 里有没有 `final_intents_built` / `submit_attempts` / `entry_orders_submitted`，并没有证明旧 cycle 产生的 intent 与当前 event 的 city/date/metric/family/bin/causal_snapshot_id 有任何关系。

---

# 3. CI 状态已经是硬阻断

PR head 的 GitHub Actions 不是绿的：

```text
money-path-required: failed
money-path-release-gate: failed
replay-correctness: success
full sweep: skipped
```

两个失败不是小问题：

## 3.1 `money-path-required` failure

CI semantic classifier 把 PR328 标成 **P0 risk**，并发现 unregistered states：

```text
PARTIAL_ALLOWED
PARTIAL_BLOCKED
UNKNOWN_REVIEW_REQUIRED
FDR_REJECTED
EXISTING_CYCLE_NO_SUBMIT
```

这说明 PR 引入了新的 money-path / state-machine / rejection semantics，但没有完整注册。这个和 repo 的 money-path governance 直接冲突。

## 3.2 `money-path-release-gate` failure

Release gate 暴露了 schema_version 破坏：

```text
NoTradeEventsSchemaCompatibilityError:
  live no_trade_events schema is not current: schema_version_check_39

sqlite3.IntegrityError:
  CHECK constraint failed:
  schema_version IN (12, ..., 35)
```

PR 把 `SCHEMA_VERSION` bump 到 39。 但是 `no_trade_events_schema.py` 的 `schema_version CHECK` 仍只接受到 38。 CI 还显示 `decision_events` 也没有接受 39。这会导致 live 写入/测试直接失败，不是 cosmetic。

**结论：CI red alone is merge-blocking.** 即使没有下面的语义问题，也不能 merge。

---

# 4. P0 blockers

## P0-1 — Reactor 没有真正执行 EDLI money path；FDR/Kelly 是 no-op

`src/events/reactor.py` 看似有完整 gates，但它接受外部注入 gate。`src/main.py` 实际传入的是：

```python
fdr_gate=existing_cycle_downstream_gate
kelly_gate=existing_cycle_downstream_gate
```

而 `existing_cycle_downstream_gate(_event) -> True`。

随后 `final_intent_submit` 不是对当前 event/candidate 生成 final intent，而是：

```python
return submit_existing_cycle_for_event(event, run_cycle=run_cycle)
```

`submit_existing_cycle_for_event()` 只是把 event type 映射到 `DiscoveryMode.DAY0_CAPTURE` 或 `DiscoveryMode.UPDATE_REACTION`，跑一次旧 cycle，并根据 summary 里是否有 `final_intents_built` / `submit_attempts` / `entry_orders_submitted` 返回 True。

这造成严重语义破坏：

```text
event A: Chicago high Day0 hard fact
run_cycle(DAY0_CAPTURE) 可能生成 unrelated market/order
reactor 仍认为 event A submitted
live cap ledger reserve event A
no guarantee order matched event A's family/bin/snapshot
```

这违反 EDLI acceptance contract：

```text
event-specific causal_snapshot_id
event-specific family FDR
event-specific executable snapshot
event-specific native cost
event-specific typed Kelly
```

**Fix required:**

Reactor 不能用 `run_cycle(mode)` summary 当 event submit truth。必须新增或抽取一个 real adapter：

```text
hydrate_event(event)
  -> exact city / target_date / metric / family_id / bins / causal_snapshot
  -> full-family candidate set
  -> LiveBinInferenceState
  -> RobustExecutableTradeScore per candidate
  -> existing full-family FDR
  -> typed ExecutionPrice Kelly
  -> RiskGuard
  -> final intent
  -> executor
```

如果现有 cycle runner 不能单-event hydrate，就不要标记 `reactor_mode=live`。

---

## P0-2 — Executable snapshot gate 只检查“任意 fresh weather snapshot”，不绑定 event

`executable_snapshot_gate_from_trade_conn()` 只检查 `executable_market_snapshots` 表里有没有一条 fresh active weather/temperature snapshot。它没有检查：

```text
event.city
event.target_date
event.metric
event.condition_id
event.token_id
event.family_id
event.causal_snapshot_id
YES/NO token map
native quote side
freshness relative to decision_time
```

代码只是查：

```text
freshness_deadline >= now
yes_token_id/no_token_id present
active=1 if column exists
closed=0 if column exists
event_slug LIKE weather/temperature if column exists
```

这意味着某个 fresh New York weather market snapshot 可以让 Chicago Day0 event 通过 executable gate。

**Fix required:**

Executable snapshot gate must be event-specific:

```text
event.entity_key / payload -> condition_id/token_id/family_id/city/date/metric
snapshot_repo.get_snapshot(executable_snapshot_id)
assert token map matches event candidate
assert snapshot condition_id matches event market family
assert fresh at decision_time
assert fee/tick/min-order/negRisk match
assert native quote available for direction
```

---

## P0-3 — PR 没有把 LiveBinInference / TradeScore 接入 reactor

`src/strategy/live_inference/*` 新增了纯函数和 helpers，但 reactor 没有调用：

```text
state.py LiveBinState
absorbing_boundary.py evaluate_day0_absorbing_boundary
markov_smoothing.py
bayesian_factors.py
executable_cost.py executable_cost / quote_book_from_executable_snapshot
trade_score.py robust_trade_score
```

`trade_score.py` 只定义了 formula helpers 和 assertions。 `executable_cost.py` 实现了 native quote book-walk 和 `ExecutionPrice` construction。 但是 main/reactor path 根本没有把这些用于 event decisions。

这导致：

```text
EDLI q_live not computed
Day0 K_t not applied to family distribution
Forecast LLR not applied
native quote cost not computed
TradeScore not computed
ExecutionPrice not passed to Kelly
```

Tests 也没有证明这些被接线。`tests/events/test_reactor.py` 只是注入 lambda gates True/False；`tests/engine/test_event_reactor_no_bypass.py` 甚至明确断言 adapter 里没有 `execute_final_intent` 字符串，实际没有证明 final-intent contract。

**Fix required:**

Add integration test that fails today:

```text
test_reactor_calls_live_inference_trade_score_and_typed_kelly_for_event_candidate
test_reactor_rejects_if_trade_score_nonpositive
test_reactor_rejects_if_execution_price_not_kelly_safe
test_reactor_candidate_condition_id_must_match_event_condition_id
test_run_cycle_unrelated_submit_does_not_count_as_event_submit
```

---

## P0-4 — Day0 trigger uses an observability-only table as live trigger substrate

PR’s `_edli_emit_day0_extreme_events()` opens `trade_conn` and scans `settlement_day_observation_authority`. But the table ownership registry says that table is:

```text
trade DB
written from cycle_runtime.execute_discovery_phase
observability only — no trade-behavior dependency
```

This is a deep semantic inversion. A Day0 event trigger should be emitted from the observation source/update path or a durable observation authority source. This PR scans a table that existing cycle runtime writes **during decision evaluation**. That means Day0 “event-driven” behavior is downstream of the old cycle, not upstream.

Even worse, `authority_row_to_observation()` defaults several source-truth fields toward pass-like values:

```text
source_match_status = MATCH if row.source exists else UNKNOWN
station_match_status = MATCH if row.station_id exists else UNKNOWN
metric_match_status = MATCH if metric in {high, low}
source_authorized_status = AUTHORIZED only if source_authorized_for_settlement == 1
```

This is not enough to prove:

```text
station maps to settlement source
local date / DST exact match
rounding/unit identity
observation_source authorized for settlement_source_type
available_at source reality
```

**Fix required:**

Either:

```text
A. Emit Day0 events from actual observation ingest / Day0ObservationContext update path
```

or, if using `settlement_day_observation_authority`:

```text
B. Change registry/docs/tests to make it a live-decision dependency,
   prove it is source-authoritative,
   prove it is written before EDLI decision,
   prove no old-cycle causality loop,
   and fail closed on missing source/station/DST/rounding fields.
```

Current PR does neither.

---

## P0-5 — MarketChannel ingestor mislabels token outcome and omits venue facts

Polymarket market channel messages are token-level public market data. Official docs say market channel subscribes by asset IDs and emits `book`, `price_change`, `tick_size_change`, `last_trade_price`, `best_bid_ask`, `new_market`, and `market_resolved`.([Polymarket Documentation][1]) Orderbook docs say BUY uses best ask, SELL uses best bid, and midpoint/last trade are display semantics, not executable cost.([Polymarket Documentation][2])

PR’s `market_channel_ingestor.py` has several P0/P1 issues:

### Outcome mislabelling

For `book` messages:

```python
outcome_label=str(message.get("outcome_label") or "YES")
```

For BBA/price change it also defaults to `"YES"`。

Polymarket market channel payloads are by `asset_id`; they do not guarantee `outcome_label`. Outcome label must come from Zeus token map / `ExecutableMarketSnapshotV2` / scanner topology. If a NO token event lacks outcome_label, this code labels it YES and writes `buy_yes/sell_yes` evidence for a NO token.

That breaks native YES/NO cost semantics.

### Missing tick/min-order/negRisk in payload

`MarketBookEventPayload` lacks:

```text
tick_size
min_order_size
neg_risk
depth_at_best_bid
depth_at_best_ask
```

Those were explicit EDLI requirements and venue facts. Polymarket orderbook includes `min_order_size`, `tick_size`, `neg_risk`, and hash.([Polymarket Documentation][2])

### tick_size_change action is ignored

`handle_message()` returns `MarketChannelAction(refresh_snapshot=True, reason="tick_size_change")`, but `MarketChannelOnlineService.run_websocket_forever()` just calls `handle_message()` and ignores the returned action. That means tick-size changes do not actually force snapshot refresh.

### No coalescer in online path

The code writes every handled book/BBA event synchronously through `EventWriter`. `EventWriter` itself is not a queue/single-writer thread; it wraps `EventStore` and calls insert immediately. This violates the event spam / DB lock prevention contract.

**Fix required:**

```text
- Build token_id -> outcome_label / condition_id / negRisk / min_order / tick map from current ExecutableMarketSnapshotV2.
- Reject or quarantine market messages whose token_id is not in active Zeus map.
- Include tick_size/min_order_size/negRisk in payload.
- Route tick_size_change to snapshot refresh/invalidation.
- Use event_coalescer before EventWriter.
- EventWriter must be actual single-writer queue or documented as same-thread store; current name is misleading.
```

---

## P0-6 — Schema version bump is incomplete and breaks release gate

`SCHEMA_VERSION=39` is set in `src/state/db.py`. But `no_trade_events_schema.py` still has `schema_version CHECK` list ending at 38 in both CREATE and rebuild DDL. CI confirms this fails.

The same release gate failure shows `decision_events` only accepts through 35. This is not theoretical: with schema_version 39, write paths can hit `CHECK constraint failed`.

**Fix required:**

```text
- Update all schema_version CHECK constraints impacted by SCHEMA_VERSION=39:
  no_trade_events
  decision_events
  any other natural-key decision/evidence tables that write current SCHEMA_VERSION.
- Add rebuild migrations for existing live DBs.
- Update pinned schema hash.
- Re-run money-path-release-gate.
```

Until fixed, daemon online config is unsafe.

---

## P0-7 — New config enables live EDLI before acceptance gates are real

PR sets:

```json
"edli_v1": {
  "enabled": true,
  "reactor_mode": "live",
  "event_writer_enabled": true,
  "forecast_snapshot_trigger_enabled": true,
  "forecast_complete_live_enabled": true,
  "day0_extreme_trigger_enabled": true,
  "day0_hard_fact_live_enabled": true,
  "market_channel_ingestor_enabled": true
}
```

This is exactly the intended final state **only after** semantic gates are real. In this PR, they are not. The reactor’s FDR/Kelly gates are no-op placeholders; executable snapshot gate is not event-bound; Day0 source is an observability table; TradeScore is not called.

Therefore `enabled=true` and `reactor_mode=live` are premature.

**Fix required:**

Either complete the semantic chain, or keep config off until actual EDLI integration is implemented. Since the user goal is “online after full implementation,” the correct path is **complete the integration**, not ship this PR as-is.

---

# 5. P1 blockers / high-risk semantic issues

## P1-1 — Forecast completeness can pass when expected steps are empty

`classify_forecast_snapshot()` computes:

```python
expected_steps = _json_list(coverage.expected_steps_json or source_run.expected_steps_json)
observed_steps = _json_list(...)
required_steps_present = set(expected_steps).issubset(set(observed_steps))
```

If expected steps are absent or empty, `set().issubset(...)` is `True`.

The file defines `ecmwf_open_data_expected_steps(cycle_hour)` for 00/12 vs 06/18, but it is not used in classification.

Given ECMWF current docs differentiate 00/12 and 06/18 step ranges, missing expected steps should be fail-closed, not “all required steps present.”([Polymarket Documentation][2])

**Fix:**

```text
if expected_steps missing:
  derive cycle-specific required steps from official/ingest policy
  or PARTIAL_BLOCKED with reason EXPECTED_STEPS_UNKNOWN
```

---

## P1-2 — Forecast trigger is a scanner, not post-commit event hook

PR did not change `src/data/ecmwf_open_data.py`; ForecastSnapshotReady is emitted by `_edli_emit_forecast_snapshot_events()` in `src/main.py`, every minute, scanning committed rows.

This is acceptable as catch-up, but it is **not** the low-latency post-commit trigger required by EDLI. It reintroduces scheduler cadence into forecast opportunity discovery.

**Fix:**

Keep scanner as catch-up, but also add post-commit event sink/callback at Open Data commit point, or document that PR328 only implements catch-up mode and not full EDLI forecast trigger.

---

## P1-3 — NoTradeRegretLedger is too thin for EDLI learning/reporting

`NoTradeRegretEvent` only records:

```text
event_id
rejection_stage/reason/bucket
market_slug / condition_id / token_id / outcome_label
later_outcome / would_have_won / would_have_filled
```

Schema likewise lacks:

```text
decision_time
city / target_date / metric / family_id / bin_label / direction
q_live
q_lcb_5pct
c_fee_adjusted
c_cost_95pct
p_fill_lcb
trade_score
native_quote_available
source_status
family_complete
hypothetical order type / fill status / fill price
causal_snapshot_id
executable_snapshot_id
```

This cannot produce the EDLI reports promised by the spec.

The compatibility writer is worse: it hardcodes `temperature_metric='high'`, `target_date='unknown'`, `observation_time='unknown'`, `decision_seq=0`, `reason='uncategorized'`, and schema_version=38. That can collide via primary key and erase evidence with `INSERT OR IGNORE`.

**Fix:**

Implement the full regret schema. Compatibility write must either use a real `DecisionNaturalKey` or not write existing `no_trade_events` for event-only cases.

---

## P1-4 — FDR family accounting is not real

`OpportunityEventReactor._log_family_once()` only does:

```python
family_key = event.entity_key.rsplit("|", 1)[0]
self._family_logged.add(family_key)
```

This is not Zeus’s full-family FDR logging. It does not call canonical `selection_family` / `selection_hypothesis` logic and does not log sibling hypotheses into the durable FDR facts.

Tests only assert `family_log_count() == 1` for two synthetic event keys.

**Fix:**

Use existing `selection_family` canonical family id and durable family/hypothesis fact logging. Duplicate event idempotency must not reduce or inflate the BH denominator.

---

## P1-5 — Live cap ledger has a notional accounting bug

`EdliLiveCapLedger.check_day0()` checks:

```python
if existing_notional + max_notional_usd > max_notional_usd:
    block
```

This uses the cap value as both proposed order notional and max cap. With default max_orders=1 it works accidentally, but if `tiny_live_max_orders_per_day > 1`, any second order blocks regardless of actual intended notional. It also does check/reserve in separate operations, not atomic.

**Fix:**

Pass `candidate_notional_usd` separately and reserve under a transaction / unique constraint.

---

## P1-6 — Table registry marks PK columns nullable

EDLI registry entries mark primary-key columns as nullable, e.g.:

```text
opportunity_events.event_id nullable: true
event_dead_letters.dead_letter_id nullable: true
execution_feasibility_evidence.evidence_id nullable: true
no_trade_regret_events.regret_event_id nullable: true
edli_live_cap_usage.usage_id nullable: true
```

`opportunity_events_schema.py` also uses `event_id TEXT PRIMARY KEY` without explicit `NOT NULL`. SQLite semantics around non-integer primary key nullability are tricky; in a money-path table, the invariant should be explicit.

**Fix:**

Make all PKs:

```sql
event_id TEXT NOT NULL PRIMARY KEY
```

or use `WITHOUT ROWID` where appropriate, and update registry required_columns to `nullable: false`.

---

# 6. P2 / operational issues

## P2-1 — EventWriter is not a single-writer queue

The implementation says “Single-writer facade,” but it only wraps `EventStore(conn)` and writes synchronously.

Market channel thread opens one world connection and writes/commits from that thread. Reactor scheduler also opens world connections. This may not violate SQLite by itself, but it does not implement the intended queue/coalescer/backpressure architecture.

**Fix:**

Either rename it honestly as `EventStoreWriter` or implement actual queue/single writer. Market channel should not write every raw packet.

---

## P2-2 — Tests are not strong enough for a money-path PR

`tests/money_path/test_edli_online_invariants.py` mainly asserts strings and settings:

```text
settings.edli_v1.enabled is True
"edli_event_reactor" in src/main.py
"wss://..." in ingestor source
no filename contains shadow_
```

That does not prove:

```text
daemon boots with live DBs
market channel token map is correct
event-specific candidate selected
TradeScore called
ExecutionPrice reaches Kelly
FDR full-family logged
RiskGuard blocks
executor final-intent contract used
```

**Fix:**

Add integration tests that execute actual adapter paths with fixture DBs shaped like world/forecasts/trade DBs, not only in-memory tables.

---

# 7. Positive findings

This PR does include useful scaffolding:

1. **Event rows and processing state are separated.** `opportunity_events` is immutable with append-only triggers, while `opportunity_event_processing` is mutable.

2. **`assert_available_for_decision` checks both available_at and received_at.** That is stricter than the minimal requirement and helps prevent queue-time leakage.

3. **Market channel is not allowed to write fill truth directly.** The ingestor includes `assert_market_channel_not_fill_authority` and separates evidence from fill truth, although the evidence pathway still needs correction.

4. **Taker FOK/FAK live remains disabled in config.** That respects current execution law. The official order docs define FOK/FAK as immediate market-order semantics, while current Zeus execution law requires limit-order/maker-entry behavior.([Polymarket Documentation][3])

5. **Day0 boundary helper uses `SettlementSemantics.round_single()`.** This is correct in isolation.

But these positives do not offset the P0 wiring gaps.

---

# 8. Impact surface assessment by file group

| File group | Assessment |
| --- | --- |
| `architecture/*` | Registry additions are present, but CI still flags unregistered states. PK nullability is wrong. Money-path CI is not green. |
| `config/settings.json` | Final-state config is enabled too early. Live mode should not be enabled while real EDLI chain is missing. |
| `docs/operations/edli_v1/*` | Useful context docs, but implementation does not meet the documented contract. |
| `src/events/opportunity_event.py` | Good timestamp triad and payload hash; market payload under-specified for tick/min/negRisk; string-only payload_json makes type guarantees weaker. |
| `src/events/event_store.py` | Append-only / processing split good. Single-writer/backpressure is not implemented. |
| `src/events/reactor.py` | Main P0: gate shell only; does not call inference/cost/TradeScore; family logging is in-memory placeholder. |
| `src/engine/event_reactor_adapter.py` | Main P0: FDR/Kelly no-op; executable snapshot gate is any-fresh-weather-market; submit is unrelated old cycle summary. |
| `src/events/triggers/forecast_snapshot_ready.py` | Partial implementation; no post-commit hook; empty expected steps can pass; cycle-specific helper not used. |
| `src/events/triggers/day0_extreme_updated.py` | Uses observation_available_at and SettlementSemantics, but scans an observability-only trade table written by old cycle. |
| `src/events/triggers/market_channel_ingestor.py` | Online service exists, but outcome_label defaults YES, no coalescer, no tick/min/negRisk payload, tick-size change ignored. |
| `src/strategy/live_inference/*` | Useful pure helpers, but not integrated into reactor/money path. |
| `src/state/schema/*` | EDLI tables exist, but `SCHEMA_VERSION=39` breaks existing `no_trade_events` / `decision_events` checks. |
| `tests/*` | Many tests, but mostly synthetic or string-based; they prove scaffolding, not semantic money-path integration. |

---

# 9. Required repair order

Do not try to fix this by adding one more test. The correct repair sequence is:

## Step 1 — Fix CI hard failures

```text
- Register the new states that classifier found, or remove/rename them from money-path state space.
- Extend no_trade_events schema_version CHECK to 39.
- Extend decision_events schema_version CHECK to 39.
- Add table rebuild migration and schema-pinned hash update.
- Re-run money-path-required and money-path-release-gate.
```

## Step 2 — Disable live config until semantic chain is real

Temporarily set:

```json
"edli_v1": {
  "enabled": false,
  "reactor_mode": "off"
}
```

or keep enabled only in a non-submitting mode. Since your target is online after full implementation, this should be temporary while wiring is fixed.

## Step 3 — Replace reactor placeholder gates

Remove:

```python
existing_cycle_downstream_gate -> True
submit_existing_cycle_for_event(event, run_cycle=run_cycle)
```

Implement:

```text
hydrate_event_to_candidate_family(event)
evaluate_live_distribution(event, candidate_family)
compute_native_executable_cost(candidate)
compute_robust_trade_score(candidate)
apply_existing_full_family_fdr(candidate_family)
apply_kelly(typed ExecutionPrice)
apply_RiskGuard
build existing FinalExecutionIntent
submit via executor
```

The submitted intent must prove it corresponds to the same event:

```text
same city
same target_date
same metric
same family_id
same condition_id/token_id
same causal_snapshot_id
same executable_snapshot_id
```

## Step 4 — Fix Day0 trigger source

Either connect to real observation update path, or reclassify and validate `settlement_day_observation_authority` as live authority. If it remains observability-only, EDLI cannot depend on it for live event generation.

## Step 5 — Fix market-channel token semantics

```text
- Build active token metadata from ExecutableMarketSnapshotV2.
- Do not default outcome_label to YES.
- Include tick_size, min_order_size, neg_risk.
- Coalesce raw WS events.
- Actually act on tick_size_change by invalidating/refreshing snapshots.
```

## Step 6 — Expand NoTradeRegretLedger

Add required q/c/fill/score fields and remove fake `no_trade_events` natural key writes.

## Step 7 — Add semantic integration tests

Minimum tests needed:

```text
test_reactor_event_specific_candidate_binding
test_reactor_unrelated_run_cycle_submit_does_not_count
test_reactor_calls_live_inference_and_trade_score
test_reactor_kelly_requires_typed_execution_price
test_reactor_fdr_logs_full_family
test_day0_observation_authority_not_used_if_observability_only
test_market_channel_no_default_yes_for_no_token
test_tick_size_change_invalidates_snapshot
test_no_trade_regret_contains_q_cost_fill_score
```

---

# 10. Acceptance contract status

| Contract | Status | Reason |
| --- | ---: | --- |
| immutable event rows | PARTIAL PASS | append-only triggers exist. |
| processing separate | PASS | separate processing table. |
| idempotent events | PARTIAL PASS | event hash/idempotency exists; FDR duplicate accounting not real. |
| no event after available_at | PASS in store/model | fetch and assert block future. |
| forecast complete live eligible | PARTIAL / unsafe | completeness can pass with empty expected steps; live chain missing. |
| Day0 hard fact source-gated | PARTIAL / unsafe | statuses exist, but source table is observability-only and defaults are weak. |
| absorbing boundary uses SettlementSemantics | PASS in helper | but helper not integrated into live reactor. |
| orderbook event cannot change q | PASS | but q path absent. |
| market channel online | PARTIAL | service exists, but token/outcome/tick semantics wrong. |
| public market not fill truth | PARTIAL PASS | guard exists; evidence source still thin. |
| no midpoint/last trade cost | PASS in helper | not integrated. |
| buy NO native NO ask | PASS in helper | not integrated; market payload may mislabel NO as YES. |
| Kelly typed ExecutionPrice | FAIL | helper exists, reactor does not call it/Kelly. |
| FDR full family | FAIL | in-memory fake family log only. |
| RiskGuard mandatory | PARTIAL | only level GREEN check, not full exposure/cluster/cap. |
| rejection writes regret | PARTIAL | writes thin regret, missing q/c/fill/score. |
| scheduler maintenance intact | PASS | existing jobs preserved. |
| no direct venue adapter in reactor | PASS | but existing-cycle submit is not event-bound. |
| all new tables registered | PARTIAL | tables registered, but CI still flags states; PK nullability wrong. |
| wrong-DB regression | PARTIAL | tests exist, but Day0 depends on trade observability table. |
| final config online safe | FAIL | config live before semantics are safe. |

---

# 11. Final recommendation

**Do not merge PR328. Do not reboot daemon with PR328.**

The PR is valuable as a scaffold, but it is not a safe EDLI v1 implementation. The most dangerous failure mode is not that it “does nothing”; it may do something worse: a forecast/Day0 event can trigger an unrelated old cycle, and if that cycle builds/submits any final intent, the reactor can treat the event as submitted, reserve live cap, and log event success without proving event-specific source truth, p_live, TradeScore, FDR, Kelly, or executable snapshot binding.

The merge-ready version must convert this from:

```text
event -> run old cycle(mode) -> infer success from summary
```

to:

```text
event -> hydrate exact causal family
      -> compute p_live
      -> native executable cost
      -> robust TradeScore
      -> full-family FDR
      -> typed Kelly
      -> RiskGuard
      -> final intent for the same event
      -> executor
```

Until that is true and both `money-path-required` and `money-path-release-gate` are green, PR328 is a **NO-GO**.

[1]: https://docs.polymarket.com/developers/CLOB/websocket/market-channel "Market Channel - Polymarket Documentation"
[2]: https://docs.polymarket.com/trading/orderbook "Orderbook - Polymarket Documentation"
[3]: https://docs.polymarket.com/trading/orders/create "Create Order - Polymarket Documentation"
