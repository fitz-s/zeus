# Live Redecision Repair Plan

Date: 2026-06-17
Status: implementing / live-only decoupling verified
Parent objective: `docs/operations/current/GOAL.md` / `current_live_recovery`
Scope: existing-position redecision and exit readiness, open-maker-entry continuous redecision after order ack, and live forecast-authority precision for new entry decisions where the current reactor consumes replacement posterior rows. Shadow artifacts may be diagnostic inputs only; they are not an acceptable endpoint for live decision authority.

## Compact Reentry Rule

After conversation compaction or session handoff, reread this file before taking action. Treat this plan as the current workflow source for this repair until it is superseded by a newer `docs/operations/current/plans/live_redecision_repair/PLAN.md` revision or closeout receipt.

Do not restart daemons, reload LaunchAgents, mutate live DB schema, submit orders, cancel orders, or alter operator controls without explicit operator approval in the current session. Public-repo external model consultation is allowed, but advisory output is not evidence until verified against repo source, active config, canonical DB rows, process state, and live receipts.

Latest operator approval on 2026-06-17 superseded the earlier read-only guard for this narrow repair: already-running live surfaces must not conflict with shadow-only wiring. Approved live side effects for this slice were limited to replacement forecast authority DB repair, renaming the replacement forecast runtime directory from `state/replacement_forecast_shadow` to `state/replacement_forecast_live`, and reloading `com.zeus.live-trading`, `com.zeus.forecast-live`, and `com.zeus.data-ingest`. No submit or cancel action was performed.

## Requirements Summary

The live system must continuously re-evaluate held positions before fill, during holding, through exit, and after settlement. The current target is not to force one order through. The target is to repair the systematic chain that allows a valid redecision to become a safe, evidence-backed exit action when the live market state justifies it.

Primary chain for this slice:

`monitor refresh -> probability/price freshness -> ExitContext -> Position.evaluate_exit -> execute_exit -> CTF collateral preflight -> execute_exit_order gates -> venue command/order receipt -> lifecycle/settlement follow-through`

Current read-only evidence gathered on 2026-06-17:

- `position_events` continues to receive `MONITOR_REFRESHED`; latest rows include fresh probability and market price for Houston/Tokyo/Seoul held positions.
- Some Day0 positions are correctly marked not-fresh with `BELIEF_AUTHORITY_FAULT`; this must remain a conservative hold/degraded-authority signal, not a panic-exit trigger.
- Historical `EXIT_ORDER_REJECTED` rows prove redecision did fire for `CI_SEPARATED_REVERSAL`, then failed on `ws_gap`, `reconcile_finding_threshold`, and repeated `collateral_snapshot_stale`.
- Current collateral snapshots are `CHAIN` authority and open-position CTF token balances/allowances cover shares; the current defect is stale snapshot usage on the exit path, not missing inventory.
- Current `execution_capability.exit` is blocked by `heartbeat_supervisor`, `ws_gap_guard`, and `risk_allocator_global`.
- Current healthcheck reports `POSITION_CURRENT_MONITOR_FRESHNESS_SCHEMA_DRIFT`, `LIVE_LAUNCHD_CONTRACT_DRIFT`, and `PROCESS_LOADED_CODE_STALE`.

Additional read-only evidence gathered after compact reentry on 2026-06-17:

