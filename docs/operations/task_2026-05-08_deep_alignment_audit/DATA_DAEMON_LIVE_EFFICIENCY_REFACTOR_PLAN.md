# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: AGENTS.md money path; docs/operations/AGENTS.md packet evidence rules; topology navigation for data-daemon planning packet; read-only runtime probes on 2026-05-14; src/ingest_main.py; src/data/source_health_probe.py; state/zeus-forecasts.db read-only schema; state/zeus-world.db read-only schema.

# Data Daemon Live Efficiency Refactor Plan

Status: PLAN_APPROVED

This is a planning artifact only. It does not authorize source edits, DB
mutation, daemon restart, launchctl mutation, live venue calls, data cleanup, or
promotion of stale data. Each implementation slice below requires a separate
topology route, planning-lock check where applicable, tests, and critic review.

## 0. Downgrade And Erratum

Prior verdict withdrawn:

- Previous packet-local `APPROVE` language is downgraded to `REVISE`.
- The prior implementation branch is downgraded to `CODE_EXPERIMENT_STALE`,
  not `CODE_READY`, because it exists only in the isolated worktree
  `/Users/leofitz/.openclaw/workspace-venus/zeus-data-daemon-live-efficiency-2026-05-14`
  and is based on `3e13d7e37dfc28a2725dcaab23b2c2e02c377f02`, while the
  current observed runtime checkout was `097e08d946979c41f7f56068f4da594e6f4d5954`
  during the read-only probe.
- The previous critic approval reviewed a local plan/diff shape, not current
  HEAD plus runtime process plus canonical DB readiness. It therefore cannot
  support any `LIVE_RUNNING`, `LIVE_CONSUMING`, or `DONE` claim.
- The old package-specific plan remains useful as historical design material,
  but all active planning must use this revised file and the new critic review.

Why the downgrade is required:

1. The current runtime checkout observed during probing did not contain
   `src/ingest/forecast_live_daemon.py`.
2. Runtime processes included `python -m src.ingest_main` and `python -m
   src.main`, but no `src.ingest.forecast_live_daemon` process.
3. `state/zeus-forecasts.db` had current OpenData snapshot/source_run activity,
   while `source_run_coverage` and `readiness_state` still lived in
   `state/zeus-world.db` and were stale at 2026-05-04/2026-05-05 for the latest
   producer readiness rows.
4. `src/data/source_health_probe.py` treated HTTP 4xx/429 as success because it
   raised only for `status_code >= 500`.
5. The stale implementation branch would collide with current HEAD changes in
   `src/data/ecmwf_open_data.py`, `src/ingest_main.py`, `src/state/db.py`, and
   `src/engine/evaluator.py`; direct cherry-pick or wholesale copy risks
   regressing the forecast DB split and recent live-readiness work.

## 1. Objective

Build a live data daemon architecture that can feed the live trading daemon with
fresh, complete, machine-verifiable forecast readiness at quant-trading
latency, without accumulating another layer of wrappers or duplicate owners.

The first-principles chain is:

`release event -> fetch/acquire -> extract/normalize -> source_run -> source_run_coverage -> producer_readiness -> executable forecast reader -> evaluator/live daemon`

The refactor succeeds only when every edge in this chain is either:

- fresh, complete, and live-eligible with measured latency; or
- explicitly blocked/degraded with a reason that cannot be rendered green.

The refactor fails if it adds a new daemon while the old daemon still schedules
the same OpenData work, if source-health green can coexist with HTTP 429, or if
live can consume snapshots without fresh coverage/readiness.

## 2. Non-Goals

- Do not delete or consolidate duplicate DB files in this packet.
- Do not mutate production/canonical DBs during planning.
- Do not restart `com.zeus.data-ingest`, add launchd plists, or launch new
  long-running daemons before operator authorization.
- Do not change live order/execution/risk behavior from this planning packet.
- Do not make TIGGE/OpenData statistical-equivalence claims.
- Do not refit calibration, rebuild settlement truth, backfill historical
  training data, or touch venue-facing behavior.
- Do not treat `docs/operations/current_data_state.md` or older packet text as
  present-tense proof without fresh read-only probes.

## 3. Current Read-Only Evidence

Observed from the runtime workspace on 2026-05-14:

