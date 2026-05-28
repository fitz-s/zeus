# PR332 深度审核 / EDLI Redemption Review

Source: user-provided PR332 deep audit in the Codex thread on 2026-05-24.

## Verdict

PR332 仍然是 NO-GO。不能 merge，不能 daemon reboot。

它比 PR328 明显前进了一步：PR332 把目标降级为 “EDLI v1 no-submit online implementation”，real_order_submit_enabled=false、taker_fok_fak_live_enabled=false，并且补了不少 redemption tests、schema 注册、receipt 字段和 no-submit 边界。PR body 也明确说它 supersedes PR328/PR331，并承认 daemon restart、live Polymarket websocket/user-channel smoke、real executor submit 都没有运行。

但按原要求审核，PR332 仍然没有达到“语义顺序和 side-effect authority 被证明后再接线”的标准。它把 direct venue submit 禁用了，这是正确的；可是它仍然通过 submit_existing_cycle_for_event(event, run_cycle=run_cycle) 把 EDLI event 接进旧的 full run_cycle。也就是说，它仍然不是一个真正的 event-bound EDLI decision engine，而是一个 event-triggered existing-cycle no-submit wrapper。

这次最重要的结论：

```text
#332 is safer than #328 because it is no-submit.
#332 is not merge-safe because it still causes broad existing-cycle runtime side effects,
regresses market discovery coverage, and does not use the proof-kernel as the runtime authority.
```

## 1. 审核范围和方法

我按原来的 live-money / repo-grounded 标准审，不按普通 PR review 标准审。具体做了：

1. 查 PR metadata / body / changed-file list / CI workflow status。
2. 查核心 runtime wiring:
   `src/main.py`
   `src/events/reactor.py`
   `src/engine/event_reactor_adapter.py`
   `src/engine/cycle_runner.py`
   `src/engine/cycle_runtime.py`
3. 查 proof-kernel files:
   `src/events/candidate_binding.py`
   `src/events/decision_engine.py`
   `src/engine/event_bound_final_intent.py`
   `src/events/money_path_adapters.py`
4. 查 trigger / data surfaces:
   `forecast_snapshot_ready.py`
   `day0_extreme_updated.py`
   `market_channel_ingestor.py`
   `market_scanner.py`
5. 查 schema / no-trade / event store:
   `opportunity_events_schema.py`
   `event_store.py`
   `event_writer.py`
   `no_trade_regret_events_schema.py`
   `no_trade_regret.py`
6. 查 tests 是否证明真实语义，而不是字符串存在 / synthetic lambda gate。
7. 复核 Polymarket orderbook / market channel / user channel / order type semantics 和 ECMWF Open Data semantics。

我没有本地 clone，也没有本地跑 tests；我基于 GitHub PR/API、file patch、workflow logs、repo file content 和官方文档审阅。PR body 自报的 tests 包括 210 passed、717 passed 等，但它同时明确 real daemon / live WS / user-channel / real executor submit 都没有跑。

## 2. PR332 相比 PR328 的真实进步

PR332 有几处值得保留：

### 2.1 CI 状态明显改善

PR328 当时 money-path-required 和 money-path-release-gate 都失败；PR332 最新 workflow 显示 money-path-required、money-path-release-gate、replay-correctness 都已经成功，full-pytest-sweep 仍然 skipped。PR body 也列出 schema/table/test-quality、money-path、replay correctness 都已跑。

### 2.2 PR332 增加了 proof-kernel scaffold

新增了：

```text
src/events/candidate_binding.py
src/events/decision_engine.py
src/engine/event_bound_final_intent.py
src/events/money_path_adapters.py
```

`candidate_binding.py` 现在能把 forecast/Day0 event 绑定到 exact city/date/metric candidate family，要求 causal_snapshot_id、完整 YES/NO token map，并拒绝 market-data event 直接生成 live trade candidates。

`EventBoundDecisionEngine` 目前只做 candidate binding，不做 runtime side effects。

`EventBoundFinalIntentReceipt` 也被定义成包含 event/family/candidate/condition/token/executable snapshot/ExecutionPrice/FDR/Kelly/Risk/final_intent/side_effect_status 的 typed receipt。