- Live `venue_commands` currently has no open ENTRY orders; one recovered EXIT SELL remains `PARTIAL`. This means entry reprice proof must be source/systematic, not a one-order live observation.
- `state/status_summary.json` at `2026-06-17T10:01:49.997174+00:00` shows entry, exit, and cancel capability all at `requires_intent` with `blocked_components=[]`; live gates are not the current blocker.
- `src/engine/cycle_runtime.py::cleanup_stale_entry_orders` cancels no-fill ACKED ENTRY orders when a fresher book proves the order is no longer competitive, but it does not itself submit a replacement order.
- `src/main.py::_edli_event_reactor_cycle` continuously re-emits forecast-decision opportunities with a wrapping fair cursor, and `src/main.py::_edli_continuous_redecision_screen_cycle` screens cached belief x fresh executable prices plus open maker rests.
- `src/main.py::_maker_rest_escalation_cycle` cancels expired post-only maker rests and emits an escalation-origin `FORECAST_SNAPSHOT_READY` through the existing FSR machinery so the next reactor cycle re-certifies the family ahead of the round-robin backlog.
- `src/events/continuous_redecision.py::screen_resting_orders` claims to pull rests whose limit has fallen behind current best bid, but it uses `read_freshest_executable_prices()`, which returns buy-side executable ask costs. Comparing ask cost to maker bid can turn normal spread into false `BOOK_MOVED`.
- `src/main.py::_edli_open_maker_rests_for_screen` defaults every open rest to `buy_yes`; the comment says NO token checks are side-agnostic, but the price lookup is direction-specific. This can misread NO rests and is misleading source text.
- `src/engine/cycle_runtime.py::execute_monitoring_phase` builds fresh `ExitContext`, refreshes pending-exit quote from current CLOB, calls `Position.evaluate_exit`, and routes true exits through `execute_exit`. `src/strategy/portfolio_rotation.py` is pure math only; the live cycle may report `portfolio_rotation_evaluation_status` as read-side value evidence, but no cross-family rotation may be labeled live/actionable until an explicit sell-then-entry executor owns that handoff.
- `state/status_summary.json` at `2026-06-17T10:12:55.563464+00:00` shows the event reactor processed 3 candidates, rejected all 3, built 0 final intents, and made 0 submit attempts. The current reasons are `FDR_REJECTED`, `LIVE_INFERENCE_INPUTS_MISSING:FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:MISSING_EXPECTED_MEMBERS`, and `TRADE_SCORE_NON_POSITIVE`.
- The processed Milan and Tel Aviv forecast events carried forecast payloads marked `COMPLETE` / `LIVE_ELIGIBLE` with 51/51 members, but their `forecast_posteriors.trade_authority_status` rows were `SHADOW_ONLY`. The live reader nevertheless accepted them as `probability_authority="replacement_0_1"`.
- Milan regret evidence showed `direction=buy_yes`, `q_live=0.199009684818666`, and `q_lcb_5pct=0.7957045133438944`. The underlying posterior row for the same bin had `q_lcb=0.04625961651748593` and `q_ucb=0.19901301444522052`; the receipt-facing q-lower-bound was a NO/payoff-space value attached to a YES proof.
- `src/data/replacement_forecast_bundle_reader.py` claims a live bundle but filters `forecast_posteriors.trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY')`, and `ReplacementForecastPosteriorBundle.__post_init__` rejects non-shadow statuses. This is misleading executable source under the user’s live-only requirement.
- `src/engine/qkernel_spine_bridge.py::_overlay_spine_economics_onto_proof` overwrites `q_posterior` / `q_lcb_5pct` with spine payoff economics and claims it preserves `q_lcb <= q_point`. Current live Milan rows disprove that invariant.

## Decision

Repair in this order:

1. Prove and repair the durable monitor freshness read model.
2. Add exit-side synchronous collateral snapshot refresh before CTF sell preflight.
3. Repair/prove heartbeat, WS-gap, and risk allocator exit gates without bypassing them.
4. Preserve monitor probability authority boundaries; only repair posterior materialization/reseed if fresh-authority evidence remains absent.
5. Remove or rewrite misleading source comments that contradict executable behavior.
6. Repair open-maker-rest redecision math so confirmed entry rests are evaluated against the correct held-side/current-best-bid authority, with NO-token direction preserved.
7. Repair replacement forecast authority wiring so live entry decisions consume only row-level `LIVE_AUTHORITY` posterior bundles; shadow rows remain visible for diagnostics but cannot be converted into live probability authority by reader fallback.
8. Repair qkernel bridge receipt semantics so receipt-facing `q_posterior` and `q_lcb_5pct` remain selected-side probability fields and cannot be overwritten by payoff-space values that contradict the selected direction.
9. State the current boundary for post-fill hold/exit/shift: hold/exit is live; portfolio rotation/switch is shadow-only unless a later plan wires actuation through exit + entry with separate receipts.

This order separates decision truth from execution readiness and prevents an accidental one-order-only fix.

## Acceptance Criteria

1. Live `state/zeus_trades.db.position_current` contains `last_monitor_prob_is_fresh` and `last_monitor_market_price_is_fresh`.
2. `scripts/healthcheck.py --json` no longer reports `POSITION_CURRENT_MONITOR_FRESHNESS_SCHEMA_DRIFT`.
3. Exit sell preflight synchronously refreshes collateral truth before checking CTF inventory, while preserving fail-closed behavior for degraded, stale, failed-refresh, insufficient-token, or insufficient-allowance states.
4. No code path uses pUSD balance as proof for CTF sell inventory when a token id is available.
5. `execution_capability.exit` is no longer blocked by stale heartbeat/ws/risk causes before any live submit claim is made. If still blocked, the current blocker is reported with live evidence rather than bypassed.
6. `MONITOR_REFRESHED`, `EXIT_INTENT`, `EXIT_ORDER_REJECTED`, `EXIT_ORDER_FILLED`, and venue command rows are used as separate proof classes; no single green status summary is treated as completion.
7. Misleading text is fixed where it can cause future agents to misread behavior:
   - `src/control/ws_gap_guard.py` must distinguish exit evaluation/reconciliation from exit venue submission.
   - `src/engine/monitor_refresh.py` must not encode drift-prone live coverage facts as durable source comments.
