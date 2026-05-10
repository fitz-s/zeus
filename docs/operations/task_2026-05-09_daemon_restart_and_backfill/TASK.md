# TASK — Restart daemon, verify fresh fetch, run 317 London backfill

Created: 2026-05-09
Authority: operator directive 2026-05-09 — same launchctl/DB-write authorization as the 2026-05-08 `task_2026-05-08_post_merge_full_chain` dispatch

## GOAL

Today's main has all hardening: F1 subprocess hardening (PR #101) + integer-snap F→C + SOURCE_DISAGREEMENT (PR #100) + STEP_HOURS 282 + Track A.6 daemon retrofit. Now:
- Restart daemon to load all the above
- Verify ECMWF catch-up succeeds for today's available cycle (2026-05-09/00Z, cycle+8h+)
- Verify 100 BLOCKED rows resolve naturally
- Run 317 London backfill `--apply` (PR #100 shipped integer-snap logic + script)
- Verify London QUARANTINED count drops 317 → close to 0

## PHASE A — Pull main + restart daemon

1. `git checkout main && git pull origin main` (should be at commit `cd43945e` or later — PR #102 just merged)
2. Verify expected commits: `git log --oneline -10` should show #102, #100, #101 merges.
3. Restart data-ingest daemon:
   - `launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist`
   - `launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist`
4. Verify daemon alive:
   - `launchctl list | grep zeus`
   - `state/daemon-heartbeat-ingest.json` mtime in last 5 min
   - `tail -30 logs/zeus-ingest.err` — no Python tracebacks at boot

## PHASE B — Verify fresh ECMWF fetch

1. Wait for the daemon's startup catch-up to fire (boot path, runs ~30-60s after start).
2. Inspect `state/scheduler_jobs_health.json::ingest_opendata_startup_catch_up`:
   - last_success_at should advance to a 2026-05-09 timestamp
   - status should be OK (with NEW commits this is a real success, not the 2026-05-08 false-positive)
3. Inspect `state/zeus-world.db` `source_run` table:
   - `SELECT issued_at, source_id, run_hour, status, COUNT(*) FROM source_run WHERE issued_at >= '2026-05-09' GROUP BY 1,2,3,4`
   - Expect at least 1 SUCCESS row for `ecmwf_open_data` for 2026-05-09/00Z
4. If no fresh source_run after 10 min wait: tail logs/zeus-ingest.err for actual exception (F1 captures full stderr now).

## PHASE C — Verify 100 BLOCKED resolution

1. `python3 scripts/reevaluate_readiness_2026_05_07.py --apply` (re-evaluates readiness against new source_runs).
2. SQL: `SELECT status, reason_codes_json, COUNT(*) FROM readiness_state GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10`
3. Expect: BLOCKED `SOURCE_RUN_HORIZON_OUT_OF_RANGE` count drops from 100 → close to 0 (some edge cases at 06/18 short-horizon may remain — document).

## PHASE D — Run 317 London backfill --apply

1. Verify the script exists and matches latest main:
   - `ls scripts/backfill_london_f_to_c_2026_05_08.py`
   - Should have integer-snap logic (math.floor(x+0.5)) per PR #100
2. Dry-run first (sanity check vs prior numbers):
   - `python3 scripts/backfill_london_f_to_c_2026_05_08.py --dry-run`
   - Note: with integer-snap, expected resolve count rises from prior 195 toward ~270-300
3. Apply:
   - `python3 scripts/backfill_london_f_to_c_2026_05_08.py --apply`
4. Verify:
   - SQL: `SELECT COUNT(*) FROM settlements_v2 WHERE city='London' AND quarantine_reason='harvester_live_obs_outside_bin'`
   - Expect: drops from 317 → close to 0 (residual = genuine outside-bin even with integer-snap)

## PHASE E — Reporting

Write `docs/operations/task_2026-05-09_daemon_restart_and_backfill/RUN.md`:
- Pre-state: BLOCKED count, London quarantined count, source_run latest timestamp
- Phase A actions + verification
- Phase B actions + new source_run rows
- Phase C BLOCKED count after reevaluate
- Phase D backfill dry-run + apply numbers
- Post-state: BLOCKED count, London quarantined count
- Operator handoff (if any residual issues)

## VERDICT_TOKENS
- `ALL_PHASES_COMPLETE` — daemon up, fresh source_run, BLOCKED dropped, London backfill applied
- `PHASE_<X>_BLOCKED_<reason>` — partial completion
- `PARTIAL_<phases_done>_<reason>`

## CONSTRAINTS
- Operator authorized launchctl + plist + production DB writes for THIS dispatch
- Single-writer doctrine on backfill — daemon should not be mid-write while backfill runs (the backfill script uses db_writer_lock helper per its registration in conftest)
- If anything fails non-trivially, STOP and report — operator decides recovery
- No --no-verify, no force, no schema changes
- Backfill is idempotent — re-running on already-resolved rows skips via `backfilled_via` tag

## FINAL_REPLY_FORMAT
Single line: `<VERDICT> docs/operations/task_2026-05-09_daemon_restart_and_backfill/RUN.md`
