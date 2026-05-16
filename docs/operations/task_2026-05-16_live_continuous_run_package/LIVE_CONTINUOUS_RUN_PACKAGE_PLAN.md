# Live Continuous Run Package Plan

Created: 2026-05-16  
Branch: `followup/live-continuous-run-package-2026-05-16`  
Base: `origin/main` / `6be2f27b1a5966b098c5f886e9fc506ba0483e65`  
Scope: follow-up live run package after first real live order milestone.  
Mode: plan packet first; implementation requires separate topology admission and live-safe rollout gates.

## 0. Milestone Baseline

The previous milestone is real but incomplete: Zeus has recorded one live order path with command, venue order fact, venue trade fact, and position fill evidence.

Recorded evidence from read-only live DB checks:

- `scripts/check_live_order_e2e.py --json` returned `PASS` / `LIVE_ORDER_FILLED`.
- Latest command: `2f3807c5dc744a32`, state `PARTIAL`, intent `ENTRY`.
- Venue order id: `0x9ad6940952565afb74e4b7ad7ae17e0f8471d125a7211045212a0ef551822a3c`.
- `venue_trade_facts`: one `REST` / `CONFIRMED` fact, trade id `379f2c4f-8311-43c2-b404-d972c5699ccf`.
- `position_events`: includes `ENTRY_ORDER_FILLED` for position `c30f28a5-d4e`.
- `scripts/check_data_pipeline_live_e2e.py --live --json` returned `PASS`; reader probe was `LIVE_ELIGIBLE / EXECUTABLE_FORECAST_READY` with reader latency about `0.512 ms`.

This package is not allowed to reclassify that as final live readiness. The next target is continuous live operation on mainline code.

## 1. Objective

Make the live runtime continuously operable on mainline code after a real fill, with proof that the system can keep evaluating, risk-checking, refreshing data/source health, and writing operational records without crashing, silently drifting branch, or being blocked by non-live DB holders.

A successful package has two possible final verdicts. `LIVE_CONTINUOUS_READY` is the only verdict that means restored continuous live operation. `CONTROLLED_DEGRADED` means the system is alive and fail-closed but not restored.

`LIVE_CONTINUOUS_READY` must prove all of the following in one live run window:

1. The live daemon process is running code whose commit matches the intended mainline commit.
2. `load_portfolio()` can load active filled/partial positions after `CHAIN_SYNCED` and `ENTRY_ORDER_FILLED` without `ExitState` crashes.
3. RiskGuard ticks successfully and does not remain RED due to stale/crashed loader state.
4. Source freshness writer/reader contract is alive and fresh for all live-required sources; stale source gating cannot satisfy `LIVE_CONTINUOUS_READY`.
5. DB write contention is not repeatedly blocking collateral heartbeat or other live-critical writes.
6. `check_live_order_e2e.py` and `check_data_pipeline_live_e2e.py` both pass on the same live DB set.
7. launchd has explicit, deterministic restart behavior for every long-running live process.

## 2. Non-Goals

- Do not place another live order merely to prove this package unless all upstream safety gates are green and the operator separately authorizes a new order attempt.
- Do not mutate live DB rows as a shortcut to hide the `entered` crash.
- Do not kill bridge or daemon processes as a first response; identify ownership and replace unsafe long-lived handles with a structural boundary.
- Do not treat forecast-live success as source-health success. These are separate producers.
- Do not edit the current live worktree during planning. Live runtime can continue to use it until an explicit deployment step switches it.

## 3. Subagent Findings

### 3.1 Semantic-Mix Root Cause: Confirmed

Spark explorer verdict: correct.

Evidence:

- `src/state/db.py:7066`: `query_portfolio_loader_view` emits `exit_state` from `hints.get("exit_state")`.
- `src/state/db.py:7706-7710`: `_query_transitional_position_hints` copies generic `payload.status` into `exit_state` without filtering to exit events.
- `src/state/portfolio.py:1340`: portfolio row loader passes `exit_state` into `Position`.
- `src/state/portfolio.py:442-443`: `Position.__post_init__` coerces to `ExitState`.
- `src/contracts/semantic_types.py:39-47`: `ExitState` has no `entered` member.
- Runtime traceback confirms `ValueError: 'entered' is not a valid ExitState` in both `src.main` cycle and RiskGuard tick.

Additional similar risk:

- `src/state/db.py:6829-6837` / `query_position_current_status_view` also derives an `exit_state` display count from the same generic transitional hints. Even if not crash-critical, it can misreport exit-state counts.

Structural diagnosis:

Generic event payload `status` is a lossy cross-domain field. It currently crosses from lifecycle/chain reconciliation into exit FSM. The fix must make the category impossible by separating event-status domains, not by special-casing `entered`.

### 3.2 Live/Main Code-Plane Drift: Confirmed

Spark explorer verdict: drift present.

Evidence:

- Live worktree command evidence:
  - `HEAD = d0915a8b020bc507eed11ec9b5e5d33ce703b80e`.
  - `origin/main = 6be2f27b1a5966b098c5f886e9fc506ba0483e65`.
  - `git status --short --branch` shows `deploy/live-order-e2e-verification-2026-05-15`, dirty, and ahead of its remote branch.
- Ancestry from explorer: `HEAD` is behind `origin/main`; `origin/main` is not contained by `HEAD`.
- Local leader evidence also showed `origin/main..HEAD` includes deletions or reversions of PR #118 files in the live worktree branch surface.
- `/Users/leofitz/Library/LaunchAgents/com.zeus.live-trading.plist` pins `WorkingDirectory` and `PYTHONPATH` to `/Users/leofitz/.openclaw/workspace-venus/zeus`, not to an immutable release path or verified commit.
- The same plist has `KeepAlive=false`.

Structural diagnosis:

Live runtime is tied to a mutable developer worktree rather than a deployment identity. That lets a merged PR and the running daemon prove different code. There is no mandatory runtime-commit attestation gate in the live health evidence.

### 3.3 DB Lock and Source Health Degradation: Confirmed Partial Degradation

Spark explorer verdict: continuity partially degraded, not fully blocked.

Evidence:

- `zeus_trades.db`, WAL, and SHM are open by `src.main` and two long-lived `gyoshu_bridge.py` processes, PIDs `47893` and `48522`, PPID `79876`.
- `zeus-live.err` repeatedly logs `CollateralLedger heartbeat refresh failed closed: database is locked`.
- `state/source_health.json` is valid JSON but `written_at` is `2026-05-15T08:25:56.780680+00:00`, roughly a day stale at the time of investigation.
- `src/control/freshness_gate.py` reads and evaluates this artifact; it does not write it.
- `src/ingest_main.py` / `src/data/source_health_probe.py` own the source-health writer path via `acquire_lock("source_health")`.
- `src/ingest/forecast_live_daemon.py` owns forecast writes and does not refresh `source_health.json`.

Structural diagnosis:

DB lock is currently handled as degraded closed-fail for heartbeat writes, not as process crash. Source health has a decoupled writer/reader contract; forecast-live freshness is not a substitute for source-health freshness. The current operator surface does not distinguish these enough for a continuous-run readiness claim.

## 4. Root Causes

### RC1: Event payload status lacks a typed domain

The same JSON key, `status`, is reused for lifecycle, chain reconciliation, venue terminal state, and exit lifecycle meanings. The loader reads it as exit FSM state. This violates the money-path principle that Module A output must not lose semantic context before Module B consumes it.

### RC2: Runtime identity is path-based, not commit-based

launchd starts whatever code is at the mutable worktree path. Git merge status, PR state, and process state are not joined into one proof surface.

### RC3: Live DB access is not isolated to live owners