8. Targeted tests pass for collateral refresh, exit safety, monitor freshness schema migration, heartbeat LaunchAgent/healthcheck contract, and any changed comments/static assertions.
9. Planning-lock passes with this plan as `--plan-evidence` for every changed high-risk path.
10. No live DB write, process reload, LaunchAgent reload, submit, cancel, or operator control change is performed without explicit current-session approval.
11. Open ACKED/PARTIAL maker ENTRY rests are screened against current same-side best bid, not executable ask cost, before cancel/redecision. A normal bid-ask spread must not trigger `BOOK_MOVED`; a rest more than the configured tick drift behind best bid must trigger a pull and redecision.
12. NO-token rests carry `buy_no` into the rest screen. Direction must be derived from executable snapshot token identity, not from venue side alone.
13. Misleading comments claiming side-agnostic rest checks are removed or corrected.
14. `read_replacement_forecast_bundle()` refuses `SHADOW_ONLY` / `SHADOW_VETO_ONLY` posterior rows on the live decision path and returns an explicit live-authority-missing block instead of serving them as `replacement_0_1`.
15. Replacement forecast materialization stamps `forecast_posteriors.trade_authority_status='LIVE_AUTHORITY'` only when the runtime trade-authority flag ladder is live and the row carries fused-Normal point q plus both certified bootstrap bounds. Bounds-less, Wilson fallback, capture-missing, or unpromoted rows remain diagnostic/shadow and are not live-readable.
16. Qkernel spine overlay cannot create `q_lcb_5pct > q_posterior` for receipt-facing selected-side probability fields. If qkernel controls selection economics, probability authority fields must remain internally consistent and direction-specific.
17. Misleading source text that says live authority is flag-only or that replacement bundles must remain shadow-only is removed or rewritten where touched by this slice.
18. `scripts/healthcheck.py --json` must surface a forecast posterior live-authority schema drift when `state/zeus-forecasts.db::forecast_posteriors` cannot represent `LIVE_AUTHORITY`, so operator readiness cannot look green while live posterior rows are structurally trapped in shadow status.

## Implementation Plan

Implementation update, 2026-06-17 11:22 UTC: replacement forecast live-only decoupling has been implemented and live-verified.

- Runtime policy is now live-or-disabled for replacement forecast authority. `SHADOW_ONLY` / `SHADOW_VETO_ONLY` remain only as one-time migration inputs for old DB rows and diagnostic-only non-money surfaces; they are not live reader, reactor, coverage, or production-job authority.
- `forecast_posteriors` now has `DIAGNOSTIC_ONLY` / `LIVE_AUTHORITY` row authority. Live DB proof after repair: `DIAGNOSTIC_ONLY=4461`, `LIVE_AUTHORITY=3152`, legacy posterior status count `0`.
- `readiness_state.provenance_json.trade_authority_status` was repaired: `DIAGNOSTIC_ONLY=101`, `LIVE_AUTHORITY=606`, legacy readiness provenance count `0`.
- Replacement forecast production config moved to `replacement_forecast_live`; runtime state directory was renamed from `state/replacement_forecast_shadow` to `state/replacement_forecast_live`.
- Forecast-live proof after reload: scheduler registered `replacement_forecast_live_materialize` and logged `live_authority_enabled=True`.
- Healthcheck proof after reload: process code fresh, live health composite OK, heartbeat fresh, `status_process_contract_ok=True`, forecast posterior schema OK, entry execution capability OK, `entry_forecast_status.status=LIVE_ELIGIBLE`. Overall health remains false due to dirty code-plane and the pre-existing Celsius city partition assumption mismatch, not due to replacement live authority, heartbeat, forecast schema, or entry capability.
- Dry-run proof: `scripts/check_replacement_forecast_live_dry_run.py --stdout` now executes against live config and reports runtime `LIVE_AUTHORITY`; remaining block is real current-target live coverage gap, not missing refit handoff or crash.

Implementation status, 2026-06-17 09:52 UTC: source repair and live verification are complete for this slice. Operator-approved live side effects performed during implementation: additive trade DB schema migration for `position_current` monitor freshness columns; venue heartbeat LaunchAgent contract reload; live daemon kickstart to load repaired source; command-recovery journal repair for `5d9e33cd1a61463e`; M5 recovery journal repair for live open partial SELL order `0x9b70c47cc25103138fde1db1f4c231eabbcd4ff4e82556c7b7509ceb6e15243b`. No submit, cancel, or operator control mutation was performed.

Closeout evidence:

- `position_current` in `state/zeus_trades.db` has `last_monitor_prob_is_fresh` and `last_monitor_market_price_is_fresh`; healthcheck no longer reports monitor freshness schema drift.
- Exit sell preflight refreshes CHAIN CTF collateral on both `exit_lifecycle` and direct `executor.execute_exit_order` paths, preserving fail-closed behavior for stale/degraded/failed-refresh/insufficient CTF states.
- `recovery_no_venue_order_id` command `5d9e33cd1a61463e` was not no-exposure; authenticated CLOB trade evidence showed a confirmed maker fill. It is now recovered to `FILLED` with order `0x250ca6e0d08358b1006b02556e098235b96ac07cf5c83bff85b08ea48504728a` and trade `1121ed3b-22fb-480b-8481-2a7cee280df4`.
- The live M5 `exchange_ghost_order` was a real open partial SELL against known Chengdu NO-token holdings. It is now reconstructed as recovered EXIT command `recovered_exit:c699b492db2547d4c9063ea5`, with confirmed sell trade `cf1c5009-b04e-46e2-9fcf-0dd070868c69`, and position `ad59da00-c32` restored to `pending_exit` with remaining chain shares `13.6221`.
- A discovered ws-gap bug that recorded account-wide unrelated trades as `unrecorded_trade` findings was fixed; the 704 live findings created by the bad full sweep during verification were resolved with audit resolution `ws_gap_account_wide_unscoped_trade_noise_resolved`.
- Live DB proof after repair: `unknown_side_effects=(0, ())`, open reconcile findings `0`, and unresolved finding counts empty.
- Daemon status proof after repair: `state/status_summary.json` timestamp `2026-06-17T09:51:59.148398+00:00`, entry and exit `blocked_components=[]`, both statuses `requires_intent`, heartbeat/ws-gap/risk/collateral components all allowed.
- Healthcheck proof after repair: schema drift, LaunchAgent drift, process-loaded-code stale, live DB holder issues, entry execution capability issue, and live health composite issue are all null. Overall `healthy=false` remains because unrelated source-health freshness surfaces are outside this existing-position execution slice.

