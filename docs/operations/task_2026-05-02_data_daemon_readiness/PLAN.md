# Plan: Data Daemon Readiness Architecture
> Created: 2026-05-02 | Status: DRAFT FOR CRITIC REVIEW | Base: PR #44 `live-blocker-fixes-2026-05-02`

## Goal

Zeus live entries are blocked unless a machine-readable readiness contract proves the specific city, local date, metric, source run, market topology, and strategy dependencies are fresh, complete, causally available, and safe for live execution.

## Context

- Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-pr44-data-daemon-readiness`.
- Branch: `data-daemon-readiness-2026-05-02`, stacked on PR #44 head branch `live-blocker-fixes-2026-05-02`.
- PR #44 baseline already includes the PR #42 emergency boot-staleness guard and follow-up fixes: pre-Phase-1 timestamp snapshot, `solar_daily` `WRITTEN` filter, boot-forced advisory locks, and `tests/test_ingest_main_boot_resilience.py`.
- Review artifact read in full from sibling worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/artifacts/Zeus_May2_review_data_deamon.md`.
- Broad topology navigation for the full request was advisory-only (`risk_tier: T3`) and must be split into narrow typed phases before source edits.
- Current reusable substrate: `src/control/freshness_gate.py`, `src/state/data_coverage.py`, `src/data/dual_run_lock.py`, `src/data/dissemination_schedules.py`, `src/data/forecast_source_registry.py`, `src/data/hole_scanner.py`, `src/data/market_scanner.py`, `src/types/metric_identity.py`, `src/engine/cycle_runner.py`.

## Approach

Keep PR #42's boot-time smoke guard as an emergency stopgap, but do not let table-level timestamps authorize live trades. Add a release-calendar-driven readiness layer that the ingest daemon writes and the trading runtime consumes fail-closed.

`src/data/dissemination_schedules.py` is the predecessor to reuse for source availability, but it is not enough by itself: it reconstructs forecast availability for previous-runs rows, not live source release calendars, Open Data track completeness, observation readiness, market topology, or strategy dependency eligibility.

## Executive Decision

This packet turns PR #42 from a boot-time smoke detector into the first stage of a durable readiness architecture. The architectural move is to stop asking "are the tables recent?" and instead ask "is this exact live trading dependency graph proven current and causally valid for this exact scope?"

The target system has two writers and one reader:

- Ingest/data jobs write durable provenance facts: job run, source run, data coverage, source health, topology snapshots.
- A readiness builder writes derived, scoped `readiness_state` rows.
- `cycle_runner` reads only the readiness contract before permitting new live entries.

The data daemon does not decide strategy, edge, sizing, or execution. Its authority ends at facts and readiness verdicts. The trading runtime remains responsible for strategy selection and trade admission, but it must fail closed when required readiness is absent, expired, blocked, or shadow-only.

## Current Baseline Facts

### PR #44 Emergency Guard

PR #44 already contains the emergency remediation for the May 2 daemon failure pattern:

- Boot catch-up snapshots pre-catch-up timestamps so Phase 1 cannot mask stale tables.
- Forecast boot freshness currently checks a global `MAX(captured_at)` from `forecasts`.
- Solar boot freshness now checks `data_coverage.status = 'WRITTEN'`, so recent `FAILED` or `MISSING` coverage rows cannot masquerade as fresh.
- Boot forced work acquires the same `dual_run_lock` keys as scheduled jobs.
- Existing tests in `tests/test_ingest_main_boot_resilience.py` prove the emergency behavior.

Those tests are not enough for live readiness, but they are the regression base. Do not delete them until replacement readiness tests cover the same outage scenarios.

### Existing Substrate Verdicts

| Existing file | Verdict | Reason |
|---|---|---|
| `src/control/freshness_gate.py` | CURRENT_REUSABLE as reachability input | It handles source-health JSON verdicts and already separates boot vs mid-run behavior. |
| `src/state/data_coverage.py` | CURRENT_REUSABLE as physical coverage ledger | It has guarded state transitions and WRITTEN/FAILED/MISSING/LEGITIMATE_GAP semantics. |
| `src/data/dual_run_lock.py` | CURRENT_REUSABLE for idempotency | It is already used by K2 jobs and PR #42 forced boot work. |
| `src/data/dissemination_schedules.py` | CURRENT_REUSABLE predecessor, incomplete | It reconstructs previous-runs availability but not live source release calendars or readiness. |
| `src/data/forecast_source_registry.py` | CURRENT_REUSABLE source identity substrate | It defines source IDs, gates, fallback roles, and operator semantics. |
| `src/data/hole_scanner.py` | CURRENT_REUSABLE detector, incomplete | It detects holes; it does not express scoped live authorization. |
| `src/data/market_scanner.py` | CURRENT_REUSABLE authority substrate | It carries Gamma snapshot authority and source-contract checks. |
| `src/types/metric_identity.py` | CURRENT_REUSABLE invariant | It makes high/low separable and should be in every readiness key. |
| `src/engine/cycle_runner.py` | CURRENT_REUSABLE high-risk consumer | It is the live cycle seam; change narrowly with tests. |