| Surface | Current fact | Planning implication |
|---|---|---|
| Old implementation worktree | branch `feat/data-daemon-live-efficiency-2026-05-14`, HEAD/base `3e13d7e37dfc28a2725dcaab23b2c2e02c377f02` | Treat as stale experiment; do not merge wholesale. |
| Process list | `python -m src.ingest_main`, `python -m src.main`, and `python -m src.riskguard.riskguard` were running; no `src.ingest.forecast_live_daemon` process | The new daemon was not running in the observed runtime. |
| Observed runtime file presence | `src/ingest/forecast_live_daemon.py` absent in the observed runtime checkout | No runtime launch path was present there. |
| Forecast DB source_run | latest OpenData HIGH `2026-05-14T00Z` was `PARTIAL/PARTIAL`, `source_available_at=2026-05-14T08:05:00Z`, fetched at `2026-05-14T16:55:50Z`, reason `NOT_RELEASED_STEPS=[150..282]` | There was current source_run/snapshot activity, but not live-ready complete coverage. |
| Forecast DB snapshots | `ensemble_snapshots_v2` latest OpenData source_run `ecmwf_open_data:mx2t6_high:2026-05-14T00Z` had 3293 rows, latest fetch time `2026-05-14T16:55:50Z` | Snapshots can be current while readiness is stale/missing. |
| Forecast DB schema | had `source_run` and `ensemble_snapshots_v2`; no `readiness_state`; no `source_run_coverage` | Forecast authority chain is split across DBs. |
| World DB readiness | `readiness_state` had 577 rows; latest computed at `2026-05-04T18:29:23Z`, latest expiry `2026-05-05T18:29:23Z` | Current live producer readiness was expired, so live should block entries. |
| World DB coverage | latest OpenData coverage rows were 2026-05-04/2026-05-03 source runs | Coverage had not followed current 2026-05-14 forecast snapshots. |
| Source health probe code | ECMWF probe raised only for HTTP `>=500` | HTTP 429/403/client-throttle could be reported as success. |

## 4. Root Cause

The root cause is not one missing daemon, one failed import, or one retry bug.
It is the absence of a machine-enforced completion contract across five
boundaries:

1. **Branch-to-runtime boundary**: source changes in a side worktree were treated
   as progress without proving they existed on the runtime checkout/head.
2. **Scheduler-owner boundary**: adding a forecast daemon did not mechanically
   remove or disable OpenData scheduling in `src.ingest_main`.
3. **Authority-chain boundary**: snapshots, source runs, coverage, and readiness
   can reside in different DBs and ages, so live eligibility is not atomic.
4. **Health-semantics boundary**: source reachability, source-run success,
   readiness freshness, process liveness, and evaluator readiness are different
   facts but are easy to collapse into one green/red bit.
5. **Approval boundary**: critic approval reviewed prose and stub tests, not
   current HEAD, runtime liveness, DB freshness, and live consumption evidence.

This is why repeated local patches make the system worse: each patch repairs a
symptom at one boundary while leaving the relationship between modules
unencoded. The fix must convert those relationships into tests, route gates, and
status states that future agents cannot skip.

## 5. Completion State Machine

No future report may use `complete`, `done`, or `fixed` without naming the
highest state reached:

| State | Meaning | Required proof |
|---|---|---|
| `PLAN_APPROVED` | Critic approved this plan only | This file plus approved critic review. |
| `CODE_READY_ON_HEAD` | Source changes exist on the target head/worktree and tests pass | Fresh branch from target head, no stale-base gap, unit/relationship tests pass. |
| `STAGED_SMOKE_PASS` | Daemon path works against temp/staging DBs | Dry-run/once smoke, no production DB mutation. |
| `OPERATOR_LAUNCH_READY` | Launch/runbook is ready but not applied | Launch plan, rollback, verifier, explicit operator-go still pending. |
| `LIVE_RUNNING` | New daemon process is actually running | Process/launchd/heartbeat instance evidence from current runtime. |
| `PRODUCER_READY` | Fresh source_run/coverage/readiness is live-eligible | DB freshness/count checks with non-expired readiness. |
| `LIVE_CONSUMING` | Live evaluator consumes the new readiness path and blockers clear | Live logs/status show no producer-readiness blockers for eligible candidates. |
| `DONE` | All above plus rollback and monitoring evidence | Complete evidence bundle; critic final approval. |

The prior work reached at most `PLAN_DRAFT_WITH_STALE_CODE_EXPERIMENT`; it did
not reach `CODE_READY_ON_HEAD`.

## 6. Target Architecture

### 6.1 Process Ownership