Residual non-slice observations:

- `command_recovery` still logs an older filled-entry projection repair error for command `84fb2c4c685a4040` requiring matching `decision_log.trade_case`; it is not an unknown-side-effect or open-reconcile blocker after this repair.
- Some Day0/source-health warnings remain and correctly fail closed at their own gates; this slice did not alter source truth, forecast authority, or new-entry alpha search.

### Slice A: Monitor Freshness Schema And Read-Model Proof

Purpose: durable redecision evidence must survive projection/reload. A fresh monitor event is not enough if the live projection cannot store or reload freshness bits.

Expected source surfaces:

- `src/state/ledger.py::_ensure_position_current_authority_columns`
- `src/state/db.py::init_schema_trade_only`
- `src/engine/lifecycle_events.py` projection builder
- `src/state/projection.py` upsert path
- `src/state/portfolio.py` loader and `ExitContext`
- `scripts/healthcheck.py`
- `tests/test_position_current_trade_schema_migration.py`
- `tests/test_healthcheck.py`

Steps:

1. Confirm source migration already includes both monitor freshness columns and `init_schema_trade_only()` calls the additive migration.
2. If the source path is correct, do not add a second migration path. Treat missing live columns as a live migration/reload proof problem.
3. If source path is incomplete, add only idempotent additive migration logic under existing ownership.
4. Verify projection and portfolio reload preserve `last_monitor_prob_is_fresh` and `last_monitor_market_price_is_fresh`.
5. Keep healthcheck fail-closed until the active trade DB schema has the columns.

Stop conditions:

- Stop before any direct live DB mutation unless operator explicitly approves a live schema migration action.
- Stop if topology says this needs a broader schema packet not covered by this plan.

### Slice B: Exit-Side Collateral Snapshot Refresh

Purpose: exit retry must use current chain CTF token truth before CTF sell preflight. The fix must not relax collateral safety.

Expected source surfaces:

- `src/execution/exit_lifecycle.py::_execute_live_exit`
- `src/execution/collateral.py::check_sell_collateral`
- `src/execution/executor.py::_refresh_entry_collateral_snapshot_for_submit`
- `src/execution/executor.py::execute_exit_order`
- `src/state/collateral_ledger.py::CollateralLedger.refresh`
- `src/state/collateral_ledger.py::sell_preflight`
- `tests/test_exit_safety.py`
- `tests/execution/test_collateral_lock_retry.py`
- `tests/test_collateral_ledger.py`

Preferred design:

1. Generalize the entry-only refresh helper into a shared submit-path collateral refresh helper, or add an exit helper that delegates to the same implementation.
2. In `exit_lifecycle._execute_live_exit`, refresh collateral after deterministic dust checks and before `check_sell_collateral()`.
3. In `executor.execute_exit_order`, refresh collateral before `_assert_collateral_allows_sell()` so direct executor exit calls have the same guarantee.
4. Preserve existing lock retry semantics for transient SQLite writer contention.
5. Treat refresh failure, degraded snapshot, stale snapshot, insufficient CTF balance, and insufficient CTF allowance as blocked/retryable, not as submit permission.

Tests:

- Stale collateral snapshot + successful refresh + sufficient CTF inventory allows exit preflight to proceed to the next gate.
- Stale collateral snapshot + refresh failure returns blocked/rejected with a clear reason and no order submit.
- Degraded refreshed snapshot remains blocked.
- Sufficient pUSD but insufficient CTF still blocks sell.
- Existing dust-hold behavior still happens before collateral refresh.
- Direct `execute_exit_order` path gets the same refresh-before-sell-preflight behavior.

### Slice C: Heartbeat, WS Gap, And Risk Exit Gate Proof

Purpose: reduce-only exit submit must not bypass venue safety gates, but those gates must reflect current live truth and not stale config/code drift.

Expected source and runtime surfaces:

- `src/control/heartbeat_supervisor.py`
- `src/control/live_health.py`
- `src/control/ws_gap_guard.py`
- `src/observability/status_summary.py`
- `src/risk_allocator.py`
- `scripts/healthcheck.py`
- `state/venue-heartbeat-keeper.json`
- `state/status_summary.json`
- `~/Library/LaunchAgents/com.zeus.venue-heartbeat.plist`
- `tests/test_heartbeat_supervisor.py`
- `tests/test_healthcheck.py`

