# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: AGENTS.md money path; docs/operations/AGENTS.md packet routing; topology route "packetized plan for data pipeline live rootfix based on read-only diagnosis"; read-only live DB/process probes from 2026-05-15; user correction 2026-05-15 requiring live verification, not shadow acceptance; src/data/executable_forecast_reader.py; src/data/ecmwf_open_data.py; scripts/ingest_grib_to_snapshots.py; src/engine/evaluator.py.

# Data Pipeline Live Rootfix Plan

Status: IMPLEMENTATION_GATED_BY_LIVE_DEPLOYMENT.

This packet supersedes the 2026-05-14 plan as the active rootfix plan for the
current live data pipeline failure. The earlier plan was useful but
insufficient: it focused on adding a dedicated forecast daemon, 429 handling,
and readiness wiring, but it did not empirically prove that live could consume
the data and it missed two root causes now proven by live read-only probes:

- unqualified SQL reads allow empty trade/world shadow tables to hide the real
  forecast authority rows in `state/zeus-forecasts.db`;
- the OpenData ingest loop reprocesses historical extracted JSON under the
  newest `source_run_id`, corrupting source-run attribution and making the
  latest run contain past target dates.

No completion claim is allowed until a real live end-to-end probe shows:

`forecast source_run -> source_run_coverage -> producer readiness -> executable forecast reader -> evaluator live cutover`

on the same DB files and live process wiring used by `src.main`, with measured
timings. Read-only live diagnostics are allowed; shadow DBs, mocked daemons, or
offline fixtures are not acceptance evidence. Venue side effects remain outside
this packet unless the operator explicitly authorizes an execution test.

## Current Branch Status - 2026-05-15

The rootfix implementation is staged in branch
`feat/data-pipeline-rootfix-2026-05-15`, not in the active live checkout.

Code-level gates now pass:

- topology admission and planning-lock for this packet;
- schema hash pin with `SCHEMA_VERSION=3` and `SCHEMA_FORECASTS_VERSION=3`;
- forecast-live/job_run relationship tests;
- OpenData fetch, source-run attribution, target-local coverage, reader
  authority, and forecast DB split tests.
- implementation critic REVISE items for selected-cycle binding and
  `dual_run_lock.py` source rationale were resolved in branch tests and
  topology.
- handoff critic REVISE item for live process overmatching was resolved by
  strict daemon-command matching and `tests/test_check_data_pipeline_live_e2e.py`.

The live gate does not pass yet. The unchanged live verifier still fails because
the running machine uses legacy `python -m src.ingest_main` as forecast owner,
the live forecasts DB lacks `source_run_coverage` and `readiness_state`, and
the latest live `source_run` still includes target dates before its source
cycle. Therefore this branch may be handed off as the implementation candidate,
but the data pipeline is not empirically fixed until the branch is deployed to
the live checkout, live forecast ownership is switched to the dedicated owner,
the forecasts DB schema is initialized by that live path, and the live verifier
passes.

## Empirical Baseline

Read-only probes on 2026-05-15 found:

1. Active processes are still legacy mixed daemons:
   - `python -m src.ingest_main` PID 63759, cwd repo root, open DB
     `state/zeus-forecasts.db`.
   - `python -m src.main` PID 63761, cwd repo root.
   - The dedicated forecast live daemon is not the running data owner.

2. Latest forecast data exists, but only in the forecasts DB:
   - `state/zeus-forecasts.db.source_run` latest:
     `ecmwf_open_data:mx2t6_high:2026-05-14T00Z`.
   - status `PARTIAL`, completeness `PARTIAL`, captured at
     `2026-05-14T16:55:50.363673+00:00`.
   - `state/zeus-forecasts.db.ensemble_snapshots_v2` has 3293 rows for that
     source run.
   - `state/zeus-world.db` has zero `source_run` and zero
     `ensemble_snapshots_v2` rows for that source run.
   - `state/zeus_trades.db` has zero `source_run`, zero
     `source_run_coverage`, and zero `ensemble_snapshots_v2` rows for that
     source run.

