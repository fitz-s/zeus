# Live Order End-to-End Verification and Repair Plan

Created: 2026-05-15
Branch: `feat/live-order-e2e-verification-2026-05-15`
Base: `origin/main` at `8b3c3c2c59deb4daf61ada07eced318abd08a8bf`
Topology route: `operation planning packet`, T1, admitted path only.

## Purpose

This packet is the execution plan for proving and, where necessary, repairing the complete live money path:

`forecast-live data production -> canonical forecast DB -> live reader -> live daemon evaluation -> executor -> Polymarket order side effect -> durable command/order/position/reconciliation records`

The goal is not satisfied by proving that data reaches the live reader. The goal is satisfied only when the running live system, on the deployed code, places an expected live limit order through the normal executor path and the order is recorded through the designed canonical record chain.

## Authority and Scope

Primary authority surfaces:

- `AGENTS.md:7-13` defines the money path and probability chain.
- `AGENTS.md:33-45` identifies `src/main.py`, `src/engine/cycle_runner.py`, `src/engine/evaluator.py`, and `src/execution/executor.py` as the live runtime path, with `chain/CLOB facts -> canonical DB/events -> projections/status -> derived reports` as the truth path.
- `AGENTS.md:47-54` defines the current K1 DB split: `state/zeus-world.db` and `state/zeus-forecasts.db`.
- `AGENTS.md:80-91` defines risk behavior; degraded data blocks entries, and RED cancels/sweeps.
- `AGENTS.md:108-120` defines chain reconciliation and Chain > local authority.
- `src/execution/AGENTS.md` requires limit orders only and live placement through the V2 adapter with pre-side-effect command persistence.
- `src/state/AGENTS.md` defines append-first canonical truth and `venue_command_repo.py` as a high-risk command journal.
- `src/data/AGENTS.md` defines forecast/source data as truth-binding and forbids silent fallback/provider semantics drift.
- `src/engine/AGENTS.md` requires reconciliation before evaluation and forbids engine shortcuts around truth, risk, and lifecycle law.
- `REVIEW.md:45-52` classifies irreversible chain mutation without reconciliation, market-order substitution, gateway bypass, V2 preflight bypass, and side effects without `venue_commands` as Critical.

The plan does not authorize direct CLOB SDK calls, manual DB row fabrication, fake fills, or bypass of risk/entry/rollout gates. A live order must be produced by the normal daemon/evaluator/executor path.

## Initial Read-Only Baseline

Observed before this branch was created:

- PR #117 was merged into `origin/main` at `8b3c3c2c59` with passing checks.
- The active live launchd labels were `com.zeus.live-trading`, `com.zeus.forecast-live`, and `com.zeus.riskguard-live`.
- The running live processes were started before PR #117 was merged, so process liveness alone did not prove merged code was loaded.
- `scripts/check_data_pipeline_live_e2e.py --live` could prove forecast DB -> live reader alignment, but the script explicitly never performs venue actions (`scripts/check_data_pipeline_live_e2e.py:5-9`).
- `scripts/live_health_probe.py` still checks `src.ingest_main` for ingest liveness (`scripts/live_health_probe.py:49-53`) and reports `ingest_dead` when that process is absent (`scripts/live_health_probe.py:114-123`). This is semantically stale if `com.zeus.forecast-live` is now the forecast owner.
- Live health showed `entry=requires_intent`, `blocking_gates=1`, and `funnel=4547/0/0`; therefore the system had not yet proven order selection or submission.

All baseline values must be refreshed at execution start and attached to the evidence bundle. Stale values from this plan are not completion evidence.

## Completion Definition

The operation is complete only after all gates below are true in one evidence bundle:

1. **Deployed-code proof**: live processes have PIDs/start times after deployment and run from the intended worktree/commit.
2. **Forecast producer proof**: `forecast-live` is the single OpenData owner, heartbeat is fresh, and source-run/job-run/coverage/readiness rows advanced after deployment.
3. **Reader proof**: the live reader consumes the latest source_run/release_key and returns `EXECUTABLE_FORECAST_READY` or a typed fail-closed reason that matches DB truth.
4. **Entry gate proof**: `execution_capability.entry` is authorized and `global_allow_submit=true`, or a named blocker is repaired with a relationship test before retry.
5. **Decision proof**: the live daemon creates a valid execution candidate/final intent from the normal evaluator path.
6. **Submit proof**: executor submits a real live limit order through the normal gateway. The side effect is preceded by `venue_commands` insertion and `venue_command_events` transition.
7. **Accepted-order proof**: the venue returns an accepted/acked/resting order outcome with venue order identity. Rejected or unknown outcomes are blocker evidence, not completion evidence for "live placed an expected order."
8. **State proof**: canonical DB/event state, derived status, and chain reconciliation agree with the venue outcome.
9. **Guard proof**: a post-order guard monitors until the order is filled/cancelled/expired or otherwise reaches the designed next lifecycle state. A resting accepted order is enough to prove "live placed an order"; a filled position requires continued lifecycle verification.

## Non-Negotiable Invariants