这些都是正确方向。

### 2.3 No-submit boundary 更明确

`config/settings.json` 中 EDLI 配置为：

```json
"enabled": true,
"reactor_mode": "live_no_submit",
"real_order_submit_enabled": false,
"taker_fok_fak_live_enabled": false
```

Reactor 也会拒绝 `side_effect_status in {"COMMAND_CREATED", "SUBMITTED"}`，并在 `real_order_submit_enabled=false` 时阻断非 `NO_SUBMIT` receipt。

这比 #328 的 live-submit 形态安全。

## 3. P0 blockers

### P0-1 — EDLI reactor 仍然通过 run_cycle 接入旧 broad runtime；这不是 event-bound proof kernel

`src/main.py` 中 `_edli_event_reactor_cycle()` 构造 reactor 后，`final_intent_submit` 是 `_submit_via_existing_cycle()`，它直接调用：

```text
submit_existing_cycle_for_event(
    event,
    run_cycle=run_cycle,
    taker_fok_fak_live_enabled=...,
    real_order_submit_enabled=...,
)
```

`submit_existing_cycle_for_event()` 再把 event 映射到 `DiscoveryMode.DAY0_CAPTURE` 或 `DiscoveryMode.UPDATE_REACTION`，调用：

```text
summary = run_cycle(mode, edli_event_context=context)
```

然后从 summary 读取 `edli_submit_accepted`、`edli_trade_score_positive`、`edli_fdr_pass`、`edli_kelly_pass`、`edli_final_intent_id` 等字段，拼成 `EventSubmissionReceipt`。

这仍然违反 redemption 核心要求：

```text
Wrong:
  event -> run_cycle(mode, context) -> summary -> EventSubmissionReceipt
Required:
  event -> EventBoundDecisionEngine -> EventBoundDecisionResult
        -> EventBoundFinalIntentReceipt
```

为什么这是 P0，即使 no-submit？因为 `run_cycle()` 不是只做 EDLI candidate evaluation。它会先做 pending position reconciliation、chain sync、stale order cleanup、command recovery、monitoring phase、force-exit sweep 等 broad runtime work。`cycle_runner.run_cycle()` 在进入 discovery 前就会运行 portfolio/reconciliation/command recovery/monitoring related paths。

所以 `live_no_submit` 并不等于 side-effect free。EDLI event reactor 每分钟跑一次，若有 pending EDLI event，就可能触发一个 full cycle 的非-EDLI side effects。PR body 声称 “no real venue submission”，但 side-effect authority 不只包括新订单 submit；还包括 order cleanup、command recovery、exit lifecycle、position state writes、chain reconcile, and portfolio mutations.

Required fix:

- EDLI reactor must not call `run_cycle()`.
- `submit_existing_cycle_for_event()` must be deleted or quarantined as legacy spike code.
- Runtime path must use `EventBoundDecisionEngine` or a new `EventBoundNoSubmitEngine` directly.
- `run_cycle` can remain scheduler maintenance / old discovery path, but not EDLI event proof authority.

Minimum failing test to add:

```python
def test_edli_reactor_does_not_call_run_cycle_or_cycle_runner_adapter():
    assert "submit_existing_cycle_for_event" not in Path("src/main.py").read_text()
    assert "run_cycle" not in Path("src/engine/event_reactor_adapter.py").read_text()
```

The current test only checks `run_cycle` is absent from `src/events/reactor.py`, which is too narrow.

### P0-2 — Proof kernel exists but is not the runtime authority

`src/events/decision_engine.py` is a clean pure binding skeleton: it evaluates an `EventBoundDecisionRequest` and returns `CANDIDATE_FAMILY_READY` or `NO_TRADE`; it never routes orders.

But `src/main.py` / `src/events/reactor.py` do not use `EventBoundDecisionEngine` as the live/no-submit authority. Runtime still goes:

```text
EventStore -> Reactor -> submit_existing_cycle_for_event -> run_cycle -> summary receipt
```

The proof kernel is therefore a test artifact / scaffold, not the actual runtime path.

