# EDLI / PR328 Redemption Package

先明确一件事：你这次批评是成立的。**我之前给的 implementation prompt 没有把“语义顺序证明”和“side-effect authority 证明”放在代码生成之前作为硬门槛，导致 implementation agent 在没有先证明事件如何绑定候选、候选如何绑定 FDR/Kelly/RiskGuard/final-intent/executor 的情况下，开始铺 event store、scheduler、schema、reports、online config。** 这不是简单 code review 可以补的，是设计执行顺序错误。

PR328 最新状态也证明它仍然不是可上线实现：PR328 仍是 draft，最新 head 是 `3c421b8c9f91c4e5514bd9ca273dba32ae2f02cd`，PR 自称 “Enable EDLI v1 online event loop”，范围已经扩大到 **81 changed files / +13959 / -112**，且 body 仍列出 daemon restart、live websocket/user-channel smoke 未执行。 最新 CI 中 `money-path-release-gate` 已经转绿，但 `money-path-required` 仍失败，semantic classifier 仍把 change 标为 **P0**，并指出仍有未注册 state：`FINAL_INTENT_RECEIPT_MISSING`。

下面不是继续 patch PR328 的 “code review”。这是一个 **redemption package**：从设计合理性重判、语义接线重建、side-effect authority 重排、PR328 最新状态复核，到下一步 implementation/review 指令。

---

## 0. Redemption verdict

**最终裁决：EDLI 方向仍然合理，但 PR328 必须降级为 failed spike / scaffold，不应继续以当前 PR 形态修到上线。**

正确路线不是“继续在 PR328 上修 20 个 P0”。正确路线是：

```text
1. 冻结 PR328 live side effects。
2. 把 PR328 中可复用的 pure/schema/reports pieces 退回 scaffold。
3. 新建 Redemption PR sequence。
4. 第一批 PR 只证明 semantic binding，不写 live submit。
5. 第二批 PR 才接入 existing money path。
6. 最后一批 PR 才允许 daemon online + tiny live pilot。
```

**核心原则：event is not alpha, event is not order authority, event is not executable cost, event is not fill truth.**
Event 只能触发一个 **causally bound decision reconstruction**；只有当这个 decision reconstruction 产生可机器验证的 receipt，才允许进入 side-effect path。

---

# 1. Root-cause admission

这次 failure 的根本原因不是某个函数没写好，而是 prompt/spec 的 sequencing 错了。

我之前的 prompt 让 agent 同时做：

```text
event store
forecast trigger
Day0 trigger
market channel
live inference
TradeScore
regret ledger
reports
daemon online config
```

但没有先强制完成以下证明：

```text
P0 proof 1: An event can be bound to exactly one market family and candidate set.
P0 proof 2: That candidate set can be hydrated without using future data.
P0 proof 3: That candidate set can pass through existing FDR without shrinking/duplicating family denominator.
P0 proof 4: Kelly receives typed ExecutionPrice produced from native executable quote.
P0 proof 5: FinalExecutionIntent belongs to the same event/candidate/snapshot.
P0 proof 6: Executor side effect authority stays in existing executor.
P0 proof 7: A public market-channel event can never become fill truth.
P0 proof 8: Scheduler can catch up but cannot become the alpha authority again.
```

因为这些 proofs 没有被要求在 **Phase 0** 先做，implementation agent 自然走向“先铺外壳，再把旧 cycle 包一下”的错误路径。最新 PR328 已经尝试修补 receipt、event context、FDR proof、TradeScore proof，但它仍然是在旧架构上缝补，不是重新建立确定的 semantic order。

---

# 2. Latest PR328 reality check

## 2.1 What changed since the prior review

PR328 latest head 已经做了若干修补：

1. Reactor 不再直接接受 `fdr_gate=True` / `kelly_gate=True`，而是增加了 `EventSubmissionReceipt`，要求 receipt 包含 event id、snapshot、TradeScore、FDR、Kelly、final intent proof。

2. `submit_existing_cycle_for_event()` 不再只看旧 summary 中 submit counters，而是要求 `run_cycle(mode, edli_event_context=context)` 返回 event-bound fields。

3. `cycle_runner.run_cycle()` 已接受 `edli_event_context`，并传给 `cycle_runtime.execute_discovery_phase()`。

4. Market channel ingestor 已补 token metadata、outcome label、不再默认 YES，并加入 coalescer、tick-size action、snapshot invalidation。

5. Day0 trigger 增加 `live_authority_status`，并新增 `observation_context_to_live_observation()`，从 live observation object 生成事件，不再只靠 observability table；但 main 里 catch-up 仍扫描 authority rows。 

这些是朝正确方向走，但仍不够。

## 2.2 Current remaining hard blockers

最新 PR328 仍然不能被视为 mergeable，因为：

```text
- money-path-required 仍失败。
- PR 仍是 draft。
- daemon restart / live WS / user-channel smoke 仍未执行。
- EDLI semantic chain 仍穿插在 cycle_runtime 巨型函数内，证明不透明。
- event context / receipt 是后验摘要，不是主导 decision construction 的 first-class object。
- q_live / FDR / Kelly / final intent proof 仍可能是由旧 cycle 副作用间接生成，不是 EDLI kernel 的 primary output。
```