| Process | Writes allowed | Work allowed | Work forbidden |
|---|---|---|---|
| `forecast-live-daemon` | Forecast authority chain only | ECMWF OpenData HIGH/LOW, startup latest-cycle catch-up, source_run, coverage, producer readiness, forecast heartbeat | TIGGE, calibration refit, observations, settlement/UMA, market scan, execution, generic status rollup |
| `world-maintenance-daemon` or legacy `ingest_main` in world mode | World/observation/source-maintenance tables only | observations, source/station probes, settlement/UMA, harvester truth, world maintenance | OpenData live entry production once forecast-live owner is active |
| `ops-observability-daemon` | File/projection only by default | liveness, read-only status projections, metrics | canonical DB writes, source production |
| `offline-training-runner` | staging DB/artifacts first | TIGGE, historical backfill, replay, calibration rebuild/refit | live scheduler co-tenancy, direct live readiness writes |
| live trading daemon | trading/runtime surfaces only | consume executable forecasts/readiness, evaluate, execute, monitor | fetch forecast data, manufacture producer readiness |

### 6.2 Single Forecast Owner

Introduce one owner switch, for example `ZEUS_FORECAST_LIVE_OWNER` with values:

- `ingest_main`: legacy/default until cutover.
- `forecast_live`: `forecast-live-daemon` owns OpenData HIGH/LOW and startup
  catch-up; `ingest_main` must not register those jobs.

The exact config name may change during implementation, but the invariant must
not: under one launch mode, exactly one process may schedule OpenData live
forecast production.

### 6.3 Forecast Authority Chain

Preferred ownership decision:

- `state/zeus-forecasts.db` should own `source_run`,
  `ensemble_snapshots_v2`, `source_run_coverage`, and producer
  `readiness_state` for forecast/live-entry readiness.
- `state/zeus-world.db` may consume projections, but it must not be the only
  place where readiness exists while current snapshots/source_run live in the
  forecasts DB.

Rejected default alternative:

- Cross-DB live eligibility by attaching world and forecasts DBs in the hot
  path. This remains possible only if lock ordering, crash recovery, and reader
  consistency are explicitly tested. It is lower preference because it preserves
  the split authority failure mode.

### 6.4 HTTP 429 And Source Health Semantics

Source health states must be:

- `OK`: verified success by a source probe or source-run acquisition.
- `THROTTLED`: HTTP 429 or equivalent source quota/rate-limit signal.
- `CLIENT_BLOCKED`: HTTP 401/403/404/410 or client-side source rejection.
- `UPSTREAM_ERROR`: HTTP 5xx.
- `DEPENDENCY_MISSING`: local dependency/import/extractor unavailable.
- `NETWORK_ERROR`: timeout/DNS/TLS/connectivity failure.
- `UNKNOWN`: no current evidence.

Only `OK` may render green. HTTP 429 is never success.

## 7. Efficiency SLOs

These thresholds are exact acceptance targets for the implementation branch.
They may only be revised by a fresh baseline document plus critic approval.

| Metric | Target |
|---|---:|
| Forecast daemon heartbeat interval | <= 30 seconds |
| Heartbeat stale threshold | 90 seconds |
| Process start to first heartbeat | <= 10 seconds |
| Release planner detects due OpenData work after planned `source_available_at` | <= 60 seconds |
| Source probe classifies HTTP 429/4xx/5xx | within one probe timeout, <= 15 seconds |
| Throttled/not-released cycle produces explicit blocked source_run status | <= 180 seconds from work item start |
| Complete cycle authority materialization after all required artifacts are available | <= 600 seconds |
| SQLite write lock hold per authority transaction | <= 5 seconds |
| Total forecast authority write time per source_run | <= 30 seconds |
| Status projection freshness after source_run update | <= 30 seconds |
| Live evaluator consumes fresh readiness or emits blocker reason after cycle | <= 60 seconds |

Current evidence violates these targets: a 2026-05-14 OpenData HIGH source_run
with `source_available_at=08:05Z` was fetched/imported around `16:55Z`, and
producer readiness in world DB remained expired from 2026-05-05.

## 8. Implementation Phases

### Phase 0 - Packet Repair And Critic Gate

Goal: make the planning surface honest before implementation.

Actions:

1. Mark prior approval as withdrawn/superseded.
2. Keep this package-specific plan as the active data-daemon plan; do not use
   the generic deep-audit `PLAN.md` as the implementation plan.