## Non-Goals

- Do not rewrite the daemon into a new service topology in this PR.
- Do not remove PR #42 emergency checks until readiness consumption is proven.
- Do not allow `data_coverage.WRITTEN` to equal `LIVE_ELIGIBLE`.
- Do not allow `source_health.json` to equal `LIVE_ELIGIBLE`.
- Do not add tracked runtime-local config such as `config/settings.json`.
- Do not repair unrelated global topology registry drift as part of this packet.
- Do not make operator controls capable of promoting incomplete data.
- Do not let backfill or catch-up rows authorize live trades without causal proof that they were available before the decision time.

## Authority And Topology Protocol

Broad topology admission already returned T3 advisory-only for the all-files version of this task. Implementation must be split into narrow admission packets.

Each source-edit phase must run:

```bash
python scripts/topology_doctor.py --navigation --task "<phase name>" --files <phase files>
python scripts/topology_doctor.py --planning-lock --changed-files <phase files> --plan-evidence docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md
python scripts/topology_doctor.py --map-maintenance --changed-files <phase files>
```

The plan evidence is this file. If a phase changes `src/state/db.py`, `src/engine/cycle_runner.py`, or more than four source files, treat it as T3 even if the tool output is permissive.

Known unrelated topology drift must be recorded but not absorbed unless it touches a changed file. For touched files, update:

- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`
- `architecture/test_topology.yaml`
- nearest scoped `AGENTS.md` where lifecycle/header law requires it

## Design Principles

### Readiness Is A Contract, Not A Timestamp

The readiness verdict must be a durable row with scope, dependencies, provenance, expiry, and reason codes. A timestamp alone cannot tell whether the relevant city, local date, metric, source track, market topology, and strategy dependency are safe.

### Reachability Is Only One Input

`source_health.json` tells whether a source is reachable or stale in the control plane. It does not prove rows exist, source cycles have released, high and low are both populated, market topology is current, or a quote was checked. `FreshnessVerdict.FRESH` can support readiness; it cannot replace readiness.

### Backfill Is Shadow Unless Proven Causal

A row imported later may be useful for learning and reconciliation, but it must not authorize a live decision that happened before the row existed. The readiness builder must default backfill-origin data to `SHADOW_ONLY` unless source-run provenance proves causal availability.

### City-Local Date Is The Market Date

Readiness must key on city-local target date, not UTC date. DST days must use the actual local-day hour count. Ambiguous/folded times need explicit handling in tests.

### High And Low Are Different Metrics

Readiness must include `MetricIdentity` or its normalized components. `HIGH_LOCALDAY_MAX` and `LOW_LOCALDAY_MIN` may share a city/date but cannot share live eligibility.

## Target Architecture

### Logical Planes

| Plane | Examples | Readiness role |
|---|---|---|
| forecast | Open Data, previous-runs, TIGGE | signal/calibration readiness |
| observation | daily obs, hourly instants | Day0 and settlement readiness |
| solar_aux | solar daily | auxiliary strategy dependency |
| market_topology | Gamma scanner/source contract | market construction readiness |
| quote | CLOB/orderbook | submit-stage readiness only |
| settlement_truth | harvester/truth writer | settlement capture and learning |
| source_health | source probes | reachability input |
| hole_backfill | hole scanner/catch-up | coverage repair and block/shadow impacts |
| telemetry_control | ingest status/control plane | visibility and downgrade-only controls |

### Durable Contracts

The target durable model is:

- `job_run`: one scheduler/job attempt and its lock/status/result.
- `source_run`: one upstream source cycle/run and its release/fetch/capture/completeness truth.
- `market_topology_state`: one durable Gamma/source-contract/bin topology fact per market scope.
- `readiness_state`: scoped derived live/shadow/blocked verdicts.
- Existing `data_coverage`: physical table-row coverage status.
- Existing/source-health output: reachability evidence, eventually DB-backed but not required for first consumer.

### Runtime Read Path

The runtime read path should be small and boring:

```python
verdict = readiness_repo.get_entry_readiness(
    conn,
    city_id=candidate.city_id,
    city=candidate.city,
    city_timezone=candidate.city_timezone,
    target_local_date=candidate.target_date,
    metric_identity=candidate.metric_identity,
    strategy=candidate.strategy_id,
    market_family=MARKET_FAMILY_WEATHER_TEMPERATURE,
    condition_id=candidate.condition_id,
)
```

`get_entry_readiness(...)` must not accept a bare city display name or bare `"high"`/`"low"` string. It must receive canonical city identity, IANA timezone, target local date, and normalized `MetricIdentity` components. That prevents a readiness row from surviving city-config changes, DST ambiguity, or data-version/metric drift.

Entry readiness explicitly excludes executable quote freshness. Quote freshness belongs to the venue submit-stage gate because it requires token id, side, orderbook timestamp, and order intent. Gamma market metadata can satisfy market topology readiness; it cannot satisfy quote readiness.

Fail-closed mapping:

| Status | Runtime behavior |
|---|---|
| `LIVE_ELIGIBLE` | Candidate may continue to existing evaluator/executor gates. |
| `SHADOW_ONLY` | No live submit; diagnostics/shadow only if the mode exists. |
| `BLOCKED` | No entry; persist reason. |
| `DEGRADED_LOG_ONLY` | Does not block when dependency is not required by this strategy. |
| `UNKNOWN_BLOCKED` | Missing/expired unreadable state blocks live. |

## Data Contracts

### `source_release_calendar.yaml`

Initial tracked machine-law shape:

```yaml
schema_version: 1
entries:
  - calendar_id: ecmwf_open_data_mx2t6_high
    source_id: ecmwf_open_data
    track: mx2t6_high
    plane: forecast
    timezone: UTC
    cycle_hours_utc: [0, 6, 12, 18]
    parameter: mx2t6
    metric: high
    period_semantics: max temperature at 2m in previous 6h ending at valid step
    expected_members: 51
    expected_step_rule: local_day_6h_windows_up_to_240h
    safe_fetch:
      default_lag_minutes: 485
    partial_policy: BLOCK_LIVE
    max_source_lag_seconds: 108000
    live_authorization: true
