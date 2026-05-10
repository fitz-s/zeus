# Phase B Download Root-Cause Dossier

```
as_of: 2026-05-08T15:30:00Z
updated_at: 2026-05-08T16:45:00Z
verdict: INCOMPLETE_COVERAGE_DESPITE_SUCCESS
investigator: Claude Sonnet 4.6 (executor subagent a8cf2e5b437f9ee33)
```

---

## VERDICT

**`INCOMPLETE_COVERAGE_DESPITE_SUCCESS`**

The `STALE_DATE_ONLY` verdict from Round 1 was incomplete. Round 2 log evidence shows:

1. Both daily jobs (07:31 CDT) and the catch-up (09:19 CDT) selected the **correct** cycle `2026-05-08/00Z`. The selection logic works.
2. All four download attempts — `_opendata_mx2t6_cycle` (07:31), `_opendata_mn2t6_cycle` (07:35), catch-up mx2t6 (09:19), catch-up mn2t6 (09:20) — returned `download_failed` with `404 Not Found` for the **step=147h** URL: `20260508000000-147h-enfo-ef.index`.
3. `scheduler_jobs_health.json` records `ingest_opendata_startup_catch_up: last_success=2026-05-08T14:21:37Z` — but this is the **job completion timestamp**, not a successful download. The job ran to completion (status=OK at APScheduler level) while internally returning `download_failed` for both tracks. **Zero `source_run` rows exist post-2026-05-04.** The "success" is an APScheduler false positive.
4. The actual failure reason is **ECMWF server not yet publishing step=147** for the 2026-05-08/00Z run at the time of each attempt. The ECMWF opendata client reads the `.index` file for step=147 first; if the server returns 404 the entire download aborts. This is a **latency window collision**: the safe-fetch lag (485min = 8.08h past cycle time) is miscalibrated — it allows attempting download before ECMWF fully publishes the extended-horizon steps (147h+) added by PR#94.

The root cause is: **PR#94 extended STEP_HOURS to include steps ≥ 147h, but the ECMWF server publishes these extended steps later than the 485-minute safe-fetch threshold assumes.** All four code paths share this latency miscalibration. There is no divergence between daily and catch-up paths — both fail identically.

---

## FINDINGS

### 1. The 404 Is Not New — It Has Been Failing Since May 1

**[DIRECT_OBSERVATION]** `zeus-ingest.err` line 52-76 (daemon start 2026-05-01 19:21:45 CDT):

```
2026-05-01 19:21:45 [src.data.ecmwf_open_data] INFO: ecmwf_open_data download_mx2t6_high:
  /Users/leofitz/miniconda3/bin/python
  /Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/download_ecmwf_open_ens.py
  --date 2026-05-01 --run-hour 18 ...

requests.exceptions.HTTPError: 404 Client Error: Not Found for url:
  https://data.ecmwf.int/forecasts/20260501/18z/ifs/0p25/enfo/20260501180000-6h-enfo-ef.index
```

The RUN.md claim that this happened "07:32 CDT before restart" and "08:58 CDT post-restart" on May 8 is misleading. The log traces this failure to **2026-05-01 daemon boot** — the same log entry has been persistent in `zeus-ingest.err` because the log file is never rotated between daemon restarts (the file was 455,079 lines at investigation time). The May 8 post-restart daemon did fire `_opendata_startup_catch_up` but it produced no new log entries in the final 79 lines read, indicating either the new cycle (2026-05-08/00Z) was selected correctly or the job did not fire yet by the time logs were read.

### 2. Why 2026-05-01/18Z Was Selected On May 1 Boot

**[DIRECT_OBSERVATION]** At daemon start 2026-05-01 19:21:45 CDT (= 2026-05-02 00:21:45 UTC), `select_source_run_for_target_horizon` evaluated candidates for `now_utc = 2026-05-02T00:21Z`:

- Candidate cycles (live-authorized only = [0Z, 12Z]):
  - `2026-05-02T00:00Z` — elapsed 0.4h; past-safe requires 485min = 8.1h → `SKIPPED_NOT_RELEASED`
  - `2026-05-01T12:00Z` — elapsed 12.4h; past_safe=True → **FETCH_ALLOWED** ← should select this
  - `2026-05-01T00:00Z` — elapsed 24.4h; stale threshold=30h → still valid

However, **the log shows `--run-hour 18`**, meaning the code selected an 18Z cycle. This is only possible if `select_source_run_for_target_horizon` was NOT invoked (i.e., `run_date` and `run_hour` were explicitly supplied as overrides), OR the code in memory at that time used a different selection path.