3. Live reader cannot consume the available forecast data:
   - A direct `read_executable_forecast(...)` call through
     `get_trade_connection_with_world()` attached `main`, `world`, and
     `forecasts`.
   - It returned `BLOCKED / PRODUCER_READINESS_MISSING` in 0.279 ms.
   - This is expected from code: `src/data/executable_forecast_reader.py:193`
     reads bare `readiness_state`, `:233` reads coverage through the bare repo
     helper, `:264` reads bare `ensemble_snapshots_v2`, and `:438` reads bare
     `source_run`. On a trade connection, bare names resolve to `main` before
     attached schemas.

4. The latest source run is semantically contaminated:
   - `source_run.valid_time_start = 2026-05-06`,
     `valid_time_end = 2026-05-20`.
   - The same `source_run_id=ecmwf_open_data:mx2t6_high:2026-05-14T00Z`
     contains `target_date=2026-05-06` rows.
   - That cannot be a valid May 14 forecast source-run attribution for live
     entry; it proves historical JSON is being reprocessed under a new
     `SourceRunContext`.

5. The ingest path is not live-efficient:
   - `logs/zeus-ingest.err` showed
     `loop_progress track=mx2t6_high i=1600/3293 elapsed_ms=39011255`.
   - That is about 39,011 seconds for 1600 files, or roughly 10.84 hours
     halfway through one track.
   - The code explains the shape: `scripts/ingest_grib_to_snapshots.py:760`
     rglobs all historical JSON under the extracted track directory, then
     `:807` loops every file. The OpenData caller passes
     `date_from=None`, `date_to=None`, `cities=None`, and `overwrite=True` at
     `src/data/ecmwf_open_data.py:819`.

6. Partial-run semantics are over-broad:
   - `src/data/ecmwf_open_data.py:365` marks a run `PARTIAL` whenever the
     overall run lacks long-horizon steps.
   - `src/data/forecast_target_contract.py:173` adds
     `SOURCE_RUN_PARTIAL` to every target when the source run is not globally
     complete.
   - `src/data/ecmwf_open_data.py:468` then requires global run completeness
     for per-target `LIVE_ELIGIBLE`.
   - A target covered by available short-horizon steps can therefore be blocked
     solely because later not-yet-released horizons are absent.

7. HTTP 429 is not the current empirical blocker in the live probe, but it
   remains part of the repaired contract:
   - `src/data/ecmwf_open_data.py:696` has bounded retry logic and
     `Retry-After` parsing hooks.
   - The rootfix must keep provider-precise retry behavior and telemetry, but
     not mislabel 429 as the reason live cannot read current rows.

## Root Cause Judgment

The data pipeline is not fixed. The true failure is not one missing condition;
it is a broken authority relationship between producer, store, and consumer.

### R1 - Forecast authority is split across DBs and read through unqualified tables.

The writer currently produces fresh snapshots/source_run in forecasts DB, while
the live reader uses a trade connection and unqualified table names. Empty or
stale shadow tables in `main` and `world` satisfy SQLite name resolution before
the real `forecasts` tables. Live sees "missing readiness" even while forecast
rows exist.

Design failure: table ownership is implicit and caller-dependent.

Required structural fix: one canonical forecast authority store with qualified
reads/writes. No executable forecast code may rely on bare table names when a
multi-DB connection is possible.

### R2 - Source-run attribution is corrupted by historical directory scans.

OpenData calls the generic JSON ingester over the whole extracted directory and
injects the current source-run context for every file. That makes stale target
files look as if they came from the latest source run.

Design failure: source-run identity is applied after file discovery, not bound
to a cycle-scoped manifest.

Required structural fix: file discovery must be source-run scoped before row
construction. A file outside the selected cycle manifest cannot be assigned the
selected `source_run_id`.

### R3 - Readiness and coverage ownership is not co-located with forecast truth.

