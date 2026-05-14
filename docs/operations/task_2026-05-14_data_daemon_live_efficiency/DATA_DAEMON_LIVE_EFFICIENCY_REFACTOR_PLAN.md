# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: AGENTS.md money path; topology route "operation planning packet: data daemon live efficiency refactor structural decisions, HTTP 429, live readiness wiring, launch maintenance, end-to-end verification plan"; src/data/AGENTS.md; src/state/AGENTS.md; src/engine/AGENTS.md; src/ingest_main.py; src/data/ecmwf_open_data.py; src/data/executable_forecast_reader.py; src/data/producer_readiness.py; src/state/readiness_repo.py.

# Data Daemon Live Efficiency Refactor Plan

Status: APPROVED_PLAN_ONLY after `CRITIC_APPROVAL.md`.

This packet is implementation planning and review evidence. It does not
authorize live venue actions, production DB mutation, DB cleanup, calibration
refit, TIGGE activation, source-routing changes, or daemon launch on the
operator machine. Implementation slices require the dedicated topology profile
`data daemon live efficiency refactor implementation`, planning-lock, tests,
and phase critic review.

## Target

Make live entry forecast data production a bounded, measurable, high-priority
machine path:

`ECMWF release calendar -> OpenData fetch -> extract -> source_run -> source_run_coverage -> producer readiness -> entry readiness -> executable forecast reader -> evaluator`

The refactor is complete only when the live path runs from a dedicated forecast
producer, handles HTTP 429 precisely, publishes readiness before evaluator
evaluation, proves old/new OpenData schedulers cannot both own the job, and
passes end-to-end tests that live trading consumes DB readiness rather than
direct-fetching data.

## Current Failure Model

The present `src/ingest_main.py` is an all-in-one scheduler. It mixes live
OpenData, observations, world maintenance, market/UMA support, status/heartbeat,
and offline/replay-style work in one daemon. Prior patches added executor lanes
and removed TIGGE scheduling, but the daemon still encodes the wrong relation:
unrelated jobs can share launch lifecycle, scheduler health, and queue semantics
with the live forecast producer.

Four concrete gaps must be fixed together:

1. HTTP 429 is treated as generic retryable HTTP with fixed sleep. It does not
   honor `Retry-After`, does not avoid final-attempt sleep, and does not record
   fetch-stage latency separately from ingest/commit latency.
2. Live readiness wiring is not fully daemon-produced. The evaluator still
   materializes `entry_forecast` readiness on its hot path before reading the
   executable forecast.
3. There is no dedicated `forecast-live-daemon` entry point or launch contract.
   Existing external launch surfaces refer to `com.zeus.data-ingest`.
4. Topology historically lacked one route for this cross-module refactor, so
   agents tended to patch one symptom at a time under split profiles.

## Non-Goals

- No live venue submit/cancel/redeem.
- No production DB mutation in tests or planning.
- No DB deletion/consolidation in this packet.
- No settlement/source routing change.
- No `config/cities.json` or `config/settings.json` change.
- No calibration rebuild/refit, Platt parameter promotion, or TIGGE activation.
- No claim that the new daemon is deployed until operator launch wiring is
  explicitly applied outside this repo plan.

## Structural Decisions

### D1 - Forecast production gets its own process boundary.

Add `src/ingest/forecast_live_daemon.py` as the dedicated live forecast producer.
It owns only:

- ECMWF OpenData HIGH/LOW scheduled cycles;
- startup latest-cycle catch-up;
- forecast producer heartbeat;
- source_run/source_run_coverage/producer readiness publication;
- entry readiness publication or a reader contract that removes evaluator-side
  entry-readiness materialization.

It must not own daily/hourly observations, solar, hole scan, station migration,
market scan, UMA, harvester truth, automation analysis, replay, refit, TIGGE,
or generic ingest status rollups.

### D2 - Old and new OpenData owners must be mutually exclusive.

Adding a new daemon is a failure unless the same phase disables or removes the
old `ingest_main.py` OpenData schedules, or proves a single launch mode can
activate only one owner. The minimal implementation is a shared predicate:
`forecast_live_daemon_enabled()` true means `ingest_main.py` skips OpenData
HIGH/LOW and startup catch-up registration.

### D3 - HTTP 429 handling is provider-precise and local.

For each OpenData step:

- token bucket remains the burst guard;
- 429 reads `Retry-After` when present;
- missing/invalid `Retry-After` falls back to `_PER_STEP_RETRY_AFTER`;
- final failed attempt does not sleep;
- a 429 on one step does not globally lower rate for unrelated steps;
- fetch-stage timing is recorded separately from extract/ingest/commit timing.

The target timing policy for tests:

- zero extra sleep after final failed attempt;
- exact provider-advertised sleep for parseable `Retry-After`;
- fallback sleep equal to `_PER_STEP_RETRY_AFTER` for absent/invalid header;
- test cycle runtime bounded by mocked sleeps, not wall-clock network time.

