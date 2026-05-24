# PR332 Deploy-Ready Review

Saved: 2026-05-24
Reviewed head: `8bf87df499a321f438f6a1419baf170fa5f74c9d`
Verdict: NO-GO / do not merge / do not daemon reboot / do not call deploy-ready.

## Review Findings

The review confirmed that PR332 had fixed several earlier catastrophic issues:

- EDLI no longer calls the broad `run_cycle()` wrapper.
- Market discovery is full weather discovery with slug fallback, not slug-only.
- Fresh-at-submit executable snapshot recapture is restored.
- No-submit proof no longer reserves Day0 live cap.
- Event payloads no longer own q/FDR/Kelly proof inputs.

The review still found deploy-readiness P0s:

1. Runtime market topology lookup used `trade_conn` for `market_events_v2`, even though `market_events_v2` is forecasts DB authority.
2. Forecast inference bypassed Zeus calibration by setting `p_cal = p_raw`.
3. Native quote fallback fabricated depth from `orderbook_top_ask` plus `min_order_size`.
4. Day0 trigger was enabled in config while the online observation-context hook was not wired and the catch-up scanner was disabled.
5. The no-submit adapter did not revalidate executable forecast reader/source-run authority at receipt time.
6. Full pytest sweep was not passing.
7. Daemon restart and live Polymarket websocket/user-channel smoke were not run.

P1 issues:

- RiskGuard proof is still only a top-level GREEN risk-level check, not full entry exposure/cluster/portfolio cap proof.
- Day0 boundary receipts lack explicit fact-true/killed-bin report fields.
- Market-channel thread still needs burst/concurrency proof against reactor writes.

## Required Fixes From Review

- Split topology connection authority: forecasts connection owns `market_events_v2`; trade connection owns executable snapshots.
- Restore calibration authority: consume calibrated probabilities or Platt calibrator; fail closed when calibration authority is missing.
- Remove synthetic depth fallback: min order size is not liquidity.
- Wire an actual Day0 observation hook or disable Day0 trigger/hard-fact live.
- Revalidate forecast source-run/source-run-coverage/readiness evidence at receipt time.
- Full sweep must pass or have an explicit unrelated-baseline waiver before deploy-ready.
- Daemon restart and live websocket/user-channel smoke are required before daemon-online readiness.

## Repair Status In Current Worktree

Implemented after this review:

- `event_bound_no_submit_adapter_from_trade_conn()` and `executable_snapshot_gate_from_trade_conn()` now accept `topology_conn`; `src/main.py` passes the forecasts connection for topology.
- `build_event_bound_no_submit_receipt()` now reads market topology from `topology_conn` / forecast authority, while executable snapshots stay on `trade_conn`.
- `_market_analysis_from_event_snapshot()` now uses `_snapshot_p_cal()`, which reads persisted calibrated probability authority when present or calls the Platt calibrator with source/cycle/horizon provenance. Missing calibration authority fails closed with `CALIBRATION_AUTHORITY_MISSING`.
- Receipt-time forecast source revalidation now checks `source_run` and `source_run_coverage` status, completeness, readiness, expiry, and required-step coverage.
- `_native_quote_book_from_snapshot_row()` no longer creates synthetic liquidity from `orderbook_top_ask`; it requires native depth or explicit best-depth size columns.
- `day0_extreme_trigger_enabled` and `day0_hard_fact_live_enabled` are disabled in config until the online Day0 observation hook is wired.
- Production-shaped regression tests now cover separate trade/forecast connections, calibration authority, source-run revalidation, and top-ask-without-depth rejection.
- Follow-up Codex-only critic review on commit `80aa85e` found two remaining proof gaps: source-run coverage did not require `snapshot_ids_json` to contain the causal snapshot, and the public adapter/gate still allowed implicit trade-connection fallback when forecast/topology connections were omitted.
- Those follow-up gaps are repaired in the current worktree: receipt-time coverage revalidation now requires `snapshot_ids_json` to contain the hydrated snapshot and, for forecast events, the event `causal_snapshot_id`; the no-submit receipt builder and executable snapshot gate fail closed when explicit forecast/topology authority connections are absent. Regression tests cover both.
- Latest follow-up repair also closes two additional deploy-readiness gaps from the review:
  receipt-time forecast proof now calls the canonical
  `read_executable_forecast_snapshot()` reader and requires the returned
  snapshot id to match the hydrated/event causal snapshot; SQL checks remain
  only a pre-hydration guard. No-submit Kelly bankroll now uses an injected
  deterministic provider in tests or the runtime bankroll cache only; it does
  not call the live wallet fetch path from the proof-only adapter.