```

Required first entries:

- `ecmwf_open_data` high track.
- `ecmwf_open_data` low track.
- previous-runs family marked reconstructed/diagnostic unless proven live-causal.
- HKO/WU/Ogimet observation families with source-specific caveats.
- TIGGE archive marked `BACKFILL_ONLY` unless real-time access is proven.
- solar daily as auxiliary-only unless a strategy declares it required.

### `job_run`

Proposed fields:

- `job_run_id`
- `job_name`
- `plane`
- `scheduled_for`
- `missed_from`
- `started_at`
- `finished_at`
- `lock_key`
- `lock_acquired_at`
- `status`
- `reason_code`
- `rows_written`
- `rows_failed`
- `source_run_id`
- `source_id`
- `track`
- `release_calendar_key`
- `safe_fetch_not_before`
- `expected_scope_json`
- `affected_scope_json`
- `readiness_impacts_json`
- `readiness_recomputed_at`
- `meta_json`

### `source_run`

Proposed fields:

- `source_run_id`
- `source_id`
- `track`
- `release_calendar_key`
- `ingest_mode`
- `origin_mode`
- `source_cycle_time`
- `source_issue_time`
- `source_release_time`
- `source_available_at`
- `fetch_started_at`
- `fetch_finished_at`
- `captured_at`
- `imported_at`
- `valid_time_start`
- `valid_time_end`
- `target_local_date`
- `city_id`
- `city_timezone`
- `temperature_metric`
- `physical_quantity`
- `observation_field`
- `data_version`
- `expected_members`
- `observed_members`
- `expected_steps_json`
- `observed_steps_json`
- `expected_count`
- `observed_count`
- `completeness_status`
- `partial_run`
- `raw_payload_hash`
- `manifest_hash`
- `status`
- `reason_code`

### `readiness_state`

Proposed fields:

- `readiness_id`
- `scope_type`
- `city_id`
- `city`
- `city_timezone`
- `target_local_date`
- `metric`
- `temperature_metric`
- `physical_quantity`
- `observation_field`
- `data_version`
- `source_id`
- `track`
- `source_run_id`
- `market_family`
- `event_id`
- `condition_id`
- `token_ids_json`
- `strategy_key`
- `status`
- `reason_codes_json`
- `computed_at`
- `expires_at`
- `dependency_json`
- `provenance_json`

Uniqueness must include at least `scope_type`, `city_id`, `city_timezone`, `target_local_date`, normalized metric identity components, `strategy_key`, `market_family`, and the relevant source/market identity. A display city label is not part of the safety identity.

### `market_topology_state`

Proposed fields:

- `topology_id`
- `market_family`
- `event_id`
- `condition_id`
- `question_id`
- `city_id`
- `city_timezone`
- `target_local_date`
- `temperature_metric`
- `physical_quantity`
- `observation_field`
- `data_version`
- `token_ids_json`
- `bin_topology_hash`
- `gamma_captured_at`
- `gamma_updated_at`
- `source_contract_status`
- `source_contract_reason`
- `authority_status`
- `status`
- `expires_at`
- `provenance_json`

Topology readiness must be computed from this durable table, not from transient scanner memory. Stale Gamma data, empty fallback snapshots, source-contract mismatch, token/bin topology changes, or expired topology state block affected entry scopes.

### Schema constraints and DB ownership

- These tables belong in the primary state DB initialized by `src/state/db.py`, not in attached trade/world DBs, unless implementation evidence proves a table is trade-ledger specific.
- DDL must be idempotent on legacy DBs and pass an `init_schema()` twice test.
- Add query indices for runtime lookup by city/date/metric/strategy/market status and expiry.
- Add `CHECK` constraints for status enums where SQLite can enforce them.
- JSON fields must be validated before write; invalid JSON or missing required keys fails before persistence.
- Migration tests must cover a copied legacy DB or a fixture representing the current PR44 schema.

Status enum:

- `LIVE_ELIGIBLE`
- `SHADOW_ONLY`
- `BLOCKED`
- `DEGRADED_LOG_ONLY`
- `UNKNOWN_BLOCKED`

Initial reason-code taxonomy:

- `SOURCE_RELEASE_CALENDAR_UNKNOWN`
- `SOURCE_RUN_NOT_RELEASED`
- `SOURCE_RUN_PARTIAL`
- `SOURCE_RUN_STALE`
- `SOURCE_HEALTH_STALE`
- `DATA_COVERAGE_MISSING`
- `DATA_COVERAGE_FAILED`
- `BACKFILL_ONLY`
- `CITY_TIMEZONE_MISSING`
- `DST_WINDOW_INCOMPLETE`
- `HIGH_LOW_METRIC_MISMATCH`
- `MARKET_TOPOLOGY_STALE`
- `SOURCE_CONTRACT_MISMATCH`
- `QUOTE_NOT_CHECKED`
- `QUOTE_STALE`
- `QUOTE_NOT_APPLICABLE_AT_ENTRY`
- `STRATEGY_DEPENDENCY_NOT_REQUIRED`
- `OPERATOR_BLOCK`
- `OPERATOR_SHADOW_ONLY`

### Readiness invalidation law

Any failure in a required dependency must atomically invalidate or overwrite affected prior green readiness. Expiry alone is insufficient. The same job-run or readiness recomputation transaction that observes a failed, partial, skipped-not-released, source-contract-mismatch, topology-stale, or hole-detected fact must write `BLOCKED` or `SHADOW_ONLY` for the affected scopes.

Mandatory tests:

- `test_failed_source_run_invalidates_prior_live_eligible`
- `test_partial_run_overwrites_green_to_blocked`
- `test_hole_detection_clears_green_scope`
- `test_source_contract_mismatch_overwrites_green_scope`

### Backfill causality contract

`ingest_mode`/`origin_mode` values must include `SCHEDULED_LIVE`, `BOOT_CATCHUP`, `HOLE_BACKFILL`, and `ARCHIVE_BACKFILL`. Only `SCHEDULED_LIVE` can be considered live-causal by default. Other modes start as `SHADOW_ONLY` unless source-run facts prove `source_available_at <= readiness_computed_at <= decision_time` for the exact decision being authorized.

### PR split decision

This work must not merge as one large implementation PR. Use stacked PRs:

- PR45a: plan, evidence, and a test-only contract pack for every false-readiness seam named in this plan. PR45a must lock the behavior before schema/repo/source implementation begins.
- PR45b: release calendar plus typed `job_run`, `source_run`, `readiness_state`, and `market_topology_state` schemas/repos; no live behavior change.
- PR45c: provenance dual-writes in ingest jobs; no live behavior change.
- PR45d: readiness builder plus hole/backfill/topology readiness in shadow mode.
- PR45e: `cycle_runner` consumption for forecast strategies and emergency-retirement gates.
- Separate or prerequisite PR: observation/settlement-time split, unless it lands before any settlement-capture readiness can be live.

Until the observation/settlement-time split lands, `settlement_capture` readiness is forced `BLOCKED` or `SHADOW_ONLY`. Cycle consumption may launch for forecast-only strategies only.

PR45a required failing/contract tests:

- `test_fresh_scope_cannot_authorize_different_city_id_timezone_date_or_metric`
- `test_utc_date_cannot_substitute_for_city_local_target_date`
- `test_dst_spring_fall_local_day_hour_counts_are_required`
- `test_naive_timestamp_ambiguity_blocks_live_readiness`
- `test_source_health_fresh_cannot_authorize_live_without_source_run_and_coverage`
- `test_data_coverage_written_cannot_authorize_live_without_source_run_and_release_provenance`
- `test_failed_source_run_invalidates_prior_live_eligible`
- `test_partial_run_overwrites_green_to_blocked`
- `test_hole_detection_clears_green_scope`
- `test_source_contract_mismatch_overwrites_green_scope`
- `test_backfill_origin_defaults_shadow_only_without_causal_proof`
- `test_market_topology_stale_or_empty_fallback_blocks_entry_scope`
- `test_quote_freshness_is_not_entry_readiness`
- `test_settlement_capture_forced_blocked_until_settlement_time_law`

## Phase Plan

### Phase 0: Baseline Evidence Lock

Scope:

- Plan file only, plus verification commands.

Exit criteria:

- PR #44 baseline behavior is documented.
- Critic has reviewed this implementation plan.
- No runtime code changes are mixed into the plan commit.

### Phase 1: Time-Semantics Relationship Tests

Files:

- `tests/test_ingest_boot_time_semantics.py`
- `architecture/test_topology.yaml`
- `tests/AGENTS.md`

Tests:

- Fresh City A cannot authorize City B.
- Fresh target-local-date A cannot authorize target-local-date B.
- Same display city cannot authorize a different `city_id` or IANA timezone.
- UTC-date equality cannot substitute for city-local market date.
- Fresh high cannot authorize low.
- Recent `FAILED`/`MISSING` coverage cannot authorize readiness.
- `source_health` fresh/reachable cannot authorize live without matching source-run and coverage provenance.
- `data_coverage.WRITTEN` cannot authorize live without matching source-run and release provenance.
- DST spring/fall local-day counts are required for readiness scope.
- Naive source/fetch/capture/decision timestamps cannot produce `LIVE_ELIGIBLE`. The only allowed normalization boundary must receive an explicit timezone from source release calendar or city config, persist the normalized aware timestamp, and tests must prove assumed-UTC/assumed-local ambiguity blocks live readiness.
- Scheduler timezone is explicit UTC.
- Boot before source release records not-released, not fresh.
- Backfill-origin row cannot produce live eligibility.

### Phase 2: Release Calendar

Files:

- `src/data/release_calendar.py`
- `config/source_release_calendar.yaml` or `architecture/source_release_calendar.yaml` after critic verdict.
- `src/data/dissemination_schedules.py`
- `tests/test_release_calendar.py`
- `src/data/AGENTS.md`
- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`