Steps:

1. Keep `heartbeat_supervisor`, `ws_gap_guard`, and `risk_allocator_global` blocking semantics intact.
2. Fix source-level healthcheck/contract logic only if it is wrong; otherwise treat current failures as live LaunchAgent/process reload requirements.
3. For heartbeat LaunchAgent drift, the intended operator-approved runtime fix is timeout <= half cadence and a valid `ThrottleInterval`.
4. After heartbeat is healthy, re-check whether risk allocator clears `heartbeat_lost`.
5. Re-check WS gap/M5 reconcile required separately; do not assume heartbeat fixes WS gap.
6. If WS gap remains, repair reconcile evidence/latch clearing through the existing M5 recovery path, not by disabling the guard.

Live verification gates after operator-approved reload actions:

- `venue-heartbeat-keeper.json` is fresh, `health=HEALTHY`, `resting_order_safe=true`.
- `status_summary.execution_capability.exit.blocked_components` no longer includes stale heartbeat or risk heartbeat loss.
- If `ws_gap_guard` remains blocked, its current reason is reported and tied to reconcile findings rather than hidden.
- `scripts/healthcheck.py --json` does not report `LIVE_LAUNCHD_CONTRACT_DRIFT` or relevant `PROCESS_LOADED_CODE_STALE` for the execution/risk/forecast surface being claimed.

### Slice D: Monitor Probability Authority Boundaries

Purpose: keep stale belief from becoming fake edge. Exit/hold/shift decisions must distinguish fresh authority from degraded authority.

Expected source surfaces:

- `src/engine/monitor_refresh.py::monitor_probability_refresh`
- `src/engine/position_belief.py`
- `src/engine/cycle_runtime.py::_build_exit_context`
- `src/state/portfolio.py::ExitContext`
- `tests/engine/test_position_belief_authority.py`
- `tests/test_live_safety_invariants.py`

Steps:

1. Preserve `forecast_posteriors` / replacement posterior authority for fresh monitor probabilities.
2. Preserve `BELIEF_AUTHORITY_FAULT` and `legacy_belief_substitution_suppressed` behavior for stale/missing posterior.
3. If positions remain blind because posterior materialization is stale, repair same-family posterior materialization/reseed rather than substituting legacy ENS or entry posterior as fresh.
4. Keep Day0 hard-fact observation path separate from forecast belief substitution.

Tests:

- Stale posterior records `last_monitor_prob_is_fresh=false` and does not produce a statistical exit off stale belief.
- Fresh posterior records `last_monitor_prob_is_fresh=true` and can feed `ExitContext`.
- Day0 observation lane remains distinct and does not borrow settlement/source facts incorrectly.

### Slice E: Misleading Source Cleanup

Purpose: remove contradictory guidance that would cause future agents to repair the wrong surface.

Expected edits:

- `src/control/ws_gap_guard.py`: clarify that monitor, evaluation, and reconciliation can continue during a WS gap, but exit venue submission remains blocked until recovery evidence clears the submit latch.
- `src/engine/monitor_refresh.py`: remove or rewrite drift-prone comments claiming current live-position coverage. Keep the durable rule: stale/missing same-authority posterior fails closed and triggers same-family reseed.

Tests:

- Prefer existing tests if comments only.
- Add a lightweight static assertion only if there is already a local pattern for preventing this specific misleading wording.

### Slice F: Open Maker Entry Rest Continuous Redecision

Purpose: after an ENTRY order is confirmed/ACKED as a resting maker order, it must remain in continuous redecision. The system should cancel and re-route through the certified reactor when fresh evidence or book movement says the resting bid is stale, but it must not churn from ordinary spread or a bare price wiggle.

Expected source surfaces:

- `src/events/continuous_redecision.py::read_freshest_executable_prices`
- `src/events/continuous_redecision.py::screen_resting_orders`
- `src/main.py::_edli_open_maker_rests_for_screen`
- `src/main.py::_edli_continuous_redecision_screen_cycle`
- `tests/events/test_continuous_redecision_resurrection.py`
- `tests/execution/test_escalation_redecision_emit.py`

Preferred design:

1. Add or reuse a pure reader that returns current best bid per `(condition_id, direction)` for resting maker entries. For `buy_yes`, use YES best bid. For `buy_no`, use the native NO best bid implied by YES best ask (`1 - yes_ask`) unless a native NO book is available in the snapshot surface.
2. Keep `read_freshest_executable_prices()` as the ask/cost authority for entry edge screening. Do not repurpose it for maker-rest queue-position checks.
3. In `_edli_open_maker_rests_for_screen`, resolve each rest's direction from `executable_market_snapshots.yes_token_id/no_token_id/selected_outcome_token_id`, and carry `buy_yes` or `buy_no` into `OpenRest.side`.
4. In `screen_resting_orders`, compare `current_best_bid - rest.limit_price` against `REST_BOOK_DRIFT_TICKS * TICK_SIZE`. Only positive drift beyond tolerance pulls the rest for `BOOK_MOVED`.
5. Preserve existing belief-decay and stale-quote pulls. Do not add a direct submit path in the screen; cancel plus redecision must still flow through existing maker-rest cancel machinery and reactor certification.
6. Update misleading comments that say NO rests are side-agnostic or that ask cost is best bid.

