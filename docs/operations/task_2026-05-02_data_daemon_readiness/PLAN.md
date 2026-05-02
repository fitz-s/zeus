# Plan: Data Daemon Readiness Architecture
> Created: 2026-05-02 | Status: DRAFT | Base: PR #44 `live-blocker-fixes-2026-05-02`

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

## Tasks

- [ ] 1. Phase admission and evidence lock
  - Files: `docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md`, `src/ingest_main.py`, `tests/test_ingest_main_boot_resilience.py`
  - What: confirm PR #44 baseline behavior, run narrow topology navigation per phase, and preserve current PR #42 tests before adding architecture.
  - Gate: `git status --short --branch`; focused topology command for each phase; no broad all-files admission.

- [ ] 2. Time-semantics relationship tests
  - Files: `tests/test_ingest_boot_time_semantics.py`, `tests/AGENTS.md`, `architecture/test_topology.yaml`
  - What: prove global max cannot express city/date/metric readiness; add tests for stale city hidden by fresh city, stale low hidden by fresh high, naive timestamp handling, scheduler UTC, and `FAILED`/`MISSING` coverage rows.
  - Gate: new tests carry lifecycle headers and topology registration.

- [ ] 3. Source release-calendar contract
  - Files: `src/data/release_calendar.py`, `config/source_release_calendar.yaml`, `src/data/dissemination_schedules.py`, `src/data/AGENTS.md`, `architecture/source_rationale.yaml`, `architecture/module_manifest.yaml`, `tests/test_release_calendar.py`
  - What: add versioned source/track calendars with cycle hours, safe-fetch windows, max lag, expected members/steps/hours, partial policy, and `BACKFILL_ONLY` archive status.
  - Gate: unknown calendar means not live-eligible; boot before release records `SKIPPED_NOT_RELEASED` instead of fresh.

- [ ] 4. Durable readiness schema and APIs
  - Files: `src/state/db.py`, `src/state/readiness_repo.py`, `src/data/readiness_builder.py`, `tests/test_readiness_state.py`
  - What: add `readiness_state` with `LIVE_ELIGIBLE`, `SHADOW_ONLY`, `BLOCKED`, `DEGRADED_LOG_ONLY`, `UNKNOWN_BLOCKED`, reason codes, expiry, dependencies, and provenance.
  - Gate: missing or expired readiness blocks live; old green readiness cannot silently persist.

- [ ] 5. Job/source run provenance
  - Files: `src/state/db.py`, `src/state/job_run_repo.py`, `src/state/source_run_repo.py`, `src/observability/scheduler_health.py`, `src/ingest_main.py`, `src/data/forecasts_append.py`, `src/data/ecmwf_open_data.py`, `src/data/daily_obs_append.py`, `src/data/hourly_instants_append.py`, `src/data/solar_append.py`, `tests/test_source_run_schema.py`, `tests/test_job_run_provenance.py`
  - What: persist `job_run_id`, `scheduled_for`, `source_run_id`, source cycle/issue/release/available/fetch/capture/import/valid times, completeness, expected/observed counts, payload or manifest hashes, status, and reason.
  - Gate: source release time, fetch time, captured time, valid time, and target local date remain separate in assertions.

- [ ] 6. City/date/metric readiness builder
  - Files: `src/data/readiness_builder.py`, `src/types/metric_identity.py`, `src/config.py`, `tests/test_city_metric_readiness.py`
  - What: key readiness by city IANA timezone, city-local target date, high/low `MetricIdentity`, source track, market family, and strategy dependency.
  - Gate: UTC/local date crossing, DST spring/fall expected-hour counts, one-city-fresh-not-all, and high/low separation tests.

- [ ] 7. Boot catch-up integration
  - Files: `src/ingest_main.py`, `src/data/release_calendar.py`, `src/data/readiness_builder.py`, `tests/test_ingest_main_boot_resilience.py`, `tests/test_ingest_boot_time_semantics.py`
  - What: keep immediate boot checks, but route forced work through release-calendar safe-fetch rules and recompute scoped readiness. Keep 18h only as alert/smoke heuristic, not live law.
  - Gate: no global `MAX(captured_at)` or `MAX(fetched_at)` is consumed as live readiness.

- [ ] 8. Hole/backfill readiness effects
  - Files: `src/data/hole_scanner.py`, `src/state/data_coverage.py`, `src/data/readiness_builder.py`, `tests/test_hole_scanner_readiness.py`
  - What: active market holes block affected readiness scopes; backfill-origin rows remain `SHADOW_ONLY` unless causal live proof exists; irrelevant auxiliary holes log only.
  - Gate: backfill rows cannot live-authorize by themselves.

- [ ] 9. Market topology readiness and cycle gate
  - Files: `src/data/market_scanner.py`, `src/state/readiness_repo.py`, `src/data/readiness_builder.py`, `src/engine/cycle_runner.py`, `tests/test_market_topology_readiness.py`, `tests/test_cycle_runner_readiness.py`
  - What: persist Gamma topology freshness, source-contract status, token/bin topology hash, and quote-not-checked status; make `cycle_runner` block new entries on missing/blocked readiness.
  - Gate: stale topology blocks entries; quote freshness remains submit-stage and is not satisfied by Gamma metadata.

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

- Compile: `/usr/local/bin/python3 -m py_compile src/ingest_main.py src/data/release_calendar.py src/data/readiness_builder.py src/state/readiness_repo.py src/state/job_run_repo.py src/state/source_run_repo.py`
- Focused tests: `/usr/local/bin/python3 -m pytest tests/test_ingest_main_boot_resilience.py tests/test_ingest_boot_time_semantics.py tests/test_release_calendar.py tests/test_readiness_state.py tests/test_city_metric_readiness.py tests/test_cycle_runner_readiness.py -q`
- Topology: `scripts/topology_doctor.py --navigation` per narrow phase; `scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md` before governed edits.
- Registry maintenance: update `architecture/source_rationale.yaml`, `architecture/module_manifest.yaml`, `architecture/test_topology.yaml`, and scoped `AGENTS.md` entries for new files.

## Risks / Open Questions

- `src/engine/cycle_runner.py` is high fanout and planning-lock sensitive; prefer a small readiness gate seam over evaluator/executor rewrites.
- `current_source_validity.md` and `current_data_state.md` are current-fact surfaces, not durable authority. Re-audit before any live source/data truth claim.
- `config/settings.json` was a PR39 artifact hazard. Adding `config/source_release_calendar.yaml` must not reintroduce runtime-local config drift.
- Any `src/state/db.py` change must be idempotent for legacy DBs and covered by schema tests.
- Global topology checks currently report unrelated stale/missing registry paths. Do not make this packet absorb broad topology cleanup, but keep touched surfaces registered.