API target:

```python
decision = release_calendar.evaluate_safe_fetch(source_id, track, cycle_time, now_utc)
```

Return statuses:

- `FETCH_ALLOWED`
- `SKIPPED_NOT_RELEASED`
- `CALENDAR_UNKNOWN_BLOCKED`
- `BACKFILL_ONLY`
- `PARTIAL_EXPECTED_RETRY`

### Phase 3: Readiness Schema And Repository

Files:

- `src/state/db.py`
- `src/state/readiness_repo.py`
- `tests/test_readiness_state.py`
- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`

Requirements:

- Idempotent DDL for old DBs.
- DB-level status validation where practical.
- Missing or expired readiness returns `UNKNOWN_BLOCKED`.
- Invalid status/reason JSON cannot be written.
- Prior `LIVE_ELIGIBLE` rows are overwritten or tombstoned when a required dependency later fails, becomes partial, is skipped-not-released, or has an active hole/topology mismatch.
- Schema tests specify DB ownership, uniqueness, indices, enum checks, and double-`init_schema()` idempotency.

### Phase 4: Job Run And Source Run Provenance

Files:

- `src/state/db.py`
- `src/state/job_run_repo.py`
- `src/state/source_run_repo.py`
- `src/observability/scheduler_health.py`
- `src/ingest_main.py`
- Forecast and observation appenders in separate sub-phases if necessary.
- `tests/test_source_run_schema.py`
- `tests/test_job_run_provenance.py`

Boundary:

- This phase must not change live trade behavior.
- If writer dual-write grows too large, split forecast-source-run and observation-source-run into separate PRs.
- `job_run` records include first-class source/track/calendar/scope/impact fields, not only `meta_json`.
- Failed or partial job records identify `affected_scope_json` so readiness recomputation can block only the impacted city/date/metric/strategy scopes.

### Phase 5: City/Date/Metric Readiness Builder

Files:

- `src/data/readiness_builder.py`
- `src/types/metric_identity.py`
- `src/config.py`
- `tests/test_city_metric_readiness.py`

Strategy dependency draft:

| Strategy | Forecast | Day0 obs | Daily obs | Solar | Market topology | Quote | Settlement truth |
|---|---|---|---|---|---|---|---|
| `opening_inertia` | required | no | no | optional | required | submit-stage | no |
| `center_buy` | required | no | no | optional | required | submit-stage | no |
| `shoulder_sell` | required | maybe | no | optional | required | submit-stage | no |
| `settlement_capture` | maybe | required | required | optional | required | submit-stage | required, but forced blocked/shadow until Phase 10 lands |

### Phase 6: Boot Integration

Files:

- `src/ingest_main.py`
- `src/data/release_calendar.py`
- `src/data/readiness_builder.py`
- `tests/test_ingest_main_boot_resilience.py`
- `tests/test_ingest_boot_time_semantics.py`

Requirements:

- Keep PR #42 immediate boot checks.
- Route forced work through safe-fetch rules.
- Recompute readiness after boot work.
- Keep global max as alert/smoke only.

### Phase 7: Hole And Backfill Readiness Effects

Files:

- `src/data/hole_scanner.py`
- `src/state/data_coverage.py`
- `src/data/readiness_builder.py`
- `tests/test_hole_scanner_readiness.py`

Requirements:

- Relevant active market hole blocks affected city/date/metric/strategy only.
- Irrelevant auxiliary holes log only.
- Backfill repairs stay shadow unless causal live proof exists.

### Phase 8: Market Topology Readiness

Files:

- `src/state/db.py`
- `src/state/market_topology_repo.py`
- `src/data/market_scanner.py`
- `src/state/readiness_repo.py`
- `src/data/readiness_builder.py`
- `tests/test_market_topology_readiness.py`

Requirements:

- Persist Gamma/source-contract/bin topology facts into `market_topology_state` before readiness consumes them.
- Stale/empty fallback Gamma state blocks affected entry scopes.
- Source-contract mismatch blocks affected markets.
- Quote readiness remains submit-stage and cannot be satisfied by Gamma metadata.

### Phase 9: Cycle Runner Consumption

Files:

- `src/engine/cycle_runner.py`
- `src/state/readiness_repo.py`
- `tests/test_cycle_runner_readiness.py`

Gate placement options:

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| pre-discovery global gate | smallest edit | lacks candidate scope | insufficient final state |
| candidate admission seam | exact scope | may touch discovery helper | preferred target |
| evaluator gate | exact and late | too close to edge logic | avoid first |
| executor gate | final safety | too late for discovery | useful only for quote backstop |

Entry readiness consumption must not require a quote row. It should use a reason such as `QUOTE_NOT_APPLICABLE_AT_ENTRY` and leave token/side/orderbook freshness to submit-stage tests. This phase may merge only for forecast-only strategies unless Phase 10 settlement-time law has landed; otherwise `settlement_capture` remains blocked or shadow-only.

### Phase 10: Observation And Settlement Time Split

Files:

- `src/data/daily_obs_append.py`
- `src/data/hourly_instants_append.py`
- `src/execution/harvester.py`
- `src/state/db.py`
- `tests/test_observation_settlement_time_semantics.py`

Requirements:

- Observation instant, provider fetch, provider revision/display, market close, UMA proposal/resolution, Zeus settlement record, and redeem timestamps remain separate.
- Settlement-capture can be blocked without globally blocking forecast-only strategies.

### Phase 11: Telemetry And Operator Controls

Files:

- `src/data/ingest_status_writer.py`
- `src/control/control_plane.py`
- `src/state/readiness_repo.py`
- `tests/test_readiness_operator_controls.py`

Requirements:

- Export readiness counts by status/reason.
- Operator override can only block or downgrade to shadow.
- Telemetry is derived from readiness, not the source of readiness.

### Phase 12: Emergency Patch Retirement

Files:

- `src/ingest_main.py`
- `tests/test_ingest_main_boot_resilience.py`
- `tests/test_readiness_state.py`
- `tests/test_cycle_runner_readiness.py`

Requirements:

- PR #42 outage scenarios survive as readiness tests.
- `_BOOT_FRESHNESS_THRESHOLD_HOURS` becomes alert-only or disappears.
- No global `MAX(captured_at)` or `MAX(fetched_at)` can authorize live.

## Acceptance Gates

Minimal launch-safe readiness requires:

- Missing readiness blocks live.
- Source release calendar blocks pre-release fetches.
- Scheduler timezone is explicit UTC or proven equivalent.
- City/date/metric readiness exists for active markets.
- High and low cannot cross-authorize.
- Source health and readiness remain separate.
- Market topology readiness blocks stale Gamma/source-contract scopes.
- `cycle_runner` consumes readiness before new live entries.

Forecast readiness requires:

- Cycle, issue, release, available, fetch, capture, import, valid, and local target dates are distinct.
- Expected vs observed members/steps are stored.
- Partial runs block or shadow dependent strategies.

Backfill readiness requires:

- Backfill provenance is stored.
- Backfill rows cannot live-authorize without causal proof.
- Readiness recomputation marks backfill `SHADOW_ONLY` by default.

Emergency retirement requires:

- Boot still evaluates immediately.
- PR #42 failure modes remain covered.
- Table-level timestamp max is alert-only or removed.

## Risk Register

| Risk | Failure mode | Antibody |
|---|---|---|
| source reachable but data missing | false live green | readiness requires source-run and coverage completeness |
| one city fresh makes all cities fresh | cross-scope false freshness | city-scoped tests |
| high fresh makes low fresh | metric contamination | `MetricIdentity` in key |
| boot before source release | stale/no-run treated fresh | safe-fetch calendar test |
| backfill authorizes live | hindsight leakage | backfill shadow-only tests |
| old readiness persists | stale green | expiry and failure-overwrite tests |
| market topology stale | wrong contract traded | topology readiness gate |
| quote not checked | executable price stale | quote status separate from Gamma metadata |
| override promotes live | manual false green | downgrade-only tests |
| schema migration breaks legacy DB | startup failure | idempotent DDL tests |
| cycle gate bypassed by alternate mode | live leak | mode-specific cycle tests |

## Verification Commands

Focused compile once implementation files exist:

```bash
/usr/local/bin/python3 -m py_compile \
  src/ingest_main.py \
  src/data/release_calendar.py \
  src/data/readiness_builder.py \
  src/state/readiness_repo.py \
  src/state/job_run_repo.py \
  src/state/source_run_repo.py