- No market orders. Entry execution remains limit-order-only (`src/execution/executor.py:1-12`).
- No direct SDK call outside the executor/gateway path. `REVIEW.md:51-52` treats this as Critical.
- No side effect before durable command persistence. `_live_order` documents the intended chain: persist command/event, then call `client.place_limit_order`, then append ack/reject/unknown (`src/execution/executor.py:2252-2267`).
- No forged evidence. `scripts/check_data_pipeline_live_e2e.py` is read-only and cannot prove order execution.
- No bypass of readiness/rollout evidence. `docs/runbooks/live-operation.md:184-191` requires evidence-gated flips.
- No claim that `readiness_state.status='LIVE_ELIGIBLE'` alone means live can submit. The runbook states read-side validation is also required (`docs/runbooks/live-operation.md:278-287`).

## Worktree and Deployment Strategy

1. Preserve the existing main worktree and any unrelated branches.
2. Perform implementation in this isolated worktree:
   `/Users/leofitz/.openclaw/workspace-venus/zeus-live-order-e2e-verification-2026-05-15`.
3. Keep the branch based on current `origin/main` unless a later fetch shows main moved; if main moved, merge or rebase only after a conflict-first scan.
4. Do not point launchd at a dirty or uncommitted worktree for final evidence. Either:
   - deploy from the committed branch in this worktree, or
   - merge to `main` and deploy from the clean live root.
5. Record `git rev-parse HEAD`, `git status --short`, and process working directories in every evidence bundle.

## Pre-Implementation Freeze and Live-Mutation Gates

This packet starts as a plan packet. Before implementation or live mutation, it must be promoted to the active execution packet:

1. Route and update `docs/operations/current_state.md` to name `docs/operations/task_2026-05-15_live_order_e2e_verification/` as the active execution packet.
2. Record the topology route for that `current_state.md` edit and run planning-lock with this plan as evidence.
3. Separate evidence classes in the work log:
   - `READ_ONLY_BASELINE`: process/DB/log inspection only.
   - `REPO_EDIT`: code/test/doc changes only.
   - `LIVE_RESTART_GO`: launchd restart authorization and preconditions.
   - `LIVE_SUBMIT_GO`: first live submit authorization and preconditions.
4. Treat the user's `/goal` directive in this thread as the standing operator authorization to pursue the full goal, but do not execute a live restart or live submit until the corresponding gate checklist is satisfied and recorded.

`LIVE_RESTART_GO` checklist:

- plan has critic `APPROVE`;
- branch/worktree is clean and committed;
- focused tests for any repo edits pass;
- riskguard is running or its absence is a named blocker;
- rollback/containment command path is recorded;
- no raw secrets are captured in the evidence bundle.

`LIVE_SUBMIT_GO` checklist:

- deployed-code proof, forecast producer proof, reader proof, and entry gate proof are all satisfied after restart;
- `execution_capability.entry.global_allow_submit=true` and `live_action_authorized=true`;
- a real evaluator-produced candidate/final intent is captured with correlation fields;
- no direct SDK/manual DB/test-double path is involved;
- expected order notional, side, token, limit price, and cancellation policy are recorded before submit.

## Phase 1 - Baseline Evidence Refresh

Purpose: prove the exact starting state before repairs or live restarts.

Read-only commands:

```bash
git status --short --branch
git rev-parse HEAD origin/main
git worktree list --porcelain
launchctl list | rg 'com\\.zeus\\.(live-trading|forecast-live|riskguard-live|data-ingest)'
launchctl print "gui/$(id -u)/com.zeus.live-trading"
launchctl print "gui/$(id -u)/com.zeus.forecast-live"
launchctl print "gui/$(id -u)/com.zeus.riskguard-live"
python3 scripts/check_daemon_heartbeat.py
python3 scripts/check_data_pipeline_live_e2e.py --json --live
python3 scripts/live_health_probe.py
```

DB read-only queries:

```sql
SELECT source_run_id, run_date, run_hour, source, product, completed_at
FROM source_run
ORDER BY completed_at DESC
LIMIT 5;

SELECT source_run_id, COUNT(*) AS rows, MAX(computed_at) AS max_computed_at
FROM readiness_state
GROUP BY source_run_id
ORDER BY max_computed_at DESC
LIMIT 5;

SELECT COUNT(*) AS open_commands
FROM venue_commands
WHERE state NOT IN ('FILLED', 'CANCELLED', 'EXPIRED', 'REJECTED', 'SUBMIT_REJECTED');
```

Pass criteria:

- Evidence includes commit, process PIDs, process cwd, DB max timestamps, latest source_run, reader output, entry status, risk level, and current venue command counts.
- Any secret-bearing environment output is redacted before being committed or pasted into reports.

Failure handling:

- If launchd output exposes credentials, do not preserve raw output. Re-run with filtered fields only.
- If live DB cannot be opened read-only, stop implementation and diagnose DB path/permissions first.

## Phase 2 - Health Probe Semantics Repair

Purpose: eliminate the stale `ingest_dead` false blocker after the forecast-live split.

Current design drift:

- `scripts/live_health_probe.py:49-53` hard-codes process liveness as `src.main`, `src.ingest_main`, and `src.riskguard`.
- `docs/operations/task_2026-05-14_data_daemon_live_efficiency/FORECAST_LIVE_OPERATOR_HANDOFF.md` defines `com.zeus.forecast-live` and its jobs as the forecast owner.
- `scripts/check_data_pipeline_live_e2e.py:134-144` already recognizes `src.ingest.forecast_live_daemon` as a first-class owner.

