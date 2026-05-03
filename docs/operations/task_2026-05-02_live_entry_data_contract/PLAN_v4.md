# Plan V4: Live Entry Forecast-Target Contract

Created: 2026-05-03
Authority basis: Zeus May 2 data review, PLAN_v3 critic pass, ECMWF Open Data/TIGGE architecture audit
Status: Accepted candidate for implementation planning; supersedes PLAN_v3 unless later reviewer rejects

## 0. Executive Verdict

PLAN_v3 got the source identity, transport gate, provenance, readiness, calibration transfer, and decision persistence direction right. It was still missing the first-principle object for live entry:

> A live forecast entry is authorized by future target-local-date coverage, not by the fact that the latest source run was fetched.

The Open Data producer's job is not to fetch "today data". Its job is to use the latest complete ECMWF source run to produce complete city/date/metric forecast rows for future Polymarket weather market settlement dates.

PLAN_v4 therefore keeps the PLAN_v3 source/transport/readiness/calibration/persistence model, but upgrades the core contract to `LiveEntryForecastTargetContract.v1`.

## 1. Root Correction

Three dates must never collapse into one concept:

| Concept | Example | Meaning | Can authorize live by itself? |
| --- | --- | --- | --- |
| `source_cycle_date` | `2026-05-03 00Z` | ECMWF run/init/cycle time | No |
| `fetch_date` | `2026-05-03 08:10Z` | Zeus pulled Open Data file | No |
| `target_local_date` | `2026-05-08` in `America/New_York` | Polymarket settlement date | This is the keyed trading object |

The existing Open Data code can validly fetch a freshest source run only if extraction and readiness prove coverage for future target-local dates.

Forbidden interpretations:

- "today's freshest run" means only today's target date.
- source cycle date is a proxy for target local date.
- a v2 row for today's local date can authorize a D+N market.
- source health green implies entry readiness.

## 2. Core Contract

Named contract:

```text
LiveEntryForecastTargetContract.v1
```

Definition:

```text
A forecast source run is live-usable for a market only if it produces an explicitly linked, complete forecast row for that market's future city-local target date and metric.
```

Core object:

```text
ForecastTargetCoverage =
  source_run_id
  source_id
  source_transport
  release_calendar_key
  source_cycle_time
  track: mx2t6_high | mn2t6_low
  city_id
  city_timezone
  target_local_date
  target_local_window_start_utc
  target_local_window_end_utc
  required_steps
  observed_steps
  expected_members
  observed_members
  snapshot_ids
  completeness_status
  producer_readiness_status
```

Live opening-hunt entry requires:

```text
source_run complete
AND future target_local_date covered
AND correct high/low metric
AND all required steps present
AND all expected members present
AND v2 rows linked to source_run_id/source_id/source_transport/release_calendar_key
AND producer readiness not expired
AND calibration policy allows at least shadow/canary/live
AND entry readiness allows this market/strategy/condition
```

## 3. V3 Decisions Retained And Upgraded

| PLAN_v3 decision | V4 status | V4 correction |
| --- | --- | --- |
| Open Data and TIGGE are same ECMWF IFS ENS authority family but different SLA identities | Keep | Preserve separate source/data_version identities; allow named calibration transfer only |
| Open Data can be live inference source; TIGGE durable calibration/archive | Keep | TIGGE remains backfill/training unless real-time access is separately proven |
| Add explicit v2 columns | Keep | Also require producer and reader to enforce future target coverage |
| Transport-aware gate | Keep | Gate source + transport + role + readiness + rollout + calibration |
| Add `entry_forecast` config | Keep | Add target horizon and cycle-profile policy |
| Calibration transfer policy | Keep | Default `SHADOW_ONLY`; persist forecast/calibration split |
| Two-layer readiness | Keep | Producer readiness must be per future city/date/metric coverage, not source-run freshness |
| Decision provenance persistence | Keep | Add input snapshot ids, source run, readiness id, calibration policy fields |
| Live canary operator gate | Keep | Also require active future target-date coverage evidence |
| Fresh worktree | Keep | Do not implement on PR46 healthcheck branch |

## 4. Source Authority And Transport Model

| Layer | Example | Purpose |
| --- | --- | --- |
| Authority family | `ecmwf_ifs_ens` | calibration transfer grouping |
| Source id | `ecmwf_open_data`, `tigge` | upstream and SLA identity |
| Transport | `ensemble_snapshots_v2_db_reader` | executable access route |
| Role | `entry_primary`, `monitor_fallback`, `diagnostic`, `learning` | money-lane permission |
| Data version | `ecmwf_opendata_mx2t6_local_calendar_day_max_v1` | physical extraction identity |

