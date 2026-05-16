# F1 Subprocess Hardening — Run Log

**Task:** `task_2026-05-08_f1_subprocess_hardening`
**Date:** 2026-05-08
**Branch:** `fix/f1-ecmwf-subprocess-hardening-2026-05-08`
**Authority:** `docs/operations/task_2026-05-08_ecmwf_publication_strategy/REPORT.md` §6.2

## Summary

Applied the three F1 changes to `src/data/ecmwf_open_data.py` as recommended in REPORT.md:

(a) **Timeout extended**: `download_timeout_seconds` default 600 → 1500. Empirical full-fetch measured at 609.6s; 600s ceiling was the primary post-PR#94 failure cause.

(b) **Bounded retry with backoff**: 3-attempt loop around download subprocess (delays: 0, 60, 180s). Distinguishes 404 on grid-valid step (stderr contains "404"/"Not Found" but not "No index entries") → `SKIPPED_NOT_RELEASED` with no retry, from all other rc≠0 → retryable until exhaustion → `download_failed`.

(c) **Full stderr capture**: `_run_subprocess` truncation 400 → 4096 chars. On download failure, stderr written to `tmp/ecmwf_open_data_{date}_{hour}z_{track}.stderr.txt` via `_write_stderr_dump()`.

F2 (AWS mirror fallback) assessed: adds ~30 lines of source routing logic and a second subprocess invocation path. Deferred per brief constraint (>20 lines).

## Files changed

- `src/data/ecmwf_open_data.py` — timeout, retry loop, stderr capture
- `tests/test_ecmwf_open_data_subprocess_hardening.py` — 5 new unit tests (new file)
- `tests/test_opendata_writes_v2_table.py` — updated timeout assertion 600→1500
- `architecture/test_topology.yaml` — registered new test file

## Tests

New: `tests/test_ecmwf_open_data_subprocess_hardening.py`
- `test_timeout_default_1500s` — regression guard on default value
- `test_subprocess_retry_succeeds_on_second_attempt` — rc=1 then rc=0 → ok
- `test_subprocess_retry_exhausts` — rc=1×3 → download_failed
- `test_skipped_not_released_on_grid_valid_404` — 404 without "No index entries" → SKIPPED_NOT_RELEASED, no retry
- `test_no_index_entries_404_is_retried_not_skipped` — "No index entries" 404 → retried × 3, download_failed

## Schema impact

ZERO. No migrations, no table changes, no release_calendar changes.

## Lag impact

ZERO. No changes to `source_release_calendar.yaml` or `release_calendar.py`.