Implementation intent:

1. Add a relationship test that simulates:
   - forecast-live process present,
   - legacy `src.ingest_main` absent,
   - forecast-live heartbeat fresh,
   - data reader healthy.
2. The test must assert no `ingest_dead` alert is emitted in that condition.
3. Update `scripts/live_health_probe.py` to classify forecast ingestion by owner:
   - `forecast_live_dead` only when the forecast-live owner/heartbeat is absent or stale.
   - `legacy_ingest_absent` is not an alert when forecast-live is owner.
   - `legacy_ingest_opendata_owner_present` is an alert if legacy ingest still owns OpenData.
4. Preserve riskguard and main daemon checks.

Expected verification:

```bash
python3 -m pytest tests/test_live_health_probe.py tests/test_check_data_pipeline_live_e2e.py
python3 scripts/live_health_probe.py
```

Pass criteria:

- Health probe no longer reports a false `ingest_dead` when forecast-live is healthy.
- It still reports an actionable alert when neither forecast-live nor legacy ingest can own forecast data.

## Phase 3 - Forecast-Live Deployment and Restart Proof

Purpose: prove the running forecast producer is the intended code.

Deployment/restart sequence:

1. Ensure branch is clean and committed.
2. Start or restart `com.zeus.forecast-live` from the intended worktree/commit.
3. Verify PID changed or start time is after deployment.
4. Verify cwd and module are correct.
5. Wait for startup catch-up or the next scheduled job.
6. Check forecast-live heartbeat and DB progress.

Evidence commands:

```bash
launchctl kickstart -k "gui/$(id -u)/com.zeus.forecast-live"
launchctl print "gui/$(id -u)/com.zeus.forecast-live" | sed -n '/program = /p;/working directory = /p;/pid = /p;/last exit code = /p'
python3 scripts/check_data_pipeline_live_e2e.py --json --live
```

Pass criteria:

- Exactly one forecast-live owner.
- No legacy OpenData owner.
- Forecast heartbeat file updated after restart.
- Latest source_run/coverage/readiness rows have timestamps after restart, or the evidence explains why no new upstream release was due and proves the previous latest release remains unexpired.
- Reader elapsed time is recorded in milliseconds. Target budget is empirical: baseline current reader was sub-millisecond; regression threshold is `p95 <= 5 ms` for local DB read in a 30-run loop unless hardware contention is recorded.

Failure handling:

- HTTP 429, source fetch failure, or incomplete source_run is not patched at the caller. Root-cause into rate limiter, source order, release-calendar timing, and job_run state.
- A missed-window source_run must create durable job/source state rather than silent success.

## Phase 4 - Live Daemon Restart and Reader Consumption

Purpose: prove `src.main` is running the intended code and consuming the current forecast DB.

Sequence:

1. Restart `com.zeus.live-trading` after forecast-live is healthy.
2. Verify PID/start time/cwd/commit.
3. Run the data verifier against the live root.
4. Inspect status summary and entry capability.

Evidence commands:

```bash
launchctl kickstart -k "gui/$(id -u)/com.zeus.live-trading"
launchctl print "gui/$(id -u)/com.zeus.live-trading" | sed -n '/program = /p;/working directory = /p;/pid = /p;/last exit code = /p'
python3 scripts/check_daemon_heartbeat.py
python3 scripts/check_data_pipeline_live_e2e.py --json --live
python3 scripts/live_health_probe.py
```

Pass criteria:

- `src.main` heartbeat is fresh.
- Live reader uses the same latest source_run/release_key selected by producer evidence.
- Entry readiness is typed: authorized, blocked with named reason, or degraded with source evidence.
- No blocker is reported only because a retired process name is absent.

## Phase 5 - Entry Gate and Intent Root Cause

Purpose: resolve `entry=requires_intent` without bypassing governance.

Investigation surfaces:

- `state/status_summary.json` for `execution_capability.entry`.
- `cycle.block_registry` for active blockers.
- `state/entry_forecast_promotion_evidence.json` and activation evidence if rollout gate is active.
- `docs/runbooks/live-operation.md:167-224` for flag order and fail-closed behavior.
- `tests/test_activation_flag_combinations.py` for relationship coverage.

Possible root categories:

1. Missing operator/promotion evidence.
2. Readiness writer missing or stale.
3. Rollout gate active with stale evidence.
4. Calibration gate or canary evidence failure.
5. Risk level not GREEN.
6. No executable candidate after evaluator filters.
7. Market discovery or mode excludes all current contracts.

Repair rule:

- For every blocker category, add or update a relationship test first.
- The fix must make the category impossible to misreport. Example: if a stale evidence file causes `requires_intent`, the status must name the stale evidence path and age, not collapse into an ambiguous string.

Pass criteria:

- `execution_capability.entry.status` is either submit-capable or a precise typed blocker.
- If submit-capable, `global_allow_submit=true` and `live_action_authorized=true`.
- If still blocked, the blocker is backed by a failing test and a targeted implementation plan.