Live entry is allowed only through:

```text
source_id = ecmwf_open_data
source_transport = ensemble_snapshots_v2_db_reader
role = entry_primary
producer_readiness = LIVE_ELIGIBLE
calibration_policy = LIVE_ELIGIBLE or CANARY_ELIGIBLE
rollout_mode = canary | live
```

Blocked routes:

```text
collect_open_ens_cycle(...) -> producer only
fetch_ensemble(model="ecmwf_open_data", role="entry_primary") -> forbidden
Open-Meteo ensemble ECMWF -> monitor_fallback/diagnostic only
TIGGE archive -> learning/backfill only
```

Calendar/live-causality outranks registry role. Even if the registry allows a role, `BACKFILL_ONLY_BLOCKED` prevents live entry.

## 5. Entry Forecast Config

Add strict config separate from `ensemble.primary`:

```json
{
  "entry_forecast": {
    "source_id": "ecmwf_open_data",
    "source_transport": "ensemble_snapshots_v2_db_reader",
    "authority_family": "ecmwf_ifs_ens",
    "high_track": "mx2t6_high_full_horizon",
    "low_track": "mn2t6_low_full_horizon",
    "target_horizon_days": 10,
    "warm_horizon_days": 10,
    "source_cycle_policy": "latest_complete_full_horizon",
    "allow_short_horizon_06_18": false,
    "rollout_mode": "blocked",
    "calibration_policy_id": "ecmwf_open_data_uses_tigge_localday_cal_v1",
    "require_active_market_future_coverage": true
  }
}
```

Config law:

- `ensemble.primary` remains generic/legacy/diagnostic.
- `entry_forecast` is the only source for live forecast-entry data.
- Missing `entry_forecast` blocks forecast entries.
- Default rollout is `blocked`.
- `allow_short_horizon_06_18=false` initially.
- `target_horizon_days` is advisory market/warm scope only. Live eligibility is decided by computed `max_required_step_hour <= selected_profile.live_max_step_hours` for each city/date/metric scope.
- If any active market scope requires a step beyond the selected profile's live horizon, that scope is `SOURCE_RUN_HORIZON_OUT_OF_RANGE`, not partially live.

## 6. Release Calendar V2

Current flat calendar entries with `cycle_hours_utc: [0, 6, 12, 18]` are insufficient. ECMWF Open Data ENS cycles have cycle-specific horizons: 00/12 are full horizon; 06/18 are shorter. `mx2t6` and `mn2t6` are previous-6-hour period aggregates, not instantaneous temperatures.

New calendar shape:

```yaml
schema_version: 2
entries:
  - calendar_id: ecmwf_open_data_mx2t6_high
    source_id: ecmwf_open_data
    track: mx2t6_high
    plane: forecast
    timezone: UTC
    parameter: mx2t6
    metric: high
    period_semantics: max temperature at 2m in previous 6h ending at valid step
    expected_members: 51
    partial_policy: BLOCK_LIVE
    live_authorization: true
    source_transport_required: ensemble_snapshots_v2_db_reader
    cycle_profiles:
      - cycle_hours_utc: [0, 12]
        horizon_profile: full
        max_step_hours: 360
        live_max_step_hours: 240
        safe_fetch:
          derived_0_240_not_before_minutes: 485
          conservative_not_before_minutes: 505
        full_horizon_live_authorization: true
      - cycle_hours_utc: [6, 18]
        horizon_profile: short
        max_step_hours: 144
        safe_fetch:
          conservative_not_before_minutes: 285
        full_horizon_live_authorization: false
        live_authorization: false
        reason: 06/18 cycles cannot cover full configured future horizon
```

Safe-run selector:

```python
select_source_run_for_target_horizon(
    now_utc,
    source_id="ecmwf_open_data",
    track="mx2t6_high",
    required_max_step_hours=max_required_step,
    cycle_policy="latest_complete_full_horizon",
)
```

Return fields:

```text
source_cycle_time
calendar_profile_id
decision: FETCH_ALLOWED | SKIPPED_NOT_RELEASED | HORIZON_OUT_OF_RANGE | BACKFILL_ONLY_BLOCKED
safe_fetch_not_before
max_step_hours
reason_code
```

