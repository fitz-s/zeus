# ECMWF pipeline latency — measured, and mostly NOT a defect (2026-09-17)

The consult proposed that a whole-horizon barrier might be costing ~81-90 minutes and
said to instrument before optimizing, because "the actual gain may be zero." It was
right. This records the probes so nobody re-opens it on the earlier hypothesis.

## What I measured

### 1. Our download is fast; our start time is late — but the start time is the provider's

From `source_run` (`zeus-forecasts.db`), last 10 ECMWF cycles:

| | value |
|---|---|
| fetch START after cycle | min +465m, **median +494m**, max +551m |
| download DURATION | min 0.0m, **median 2.3m**, max 10.4m |
| observed/expected | 362/362 … 378/378 (all COMPLETE, partial_run=0) |

So the pipeline itself costs ~2 minutes. Raising `ZEUS_ECMWF_MAX_WORKERS` from 2 to 5
could recover at most ~1.4m of a 2.3m median download (and zero when there is no
backlog). The docstring/code mismatch (`:19` claims 5, `:536` defaults to 2) is real but
worth ~1 minute, not the headline.

### 2. The +400m partial path IS wired and live — my "unwired" reading was wrong

`evaluate_safe_fetch` probed directly across a cycle:

```
+399m  SKIPPED_NOT_RELEASED
+400m  FETCH_ALLOWED   partial_window=True  live_auth=False
                       target_window_live_authorization=True  entry=True profile=True
+485m  FETCH_ALLOWED   partial_window=False live_auth=True
```

I had read `forecast_live_daemon.py:531-540` as downgrading every partial-window fetch to
`HORIZON_OUT_OF_RANGE`, because `release_calendar.py:366-370` sets
`live_authorization = ... and not partial_window` → False during the window. **That is
not what happens:** the daemon's guard tests `entry_live_authorization` and
`profile_live_authorization`, both of which are `True` at +400m. Fetch is permitted from
+400m. Confirmed independently by the live log: **zero** occurrences of
`HORIZON_OUT_OF_RANGE` or "cycle is not live-authorized" in `logs/zeus-forecast-live.log`.

One genuine (cosmetic) finding survives: `target_window_live_authorization` is computed at
`release_calendar.py:350` and **read by no consumer anywhere** (`rg` across `src/` and
`scripts/` returns only its own definition and the dict key). It is dead metadata — the
gate it was built for is satisfied through the entry/profile flags instead. Not a
behaviour bug; delete it or wire it, but do not "fix" it thinking it is the 94 minutes.

### 3. Cycle selection is already optimal — the alarm I raised was wrong

At 18:00 UTC the selector picks 06Z, not 12Z, which looked like serving a 12-hour-old
cycle. It is correct: at 18:00, 12Z is only **+360m** old and its first products do not
exist until +400m (`next_safe_fetch_at = 18:40`). Meanwhile the 06/18 profile is
`horizon_profile: short` with `default_lag_minutes: 285`, so those cycles release
*earlier* than 00/12's 400m. The selector walks candidates newest-first and takes the
first `FETCH_ALLOWED` — exactly right.

Selected-cycle age across a full day (15-min grid, n=96):

```
min=+285m  p50=+525m  p90=+690m  max=+750m   (worst 06:30 UTC)
```

Theoretical best given 6-hourly cycles and the two release lags is `[285, 760]m`. We
measure `[285, 750]m` — **inside the bound**. There is no scheduling slack to recover.

## Verdict

| Opportunity | Measured worth |
|---|---|
| Whole-horizon barrier | **none** — partial path live from +400m, selection already optimal |
| Download concurrency 2→5 | ~1.4 min of a 2.3 min download, only under backlog |
| Subprocess cold start + JSON intermediate | **UNMEASURED** — see below |

The remaining ECMWF opportunity is the process boundary, not scheduling: each cycle does
`bytes → disk GRIB → subprocess (conda python, per track) → JSON on disk → re-read →
rows`. The consult's preferred design is download/range assembly → complete-message
framing → **warm** isolated decoder workers → typed binary batches → canonical ingest,
keeping the subprocess isolation (native decoder crashes, ABI/env problems, memory
growth) but paying the startup once. eccodes can build a handle from an in-memory
message, so the GRIB need not land on disk to satisfy the decoder — but that requires a
complete framed message, not an arbitrary 1 MB HTTP chunk.

**Do not implement that yet.** There is no spawn/decode/serialize/commit decomposition,
so there is no honest millisecond estimate, and the whole measured pipeline is ~2.3
minutes against a provider lag of 400+ minutes. Instrument first: cold startup separately
from decode, serialization, IPC and commit.

## Standing conclusion

Provider dissemination (400-485 min) dominates our pipeline (~2.3 min) by ~200x. Nothing
in the ECMWF path is a credible source of trading alpha. **The redecision sink remains
the highest-confidence latency target** (p50 224.6s local queueing), and the pre-submit
receipt store second (p50 516ms). This matches the prior standing finding that possession
lag is provider dissemination, not our pipeline.