## Phase 5A - pUSD Allowance, Signature Type, and Funder Root Cause

Purpose: resolve the current live blocker without weakening the collateral gate.

Observed live evidence after deploying commit `083801b7d0`:

- `scripts/check_data_pipeline_live_e2e.py --json --live` passes: forecast-live is the single owner, the live reader consumes `ecmwf_open_data:mn2t6_low:2026-05-15T00Z`, and reader latency is sub-millisecond.
- `scripts/check_live_order_e2e.py --json` fails with `venue_commands row not found`.
- The latest real live cycle reached `final_execution_intent_built` and `executable_snapshot_repriced`, then rejected one Karachi candidate with:
  `execution_intent_rejected:pusd_allowance_insufficient: required_micro=3199600 available_allowance_micro=0 allowance_micro=0`.
- `collateral_ledger_snapshots` show repeated fresh `authority_tier='CHAIN'` rows with `pusd_balance_micro=199396602` and `pusd_allowance_micro=0`.
- Direct SDK read of `get_balance_allowance(COLLATERAL)` returns balance but no allowance field; the adapter maps absent allowance to zero.
- The active V2 adapter constructs `ClobClient(... signature_type=2, funder=<keychain funder>)`, while Polymarket CLOB V2 deposit-wallet documentation requires deposit-wallet orders and balance cache sync with `signature_type=3` / `POLY_1271` and the deposit wallet as funder.
- Follow-up read-only chain proof shows the current keychain funder is a contract address with pUSD balance `199396602` micro and max ERC20 allowance to both CLOB V2 exchange spenders. Under `signature_type=3`, the same funder view has zero CLOB pUSD balance. Therefore the immediate live blocker is not "switch to 3"; it is that CLOB balance/allowance omits `allowance`, and Zeus treated that missing field as zero despite chain allowance being sufficient.

Current structural failure class:

`wallet/funder/signature semantics -> CLOB balance allowance truth -> CollateralLedger snapshot -> executor pre-submit gate`

This is not a forecast-data failure and not a passive-limit intent failure. It is an account-authority boundary failure. Zeus must not paper over it by treating pUSD balance as spendable without allowance.

Required diagnosis before code changes:

1. Confirm the canonical funder address class: EOA, legacy proxy, Gnosis safe, or CLOB V2 deposit wallet.
2. Confirm the configured signature type that matches that funder class.
3. Confirm whether CLOB `/balance-allowance` returns nonzero allowance after using the matching signature type.
4. Confirm whether `update_balance_allowance(COLLATERAL)` is a read/cache-sync call only, or whether any missing approval requires a separate on-chain/deposit-wallet approval batch.
5. Confirm that no raw secret or unredacted launchd environment dump enters committed evidence.

Implementation rule:

- Make signature type an explicit adapter setting sourced from a single config/env surface. The current live keychain funder defaults to `signature_type=2`; a future deposit-wallet migration must explicitly set `POLYMARKET_CLOB_V2_SIGNATURE_TYPE=3` together with the funded deposit-wallet funder.
- If CLOB balance/allowance omits `allowance`, read ERC20 allowance from chain for the configured funder and the CLOB V2 exchange spenders. If chain read fails, preserve fail-closed zero allowance.
- Add relationship tests that prove:
  - `PolymarketV2Adapter` passes the configured signature type into `ClobClient`.
  - balance/allowance reads and update calls use the same configured signature type.
  - missing CLOB allowance falls back to chain ERC20 allowance when available.
  - a collateral payload missing `allowance` does not silently become a live-ready spend authorization unless the documented account type says allowance is not required. Current assumption: missing allowance is zero and fail-closed.
  - the executor still blocks before `venue_commands` when pUSD allowance is below notional.
- If the live wallet truly has allowance zero after the correct signature type and balance-cache update, stop code changes and route through the designed operator/on-chain approval path. Do not fabricate approvals, bypass preflight, or insert commands manually.

Pass criteria:

- The live adapter reports the configured signature type and funder class in sanitized evidence.
- A fresh `CollateralLedger` snapshot after restart has `authority_tier='CHAIN'`, fresh `captured_at`, enough `pusd_balance_micro`, and enough `pusd_allowance_micro` for the candidate notional, or a named external approval blocker is recorded.
- Only after the above can Phase 6/7 retry a real live submit.

### Phase 5F - Q1 Egress Preflight Authority Repair

Live cycle 605 proved the evaluator/executor reached a real submit attempt, but the order was rejected before venue submission:

`v2_preflight_failed: Q1_EGRESS_EVIDENCE_ABSENT: missing Q1 egress evidence: docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence/q1_zeus_egress_2026-04-26.txt`

Root cause:

- The Q1 egress evidence file was introduced in commit `31615be` and deleted in commit `6535f50` during workspace cleanup.
- `PolymarketV2Adapter.DEFAULT_Q1_EGRESS_EVIDENCE` still pointed at that deleted packet path.
- The live preflight therefore depended on a stale historical packet path instead of a current live-control evidence surface.

Repair rule:

- Do not restore the old April packet file.
- Move the default Q1 evidence surface to `docs/operations/live_egress/q1_zeus_egress_current.txt`.
- Keep fail-closed behavior when the configured evidence file is absent.
- Add `POLYMARKET_CLOB_V2_Q1_EGRESS_EVIDENCE` as an explicit operator override for equivalent current evidence.
- Validate Q1 evidence content before SDK contact; arbitrary existing files and archived April packet paths must fail closed.
- Verify with relationship tests that the default no longer points at the archived packet, that the default file is tracked and present, that the live client threads the operator override into the adapter, and that invalid/stale evidence paths do not contact the SDK.

## Phase 6 - Candidate and Final Intent Proof

Purpose: prove the evaluator produces an orderable decision from real data.

Evidence to capture from live cycle:

- Market/contract identity: market_id, condition_id, token_id, YES/NO side.
- Strategy key.
- Forecast source_run/release_key consumed.
- P_raw, P_cal, market fusion, P_posterior.
- Edge and double-bootstrap CI.
- Kelly/fractional sizing.
- FinalExecutionIntent fields: snapshot_id, snapshot_hash, selected_token_id, final_limit_price, submitted_shares, order_type, cancel_after, decision source context.
- Correlation fields: cycle start time or cycle id if present, decision_id/hypothesis_id, decision_snapshot_id, snapshot_id, snapshot_hash, cost_basis_id/hash, correlation_key, idempotency_key, and later command_id.

Pass criteria:

- Candidate originates in the normal live evaluator path.
- Snapshot identity survives into `FinalExecutionIntent`; executor validates snapshot hash/token/tick/min-order identity (`src/execution/executor.py:1213-1273`).
- No manual candidate injection, fake venue, or test-double path is used for live proof.
- `FinalExecutionIntent` was created by the live cycle path that attaches corrected pricing authority (`src/engine/cycle_runtime.py:355-525`) and submitted through `execute_final_intent` (`src/execution/executor.py:1343-1393`) or a documented current entry seam with equivalent traceability.

If no candidate is selected:

- Do not force a trade.
- Diagnose the first zeroing stage in the funnel.
- If the system is correctly finding no positive EV trade, record that and continue guard until an expected opportunity arises.
- If the zeroing is due to data/readiness/risk/config defect, repair that defect with tests and re-run.

## Phase 7 - Real Live Submit Proof

Purpose: place the live order through the normal executor path and prove persistence ordering.

This phase may start only after `LIVE_SUBMIT_GO` is recorded.

Expected executor order:

1. Pre-submit gates: cutover, heartbeat, WS gap, risk allocator.
2. Calculate/validate limit order details.
3. Insert `venue_commands`.
4. Append `SUBMIT_REQUESTED`.
5. Call `client.place_limit_order`.
6. Append `SUBMIT_ACKED`, `SUBMIT_REJECTED`, or `SUBMIT_UNKNOWN` / `REVIEW_REQUIRED`.

Key code references:

- `src/execution/executor.py:58-93` for submit guards.
- `src/execution/executor.py:642-784` for pre-submit envelope persistence.
- `src/execution/executor.py:2252-2267` for the `_live_order` side-effect sequence.
- `src/state/venue_command_repo.py:4-15` for command/event journal ownership.
- `src/state/venue_command_repo.py:50-134` for legal command state transitions.

Required correlation trace:

```text
live process pid/start_time/worktree/commit
  -> cycle started_at / mode / discovery surface
  -> evaluator decision_id or hypothesis_id
  -> decision_snapshot_id
  -> FinalExecutionIntent snapshot_id + snapshot_hash + cost_basis_hash + correlation_key
  -> execute_final_intent decision_id argument
  -> venue_commands.command_id + decision_id + idempotency_key + snapshot_id
  -> venue_command_events SUBMIT_REQUESTED payload
  -> venue_submission_envelopes pre-submit envelope hash
  -> venue acked/resting order id
  -> venue_order_facts / status projection / reconciliation evidence
```

If any link is absent, the result is not end-to-end proof. The repair is to add the missing relationship evidence or fail-closed trace, not to infer lineage from timestamps alone.

Evidence queries:

```sql
SELECT command_id, state, side, token_id, limit_price, size, snapshot_id,
       idempotency_key, created_at, updated_at
FROM venue_commands
ORDER BY created_at DESC
LIMIT 10;

SELECT command_id, event_type, state_after, occurred_at, payload_json
FROM venue_command_events
WHERE command_id = :command_id
ORDER BY occurred_at ASC;

SELECT envelope_id, command_id, captured_at, order_id, raw_request_hash,
       raw_response_json IS NOT NULL AS has_response
FROM venue_submission_envelopes
WHERE command_id = :command_id
ORDER BY captured_at ASC;
```

Pass criteria:

- `venue_commands.created_at` precedes SDK side-effect evidence.
- Event sequence is legal under `venue_command_repo` transitions.
- An accepted live order contains a venue order id or equivalent SDK order identity and reaches an accepted/resting/open-order state.
- Rejected/unknown outcomes are durable blocker evidence only. They prove the path reached a gateway failure boundary, not that Zeus placed an expected live order. The operation continues with root-cause repair and another gated submit attempt.

## Phase 8 - Order, Fill, Position, and Chain Reconciliation Proof