PR body 仍明确 “known not-reviewed surfaces” 包括 launchd daemon restart、live Polymarket websocket connection、authenticated user-channel / explicit reconciliation smoke。 这本身就不能满足 “daemon reboot 后 online” 的验收。

---

# 3. Re-reasoning the design from first principles

## 3.1 EDLI 是否仍然合理？

合理，但它必须被重定义为 **semantic trigger layer**，不是 “event loop + old cycle rerun”。

Zeus 当前 money path 已经有大量既有权威：

```text
source truth
forecast reader
calibration / Platt
market fusion
selection family / FDR
ExecutionPrice / Kelly
RiskGuard
FinalExecutionIntent
executor / venue adapter
lifecycle / reconciliation / settlement
```

EDLI 的正确角色是：

```text
event tells Zeus WHEN to re-evaluate a specific causal family;
EDLI must not decide HOW to bypass existing money path.
```

也就是说：

```text
event-driven = trigger timing / causal timestamp / replay identity
not event-driven = bypass FDR/Kelly/RiskGuard/executor
```

## 3.2 Correct semantic order

所有 live-money decision 必须按这个顺序：

```text
Event Fact
  ↓
Causal Eligibility
  ↓
Market Family Binding
  ↓
Source Truth Binding
  ↓
Forecast / Observation / Market Data Hydration
  ↓
Candidate Family Generation
  ↓
Live Inference
  ↓
Executable Snapshot Binding
  ↓
Native Executable Cost
  ↓
Robust TradeScore
  ↓
Full-Family FDR
  ↓
Typed Kelly
  ↓
RiskGuard / Portfolio / Cluster / Live Cap
  ↓
FinalExecutionIntent
  ↓
Executor Side Effect
  ↓
User Channel / Reconcile Fill Truth
  ↓
Lifecycle / Settlement / Learning
```

其中任何一步失败，必须 no-trade / dead-letter / report，不能向后推。

## 3.3 Side-effect authority rule

PR328 最大危险是把 old cycle summary 包装成 event receipt。正确规则应该更硬：

```text
No function may submit or mark event-submitted unless it owns a typed object:
  EventBoundFinalIntentReceipt
```

这个 receipt 必须由 final-intent builder 生成，而不是由 cycle summary 拼出来。

```python
@dataclass(frozen=True)
class EventBoundFinalIntentReceipt:
    event_id: str
    causal_snapshot_id: str
    inference_id: str
    family_id: str
    candidate_id: str
    condition_id: str
    token_id: str
    direction: str
    executable_snapshot_id: str
    execution_price: ExecutionPrice
    trade_score_id: str
    fdr_family_id: str
    kelly_decision_id: str
    risk_decision_id: str
    final_intent_id: str
    command_id: str | None
    side_effect_status: Literal[
        "NO_SUBMIT",
        "INTENT_BUILT",
        "COMMAND_CREATED",
        "SUBMITTED",
        "ACCEPTED",
        "REJECTED",
        "TIMEOUT_UNKNOWN",
    ]
```

`run_cycle()` summary is observability; it must never be the proof object.

---

# 4. External reality that must constrain the redemption

## 4.1 Polymarket venue reality

Polymarket market channel is public level-2 market data. It subscribes by asset IDs and emits `book`, `price_change`, `tick_size_change`, `last_trade_price`, `best_bid_ask`, `new_market`, and `market_resolved`; this supports online quote/book evidence but not fill truth. ([Polymarket Documentation][1])

Polymarket orderbook response includes token-level `bids`, `asks`, `tick_size`, `min_order_size`, `neg_risk`, and `hash`; buy price is best ask and sell price is best bid. ([Polymarket Documentation][2]) The midpoint is explicitly a displayed implied probability, and if spread is wider than $0.10 Polymarket displays last traded price instead of midpoint; neither is executable cost. ([Polymarket Documentation][2])

FOK/FAK are market order types that execute against resting liquidity immediately; FOK fills all or cancels, FAK fills what is available and cancels the remainder, and post-only only works with GTC/GTD and rejects FOK/FAK. ([Polymarket Documentation][3])

**Redemption implication:** Market channel may run online, but live stale-book directional trading is still out of scope. Public WS cannot be fill authority. All cost must be native ask/bid/depth/tick/min-order/negRisk and all fill state must come from user channel or reconciliation.

## 4.2 ECMWF Open Data reality

ECMWF says IFS data are released at the end of the real-time dissemination schedule; rolling archive availability is the most recent 12 forecast runs, approximately 2–3 days, based on 00/06/12/18 UTC cycles. The same official page gives step ranges: 00/12 cycles 0–144 by 3 and 150–360 by 6, while 06/18 cycles are 0–144 by 3. ([ECMWF][4])

**Redemption implication:** `available_at` must be committed usable availability, not nominal issue time. Forecast completeness cannot use an empty `expected_steps` list and call it complete. Required steps must be derived from cycle/track/target-date semantics or fail closed.