Tests:

- Same-snapshot rest with normal spread does not pull merely because ask is above maker limit.
- `buy_yes` rest pulls when current YES best bid has moved more than one tick above the old limit.
- `buy_yes` rest does not pull when current YES best bid is within tolerance.
- `buy_no` rest direction is preserved by `_edli_open_maker_rests_for_screen` from token identity.
- `buy_no` rest uses native NO bid authority rather than YES-side ask/bid confusion.
- Existing belief-worsening and stale-quote tests still pass.

Stop conditions:

### Slice G: Live Replacement Forecast Authority And Q Evidence Repair

Purpose: new-entry decisions must not treat shadow-only replacement posterior rows as live probability authority, and qkernel selection must not corrupt receipt-facing probability fields with payoff-space economics.

Expected source surfaces:

- `src/data/replacement_forecast_bundle_reader.py`
- `src/data/replacement_forecast_materializer.py`
- `src/data/replacement_forecast_runtime_policy.py`
- `src/data/replacement_forecast_live_dry_run.py`
- `src/data/replacement_forecast_switch_decision.py`
- `src/engine/qkernel_spine_bridge.py`
- `src/engine/event_reactor_adapter.py` comments at the replacement authority seam
- `tests/test_replacement_forecast_materializer.py`
- `tests/test_replacement_qlcb_materialization.py`
- `tests/engine/test_qkernel_spine_bridge.py`
- `tests/engine/test_replacement_0_1_authority_evidence_gate.py`

Preferred design:

1. Define the live row predicate as row-level `trade_authority_status == "LIVE_AUTHORITY"` plus fused-Normal q mode plus both certified bootstrap bounds. A shadow row can be newest or complete, but it is not live authority.
2. In the bundle reader, scan newest scope rows for the latest live-authority-grade row. If none exists, block with an explicit reason and include no shadow fallback bundle.
3. In materialization, compute posterior row trade authority from the runtime flag ladder and the q/bounds predicate. Write `LIVE_AUTHORITY` only when all live predicates hold; otherwise keep the row diagnostic/shadow.
4. Do not promote deterministic Open-Meteo anchor rows to live trading authority; the fused posterior row is the live decision carrier.
5. In qkernel spine overlay, keep `q_posterior` and `q_lcb_5pct` from the selected reactor proof unless qkernel can supply same-side probability-space values with the same invariant. It may still overlay the selection `trade_score` so the downstream submit path sees the spine’s chosen economics, but it must not relabel payoff-space values as probability fields.
6. Remove or rewrite touched comments that say `LIVE_AUTHORITY` is flag-only or that bundles must remain shadow-only.

Tests:

- A shadow-only posterior row with fused q and bounds is not returned by the live bundle reader.
- A fused-Normal posterior with both bootstrap bounds and live runtime flags is materialized as `LIVE_AUTHORITY` and is readable by the live bundle reader.
- A fallback/non-bootstrap-bounds row stays shadow and is blocked by the live reader.
- Qkernel overlay preserves `q_posterior` / `q_lcb_5pct` on the proof and therefore preserves `q_lcb_5pct <= q_posterior` for a buy-yes proof like the observed Milan case.
- Existing replacement authority tests are updated so they no longer bless shadow rows as live probability authority.

Stop conditions:

- Stop before mutating existing live DB rows to backfill authority status unless the operator explicitly approves that live-state action in the current session.
- Stop before restarting/reloading the live daemon. Source changes alone do not prove that the running daemon has loaded the repair.

Implementation status, 2026-06-17 10:36 UTC: source repair complete; live reload / live DB migration not performed.

Implemented:

- `src/data/replacement_forecast_bundle_reader.py` now serves only row-level `LIVE_AUTHORITY` bundles with fused-Normal q mode plus both certified bounds. Shadow rows return `REPLACEMENT_POSTERIOR_LIVE_AUTHORITY_MISSING` instead of being laundered into `replacement_0_1`.
- `src/data/replacement_forecast_materializer.py` computes row authority from runtime flags plus the fused/bootstrap carrier predicate, and includes an idempotent source migration for the existing SQLite CHECK so future approved schema migration can admit `LIVE_AUTHORITY`.
- `src/state/schema/v2_schema.py` and `architecture/_schema_fingerprint.txt` now allow `LIVE_AUTHORITY` in the canonical `forecast_posteriors.trade_authority_status` CHECK.
- `scripts/healthcheck.py` now reports `FORECAST_POSTERIORS_LIVE_AUTHORITY_SCHEMA_DRIFT` when the active forecasts DB still has the old shadow-only CHECK, and the top-level healthy predicate includes that result.
- `src/engine/qkernel_spine_bridge.py` no longer overwrites receipt-facing `q_posterior`, `q_lcb_5pct`, or `q_source` with payoff-space spine economics. It preserves selected-side probability authority and only overlays the qkernel-selected `trade_score`.
- Replacement event/receipt provenance now accepts `LIVE_AUTHORITY` while preserving no-training and no-settlement-authority constraints.
- Misleading touched text claiming flag-only live authority or shadow-only replacement bundles was removed or rewritten.