Purpose: prove the order outcome is visible through Zeus's designed truth chain.

For accepted/resting order:

- Verify open order fact or equivalent command state.
- Verify derived status shows pending/open order state.
- Verify no local position is invented before fill.

For partial/full fill:

- Verify `venue_trade_facts`.
- Verify `position_events` append and `position_current` projection in one transaction boundary.
- Verify lifecycle phase is legal.
- Verify chain reconciliation sees SYNCED or a typed unknown/quarantine state, never silent void from stale chain data.

Evidence queries:

```sql
SELECT * FROM venue_order_facts
WHERE command_id = :command_id
ORDER BY observed_at DESC
LIMIT 10;

SELECT * FROM venue_trade_facts
WHERE command_id = :command_id
ORDER BY observed_at DESC
LIMIT 10;

SELECT * FROM position_events
WHERE source_command_id = :command_id
ORDER BY created_at ASC;

SELECT * FROM position_current
WHERE source_command_id = :command_id OR entry_command_id = :command_id;
```

Pass criteria:

- Resting order: command and order facts agree; no false position.
- Fill: trade facts, position events, and projection agree.
- Unknown venue response: command enters review/unknown path; guard continues until resolved.

## Phase 9 - Post-Order Guard

Purpose: keep the system watching after submit, because a single ack does not prove lifecycle health.

Guard interval:

- Every 5 minutes for the first 30 minutes after submit.
- Every 15 minutes until order terminal state, fill, cancellation, expiry, or manual closeout.
- Continue at the normal monitoring cadence for filled positions.

Guard checks:

- daemon heartbeat.
- forecast-live heartbeat.
- riskguard heartbeat and risk level.
- order command state.
- venue order facts and fills.
- position lifecycle/projection.
- chain reconciliation status.
- status_summary and monitoring/exit surfaces.

Completion categories:

- `LIVE_ORDER_SUBMITTED`: live order reached venue and was durably acked/resting.
- `LIVE_ORDER_REJECTED_RECORDED`: live gateway path worked but venue rejected; rejection is durable and root cause is known.
- `LIVE_ORDER_FILLED`: fill observed and position state recorded.
- `LIVE_LIFECYCLE_TERMINAL`: position/order reached designed terminal state.

Only `LIVE_ORDER_SUBMITTED` or stronger satisfies the user's "live actually placed an order" condition. Only `LIVE_LIFECYCLE_TERMINAL` satisfies a full lifecycle completion claim.

## Phase 10 - Critic Gates

Run critic after each major transition:

1. After plan landing.
2. After health/readiness implementation repair.
3. Before live restart.
4. Before first live submit attempt.
5. After submit evidence bundle.

Critic questions:

- Did the plan/fix add complexity without making a failure class impossible?
- Is every completion claim backed by live evidence rather than shadow/dry-run evidence?
- Is any side effect path outside `venue_command_repo` / executor / V2 adapter?
- Does any derived JSON/status file outrank canonical DB/event truth?
- Are timing claims measured after deployment and tied to the deployed PID/commit?

Approval state must be `APPROVE`. `REVISE` is unresolved.

## Phase 5B Live Blocker: uint256 Allowance vs SQLite INTEGER

After the Phase 5A allowance-authority fix was deployed to the live root, the
live daemon reached the CLOB V2 collateral read with `signature_type=2`, derived
the API key, synchronized CLOB balance/allowance cache, and recovered pUSD
allowance from Polygon ERC20 chain truth. The next boundary failed:

`FAIL-CLOSED: wallet query failed at daemon start: Python int too large to convert to SQLite INTEGER`

Root cause:

- ERC20 allowance is `uint256`; the user's current funder has max allowance to
  the Polymarket V2 spender contracts.
- `collateral_ledger_snapshots.pusd_allowance_micro` is SQLite `INTEGER`
  signed int64.
- The ledger tried to persist max uint256 directly as the canonical snapshot
  allowance, so SQLite rejected the value before live startup could complete.

Structural fix:

- Keep the collateral gate fail-closed and do not bypass `CollateralLedger`.
- Normalize any non-negative pUSD/pUSD-legacy micro value entering SQLite to the
  maximum storable signed int64 (`2^63 - 1`).
- Treat this capped value as the ledger-domain proof that allowance is enough
  for any pUSD spend the system can record, while raw payload provenance still
  contributes to `raw_balance_payload_hash`.
- Preserve the existing insufficient-allowance behavior when the real allowance
  is below required notional.

Required antibodies:

- `tests/test_collateral_ledger.py::test_refresh_caps_uint256_allowance_to_sqlite_domain`
- `tests/test_collateral_ledger.py::test_set_snapshot_caps_uint256_allowance_to_sqlite_domain`
- Existing insufficient-allowance test must still fail closed.

Live completion still requires a fresh restart on the deployed commit and a real
command/order proof from `scripts/check_live_order_e2e.py --json`; this Phase 5B
fix only removes the current boot-time collateral persistence blocker.

## Phase 5C Live Blocker: CLOB Zero-Allowance Cache vs Chain Truth

After Phase 5B, live startup succeeded and installed the global
`CollateralLedger`, but a later live collateral refresh wrote
`pusd_allowance_micro=0` while the same wallet's Polygon ERC20 allowance to both
V2 spender contracts remained max uint256.