Why this matters:
If the runtime authority is still old cycle summary, then the semantic object `EventBoundCandidateFamily` is not guaranteed to be the same family that got repriced, FDR-tested, Kelly-sized, or final-intent-built. The system can pass proof-kernel unit tests while runtime ignores the kernel.

Required fix:

`src/events/reactor.py` should call:

```text
EventBoundDecisionEngine.evaluate(...)
```

not:

```text
final_intent_submit(event) -> submit_existing_cycle_for_event(... run_cycle ...)
```

For no-submit, the proper runtime is:

```text
event -> bind candidate family
      -> source truth
      -> inference
      -> executable cost / TradeScore
      -> FDR/Kelly/Risk proof
      -> EventBoundFinalIntentReceipt(side_effect_status=NO_SUBMIT)
      -> no_trade/regret/report
```

No `run_cycle`.

### P0-3 — PR332 regresses market_discovery from full weather discovery to slug-pattern-only

This is a high-impact live runtime regression unrelated to EDLI but introduced by PR332.

`src/main.py::_market_discovery_cycle()` now imports `find_slug_pattern_weather_markets` and calls only that function.

But the scanner itself documents slug-pattern fetch as a fallback for markets that are newly opened and may not yet be tag-query reachable; it enumerates city/date/prefix tuples under request/budget caps.

That means PR332 appears to replace broad tag-based weather discovery with slug-only fallback. The previous main comment in the patch explicitly said full tag-query “all ~51 cities” is primary and slug-pattern is fallback, but the PR deletes that and uses slug-only. This can reduce market substrate coverage and silently starve executable snapshots for cities not covered by the slug pattern cursor/budget.

Required fix:

`_market_discovery_cycle` must call `find_weather_markets(include_slug_pattern=True)` or equivalent full discovery + slug fallback.

Do not merge EDLI while it regresses baseline market discovery. This is a P0 because it affects existing live substrate outside no-submit EDLI.

### P0-4 — PR332 removes the fresh-at-submit recapture path and reintroduces executable snapshot staleness failure

Current `cycle_runtime._reprice_decision_from_executable_snapshot()` now does:

```text
snapshot = get_snapshot(conn, snapshot_id)
...
if not is_fresh(snapshot, datetime.now(timezone.utc)):
    raise ValueError("executable_snapshot_stale")
```

But the repo contains a root-cause document saying live system had “~0 NEW entries” because above-floor edges died at order construction on `executable_snapshot_stale`; the documented fix is fresh-at-submit recapture using `capture_executable_market_snapshot`, not widening the 30s gate and not simply raising stale.

PR332 patch removes the helper path that had `_ensure_fresh_executable_snapshot`, `_market_dict_from_snapshot`, `_reprice_recapture_fresh_snapshot`, and snapshot_id propagation, replacing it with the hard stale raise. This is likely a regression of a separate live-money fix.

Why this matters even no-submit:
EDLI no-submit still calls `run_cycle()` and can enter reprice/final intent build. If the snapshot is stale, it will fail before producing a valid no-submit receipt. More importantly, this change affects the existing non-EDLI runtime path too.

Required fix:

Either:

- Restore fresh-at-submit recapture logic exactly as specified in `EXEC_FRESHNESS_ROOTCAUSE_2026-05-24.md`,

or:

- Remove this PR’s cycle_runtime reprice changes entirely and keep EDLI no-submit detached from existing `cycle_runtime`.

Do not silently regress a known live blocker.

### P0-5 — EventBoundFinalIntentReceipt exists but is not used as the authoritative runtime receipt

`src/engine/event_bound_final_intent.py` defines `EventBoundFinalIntent` and `EventBoundFinalIntentReceipt`, and `build_event_bound_final_intent_receipt()` asserts the `ExecutionPrice` is Kelly-safe.

But runtime receipt is still built in `submit_existing_cycle_for_event()` by reading summary fields from `run_cycle()` output, not by calling `build_event_bound_final_intent_receipt()`.

That means the typed receipt object is not the runtime authority. It is only unit-tested in isolation. The no-submit path can still say “accepted” based on summary fields, not based on a real `EventBoundFinalIntentReceipt`.

Required fix:

The only acceptable no-submit proof object should be:

```text
EventBoundFinalIntentReceipt(side_effect_status="NO_SUBMIT")
```

