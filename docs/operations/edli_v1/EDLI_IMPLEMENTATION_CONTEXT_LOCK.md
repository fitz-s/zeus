# EDLI v1 Implementation Context Lock

Created: 2026-05-24
Worktree: `/Users/leofitz/.openclaw/workspace-venus/.codex-worktrees/edli-v1-implementation`
Branch: `codex/edli-v1-implementation`

## Context Anchor

- Reference spec: `docs/operations/edli_v1/REFERENCE_event_sourced_opportunity.md`
- Original source: `/Users/leofitz/Downloads/event-sourced opportunity.md`
- SHA-256: `1b21f43708666a4e1714ea97128ffeeed05cde43a9a624a6c0f763ec270c8d11`

## Authority Order

1. Venue/exchange/official API docs.
2. Real repo code that runs live money path.
3. Repo schema / DB ownership / tests / CI.
4. Repo docs / AGENTS / architecture specs.
5. `docs/operations/edli_v1/REFERENCE_event_sourced_opportunity.md`.
6. Inferred intent.

## Prompt Acceptance Contract

Final online state:

- EDLI event writer enabled.
- Forecast snapshot trigger enabled.
- Day0 extreme trigger enabled.
- Market channel ingestor enabled.
- Reactor mode live for EDLI-approved opportunities.
- Day0 hard fact live enabled.
- Complete forecast snapshot live enabled only if existing source/FDR/Kelly/RiskGuard/execution gates pass.
- Public market channel online as market-data ingestion, quote cache, stale-book evidence, and feasibility evidence.
- Public market-channel events are never fill truth.
- Stale-book directional trading disabled because it is outside EDLI v1 scope.
- Taker FOK/FAK live disabled unless execution law is explicitly changed and tested.
- No production module named `shadow_*`.
- Production market channel module is `market_channel_ingestor.py`.
- Evidence table is `execution_feasibility_evidence`.

Acceptance IDs A01-A40:

| ID | Contract |
| --- | --- |
| A01 | `opportunity_events` rows are immutable append-only. |
| A02 | Mutable processing state is in `opportunity_event_processing`. |
| A03 | Every event has deterministic `event_id`, `payload_hash`, `idempotency_key`. |
| A04 | Duplicate `idempotency_key` cannot double-count FDR family. |
| A05 | `observed_at`, `available_at`, `received_at` are separate fields. |
| A06 | No event enters inference if `available_at > decision_time`. |
| A07 | Live forecast decision requires `causal_snapshot_id`. |
| A08 | Forecast live eligibility reuses executable forecast reader / bundle evidence. |
| A09 | COMPLETE forecast snapshot can be live eligible. |
| A10 | PARTIAL_ALLOWED is online evidence/no-trade, not live trade. |
| A11 | PARTIAL_BLOCKED is no-trade. |
| A12 | Day0 hard fact requires source/station/local-date/DST/rounding/metric match. |
| A13 | Day0 source mismatch blocks positive TradeScore. |
| A14 | Absorbing boundary uses `SettlementSemantics`, not Python round. |
| A15 | Orderbook events cannot change `q_live`. |
| A16 | Market-channel ingestor is online after daemon reboot. |
| A17 | Public market channel cannot prove fill. |
| A18 | User channel/reconcile is only fill-state authority. |
| A19 | No midpoint/displayed probability/last_trade_price is executable cost. |
| A20 | Buy YES uses native YES ask/depth. |
| A21 | Buy NO uses native NO ask/depth. |
| A22 | Sell uses held token bid/depth. |
| A23 | `c_no = 1 - yes_price` is forbidden. |
| A24 | Fee/tick/min-order/negRisk come from `ExecutableMarketSnapshotV2`. |
| A25 | Kelly receives typed fee-adjusted `ExecutionPrice` only. |
| A26 | Accepted/resting/matched/partial/cancel remainder/timeout UNKNOWN are distinct. |
| A27 | Reactor never imports or calls venue adapter directly. |
| A28 | FDR logs full sibling family once per event family. |
| A29 | RiskGuard remains mandatory. |
| A30 | Every event-triggered rejection writes `no_trade_regret_events`. |
| A31 | Later outcome fields are unavailable to live inference. |
| A32 | Scheduler maintenance jobs remain intact. |
| A33 | Rollback is feature flags off + no reactor; old scheduler path intact. |
| A34 | Tiny live cap applies to Day0 hard fact live trades. |
| A35 | Taker FOK/FAK live remains off unless execution-law packet approves it. |
| A36 | All new tables are in `db_table_ownership.yaml`. |
| A37 | All new money-path objects are in `money_path_objects.yaml` / `money_path_ci.yaml`. |
| A38 | Wrong-DB regression test passes. |
| A39 | No production module remains named `shadow_*`. |
| A40 | Final config enables EDLI online components after daemon reboot. |