3. Run independent critic review against this revised plan.
4. Update `docs/operations/AGENTS.md` registration for this package-specific
   plan and critic file.

Exit gate:

- New critic verdict is `APPROVE`; otherwise this plan remains `REVISE`.

### Phase 1 - Fresh Implementation Branch From Current Head

Goal: prevent stale-branch regression.

Actions:

1. Create a new branch/worktree from the latest target head.
2. Treat old `feat/data-daemon-live-efficiency-2026-05-14` as a reference only.
3. Port ideas manually by file, preserving current forecasts DB split and recent
   `src/data/ecmwf_open_data.py`, `src/ingest_main.py`, `src/state/db.py`, and
   evaluator changes.
4. Add a stale-base guard to the implementation checklist: no final claim if
   `merge-base(implementation, target_head) != target_head` or if target head
   advanced without rebase/merge review.

Exit gate:

- `git merge-base` and `git diff --stat target..branch` evidence in the packet.

### Phase 2 - Relationship Tests Before Source Edits

Goal: make the broken categories fail before implementation.

Required tests:

1. Forecast owner mode: when owner is `forecast_live`, `ingest_main` does not
   register OpenData HIGH/LOW daily jobs or startup catch-up.
2. Forecast daemon scheduler: registers exactly the OpenData HIGH/LOW/live
   catch-up jobs with expected executors, timing, max instances, and misfire
   policy.
3. Source health: HTTP 429/403/timeout/dependency failure produce degraded or
   failed status, not success.
4. Authority chain success: complete source_run writes snapshots, coverage, and
   non-expired readiness in the selected owner DB.
5. Authority chain failure: failed/partial source_run cannot produce
   `LIVE_ELIGIBLE` readiness and cannot leave live-visible snapshots without a
   blocked/quarantined reason.
6. Readiness expiry: expired producer readiness blocks live evaluator input.
7. Status projection: source health, source_run freshness, coverage readiness,
   and live entry readiness are rendered separately.
8. Fast-lane guard: observability/heartbeat jobs cannot open canonical write DB
   connections.

Exit gate:

- Tests are committed before implementation or are included in the same slice
  with clear red/green evidence.

### Phase 3 - Fix Source Health And Dependency Preflight

Goal: stop false green and fail before the scheduler hides dependency failures.

Actions:

1. Replace `status_code >= 500` probe semantics with explicit source-health
   classification.
2. Add daemon boot preflight for `ecmwf.opendata` import, GRIB/extraction
   runtime, forecast DB schema, writable cache, and writer lock short test.
3. Boot preflight failure writes `BOOT_PREFLIGHT_FAILED` or equivalent local
   liveness state and does not register normal ingest jobs.
4. Bind source-health projection to real source-run outcomes where possible so
   a failing real acquisition outranks a shallow HEAD probe.

Exit gate:

- 429 and missing dependency tests fail on old behavior and pass after fix.

### Phase 4 - Implement Single Forecast Owner

Goal: prevent duplicate OpenData scheduling.

Actions:

1. Add the owner switch in one small, testable config/helper surface.
2. Modify `src.ingest_main` job registration so OpenData HIGH/LOW and startup
   catch-up are absent when forecast-live owner is active.
3. Add `forecast-live-daemon` entry point on current head with no imports from
   execution/trading/risk/evaluator code.
4. Preserve a bounded legacy mode until operator cutover.

Exit gate:

- Job-list tests prove exactly one owner under each mode.
- Static import tests prove forecast-live daemon cannot trade or evaluate.

### Phase 5 - Forecast Authority Writer

Goal: make source_run, snapshots, coverage, and readiness atomic enough for
live.

Actions:

1. Decide and implement forecast authority ownership, preferably in
   `zeus-forecasts.db`.
2. Add or move `source_run_coverage` and producer `readiness_state` ownership
   for forecast/live-entry readiness.
3. Introduce one authority writer/service that consumes normalized forecast
   batches and writes source_run, snapshots, coverage, and readiness in one
   transaction or an explicit two-phase blocked state.
4. Add reader policy so snapshots linked to failed/partial source_runs cannot
   become executable forecasts.

Exit gate:

- Complete run cannot lack coverage/readiness.
- Failed/partial run cannot be live eligible.
- Reader tests prove stale/partial/expired rows block.

### Phase 6 - Event-Driven Release Work Queue

Goal: reduce release-to-readiness latency without fighting APScheduler backlog.