generated by the final-intent builder, not summary parsing.

Add failing test:

```text
test_submit_existing_cycle_summary_cannot_create_event_submission_receipt
test_runtime_receipt_must_be_event_bound_final_intent_receipt
```

### P0-6 — run_cycle broad side effects make “no-submit” boundary misleading

The PR says “real venue submission disabled.” That is good, but incomplete.

`run_cycle()` can still perform:

```text
pending position reconciliation
chain sync
stale order cleanup
command recovery
promote pending trades
force-exit sweep
monitoring phase
portfolio persistence
tracker persistence
```

The cycle runner excerpt shows it performs command recovery, stale entry order cleanup, pending trade promotion, force-exit sweep, and monitoring before discovery submit logic.

Therefore:

```text
EDLI event -> run_cycle()
```

is not “no side effect.” It is “no new EDLI entry submit if the no-submit guard works,” but still broad runtime side effects may occur. This violates the original redemption requirement that no-submit EDLI be an event-bound proof path, not a runtime cycle trigger.

Required fix:

No-submit EDLI must run in a proof-only engine that does not call:

```text
cycle_runner.run_cycle
cycle_runtime.execute_monitoring_phase
command_recovery
cleanup_stale_entry_orders
execute_exit
executor
venue adapter
```

## 4. P1 blockers

### P1-1 — No-trade regret still loses most event-bound q/c/fill/score information in reactor-level rejections

`no_trade_regret_events_schema.py` has been expanded to include `q_live`, `c_fee_adjusted`, `c_cost_95pct`, `p_fill_lcb`, `trade_score`, family fields, causal snapshot, executable snapshot, etc.

But `OpportunityEventReactor._write_regret()` only populates:

```text
event_id
rejection_stage/rejection_reason
regret_bucket
market_slug / condition_id / token_id / outcome_label
```

from event payload.

So many reactor rejections still write thin regret rows. That is better than nothing, but it cannot power the EDLI reports promised by the package.

Required fix:

`EventBoundDecisionResult` must carry the richer fields, and reactor must write them:

```text
decision_time
city/target_date/metric
family_id/bin_label/direction
q_live/q_lcb
c_fee_adjusted/c_cost_95pct
p_fill_lcb
trade_score
native_quote_available
source_status
family_complete
causal_snapshot_id
executable_snapshot_id
```

This cannot be fixed inside `_write_regret(event, stage, reason)` alone; it requires moving regret writing to the decision-result object.

### P1-2 — Forecast completeness fallback may require full-cycle steps rather than target-date required steps

`forecast_snapshot_ready.py` uses `_required_expected_steps()` and falls back to `ecmwf_open_data_expected_steps(cycle_hour)` when coverage/source-run expected steps are absent. For 00/12 it returns the whole 0–360h set, and for 06/18 the 0–144h set.

Official ECMWF docs do distinguish 00/12 vs 06/18 step ranges; PR332 correctly captures that broad cycle distinction. But EDLI live completeness should be target local-day required extrema steps, not necessarily the full cycle dissemination horizon. Requiring the full horizon as fallback can convert a valid target-day usable snapshot into `PARTIAL_BLOCKED`, which may not be unsafe but is a liveness / alpha-loss bug. ECMWF’s Open Data page confirms release/cycle step differences, so cycle-specific logic is needed, but it should be target-window specific rather than whole-cycle by default.

Required fix:

When `source_run_coverage.expected_steps_json` is missing:

derive required steps from `ForecastTargetScope.target_window_start/end`,
city timezone, target date, metric, track, and source cycle.

If that cannot be done, fail closed with `EXPECTED_STEPS_UNKNOWN`; do not substitute the entire forecast horizon unless the target actually requires it.

### P1-3 — Day0 catch-up still reads settlement_day_observation_authority from trade DB

`main._edli_emit_day0_extreme_events()` still calls `trigger.scan_authority_rows(observation_conn=trade_conn, ...)`.

`day0_extreme_updated.py` now distinguishes `observation_context_to_live_observation()` as the online source hook and says the authority scanner is catch-up/evidence and defaults to `OBSERVABILITY_ONLY`.

