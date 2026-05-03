# Phase 0 Evidence Lock

Created: 2026-05-03
Captured at: 2026-05-03T02:31:14Z
Authority basis: PLAN_v4 Phase 0, fresh worktree from origin/main
Status: COMPLETE

## Worktree Identity

```text
worktree: /Users/leofitz/.openclaw/worktrees/zeus-live-entry-v4-2026-05-03
branch: live-entry-forecast-target-v4-2026-05-03
base: 47d11d456e6ff6bf23385c4365d45fb0244ff717
base subject: Merge PR #45 data daemon readiness
```

Current worktree list at capture:

```text
/Users/leofitz/.openclaw/workspace-venus/zeus                                      b7bf0378 [healthcheck-riskguard-live-label-2026-05-02]
/Users/leofitz/.openclaw/workspace-venus/zeus-pr39-source-contract-slim            2c8b7059 [source-contract-protocol-slim-clean-2026-05-02]
/Users/leofitz/.openclaw/workspace-venus/zeus-review-crash-remediation-2026-05-02  396df67e [review-crash-remediation-2026-05-02]
/Users/leofitz/.openclaw/worktrees/zeus-live-entry-v4-2026-05-03                   47d11d45 [live-entry-forecast-target-v4-2026-05-03]
```

Phase 0 started in a new physical worktree, not in the PR46 healthcheck branch.

## Baseline Gate

Command:

```bash
/usr/local/bin/python3 -m pytest tests/test_release_calendar.py tests/test_readiness_state.py -q
```

Result:

```text
..................                                                       [100%]
18 passed in 0.12s
```

The PR45 release-calendar/readiness substrate is healthy on the selected base.

## PR45 Substrate Present

Confirmed files:

```text
config/source_release_calendar.yaml
src/state/job_run_repo.py
src/state/market_topology_repo.py
src/state/readiness_repo.py
src/state/source_run_repo.py
```

## Current Missing Contract: entry_forecast Config

Strict search for config/accessor keys:

```bash
rg -n '"entry_forecast"|entry_forecast\s*[:=]' config src tests --glob '!docs/**'
```

Result: no matches.

Broader search for `entry_forecast` only finds old evaluator evidence helper/test names, not the PLAN_v4 `entry_forecast` configuration object.

Conclusion: missing `entry_forecast` means forecast entries must remain blocked until Phase 2 adds the strict config contract.

## Current Missing Contract: Executable v2 Source Linkage

`src/state/schema/v2_schema.py` currently creates `ensemble_snapshots_v2` with:

```text
snapshot_id
city
target_date
temperature_metric
physical_quantity
observation_field
issue_time
valid_time
available_at
fetch_time
lead_hours
members_json
p_raw_json
spread
is_bimodal
model_version
data_version
training_allowed
causality_status
boundary_ambiguous
ambiguous_member_count
manifest_hash
provenance_json
authority
recorded_at
```

Idempotent follow-on columns currently add:

```text
members_unit
members_precision
local_day_start_utc
step_horizon_hours
unit
```

Absent executable linkage columns required by PLAN_v4:

```text
source_id
source_transport
source_run_id
release_calendar_key
source_cycle_time
source_release_time
source_available_at
```

Conclusion: existing v2 rows are not executable for live entry under PLAN_v4. They remain legacy/shadow/training unless explicitly backfilled with source-run provenance.

## Current Missing Contract: source_run_coverage

Command:

```bash
rg -n "source_run_coverage" src tests config --glob '!docs/**'
```

Result: no matches.

Conclusion: source-run identity exists, but per city/date/metric future coverage is not yet a first-class persisted object. Phase 3 must add `source_run_coverage` before executable reader work.

## Current Risk: Open Data Cycle Selection Still Source-Run Centered

Evidence:

```text
src/data/ecmwf_open_data.py:110:def _default_cycle(now: datetime) -> tuple[date, int]:
src/data/ecmwf_open_data.py:124:    # Pre-07Z: yesterday's 18Z is the freshest fully-available run.
src/data/ecmwf_open_data.py:192:        _default_cycle(now) if run_date is None or run_hour is None else (run_date, run_hour)
src/data/ecmwf_open_data.py:310:    Use this in any reader that wants "freshest source first, fall back to
```

`src/ingest_main.py` still documents startup catch-up as:

```text
src/ingest_main.py:484:    Fires once at daemon start; pulls today's freshest run for both tracks.
```

Conclusion: current producer language and cycle selection still center source-run freshness, not future target-local-date coverage. Phase 1 tests must make this category fail before implementation changes.

## Phase 0 Verdict

Phase 0 is complete.

Facts locked:

- Fresh worktree exists and is isolated from PR46 dirty state.
- Base is `origin/main` at PR45 merge.
- Baseline release-calendar/readiness tests pass.
- `entry_forecast` config is absent.
- `ensemble_snapshots_v2` lacks executable source linkage columns.
- `source_run_coverage` does not exist.
- Open Data startup and `_default_cycle()` still express the source-run freshness model.

Next allowed step: Phase 1 failing relationship tests for future target-local-date coverage.