---

# 5. Why PR328 still “falls off” even after fixes

The current fix direction adds “receipt proof” after the old cycle, but the architecture still has a fatal inversion:

```text
Wrong:
  event -> run_cycle(mode, context) -> mutate existing discovery pipeline -> summary receipt

Right:
  event -> event-bound candidate family -> explicit decision object -> final intent receipt
```

The old cycle is a broad scanner. Even with filters, its semantics are still:

```text
mode-wide discovery
many candidates
many strategies
legacy cycle-level gates
summary aggregation
```

EDLI needs:

```text
one event
one causal family
full sibling hypotheses
one or zero selected event-bound final intents
```

A summary object cannot prove this unless it is built from typed lower-level receipts, and if those lower-level receipts exist, the summary should not be the authority anyway.

---

# 6. Current PR328 latest state: what is improved but still unsafe

| Area           | Latest PR328 improvement                                                      | Still unsafe because                                                                                                                     |
| -------------- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Reactor        | Adds `EventSubmissionReceipt` and checks receipt fields.                      | Receipt is produced by old cycle summary, not by first-class event-bound final-intent builder.                                           |
| Cycle runner   | Accepts `edli_event_context` and forwards into runtime.                       | Context is injected into a huge discovery phase; no isolated EDLI kernel.                                                                |
| Cycle runtime  | Adds filters, EDLI inference stamp, FDR proof, Kelly proof, TradeScore proof. | These are patch hooks inside legacy flow, not a deterministic event-bound pipeline.                                                      |
| Market channel | Adds token metadata, coalescer, tick-size invalidation.                       | Still writes through same world conn thread; no demonstrated production WS smoke; coalescer semantics still not full backpressure proof. |
| Day0           | Adds live observation conversion and `live_authority_status`.                 | Main still has catch-up scanner over observability authority table; live hook is buried inside cycle runtime.                            |
| CI             | release gate now green.                                                       | money-path-required still fails; semantic classifier marks P0 and unregistered state remains.                                            |
| Config         | EDLI remains enabled/live.                                                    | Live config is still ahead of proven semantic readiness.                                                                                 |

---

# 7. Redemption architecture: EDLI must become a small proof kernel

## 7.1 Replace “event-triggered old cycle” with “EventBoundDecisionEngine”

Add a new object that is explicitly **not** a scheduler wrapper:

```text
src/events/decision_engine.py
```

Authority:

```text
owns event-bound causal decision construction
does not own venue side effects
does not own final intent execution
```

Core API:

```python
@dataclass(frozen=True)
class EventBoundDecisionRequest:
    event: OpportunityEvent
    decision_time: datetime
    mode: Literal["shadow", "live"]
    operator_live_cap: LiveCapConfig

@dataclass(frozen=True)
class EventBoundDecisionResult:
    event_id: str
    status: Literal[
        "NO_TRADE",
        "DEAD_LETTER",
        "FINAL_INTENT_READY",
        "SUBMITTED",
    ]
    candidate_family: EventBoundCandidateFamily | None
    inference_state: LiveBinInferenceState | None
    trade_scores: tuple[RobustExecutableTradeScore, ...]
    fdr_result: FdrResult | None
    kelly_result: KellyResult | None
    risk_result: RiskDecision | None
    final_intent: FinalExecutionIntent | None
    rejection_stage: str | None
    rejection_reason: str | None
```

Rule:

```text
OpportunityEventReactor may call EventBoundDecisionEngine.
OpportunityEventReactor may not call run_cycle().
run_cycle may remain scheduler maintenance/discovery path.
```

## 7.2 Event-bound candidate family

Add:

```text
src/events/candidate_binding.py
```

Required object:

```python
@dataclass(frozen=True)
class EventBoundCandidateFamily:
    family_id: str
    event_id: str
    event_type: str
    city: str
    target_date: str
    metric: Literal["high", "low"]
    condition_ids: tuple[str, ...]
    yes_token_ids: tuple[str, ...]
    no_token_ids: tuple[str, ...]
    bins: tuple[Bin, ...]
    causal_snapshot_id: str
    market_topology_source: Literal[
        "forecasts.market_events_v2",
        "trade.executable_market_snapshots",
        "scanner_verified_topology",
    ]
    binding_hash: str
```

No candidate can exist unless:

```text
city/date/metric exact match
family complete
condition/token map complete
YES/NO native token map complete
causal_snapshot_id present
available_at <= decision_time
```

## 7.3 Event-bound final intent receipt

Add:

```text
src/engine/event_bound_final_intent.py
```

This is the only acceptable receipt producer. It must own:

```text
EventBoundDecisionResult -> FinalExecutionIntent -> EventBoundFinalIntentReceipt
```

`cycle_runtime` can expose helper functions, but **receipt cannot be summary-derived**.

---

# 8. Redemption PR sequence

This must be split. One mega-PR is the exact failure mode.

## R0 — Freeze and quarantine PR328

**Goal:** prevent further live confusion.

Actions:

```text
- Keep PR328 open as failed spike or close it.
- Do not merge.
- Do not reboot daemon with PR328.
- Add docs/operations/edli_v1/REDEMPTION_ROOT_CAUSE.md.
- Record that PR328 latest head still fails money-path-required.
- Set EDLI config off in PR328 branch or in follow-up safety PR.
```

Acceptance:

```text
- config/settings.json has edli_v1.enabled=false or reactor_mode=off.
- money-path-required failure acknowledged, not waived.
```

## R1 — Semantic proof harness only, no runtime

Files:

```text
src/events/candidate_binding.py
src/events/decision_engine.py
tests/events/test_event_candidate_binding.py
tests/events/test_event_bound_decision_engine_no_runtime.py
```

Implement only pure event → candidate-family binding.

Tests:

```text
test_forecast_event_requires_causal_snapshot_id
test_day0_event_requires_live_authority_status
test_market_event_never_creates_live_trade_candidate
test_candidate_family_requires_complete_yes_no_token_map
test_wrong_city_market_rejected
test_wrong_date_market_rejected
test_wrong_metric_market_rejected
test_family_binding_hash_deterministic
```

No scheduler, no executor, no `run_cycle`.

## R2 — Forecast completeness and temporal authority

Files:

```text
src/events/triggers/forecast_snapshot_ready.py
src/events/forecast_completeness.py
tests/events/test_forecast_snapshot_ready_temporal_authority.py
```

Hard rules:

```text
expected_steps missing => PARTIAL_BLOCKED / EXPECTED_STEPS_UNKNOWN
available_at must be source usable time
nominal issue_time cannot authorize live
cycle-specific required steps for 00/12 vs 06/18
COMPLETE requires executable_forecast_reader ok
```

Use PR329 temporal control-plane ideas as design input, but do not merge PR329 as dependency unless reviewed.

## R3 — Day0 source authority from real observation object

Files:

```text
src/events/triggers/day0_extreme_updated.py
src/events/day0_authority.py
tests/events/test_day0_live_authority_binding.py
```

Hard rule:

```text
settlement_day_observation_authority scanner is catch-up/evidence only.
It cannot produce LIVE_AUTHORITY unless the row explicitly contains:
  live_authority_status=LIVE_AUTHORITY
  source/station/local-date/DST/metric/rounding all match
```

Better:

```text
emit live Day0 event only from Day0ObservationContext live path.
```

## R4 — LiveBinInference kernel isolated

Files:

```text
src/strategy/live_inference/state.py
src/strategy/live_inference/absorbing_boundary.py
src/strategy/live_inference/bayesian_factors.py
src/strategy/live_inference/inference_engine.py
tests/strategy/live_inference/test_inference_engine_event_bound.py
```

Prove:

```text
event.available_at <= decision_time
Day0 K_t applied before Markov/factors
forecast COMPLETE only
orderbook does not change q
p_live normalized
zero mass fail closed
```

Do **not** mutate `decision.edge` inside `cycle_runtime`. That was a patch smell in PR328.

## R5 — Native executable cost kernel isolated

Files:

```text
src/strategy/live_inference/executable_cost.py
src/strategy/live_inference/trade_score.py
tests/strategy/live_inference/test_event_bound_executable_cost_trade_score.py
```

Prove:

```text
buy_yes -> YES ask/depth
buy_no -> NO ask/depth
sell -> held token bid/depth
no midpoint
no last trade
no NO complement
fee/tick/min-order/negRisk from ExecutableMarketSnapshotV2
ExecutionPrice.assert_kelly_safe()
```

## R6 — FDR/Kelly/Risk adapter contracts

Files:

```text
src/events/money_path_adapters.py
tests/events/test_event_bound_fdr_kelly_risk_adapters.py
```

This PR must not submit.

Tests:

```text
test_full_family_hypotheses_logged_before_fdr
test_duplicate_event_does_not_change_family_denominator
test_fdr_reject_blocks
test_kelly_requires_typed_execution_price
test_kelly_bare_float_forbidden
test_riskguard_red_blocks
test_cluster_cap_blocks
```

## R7 — Final intent receipt, no executor call

Files:

```text
src/engine/event_bound_final_intent.py
tests/engine/test_event_bound_final_intent_receipt.py
```

Prove receipt fields:

```text
event_id
family_id
candidate_id
condition_id
token_id
executable_snapshot_id
trade_score_id
fdr_family_id
kelly_cost_basis_id
final_intent_id
```

No venue adapter import.

## R8 — Reactor integration, still no live submit

Files:

```text
src/events/reactor.py
src/main.py
tests/events/test_reactor_event_bound_decision.py
```

Reactor calls `EventBoundDecisionEngine`, not `run_cycle`.

Acceptance:

```text
test_reactor_does_not_call_run_cycle
test_reactor_does_not_import_venue_adapter
test_reactor_event_specific_final_intent_ready
test_reactor_logs_all_rejections
```

## R9 — Executor submit behind explicit live cap

Files:

```text
src/events/reactor.py
src/engine/event_bound_final_intent.py
tests/money_path/test_edli_live_submit_contract.py
```

