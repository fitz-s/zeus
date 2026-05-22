# Zeus Live Short-Term Recovery Workflow

## 1. Current original goal
Live must continuously refresh source/forecast/settlement/evaluator/sizing/venue/reconcile/redeem and produce verifiable real trading profit; process liveness, one green healthcheck, one order/fill, or a merged PR is not completion.

## 2. Current full-analysis references
- Analysis reference A: endpoint contracts are fragmented across tradability, book semantics, family selection, passive maker authority, order truth, family exposure, and business-plane health. Do not fix isolated symptoms without identifying which authority boundary failed.
- Analysis reference B: the latest probe superseded the ask-only/sub-$1 hypothesis for the newest cycle: after 20:58Z snapshots were two-sided, no-trade was only `MODEL_CONFLICT`, and latest `day0_capture` had `candidates=0`. Current no-intent evidence is upstream of `FinalExecutionIntent` unless a later cycle proves otherwise.
- Analysis reference C: the newest read-only probe superseded both downstream hypotheses for the active cycle: code-plane/process-plane/health passed, `day0_capture` reached evaluator with `candidates=1`, but `edges_found=0` and `edges_after_fdr=0`. The active blocker is math/signal frontier: Day0 p_raw is observation-fused, while the generic bootstrap CI had been sampling remaining extrema as a different probability object.
- Analysis reference D: post-PR live evidence moved the active opening_hunt blocker to evaluator source/math/strategy frontier: candidates reached evaluator and were rejected by `MODEL_CONFLICT` / `ULTRA_LOW_PRICE_NOT_AUTHORIZED`, so family/final-intent/submit was not reached.
- Analysis reference E: `MODEL_CONFLICT` must not be a bare hard-kill label. It needs source-run/issue/valid-window comparability proof, JSD/mode/physical-temperature evidence, and a single conflict authority; ultra-low rejects must decompose normal-price vs tail/prohibited penny edge instead of implying execution failure.

## 3. Current short-term implementation target
- Add a Money Path Frontier Report for scheduler, market, candidate, math, family, execution, and submit boundaries.
- Add per-mode scheduler liveness so latest `status_summary` cannot hide whether `opening_hunt`, `imminent_open_capture`, `update_reaction`, or `day0_capture` progressed or skipped.
- Preserve the decision-events ghost-table boot fix: interrupted `decision_events_new` rebuild artifacts must not wedge schema registry when `decision_events` is already current.
- Treat already-implemented full-analysis contracts as proof obligations, not assumptions: active=false negRisk capture, ask-only BUY semantics, passive maker authority, fill-adjusted passive EV, command-only family exposure, canonical order truth reducer, no runtime no-trade schema rebuild.

## 4. Required terminal classes
- A: no market for this mode.
- B: market filter bug.
- C: signal conflict.
- D: family exposure block.
- E: execution viability failure.
- F: submit or recovery frontier.

## 5. Full-analysis contract proof map
- TradabilityAuthority / active=false: covered by `tests/test_executable_market_snapshot_v2.py` and `tests/test_market_scanner_negrisk.py`; current code uses acceptingOrders + enableOrderBook + CLOB archived/orderbook, not child active.
- EntryBookSemantics / ask-only BUY: covered by `tests/test_runtime_guards.py::{test_buy_entry_ask_only_snapshot_reprices_without_bid_midpoint,test_ask_only_book_never_builds_passive_maker_vwmp_intent}`.
- PassiveMakerAuthority / fill-adjusted EV: covered by `tests/test_executable_market_snapshot_v2.py` and `tests/test_runtime_guards.py::{test_passive_economic_floor_uses_fill_adjusted_expected_profit,test_passive_economic_floor_passes_positive_fill_adjusted_net_ev}`.
- FamilySelectionAuthority / command-only exposure: covered by `tests/test_inv_family_exclusive_sizing.py::{test_trade_db_family_exposure_blocks_command_without_position_projection,test_weather_family_exposure_resolver_merges_trade_truth_and_portfolio_projection}`.
- CanonicalOrderTruth: covered by `tests/test_command_recovery.py` partial-entry/exit weaker-fact tests and `tests/test_exchange_reconcile.py::test_local_order_open_uses_canonical_order_truth_over_later_weaker_fact`.
- Runtime schema safety: covered by `tests/test_decision_seq_cross_table_no_collision.py::TestDecisionSeqCrossTableNoCollision::test_runtime_ensure_table_does_not_rebuild_stale_no_trade_schema` plus the new decision-events ghost cleanup test.