Verification:

- `python3 -m py_compile src/state/schema/v2_schema.py src/data/replacement_forecast_bundle_reader.py src/data/replacement_forecast_materializer.py src/data/replacement_forecast_event_payload.py src/data/replacement_forecast_receipt_provenance.py src/data/replacement_forecast_runtime_policy.py src/data/replacement_forecast_live_dry_run.py src/data/replacement_forecast_switch_decision.py src/engine/qkernel_spine_bridge.py src/engine/event_reactor_adapter.py`
- `pytest -q tests/test_replacement_forecast_bundle_reader_tradeable_latest.py tests/test_replacement_forecast_materializer.py::test_live_authority_status_requires_live_flags_and_bootstrap_bounds tests/test_replacement_forecast_materializer.py::test_live_authority_status_rejects_wilson_or_missing_bounds tests/test_replacement_forecast_materializer.py::test_forecast_posteriors_live_authority_check_migration_preserves_rows tests/integration/test_qkernel_spine_blockers_pr409.py::test_overlay_preserves_probability_fields_and_updates_score tests/integration/test_qkernel_spine_blockers_pr409.py::test_overlay_does_not_create_milan_buy_yes_probability_contradiction tests/integration/test_qkernel_spine_routing.py::test_selected_proof_shape_is_submission_pipeline_ready tests/engine/test_event_reactor_no_bypass.py::test_replacement_live_authority_same_direction_replaces_receipt_probability` passed: 14 passed, 1 existing numpy warning.
- `python3 scripts/check_schema_fingerprint.py` passed.
- `git diff --check` passed.
- `python3 scripts/topology_doctor.py --planning-lock --plan-evidence docs/operations/current/plans/live_redecision_repair/PLAN.md --changed-files ...` passed.
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...` passed.
- `pytest -q tests/test_healthcheck.py::test_forecast_posteriors_schema_status_rejects_missing_live_authority_status tests/test_healthcheck.py::test_forecast_posteriors_schema_status_accepts_live_authority_status tests/test_healthcheck.py::test_healthcheck_uses_mode_qualified_status_and_reports_healthy` passed.

Read-only live evidence after source repair:

- `state/zeus-forecasts.db` still has the old `forecast_posteriors` CHECK allowing only `SHADOW_ONLY` / `SHADOW_VETO_ONLY`; no live DB migration was executed in this session.
- `python3 scripts/healthcheck.py --json` now exposes this as `forecast_posteriors_schema_ok=false`, `forecast_posteriors_schema_issue=FORECAST_POSTERIORS_LIVE_AUTHORITY_SCHEMA_DRIFT`, and `healthy=false`.
- Current replacement posterior rows are still `SHADOW_ONLY` even when they carry `FUSED_NORMAL_FULL` / `FUSED_NORMAL_PARTIAL`, bootstrap `q_lcb_basis=fused_center_bootstrap_p05`, and both q bounds.
- `state/status_summary.json` at `2026-06-17T10:29:54.850998+00:00` still shows event reactor mode, entry/exit/cancel `requires_intent` with no blocked components, 0 final intents and 0 submit attempts.
- New live regret rows after the source edit still show the old running-code contradiction, e.g. Shenzhen buy_yes at `2026-06-17T10:30:24.845359+00:00` with `q_live=0.14429988196290827` and `q_lcb_5pct=0.8669544973916138`. This proves the running daemon/live DB have not consumed the source repair yet; it is not a post-reload failure.

- Stop before any live cancel/submit or operator-control mutation.
- Stop if the only possible implementation would use executable ask cost as a proxy for best bid; that would preserve the existing defect.

### Slice G: Post-Fill Hold/Exit/Rotation Boundary

Purpose: answer the live claim precisely. After fill, Zeus does continuously monitor weather belief and market price for hold/exit decisions. Portfolio replacement/switch is currently mathematical shadow evidence, not live actuation.

Current source facts:

- `execute_monitoring_phase` refreshes monitor probability, market price, and pending-exit quotes each cycle, then calls `Position.evaluate_exit`.
- `Position.evaluate_exit` fails closed on stale/missing probability or market-price authority, and can trigger exits for RED force exit, settlement-imminent, whale toxicity, divergence, flash crash, CI separation, vig extremes, and directional exit rules.
- `execute_exit` does not locally close a live position without confirmed sell fill.
- `_emit_portfolio_rotation_evaluation_status` may compute a non-actuating replacement-candidate value summary, but it reports `portfolio_rotation_evaluation_status` and must not surface switch/rotation as a live redecision action until a real sell+replacement-buy chain is wired.

Decision:

- This slice may repair misleading text and tests around the boundary, but it must not claim live switch/rotation actuation exists.
- A future live rotation slice must be a separate plan because it would require ordered `EXIT_INTENT -> SELL command/fill -> released cash proof -> new ENTRY intent` with receipts for every leg and a failure mode when the sell fills but replacement entry does not.

## RALPLAN-DR Summary

Principles:

1. Separate decision truth, execution permission, and live side effects.
2. Fail closed on stale or missing authority; never fabricate freshness.
3. Chain/CLOB and canonical DB evidence outrank projections and comments.
4. A fix that only permits one order is a failure; repair reusable causal paths.
5. Operator-controlled live actions require explicit current approval.

Decision drivers:

1. Highest risk is a false live submit or false exit from stale authority.
2. Highest current blocker is multi-plane drift: schema/read model, collateral freshness, heartbeat/ws/risk gates.
3. Verification must use current live evidence, not replay-only or unit tests.

Options considered:

- Option 1: Repair only heartbeat/ws/risk gates. Rejected because exit code checks CTF collateral before later executor submit gates, and historical retries already failed on stale collateral.
- Option 2: Repair only collateral refresh. Rejected because current exit capability is also blocked by heartbeat/ws/risk and live schema drift.
- Option 3: Lower exit thresholds or force one sell. Rejected because it violates authority boundaries and does not prove systematic alpha/profit flow.
- Option 4: Ordered multi-plane repair from read-model proof to collateral refresh to live gate proof. Chosen because it matches the observed causal chain and keeps fail-closed semantics.

## ADR

Decision: Use an ordered multi-plane repair for existing-position redecision: read-model proof, exit collateral refresh, live gate proof, probability authority preservation, and misleading-source cleanup.

Drivers: live-money safety; durable redecision evidence; no stale-as-fresh behavior; no one-order filler fixes; operator-approved live side effects only.

Alternatives considered: heartbeat-only, collateral-only, threshold lowering, and replacing stale posterior with legacy/entry probability. All are rejected because they either bypass safety, fail to clear the actual chain, or fabricate confidence.

Why chosen: The live evidence shows redecision is running, exits have been decided, and failures occur across separate planes. A multi-plane repair is the smallest plan that can satisfy the observed chain without weakening safety.

Consequences: Implementation touches high-risk paths and must use planning-lock evidence. Some verification requires operator-approved reload/migration actions; local tests alone cannot close the live claim.

Follow-ups: After this slice, revisit new-entry decision frontier separately. Do not merge new-entry alpha search into this existing-position redecision repair unless fresh evidence proves shared root cause.

## Verification Plan

Local/source verification:

- `python3 scripts/topology_doctor.py --planning-lock --plan-evidence docs/operations/current/plans/live_redecision_repair/PLAN.md --changed-files <changed files>`
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files <changed files>` when files are added or registry-sensitive surfaces change.
- `python3 -m py_compile` for changed Python files.
- Targeted pytest:
  - `tests/test_position_current_trade_schema_migration.py`
  - `tests/test_exit_safety.py`
  - `tests/execution/test_collateral_lock_retry.py`
  - `tests/test_collateral_ledger.py`
  - `tests/test_healthcheck.py`
  - `tests/test_heartbeat_supervisor.py`
  - `tests/engine/test_position_belief_authority.py`