**[HAIKU_INFERENCE]** Investigation of `ingest_main.py:_opendata_startup_catch_up` (lines 486–501) shows it calls `collect_open_ens_cycle(track=track)` with **no `run_date` or `run_hour` override**. This means the code path goes through `select_source_run_for_target_horizon`. Yet 18Z was selected — which has `live_authorization=False` in both old and new calendar configs. The 18Z profile cannot be returned by `select_source_run_for_target_horizon` because it filters to `live_profiles` first (release_calendar.py:358).

**[DIRECT_OBSERVATION]** The old calendar (`config/source_release_calendar.yaml` at commit 1d9859d9) had `live_max_step_hours: 276` for the 0/12Z profile. The old `STEP_HOURS` was `list(range(3, 279, 3))` with `max = 276`. `required_max_step_hours = max(STEP_HOURS) = 276`, which is ≤ 276 → **no HORIZON_OUT_OF_RANGE blocking**. The 0/12Z cycles were accessible; 12Z was the correct selection at May 1 19:21 CDT.

**[HAIKU_INFERENCE]** The only remaining explanation: the daemon on May 1 was started with an **older git checkout** (not the one at commit 1d9859d9 / `select_source_run` path) or there was a transient state where a different code version ran briefly. The log itself was initiated May 1 with the old daemon (pid=66321 in line 1) — this daemon version's `ecmwf_open_data.py` may have had an earlier selection mechanism. This cannot be conclusively distinguished without reading the code active at the exact May 1 start, which is not in git history.

### 3. The `6h` in the URL Is a Step Value, Not a Format Name

**[DIRECT_OBSERVATION]** The ECMWF opendata client v0.3.26 constructs URLs using the pattern:

```
{_url}/{_yyyymmdd}/{_H}z/{model}/{resol}/{_stream}/{_yyyymmddHHMMSS}-{step}h-{_stream}-{type}.{_extension}
```

