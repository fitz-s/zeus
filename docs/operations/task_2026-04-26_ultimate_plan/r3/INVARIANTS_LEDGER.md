# R3 Invariants Ledger

Live tracker of all NC-NEW + INV-NEW invariants introduced by R3 phases.
Updated by EACH phase merge — CI auto-appends rows on green.

Cold-start agent reads this BEFORE writing code (boot step 6 of
`PHASE_BOOT_PROTOCOL.md`).

If `Status` ≠ LIVE, the invariant is NOT yet enforced. Phases depending
on a non-LIVE invariant must NOT assume the invariant holds.

## Status legend

- `PENDING` — invariant proposed in a phase yaml but phase not yet started
- `IN_PROGRESS` — phase implementing the invariant is IN_PROGRESS
- `LIVE` — phase merged + antibody green on HEAD
- `BROKEN` — antibody currently failing on HEAD; URGENT block on all new phase merges
- `RETIRED` — invariant amended out by a later planning-lock event

## Per-PR update protocol

When a phase PR is created:
1. List the new NC-NEW + INV-NEW IDs in PR description.
2. PR description must include the antibody test path + the semgrep rule body (if applicable).

When the PR merges (CI green):
1. CI auto-appends rows to this ledger with status=LIVE.
2. CI verifies all PRIOR LIVE invariants still pass on the merged HEAD.
3. If any prior invariant flips to BROKEN: ALL pending phase merges blocked until fixed.

## Ledger