Non-live bridge processes can hold live DB descriptors for days. Even when SQLite handles this with WAL/shared locks, observed collateral heartbeat writes repeatedly fail closed.

### RC4: Freshness and forecast data are conflated in operator reasoning

The data pipeline verifier proves executable forecast readiness. It does not prove source-health probe freshness for WU/Open-Meteo/day0 sources. Continuous live operation needs both contracts visible.

### RC5: launchd resiliency is inconsistent

forecast-live and riskguard-live have `KeepAlive=true`; live-trading has `KeepAlive=false`. A daemon can appear healthy while running, yet lacks restart continuity.

## 5. Implementation Phases

### Phase A: Baseline and Packet Admission

Deliverables:

- Keep this packet active as the follow-up live run package.
- Record baseline commands in a plan-local section, not as separate evidence files unless a gate consumes them.
- Do not touch live DB.

Required checks:

- `git rev-parse HEAD origin/main` in live worktree and planned implementation worktree.
- `git status --short --branch` in live worktree and package branch.
- `launchctl print gui/$UID/com.zeus.live-trading`.
- `launchctl print gui/$UID/com.zeus.riskguard-live`.
- `launchctl print gui/$UID/com.zeus.forecast-live`.
- `scripts/check_live_order_e2e.py --json` against live DB.
- `scripts/check_data_pipeline_live_e2e.py --live --json`.
- `lsof state/zeus_trades.db state/zeus_trades.db-wal state/zeus_trades.db-shm`.

Exit criteria:

- Baseline identifies current blockers without mutating runtime.

### Phase B: Typed Event-Status Boundary

Files likely touched:

- `src/state/db.py`
- `tests/test_db.py` or focused portfolio-loader tests
- possibly `tests/test_pnl_flow_and_audit.py` or status-summary tests if display semantics change

Design:

- Replace generic `payload.status -> exit_state` mapping with an explicit exit-event-only extraction.
- Only events whose `event_type` is in the exit lifecycle family may provide `exit_state` from payload status.
- Entry/lifecycle/chain events may still provide lifecycle hints such as `entered_at`, `entry_fill_verified`, or `day0_entered_at` through explicitly named fields.
- Reject or ignore non-ExitState values rather than coercing them.
- Apply the same filter to `query_position_current_status_view` so display counts cannot inherit lifecycle statuses as exit states.

Relationship tests:

1. Seed `position_current` active position plus `CHAIN_SYNCED` payload with `status="entered"` and no exit events.
2. `query_portfolio_loader_view()` must return `exit_state=""` and state mapped from phase/lifecycle, not from payload status.
3. `load_portfolio()` / `_position_from_projection_row()` must construct `Position` without exception.
4. `query_position_current_status_view()` must count exit state as `none`, not `entered`.
5. Seed a real exit event with `status="sell_pending"`; loader must preserve `sell_pending`.

Exit criteria:

- RiskGuard no longer throws `ExitState('entered')` on the current live DB snapshot.
- The bug category is guarded by relationship tests across `position_events -> portfolio loader -> RiskGuard`.

### Phase C: Runtime Commit Attestation and Main Alignment

Files likely touched:

- `scripts/live_health_probe.py`
- `src/main.py` or status writer code if runtime heartbeat needs commit metadata
- launch/bindings config if package owns deployment template
- tests for probe output

Design:

- Define a deployment identity invariant: live launchd must point to a clean, expected commit, either through an immutable release worktree/path or through an explicit `ZEUS_LIVE_EXPECTED_COMMIT` gate that fails if the mutable worktree drifts.
- Add a runtime commit identity to heartbeat/status output, sourced from the running repo path.
- Health probe compares runtime commit and branch cleanliness against expected `origin/main` or an explicit `ZEUS_LIVE_EXPECTED_COMMIT`.
- Distinguish `LIVE_CODE_PLANE_DRIFT` from ordinary dirty-worktree warnings.
- Keep runtime check read-only.

Exit criteria:

- A live health check can state `runtime_commit == expected_commit` and `runtime_tree_clean == true`, or else emits `LIVE_CODE_PLANE_DRIFT` and blocks `LIVE_CONTINUOUS_READY`.
- If live worktree is dirty or behind main, the check fails closed for live-ready claims.
- The check does not require stopping the running daemon.

### Phase D: launchd Continuity Contract

Files likely touched:

- launchd template or bindings under `bindings/zeus/**`
- installer script if present
- possibly operator handoff docs

Design:

- Decide and encode deterministic `KeepAlive` policy for `com.zeus.live-trading`.
- If `KeepAlive=true`, include throttle and minimum runtime to avoid hot crash loops.
- If `KeepAlive=false` is intentionally kept, add an explicit external supervisor proof requirement. Current evidence does not show that external supervisor.
- Health probe must report the keepalive policy for live/riskguard/forecast.

Exit criteria:

- `launchctl print` for all three live services matches the package policy.
- A killed or exited non-trading-safe test service path can demonstrate restart semantics before applying live deployment.

### Phase E: Live DB Ownership and Lock Hygiene

Files likely touched:

- health probe / diagnostics first
- OMC bridge integration only if an admitted route exists
- collateral ledger only if it owns long transactions or missing timeout behavior

Design:

- Define a live DB connection policy: live owners may hold write-capable handles; non-live bridges must use read-only short-lived handles or be excluded from canonical live DBs.
- Classify all live DB holders into live owner, read-only observer, or forbidden long-lived bridge.
- Add a probe that reports long-lived non-live holders of `zeus_trades.db*`.
- Do not kill bridge processes in code. Provide an operator-safe cleanup path or make bridge use read-only / short-lived connections when pointed at live DB.
- Verify collateral heartbeat can complete without repeated `database is locked` over a 10-15 minute rolling window.

Exit criteria:

- No repeated `CollateralLedger heartbeat refresh failed closed: database is locked` across at least two expected collateral heartbeat cadences in the acceptance window.
- Live DB holders are explainable and bounded.
- WAL size and checkpoint behavior are not treated as success alone; the acceptance is absence of blocked critical writes.

### Phase F: Source-Health Writer Contract

Files likely touched:

- `src/ingest_main.py`
- `src/data/source_health_probe.py`
- `src/control/freshness_gate.py`
- health probe/status reporting tests

Design:

- Keep `forecast_live_daemon` and `source_health_probe` responsibilities separate.
- Health probe must report both forecast readiness freshness and source-health freshness.
- If source-health writer is not expected to run continuously, encode that as `CONTROLLED_DEGRADED` with explicit disabled lanes; it cannot pass `LIVE_CONTINUOUS_READY`.
- If it is expected to run continuously, ensure launch/scheduler ownership exists and prove fresh writer cadence.

Exit criteria:

- For `LIVE_CONTINUOUS_READY`, `source_health.json` written_at age <= 15 minutes during acceptance and all live-required source budgets are green. The source-health writer is scheduled every 10 minutes; the additional 5 minutes is scheduler/probe-runtime slack, not a stale-data allowance.
- For `CONTROLLED_DEGRADED`, freshness gate may report documented stale/disabled lanes, but the package must explicitly deny live-ready status.
- `Freshness gate STALE` no longer silently blocks day0/live cycle without a corresponding operator-visible reason.

### Phase G: Integrated Live Acceptance Window

Run after Phases B-F are implemented and deployed to the intended mainline runtime.

Minimum window for `LIVE_CONTINUOUS_READY`: at least 30 minutes without manual DB mutations, and at least two successful cadences for every active periodic writer that should run inside the window. If a writer cadence is longer than 30 minutes, the package must include the latest scheduled run evidence and prove it is not due/stale.

Required observations:

- `src.main` process alive, heartbeat fresh.
- CLOB venue heartbeat returns HTTP 200.
- `riskguard-live` tick succeeds at least twice after fix deployment, or once per configured cadence if the cadence is longer than 30 minutes and the schedule is documented.
- `check_live_order_e2e.py --json` remains `PASS / LIVE_ORDER_FILLED` or stronger.
- `check_data_pipeline_live_e2e.py --live --json` remains `PASS`.
- No repeated `database is locked` warnings for collateral heartbeat across at least two expected heartbeat attempts.
- No `ValueError: 'entered' is not a valid ExitState`.
- `source_health.json` freshness is green for `LIVE_CONTINUOUS_READY`; controlled-degraded evidence must be labeled `CONTROLLED_DEGRADED`, not ready.
- runtime commit attestation equals expected mainline commit.

Only after this window may the package claim live continuous-run readiness.

## 6. Verdict Semantics

`LIVE_CONTINUOUS_READY` means all live-required daemons are running the expected clean mainline commit, all live-required freshness writers are fresh, RiskGuard and live cycle can process the post-fill portfolio without crash, and DB write contention is absent across the configured cadences.

`CONTROLLED_DEGRADED` means Zeus is alive and fail-closed with explicit degraded lanes. This can be an acceptable operator state, but it is not live-ready and cannot close this package as restored continuous operation.

A report that says only "degraded safely" fails this package's completion definition.

## 7. Verification Matrix

| Concern | Test / Probe | Pass Condition |
| --- | --- | --- |
| Event status semantic mix | New relationship test | Non-exit `status="entered"` cannot become `exit_state` |
| Exit FSM preservation | New exit-event positive test | Exit event `sell_pending` still survives |
| RiskGuard loader | Targeted RiskGuard/load_portfolio test on fixture | No `ExitState` crash |
| Runtime code plane | Live health probe | Runtime commit equals expected commit and tree clean; drift blocks ready |
| launchd continuity | launchctl probe + template test | KeepAlive policy matches package decision |
| DB lock hygiene | log scan + lsof probe | Non-live holders comply with policy; no repeated critical write lock failures across cadences |
| Source health | freshness probe | writer fresh for ready; controlled degradation is a non-ready verdict |
| Data pipeline | `check_data_pipeline_live_e2e.py --live --json` | PASS |
| Live order proof | `check_live_order_e2e.py --json` | PASS / recorded fill proof |

## 8. Rollback and Safety

- Semantic loader fix rollback: revert source commit; no DB rewrite required.
- Runtime commit attestation rollback: disable claim gate, not live trading safety gates.
- launchd change rollback: unload/load previous plist. Must capture previous plist before deploy.
- DB bridge cleanup rollback: restart bridge with previous mode only after confirming no live DB write lock hazard.
- Source-health scheduler rollback: keep freshness gate conservative; stale source should degrade, not silently become green.

## 9. Worktree and Deployment Discipline

- Implementation happens on `followup/live-continuous-run-package-2026-05-16` or a dedicated child worktree based on it.
- The current live worktree must not be used as the implementation scratchpad while live continues running.
- Deployment to live path requires a separate operator-safe step that proves dirty state is handled and runtime commit is expected.
- Do not remove old worktrees or bridge processes as part of this plan unless a separate cleanup route admits it.

## 10. Completion Definition

This package is complete only when all of the following are true:

1. Plan and critic review are landed.
2. Implementation PR is reviewed and merged.
3. Mainline runtime is deployed or attested as the actual running code plane with a clean expected commit.
4. Continuous live acceptance window passes with the `LIVE_CONTINUOUS_READY` verdict, not `CONTROLLED_DEGRADED`.
5. Evidence shows the prior real order remains recorded and loadable.
6. Evidence shows Zeus can continue a post-fill live cycle without `ExitState('entered')`, stale source health, or repeated DB lock closed-fail.
7. Non-live DB holders are either absent from canonical live DB write paths or proven read-only/short-lived by policy and observation.

Anything less is an intermediate state, not live-ready.