`source_run`, `source_run_coverage`, `producer_readiness`, and snapshots form
one authority chain, but the chain is persisted/read across inconsistent
schemas. `state/zeus-world.db` contains stale producer readiness from May 4,
`state/zeus_trades.db` contains hot-path `entry_forecast` readiness, and
`state/zeus-forecasts.db` contains the fresh source run/snapshots.

Design failure: a chain that must be atomic is distributed without explicit
schema-qualified repository contracts.

Required structural fix: forecast authority chain rows live in the forecasts
store or are exposed through an explicit store abstraction that resolves each
table to its canonical schema.

### R4 - Run-global completeness is blocking target-local completeness.

A source run can be globally partial because long horizons are not released,
while a near target can still be covered by released steps. Current logic
collapses these into one status and blocks all targets.

Design failure: run transport completeness and target-local executable
coverage are treated as the same predicate.

Required structural fix: source_run status records provider/run outcome;
source_run_coverage records target-local executability. Live uses target-local
coverage plus source lineage, not global "all horizons released".

### R5 - The data daemon is doing historical backfill work on the live path.

The current loop scans 3293 files and has taken more than 10 hours for half of
one track. That is not a live producer; it is a replay/backfill pattern running
inside a live scheduler.

Design failure: live ingestion and historical maintenance share the same file
enumeration and overwrite semantics.

Required structural fix: live path ingests only the selected cycle manifest;
historical backfill becomes a separate offline job with separate locks,
metrics, and admission.

### R6 - Launch wiring still points live at the mixed daemon.

The running machine still has `src.ingest_main` as forecast data owner.
Adding a new daemon without retiring or gating old OpenData jobs would add
complexity without changing reality.

Design failure: process ownership is not part of the acceptance gate.

Required structural fix: old/new OpenData ownership is mutually exclusive and
verified by live process inspection during live verification.

## Non-Goals

- No live order placement, cancellation, sweep, redeem, or venue mutation.
- No deletion, VACUUM, or manual rewrite of production DB files in this plan.
- No calibration refit/promotion.
- No source-role change for settlement or observations.
- No TIGGE activation.
- No attempt to clean the broader duplicate-DB estate until the live forecast
  chain has an empirical proof and a rollback path.

## Target Architecture

The repaired live data path is:

`release calendar -> selected OpenData cycle -> cycle file manifest -> forecast authority store -> source_run -> source_run_coverage -> producer_readiness -> executable_forecast_reader -> evaluator`

Properties:

- source_run identity is created before download/extract and carried into a
  cycle manifest;
- JSON files not in that manifest cannot be ingested for the source run;
- all forecast authority reads use the forecasts schema or a typed store API;
- live reader returns a bundle only when source lineage, target coverage,
  producer readiness, snapshot membership, and timing order all hold;
- evaluator never direct-fetches OpenData when the executable reader is
  enabled;
- data daemon telemetry is stage-separated and measured in seconds.

## Live Verification Contract

For this packet, "live verification" means the program is exercised against the
actual live repo checkout, live process ownership, and the real runtime DB files
that `src.main` and the data producer use. Shadow verification can be a local
development aid only; it cannot satisfy a gate or completion claim.

Required live gates:

1. Process ownership gate:
   - inspect active process command lines;
   - prove exactly one OpenData forecast owner is active;
   - prove the owner is the intended live forecast producer, not an orphaned
     compatibility path.

2. Runtime DB gate:
   - attach and print the exact DB files used by live;
   - prove forecasts, coverage, readiness, and reader all resolve through the
     same canonical authority chain;
   - fail if a shadow/empty table in `main` or `world` masks forecasts truth.

3. Program gate:
   - every new checker, daemon entry point, reader, and ingestion path must be
     run against live paths before it can be called complete;
   - unit tests remain necessary but never replace the live gate.