Actions:

1. Add durable work items keyed by source, track, cycle, release key, horizon
   profile, and data_version.
2. Prioritize live tradable horizons before catch-up/backfill.
3. Acquire/cache artifacts outside DB write locks.
4. Allow HIGH/LOW acquisition concurrency while serializing authority writes per
   owner DB.
5. Make every work item idempotent.

Exit gate:

- Dry-run/staged evidence reports due, running, completed, blocked, throttled,
   and dependency-failed work items with timestamps.

### Phase 7 - Live Wiring Plan, Not Auto-Launch

Goal: make deployment explicit and reversible.

Actions:

1. Add launch/runbook material for `forecast-live-daemon` only after code tests
   pass.
2. Define old daemon compatibility mode: `ingest_main` remains world/legacy
   owner or disables OpenData when forecast-live owner is active.
3. Define rollback: stop forecast-live daemon, set owner back to `ingest_main`,
   verify old OpenData registration, clear only runtime-local heartbeat if
   needed.
4. Add operator checklist for process, heartbeat, source_run, coverage,
   readiness, and live blocker checks.

Exit gate:

- No launchctl mutation or canonical DB write without explicit operator-go.

### Phase 8 - Staged End-To-End Verification

Goal: prove the chain before live claims.

Stages:

1. Unit and relationship tests.
2. Temp DB smoke with synthetic complete/partial/429/missing-dependency cases.
3. Read-only production probe: report current chain gaps without writes.
4. Shadow daemon mode: acquire/extract/cache and compute candidate verdicts
   without canonical writes.
5. Operator-approved staging DB write.
6. Operator-approved canonical write.
7. Operator-approved launch.
8. Post-launch live-consuming proof.

Post-launch proof requires:

- forecast-live process exists;
- heartbeat fresh within 90 seconds;
- latest source_run known for HIGH and LOW;
- source_run coverage/readiness fresh and non-expired for eligible rows;
- source health does not show false green for 429/dependency failures;
- live evaluator logs/status no longer show producer-readiness blockers for
  eligible candidates;
- no duplicate OpenData owner is active.

## 9. Maintenance Plan

1. Add a read-only verifier script in a later implementation slice, for example
   `scripts/check_forecast_live_ready.py`, with no DB writes and no launchctl
   mutation. It should output the highest completion state reached and exact
   blocker reasons.
2. Add topology gate coverage so future data-daemon plans cannot be approved
   without stale-base, owner, readiness, source-health, and runtime-state
   checks.
3. Add a packet closeout rule: critic `APPROVE` must name whether it approves
   `PLAN_APPROVED`, `CODE_READY_ON_HEAD`, `LIVE_RUNNING`, or `DONE`.
4. Keep duplicate DB cleanup as a separate authority-inventory packet. This
   refactor may reduce hot-path cross-DB dependency, but it must not delete
   legacy DBs opportunistically.

## 10. Files Likely Touched Later

Planning only, not edit authorization:

- `src/ingest_main.py`
- `src/ingest/forecast_live_daemon.py`
- `src/ingest/AGENTS.md`
- `src/data/ecmwf_open_data.py`
- `src/data/source_health_probe.py`
- `src/data/producer_readiness.py`
- `src/data/executable_forecast_reader.py`
- `src/state/db.py`
- `src/state/readiness_repo.py`
- `src/state/source_run_coverage_repo.py`
- `src/state/db_writer_lock.py`
- `src/engine/evaluator.py`
- `scripts/check_forecast_live_ready.py`
- focused tests under `tests/`
- topology/docs registry files for admitted new scripts/tests/docs

## 11. Critic Questions

The critic must reject this plan unless all answers are YES:

1. Does it downgrade the old overclaim instead of preserving a false approval?
2. Does it prevent stale worktree code from being called current-head ready?
3. Does it make duplicate OpenData scheduling mechanically impossible?
4. Does it treat HTTP 429 as degraded/throttled, not success?
5. Does it require relationship tests before source edits?
6. Does it define exact second-level SLOs and a verifier path?
7. Does it keep runtime launch and production DB mutation behind operator-go?
8. Does it avoid solving duplicate DB cleanup inside this refactor?
9. Does it define completion states so future agents cannot call `CODE_READY`
   or `PLAN_APPROVED` equal to `LIVE_CONSUMING`?
10. Does every added daemon/process remove, disable, or narrow an existing mixed
    responsibility?