## 6. What not to do
- Do not treat `task.md`, a PR merge, one health green, one order, or one fill as live completion.
- Do not create more DB backups for this repair; the operator instructed use of the unique live DB for the already-needed schema cleanup.
- Do not lower economic floors or execution gates without frontier evidence proving the gate is the real blocker.
- Do not continue hopping across oracle, partial fill, exit snapshot, family dedup, and status without updating this file.

## 7. Verification before PR
- Focused tests for schema ghost cleanup, market/candidate frontier classification, final-intent frontier counters, and per-mode scheduler liveness.
- Full-analysis proof tests listed above.
- `git diff --check`.
- Planning-lock check for changed high-risk files.

## 8. Current implementation status
- Money Path Frontier Report and per-mode scheduler business liveness: implemented on main before this branch; this branch adds healthcheck consumption of per-mode scheduler business liveness.
- FamilySelectionAuthority live `buy_no` fallback slots: implemented in this branch; live-disabled `buy_no` legs are diagnostic drops and no longer consume ranked executable fallback slots when live `buy_yes` candidates exist.
- CanonicalOrderTruth closure gaps: implemented in this branch for terminal-remainder helper, review-required confirmed-fill clearance, review-clearance DB predicates, and exchange reconcile entry fill coverage.
- PR #283 review repair: all-blocked live `buy_no` family selection no longer self-drops selected legs, evaluator now imports the native `buy_no` flag authority from family selection instead of owning a duplicate copy, and exchange reconcile feeds the order reducer deterministic ordered facts.
- Current no-intent diagnosis remains: latest live evidence points mostly to pre-family math/strategy rejection (`ultra_low_price_not_authorized`, `model_conflict`, family dedup), not a proven downstream final-intent constructor failure.
- Post-merge live baseline is aligned on `22dba733499a92effc37b5474cde94416a4d05a7`; latest health probes are green with entry `requires_intent`, not blocked by code-plane drift or manual gates.
- Latest observed `day0_capture` frontier at `2026-05-22T00:58:29Z`: substrate events 3 -> phase filter 1 -> candidate objects 1 -> evaluator decisions 1 -> `should_trade_true_before_family=0` -> terminal `math_rejected_before_family`; reason was uncategorized detail `0 edges found, 0 passed FDR`.
- Recent live no-trade mix still classifies the active blocker as pre-execution: `model_conflict`, `ultra_low_price_not_authorized`, `already_held_same_token`, and edge/FDR insufficiency dominate. There is no evidence from the latest cycles that snapshot/reprice/final-intent/submit is the current primary stop.
- Follow-up diagnostic branch `fix-live-frontier-diagnostics-20260522` adds the missing no-trade attribution handoff: rejected decisions now pass `strategy_key`, `event_source`, and `shadow_runtime` into `no_trade_events`, so future math/strategy frontier rows do not depend on parsing `reason_detail`.
- Follow-up diagnostic branch now implements the latest math-frontier cutover: `MarketAnalysis.find_edges_with_trace()` explains zero-edge outcomes per bin/direction, evaluator records the trace in legal no-trade detail, Day0 injects an observation-fused bootstrap probability sampler, and money-path frontier reports source writer observability staleness separately from source data freshness.
- Current branch target: implement the latest opening_hunt source/math/strategy authority cutover. `MODEL_CONFLICT` must persist evidence and first prove primary/crosscheck comparability; GFS crosscheck must use the same MC/noise/settlement probability-space as primary; physical temperature disagreement must drive hard conflict; ultra-low policy must distinguish non-tail penny edges from authorized tail topology; family frontier must not count pre-Kelly sibling drops as existing exposure.
- Subagent audit results recorded for this phase: strategy floor rejects are currently correct under registry (`min_entry_price=0.05`, `allow_ultra_low_tail=false`), and venue/reconcile/redeem has no unresolved side-effect command, unresolved reconcile finding, or Karachi redeem blocker.
- Current short-term target after latest analysis: freeze execution/final-intent hotfixes until evaluator semantics are repaired. `MODEL_CONFLICT` must be edge-level, not market-level pre-edge hard kill. `settlement_capture` must mean observation-locked truth; Jeddah 36C with canonical observed high 34C is Day0 observation-plus-remaining-forecast nowcast, not observed settlement capture. Do not lower the 5c floor until Day0 HIGH physical max semantics and strategy classification are proven.
- Current semantic split implementation: pre-edge global model conflict now remains soft until an edge support index exists; unsupported edge-level conflict still rejects. Day0 HIGH `p_vector` now uses physical `max(observed_high_so_far, remaining_high)` semantics. Day0 high edges above current observed high classify as `day0_nowcast_entry` with no live `strategy_key`. Weather family keys include `market_family_id` when available, while unknown historical exposure still blocks conservatively.