## Current Unknowns

- Daemon restart was intentionally not executed from Codex. The runbook records the discovered launchd workflow and requires operator verification before reboot.
- Live market-channel websocket connectivity and live venue exercise were not performed here; public market channel is wired as data/quote/evidence only and must be smoke-tested after operator restart.
- The isolated worktree does not contain `state/zeus_trades.db`; replay correctness passes when pointed at the canonical main-workspace trade DB with a worktree-local baseline.
- Latest third-party critic re-review returned NO-MERGE / NO-REBOOT with three P0s:
  EDLI p_live ordering after FDR/Kelly/Risk, Day0 live hook not writing through
  a world event writer, and EDLI config false not preventing taker FOK upgrade.
  This round addresses those three P0s; no completion claim is made.

## Current Phase

Phase: PR328 draft is open and implementation is in final verification. The event path is now event-bound through existing evaluator/cycle_runtime authority: event context filters market/candidate scope, forecast decisions require matching `causal_snapshot_id`, live inference writes `p_live` onto the same decision, native executable cost and robust TradeScore are computed from final-intent executable cost basis, durable full-family FDR proof is asserted and committed before executor entry, Kelly proof requires typed fee-deducted `ExecutionPrice` with matching cost-basis id, RiskGuard remains mandatory, and the existing final-intent/executor path remains the only side-effect path.

Current blocking audit reference:

- `docs/operations/edli_v1/PR328_DEEP_SEMANTIC_WIRING_REVIEW.md`
- Original verdict: DO NOT MERGE / DO NOT REBOOT DAEMON ON PR328 as reviewed.
- Current status against that audit: the cited P0 chain gaps have been replaced with event-bound proof plumbing in `src/engine/cycle_runtime.py`, `src/engine/event_reactor_adapter.py`, and `src/events/reactor.py`. The branch remains draft until the latest critic re-review returns and daemon restart/live websocket smoke are handled by an operator.
- Repaired P0/P1 subset: no-op FDR/Kelly removed from main wiring; executable snapshot gate and submit receipt are event-bound; forecast p_live no longer double-applies LLR; durable FDR proof is committed before executor entry; Kelly receipt requires matching cost-basis id; Day0 live events originate from `Day0ObservationContext`; authority-table scanning is evidence/catch-up; market-channel token metadata comes from executable snapshots and carries tick/min-order/negRisk; schema version CHECK ranges now accept `SCHEMA_VERSION=41`.

Completed files:

- `docs/operations/edli_v1/REFERENCE_event_sourced_opportunity.md`
- `docs/operations/edli_v1/EDLI_IMPLEMENTATION_CONTEXT_LOCK.md`
- `docs/operations/edli_v1/REPO_REALITY_CROSS_REFERENCE.md`
- `src/events/AGENTS.md`
- `src/events/__init__.py`
- `src/events/idempotency.py`
- `src/events/opportunity_event.py`
- `src/events/event_store.py`
- `src/state/schema/opportunity_events_schema.py`
- `src/state/schema/opportunity_event_processing_schema.py`
- `src/state/schema/event_dead_letters_schema.py`
- `src/state/db.py`
- `src/state/schema/no_trade_events_schema.py`
- `architecture/db_table_ownership.yaml`
- `architecture/money_path_objects.yaml`
- `architecture/money_path_ci.yaml`
- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`
- `architecture/test_topology.yaml`
- `workspace_map.md`
- `tests/events/test_opportunity_event.py`
- `tests/events/test_event_store_idempotency.py`
- `tests/state/test_edli_table_ownership.py`
- `src/events/event_writer.py`
- `src/events/event_coalescer.py`
- `src/events/dead_letter.py`
- `src/events/replay.py`
- `src/events/triggers/forecast_snapshot_ready.py`
- `src/events/triggers/day0_extreme_updated.py`
- `src/events/triggers/market_channel_ingestor.py`
- `src/strategy/live_inference/AGENTS.md`
- `src/strategy/live_inference/__init__.py`
- `src/strategy/live_inference/absorbing_boundary.py`
- `src/strategy/live_inference/state.py`
- `src/strategy/live_inference/markov_smoothing.py`
- `src/strategy/live_inference/bayesian_factors.py`
- `src/strategy/live_inference/executable_cost.py`
- `src/strategy/live_inference/trade_score.py`
- `src/events/reactor.py`
- `src/engine/event_reactor_adapter.py`
- `src/state/schema/execution_feasibility_evidence_schema.py`
- `src/state/schema/no_trade_regret_events_schema.py`
- `src/state/schema/edli_live_cap_usage_schema.py`
- `src/strategy/live_inference/no_trade_regret.py`
- `src/strategy/live_inference/promotion_ledger.py`
- `src/analysis/event_opportunity_report.py`
- `src/analysis/day0_boundary_report.py`
- `src/analysis/forecast_release_reaction_report.py`
- `src/analysis/orderbook_execution_feasibility_report.py`
- `config/settings.json`
- `src/main.py`
- `docs/operations/edli_v1/EDLI_DAEMON_REBOOT_RUNBOOK.md`
- `docs/operations/edli_v1/CRITIC_ROUND_1_MATH_TIME_REVIEW.md`
- `docs/operations/edli_v1/CRITIC_ROUND_2_SPEC_WIRING_REVIEW.md`
- `tests/events/test_event_writer_single_writer.py`
- `tests/events/test_market_event_coalescer.py`
- `tests/events/test_forecast_snapshot_ready.py`
- `tests/events/test_day0_extreme_updated_trigger.py`
- `tests/events/test_market_channel_ingestor.py`
- `tests/events/test_reactor.py`
- `tests/strategy/live_inference/test_day0_absorbing_boundary.py`
- `tests/strategy/live_inference/test_live_bin_inference.py`
- `tests/strategy/live_inference/test_executable_cost.py`
- `tests/strategy/live_inference/test_trade_score.py`
- `tests/strategy/live_inference/test_no_trade_regret.py`
- `tests/engine/test_event_reactor_no_bypass.py`
- `tests/analysis/test_event_opportunity_report.py`
- `tests/money_path/test_edli_online_invariants.py`
- `architecture/test_quality.yaml`

Cut 1 verification:

- `python -m pytest -q tests/events/test_opportunity_event.py tests/events/test_event_store_idempotency.py tests/state/test_edli_table_ownership.py --maxfail=5` -> 18 passed.
- `python scripts/check_schema_version.py --write-pin` updated `tests/state/_schema_pinned_hash.txt` for SCHEMA_VERSION 36.
- The CI-style `--timeout=300` flag was attempted and failed locally because pytest-timeout is not installed in this environment.

Follow-up implementation segment after REVIEW_REQUIRED audit:

- Added event dead-letter and deterministic replay modules.
- Added forecast committed-row catch-up scanner and a default live-eligibility adapter that delegates to `read_executable_forecast_snapshot()`.
- Added Day0 authority-row catch-up scanner over `settlement_day_observation_authority`.
- Added market-channel quote cache, REST seed/reconnect service shell, active weather token discovery from `executable_market_snapshots`, and evidence-only conversion rows.
- Added `ExecutableMarketSnapshotV2` to native YES/NO quote-book conversion so fee/tick/min-order/negRisk can come from executable snapshots.
- Added reactor Day0 hard-fact status blocking before trade scoring, dead-letter-on-exception, and optional `NoTradeRegretLedger` writes for every rejection.
- Added daemon scheduler job `edli_market_channel_ingestor`; it discovers active weather tokens, starts a daemon websocket thread, REST-seeds books through `PolymarketClient.get_orderbook_snapshot()`, and records market-channel data as quote/evidence only.

Final wiring segment:

- `src/main.py::_edli_event_reactor_cycle` now runs forecast snapshot catch-up and Day0 extreme catch-up before processing pending EDLI events.
- `src/main.py::_edli_event_reactor_cycle` supplies real gates for source truth, executable snapshot freshness, RiskGuard GREEN status, and the existing cycle-runner submit adapter.
- `src/engine/event_reactor_adapter.py::submit_existing_cycle_for_event()` maps EDLI forecast events to `DiscoveryMode.UPDATE_REACTION` and Day0 events to `DiscoveryMode.DAY0_CAPTURE`; final-intent and executor side effects remain inside the existing cycle path.
- `src/main.py::_edli_market_channel_ingestor_cycle` starts the online public market-channel service in a background daemon thread and restarts it on later scheduler ticks if it dies.
- `src/events/triggers/market_channel_ingestor.py` now has the production websocket loop for `wss://ws-subscriptions-clob.polymarket.com/ws/market` with public REST seed/reconnect and no fill truth writes.
- `src/events/triggers/day0_extreme_updated.py` accepts a per-observation `SettlementSemantics` resolver and skips incomplete authority rows instead of killing the whole scanner.

