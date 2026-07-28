# HKO final-daily continuity — Plan

Date: 2026-07-28
Branch: `fix/hko-final-continuity-20260728`
Status: active

## Background

On 2026-07-28 the live Hong Kong HIGH held position had current HKO
`hko_hourly_accumulator` rows through 23:50 HKT and a current replacement
posterior, but the HKO Daily Extract had not yet published the finalized row.
The held-position monitor correctly refused to relabel realtime monitoring data
as exact settlement truth and reported
`POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE`.

The source-correct finalized poll existed only inside the hourly multi-city
`ingest_k2_daily_obs` batch, after WU work. One unrelated WU/DB failure or the
hourly cadence could therefore delay Hong Kong finalization by up to an hour.
This packet isolates that final-source poll without changing probability,
settlement, rounding, or entry authority.

## Scope

See sibling `scope.yaml`.

Money-path location:

`source truth -> observation commit -> held-position probability refresh`

SCOPE: Hong Kong, prior completed local date, HKO Daily Extract final row only.

DRAIN: an independent five-minute source-clock job polls only while the
source-correct VERIFIED row is absent. It performs a read-only existence check,
fetches the provider without a writer lock, then attempts one non-blocking short
commit through the existing sanctioned forecasts+world connection helper.

RESET: once that row exists, the existing writer returns `already_present`
without network I/O; the next monitor cycle consumes exact final authority.

Explicit non-goals:

- no use of `hko_realtime_api` as final settlement authority;
- no relaxation of `POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE`;
- no schema, retention, DB deletion, `VACUUM`, or live-entry change;
- no new provider or city mapping.
- no network I/O while any canonical writer flock is held.

## Deliverables

- Independent `ingest_k2_hko_daily_final` scheduler job.
- Backward-compatible non-blocking admission option on the sanctioned
  forecasts+world connection helper so a poll never waits behind an unrelated
  writer; the existing default remains blocking.
- Correct registry distinction between HKO realtime monitoring and finalized
  Daily Extract sources.
- Prefetched HKO Daily Extract write seam so provider I/O occurs before the
  canonical cross-DB critical section.
- Antibodies for cadence, registry coverage, source-role identity, lock
  isolation, and local no-op after final publication.
- Source/test registry updates required by repo law.

## Verification

- Focused ingest scheduler and source registry tests.
- Existing HKO daily writer tests.
- Source-rationale and test-topology changed-surface checks.
- Cross-DB helper antibody proving non-blocking policy reaches both canonical
  flock acquisitions without changing the default transaction shape.
- Planning-lock check using this file as evidence.
- Live reload proof: loaded SHA, job heartbeat/result, canonical HKO
  observation row when the provider publishes, and monitor freshness recovery.

## Stop conditions

- Stop if current source evidence no longer names HKO Daily Extract as the
  final daily source.
- Stop if the change requires treating realtime accumulation as exact
  settlement truth.
- Stop before any DB maintenance or destructive retention action.

## Implementation evidence

- Focused behavioral suite: 14 passed.
- Python compile and `git diff --check`: passed.
- Source-rationale changed-surface check: 0 findings.
- Planning-lock with this plan and all changed paths: passed.
- Self-review caught and removed the initial network-under-cross-DB-lock shape;
  final flow is read-only check, unlocked fetch, non-blocking short commit.
- Broader legacy test files retain unrelated pre-existing failures: missing
  local `apscheduler`, removed calibration-auto-promote symbols, five stale
  `src.main` scheduler classifications, and repo-wide test-topology drift.
  These are not represented as a clean repo-wide pass.