Required release-calendar tests:

- `test_00z_12z_profiles_allow_full_future_horizon`
- `test_06z_18z_profiles_block_full_horizon_gt_144`
- `test_target_date_required_steps_drive_cycle_selection`
- `test_0730_utc_does_not_authorize_derived_0_240_if_calendar_says_0805`
- `test_partial_window_shadow_or_retry_never_live`
- `test_unknown_cycle_profile_blocks_live`
- `test_calendar_requires_target_horizon_not_just_source_cycle`

## 7. Future Horizon Fetch Plan

Producer fetch plans are driven by:

1. Active Gamma weather markets: city, target local date, metric, condition id, market close/resolution fields.
2. Warm horizon: configured cities and rolling local dates `today_local + 0..target_horizon_days`.
3. Strategy dependency: opening/center/shoulder forecast needs; settlement capture remains separately gated.

Fetch plan dataclass:

```python
@dataclass(frozen=True)
class ForecastFetchPlan:
    source_id: str
    track: str
    source_cycle_time: datetime
    release_calendar_key: str
    source_transport: str
    required_scopes: tuple[ForecastTargetScope, ...]
    required_step_hours: tuple[int, ...]
    max_required_step_hour: int
    expected_members: int
    safe_fetch_not_before: datetime
    live_authorization: bool
    reason_code: str | None
```

Target scope dataclass:

```python
@dataclass(frozen=True)
class ForecastTargetScope:
    city_id: str
    city_name: str
    city_timezone: str
    target_local_date: date
    temperature_metric: Literal["high", "low"]
    physical_quantity: str
    observation_field: str
    data_version: str
    target_window_start_utc: datetime
    target_window_end_utc: datetime
    required_step_hours: tuple[int, ...]
    market_refs: tuple[str, ...]
```

Required-step computation:

1. Convert city-local day start/end to UTC using the city IANA timezone.
2. Treat `mx2t6` and `mn2t6` as 6-hour period aggregates ending at forecast step.
3. The target UTC day window is half-open: `[target_window_start_utc, target_window_end_utc)`.
4. Each ECMWF 6-hour aggregate covers `(valid_time_utc - 6h, valid_time_utc]`.
5. The canonical extractor helper must include a step iff its aggregate interval intersects the half-open target window. This is a period-extrema adapter rule, not hourly truth.
6. Reject required steps outside the selected source run horizon.
7. DST days use actual local-day UTC duration; never assume 24 hours.
8. Tests must cover at least one UTC-negative city, one UTC-positive city, and one DST transition target date at D+10.

Producer fetches the union of required steps across scopes.

Large GRIB resilience:

- lock by source_run_id/track;
- durable `job_run` status;
- write `source_run(status=RUNNING)` before subprocess;
- update `FAILED`, `PARTIAL`, or `SUCCESS` truthfully;
- checkpoint download/extract/ingest stages;
- zero-row success is forbidden.

## 8. Schema Plan

Add executable source linkage to `ensemble_snapshots_v2`:

```sql
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_id TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_transport TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_run_id TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN release_calendar_key TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_cycle_time TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_release_time TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_available_at TEXT;
```

Indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_ens_v2_source_run
ON ensemble_snapshots_v2(source_id, source_transport, source_run_id);

