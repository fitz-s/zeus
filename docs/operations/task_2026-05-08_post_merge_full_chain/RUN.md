# RUN — Post-merge full chain (4 phases)

Created: 2026-05-08
Executor: Claude Sonnet 4.6 (aae54530390d1e886)
Run started: 2026-05-08T13:55:00Z (approx)
Report written: 2026-05-08T14:10:00Z

---

## Phase A — Daemon restart + plist load

**Status: COMPLETE**

### Steps taken

1. `git checkout main && git pull origin main`
   - Fast-forwarded 13 commits (4 merged PRs: #94/#95/#96/#98)
   - Git log confirms `c76327d0 Merge pull request #98` at HEAD

2. Data-ingest daemon restart:
   - Old daemon: pid=33671, `runs=1`, state=running (was running BEFORE git pull — old code)
   - `launchctl unload /Users/leofitz/Library/LaunchAgents/com.zeus.data-ingest.plist` → success (exit 0)
   - `launchctl load /Users/leofitz/Library/LaunchAgents/com.zeus.data-ingest.plist` → success
   - New daemon: pid=33414, state=running, `last exit code = (never exited)` ✓
   - Daemon heartbeat confirmed fresh: `state/daemon-heartbeat-ingest.json` mtime 2026-05-08 08:57:51 CDT
   - Ingest log at 08:58 CDT shows new scheduler started, 22 jobs registered, no Python tracebacks ✓

3. Calibration-transfer-eval plist:
   - `launchctl print gui/501/com.zeus.calibration-transfer-eval` shows: state=not running, path confirmed, `runs=0, watching=1` ✓
   - This is correct — it is scheduled for Sundays 04:00 AM (calendar-interval trigger). Currently loaded and armed.

4. ECMWF D+10 download:
   - STEP_HOURS verified at 282h in `src/data/ecmwf_open_data.py` (PR #94 fix) ✓
   - Daemon boot-time catch-up (`_opendata_startup_catch_up`) registered and fired at 08:58 CDT
   - **Download result: FAILED** — see Phase B section for details

### Acceptance criteria
- [x] Daemon restarted with new code (new pid, new STEP_HOURS=282h code loaded)
- [x] Calibration-transfer-eval plist loaded and armed
- [ ] ECMWF download triggered and successful — **BLOCKED** (see Phase B)

---

## Phase B — ECMWF download + BLOCKED row resolution

**Status: BLOCKED — download failing with 404**

### Download failure analysis

The ECMWF startup catch-up attempted download at 07:32 CDT (before restart) and again at 08:58 CDT (post-restart boot catch-up). Both failed with HTTP 404.

Error pattern from logs:
```
requests.exceptions.HTTPError: 404 Client Error: Not Found for url:
https://data.ecmwf.int/forecasts/20260501/18z/ifs/0p25/enfo/20260501180000-6h-enfo-ef.index
```

Two issues identified:

1. **Stale date selection**: The release calendar is selecting 2026-05-01/18Z (5 days stale) rather than today's 2026-05-08 run. The `select_source_run_for_target_horizon` logic may not be advancing past the last successful run date.

2. **Deprecated file format**: URL contains `6h-enfo-ef.index` — this is the old mx2t6 6h step format. PR #94 updated STEP_HOURS but the download script (`51 source data/scripts/download_ecmwf_open_ens.py`) is external to the zeus repo and may still be requesting deprecated 6h step files.

### Current BLOCKED row count

```sql
SELECT status, reason_codes_json, COUNT(*) FROM readiness_state GROUP BY 1,2 ORDER BY 3 DESC
```
Result:
- `LIVE_ELIGIBLE ["PRODUCER_COVERAGE_READY"]`: 477
- `BLOCKED ["SOURCE_RUN_HORIZON_OUT_OF_RANGE"]`: **100** (unchanged)

### Next ECMWF scheduled attempt

The daemon cron tick fires at 07:30 UTC daily (= 02:30 CDT). The 13:30 UTC run is available now but the startup catch-up already failed against it. The next auto-retry is 07:30 UTC 2026-05-09.

### Operator action required

Per TASK.md: "If any phase fails non-trivially, document state in RUN.md and STOP — operator decides recovery."

This is a non-trivial failure. Two potential root causes:
- (A) Release calendar date selection stuck on old cycle — operator should inspect `src/data/release_calendar.py` + `select_source_run_for_target_horizon` return value
- (B) Download script (`51 source data/scripts/download_ecmwf_open_ens.py`) still requesting deprecated 6h format — operator should update the external script to use 3h steps (mx2t3/mn2t3 + 3h stride) matching what PR #94 configured in STEP_HOURS

Manual trigger command (once root cause fixed):
```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
source .venv/bin/activate
python3 -c "
from src.data.ecmwf_open_data import collect_open_ens_cycle
print(collect_open_ens_cycle(track='mx2t6_high'))
print(collect_open_ens_cycle(track='mn2t6_low'))
"
```

After download completes, run reevaluate script:
```bash
python3 scripts/reevaluate_readiness_2026_05_07.py --apply
```
Note: this script only handles D1-resolvable rows (CALIBRATION_TRANSFER_DATA_VERSION_UNMAPPED). The 100 BLOCKED rows are `SOURCE_RUN_HORIZON_OUT_OF_RANGE` — they resolve automatically when the new source run is ingested and `source_run_coverage` is updated.

---

## Phase C — SOURCE_DISAGREEMENT isolation layer (fix #263)

**Status: COMPLETE — PR #100 open**

### PR
https://github.com/fitz-s/zeus/pull/100

### Branch
`fix/263-source-disagreement-isolation-2026-05-08`

### Changes

**`src/ingest/harvester_truth_writer.py`**
- Added `_disagreement_tolerance()` — reads `config/settings.json::settlement.disagreement_tolerance_celsius`, falls back to 1.0°C
- Added `_nearest_bin_edge_distance()` — returns abs distance from obs to nearest bin boundary
- Modified `_write_settlement_truth` else branch: when obs fails containment, check if it's within tolerance of nearest bin edge → emit `harvester_source_disagreement_within_tolerance` instead of `harvester_live_obs_outside_bin`

**`config/settings.json`**
- Added `"settlement": {"disagreement_tolerance_celsius": 1.0}` section (before "riskguard")

**`tests/test_harvester_truth_writer_source_disagreement.py`** (new)
- 7 antibody tests: T1 agree+contained→VERIFIED; T2 within-tolerance→SOURCE_DISAGREEMENT; T3 far-outside→obs_outside_bin; T4 null-bin precedence; T5 open-shoulder within-tolerance; T6 boundary-inclusive; T7 beyond-tolerance
- All 7 pass ✓

**`architecture/test_topology.yaml`**
- Registered new test in `trusted_tests` and `core_law_antibody` category
- Added full test profile with asserted invariants

**`tests/conftest.py`**
- Added `scripts/backfill_london_f_to_c_2026_05_08.py` to `_WLA_SQLITE_CONNECT_ALLOWLIST` (pending_track_a6_scripts)

**`scripts/backfill_london_f_to_c_2026_05_08.py`** (Phase D — new)
- See Phase D section below

### Test results
```
29 passed in 0.23s  (Phase C antibodies + regression guards)
45 passed in 1.42s  (existing harvester suite)
```

### Open question: retroactive reclassification
Per TASK.md default: **NO** — existing `obs_outside_bin` rows that fit the SOURCE_DISAGREEMENT pattern are NOT retroactively reclassified. The new `harvester_source_disagreement_within_tolerance` reason applies only to new writes going forward. Separate operator decision required for retroactive reclassification.

---

## Phase D — London °F→°C backfill

**Status: SCRIPT READY — apply pending Phase C merge**

Per TASK.md: "Phase D backfill ONLY after Phase C ships (so the helper code is on main / accessible)."

### Script
`scripts/backfill_london_f_to_c_2026_05_08.py`

### Dry-run results (2026-05-08 09:04 CDT)
```
Found 317 candidate rows (city=London, quarantine_reason=harvester_live_obs_outside_bin)
Dry-run results: candidates=317  would_resolve=195  still_outside=122  skipped=0
```

### Key findings from dry-run
- 195 of 317 rows resolve to VERIFIED after F→C bin conversion ✓
- 122 remain outside bin after conversion — these are genuine obs/bin mismatches (e.g. London 2025-01-25: obs=9°C, F_bin=[47,48]→C=[8.33,8.89], obs=9 > 8.89, genuine mismatch)
- Pre-#262 rows have `pm_bin_unit=None` — script uses heuristic: if bin lo ≥ 28 for London (a °C city), infer pm_bin_unit='F'
- Spot-check of 10 rows confirms logic is correct (e.g. 2025-01-22: obs=5°C, F_bin=[40,41]→C=[4.44,5.0], contained=True ✓)

### Apply instructions
After PR #100 merges to main:
```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
source .venv/bin/activate
python3 scripts/backfill_london_f_to_c_2026_05_08.py --apply
```

Expected outcome:
- London QUARANTINED drops from 317 → ~122 (195 resolved to VERIFIED)
- Re-running is idempotent (already-backfilled rows skipped via `backfilled_via` tag)

---

## Summary

| Phase | Status | Notes |
|-------|--------|-------|
| A | COMPLETE | Daemon restarted (new pid=33414), cal-eval plist armed |
| B | BLOCKED | ECMWF download failing 404 — operator action required (see above) |
| C | COMPLETE | PR #100 open, 7 antibody tests pass, SOURCE_DISAGREEMENT isolation live |
| D | READY | Script dry-run verified (195/317 would resolve); apply after C merges |

### BLOCKED rows
100 BLOCKED `SOURCE_RUN_HORIZON_OUT_OF_RANGE` rows will resolve automatically once the ECMWF download issue is fixed and a fresh run covering 2026-05-13/14 (steps 228-252h) is ingested.

### No operator authorization consumed for DB writes
Phase D `--apply` not yet run (pending Phase C merge per TASK.md constraint). No production DB writes have been made by this dispatch beyond the daemon's normal live operation.