Final verification snapshot:

- `python scripts/check_schema_version.py` -> PASS, hash `25df4bf556bb00b84ea40d35663f5e7a407b6231ba9a8d2b4fcc0ee61b80b229`, `SCHEMA_VERSION=39`.
- `python scripts/check_table_registry_coherence.py` -> PASS.
- `python scripts/ci/assert_test_quality.py` -> PASS.
- `python -m pytest -q tests/state/test_edli_table_ownership.py tests/events tests/strategy/live_inference tests/engine/test_event_reactor_no_bypass.py tests/analysis/test_event_opportunity_report.py --maxfail=8` -> PASS, 92 passed.
- `python -m pytest -q tests/money_path --maxfail=5` -> PASS, 13 passed.
- `python -m pytest -q tests/state/test_table_registry_coherence.py -k "a1_world_side_bidirectional or a1_forecasts_side_bidirectional or a4_raises_on_missing_table" --maxfail=3` -> PASS, 3 passed.
- `python3 scripts/replay_correctness_gate.py` -> REVIEW_REQUIRED, missing isolated-worktree `state/zeus_trades.db`.
- `python3 scripts/replay_correctness_gate.py --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db --bootstrap && python3 scripts/replay_correctness_gate.py --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db` -> PASS, 12,797 deterministic events, projection hash `5c1a1cb0075c157109941f7ff748acc3617b4b116d6bd9d56968fd5c121127e8`.
- Follow-up focused EDLI slice: `python -m pytest -q tests/state/test_edli_table_ownership.py tests/events tests/strategy/live_inference tests/engine/test_event_reactor_no_bypass.py tests/analysis/test_event_opportunity_report.py tests/money_path/test_edli_online_invariants.py --maxfail=8` -> PASS, 105 passed.
- Final wiring focused slice: `python -m pytest -q tests/engine/test_event_reactor_no_bypass.py tests/events/test_market_channel_ingestor.py tests/events/test_day0_extreme_updated_trigger.py tests/money_path/test_edli_online_invariants.py tests/state/test_table_registry_coherence.py --maxfail=5` -> PASS, 54 passed.
- Final wiring regression slice: `python -m pytest -q tests/engine/test_event_reactor_no_bypass.py tests/money_path/test_edli_online_invariants.py tests/state/test_table_registry_coherence.py --maxfail=3` -> PASS, 35 passed.
- Final required EDLI command after critic fixes: `python -m pytest -q tests/state/test_edli_table_ownership.py tests/events tests/strategy/live_inference tests/engine/test_event_reactor_no_bypass.py tests/analysis/test_event_opportunity_report.py --maxfail=8` -> PASS, 119 passed.
- Final required money-path command after final wiring: `python -m pytest -q tests/money_path --maxfail=5` -> PASS, 14 passed.
- Final online invariant command after final wiring: `python -m pytest -q tests/money_path/test_edli_online_invariants.py --maxfail=1` -> PASS, 4 passed.
- Final gates after final wiring: `python scripts/check_schema_version.py && python scripts/check_table_registry_coherence.py && python scripts/ci/assert_test_quality.py` -> PASS.
- Final replay gate after final wiring: `python3 scripts/replay_correctness_gate.py` -> REVIEW_REQUIRED, missing isolated-worktree `state/zeus_trades.db`.
- Final replay gate using canonical trade DB after critic fixes: `python3 scripts/replay_correctness_gate.py --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db --bootstrap && python3 scripts/replay_correctness_gate.py --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db` -> PASS, baseline `evidence/replay_baseline/2026-05-24.json`, ritual signal `logs/ritual_signal/2026-05.jsonl`, projection hash `5c1a1cb0075c157109941f7ff748acc3617b4b116d6bd9d56968fd5c121127e8`.

