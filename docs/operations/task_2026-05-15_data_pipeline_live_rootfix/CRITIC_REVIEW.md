# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: DATA_PIPELINE_ROOTFIX_PLAN.md; native critic result 2026-05-15; user correction 2026-05-15 requiring live verification instead of shadow acceptance.

# Critic Review - Data Pipeline Live Rootfix Plan

Reviewed artifact:
`docs/operations/task_2026-05-15_data_pipeline_live_rootfix/DATA_PIPELINE_ROOTFIX_PLAN.md`

Verdict: APPROVE_WITH_LIVE_ONLY_ERRATUM.

Approval scope: plan structure only. This does not approve final completion,
venue actions, production DB cleanup, calibration refit, TIGGE activation, or
manual DB mutation.

## Critic Result

The native critic approved the root-cause structure:

- bare forecast reads must be illegal under attached DBs;
- live ingest must scope files to a cycle manifest before assigning
  `source_run_id`;
- run-global completeness must be separated from target-local coverage;
- exactly one OpenData process owner must be active;
- HTTP 429 remains a support contract, not the root cause;
- completion requires an end-to-end proof against the same live DB files.

## User Erratum

The user then corrected the acceptance standard:

`Do not pursue shadow. Every program must pass live verification.`

This erratum narrows the approval. Shadow DBs, mocked daemons, and offline
fixtures may support development, but they cannot satisfy any completion gate.
The plan was revised accordingly:

- packet path changed from `live_shadow_rootfix` to `live_rootfix`;
- verifier script target changed to `scripts/check_data_pipeline_live_e2e.py`;
- final proof now requires real live process ownership and real live runtime
  DB paths;
- read-only live diagnostics are allowed, but final completion requires live
  program verification;
- venue side effects remain prohibited unless separately authorized.

## Residual Risks

1. Forecast readiness/coverage should move into forecasts DB by default. Any
   split-store implementation must prove it cannot reintroduce schema shadowing.
2. The first live proof may cover only HIGH because current empirical failure
   is HIGH. That must be labeled as the first gate; final live completion
   covers HIGH and LOW.
3. `fetch_ensemble` instrumentation must cover diagnostic crosscheck paths, not
   only the primary executable-reader cutover.

## Required First Gate

Build and run the live checker before source fixes:

```bash
python3 scripts/check_data_pipeline_live_e2e.py --json --live
```

It must inspect live process ownership, print attached DB identities and
per-schema counts, time the reader call, and fail on today's live state with
the known failures:

- `PRODUCER_READINESS_MISSING`;
- latest source-run attribution contamination from historical target dates.

## Implementation Critic - 2026-05-15

Verdict: REVISE.

The implementation critic found three branch-handoff blockers:

1. Live ownership was not proven: the live machine still runs legacy
   `src.ingest_main`, so this branch cannot claim live completion.
2. `forecast_live_daemon.run_opendata_track()` selected a job identity but
   called the collector without binding that selected source cycle, allowing
   `job_run` and `source_run` identity drift across a release-calendar boundary.
3. `src/data/dual_run_lock.py` lacked source-rationale coverage, and the live
   verifier covered the executable reader but not evaluator cutover evidence.

Resolution in this branch:

- live ownership remains an explicit external deployment blocker, not a code
  completion claim;
- `forecast_live_daemon` now passes selected `run_date`, `run_hour`, and
  `now_utc` into the collector;
- collector results with mismatched `source_run_id` or `release_calendar_key`
  fail closed and journal a FAILED `job_run`;
- `tests/test_forecast_live_daemon.py` now covers selected-cycle binding and
  source-run identity mismatch;
- `src/data/dual_run_lock.py` has a source-rationale entry and
  `DOUBLE_FORECAST_PRODUCER` hazard badge;
- `scripts/check_data_pipeline_live_e2e.py` now includes a live-checkout
  evaluator cutover static guard. This is supplementary evidence only; final
  live completion still requires a true live reader/evaluator path proof.

## Handoff Critic - 2026-05-15

Verdict: REVISE.

The follow-up critic found two remaining handoff blockers:

1. The live verifier matched any process command containing
   `forecast_live_daemon`, so pytest commands for
   `tests/test_forecast_live_daemon.py` could make `dedicated_forecast_owner`
   pass while live still used legacy `src.ingest_main`.
2. The branch was not ahead of `origin/main`; the work existed only as a dirty
   worktree and would be lost if handed off by branch name.

Resolution in this branch:

- `scripts/check_data_pipeline_live_e2e.py` now matches only real daemon launch
  shapes (`-m src.ingest.forecast_live_daemon` or
  `src/ingest/forecast_live_daemon.py`) and excludes pytest/checker commands;
- `tests/test_check_data_pipeline_live_e2e.py` locks the negative case where a
  pytest command contains `forecast_live_daemon` but must not count as a
  dedicated owner;
- the branch must be committed before it is treated as the durable handoff
  surface.