Root cause:

- CLOB `balance-allowance` can return either a missing allowance field or an
  allowance value of zero for the current contract funder/cache state.
- Phase 5A only used chain fallback when the allowance field was missing.
- A returned zero therefore re-entered the ledger as authoritative enough to
  block live submit, despite chain truth proving spend approval.

Structural fix:

- Treat CLOB collateral allowance as a cache/read surface, not final chain
  authority, when it is missing or zero.
- Query the ERC20 allowance for both Polymarket V2 spender contracts whenever
  CLOB allowance is missing or zero.
- Use the conservative minimum spender allowance. If chain truth cannot prove a
  positive allowance, preserve zero/missing and fail closed.

Required antibody:

- `tests/test_v2_adapter.py::test_collateral_payload_rechecks_chain_when_clob_reports_zero_allowance`

Live completion still requires a fresh deployed restart and real command/order
evidence. This phase only removes the false zero-allowance blocker.

## Phase 5D Live Blocker: Stale Collateral Snapshot Before Submit

The latest completed live cycle before the Phase 5C deployment reached
`final_execution_intent_built` and `executable_snapshot_repriced`, then failed
at the execution boundary with:

`execution_intent_rejected:collateral_snapshot_stale: age_seconds=934.5 max_age_seconds=60.0`

Root cause:

- `_startup_wallet_check()` refreshes collateral at daemon boot and installs a
  persistent global `CollateralLedger`.
- `opening_hunt` runs on a schedule. The existing snapshot can age past the
  60-second fail-closed freshness window before a candidate reaches submit.
- `_live_order` correctly checks collateral before command persistence, so the
  stale snapshot blocks command creation and SDK submit.

Structural fix:

- Keep `_live_order`'s command-persistence ordering intact; do not move SDK
  construction or order-side-effect risk before the existing guards.
- Use the already-running venue heartbeat lane to maintain fresh collateral
  truth for the process-wide ledger.
- Refresh the global collateral ledger only when the current snapshot is
  `DEGRADED`, future/invalid, or at least 30 seconds old, and rate-limit refresh
  attempts themselves to at most once per 30 seconds even after a failed refresh
  writes a `DEGRADED` snapshot. This keeps the snapshot inside the 60-second
  submit freshness window without creating a 5-second CLOB balance/allowance
  polling loop during network/429 incidents.
- If refresh fails, leave submit fail-closed through the existing collateral
  preflight semantics.

Required antibodies:

- `tests/test_heartbeat_supervisor.py::test_venue_heartbeat_refreshes_stale_global_collateral`
- `tests/test_heartbeat_supervisor.py::test_venue_heartbeat_skips_recent_global_collateral`
- `tests/test_heartbeat_supervisor.py::test_venue_heartbeat_throttles_degraded_collateral_refresh_attempts`

Live completion still requires a fresh deployed restart and real command/order
evidence. This phase only removes the stale collateral snapshot blocker.

## Phase 5E Live Blocker: Uncommitted Global Ledger Refresh

After Phase 5D deployment, startup produced committed fresh collateral rows, but
heartbeat refreshes did not appear through independent read-only DB checks. The
global `CollateralLedger` owns a persistent SQLite connection and
`_persist_snapshot()` inserted rows without committing them. That can split the
live process's in-memory collateral view from canonical DB truth and from fresh
executor/verifier connections.

Additional authority hardening:

- If CLOB reports `allowance=0` and chain ERC20 allowance verification is
  unavailable, the adapter must not label the payload as `CHAIN`. It should
  produce `DEGRADED` with zero allowance so submit remains fail-closed and the
  zero is not mistaken for chain truth.

Structural fix:

- `CollateralLedger._persist_snapshot()` commits immediately when the ledger
  owns the connection (`db_path=` singleton path); caller-owned connections
  still preserve caller transaction control.
- CLOB-zero plus chain-unavailable collateral payload becomes `DEGRADED`.

Required antibodies:

- `tests/test_collateral_ledger_global_persistent_conn.py::test_owned_persistent_ledger_refresh_commits_for_fresh_readers`
- `tests/test_v2_adapter.py::test_collateral_payload_degrades_when_clob_zero_and_chain_unavailable`

Live completion still requires a fresh deployed restart and real command/order
evidence.

## Implementation Backlog Derived From This Plan

Likely code/test changes:

1. Add `tests/test_live_health_probe_forecast_owner.py`.
2. Update `scripts/live_health_probe.py` to treat forecast-live as the canonical forecast owner.
3. Extend `scripts/check_data_pipeline_live_e2e.py` or add a new `scripts/check_live_order_e2e.py` that reads command/order/position evidence without side effects.
4. Add tests for the live-order evidence checker using temporary SQLite fixtures.
5. If `entry=requires_intent` remains ambiguous, add typed blocker output at the source that constructs `execution_capability.entry`.
6. If no selected candidates are produced despite ready data and open markets, add a relationship test around evaluator funnel attribution before changing selection logic.
7. If the live cycle cannot correlate decision -> final intent -> command -> venue order, add correlation evidence at the narrowest existing boundary rather than inferring from timestamps.
8. Fix the CLOB V2 account-authority boundary if the live funder requires `POLY_1271` / `signature_type=3` instead of the current hardcoded `signature_type=2`.
9. Add a sanitized live allowance verifier that reports balance, allowance, signature type, funder class, and update-cache result without exposing credentials.