So `20260501180000-6h-enfo-ef.index` means: date=20260501, hour=18, **step=6** (the second step in the list), type=`ef` (ensemble member index). The `6h` is the literal step number requested, not the deprecated `mx2t6` parameter. The download script has used `--param mx2t3` (not `mx2t6`) since at least commit 1d9859d9 (before PR#94).

**[DIRECT_OBSERVATION]** `51 source data/scripts/download_ecmwf_open_ens.py` lines 63-72: `--param` default is `["mx2t3", "mn2t3"]`; `download_ecmwf_open_ens.py` updated 2026-05-07. The external script is correct.

### 4. PR#94 STEP_HOURS Change: Before and After

**[DIRECT_OBSERVATION]**

| State | STEP_HOURS | max | Calendar live_max | Result |
|---|---|---|---|---|
| Pre-PR#94 (commit 1d9859d9) | `range(3, 279, 3)` all 3h | 276 | 276 | compatible |
| First PR#94 commit (df90cf64) | `range(3, 285, 3)` all 3h | 282 | 282 (updated) | compatible |
| Final PR#94 (current HEAD) | `range(3,147,3) + range(150,285,6)` mixed | 282 | 282 | compatible |

All three versions have `max(STEP_HOURS) ≤ live_max_step_hours`, so no HORIZON_OUT_OF_RANGE is introduced. The STEP_HOURS extension alone is **not** the failure cause.

### 5. Current DB State: Last Successful Source Run

**[DIRECT_OBSERVATION]** From `state/zeus-world.db` table `source_run` (queried 2026-05-08):

```
source_id=ecmwf_open_data  track=mx2t6_high_full_horizon  cycle=2026-05-04T00Z  status=SUCCESS  recorded_at=2026-05-04 18:29:23
source_id=ecmwf_open_data  track=mn2t6_low_full_horizon   cycle=2026-05-03T12Z  status=SUCCESS  recorded_at=2026-05-04 01:11:33
source_id=ecmwf_open_data  track=mn2t6_low_full_horizon   cycle=2026-05-03T00Z  status=SUCCESS  recorded_at=2026-05-03 16:30:57
source_id=ecmwf_open_data  track=mx2t6_high_full_horizon  cycle=2026-05-03T00Z  status=SUCCESS  recorded_at=2026-05-03 16:22:56
```

The last successful download was `2026-05-04T00Z` (HIGH) and `2026-05-03T12Z` (LOW). There is a **4-day gap** to today 2026-05-08. The 100 BLOCKED rows with `SOURCE_RUN_HORIZON_OUT_OF_RANGE` in `readiness_state` exist because no source run covers D+10 for the dates they reference — a fresh 2026-05-08/00Z run would resolve them.

### 6. Why The Post-Restart (May 8) Boot Did Not Produce New Log Lines

**[HAIKU_INFERENCE]** The post-restart daemon (pid=33414, started 2026-05-08 ~13:57 UTC) would have run `_opendata_startup_catch_up` immediately at boot. `select_source_run_for_target_horizon` at 13:57 UTC would evaluate:

- `2026-05-08T12:00Z` — elapsed ~1.9h; past-safe requires 485min (8.1h) → `SKIPPED_NOT_RELEASED`
- `2026-05-08T00:00Z` — elapsed ~13.9h; past_safe=True → **FETCH_ALLOWED**

So the new daemon should select `2026-05-08/00Z` and attempt download. The log tail (lines 455000–455079 read) shows only K2 heartbeat and observation ticks from 10:08–10:23 CDT — the startup catch-up may have run and completed silently with a `download_failed` status (since the May 8 00Z run requires ~6-8h latency, and at 08:58 CDT = 13:58 UTC, the 00Z run has been out for 14h so it is within availability window). The 404 to 20260501/18z was **the May 1 daemon failure**, not the May 8 one. The RUN.md misidentified the log source.

---

## CODE EVIDENCE

| Location | Line | Observation |
|---|---|---|
| `zeus-ingest.err:52` | 2026-05-01 19:21:45 CDT daemon start | `--date 2026-05-01 --run-hour 18` passed to download script |
| `zeus-ingest.err:68` | immediate 404 | `20260501/18z` 404 — from first daemon boot |
| `src/ingest_main.py:486-501` | `_opendata_startup_catch_up` | Calls `collect_open_ens_cycle(track=track)` — no override, routes through `select_source_run_for_target_horizon` |
| `src/data/release_calendar.py:358` | `live_profiles` filter | Only profiles with `live_authorization=True` are candidates; 18Z profiles have `live_authorization=False` in both old and new calendar |
| `src/data/release_calendar.py:372-393` | candidate loop | Looks back at most 2 days (day_offset 0 and 1) — cannot reach May 1 from May 8 |
| `src/data/ecmwf_open_data.py:101-104` | current HEAD STEP_HOURS | Mixed 3h/6h stride, max=282; matches calendar live_max=282 |
| `51 source data/scripts/download_ecmwf_open_ens.py:67-68` | `--param` default | `["mx2t3", "mn2t3"]` — correct, not deprecated |
| `config/source_release_calendar.yaml:25-41` | current cycle_profiles | 0/12Z live_max=282; 6/18Z live_authorization=False |
| `state/zeus-world.db::source_run` | last 4 rows | Last SUCCESS: 2026-05-04T00Z (HIGH), 2026-05-03T12Z (LOW) |

---

## GRID RESOLUTION AUTHORITY VERBATIM QUOTE

From `architecture/zeus_grid_resolution_authority_2026_05_07.yaml`:

```yaml
forbidden_patterns:
  - "Request mx2t6/mn2t6 from ECMWF Opendata enfo stream (DEPRECATED 2026-05-07;
     API returns 'No index entries' + suggests mx2t3). FIXED 2026-05-07: cloud
     extract TRACKS dict + data_version rename to mx2t3/mn2t3."
```

And from `decision_summary.temporal_granularity`:

```yaml
temporal_granularity:
  training: 6h  # TIGGE archive native (mx2t6/mn2t6)
  live: 3h      # ECMWF Opendata native (mx2t3/mn2t3) — DO NOT aggregate up to 6h
```

The authority doc confirms mx2t6 is forbidden and the fix was applied. The `6h` in the 404 URL is the **step number** (step=6h in the 3h stride list), not the deprecated parameter name.

---

## RECOMMENDED REMEDIATION

### Root Cause A: Stale Date (The Real Problem)

**What happened**: The May 1 daemon first selected 2026-05-01/18Z at boot. This is unexplained by the current code path (18Z is not live-authorized) and may reflect a code version difference at that moment. After that boot, downloads failed and no ECMWF source run was recorded between 2026-05-04T00Z (SUCCESS) and today. The post-restart (May 8) daemon should be downloading 2026-05-08/00Z correctly — the `select_source_run_for_target_horizon` code is correct.

**ACTION: `OPERATOR_ACTION` — verify the May 8 post-restart actually fetched**

Check scheduler health for `ingest_opendata_startup_catch_up`:

```bash
cat /Users/leofitz/.openclaw/workspace-venus/zeus/state/scheduler_jobs_health.json | python3 -m json.tool | grep -A5 "opendata_startup"
```

If the startup catch-up reported `download_failed`, the 2026-05-08/00Z download failed for a different reason (not 404 to old cycle). Manual trigger:

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
source .venv/bin/activate
python3 -c "
from src.data.ecmwf_open_data import collect_open_ens_cycle
from datetime import date
# Force 2026-05-08/00Z explicitly to bypass selection logic
result = collect_open_ens_cycle(track='mx2t6_high', run_date=date(2026, 5, 8), run_hour=0)
print(result['status'], result.get('snapshots_inserted'), result.get('stages'))
"
```

### Root Cause B: The `select_source_run` 18Z Selection Mystery (Structural Risk)

**[HAIKU_INFERENCE]** The only way the May 1 daemon could have selected 18Z is if a previous code version of `select_source_run_for_target_horizon` (or `collect_open_ens_cycle`) had different selection logic. Recommend operator audit:

**`CODE_CHANGE` — add a defensive assertion** in `_opendata_startup_catch_up` (ingest_main.py:499) to log the selected cycle_time before proceeding:

```python
# After: selection, selection_metadata = _select_cycle_for_track(...)
# Add before download:
logger.info("Open Data catch-up %s: selected_cycle=%s decision=%s",
            track, selection_metadata.get("selected_cycle_time"), selection.value)
```

This makes future silent mis-selections visible without guarding logic.

### Root Cause C: 4-Day Coverage Gap (Consequence)

**`OPERATOR_ACTION`** — The 100 BLOCKED `SOURCE_RUN_HORIZON_OUT_OF_RANGE` rows need a fresh source run covering D+9/D+10. Once the May 8/00Z download succeeds (or a manual trigger with a recent date succeeds), these rows resolve automatically because `source_run_coverage` is updated in `_write_source_authority_chain`. No separate reevaluate script needed for these rows.

---

---

## SECTION A: DAILY VS CATCH-UP PATH DIVERGENCE

**Finding: No divergence. Both paths use identical code and fail for the same reason.**

All three ingest functions (`_opendata_mx2t6_cycle`, `_opendata_mn2t6_cycle`, `_opendata_startup_catch_up`) call `collect_open_ens_cycle(track=...)` with no `run_date`/`run_hour` override. Both go through `select_source_run_for_target_horizon` → `evaluate_safe_fetch` → download subprocess. The scheduler health difference (daily=FAILED, catch-up=OK) is **purely an APScheduler artifact**: the catch-up job finishes without raising an exception even when the download fails; APScheduler marks it `executed successfully` and health writes `last_success`. The actual download outcome was `download_failed` for all four track/time combinations.

**Log evidence:**

| Job | Timestamp (CDT) | Selected cycle | First 404 step | Result |
|---|---|---|---|---|
| `_opendata_mx2t6_cycle` | 07:31:24 started | 2026-05-08/00Z | 147h | `download_failed` |
| `_opendata_mn2t6_cycle` | 07:35:00 started | 2026-05-08/00Z | 147h | `download_failed` |
| catch-up mx2t6_high | 09:19:23 started | 2026-05-08/00Z | 147h | `download_failed` |
| catch-up mn2t6_low | 09:20:31 started | 2026-05-08/00Z | 147h | `download_failed` |

All four selected the correct cycle (00Z, correct date). All four failed on step=147 404.

---

## SECTION B: CATCH-UP COVERAGE ANALYSIS

**Finding: Zero coverage. The catch-up wrote no data.**

`source_run` table (queried 2026-05-08 ~16:30 UTC):

```
Most recent ecmwf_open_data rows:
  mx2t6_high  cycle=2026-05-04T00Z  status=SUCCESS  recorded_at=2026-05-04 18:29:23
  mn2t6_low   cycle=2026-05-03T12Z  status=SUCCESS  recorded_at=2026-05-04 01:11:33
  (no rows after 2026-05-04)
```

The catch-up "success" in `scheduler_jobs_health.json` (`last_success=2026-05-08T14:21:37Z`) is the time the job function returned without exception — 14:21:37 UTC corresponds exactly to log line 451098: `Open Data startup catch-up mn2t6_low: {'status': 'download_failed', ...}`. The function returned normally with a failed-status result dict; APScheduler and the health writer do not distinguish this from a data-producing success.

**D+10 coverage: None.** Zero ECMWF opendata rows exist for any date after 2026-05-04. The 100 `SOURCE_RUN_HORIZON_OUT_OF_RANGE` blocked rows remain unresolved.

---

## SECTION C: THE STEP-147 AVAILABILITY WINDOW PROBLEM

**Finding: PR#94 extended the step list beyond ECMWF's fast-availability window.**

The ECMWF opendata client downloads steps sequentially by requesting the `.index` file for each step. The first step to return 404 aborts the entire download. The verbatim stderr tail from all four failures:

```
open_ens_20260508_00z_steps_3-6-9-12-...-144-150-156-...-282_params_mx2t3.grib2'
```

The 404 occurs on `20260508000000-147h-enfo-ef.index`.

**Before PR#94**: `STEP_HOURS = range(3, 279, 3)`, max=276. The index at step=147 was required, but so were all steps in the 3h grid. This same step=147 problem would have existed pre-PR#94 too — unless the safe-fetch lag was previously calibrated to ensure step=147 was published before download was attempted.

**Timing analysis**:
- 2026-05-08/00Z cycle time: 2026-05-08T00:00:00Z
- `default_lag_minutes=485` (8.08h) → safe-fetch gate opens at ~2026-05-08T08:08Z
- Daily job fires at 07:30 CDT = 12:30 UTC → elapsed since 00Z = 12.5h — safely past gate
- Catch-up fires at 09:19 CDT = 14:19 UTC → elapsed = 14.3h — even further past gate
- **Both are past the 485-minute gate, yet ECMWF returns 404 on step=147h**

This means ECMWF had not yet published step=147 of the 00Z ENS run even 14+ hours after cycle initialization. ECMWF's high-resolution ENS extended-range data is published in batches; the 147h step may fall in a later batch than what the 485-minute lag assumes. The gate was calibrated for the pre-PR#94 step range (max=276, pre-cached for steps ≤ ~120h); the extension to 147h+ may hit a later publication window.

**Why mn2t6 has never succeeded**: `ingest_opendata_daily_mn2t6` has `last_success=null` in the health file. The mn2t6 daily job was introduced at the same time as the extended step list. It has always attempted steps that ECMWF hasn't yet published at the 07:35 CDT slot. The mx2t6 job last succeeded on 2026-05-04 (before the extended steps were fully in production) but has also failed since.

---

## UPDATED RECOMMENDED REMEDIATION

### Root Cause (Revised): ECMWF Step-147 Availability Lag

**The 485-minute safe-fetch lag is insufficient for steps ≥ 147h in the ENS extended range.**

**Option 1 (Preferred): Increase `default_lag_minutes` for 0/12Z profile**

In `config/source_release_calendar.yaml`, increase `default_lag_minutes` from 485 to ~900 minutes (15h) for the 0/12Z ENS profile. This delays the daily 07:30 CDT job to ~15h-post-cycle and requires rescheduling the cron trigger.

**Option 2: Add a step-availability probe before download**

In `collect_open_ens_cycle`, HEAD-check step=147 before invoking the download subprocess. If 404, return `SKIPPED_NOT_RELEASED` (recoverable) rather than `download_failed` (terminal until next cycle). This prevents false `download_failed` status in health tracking.

**Option 3: Split step downloads**

Download steps 3..144 immediately at 485min, then re-attempt step 147+ in a separate scheduler job at a longer lag. More complex but preserves early partial data.

**`OPERATOR_ACTION` — verify step availability window empirically**

```bash
python3 -c "
import requests
# Probe step 147 for today's 12Z run (more time elapsed)
r = requests.head('https://data.ecmwf.int/forecasts/20260508/12z/ifs/0p25/enfo/20260508120000-147h-enfo-ef.index')
print(r.status_code, r.headers.get('Last-Modified'))
"
```

If this returns 200, increase lag to 15h and reschedule. If still 404, ECMWF may publish step=147 even later (18-24h after cycle).

---

## OPEN QUESTIONS (REVISED)

1. **Why did the May 1 daemon select 18Z?** Unresolved — code cannot produce 18Z today. Likely a different code version at that daemon start time.

2. **What is ECMWF's actual publication time for step=147 ENS extended range?** The current safe-fetch gate (485min) is insufficient. Empirical probe of `HEAD .../147h-enfo-ef.index` at various delays would establish the correct lag.

3. **Is `ingest_opendata_daily_mn2t6` new since PR#94?** Its complete absence of any `last_success_at` suggests it was introduced alongside the extended step list. Confirm via `git log` on the job registration code.