This is safer than PR328, but still awkward:

- event reactor catch-up depends on a trade DB observability table;
- live observation hook is buried inside cycle_runtime;
- event-driven Day0 still depends on old cycle executing to create some observation context;
- `scan_authority_rows` can still emit non-live events every minute and consume event-store resources.

Required fix:

For no-submit online:

- Keep `scan_authority_rows` disabled by default or evidence-only.
- Add a dedicated Day0 observation ingestion/capture hook outside `cycle_runtime`.
- Make scanner only a bounded replay/catch-up utility invoked by operator/report, not every reactor tick.

### P1-4 — Market channel online service can write/commit from a long-lived thread with a single world connection

Market channel service creates a daemon thread, opens `world_conn`, constructs `EventWriter(world_conn)`, starts websocket loop, writes events/evidence, and commits on every message loop.

`EventWriter` is still just a synchronous facade over `EventStore`, not an actual queue/single-writer thread. `EventCoalescer` exists and coalesces market events by `(event_type, entity_key)` with market budget, but flushing still happens in the market-channel thread.

This is not necessarily an immediate no-submit correctness failure, but the original DB lock/backpressure contract was stronger:

```text
raw websocket callback -> coalescer -> single event writer -> world DB
```

PR332 implements:

```text
websocket loop thread -> coalescer -> EventWriter(world_conn) -> commit
```

That can be acceptable only if documented as a single-thread writer for market channel and tested under burst load with concurrent reactor writes. I do not see a true multi-thread concurrency test proving this cannot produce DB lock storms.

### P1-5 — Live cap ledger reserves cap in no-submit mode

In `OpportunityEventReactor`, after a valid receipt, if event type is `DAY0_EXTREME_UPDATED`, it increments `_day0_live_orders_today` and reserves in `edli_live_cap_usage`, regardless of whether `side_effect_status` is `NO_SUBMIT`.

That means no-submit proof cycles can consume the tiny live cap. This is semantically wrong:

```text
NO_SUBMIT proof receipt != live order usage
```

It may block the later actual tiny live pilot because dry/no-submit runs already consumed durable cap.

Required fix:

Reserve cap only when:

```text
side_effect_status in {"COMMAND_CREATED", "SUBMITTED", "ACCEPTED"}
and real_order_submit_enabled=True
```

For no-submit, write a separate no-submit proof counter if needed.

### P1-6 — Market discovery refresh inside market-channel tick-size action is broad and expensive

On tick-size or market-resolved action, `_refresh_snapshot_action()` does:

```text
markets = find_slug_pattern_weather_markets(min_hours_to_resolution=0.0)
if action.condition_id:
    markets = _edli_filter_markets_for_condition(markets, action.condition_id)
...
refresh_executable_market_substrate_snapshots(... markets=markets ...)
```

This means a tick-size change can trigger a slug-pattern market scan rather than directly refreshing the one known condition/token from current executable snapshot identity. It is safer than no refresh, but it is not the narrow “refresh this token/condition snapshot” path and can miss the condition if slug-pattern does not return it.

Required fix:

For tick-size changes:

- invalidate snapshot rows by condition/token
- recapture executable snapshot from existing condition/token identity directly
- do not depend on slug-pattern rediscovery

## 5. External reality checks

### Polymarket

PR332 correctly treats public market channel as market data, not fill truth. Official docs define market channel as public L2 data subscribed by asset IDs, with book/price/tick/BBA/new/resolved events; this supports quote/cache/evidence, not account-specific fill truth.

Orderbook docs say token orderbook contains bids/asks/tick/min-order/negRisk/hash, BUY price is best ask, SELL price is best bid, midpoint is display probability, and wide spread can display last traded price instead of midpoint. PR332 mostly aligns with this in cost helpers, though runtime still routes through legacy cycle internals.

User-channel docs define authenticated user-specific order/trade updates, including trade statuses; PR332’s “market channel not fill truth” stance is correct.

FOK/FAK docs define them as immediate market orders, while post-only only works with GTC/GTD and rejects FOK/FAK; PR332 keeps FOK/FAK live disabled, which is correct.

### ECMWF