Only then allow:

```text
Day0 hard fact tiny live pilot
```

Not allowed:

```text
forecast event live submit unless separate acceptance passes
market-channel direct trade
stale-book directional trade
taker FOK/FAK unless execution-law PR approves
```

## R10 — Daemon online runbook and smoke

Files:

```text
docs/operations/edli_v1/EDLI_REDEMPTION_DAEMON_RUNBOOK.md
tests/money_path/test_edli_online_invariants.py
```

Must include actual operator commands and smoke checks:

```text
launchctl restart
event writer starts
market channel connects to active tokens
user channel/reconcile fill truth available
one synthetic event replay no-submit
one Day0 live-authority dry-run no-submit
reports generated
```

---

# 9. The 20+ P0 hidden branches this redemption must explicitly close

Below is the redemption bug register. These are not “review suggestions”; each needs a test or cannot ship.

| ID    | P0 branch                     | Failure mechanism                                                         | Required prevention                            | Required test                                   |
| ----- | ----------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------- |
| P0-01 | Event-triggered old cycle     | Event triggers broad `run_cycle`; unrelated order counted as event result | Reactor cannot call `run_cycle`                | `test_reactor_does_not_call_run_cycle`          |
| P0-02 | Summary-as-proof              | Cycle summary fields fabricated/aggregated                                | Receipt from final-intent builder only         | `test_summary_cannot_authorize_event_submit`    |
| P0-03 | Snapshot unbound              | Any fresh executable snapshot passes                                      | Snapshot must match event family/token         | `test_wrong_condition_snapshot_blocks`          |
| P0-04 | FDR denominator shrink        | Event evaluates one bin only                                              | Full family generated/logged before FDR        | `test_event_fdr_full_family_denominator`        |
| P0-05 | FDR duplicate inflation       | Duplicate event logs family twice                                         | idempotency + family attempt key               | `test_duplicate_event_no_fdr_double_count`      |
| P0-06 | Kelly float regression        | TradeScore float passed as cost                                           | `ExecutionPrice.assert_kelly_safe()`           | `test_kelly_rejects_bare_float`                 |
| P0-07 | NO complement cost            | buy NO uses `1 - YES`                                                     | native NO ask/depth only                       | `test_buy_no_native_no_ask_only`                |
| P0-08 | Midpoint/display cost         | UI probability used as cost                                               | ask/bid book walk only                         | `test_midpoint_display_price_forbidden`         |
| P0-09 | Last trade cost               | last_trade_price used as executable                                       | forbid last trade cost                         | `test_last_trade_forbidden_cost`                |
| P0-10 | Market WS as fill truth       | public channel treated as order/fill state                                | fill truth only user channel/reconcile         | `test_market_channel_cannot_write_fill_state`   |
| P0-11 | Tick-size drift               | tick_size_change not invalidating snapshot                                | invalidate/refresh snapshot                    | `test_tick_size_change_invalidates_snapshot`    |
| P0-12 | Min order drift               | new min_order ignored                                                     | snapshot/current orderbook gate                | `test_min_order_change_blocks`                  |
| P0-13 | negRisk mismatch              | wrong negRisk option                                                      | require snapshot negRisk                       | `test_negrisk_mismatch_blocks`                  |
| P0-14 | Day0 observability table live | old cycle writes observation table then EDLI reads it                     | live event only from observation source object | `test_observability_row_not_live_authority`     |
| P0-15 | Day0 station mismatch         | wrong station crossing                                                    | station mapping exact                          | `test_station_mismatch_blocks`                  |
| P0-16 | DST/local date mismatch       | observation crosses wrong local day                                       | timezone/DST proof                             | `test_dst_local_date_blocks`                    |
| P0-17 | Rounding mismatch             | local helper differs from settlement                                      | SettlementSemantics only                       | `test_settlement_semantics_only`                |
| P0-18 | Forecast issue-time leakage   | nominal issue time used as availability                                   | source available/committed time                | `test_issue_time_not_availability`              |
| P0-19 | Empty expected steps          | `set().issubset()` passes                                                 | fail closed if expected unknown                | `test_expected_steps_unknown_blocks`            |
| P0-20 | Partial forecast live         | partial snapshot trades                                                   | COMPLETE only                                  | `test_partial_allowed_no_live_submit`           |
| P0-21 | Live config ahead of proof    | daemon starts half-implementation                                         | config off until final gate                    | `test_config_live_requires_acceptance_manifest` |
| P0-22 | Wrong DB write                | world/trade conn confusion                                                | connection-shape tests                         | `test_wrong_db_fails_loud`                      |
| P0-23 | Schema CHECK drift            | SCHEMA_VERSION bumps without table checks                                 | schema hash + release gate                     | `test_all_current_schema_checks_accept_version` |
| P0-24 | User-channel absent           | submit allowed with no fill reconciliation                                | WS/reconcile gate required                     | `test_user_channel_or_reconcile_required`       |
| P0-25 | Timeout unknown collapse      | timeout treated as reject/fill                                            | UNKNOWN state explicit                         | `test_submit_timeout_unknown_blocks_followup`   |