4. Safety boundary:
   - data-pipeline live verification may write forecast authority rows only
     when the step explicitly declares a live no-venue mode and has rollback
     backup evidence;
   - venue submit/cancel/sweep/redeem remains prohibited in this packet.

## Timing Contract

External provider download is not fully controllable, so timing claims are split
between external fetch and internal live pipeline.

Acceptance budgets on this machine:

1. Read-only live verifier:
   - total `forecast -> reader -> evaluator-like` proof: <= 3.0 seconds warm;
   - single executable forecast reader call: <= 250 ms p95 over 20 candidates;
   - no query may scan historical snapshots without a source_run or target
     predicate.

2. No-network current-cycle ingest from already extracted files:
   - cycle manifest build: <= 2.0 seconds;
   - DB ingest plus authority-chain write for one track: <= 120 seconds initial
     gate;
   - stretch target after indexes/batching: <= 60 seconds;
   - writer lock held only during DB writes, not during download/extract/file
     discovery.

3. Full forecast producer cycle:
   - reports separate `download_seconds`, `extract_seconds`,
     `manifest_seconds`, `db_ingest_seconds`, `authority_seconds`,
     `commit_seconds`, `total_seconds`, `retry_sleep_seconds`;
   - HTTP 429 sleeps exactly provider `Retry-After` when parseable;
   - invalid/missing `Retry-After` sleeps local fallback;
   - final failed attempt does not sleep.

Any final statement like "highest efficiency" must cite the measured values
from these gates.

## Implementation Phases

### Phase 0 - Freeze evidence and replace contaminated PR path.

Tasks:

- Work only in clean branch `feat/data-pipeline-rootfix-2026-05-15`.
- Do not continue PR #115 as implementation baseline.
- Add this plan and critic review as packet evidence.
- Capture a read-only verifier before code changes.

Acceptance:

- `git status --short --branch` is clean before source edits.
- Topology admits the packet plan.
- Plan critic returns APPROVE or concrete REVISE items.
- Implementation topology admits the actual root-cause files. As of the first
  live-gate run, `data daemon live efficiency refactor implementation` admits
  `src/data/ecmwf_open_data.py` but blocks `scripts/ingest_grib_to_snapshots.py`,
  even though source-run attribution contamination occurs in the generic
  ingester's all-history file enumeration. That profile must be expanded before
  Phase 3 source edits; bypassing it would recreate the previous patch drift.

### Phase 1 - Build the live verifier first.

Add `scripts/check_data_pipeline_live_e2e.py` as a live checker. Its default
mode is read-only because current goal is data-pipeline proof, not venue action.

The checker must:

- open `state/zeus_trades.db`, attach `state/zeus-world.db` as `world`, and
  `state/zeus-forecasts.db` as `forecasts`;
- print `PRAGMA database_list`;
- inspect active live process command lines and cwd for the data producer and
  `src.main`;
- avoid counting pytest/checker commands as forecast-live owners;
- report per-schema table presence and row counts for
  `ensemble_snapshots_v2`, `source_run`, `source_run_coverage`,
  `readiness_state`;
- select the latest OpenData source run and reject it if its target-date range
  includes dates before `source_cycle_time.date()`;
- run `read_executable_forecast` on at least one current/future candidate;
- time each stage with `time.perf_counter`;
- inspect the live checkout's evaluator cutover markers so reader readiness is
  not the only cutover evidence;
- emit JSON and fail nonzero unless the full chain is live-readable.

Initial expected live result before repair: fail with the current reason
`PRODUCER_READINESS_MISSING` and source-run attribution contamination.

Acceptance:

- The failing checker proves the current failure without writes.
- The checker is later reused unchanged, or only tightened, to prove the fix on
  live DB/process wiring.

### Phase 2 - Introduce a canonical forecast authority store.

Implement a small repository boundary, not ad hoc SQL patches. The current
implementation uses schema-resolution helpers inside
`src/data/executable_forecast_reader.py`; a later extraction to a separate store
file is allowed only if it reduces complexity without changing the contract.