PR332 partially addresses ECMWF current cycle semantics through `ecmwf_open_data_expected_steps()`, which uses 00/12 full horizon and 06/18 0–144. That broadly matches ECMWF’s Open Data page, but the required EDLI completeness object should still be target-window specific rather than whole-cycle fallback.

## 6. Positive findings

These are real improvements and should be retained:

1. No-submit boundary is explicit. Config has `reactor_mode=live_no_submit`, `real_order_submit_enabled=false`, and `taker_fok_fak_live_enabled=false`.
2. Event store append-only schema is improved. `opportunity_events.event_id` is `TEXT NOT NULL PRIMARY KEY`; update/delete triggers abort mutation.
3. Event store enforces `received_at <= decision_time` as well as `available_at <= decision_time`. `fetch_pending()` filters both available_at and received_at against decision time.
4. Candidate binding proof exists. `bind_event_to_candidate_family()` rejects market-data events, requires causal snapshot, validates forecast completeness/Day0 live authority, and requires condition/YES/NO token IDs for every candidate.
5. No-trade regret schema is now much richer than PR328. It includes q/c/fill/score/family/executable snapshot fields.
6. Market channel token metadata no longer defaults unknown tokens to YES. `MarketChannelIngestor` now uses `MarketTokenMetadata` and returns None if it lacks canonical outcome metadata.

## 7. Acceptance contract status

| Contract | Status | Reason |
| --- | --- | --- |
| A01 immutable event rows | PASS | NOT NULL PK + no update/delete triggers. |
| A02 processing state separate | PASS | separate opportunity_event_processing. |
| A03 deterministic id/hash | PASS/PARTIAL | event model/tests exist; runtime not independently verified here. |
| A04 duplicate event no FDR double-count | PARTIAL | synthetic proof exists; runtime still cycle-summary based. |
| A05 timestamp triad | PASS | event store filters available/received time. |
| A06 no future event inference | PASS | store and event validation block. |
| A07 forecast causal snapshot | PARTIAL | binding requires it; runtime still run_cycle summary. |
| A08 forecast reader reuse | PARTIAL | trigger delegates to executable reader; cycle proof still broad. |
| A09 COMPLETE forecast live eligible | PARTIAL | no-submit only; completeness fallback needs target-step refinement. |
| A10 partial no live trade | PASS | source gate rejects partial. |
| A11 Day0 source/station/local/DST/rounding | PARTIAL | live observation path exists; catch-up still scans observability rows. |
| A12 source mismatch blocks | PASS/PARTIAL | payload gate exists. |
| A13 SettlementSemantics | PASS/PARTIAL | Day0 builder uses semantics, but main constructs generic semantics from row fields. |
| A14 orderbook no q update | PASS | market data events rejected from live trade. |
| A15 market channel online | PARTIAL | wired, but not live-smoked; long-lived DB thread risk. |
| A16 public market not fill truth | PASS | explicit guards and external semantics align. |
| A17 user channel/reconcile fill truth | PASS/PARTIAL | stated and guarded, not smoke-tested. |
| A18 no midpoint/last trade cost | PASS in helpers | runtime proof still via old cycle internals. |
| A19 buy YES native ask | PASS in helpers | no-submit path not fully isolated. |
| A20 buy NO native ask | PASS in helpers | same caveat. |
| A21 sell bid | PASS in helpers | same caveat. |
| A22 no complement cost | PASS in helpers/tests | same caveat. |
| A23 fee/tick/min/negRisk | PARTIAL | market metadata and snapshot gates exist; tick refresh uses broad rediscovery. |
| A24 Kelly typed ExecutionPrice | PARTIAL | `_stamp_edli_kelly_execution_price()` exists; receipt summary still cycle-derived. |
| A25 lifecycle states distinct | OUT OF SCOPE / NOT PROVEN | no live submit; user-channel smoke not run. |
| A26 reactor no venue import | PASS | tests verify reactor import only. |
| A27 FDR full family | PARTIAL | durable FDR proof helpers exist; runtime proof is inside cycle patch. |
| A28 RiskGuard mandatory | PARTIAL | only GREEN-level gate in reactor; broader risk gates live in old cycle. |
| A29 every rejection writes regret | PARTIAL | writes thin rows for reactor-level rejections. |
| A30 later outcome unavailable live | PASS | live reader excludes hindsight columns. |
| A31 scheduler maintenance intact | PARTIAL | jobs preserved, but market_discovery regressed to slug-only. |
| A32 rollback old scheduler intact | FAIL/PARTIAL | existing market_discovery changed; not purely additive. |
| A33 tiny live cap | PARTIAL | cap ledger can be consumed by no-submit proofs. |
| A34 taker FOK/FAK disabled | PASS | config false and cycle context threads false. |
| A35 table registry | PASS/PARTIAL | CI green; table ownership appears registered. |
| A36 money-path CI mapping | PASS | money-path-required latest green by workflow API. |
| A37 wrong-DB test | PASS/PARTIAL | tests exist; long-lived thread still needs runtime proof. |
| A38 no shadow_* prod modules | PASS | tests assert no shadow-named EDLI modules. |
| A39 no real submit | PASS/PARTIAL | EDLI path guards submit; run_cycle broad side effects remain. |
| A40 daemon online ready | FAIL | daemon restart and live WS/user-channel smoke not run; PR body admits this. |