- Regression coverage now includes canonical reader blocking, production-shaped
  `depth_at_best_ask` quote authorization, top-ask-without-depth rejection, and
  no-submit default bankroll path not calling `bankroll_provider.current()`.
- Latest calibration/fill repair splits calibration authority from forecast
  authority: `src/main.py` passes the world connection as `calibration_conn`,
  while forecast snapshots/topology stay on the forecasts connection and
  executable snapshots stay on the trade connection. `p_cal_json` is accepted
  only when the row carries VERIFIED model/source/run/available-at provenance;
  otherwise the adapter loads Platt calibration from `calibration_conn`.
- Visible public book depth no longer sets `p_fill_lcb=1.0`; no-submit proof
  caps visible-depth feasibility at `edli_v1.no_submit_visible_depth_fill_lcb`
  (`0.05`) unless future execution evidence explicitly proves more.
- EDLI event processing now has explicit local backpressure knobs:
  `forecast_snapshot_emit_limit=20`, `day0_catchup_emit_limit=20`, and
  `no_submit_proof_limit=10`; `src/main.py` clamps those values before event
  emission / proof processing. This is not a substitute for DB concurrency
  smoke, but it removes the previous default of running up to 50 full-family
  no-submit proofs per scheduler tick.
- Follow-up local review found and repaired another topology authority gap:
  if `market_events_v2` lacks bin range bounds, receipt generation now fails
  closed with `EVENT_BOUND_MARKET_TOPOLOGY_INVALID` instead of falling back to
  payload/default `0-1°F` bins. `p_cal_json` provenance also requires non-empty
  snapshot `source_id` and `source_run_id` before source matching can pass.
- Codex-only deep review then found a P1 market-channel runtime pressure gap:
  public tick/resolve actions could synchronously trigger unlimited refresh
  callbacks. `MarketChannelOnlineService` now dedupes refresh actions within a
  window and caps accepted refresh actions with
  `edli_v1.market_channel_refresh_max_actions_per_window=5` /
  `market_channel_refresh_window_seconds=60`.
- Day0 remains explicitly out of deploy scope for this PR: the config keeps
  `day0_extreme_trigger_enabled=false` and `day0_hard_fact_live_enabled=false`
  until an online `Day0ObservationContext` hook is implemented and smoked.

Fresh verification after this repair:

- `python -m py_compile src/main.py src/engine/event_reactor_adapter.py
  tests/engine/test_event_reactor_no_bypass.py
  tests/money_path/test_edli_online_invariants.py` -> PASS.
- `python -m pytest -q tests/engine/test_event_reactor_no_bypass.py
  tests/money_path/test_edli_online_invariants.py --maxfail=5` -> PASS,
  42 passed.
- `python -m pytest -q tests/events tests/engine/test_event_reactor_no_bypass.py
  tests/strategy/live_inference tests/money_path
  tests/state/test_edli_table_ownership.py --maxfail=10` -> PASS,
  222 passed.
- `python scripts/check_schema_version.py && python
  scripts/check_table_registry_coherence.py && python
  scripts/ci/assert_test_quality.py` -> PASS.
- `python3 scripts/replay_correctness_gate.py --db
  /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db
  --bootstrap && python3 scripts/replay_correctness_gate.py --db
  /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db`
  -> PASS after rolling same-day baseline refresh, 10,063 deterministic
  events, projection hash
  `448ae82fbe91376f25f4a5d45ccc3c00dfff3098f7fdd4861d1ae383410eb4f5`.

Still not deploy-ready:

- Full pytest sweep remains non-passing in this patch set: latest local run
  stopped at `1211 passed / 9 failed / 1 error / 10 skipped / 19 deselected`.
  Failures are the existing missing `mypy`, missing maintenance-worker
  `TASK_CATALOG.yaml`, crossing-decision passive-fill fixture, and maintenance
  untracked-quarantine expectation lanes; they still require pass or explicit
  baseline waiver before deploy-ready.
- Daemon restart / live market-channel websocket / user-channel smoke remain unrun.
- RiskGuard proof depth, Day0 online hook/boundary receipt reporting, and
  live market-channel/DB concurrency smoke remain follow-up deploy gates.