| ID | Phase | Rule (one line) | Antibody (test path or semgrep rule) | Last verified | Last commit | Status |
|---|---|---|---|---|---|---|
| (Inherited from R2 — already LIVE on HEAD 874e00cc) | | | | | | |
| INV-23..INV-32 | (R2) | (see architecture/invariants.yaml) | (existing) | 874e00cc | 874e00cc | LIVE |
| NC-16..NC-19 | (R2) | (see architecture/negative_constraints.yaml) | (existing) | 874e00cc | 874e00cc | LIVE |
| (R3 invariants — pending phase implementation) | | | | | | |
| NC-NEW-A | U2 | No INSERT INTO venue_commands outside src/state/venue_command_repo.py | semgrep `zeus-venue-commands-repo-only` | — | — | PENDING |
| NC-NEW-B | U1 | executable_market_snapshots is APPEND-ONLY | SQLite triggers + semgrep | — | — | PENDING |
| NC-NEW-C | (R2 carry, U2) | ClobClient.create_order allowlist of 3 callers | semgrep `zeus-create-order-via-order-semantics-only` | — | — | PENDING |
| NC-NEW-D | M1 | cycle_runner._execute_force_exit_sweep is SOLE caller of insert_command(IntentKind.CANCEL,...) within cycle_runner.py | tests/test_riskguard_red_durable_cmd.py::test_red_emit_sole_caller_is_cycle_runner_force_exit_block | — | — | PENDING |
| NC-NEW-E | M1 | RESTING is NOT a CommandState member | tests/test_command_grammar_amendment.py::test_RESTING_not_added_to_CommandState_NC_NEW_E | — | — | PENDING |
| NC-NEW-F | Z3 | HeartbeatSupervisor reuses single tombstone | tests/test_heartbeat_supervisor.py::test_lost_state_writes_tombstone_with_heartbeat_cancel_suspected_reason | — | — | PENDING |
| NC-NEW-G | Z2 | Provenance pinned at VenueSubmissionEnvelope, NOT specific SDK call shape | tests/test_v2_adapter.py::test_one_step_sdk_path_still_produces_envelope_with_provenance + test_two_step_sdk_path_produces_envelope_with_signed_order_hash + semgrep `zeus-v2-placement-via-adapter-only` | — | — | PENDING |
| NC-NEW-H | U2 | Calibration training filters venue_trade_facts WHERE state='CONFIRMED' | tests/test_provenance_5_projections.py::test_calibration_training_filters_for_CONFIRMED_only | — | — | PENDING |
| NC-NEW-I | U2 | Risk allocator separates OPTIMISTIC_EXPOSURE from CONFIRMED_EXPOSURE | tests/test_risk_allocator.py::test_optimistic_vs_confirmed_split_in_capacity_check | — | — | PENDING |
| NC-NEW-J | F3 | TIGGEIngest.fetch() raises TIGGEIngestNotEnabled when gate closed; open-gate fetch reads only operator-approved local JSON payloads | tests/test_tigge_ingest.py::test_tigge_fetch_raises_when_gate_closed + tests/test_tigge_ingest.py::test_tigge_open_gate_without_payload_configuration_fails_closed + tests/test_forecast_source_registry.py::test_tigge_gate_open_routes_through_ingest_not_openmeteo | — | — | PENDING |
| NC-NEW-K | Z4 | sell_preflight ONLY consults CTF balance + reservations; cannot substitute pUSD | tests/test_collateral_ledger.py::test_sell_preflight_does_NOT_substitute_pusd_for_tokens | — | — | PENDING |
| INV-NEW-A | Z1 | No live submit when CutoverGuard.current_state() != LIVE_ENABLED | tests/test_cutover_guard.py::test_executor_raises_cutover_pending_when_freeze | — | — | PENDING |
| INV-NEW-B | Z2 | Every submit() produces a VenueSubmissionEnvelope persisted via venue_command_repo BEFORE side effect | tests/test_v2_adapter.py::test_create_submission_envelope_captures_all_provenance_fields | — | — | PENDING |
| INV-NEW-C | Z3 | GTC/GTD MUST NOT be submitted when HeartbeatSupervisor.health != HEALTHY | tests/test_heartbeat_supervisor.py::test_lost_state_blocks_GTC_and_GTD_placement | — | — | PENDING |
| INV-NEW-D | Z4 | Reserved tokens released atomically when sell command transitions to CANCELED/FILLED/EXPIRED | tests/test_collateral_ledger.py::test_release_reservation_on_cancel_or_fill | — | — | PENDING |
| INV-NEW-E | U1 | Every venue_commands row cites snapshot_id; freshness gate enforced | tests/test_executable_market_snapshot_v2.py::test_command_insertion_requires_fresh_snapshot | — | — | PENDING |
| INV-NEW-F | U2 | Every fact has source + observed_at + local_sequence | tests/test_provenance_5_projections.py::test_local_sequence_monotonic_per_subject + test_source_field_required_on_every_event | — | — | PENDING |
| INV-NEW-G | M2 | Network/timeout exceptions after POST → SUBMIT_UNKNOWN_SIDE_EFFECT, never SUBMIT_REJECTED | tests/test_unknown_side_effect.py::test_network_timeout_after_POST_creates_unknown_not_rejected | — | — | PENDING |
| INV-NEW-H | M3 | WS gap detected → block new submit + force M5 sweep before unblocking | tests/test_user_channel_ingest.py::test_websocket_disconnect_triggers_REST_backfill_sweep | — | — | PENDING |
| INV-NEW-I | M4 | Replacement sell BLOCKED until prior reaches terminal or proven-absent | tests/test_exit_safety.py::test_CANCEL_UNKNOWN_blocks_replacement | 2026-04-27 | ~ | LIVE |
| INV-NEW-J | M4 | Exit mutex per (position_id, token_id) is single-holder | tests/test_exit_safety.py::test_mutex_held_blocks_concurrent_exit | 2026-04-27 | ~ | LIVE |
| INV-NEW-K | M5 | M5 sweep is read-only against venue + journal; never INSERTs into venue_commands | tests/test_exchange_reconcile.py::test_sweep_does_not_create_new_venue_commands_rows | 2026-04-27 | ~ | LIVE |
| INV-NEW-L | R1 | Settlement transitions are durable + crash-recoverable; REDEEM_TX_HASHED is recovery anchor | tests/test_settlement_commands.py::test_redeem_crash_after_tx_hash_recovers_by_chain_receipt | — | — | PENDING |
| INV-NEW-M | T1 | Paper-mode runs go through SAME PolymarketV2Adapter Protocol; FakePolymarketVenue and live adapter produce schema-identical events | tests/integration/test_p0_live_money_safety.py::test_paper_and_live_produce_identical_journal_event_shapes | — | — | PENDING |
| INV-NEW-N | F1 | Every forecast row carries source_id + raw_payload_hash + authority_tier | tests/test_forecast_source_registry.py::test_forecasts_append_persists_source_id_and_raw_payload_hash | — | — | PENDING |
| INV-NEW-O | F2 | Calibration retrain consumes ONLY venue_trade_facts WHERE state='CONFIRMED' | tests/test_calibration_retrain.py::test_arm_then_trigger_consumes_confirmed_trades_only | — | — | PENDING |
| INV-NEW-P | F2 | Calibration param promotion to live REQUIRES frozen-replay PASS | tests/test_calibration_retrain.py::test_frozen_replay_failure_blocks_promotion | — | — | PENDING |
| INV-NEW-Q | A1 | No strategy promoted to live without StrategyBenchmarkSuite.promotion_decision() = PROMOTE | tests/test_strategy_benchmark.py::test_promotion_blocked_unless_replay_paper_shadow_all_pass | — | — | PENDING |
| INV-NEW-R | A2 | Kill switch trips on threshold breach (configurable) | tests/test_risk_allocator.py::test_kill_switch_blocks_all_submits | — | — | PENDING |
| INV-NEW-S | G1 | LIVE deploy requires 17/17 G1 gate PASS + ≥1 staged-live-smoke environment | scripts/live_readiness_check.py exit code 0 | — | — | PENDING |

## Auto-appender CI hook

`.github/workflows/r3_invariant_ledger_update.yml`:

```yaml
on:
  push:
    branches: [main]
jobs:
  update_ledger:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: .venv/bin/python docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/invariant_ledger_check.py --update
      - run: |
          if git diff --exit-code docs/operations/task_2026-04-26_ultimate_plan/r3/INVARIANTS_LEDGER.md; then
            echo "no ledger updates"
          else
            git config user.name "ci-bot"
            git config user.email "ci@zeus"
            git add docs/operations/task_2026-04-26_ultimate_plan/r3/INVARIANTS_LEDGER.md
            git commit -m "ledger: auto-update post-merge"
            git push
          fi
```

## Notes

- This ledger is the SINGLE SOURCE OF TRUTH for invariant liveness across all 20 R3 phases.
- Phase agents READ but do NOT write this ledger directly. CI handles updates on merge.
- If a phase's PR description doesn't list the new IDs, CI rejects the PR.
- If an invariant flips to BROKEN: bisect, fix, OR planning-lock amendment. No silent retirement.