---

# 10. What to salvage from PR328

PR328 contains salvageable pieces, but only if extracted carefully.

## Salvageable

```text
src/events/opportunity_event.py
src/events/event_store.py
src/events/replay.py
src/events/dead_letter.py
schema modules for opportunity_events / processing / dead_letters
parts of market_channel_ingestor token metadata and public-channel separation
parts of no_trade_regret schema, after expansion
pure helper functions in executable_cost.py
pure robust_trade_score formula
tests for deterministic event id/hash
reports as read-only scaffold
```

## Not salvageable as-is

```text
src/events/reactor.py live submit model
src/engine/event_reactor_adapter.py run_cycle adapter
cycle_runtime EDLI monkeypatch hooks
config/settings.json enabling live
main.py event-reactor live scheduler path as currently written
tests that prove only strings/synthetic lambda gates
compat writer that inserts fake no_trade_events rows
```

## Must be deleted or demoted

```text
submit_existing_cycle_for_event()
EventSubmissionReceipt from run_cycle summary
existing_cycle summary as event submit proof
_cycle_lock + run_cycle as EDLI final_intent_submit
```

---

# 11. Why “fixing PR328” is worse than rebuilding

PR328 has now inserted EDLI logic into:

```text
cycle_runner.py
cycle_runtime.py
main.py
events/reactor.py
event_reactor_adapter.py
live_inference/*
state/schema/*
```

The result is a hard-to-reason hybrid:

```text
event code partially owns trigger
cycle_runtime partially owns inference
reprice path partially owns TradeScore
summary partially owns receipt
reactor partially owns live cap
executor still owns side effects
```

That violates the principle “one semantic object, one authority.” The more patches you add, the harder it becomes to prove causality.

Redemption therefore must centralize the new semantics in a small number of objects:

```text
EventBoundCandidateFamily
LiveBinInferenceState
NativeExecutableCost
RobustExecutableTradeScore
EventBoundFinalIntentReceipt
```

and only then connect them to existing Zeus authorities.

---

# 12. Correct E2E design after redemption

## 12.1 Data flow

```text
FORECAST_SNAPSHOT_READY
  -> ForecastEventBinder
  -> EventBoundCandidateFamily
  -> p_cal from executable_forecast_reader
  -> LiveBinInferenceEngine
  -> ExecutableSnapshotBinder
  -> NativeExecutableCost
  -> TradeScore
  -> FDR
  -> Kelly
  -> RiskGuard
  -> FinalIntentBuilder
  -> Executor

DAY0_EXTREME_UPDATED
  -> Day0AuthorityGate
  -> EventBoundCandidateFamily
  -> AbsorbingBoundary K_t
  -> LiveBinInferenceEngine
  -> same downstream path

BOOK/BBA/MARKET_CHANNEL
  -> Quote cache / executable snapshot invalidation / evidence ledger
  -> no direct live directional trade in EDLI v1
```

## 12.2 Authority boundaries

| Object                      | Owns                                            | Must not own                               |
| --------------------------- | ----------------------------------------------- | ------------------------------------------ |
| `OpportunityEvent`          | timestamps, identity, payload hash, idempotency | candidate selection, pricing, order submit |
| `EventBoundCandidateFamily` | exact city/date/metric/family/token binding     | probability model                          |
| `LiveBinInferenceEngine`    | p_live update                                   | venue cost, Kelly sizing                   |
| `ExecutableSnapshotBinder`  | snapshot identity/freshness/token map           | probability                                |
| `NativeExecutableCost`      | ask/bid/depth/fee/tick/min-order/negRisk        | posterior probability                      |
| `RobustTradeScore`          | q/c/fill/penalty score                          | FDR/Kelly/submit                           |
| `FDRAdapter`                | full-family BH/FDR proof                        | event idempotency                          |
| `KellyAdapter`              | typed sizing proof                              | executable snapshot creation               |
| `RiskGuardAdapter`          | portfolio/risk block                            | venue API                                  |
| `FinalIntentBuilder`        | final intent receipt                            | fill truth                                 |
| `Executor`                  | side effects                                    | event semantics                            |
| `UserChannel/Reconcile`     | order/fill truth                                | public book truth                          |

---

# 13. Required code-level contract

## 13.1 Reactor must become this

```python
class OpportunityEventReactor:
    def __init__(self, store, decision_engine, regret_ledger, config):
        ...

    def _process_one(self, event, decision_time):
        assert_available_for_decision(event, decision_time)

        result = self.decision_engine.evaluate(
            EventBoundDecisionRequest(
                event=event,
                decision_time=decision_time,
                mode=self.config.mode,
                operator_live_cap=self.config.live_cap,
            )
        )

        if result.status == "NO_TRADE":
            regret_ledger.write(result)
            store.mark_processed(event.event_id)
            return

        if result.status == "FINAL_INTENT_READY":
            if not self.config.live_submit_enabled:
                regret_ledger.write_shadowless_no_submit(result, reason="LIVE_SUBMIT_DISABLED")
                store.mark_processed(event.event_id)
                return

            receipt = executor.submit(result.final_intent)
            lifecycle.record(receipt)
            store.mark_processed(event.event_id)
            return
```