### D4 - Live consumes readiness, not fetches.

The evaluator must not need to write `entry_forecast` readiness at the moment it
is trying to evaluate a trade. The daemon must either:

1. prewrite entry readiness rows for active candidate scopes, or
2. simplify `read_executable_forecast` so producer readiness plus source_run
   coverage is sufficient for entry forecast execution.

Phase 1 chooses option 1 only if candidate scope enumeration is already
available without importing engine/execution. If not, Phase 1 keeps evaluator
entry-readiness writes as a compatibility path but adds tests that prove
prewritten rows are consumed without direct fetch; Phase 2 removes evaluator
write ownership.

### D5 - Measurements are part of the contract.

The daemon must output machine-checkable timing:

- fetch elapsed ms;
- extract elapsed ms;
- DB ingest elapsed ms;
- commit elapsed ms where available;
- total cycle elapsed ms;
- retry count and retry sleep seconds;
- source/mirror for successful fetch.

No "highest efficiency" claim is valid without these fields in returned result
dicts and tests asserting them.

## Phases

### Phase 0 - Topology and packet admission

Done when:

- descriptive packet plans and critic evidence are first-class topology targets;
- dedicated implementation profile admits the cross-module file set;
- focused topology tests and digest export checks pass.

### Phase 1 - 429 precision and fetch telemetry

Implementation:

- add `Retry-After` parser/helper in `src/data/ecmwf_open_data.py`;
- change `_fetch_one_step` retry loop to use per-response sleep;
- avoid sleep after final failed attempt;
- add fetch-stage timing to `collect_open_ens_cycle` result;
- add tests in `tests/test_ecmwf_open_data_parallel_fetch.py`.

Acceptance:

- 429 with `Retry-After: 7` sleeps 7 seconds once;
- 429 with no/invalid header sleeps fallback seconds;
- final failed attempt does not sleep;
- timing fields exist in success/failure result payloads;
- no DB writes occur in fetch worker threads.

### Phase 2 - Dedicated forecast-live-daemon entry point

Implementation:

- create `src/ingest/forecast_live_daemon.py`;
- scheduler includes OpenData HIGH/LOW, startup latest catch-up, and heartbeat
  only;
- startup initializes forecasts schema/sentinel only as needed for forecast
  production;
- `src/ingest_main.py` skips OpenData schedules when the forecast-live-daemon
  mode is enabled.

Acceptance:

- tests prove forecast daemon registers only allowed jobs;
- tests prove old ingest cannot register OpenData when new owner mode is on;
- daemon imports no engine/execution/strategy/signal/main modules;
- forecast heartbeat path is independent from generic ingest heartbeat.

### Phase 3 - Readiness handoff to live

Implementation:

- prove `read_executable_forecast` consumes prewritten producer/entry readiness
  rows and blocks missing/expired/partial rows;
- reduce or quarantine evaluator-side readiness writing behind compatibility
  mode;
- add status output that distinguishes producer readiness vs entry readiness.

Acceptance:

- evaluator live path can use prewritten readiness without direct fetch;
- missing/expired producer or entry readiness blocks;
- partial coverage blocks;
- source_run failure/partial status blocks;
- no readiness shortcut bypasses source_run/source_run_coverage.

### Phase 4 - Launch and maintenance surfaces

Implementation:

- update repo runbook/script references that assume only `com.zeus.data-ingest`
  owns live forecasts;
- document operator launch shape for separate forecast producer;
- keep launch application outside this repo implementation unless explicitly
  authorized by operator.

Acceptance:

- scripts/runbooks no longer instruct operators to solve OpenData freshness by
  restarting the whole mixed ingest daemon;
- status distinguishes forecast producer, world maintenance, and observability.

### Phase 5 - End-to-end smoke

Use mocked/no-network tests first. A live network dry-run can be operator-run
later, but the repo gate must prove the chain without external mutation:

- mocked OpenData fetch produces payload;
- extract/ingest writes source_run/coverage/readiness into temp DB;
- executable reader consumes the rows;
- evaluator path is not allowed to direct-fetch when readiness is missing;
- old/new scheduler ownership is mutually exclusive.

## Critic Gates

Each phase needs critic review before the next phase starts:

- Phase 0 critic: topology did not create a broad bypass.
- Phase 1 critic: 429 handling improves latency without global throttling.
- Phase 2 critic: new daemon reduces, not adds, live-path complexity.
- Phase 3 critic: live path is readiness-consumer, not data producer.
- Phase 4/5 critic: operational docs and tests prove end-to-end behavior.

`REVISE` is unresolved. Completion requires `APPROVE` or a real blocker.

## Stop Conditions

Stop immediately if implementation requires:

- deleting or rewriting DB files;
- production DB mutation;
- live venue side effects;
- calibration refit/promotion;
- TIGGE activation;
- source/settlement routing changes;
- changing risk/execution/lifecycle semantics;
- a second scheduler owning OpenData without mutual exclusion.

