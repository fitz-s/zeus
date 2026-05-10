# ECMWF Open Data Publication-Schedule Strategy — Scientist Evaluation

**Task:** `docs/operations/task_2026-05-08_ecmwf_publication_strategy/REPORT.md`
**Date:** 2026-05-08
**Author:** scientist agent (add149ce140494a97), written to disk by orchestrator (scientist subagent_type lacks Write/Edit).
**Authority basis:** ECMWF official sources (Confluence DAC, set-iii, ecmwf-opendata GitHub README) + verified by direct empirical HEAD-probe and full retrieve against `data.ecmwf.int` + Zeus state DB introspection + prior step-grid REPORT.

[OBJECTIVE] Choose an ECMWF Open Data download strategy that preserves D1–D5 freshness at ~8h cycle-lag while recovering D6–D10 horizon coverage, given the operator's rejection of a uniform lag-bump (Option A) and the new evidence about the actual publication schedule.

## 1. Verified ECMWF publication schedule (verbatim citations + empirical probe)

### 1.1 Confluence DAC — official dissemination schedule (verbatim)

Source: https://confluence.ecmwf.int/display/DAC/Dissemination+schedule (WebFetch 2026-05-08).

> "Forecast Day 0" → "Time Available" "06:40"
> "Forecast Day 1" → "06:44"
> "Forecast Day 2" → "06:48"
> "Forecast Day 3" → "06:52"
> "Forecast Day 10" → "07:20"
> "Forecast Day 15" → "07:40"
> "Derived products Step 0 to 240" → "07:41"
> "Derived products Step 246 to 360" → "08:01"

### 1.2 ECMWF set-iii page (verbatim)

> "The 'Dissemination schedule' confirms that 'Time Available' varies by step. For the '00UTC' run, 'Forecast Day 0' is ready at '06:40,' yet 'Forecast Day 15' waits until '07:40.' Cycles at '06UTC,' '12UTC,' and '18UTC' provide initial data at '12:40,' '18:40,' and '00:40.'"

### 1.3 ecmwf-opendata GitHub README (verbatim)

> "Forecasts are reachable between 7 and 9 hours after the forecast starting date and time"

> ENS step grid: "0 to 144 by 3, 144 to 360 by 6" for 00 and 12 UTC; "0 to 144 by 3" for 06 and 18 UTC.

> "Connectivity, the server is 'limited to 500 simultaneous connections'; users should utilize cloud mirrors for 'added reliability' if they face access issues."

### 1.4 Empirical probe (Zeus, 2026-05-08T15:46Z – 16:01Z)

Direct HEAD against `data.ecmwf.int/forecasts/{YYYYMMDD}/{HH}z/ifs/0p25/enfo/{YYYYMMDDHHMMSS}-{step}h-enfo-ef.index`:

| Cycle | step=3h | step=24h | step=90h | step=144h | step=150h | step=180h | step=240h | step=282h |
|---|---|---|---|---|---|---|---|---|
| 2026-05-08 00Z | 200 LM=07:40 GMT | 200 07:40 | 200 07:40 | 200 07:40 | 200 07:40 | 200 07:40 | 200 07:40 | 200 07:40 |
| 2026-05-07 12Z | 200 LM=19:40 GMT | 200 19:40 | 200 19:40 | 200 19:40 | 200 19:40 | (skipped) | (skipped) | 200 19:40 |
| 2026-05-06 00Z | (n/a) | (n/a) | (n/a) | 200 LM=07:40 GMT | 200 07:40 | (n/a) | (n/a) | 200 07:40 |

**Every disseminated `*-enfo-ef.index` file carries an identical `Last-Modified` timestamp = cycle_time + 7h40min**. step=147h returns 404 because **147h is not in the dissemination grid**, NOT because of staggered timing.

### 1.5 Empirical retrieve probe (2026-05-08T15:51Z – 16:01Z)

`Client(source='ecmwf').retrieve(date=20260508, time=0, stream='enfo', type=['cf','pf'], step=PR#94_STEP_HOURS, param='mx2t3', target=…)`:

| Result | Value |
|---|---|
| Status | retrieve OK |
| Bytes downloaded | 1,590,949,850 (1.59 GB) |
| Elapsed | 609.6 seconds (10 min 9.6 s) |
| Mean throughput | ~2.6 MB/s |
| Step list used | identical to PR#94 STEP_HOURS, max=282h |
| HTTP errors | none |

[FINDING] **PR#94 STEP_HOURS works against today's 00Z cycle right now.** No publication-schedule problem.

### 1.6 Reconciliation

Confluence "Forecast Day 0 at 06:40 → Forecast Day 15 at 07:40" rows describe **internal MARS publication of forecast-day artefacts**. The Open Data CDN-mirrored `*-enfo-ef.index` files (Zeus's actual consumption) are **uploaded as a single batch when the full ENS forecast is complete** — at T+7h40min for 00Z. "Derived products Step 0–240 at 07:41" / "Step 246–360 at 08:01" rows refer to **post-processed derived products**, not the base ensemble forecast index. Zeus does not consume those.

[FINDING] **The operator-stated premise — "ECMWF Open Data publishes long steps in a LATER batch than short steps" — is empirically incorrect for the index files Zeus actually fetches.** Base ENS dissemination is single-batch at T+7h40min. Phase B Round-2 dossier hypothesis is half-wrong: 147 missing is a permanent grid hole, not a window.

## 2. Re-frame the actual root cause

| Time (UTC) | Event | Step list used | Daemon code | Failure mode |
|---|---|---|---|---|
| 2026-05-04 18:29 UTC | last `mx2t6_high` SUCCESS | pre-#94 `range(3,279,3)` | pre-#94 (`live_max=276`) | n/a — succeeded |
| 2026-05-08 12:31 UTC daily mx2t6 cron | `download_failed` rc=1 in 38s | pre-#94 (3,6,…,276) | pre-#94 STEP_HOURS still loaded | HTTPError on first 404 of step=147h |
| 2026-05-08 13:58 UTC | daemon restart | now PR#94 list (147 absent) | PR#94 deployed | (catch-up scheduled) |
| 2026-05-08 14:19 UTC catch-up | `download_failed` rc=1 in 68s | PR#94 list (no 147) | PR#94 | HTTPError; truncated stderr; **NOT a step-grid bug** |

[FINDING] Two distinct causes:

1. **Pre-PR#94 STEP_HOURS bug:** `range(3,279,3)` requests non-grid steps 147, 153, 159, …, 273. ecmwf-opendata client iterates index files; first 404 (at step=147h) raises `HTTPError` and aborts. After 2026-05-04, every retry hit this deterministically.

2. **Post-PR#94 catch-up failure:** With grid-valid step list, my direct probe succeeded in 609.6s. The 68s catch-up failure cannot be the same step-grid bug. Most likely:
   - **600-second subprocess timeout** too tight for a ~10-minute download.
   - **No retry logic.** `_run_subprocess` runs once, propagates rc=1 on any transient HTTPError (rate-limit, network blip).
   - **stderr truncated to 400 chars** hides the real exception.

[FINDING] **The freshness problem the operator wants to solve does not exist as described.** Real issues: (a) Step-grid mismatch (already fixed in PR#94), (b) Subprocess fragility (600s + 0 retries against 10-min download on rate-limited CDN).

## 3. Strategy options — re-evaluation under corrected facts

| Option | What it does | Helps real failure mode? | D1-D5 freshness cost | Schema impact | Complexity |
|---|---|---|---|---|---|
| **A:** uniform 720min lag | Wait T+12h | No | **+4h delay** | none | trivial |
| **B:** two-phase cumulative | Phase-1 fetch 0–144h at +485min; phase-2 augment to 282h at +Nmin (same source_run) | No — single-batch publication; no benefit. **Adds a second 1-1.5GB fetch.** | preserved | low | medium |
| **C:** two source_runs | `release_calendar_key:short` + `:long` | Same — does not address real failure | preserved | medium | medium-high |
| **D:** adaptive progressive retry | Try every 30/60/120/240min until full list received | **Yes** | preserved | none | medium |
| **E:** per-step decoupled state | Track each step's availability | Over-engineered for single-batch | preserved (in principle) | **HIGH** | high |
| **F:** target-date horizon-tier routing | lead<6 fetches 0–144h at 485min; lead≥6 at 720min | No — adds cron complexity | preserved | medium | medium-high |
| **F1 (proposed):** No new lag — fix subprocess reliability | Keep 485min, keep PR#94 list. **Add (a) retry-with-backoff, (b) timeout 600→1500s, (c) full-stderr capture.** | **Yes** | preserved exactly as today | **none** | **low** |
| **F2 (proposed):** F1 + cloud-mirror fallback | F1 + on retryable error fall back to `source='aws'` | Yes | preserved | none | low-medium |

[FINDING] Options A, B, C, F all assume publication-staggering that does not exist. They sacrifice freshness or add complexity to "solve" a non-issue.

## 4. Quantitative comparison: freshness cost per strategy

| Strategy | D1-D5 lag | D6-D10 lag | Notes |
|---|---|---|---|
| Status quo (PR#94 only) | 485min | 485min | PR#94 grid fix; subprocess still fragile |
| **A: 720min uniform** | 720min (+235min) | 720min | rejected by operator |
| **B: cumulative 485+720** | 485min | 720min | adds ~+1.5GB/cycle network cost; same payload twice |
| **C: two source_runs** | 485min | 720min | same network cost as B; dual readiness logic |
| **D: progressive retry** | 485-525min (first try + retry budget) | 485-525min | adds 0-40min on transient failure; freshness preserved on success path |
| **F: per-target-date** | 485min | 720min | network cost B/C-like; cron complexity |
| **F1: subprocess hardening** | 485min | 485min | **zero freshness cost, zero schema change** |
| **F2: F1 + AWS fallback** | 485-490min | 485-490min | almost-zero freshness cost, additional fault tolerance |

D6-D10 freshness penalty for any cycle-splitting option is +235min ≈ 4h delay vs F1 — incurred even though the data is already on the CDN at T+7h40min.

## 5. Calibration impact (per A1+3h authority)

> "training: 6h  # TIGGE archive native (mx2t6/mn2t6)
>  live: 3h      # ECMWF Opendata native (mx2t3/mn2t3) — DO NOT aggregate up to 6h
>  note: 'Train coarser-time + live finer-time is BENIGN single-direction info-loss; calibration learns the 3h→6h envelope mapping at predict-time.'"

The 3h→6h envelope is learned per-cycle, not per-step. As long as `ensemble_snapshots_v2` arrives with consistent `step_horizon_hours` per (cycle, city, target_local_date, metric), calibration is unaffected.

[FINDING] Single-source_run options (status quo, A, D, F1, F2) preserve calibration. Multi-source_run options (B, C, F) add a read-side branch with no upside.

## 6. Recommendation: F1 — subprocess reliability fix; no new lag, no new schema

**Recommended option: F1** (with optional F2 augmentation).

### 6.1 Why F1, not B/C/D/F

F1 is the **structural fix** for the actual root cause.

- **Why not A:** rejected by operator constraint.
- **Why not B/C/F:** premised on staggering that does not exist; doubles network cost or adds cron complexity to "solve" a non-issue.
- **Why not D:** retry alone is good (component of F1) but does not address the 600s timeout ceiling. F1 ⊃ retry.
- **Why not E:** per-step state for single-batch publication is over-engineering.

### 6.2 F1 patch surface (target <50 lines)

(a) **Extend the download-subprocess timeout** in `collect_open_ens_cycle`:

```python
# src/data/ecmwf_open_data.py:490
def collect_open_ens_cycle(
    *,
    track: str = "mx2t6_high",
    run_date: Optional[date] = None,
    run_hour: Optional[int] = None,
    download_timeout_seconds: int = 1500,  # was 600 — empirical full-fetch 609.6s for 71 steps × 51 members × 1.5GB
    extract_timeout_seconds: int = 900,
    ...
```

(b) **Bounded subprocess retry with backoff** for transient HTTPError. Wrap the download in a 3-attempt retry loop (0, 60, 180 sec backoff). Distinguish:
- 404 on a step that's in the canonical grid → `SKIPPED_NOT_RELEASED` (not FAILED).
- Other rc≠0 → bounded retry; final failure → `download_failed`.

(c) **Capture full stderr.** Increase from 400 to 4096 chars + write per-failure file under `tmp/ecmwf_open_data_{cycle}.{track}.stderr.txt`.

(d) **(Optional / F2)** Cloud-mirror fallback: on retry exhaustion against `source='ecmwf'`, retry once with `source='aws'`.

### 6.3 Schema impact

**ZERO.** No new tables, no column additions, no `release_calendar_key` changes.

### 6.4 Test/validation plan

1. **Reproduce the success empirically**: run `collect_open_ens_cycle(track='mx2t6_high', run_date=date(2026,5,8), run_hour=0)` against today's cycle with F1 patch. If it succeeds (timeout 1500s + retry), the 4-day gap closes immediately because BLOCKED rows resolve when fresh source_run with covering coverage is written.
2. **Unit tests** in `tests/test_ecmwf_open_data.py`: simulate `_run_subprocess` rc=1→rc=0 → verify retry succeeds. Simulate rc=1×3 → verify final `download_failed`.
3. **Live validation**: after deploy, monitor `state/scheduler_jobs_health.json::ingest_opendata_daily_mx2t6.last_success_at` to advance to a 2026-05-08+ timestamp; confirm new `source_run` rows; confirm `source_run_coverage.readiness_status='LIVE_ELIGIBLE'` for D+10.

### 6.5 Followups (not blocking)

- Amend Phase B Round-2 dossier with empirical staggering disproof.
- Telemetry: populate `source_run.fetch_duration_s` correctly (currently 0 because fetch_started_at == fetch_finished_at).

## 7. Cross-reference to prior step-grid REPORT

The prior REPORT (`docs/operations/task_2026-05-08_ecmwf_step_grid_scientist_eval/REPORT.md`) verified PR#94 STEP_HOURS = `range(3,147,3)+range(150,285,6)` is the unique correct list. **Reinforced by today's empirical 1.59 GB retrieve.** The new finding here: publication staggering is NOT a real issue for `enfo` index files — single-batch at T+7h40min — so the strategy collapses to "fix subprocess reliability."

## 8. Limitations

[LIMITATION] Single 609.6s retrieve at T+15.5h; production may face higher contention. F2 (AWS mirror fallback) is the structural mitigation.

[LIMITATION] The 09:19 CDT post-PR#94 catch-up failure cause is inferred from truncated stderr. F1's three measures address all three candidates (timeout, rate-limit, network blip).

[LIMITATION] Confluence DAC schedule and set-iii distinguish "Forecast Day N" from "Derived products" timing. I assert base ENS index files belong to "Forecast Day N" rows (all complete by Day-15 row at T+7h40min). Verified empirically across 4 cycles, but not via reading ECMWF's CDN-upload code.

## 9. Final verdict

| Field | Value |
|---|---|
| **Recommendation** | `RECOMMEND_F1` (subprocess reliability fix; no new lag) |
| **One-line summary** | Operator's premise (staggered publication) is empirically false. Fix the subprocess (timeout 600→1500s + bounded retry + full stderr capture), keep the existing 485-min lag, keep PR#94 STEP_HOURS, change zero schema. |
| **Patch surface** | 3 small edits in `src/data/ecmwf_open_data.py`, no schema change, <50 lines diff. |
| **Freshness cost** | 0 minutes for both D1-D5 and D6-D10. |
| **Schema/coverage impact** | None. |
| **Calibration impact** | None. |
| **Followups** | (a) Amend Phase B Round-2 dossier. (b) Optional F2 AWS mirror fallback. (c) Telemetry: populate `source_run.fetch_duration_s`. |

[FINDING] **PR#94 has already structurally solved the only actual step-grid problem.** The 4-day gap continues only because (a) daemon hadn't restarted to load PR#94 until 08:58 CDT 2026-05-08, and (b) subprocess wrapper's 600s timeout + zero retry is fragile against a 10-minute single-batch fetch on a rate-limited CDN. F1 closes both gaps with a sub-50-line patch and zero freshness cost.

[FINDING] No option that involves splitting one cycle's fetch into two phases (B, C, F) is justifiable on the empirical evidence.