CREATE INDEX IF NOT EXISTS idx_ens_v2_entry_lookup
ON ensemble_snapshots_v2(
  city,
  target_date,
  temperature_metric,
  source_id,
  source_transport,
  data_version,
  source_run_id
);
```

Rules:

- `source_id IS NULL` means not executable.
- `source_transport IS NULL` means not executable.
- `source_run_id IS NULL` means not executable.
- `release_calendar_key IS NULL` means not executable.
- Existing rows stay training/shadow unless explicitly backfilled with provenance.

Add `source_run_coverage` rather than overloading a run-level source row:

```sql
CREATE TABLE IF NOT EXISTS source_run_coverage (
  coverage_id TEXT PRIMARY KEY,
  source_run_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_transport TEXT NOT NULL,
  release_calendar_key TEXT NOT NULL,
  track TEXT NOT NULL,
  city_id TEXT NOT NULL,
  city TEXT NOT NULL,
  city_timezone TEXT NOT NULL,
  target_local_date TEXT NOT NULL,
  temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
  physical_quantity TEXT NOT NULL,
  observation_field TEXT NOT NULL,
  data_version TEXT NOT NULL,
  expected_members INTEGER NOT NULL,
  observed_members INTEGER NOT NULL,
  expected_steps_json TEXT NOT NULL,
  observed_steps_json TEXT NOT NULL,
  snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
  target_window_start_utc TEXT NOT NULL,
  target_window_end_utc TEXT NOT NULL,
  completeness_status TEXT NOT NULL CHECK (
    completeness_status IN ('COMPLETE','PARTIAL','MISSING','HORIZON_OUT_OF_RANGE','NOT_RELEASED')
  ),
  readiness_status TEXT NOT NULL CHECK (
    readiness_status IN ('LIVE_ELIGIBLE','SHADOW_ONLY','BLOCKED','UNKNOWN_BLOCKED')
  ),
  reason_code TEXT,
  computed_at TEXT NOT NULL,
  expires_at TEXT,
  UNIQUE (
    source_run_id,
    source_id,
    source_transport,
    release_calendar_key,
    track,
    city_id,
    city_timezone,
    target_local_date,
    temperature_metric,
    data_version
  )
);
```

  `source_run_id` should still be generated as globally unique for source/cycle/track/transport, but the coverage table unique contract must include the first-class dimensions so a future source-run id bug cannot merge coverage across transports, calendars, or tracks.

Keep PR45 `job_run`, `source_run`, `readiness_state`, and `market_topology_state` substrate.

## 9. Open Data Producer Truth

Replace `_default_cycle()` with:

```python
select_open_data_source_run(
    now_utc,
    track,
    required_max_step_hour,
    rollout_mode,
    allow_short_horizon=False,
)
```

Producer workflow:

1. Build `ForecastFetchPlan`.
2. If not released, write source/job run status and blocked producer readiness for impacted scopes.
3. If horizon out of range, block uncovered scopes.
4. Acquire `ecmwf_open_data/{track}/{source_cycle_time}` lock.
5. Write `job_run(RUNNING)` and `source_run(RUNNING)`.
6. Download required Open Data GRIB.
7. Extract all required future target-local-date rows.
8. Ingest into `ensemble_snapshots_v2` with explicit linkage.
9. Write `source_run_coverage` per city/date/metric.
10. Write producer readiness per city/date/metric.
11. Aggregate high/low parent job status truthfully.

`ingest_grib_to_snapshots.py` must accept source-run context:

```python
@dataclass(frozen=True)
class SourceRunContext:
    source_id: str
    source_transport: str
    source_run_id: str
    release_calendar_key: str
    source_cycle_time: datetime
    source_release_time: datetime
    source_available_at: datetime | None
```

`available_at` must be `source_available_at or source_release_time`, not `issue_time`.

Producer `LIVE_ELIGIBLE` requires:

- `source_run.status = SUCCESS`;
- `source_run.completeness_status = COMPLETE`;
- `source_run_coverage.completeness_status = COMPLETE`;
- every required step exists;
- `observed_members >= expected_members`;
- v2 row has explicit source linkage;
- v2 row target date equals market target local date;
- metric matches high/low market;
- `source_transport = ensemble_snapshots_v2_db_reader`;
- `source_available_at <= readiness_computed_at`;
- readiness not expired.

Required blocker reasons:

- `FUTURE_TARGET_DATE_NOT_COVERED`
- `SOURCE_RUN_NOT_RELEASED`
- `SOURCE_RUN_PARTIAL`
- `SOURCE_RUN_FAILED`
- `SOURCE_RUN_HORIZON_OUT_OF_RANGE`
- `MISSING_REQUIRED_STEPS`
- `MISSING_EXPECTED_MEMBERS`
- `SNAPSHOT_SOURCE_LINKAGE_MISSING`
- `SNAPSHOT_TARGET_DATE_MISMATCH`
- `SNAPSHOT_METRIC_MISMATCH`
- `SOURCE_AVAILABLE_AFTER_READINESS`
- `READINESS_EXPIRED`

## 10. Executable Forecast Reader

Add:

```text
src/data/executable_forecast_reader.py
```

Public function:

```python
read_executable_forecast(
    conn,
    *,
    city_id: str,
    city_name: str,
    city_timezone: str,
    target_local_date: date,
    temperature_metric: Literal["high", "low"],
    source_id: str,
    source_transport: str,
    data_version: str,
    track: str,
    strategy_key: str,
    market_family: str,
    condition_id: str,
    decision_time: datetime,
) -> ExecutableForecastBundle
```

Reader requirements:

1. Query producer readiness and entry readiness.
2. Query `source_run_coverage`.
3. Query linked `ensemble_snapshots_v2` rows.
4. Require explicit source linkage.
5. Require successful source run.
6. Require non-expired readiness.
7. Validate member count and unit.
8. Validate local-day scope.
9. Validate issue/release/available/fetch/capture timing order.
10. Validate decision-time causality:

```text
source_available_at <= captured_at <= readiness_computed_at <= decision_time
```

Evidence bundle:

```python
@dataclass(frozen=True)
class ExecutableForecastEvidence:
    forecast_source_id: str
    forecast_data_version: str
    source_transport: str
    source_run_id: str
    release_calendar_key: str
    coverage_id: str
    producer_readiness_id: str
    entry_readiness_id: str
    source_cycle_time: str
    source_issue_time: str
    source_release_time: str
    source_available_at: str
    captured_at: str
    input_snapshot_ids: tuple[int, ...]
    raw_payload_hash: str | None
    manifest_hash: str | None
    target_local_date: str
    city_timezone: str
    required_steps: tuple[int, ...]
    observed_steps: tuple[int, ...]
    expected_members: int
    observed_members: int
