# R3 M3 work record — User-channel WS ingest + REST fallback

Date: 2026-04-27
Branch: plan-pre5
Task: R3 M3 user-channel WS ingest + REST fallback — authenticated WS observations into U2 facts with fail-closed gap guard
Status: COMPLETE, post-close PASS

Changed files:

Implementation:
- `src/ingest/AGENTS.md`
- `src/ingest/polymarket_user_channel.py`
- `src/control/ws_gap_guard.py`
- `src/execution/executor.py`
- `src/engine/cycle_runner.py`
- `src/main.py`

Tests:
- `tests/test_user_channel_ingest.py`
- `tests/test_digest_profile_matching.py`

Routing / registries / docs:
- `architecture/topology.yaml`
- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`
- `architecture/test_topology.yaml`
- `architecture/docs_registry.yaml`
- `architecture/AGENTS.md`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/reference/AGENTS.md`
- `docs/reference/modules/AGENTS.md`
- `docs/reference/modules/ingest.md`
- `docs/reference/modules/control.md`
- `src/AGENTS.md`
- `src/control/AGENTS.md`
- `workspace_map.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/reference_excerpts/polymarket_user_ws_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M3_pre_close_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M3_post_close_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/M3_gap_threshold_and_auth_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/receipt.json`

Summary:

- Added `PolymarketUserChannelIngestor` with official user-channel subscription payload (`auth`, condition-ID `markets`, `type=user`) and optional live start via lazy `websockets` import.
- Added `WSAuth` credential object and redaction of auth/apiKey/secret/passphrase fields from stored raw payloads.
- Added `ws_gap_guard` status object and `assert_ws_allows_submit()` pre-submit guard. Disconnect/auth/stale/mismatch status sets `m5_reconcile_required` and blocks submit until future M5 evidence owns recovery.
- Mapped user-channel order messages to U2 `append_order_fact(... source='WS_USER')`.
- Mapped trade statuses to U2 `append_trade_fact(... source='WS_USER')`:
  - `MATCHED` -> `OPTIMISTIC_EXPOSURE` lot and optional `PARTIAL_FILL_OBSERVED` command event.
  - `CONFIRMED` -> `CONFIRMED_EXPOSURE` lot and optional `FILL_CONFIRMED` command event.
  - `FAILED` after `MATCHED` -> quarantining/reversal lot via `rollback_optimistic_lot_for_failed_trade()`.
- Wired executor submit paths to call the WS gap guard before DB/venue side effects.
- Wired cycle summaries to surface `ws_user_channel` and `m5_reconcile_required`/`entries_blocked_reason` when a gap is active.
- Wired daemon startup to start the ingestor only when `ZEUS_USER_CHANNEL_WS_ENABLED` is truthy and condition IDs + credentials are present; misconfiguration records a fail-closed gap.

Verification:

```text
python3 -m py_compile src/ingest/polymarket_user_channel.py src/control/ws_gap_guard.py src/execution/executor.py src/engine/cycle_runner.py src/main.py: PASS
pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py: 15 passed
pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_m3_user_channel_routes_to_m3_profile: 1 passed
combined M3+executor regression run: 80 passed, 2 skipped, 18 known sqlite3 datetime adapter warnings
python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 user channel ws implementation
python3 scripts/topology_doctor.py --planning-lock ...: topology check ok
python3 scripts/topology_doctor.py --map-maintenance ... --map-maintenance-mode closeout: topology check ok
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M3: GREEN=12 YELLOW=0 RED=0
```
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py` -> 18 passed.
- `python3 scripts/topology_doctor.py closeout --changed-files ... --receipt-path docs/operations/task_2026-04-26_ultimate_plan/receipt.json --json` -> PASS; blocking_issues=[]; nonblocking warnings only for code-review-graph freshness/partial coverage and existing context-budget oversize.

Pre-close blocker remediation:
- Critic Pascal the 2nd BLOCKED stale guard behavior because stale summary/submit paths could block without materializing `m5_reconcile_required=True`. Remediation: `ws_gap_guard.summary()` and `assert_ws_allows_submit()` now materialize stale `SUBSCRIBED`/`AUTHED` status into `record_gap("stale_last_message")`; regression `test_stale_guard_path_sets_m5_reconcile_required_without_manual_check` covers the guard path without calling the ingestor manually.
- Critic Pascal the 2nd BLOCKED market mismatch scoping because summary could allow unrelated submits. Remediation: `MARKET_MISMATCH` now blocks all new submit until M5 evidence; regression `test_market_subscription_mismatch_blocks_all_new_submit_until_m5` covers global block and summary.
- Critic Pascal the 2nd BLOCKED maker-order trade fact linkage because `venue_trade_facts.venue_order_id` used the first candidate/taker order. Remediation: `_handle_trade()` now writes `command["venue_order_id"]` when the command lookup matches via `maker_orders`; regression `test_maker_order_trade_fact_uses_matched_zeus_order_id` covers foreign taker + Zeus maker order.
- Critic Pascal the 2nd rerun BLOCKED a residual mismatch recovery gap: after mismatch, a later valid message could preserve `m5_reconcile_required=True` but allow submits for unrelated markets. Remediation: any `m5_reconcile_required=True` status now blocks all new submit regardless of current subscription state or affected markets; regression `test_market_subscription_mismatch_stays_global_block_after_later_valid_message` covers mismatch followed by a valid message.

Next:
Run post-close third-party critic + verifier review before freezing M4.

Pre-close review:
- Pre-close critic Pascal the 2nd final rerun APPROVE and verifier Descartes the 2nd PASS are recorded in `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M3_pre_close_2026-04-27.md`.

Post-close review:
- Post-close third-party critic Aquinas the 2nd APPROVE and verifier Gibbs the 2nd PASS are recorded in `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M3_post_close_2026-04-27.md`; M4 is unfrozen for phase-entry/topology boot.

Known deferrals / risks:

- M5 reconciliation sweep/unblock is intentionally deferred; M3 only records `m5_reconcile_required`.
- Live WS activation requires operator-supplied condition IDs and L2 API credentials; not exercised in unit tests.
- REST fallback remains a future reconciliation path; no broad REST sweep is implemented in M3.
- `websockets` remains a lazy optional runtime import; no new dependency was added.


Post-close finalization:
- Post-close verifier Gibbs the 2nd initial BLOCK was procedural: missing post-close artifact/status consistency; `M3_post_close_2026-04-27.md` plus status/work-record/receipt surfaces were updated; Gibbs rerun PASS; M4 may be unfrozen for phase-entry/topology boot.

Final closeout:
- Final post-close closeout rerun after Gibbs PASS/status finalization: `ok=true`, `blocking_issues=[]`; nonblocking warnings limited to code-review-graph freshness/partial coverage and existing context-budget overages.