```

Focused tests:

```bash
/usr/local/bin/python3 -m pytest \
  tests/test_ingest_main_boot_resilience.py \
  tests/test_ingest_boot_time_semantics.py \
  tests/test_release_calendar.py \
  tests/test_readiness_state.py \
  tests/test_source_run_schema.py \
  tests/test_job_run_provenance.py \
  tests/test_city_metric_readiness.py \
  tests/test_hole_scanner_readiness.py \
  tests/test_market_topology_readiness.py \
  tests/test_cycle_runner_readiness.py \
  tests/test_observation_settlement_time_semantics.py \
  tests/test_readiness_operator_controls.py \
  -q
```

Per-PR closeout gates follow the split decision above. PR45e cannot merge unless source-run, job-run, hole/backfill, market topology, cycle-runner, and settlement-time gating tests are either passing or settlement-capture is explicitly blocked/shadow-only.

## Critic Review Disposition

- Accepted: add canonical `city_id`, `city_timezone`, and normalized `MetricIdentity` components to runtime API and durable schemas.
- Accepted: force `settlement_capture` blocked/shadow until observation/settlement-time law lands.
- Accepted: add explicit invalidation law for prior green readiness.
- Accepted: add first-class job-run scope/impact/calendar fields.
- Accepted: add `ingest_mode`/`origin_mode` and causal backfill relation.
- Accepted: add durable `market_topology_state` and `src/state/market_topology_repo.py`.
- Accepted: exclude quote freshness from entry readiness and reserve it for submit-stage gates.
- Accepted: specify schema ownership, uniqueness, indices, enum checks, JSON validation, and legacy/idempotent migration tests.
- Accepted: expand verification gates and declare stacked PR boundaries.
- Still open for implementation: final placement of release-calendar machine law under `config/` versus `architecture/`; use critic/planning-lock output before creating the file.

## Tasks

- [ ] 1. Phase admission and evidence lock
  - Files: `docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md`, `src/ingest_main.py`, `tests/test_ingest_main_boot_resilience.py`
  - What: confirm PR #44 baseline behavior, run narrow topology navigation per phase, and preserve current PR #42 tests before adding architecture.
  - Gate: `git status --short --branch`; focused topology command for each phase; no broad all-files admission.

- [ ] 2. Time-semantics relationship tests
  - Files: `tests/test_ingest_boot_time_semantics.py`, `tests/AGENTS.md`, `architecture/test_topology.yaml`
  - What: prove global max cannot express city/date/metric readiness; add PR45a contract tests for stale city hidden by fresh city, different `target_local_date`, same display city with different `city_id`/timezone, UTC-date versus city-local date, stale low hidden by fresh high, naive timestamp ambiguity, DST expected-hour counts, scheduler UTC, `FAILED`/`MISSING` coverage rows, `source_health.FRESH` alone exclusion, `data_coverage.WRITTEN` alone exclusion, prior-green invalidation, backfill shadow-only, topology mismatch, quote exclusion, and settlement-capture blocked/shadow until settlement-time law lands.
  - Gate: new tests carry lifecycle headers and topology registration; schema/repo implementation does not begin until this full false-readiness test contract is reviewed.

- [ ] 3. Source release-calendar contract
  - Files: `src/data/release_calendar.py`, `config/source_release_calendar.yaml`, `src/data/dissemination_schedules.py`, `src/data/AGENTS.md`, `architecture/source_rationale.yaml`, `architecture/module_manifest.yaml`, `tests/test_release_calendar.py`
  - What: add versioned source/track calendars with cycle hours, safe-fetch windows, max lag, expected members/steps/hours, partial policy, and `BACKFILL_ONLY` archive status.
  - Gate: unknown calendar means not live-eligible; boot before release records `SKIPPED_NOT_RELEASED` instead of fresh.

- [ ] 4. Durable readiness schema and APIs
  - Files: `src/state/db.py`, `src/state/readiness_repo.py`, `src/data/readiness_builder.py`, `tests/test_readiness_state.py`
  - What: add `readiness_state` with canonical `city_id`, IANA `city_timezone`, normalized `MetricIdentity` components, `LIVE_ELIGIBLE`, `SHADOW_ONLY`, `BLOCKED`, `DEGRADED_LOG_ONLY`, `UNKNOWN_BLOCKED`, reason codes, expiry, dependencies, provenance, uniqueness/indexes, and invalidation behavior.
  - Gate: missing, expired, failed, partial, skipped-not-released, hole-detected, or source-contract-mismatch dependency blocks or shadows affected scopes; old green readiness cannot silently persist.

- [ ] 5. Job/source run provenance
  - Files: `src/state/db.py`, `src/state/job_run_repo.py`, `src/state/source_run_repo.py`, `src/observability/scheduler_health.py`, `src/ingest_main.py`, `src/data/forecasts_append.py`, `src/data/ecmwf_open_data.py`, `src/data/daily_obs_append.py`, `src/data/hourly_instants_append.py`, `src/data/solar_append.py`, `tests/test_source_run_schema.py`, `tests/test_job_run_provenance.py`
  - What: persist `job_run_id`, `scheduled_for`, source/track/calendar/scope/impact fields, `source_run_id`, ingest/origin mode, source cycle/issue/release/available/fetch/capture/import/valid times, completeness, expected/observed counts, payload or manifest hashes, status, and reason.
  - Gate: source release time, fetch time, captured time, valid time, target local date, origin mode, and affected readiness scope remain separate in assertions.

- [ ] 6. City/date/metric readiness builder
  - Files: `src/data/readiness_builder.py`, `src/types/metric_identity.py`, `src/config.py`, `tests/test_city_metric_readiness.py`
  - What: key readiness by canonical city id, IANA timezone, city-local target date, full `MetricIdentity` components, source track, market family, and strategy dependency.
  - Gate: UTC/local date crossing, DST spring/fall expected-hour counts, one-city-fresh-not-all, high/low separation, and data-version separation tests.

- [ ] 7. Boot catch-up integration
  - Files: `src/ingest_main.py`, `src/data/release_calendar.py`, `src/data/readiness_builder.py`, `tests/test_ingest_main_boot_resilience.py`, `tests/test_ingest_boot_time_semantics.py`
  - What: keep immediate boot checks, but route forced work through release-calendar safe-fetch rules and recompute scoped readiness. Keep 18h only as alert/smoke heuristic, not live law.
  - Gate: no global `MAX(captured_at)` or `MAX(fetched_at)` is consumed as live readiness.

- [ ] 8. Hole/backfill readiness effects
  - Files: `src/data/hole_scanner.py`, `src/state/data_coverage.py`, `src/data/readiness_builder.py`, `tests/test_hole_scanner_readiness.py`
  - What: active market holes block affected readiness scopes; backfill-origin rows remain `SHADOW_ONLY` unless causal live proof exists; irrelevant auxiliary holes log only.
  - Gate: backfill rows cannot live-authorize by themselves.

- [ ] 9. Market topology readiness and cycle gate
  - Files: `src/state/db.py`, `src/state/market_topology_repo.py`, `src/data/market_scanner.py`, `src/state/readiness_repo.py`, `src/data/readiness_builder.py`, `src/engine/cycle_runner.py`, `tests/test_market_topology_readiness.py`, `tests/test_cycle_runner_readiness.py`
  - What: persist Gamma topology freshness, source-contract status, token/bin topology hash, authority status, and expiry into `market_topology_state`; make `cycle_runner` block new forecast-strategy entries on missing/blocked readiness.
  - Gate: stale topology blocks entries; quote freshness remains submit-stage and is not satisfied by Gamma metadata; settlement-capture remains blocked/shadow until observation/settlement time split is done.

- [ ] 10. Observation and settlement time split
  - Files: `src/data/daily_obs_append.py`, `src/data/hourly_instants_append.py`, `src/execution/harvester.py`, `src/state/db.py`, `tests/test_observation_settlement_time_semantics.py`
  - What: separate observation instant, provider fetch, provider revision/display, local day window, market close, UMA proposal/resolution, Zeus recorded settlement, and redeem timestamps.
  - Gate: settlement-capture entries block when settlement source is degraded near resolution.

- [ ] 11. Operator telemetry and downgrade-only controls
  - Files: `src/data/ingest_status_writer.py`, `src/control/control_plane.py`, `src/state/readiness_repo.py`, `tests/test_readiness_operator_controls.py`
  - What: export readiness summaries, blocked reason counts, source-run completeness, and operator overrides that can only block or downgrade to shadow.
  - Gate: operator override cannot promote incomplete data to `LIVE_ELIGIBLE`.

- [ ] 12. Emergency patch retirement
  - Files: `src/ingest_main.py`, `tests/test_ingest_main_boot_resilience.py`, `tests/test_readiness_state.py`, `tests/test_cycle_runner_readiness.py`
  - What: replace global table freshness live logic with readiness recomputation and typed boot jobs while preserving the PR #42 outage scenarios as tests.
  - Gate: boot/readiness/cycle tests pass; global max is alert-only or removed.

## Verification

- Compile: `/usr/local/bin/python3 -m py_compile src/ingest_main.py src/data/release_calendar.py src/data/readiness_builder.py src/state/readiness_repo.py src/state/job_run_repo.py src/state/source_run_repo.py src/state/market_topology_repo.py`
- Focused tests: `/usr/local/bin/python3 -m pytest tests/test_ingest_main_boot_resilience.py tests/test_ingest_boot_time_semantics.py tests/test_release_calendar.py tests/test_readiness_state.py tests/test_source_run_schema.py tests/test_job_run_provenance.py tests/test_city_metric_readiness.py tests/test_hole_scanner_readiness.py tests/test_market_topology_readiness.py tests/test_cycle_runner_readiness.py tests/test_observation_settlement_time_semantics.py tests/test_readiness_operator_controls.py -q`
- Topology: `scripts/topology_doctor.py --navigation` per narrow phase; `scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md` before governed edits.
- Registry maintenance: update `architecture/source_rationale.yaml`, `architecture/module_manifest.yaml`, `architecture/test_topology.yaml`, and scoped `AGENTS.md` entries for new files.

## Risks / Open Questions

- `src/engine/cycle_runner.py` is high fanout and planning-lock sensitive; prefer a small readiness gate seam over evaluator/executor rewrites.
- `current_source_validity.md` and `current_data_state.md` are current-fact surfaces, not durable authority. Re-audit before any live source/data truth claim.
- `config/settings.json` was a PR39 artifact hazard. Adding `config/source_release_calendar.yaml` must not reintroduce runtime-local config drift.
- Any `src/state/db.py` change must be idempotent for legacy DBs and covered by schema tests.
- Global topology checks currently report unrelated stale/missing registry paths. Do not make this packet absorb broad topology cleanup, but keep touched surfaces registered.