Live evidence gates, only after operator-approved live actions:

- Active DB schema proof from `PRAGMA table_info(position_current)`.
- `scripts/healthcheck.py --json` without schema drift, launchd contract drift, or relevant loaded-code stale issue.
- Fresh `state/venue-heartbeat-keeper.json` with healthy lease.
- Fresh `state/status_summary.json` showing exit capability no longer blocked by heartbeat/ws/risk, or reporting the remaining current blocker clearly.
- New `position_events` rows proving monitor refresh, exit intent, retry, reject, submit, fill, or hold decisions with fresh authority fields.
- If a sell order is submitted, verify venue command rows and order/fill facts separately from lifecycle projection.

## Stop Rules

- Stop before any live DB mutation, process reload, LaunchAgent reload, submit, cancel, or operator control change unless the operator explicitly approves that exact action in the current session.
- Stop if live evidence contradicts the plan and update this plan before implementation continues.
- Stop if topology planning-lock rejects this plan as insufficient evidence.
- Stop if a proposed fix reduces safety gates, fabricates freshness, or treats one order/fill as completion.

## Available Agent Guidance

Useful bounded lanes:

- `architect`: high-risk review of sequencing and authority boundaries.
- `debugger`: live gate root-cause isolation for heartbeat/ws/risk.
- `explore`: narrow code lookup for symbols and callers.
- `executor`: implementation of disjoint code slices after this plan is accepted.
- `test-engineer`: targeted tests for collateral refresh and schema/read-model behavior.
- `verifier`: live evidence audit after operator-approved runtime actions.
- `critic` or `code-reviewer`: final review before any live claim.

Parallelization guidance:

- Slice B collateral refresh can be implemented independently from Slice E comment cleanup.
- Slice C runtime/operator gate proof should remain read-only unless operator approves reload/config actions.
- Slice A schema source edits should not run concurrently with other `src/state/**` edits unless ownership is explicit.

## Closeout Requirements

Closeout must report:

- Changed files.
- Tests and topology gates run.
- Live side effects performed, or `none`.
- Current live blockers remaining, separated by schema/process/DB/heartbeat/ws/risk/order-event surfaces.
- Whether this plan was superseded or remains active.