Critic review:

- Round 1 math/time critic found P1/P2 issues in `received_at` causality, catch-up starvation, SELL fee economics, stale processing claims, VWAP tick validation, and coarse executable snapshot gating. All but the intentionally coarse wake-up-gate concern are fixed; the remaining concern is documented as EDLI wake-up topology with existing cycle authority.
- Round 2 spec/wiring critic found P0 Day0 cap durability, P1 feasibility-evidence wiring, P1 catch-up progress, and P2 coarse event binding. P0/P1 items are fixed with durable `edli_live_cap_usage`, market-channel evidence writes, and newest-window scanner behavior.
- Final map-maintenance advisory after final wiring: `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory` -> PASS with companion warnings for `architecture/module_manifest.yaml`.
- Follow-up gates: `python scripts/check_schema_version.py && python scripts/check_table_registry_coherence.py && python scripts/ci/assert_test_quality.py` -> PASS.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory` -> PASS with advisory companion warnings for `architecture/module_manifest.yaml` companion surfaces.

Remaining REVIEW_REQUIRED:

- Daemon restart was not executed; runbook records launchd commands from existing repo docs and marks operator verification required.
- Plain `python3 scripts/replay_correctness_gate.py` remains REVIEW_REQUIRED in this isolated worktree because `state/zeus_trades.db` is absent; the same gate passes when pointed at the canonical main-workspace trade DB with a local baseline.
- Live market-channel websocket connectivity was not smoke-tested here; implementation uses current public endpoint and can be verified after daemon restart.

Daemon-online config status:

- `config/settings.json` contains final `edli_v1` online keys: event writer, forecast trigger, Day0 trigger, market-channel ingestor, quote cache, no-trade-regret, and reports enabled; stale-book directional trading and taker FOK/FAK live disabled.

## Hidden Branch Register

| Branch check | Prevention | Test / evidence | Observability signal | Status |
| --- | --- | --- | --- | --- |
| wrong DB write | EDLI tables world-owned; trade init excludes them. | `tests/state/test_edli_table_ownership.py` | wrong-DB write count | PASS |
| cross-DB FK illusion | EDLI schemas have no SQLite FKs. | `test_no_cross_db_fk` | schema report | PASS |
| event spam / DB lock | Market coalescer and quote-cache service coalesce noisy events. | `tests/events/test_market_event_coalescer.py` | `edli.market_channel.coalesced` | PASS |
| idempotency collision | Deterministic payload hash + idempotency key. | `tests/events/test_opportunity_event.py` | duplicate counter | PASS |
| event ordering race | Store orders priority, available_at, received_at, event_id. | `test_replay_order_deterministic` | replay report | PASS |
| clock skew / future availability | Fetch excludes future events; inference asserts availability. | `test_pending_fetch_excludes_future_available_at` | `edli.events.future_available_at_blocked` | PASS |
| forecast issue-time leakage | Forecast event uses source availability. | `test_available_at_is_source_available_not_issue_time` | available_at violations | PASS |
| partial ECMWF snapshot | Partial allowed is evidence/no-trade. | `test_partial_40_members_no_live_trade_evidence_only` | `edli.forecast.partial_evidence` | PASS |
| ECMWF cycle horizon drift | Cycle step sets differ for 00/12 vs 06/18. | `test_00z_12z_step_set_differs_from_06z_18z_after_cycle_50r1` | forecast blocked reasons | PASS |
| Day0 provider lag | Day0 event uses observation availability. | `test_day0_event_uses_observation_available_at` | Day0 source mismatch counter | PASS |
| DST/local date/station/metric/rounding mismatch | Day0 hard-fact payload gate blocks live. | Day0 boundary and reactor tests | Day0 blocked reasons | PASS |
| token map stale | Native quote requires executable snapshot token YES/NO depth. | `test_quote_book_from_executable_snapshot_uses_snapshot_fee_tick_min_order_negrisk` | native quote unavailable | PASS |
| fee/tick/min-order/negRisk change | Cost helper reads snapshot facts and validates tick/min/order/negRisk. | executable cost tests | cost violation report | PASS |
| market/user channel confusion | Public channel fill authority assertion fails closed. | market-channel tests | fill truth source | PASS |
| accepted vs filled / partial / cancel / timeout states | Existing venue command/user-channel authority remains source of truth. | Executor/command recovery tests; EDLI does not write fill truth. | venue command reports | PASS |
| maker cancel before submit / stale quote adverse selection | Feasibility table fields exist; public service records evidence only. | feasibility evidence tests | orderbook feasibility report | PASS |
| FDR sibling undercount | Reactor logs family once per event family and idempotency dedupes. | `test_sibling_family_logged_once` | duplicate FDR family count | PASS |
| Kelly float regression | EDLI executable cost returns typed `ExecutionPrice`. | executable cost / trade score tests | Kelly input type checks | PASS |
| RiskGuard bypass | Reactor requires injected RiskGuard gate before submit. | reactor no-bypass tests | RiskGuard rejection count | PASS |
| no-trade hindsight leakage | Live reader omits later outcome columns. | no-trade-regret tests | live-reader projection | PASS |
| shadow terminology leakage | No production `shadow_*` EDLI modules. | money-path invariant test | module scan | PASS |
| feature flag misconfiguration / live cap bypass | Config online; stale-book and FOK/FAK off; Day0 tiny cap tested. | money-path + reactor tests | live cap counter | PASS |
| topology drift / CI blind spot | Registries updated and test-quality gate run. | schema/table/test-quality checks; map-maintenance advisory | topology doctor output | PASS |

## Task List Anchor

This implementation task list is governed by this file and the copied reference spec:

1. Schema + event skeleton.
2. EventWriter + coalescer.
3. ForecastSnapshotReadyTrigger.
4. Day0ExtremeUpdatedTrigger + absorbing boundary.
5. Live inference pure functions.
6. Native executable cost + robust TradeScore.
7. Online market channel ingestor.
8. Reactor online integration.
9. NoTradeRegretLedger + reports.
10. Online config + daemon reboot readiness.

## 2026-05-24 Post-PR328 Semantic Wiring Completion Segment

Purpose: close the user-provided PR328 semantic audit findings by replacing
the earlier event-triggered cron-cycle shell with event-bound proof plumbing.

Implemented in this segment:

- Reactor submit acceptance is now receipt-bound. A submitted receipt must
  match the event id, causal snapshot id, optional condition/token/executable
  snapshot ids, and must carry positive TradeScore, full-family FDR proof,
  typed fee-deducted Kelly proof, and final intent id.
- `submit_existing_cycle_for_event()` now passes `edli_event_context` into
  `run_cycle()` and converts only event-bound EDLI summary proof fields into an
  `EventSubmissionReceipt`.
- `run_cycle()` / `execute_discovery_phase()` now accept EDLI event context.
  The runtime filters markets by event city/target_date/metric and optional
  condition/token, filters forecast decisions by `decision_snapshot_id ==
  causal_snapshot_id`, computes robust TradeScore from final execution price,
  and stamps FDR/Kelly/final-intent proof back into the summary.
- Day0 live event emission now has a live observation hook from the actual
  settlement-bound `Day0ObservationContext`; the old
  `settlement_day_observation_authority` scanner remains catch-up/evidence and
  defaults to `OBSERVABILITY_ONLY`.
- Market-channel tick/resolve actions now call executable snapshot refresh,
  not only logging; market-channel `new_market` no longer defaults unmapped
  tokens to YES.
- NoTradeRegret live insert now rejects `later_outcome` / `would_have_*`; those
  fields can only be added through `enrich_after_settlement()` with a
  settlement proof.
- EDLI primary keys are explicit `TEXT NOT NULL PRIMARY KEY`; registry
  nullability was corrected. `SCHEMA_VERSION=41` and pinned hash
  `33c023760faa566ead9aefbd515af4d70e990b00198aa4c2027ffdcaecf52d0a`.

Fresh verification after this segment:

- `python -m pytest -q tests/state/test_edli_table_ownership.py tests/events tests/strategy/live_inference tests/engine/test_event_reactor_no_bypass.py tests/events/test_reactor.py tests/events/test_market_channel_ingestor.py tests/events/test_day0_extreme_updated_trigger.py --maxfail=8` -> PASS, 139 passed.
- `python -m pytest -q tests/money_path --maxfail=5` -> PASS, 14 passed.
- `python scripts/check_schema_version.py` -> PASS, `SCHEMA_VERSION=41`.
- `python scripts/ci/assert_test_quality.py` -> PASS.
- `python -m pytest -q tests/test_live_release_gate.py tests/test_live_release_registry_runtime_assertions.py --maxfail=5` -> PASS, 14 passed.
- `python scripts/check_live_release_gate.py --self-test-fixture --json` -> PASS, 9/9 gates.
- `python scripts/ci/semantic_diff_classifier.py --base origin/main --head HEAD --fail-on-unregistered --json-output /tmp/edli_semantic_diff.json` -> PASS, unregistered objects empty.
- `python -m pytest -q tests/test_money_path_semantic_ci.py tests/money_path/test_001_negrisk_tradeability_snapshot_submit.py tests/money_path/test_004_schema_live_failclosed.py tests/analysis/test_event_opportunity_report.py tests/money_path/test_edli_online_invariants.py tests/state/test_schema_current_invariant.py tests/test_execution_price.py tests/test_executor_command_split.py tests/test_command_recovery.py --maxfail=8` -> PASS, 174 passed, 1 xfailed.

Still not executed:

- Daemon restart.
- Live websocket smoke.
- Plain `python3 scripts/replay_correctness_gate.py` in this isolated worktree;
  the worktree has no `state/zeus_trades.db`.

## 2026-05-24 Final Single-Application / Durability Segment

Purpose: close the final critic findings that remained after the first semantic
rewire pass.

Implemented in this segment:

- Forecast family p_live now carries the evaluator posterior from the causal
  snapshot exactly once. `FORECAST_SNAPSHOT_READY` family application proves
  `COMPLETE` and matching `causal_snapshot_id`, but it does not reapply the
  same forecast innovation as another LLR.
- Forecast buy-NO p_live preserves native selected-side probability by storing
  family state in YES-space and complementing only for NO selected-side
  scoring.
- Durable full-family FDR proof is asserted and `conn.commit()` is called before
  executor entry, so the executor path cannot proceed on uncommitted
  selection-family evidence.
- `EventSubmissionReceipt` and reactor money-path proof now require
  `kelly_cost_basis_id`; `_stamp_edli_submit_summary()` only reports
  `edli_kelly_pass=True` when `decision.edli_kelly_cost_basis_id ==
  final_intent.cost_basis_id != ""`.

Fresh verification after this segment:

- `python -m pytest -q tests/engine/test_event_reactor_no_bypass.py --maxfail=8`
  -> PASS, 34 passed.
- `python -m pytest -q tests/events/test_reactor.py --maxfail=8` -> PASS,
  11 passed.
- `python -m pytest -q tests/state/test_edli_table_ownership.py tests/events
  tests/strategy/live_inference tests/engine/test_event_reactor_no_bypass.py
  tests/analysis/test_event_opportunity_report.py --maxfail=8` -> PASS,
  159 passed.
- `python -m pytest -q tests/money_path --maxfail=5` -> PASS, 14 passed.
- `python scripts/check_schema_version.py` -> PASS, `SCHEMA_VERSION=41`, hash
  `33c023760faa566ead9aefbd515af4d70e990b00198aa4c2027ffdcaecf52d0a`.
- `python scripts/ci/assert_test_quality.py` -> PASS.
- `python scripts/ci/semantic_diff_classifier.py --base origin/main --head HEAD
  --fail-on-unregistered --json-output /tmp/edli_semantic_diff_final3.json`
  -> PASS, no unregistered objects.
- `python scripts/check_live_release_gate.py --self-test-fixture --json` -> PASS,
  9/9 gates.
- `python -m pytest -q tests/test_money_path_semantic_ci.py
  tests/money_path/test_001_negrisk_tradeability_snapshot_submit.py
  tests/money_path/test_004_schema_live_failclosed.py
  tests/analysis/test_event_opportunity_report.py
  tests/money_path/test_edli_online_invariants.py
  tests/state/test_schema_current_invariant.py tests/test_execution_price.py
  tests/test_executor_command_split.py tests/test_command_recovery.py
  --maxfail=8` -> PASS, 174 passed, 1 xfailed.
- `python3 scripts/replay_correctness_gate.py --db
  /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db
  --baseline-date 2026-05-24` -> PASS, projection hash
  `bffe8e7732ca27c2dd6c1908d1300e077ddbcc6d7dd63c9fe79d80e4a191ae93`.

Still not executed:

- Daemon restart.
- Live websocket smoke.
- Live venue exercise.

## 2026-05-24 Critic P0 Round Closure

Third-party critic finding:

- P0: EDLI p_live was applied after the evaluator had already recorded FDR and
  done Kelly/Risk work.
- P0: Day0 direct live hook checked the trade-main connection for
  `opportunity_events`, so it skipped the real live observation path and left
  only the observability-table scanner.
- P0: EDLI config had `taker_fok_fak_live_enabled=false`, but the final-intent
  context still allowed taker upgrade.

Implemented in this round:

- `src/engine/evaluator.py::_apply_edli_live_family_before_selection()` applies
  EDLI event-time family probabilities before `find_edges()`,
  `scan_full_hypothesis_family()`, durable selection-family writes, Kelly,
  RiskGuard, and final intent. Day0 hard facts override the family distribution
  and deterministic bootstrap p-values before full-family FDR.
- Durable `selection_family_fact.meta_json` and selected
  `selection_hypothesis_fact.meta_json` now carry the EDLI p_live family hash.
  `src/engine/cycle_runtime.py::_edli_durable_fdr_proof()` rejects a selected
  EDLI decision if the durable FDR family was not computed from the same p_live
  family hash.
- `src/engine/cycle_runtime.py::_queue_edli_day0_observation_event()` now opens
  a real world DB connection and writes through `EventWriter(world_conn)`,
  instead of trying to use the trade-main cycle connection.
- `src/engine/event_reactor_adapter.py::submit_existing_cycle_for_event()` now
  threads `taker_fok_fak_live_enabled` into `edli_event_context`; `src/main.py`
  passes the configured false value; `src/engine/cycle_runtime.py` sets
  `allow_taker_upgrade` from that EDLI flag and prevents marketable/taker
  selection when false.

Fresh verification after this round:

- `python -m py_compile src/engine/evaluator.py src/engine/cycle_runtime.py
  src/engine/event_reactor_adapter.py src/main.py` -> PASS.
- `python -m pytest -q tests/engine/test_event_reactor_no_bypass.py
  tests/events/test_day0_extreme_updated_trigger.py --maxfail=8` -> PASS,
  49 passed.
- `python -m pytest -q tests/state/test_edli_table_ownership.py tests/events
  tests/strategy/live_inference tests/engine/test_event_reactor_no_bypass.py
  tests/analysis/test_event_opportunity_report.py --maxfail=8` -> PASS,
  164 passed.
- `python -m pytest -q tests/money_path --maxfail=5` -> PASS, 14 passed.
- `python scripts/check_schema_version.py && python
  scripts/ci/assert_test_quality.py` -> PASS.
- `python scripts/ci/semantic_diff_classifier.py --base origin/main --head HEAD
  --fail-on-unregistered --json-output /tmp/edli_semantic_diff_final4.json`
  -> PASS, no unregistered objects.
- `python scripts/check_live_release_gate.py --self-test-fixture --json` -> PASS,
  9/9 gates.
- `python3 scripts/replay_correctness_gate.py --db
  /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db
  --bootstrap && python3 scripts/replay_correctness_gate.py --db
  /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db
  --baseline-date 2026-05-24` -> PASS immediately after baseline refresh,
  projection hash
  `4ec6cb81e24a45687a7ca49007fa36d19bec691e663141ab2d836598d1f3257c`.

Status after this round:

- Branch is still not declared complete.
- Do not reboot daemon.
- Do not merge until a new critic pass reviews this round.