```

If evaluator compatibility needs synthetic arrays, call the adapter `period_extrema_members_adapter`, never hourly truth.

## 11. Calibration Transfer Policy

Policy object:

```yaml
policy_id: ecmwf_open_data_uses_tigge_localday_cal_v1
forecast_authority_family: ecmwf_ifs_ens
forecast_source_id: ecmwf_open_data
forecast_data_versions:
  high: ecmwf_opendata_mx2t6_local_calendar_day_max_v1
  low: ecmwf_opendata_mn2t6_local_calendar_day_min_v1
calibration_source_id: tigge
calibration_data_versions:
  high: tigge_mx2t6_local_calendar_day_max_v1
  low: tigge_mn2t6_local_calendar_day_min_v1
input_space: local_calendar_day_member_extrema
mode: SHADOW_ONLY
operator_approval_required: true
```

Evidence required before live:

- Same ECMWF IFS ENS family.
- Same ensemble member count: 51.
- Same `mx2t6`/`mn2t6` period semantics.
- Same local-calendar-day extraction algorithm.
- Same unit and members-unit normalization.
- Grid-distance evidence acceptable.
- Overlapping Open Data vs TIGGE sample comparison.
- Calibration performance comparison.
- Operator live-money approval.

Every Open Data decision must persist:

```text
forecast_source_id
forecast_data_version
forecast_source_transport
forecast_source_run_id
forecast_coverage_id
producer_readiness_id
entry_readiness_id
forecast_input_snapshot_ids
calibration_source_id
calibration_data_version
calibration_input_space
calibrator_model_key
calibration_policy_id
calibration_mode
```

Forbidden:

- Open Data forecast evidence written as TIGGE data_version.
- TIGGE archive row used as same-day live source.
- Missing calibration policy defaults to live.

## 12. Entry Readiness Composition

Producer readiness scope:

```text
city_id / city_timezone / target_local_date / metric / source_id / transport / source_run / data_version
```

Entry readiness scope:

```text
city_id / target_local_date / metric / strategy_key / market_family / condition_id
```

Entry readiness depends on:

- producer readiness;
- market topology readiness;
- source contract status;
- calibration transfer policy;
- rollout mode;
- strategy dependency;
- operator downgrade/block.

Quote freshness remains submit-stage, not entry readiness.

Producer readiness persistence law:

- `source_run_coverage` is the canonical producer-coverage table for source/run/transport/calendar/track/city/date/metric completeness.
- `readiness_state` may compose or mirror producer readiness, but any producer readiness evidence must carry `coverage_id` and `producer_readiness_id` separately from `entry_readiness_id`.
- A decision cannot persist a single ambiguous `readiness_id` for both data-production and entry authorization.

Status mapping:

| Producer | Topology | Calibration | Rollout | Entry |
| --- | --- | --- | --- | --- |
| live | current | live | live | `LIVE_ELIGIBLE` |
| live | current | shadow | shadow/live | `SHADOW_ONLY` |
| blocked | any | any | any | `BLOCKED` |
| live | stale | any | any | `BLOCKED` |
| live | current | live | blocked | `BLOCKED` |
| missing | any | any | any | `UNKNOWN_BLOCKED` |

## 13. Health And Status

`ingest_status.json` and healthcheck must show separately:

1. Source reachability.
2. Source-run status.
3. Future target-date coverage.
4. Producer readiness.
5. Entry readiness.
6. Market topology readiness.
7. Calibration policy readiness.
8. Rollout mode.
9. Direct-fetch bypass blockers.

Closed enum blockers:

- `ZERO_EXECUTABLE_OPENDATA_ROWS`
- `NO_FUTURE_TARGET_DATE_COVERAGE`
- `SOURCE_RUN_FAILED`
- `SOURCE_RUN_PARTIAL`
- `SOURCE_RUN_NOT_RELEASED`
- `SOURCE_RUN_HORIZON_OUT_OF_RANGE`
- `V2_SOURCE_LINKAGE_MISSING`
- `CALIBRATION_POLICY_SHADOW_ONLY`
- `ENTRY_FORECAST_ROLLOUT_BLOCKED`
- `MARKET_TOPOLOGY_STALE`
- `ALL_CANDIDATES_FORECAST_BLOCKED`
- `DIRECT_FETCH_ENTRY_PATH_BLOCKED`

Rule:

```text
source_health green + zero executable future v2 rows = blocked, not healthy
```

## 14. Evaluator And Monitor Wiring

Replace entry-primary forecast fetch with the executable reader behind `entry_forecast.rollout_mode`.

Bypass antibody:

```text
No evaluator new-entry path may call fetch_ensemble(..., role="entry_primary") after this cutover.
```

The contract is path-based, not model-string-based. Open Data direct fetch, Open-Meteo fallback, TIGGE archive, or any future model string must route through `read_executable_forecast` for forecast entry.

Rollout modes:

| Mode | Behavior |
| --- | --- |
| `blocked` | no live, no sizing |
| `shadow` | compute diagnostic edge, persist shadow evidence |
| `canary` | live only for allowlist/cap and operator proof |
| `live` | normal live, still readiness-gated |

Open-Meteo ensemble remains diagnostic/monitor fallback only. If monitor fallback uses direct ensemble, label it `monitor_only_degraded` and never use it as new-entry authority.

Before sizing, evaluator must have forecast evidence, readiness evidence, calibration policy evidence, topology evidence, and rollout evidence. Missing evidence blocks live sizing.

## 15. Test Plan

Highest-priority new test file:

```text
tests/test_opendata_future_target_contract.py
```

Required future target-date tests:

- `test_source_cycle_date_is_not_target_local_date`
- `test_fetching_today_source_run_does_not_authorize_only_today_target`
- `test_future_target_date_requires_matching_v2_row`
- `test_today_target_row_cannot_authorize_future_market`
- `test_active_market_future_dates_drive_fetch_plan`
- `test_warm_horizon_dates_drive_fetch_plan_when_no_market_yet`
- `test_city_local_day_window_computes_required_steps`
- `test_dst_target_day_required_steps_are_not_24h_assumption`
- `test_dplus10_required_steps_utc_negative_city_do_not_exceed_profile_silently`
- `test_dplus10_required_steps_utc_positive_city_do_not_exceed_profile_silently`
- `test_00z_full_horizon_covers_dplus10_when_steps_present`
- `test_06z_short_horizon_blocks_dplus10`
- `test_missing_required_step_blocks_producer_readiness`
- `test_missing_future_target_scope_blocks_entry_readiness`

Retain PLAN_v3 tests:

- Open-Meteo ECMWF cannot be entry-primary.
- `fetch_ensemble(model="ecmwf_open_data", role="entry_primary")` cannot bypass DB reader.
- Open Data v2 row without source linkage blocks entry.
- failed/partial source run blocks entry.
- expired/missing readiness blocks entry.
- source/data-version/source-run mismatches block reader.
- local-day adapter passes DST and non-DST cities.
- calibration transfer fields persist end to end.
- Open Data decision never writes TIGGE `data_version`.
- source health green plus zero executable v2 rows blocks readiness.
- TIGGE archive rows cannot authorize same-day live entry.
- startup catch-up child failure writes failed source-run/job status.

Release-calendar tests:

- `test_cycle_profiles_exist_for_00_12_full_and_06_18_short`
- `test_full_horizon_requires_00_or_12_cycle`
- `test_06_18_cycle_blocks_target_requiring_step_over_144`
- `test_safe_fetch_for_derived_0_240_blocks_0730_when_config_says_0805`
- `test_required_target_horizon_is_input_to_safe_fetch`
- `test_short_horizon_can_shadow_but_not_live_until_enabled`

Schema tests:

- `test_ensemble_snapshots_v2_has_executable_source_columns`
- `test_existing_rows_without_source_linkage_are_shadow_only`
- `test_source_run_coverage_schema_keys_future_target_date`
- `test_source_run_is_run_level_not_global_readiness`
- `test_source_run_coverage_unique_scope`
- `test_live_eligible_requires_expires_at`

Reader tests:

- `test_reader_requires_entry_forecast_config`
- `test_reader_requires_source_transport_db_reader`
- `test_reader_rejects_direct_collect_open_ens_transport`
- `test_reader_rejects_openmeteo_ensemble_entry_primary`
- `test_reader_rejects_tigge_same_day_archive`
- `test_reader_returns_evaluator_bundle_for_future_target`
- `test_reader_causality_requires_available_before_decision`
- `test_reader_evidence_separates_coverage_producer_and_entry_readiness_ids`

Evaluator tests:

- `test_opening_hunt_uses_executable_reader_in_shadow`
- `test_opening_hunt_never_calls_fetch_ensemble_entry_primary_after_cutover`
- `test_shadow_mode_computes_edge_but_places_no_order`
- `test_canary_mode_requires_operator_evidence`
- `test_forecast_provenance_persisted_before_sizing`
- `test_missing_calibration_policy_blocks_sizing`
- `test_open_data_decision_never_persists_tigge_data_version`

## 16. Hidden Branch Register

| Branch | Risk | Decision | Test/gate |
| --- | --- | --- | --- |
| Fetch today source run only | no future market coverage | forbid | `test_fetching_today_source_run_does_not_authorize_only_today_target` |
| Source run date mistaken for target local date | wrong market authorized | forbid | `test_source_cycle_date_is_not_target_local_date` |
| Today target row authorizes D+N market | false live green | forbid | `test_today_target_row_cannot_authorize_future_market` |
| 06/18 cycle treated as full horizon | missing future steps | block | `test_06z_short_horizon_blocks_dplus10` |
| 00/12 source run fetched before derived full horizon | partial data marked complete | block/retry | safe-fetch tests |
| `available_at=issue_time` for Open Data | causality false proof | forbid | reader causality tests |
| v2 row lacks source_run_id | unlinked data live | shadow only | schema/reader tests |
| Open Data evidence written as TIGGE version | provenance corruption | forbid | decision persistence test |
| TIGGE archive entry-primary | same-day hindsight leak | forbid | TIGGE same-day test |
| Open-Meteo ensemble authorized for entry | degraded fallback becomes money source | forbid | source gate test |
| Source health green but no future v2 rows | false readiness | block | health/readiness test |
| Calibration transfer silently live | unproven model transfer | shadow | calibration policy test |
| Backfill row live-authorizes | hindsight leakage | shadow | backfill causality test |
| High row authorizes low | metric contamination | forbid | metric scope tests |
| UTC day used instead of city-local day | wrong settlement date | forbid | local-day/DST tests |
| Active market topology stale | wrong condition/token/bin | block | topology readiness test |
| Quote freshness treated as entry readiness | stale execution price | submit-stage only | quote-not-entry test |
| Old live readiness survives failed source run | stale green | invalidate | failure overwrite test |
| Startup high succeeds, low fails but parent success | partial producer truth | aggregate failed/partial | child failure test |
| Large GRIB timeout returns OK | silent zero-row success | failed job_run | subprocess/stage test |

## 17. Implementation Roadmap

### Phase 0: Fresh worktree and evidence lock

Objective: start from `main` or explicit operator-selected base, not PR46.

Tasks:

- Confirm current main includes PR45 substrate.
- Record `entry_forecast` absence.
- Record `ensemble_snapshots_v2` missing executable source columns.
- Record `_default_cycle()` and startup wording risk.
- Add no runtime behavior.

Gate:

```bash
python -m pytest tests/test_release_calendar.py tests/test_readiness_state.py -q
```

### Phase 1: Future target-date contract tests

Objective: fail first on the exact "only today data" category.

Files:

- `tests/test_opendata_future_target_contract.py`
- `tests/test_release_calendar.py`
- `tests/test_executable_forecast_reader.py`

Expected failures before fix:

- future target coverage functions absent;
- calendar has no cycle profiles;
- v2 source linkage columns absent.

### Phase 2: Config contract

Add strict `entry_forecast` accessors, default rollout blocked, and tests.

### Phase 3: Schema

Add v2 executable source columns, source_run_coverage table, indexes, and legacy NULL shadow behavior.

### Phase 4: Release calendar v2

Add cycle profiles, target-horizon-aware selector, 00/12 full horizon, 06/18 short horizon, conservative safe-fetch times.

### Phase 5: Open Data producer fetch plan

Replace `_default_cycle()`, build FetchPlan from active markets and warm horizon, compute required steps, write source_run/source_run_coverage/readiness, aggregate status truthfully.

### Phase 6: GRIB ingester linkage

Accept SourceRunContext, write source columns, fix `available_at`, reject live ingest without source context.

### Phase 7: Producer readiness builder

Compute producer readiness per future target date; invalidate stale green rows on failed/partial/missing/horizon-out-of-range.

### Phase 8: Executable forecast reader

Read by city/date/metric/source/version/transport/source_run, validate source run + coverage + v2 rows + readiness, return evidence bundle.

### Phase 9: Calibration transfer policy

Define named Open Data/TIGGE transfer policy, default `SHADOW_ONLY`, persist split provenance, block live until approved.

### Phase 10: Health/status/readiness

Report future coverage counts, source-run vs producer readiness, blocker enums, all-candidate forecast blocked state.

### Phase 11: Evaluator and monitor shadow wiring

Route opening-hunt through executable reader in shadow first. Preserve diagnostic fallback separately. Persist evidence. Ensure no orders in shadow.

### Phase 12: Canary

Requires operator live-money approval, G1/live-readiness evidence, tiny cap/allowlist, and rollback on provenance mismatch.

## 18. Acceptance Gates

PLAN_v4 accepted only if it explicitly includes:

- future target-local-date coverage;
- source cycle vs target date separation;
- required-step computation;
- `max_required_step_hour` as the live horizon authority, not local-day offset alone;
- cycle-specific ECMWF horizon;
- v2 source linkage;
- transport-aware entry gate;
- calibration transfer default shadow;
- decision provenance persistence.

Implementation may not start until the amendment set is present in this plan: exact 6-hour period interval law, horizon-by-step arithmetic, separate coverage/producer/entry readiness evidence ids, coverage uniqueness across transport/calendar/track, and evaluator direct-fetch bypass tests.

Producer accepted only if:

- a source run writes future city/date/metric coverage;
- 06/18 cycles cannot over-authorize full horizon;
- 00/12 cycle coverage is proven by required steps;
- every active market target date is covered or blocked with reason;
- no zero-row success;
- high/low child failures aggregate truthfully.

Reader accepted only if:

- old v2 rows without linkage are rejected;
- missing future target date is rejected;
- source-run failed/partial/not-released is rejected;
- calibration shadow-only prevents live sizing;
- decision evidence carries forecast/calibration split.

Live canary accepted only if:

- operator live-money-deploy-go exists;
- rollout mode canary;
- tiny cap/allowlist;
- producer readiness live;
- entry readiness live;
- calibration policy live/canary;
- topology current;
- submit-stage quote gate still blocks stale orderbook.

## 19. Not Now

- Do not implement in PR46 healthcheck branch.
- Do not change `ecmwf_open_data` registry directly to static `entry_primary`.
- Do not authorize Open-Meteo ECMWF for entry-primary.
- Do not use TIGGE archive to unblock same-day live orders.
- Do not treat source health or RiskGuard green as entry readiness.
- Do not treat a fetched source run as future target-date coverage.
- Do not treat today's target row as D+N market coverage.
- Do not treat 06/18 cycles as full horizon.
- Do not call synthetic extrema adapter hourly data.
- Do not write Open Data evidence under TIGGE data_version.
- Do not promote calibration transfer to live without evidence and operator approval.
- Do not remove PR42/PR45 safety tests before replacement tests pass.

## 20. External Reality References

- ECMWF Open Data documentation: https://www.ecmwf.int/en/forecasts/datasets/open-data
- ECMWF TIGGE FAQ: https://confluence.ecmwf.int/display/TIGGE/FAQ
- ECMWF dissemination schedule: https://confluence.ecmwf.int/display/DAC/Dissemination%20schedule
- ecmwf-opendata client notes: https://pypi.org/project/ecmwf-opendata/