No `run_cycle`.

## 13.2 Decision engine must own sequence

```python
class EventBoundDecisionEngine:
    def evaluate(self, request):
        event = request.event
        family = candidate_binder.bind(event, request.decision_time)

        source_gate.assert_pass(family, event)
        inference = inference_engine.evaluate(family, event)
        executable = executable_binder.bind(family, inference)
        scores = trade_score_engine.score(family, inference, executable)

        candidates = [s for s in scores if s.trade_score > 0]
        if not candidates:
            return no_trade("TRADE_SCORE")

        fdr = fdr_adapter.evaluate_full_family(family, candidates)
        if not fdr.pass_:
            return no_trade("FDR")

        kelly = kelly_adapter.size(fdr.selected, executable.execution_price)
        if not kelly.pass_:
            return no_trade("KELLY")

        risk = riskguard_adapter.check(kelly)
        if not risk.pass_:
            return no_trade("RISK_GUARD")

        final_intent = final_intent_builder.build(...)
        receipt = final_intent_builder.receipt(...)
        return EventBoundDecisionResult(status="FINAL_INTENT_READY", ...)
```

## 13.3 No field can be inferred from wrong authority

Forbidden:

```text
infer condition_id from slug string when market_events_v2 missing
infer station match from station_id non-empty
infer local date from UTC date
infer NO cost from YES cost
infer FDR pass from selected decision count
infer Kelly pass from nonzero size
infer event submit from cycle summary
infer fill from market channel
```

---

# 14. Tests that must exist before any review resumes

These are minimum gate tests. Without them, the PR is not reviewable.

## 14.1 Event binding

```text
tests/events/test_event_bound_candidate_binding.py
  test_forecast_event_binds_exact_city_date_metric_family
  test_day0_event_binds_exact_city_date_metric_family
  test_wrong_city_snapshot_rejected
  test_wrong_date_market_rejected
  test_wrong_metric_market_rejected
  test_no_condition_or_token_without_family_topology_blocks
```

## 14.2 Temporal and leakage

```text
tests/events/test_event_temporal_filtration.py
  test_available_at_future_rejected
  test_received_at_future_rejected
  test_forecast_issue_time_cannot_authorize
  test_observation_time_cannot_replace_available_at
  test_backfilled_event_never_live_authorizes
```

## 14.3 Day0

```text
tests/events/test_day0_live_authority.py
  test_observability_table_row_is_not_live_authority
  test_live_observation_context_can_emit_live_authority
  test_station_mismatch_blocks
  test_dst_ambiguous_blocks
  test_rounding_unit_mismatch_blocks
  test_metric_swap_blocks
  test_settlement_semantics_only
```

## 14.4 Forecast

```text
tests/events/test_forecast_snapshot_ready_authority.py
  test_expected_steps_unknown_blocks
  test_00_12_expected_steps_complete
  test_06_18_expected_steps_complete
  test_partial_allowed_no_live_submit
  test_complete_requires_executable_reader_ok
```

## 14.5 Market channel

```text
tests/events/test_market_channel_authority.py
  test_public_market_channel_never_fill_truth
  test_no_default_yes_for_unknown_token
  test_no_token_metadata_blocks
  test_tick_size_change_invalidates_snapshot
  test_market_channel_coalescer_budget
  test_user_channel_or_reconcile_required_for_fill
```

## 14.6 Inference / cost / score

```text
tests/strategy/live_inference/test_event_bound_inference_cost_score.py
  test_day0_kill_applies_before_trade_score
  test_orderbook_does_not_change_q
  test_buy_yes_uses_yes_ask
  test_buy_no_uses_no_ask
  test_sell_uses_bid
  test_midpoint_forbidden
  test_last_trade_forbidden
  test_fee_erases_edge
  test_min_order_blocks
```

## 14.7 FDR / Kelly / Risk / final intent

```text
tests/events/test_event_bound_money_path.py
  test_full_family_fdr_required
  test_duplicate_event_no_double_count
  test_kelly_requires_execution_price
  test_riskguard_blocks
  test_final_intent_receipt_matches_event
  test_summary_cannot_authorize_submit
  test_reactor_does_not_call_run_cycle
```

---

# 15. CI gates required before merge

A PR that claims EDLI money-path integration must pass:

```bash
python scripts/check_schema_version.py
python scripts/check_table_registry_coherence.py
python scripts/ci/assert_test_quality.py

python -m pytest -q \
  tests/events/test_event_bound_candidate_binding.py \
  tests/events/test_event_temporal_filtration.py \
  tests/events/test_day0_live_authority.py \
  tests/events/test_forecast_snapshot_ready_authority.py \
  tests/events/test_market_channel_authority.py \
  tests/strategy/live_inference/test_event_bound_inference_cost_score.py \
  tests/events/test_event_bound_money_path.py \
  --maxfail=1

python -m pytest -q tests/money_path --maxfail=1
python3 scripts/replay_correctness_gate.py
```