## 8. Required next steps

### Step 1 — Remove or isolate EDLI’s run_cycle dependency

The first merge-blocking change is:

```text
src/main.py:
  _submit_via_existing_cycle must not call run_cycle.
src/engine/event_reactor_adapter.py:
  submit_existing_cycle_for_event must be removed from runtime path.
```

If it remains for tests/spike, put it behind a name like:

```text
legacy_submit_existing_cycle_for_event_SPIKE_DO_NOT_USE
```

and assert main does not import it.

### Step 2 — Keep no-submit but build receipt from proof kernel

Use:

```text
EventBoundDecisionEngine
EventBoundCandidateFamily
LiveBinInferenceEngine
NativeExecutableCost
RobustTradeScore
FDRAdapter
KellyAdapter
RiskGuardAdapter
EventBoundFinalIntentReceipt
```

as the runtime path. Do not parse `run_cycle` summary as proof.

### Step 3 — Revert market discovery regression

Change `_market_discovery_cycle()` back to full discovery:

```text
find_weather_markets(include_slug_pattern=True, ...)
```

or equivalent full-tag + slug fallback. Do not leave slug-pattern-only discovery in a live daemon path.

### Step 4 — Restore or consciously separate exec freshness recapture

Either restore fresh-at-submit recapture in existing cycle runtime or remove this PR’s changes that strip it. The repo’s own root-cause doc says stale executable snapshots are a primary live entry failure and prescribes fresh recapture rather than raising stale.

### Step 5 — Make no-submit cap non-consuming

No-submit proof should not reserve live order cap. Add separate no-submit proof counters if needed.

### Step 6 — Add high-value failing tests

Add tests that fail on current PR332:

```text
test_main_edli_reactor_does_not_call_run_cycle
test_event_reactor_adapter_no_submit_path_does_not_import_or_call_run_cycle
test_market_discovery_uses_full_weather_discovery_with_slug_fallback
test_no_submit_receipt_must_be_EventBoundFinalIntentReceipt_not_summary_dict
test_no_submit_does_not_reserve_live_cap
test_edli_runtime_uses_EventBoundDecisionEngine
test_exec_freshness_recapture_not_regressed_for_existing_runtime
```

## 9. Final recommendation

PR332 should not be merged in current form.

It is much better than PR328, and it is no longer an immediate “will place wrong live orders” risk because real order submission is disabled. But it still fails the core redemption requirement:

```text
EDLI runtime must be event-bound proof kernel first,
not event-triggered old cycle with no-submit summary receipt.
```

The two most dangerous non-EDLI regressions are:

1. market_discovery changed to slug-pattern-only;
2. executable snapshot fresh-at-submit recapture appears removed.

Those alone are enough to block merge because they affect existing live runtime outside EDLI.

Merge status: NO-GO.
Correct next action: split. Keep the schema/event-store/candidate-binding pure pieces, but make a smaller PR that removes runtime run_cycle dependency and reverts market-discovery/exec-freshness regressions before any daemon-online claim.