## 9. Current verification evidence
- `py_compile`: healthcheck, family exposure, command recovery, exchange reconcile, venue command repo, and focused tests passed.
- B1 focused relationship tests: 9 passed for canonical order/trade truth across command recovery and exchange reconcile.
- B2 family tests with local sklearn stub: 7 passed, including command-only exposure and live-disabled `buy_no` fallback exclusion.
- Full-analysis proof subset with local sklearn stub: 10 passed for active=false negRisk, ask-only BUY, passive EV, and no runtime no-trade schema rebuild.
- Fill-finality gate: command recovery + exchange reconcile full files passed with local `apscheduler`/`sklearn` stubs (`169 passed`).
- U2 ingest/projection gate: user channel ingest + provenance projections passed with local `apscheduler`/`sklearn` stubs (`83 passed`).
- Live-safety gate: `tests/test_live_safety_invariants.py` passed with local sklearn stub (`123 passed`).
- `tests/test_runtime_guards.py` no longer stalls on the chain-reconciliation test after adding a deterministic market-scanner stub, but the full-file local run currently fails 52 unrelated existing cases in this environment; targeted full-analysis runtime guard proof cases pass. Do not claim full runtime-guards gate until CI or a clean local runtime-guard environment proves it.
- Topology/planning-lock: admitted routes used for family exposure, fill finality ledger, state review-clearance predicates, and healthcheck; `task.md` remains a user-directed tracker outside topology admission.
- Follow-up diagnostic branch focused tests: `tests/test_runtime_guards.py::{test_no_trade_event_writer_preserves_decision_attribution,test_model_conflict_cycle_records_math_frontier_classification,test_math_no_trade_frontier_publishes_status_reason_proof}` passed with local sklearn stub (`3 passed`).
- Follow-up diagnostic branch syntax/checks: `python3 -m py_compile src/engine/cycle_runtime.py tests/test_runtime_guards.py` and `git diff --check` passed.
- Math-frontier cutover proof with local sklearn stub: `tests/test_market_analysis.py` passed (`49 passed`); runtime focused frontier tests passed (`7 passed`), and the combined relationship set including Day0 bootstrap/no-trade attribution/source-writer observability passed (`10 passed`).
- Evaluator semantic split proof: `tests/test_model_agreement.py tests/test_phase6_day0_split.py::TestRBA_HighPathMaxArray tests/test_evaluator_strategy_key_failclosed.py tests/test_market_analysis.py tests/test_inv_family_exclusive_sizing.py` passed (`117 passed`); `py_compile` for touched signal/evaluator/family files passed; `git diff --check` passed; planning-lock check passed.