Additionally:

```text
money-path-required must pass
money-path-release-gate must pass
replay-correctness must pass
topology-file-arrangement must pass
full-pytest-sweep cannot be skipped for the final live PR
```

Latest PR328 still fails `money-path-required`; that alone blocks it.

---

# 16. Updated agent prompt philosophy

The replacement implementation prompt must start with proofs, not file creation.

## 16.1 Old prompt failure

Old prompt effectively said:

```text
Implement all EDLI components.
Make it online after reboot.
Use subagents.
Run tests.
```

That let agent do:

```text
schema + code + scheduler + config
then retrofit semantic receipts
```

## 16.2 New prompt must say

```text
Do not write runtime/scheduler/config until R0–R7 proof tests exist and fail.

First implement tests that prove:
  event-specific binding
  side-effect authority
  no run_cycle as submit proof
  no market channel fill truth
  no snapshot unbound gate
  no FDR/Kelly no-op

Only then implement code to make tests pass.

After R7, open a separate PR for runtime scheduler wiring.
```

## 16.3 The replacement Codex instruction should include this kill switch

```text
If you find yourself needing to call run_cycle() from EDLI reactor,
STOP and report REVIEW_REQUIRED.
Do not wrap it.
Do not summarize it into a receipt.
Do not claim live mode.
```

---

# 17. Concrete next step

## Immediate action

Do **not** continue fixing PR328 as-is. First commit should be a safety commit:

```text
config/settings.json:
  edli_v1.enabled = false
  edli_v1.reactor_mode = "off"
```

and add:

```text
docs/operations/edli_v1/REDEMPTION_ROOT_CAUSE.md
```

Then create a new branch:

```text
redemption/edli-proof-kernel
```

Start with R1.

## What to tell implementation agent

Give the agent this first task only:

```text
Implement R1 only.

No scheduler.
No config enable.
No run_cycle.
No executor.
No venue.
No market websocket.
No live submit.
No reports.

Build EventBoundCandidateFamily and EventBoundDecisionEngine skeleton.
Write tests proving event-specific binding and rejecting unbound events.
```

If the agent cannot make R1 pass without touching cycle_runtime, the design is still wrong.

---

# 18. Redemption acceptance contract

The system is not redeemed until all are true:

```text
A01 Reactor never calls run_cycle.
A02 Reactor never imports venue adapter.
A03 EventBoundDecisionEngine produces typed EventBoundDecisionResult.
A04 EventBoundCandidateFamily binds city/date/metric/family/token exactly.
A05 Forecast events require causal_snapshot_id and COMPLETE source evidence.
A06 Day0 events require live observation authority, not observability-only rows.
A07 Market channel never writes fill/order truth.
A08 Market channel token metadata has canonical YES/NO mapping.
A09 tick/min-order/negRisk/freshness come from executable snapshot.
A10 Orderbook event cannot change q_live.
A11 Day0 K_t uses SettlementSemantics and source/station/local-date/DST/metric/rounding gates.
A12 Native executable cost uses YES/NO ask for buys and held-token bid for sells.
A13 NO complement/midpoint/last trade cost are impossible by type/test.
A14 RobustTradeScore is computed before FDR/Kelly.
A15 FDR logs/tests full family denominator.
A16 Kelly receives typed ExecutionPrice only.
A17 RiskGuard blocks are mandatory.
A18 FinalExecutionIntent receipt is event-bound.
A19 Executor is the only side-effect path.
A20 User channel/reconcile are the only fill truth.
A21 Every rejection writes full no-trade regret with q/c/fill/score data.
A22 Later outcome columns are not readable by live inference.
A23 Scheduler maintenance remains intact.
A24 EDLI config stays off until all above pass.
A25 Daemon reboot runbook and live smoke are executed or explicitly operator-gated.
A26 money-path-required is green.
A27 money-path-release-gate is green.
A28 replay correctness is green.
```

---

# 19. Final redemption conclusion

Your grading of the current state as F is fair. The implementation built from the prior prompt did not reach code-review maturity because it did not first establish the semantic proof kernel. The corrected path is not more patching inside PR328; it is a **proof-first reconstruction**.

The design itself is still worth saving, but only in this restricted form:

```text
EDLI = event-time causal opportunity trigger + event-bound decision proof
not
EDLI = online event loop wrapping legacy cycle summary
```

The next mergeable artifact should be tiny compared with PR328: a pure event-bound binding/inference proof PR, no live scheduler, no executor, no config online. Once that passes, then we rebuild toward live in explicit cuts.

[1]: https://docs.polymarket.com/developers/CLOB/websocket/market-channel "Market Channel - Polymarket Documentation"
[2]: https://docs.polymarket.com/trading/orderbook "Orderbook - Polymarket Documentation"
[3]: https://docs.polymarket.com/trading/orders/create "Create Order - Polymarket Documentation"
[4]: https://www.ecmwf.int/en/forecasts/datasets/open-data "Open data | ECMWF"