- `src/data/forecast_authority_store.py` or equivalent owns schema-qualified
  access to forecast authority rows;
- snapshots and source_run resolve to forecasts DB;
- source_run_coverage and producer_readiness resolve to the same canonical
  forecast authority store, unless a migration plan explicitly proves a safer
  split;
- multi-DB connections use qualified names such as `forecasts.source_run`;
- bare table reads are forbidden in executable forecast code when attached DBs
  can exist.

Relationship tests:

- create a temp trade DB with empty `main.ensemble_snapshots_v2`,
  `main.source_run`, `main.source_run_coverage`, `main.readiness_state`;
- attach a temp forecasts DB containing valid rows;
- assert executable forecast reader uses forecasts rows and returns
  `EXECUTABLE_FORECAST_READY`;
- assert removing forecasts readiness returns the appropriate blocked reason.

Acceptance:

- The reader cannot be fooled by empty main/world shadow tables.
- Existing tests for missing readiness and partial coverage still fail closed.

### Phase 3 - Stop source-run attribution contamination.

Replace all-history rglob ingestion on the live path with cycle-scoped file
selection.

Precondition:

- topology profile admits `scripts/ingest_grib_to_snapshots.py` and its focused
  test because the contamination category cannot be made impossible from
  `src/data/ecmwf_open_data.py` alone.

Required changes:

- extraction stage returns or writes a manifest of files for the selected
  cycle, track, parameter, and source_run;
- `collect_open_ens_cycle` passes that manifest to the ingester;
- `ingest_track` gains a manifest/file-list path or a strict source-run filter;
- live OpenData calls do not pass `date_from=None`, `date_to=None`,
  `cities=None`, `overwrite=True` over the whole historical directory;
- each row must prove the source_run_id, cycle time, and target date are
  compatible before write.

Relationship tests:

- seed a temp extracted directory with old and current files;
- run a current-cycle ingest;
- assert old target dates are not assigned to the current source_run_id;
- assert `source_run.valid_time_start` is not before the selected
  `source_cycle_time.date()` for live OpenData runs;
- assert historical backfill can still use a separate explicit path, not the
  live scheduler.

Acceptance:

- Latest source_run target-date range no longer includes stale historical
  targets.
- Ingest file count equals the selected manifest count, not the full directory
  count.

### Phase 4 - Repair target-local coverage semantics.

Separate provider/run completeness from target executable coverage:

- source_run records run-level outcome: provider status, observed steps,
  missing released/unreleased steps, expected/observed members;
- source_run_coverage records target-local completeness for each city/date;
- a globally partial source run may produce `LIVE_ELIGIBLE` coverage for
  targets whose required steps and members are present;
- targets requiring unreleased horizons remain blocked with
  `SOURCE_RUN_HORIZON_OUT_OF_RANGE` or `SOURCE_RUN_NOT_RELEASED`.

Relationship tests:

- source_run missing steps 150..282, target requires max step 144:
  coverage is `COMPLETE / LIVE_ELIGIBLE`;
- same source_run, target requires step 150:
  coverage is blocked;
- reader accepts only the first and blocks the second.

Acceptance:

- No target is blocked solely by global `source_run.completeness_status=PARTIAL`
  when target-local required steps are complete.
- Source-run failures still block every target.

### Phase 5 - Make producer readiness co-transactional with forecast truth.

The source-run authority chain must be written and read as one unit:

- `source_run`;
- `source_run_coverage`;
- `producer_readiness`;
- snapshots;
- manifest hash/evidence.

Implementation options:

1. Move `source_run_coverage` and `producer_readiness` into forecasts DB for
   forecast-source strategy key, with migration/read compatibility for old
   rows; or
2. Keep rows in world/trade but access them only through a store that qualifies
   schemas and proves cross-DB transaction timing.

Preferred path is option 1 because this chain is forecast authority, not trade
state.

Acceptance:

- One source-run transaction can create all chain rows before derived exports.
- The live reader sees the same rows without schema ambiguity.
- Stale May 4 world readiness can no longer satisfy or block a May 14 forecast
  source run.

### Phase 6 - Rewire daemon ownership without adding a second owner.

Do not merely add `forecast_live_daemon.py`.

Required:

- a single forecast producer process owns OpenData HIGH/LOW schedules;
- `src.ingest_main` skips OpenData jobs whenever the dedicated forecast daemon
  is enabled;
- startup catch-up exists in only one owner;
- process status and heartbeat identify the forecast owner;
- live verification inspects process command lines before claiming deployment.

Acceptance:

- Unit tests prove old/new scheduler mutual exclusion.
- Live verification reports exactly one OpenData owner.
- No engine/execution imports are introduced into the forecast producer.

### Phase 7 - Preserve and test HTTP 429 behavior.

Keep 429 handling as a support fix, not the main root cause:

- parse `Retry-After` seconds and HTTP-date values;
- fallback for missing/invalid values;
- no sleep after final failed attempt;
- retry timing counted in `retry_sleep_seconds`;
- 429 on one step does not globally throttle unrelated steps.

Acceptance:

- Mocked tests assert exact sleep seconds.
- Cycle telemetry includes retry counts and sleeps.

### Phase 8 - End-to-end empirical live proof.

The repair is complete only after the unchanged verifier from Phase 1 proves:

- real DB files used by live are attached;
- real live process ownership is inspected and matches the intended producer;
- latest source_run has no historical target-date contamination;
- forecasts store contains source_run, snapshots, coverage, and producer
  readiness for the same source_run;
- `read_executable_forecast` returns `LIVE_ELIGIBLE /
  EXECUTABLE_FORECAST_READY` for at least one current/future covered candidate;
- evaluator live cutover consumes the reader bundle and does not direct-fetch;
- measured timings satisfy the timing contract;
- no live venue submit/cancel/sweep/redeem call occurred.

Minimum commands:

```bash
python3 scripts/check_data_pipeline_live_e2e.py --json --live
pytest -q -p no:cacheprovider \
  tests/test_ecmwf_open_data_parallel_fetch.py \
  tests/test_ecmwf_open_data_subprocess_hardening.py \
  tests/test_forecast_live_daemon.py \
  tests/test_job_run_schema.py \
  tests/test_executable_forecast_reader.py \
  tests/test_opendata_future_target_contract.py \
  tests/test_readiness_state_schema.py \
  tests/test_entry_forecast_chain_relationship.py \
  tests/test_opendata_writes_v2_table.py \
  tests/state/test_forecast_db_split_invariant.py
```

If a live DB writeful data cycle is required, it must be explicitly marked as
live no-venue verification and include a rollback copy path before mutation.
Read-only live proof comes first, but read-only proof alone is not final
completion.

## Critic Attack Checklist

The critic must reject the plan if any answer is "no":

1. Does the plan make it impossible for empty trade/world shadow tables to hide
   forecasts DB truth?
2. Does it prevent historical JSON from being assigned to a new source_run?
3. Does it avoid adding a new daemon while leaving the old OpenData owner
   active?
4. Does it prove target-local coverage rather than relying on global run
   completeness?
5. Does it define seconds-level timing gates and the command that measures
   them?
6. Does it preserve fail-closed behavior when readiness, source_run, lineage,
   or coverage is missing?
7. Does it avoid venue side effects and production DB cleanup while still
   requiring live process/DB verification?

`REVISE` is not a pass. Implementation can proceed only after APPROVE or after
the listed blocker is resolved.

## Stop Conditions

Stop and re-plan if implementation requires:

- deleting, VACUUMing, rewriting, or manually editing production DB files;
- changing risk/execution/lifecycle semantics;
- bypassing source lineage checks;
- broad cleanup of duplicate DBs;
- a second OpenData scheduler owner;
- a live venue side effect;
- final claims based only on process liveness, row counts, shadow checks, mocks,
  or plan approval.