Potential files, subject to fresh topology routing before edit:

- `scripts/live_health_probe.py`
- `tests/test_live_health_probe*.py`
- `scripts/check_live_order_e2e.py`
- `tests/test_check_live_order_e2e.py`
- `src/engine/**` only if entry/funnel root cause is there.
- `src/execution/**` only if evidence proves submit persistence ordering is broken.
- `src/state/**` only with K0/K1 planning-lock evidence if command/position truth ownership is touched.
- `src/venue/polymarket_v2_adapter.py` and `src/data/polymarket_client.py` only for the CLOB V2 signature/funder/allowance authority slice.
- `tests/test_v2_adapter.py` and `tests/test_collateral_ledger.py` for the signature/allowance relationship antibodies.

Required topology and registry checks for additions:

- New scripts under `scripts/**` need file-header provenance and `architecture/script_manifest.yaml` registration.
- New tests under `tests/test_*.py` need file-header provenance and `architecture/test_topology.yaml` registration.
- Source edits require scoped `AGENTS.md` reads, topology navigation for the exact slice, planning-lock where triggered, and focused relationship tests before implementation.
- High-risk `src/execution/**`, `src/state/**`, or `src/engine/**` edits require critic approval before live restart and before live submit.
- Before commit, run `git diff --check`, `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence ...`, and `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files ...`.

## Verification Matrix

| Layer | Proof | Tool |
| --- | --- | --- |
| Branch isolation | clean branch from `origin/main` | `git status`, `git rev-parse` |
| Topology admission | plan path, registry, and each source/test/script slice admitted | `scripts/topology_doctor.py --navigation`, planning-lock, map-maintenance |
| Packet freeze | active execution packet recorded before implementation/live mutation | `docs/operations/current_state.md` route + planning-lock |
| Data producer | single owner, fresh source_run/readiness | `check_data_pipeline_live_e2e.py`, SQL |
| Health semantics | no false `ingest_dead` under forecast-live | pytest + live probe |
| Live reader | same latest source_run/release_key | `check_data_pipeline_live_e2e.py --live` |
| Entry gate | authorized or typed blocker | status_summary + tests |
| Candidate | normal evaluator output with correlation fields | status/log/evidence bundle |
| Submit | command row before SDK and accepted/resting order ack | SQL + executor logs + venue/order fact |
| Order state | order facts/projection agree | SQL |
| Fill/position | trade facts + lifecycle projection | SQL + reconciliation |
| Guard | no stale heartbeat/risk/order drift | recurring checks |

## Failure Protocol

When a phase fails:

1. Classify the failing relationship boundary, not just the local symptom.
2. Write or update a relationship test that fails on the current bug.
3. Implement the narrowest structural fix.
4. Run focused tests.
5. Re-run live evidence for the failed boundary.
6. Ask critic to attack the fix.
7. Continue only after `APPROVE`.

Do not accumulate one-off fixes that merely chase the next surfaced problem.

## Evidence Bundle Shape

Use a single timestamped evidence directory per live attempt:

`docs/operations/task_2026-05-15_live_order_e2e_verification/evidence/<YYYYMMDDTHHMMSSZ>/`

Expected files:

- `baseline.md`
- `processes.md`
- `forecast_reader.json`
- `health_probe.txt`
- `entry_gate.json`
- `candidate.json`
- `correlation_trace.md`
- `submit_command.sql.txt`
- `submit_events.sql.txt`
- `order_position_reconciliation.sql.txt`
- `critic_review.md`
- `result.md`

Do not commit raw secrets or full unredacted launchd environment dumps.

## Rollback and Containment

If a deployed repair degrades live health before submit:

- restart previous known-good launchd target or return launchd cwd to clean `main`.
- keep riskguard running.
- block new entries until health and data proof are restored.

If a live order submit creates unknown side effect:

- do not manually invent terminal state.
- preserve command in `SUBMIT_UNKNOWN_SIDE_EFFECT` / `REVIEW_REQUIRED` path.
- reconcile against venue/chain until ack/reject/fill/cancel truth is known.

If risk turns RED:

- obey Zeus risk law: cancel pending and sweep active positions as designed.

## First Execution Slice

The first implementation slice after this plan is:

1. Commit this packet plan.
2. Run a critic review of this plan.
3. If critic returns `REVISE`, update this plan and re-run critic until `APPROVE`.
4. Freeze this packet through `docs/operations/current_state.md` with planning-lock evidence.
5. Implement the health probe forecast-owner repair and tests.
6. Build read-only live-order evidence checker with correlation-trace validation.
7. Re-run topology/planning-lock/map-maintenance for every source/test/script edit before committing.
8. Record `LIVE_RESTART_GO`, deploy/restart only after tests and critic approval.
9. Record `LIVE_SUBMIT_GO`, then start live verification from Phase 1 and continue until an accepted/resting live order or stronger satisfies the completion definition.
