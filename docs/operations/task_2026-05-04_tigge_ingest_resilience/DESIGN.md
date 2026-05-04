# PR Design: TIGGE Ingest Resilience (mirroring PR #42 for K2)

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04
**Author:** Claude Opus 4.7
**Authority basis:** Operator directive 2026-05-04 (two corrections):
- "we just fixed the data daemon yesterday but TIGGE still gone — restore TIGGE, then design structural fix"
- "backfill from last existing data point to farthest fetchable; TIGGE archive 48h embargo means live entry depends on the data daemon's real-time public feed (ECMWF Open Data), NOT TIGGE archive"

PR #42 (`33069ee4`) is the architectural precedent.

---

## Problem statement

Zeus is a weather-prediction market trader. Two distinct data flows must stay healthy:

| Flow | Source | Live cadence | Used by |
|---|---|---|---|
| **Training** | TIGGE MARS archive (multi-center ensemble) | Daily, 48h-embargoed (max fetchable issue = today − 2d) | Platt calibration pair generation |
| **Live entry** | ECMWF Open Data (public real-time feed) via data daemon | Multiple per day, no embargo | `fetch_ensemble(role='entry_primary')` in evaluator |

Both flows are alignment-critical:
- Training data must keep pace with the embargo limit (TIGGE), or Platt models become stale
- Live entry data must come from the data daemon's real-time path (ECMWF Open Data); falling back to openmeteo creates training/serving skew because openmeteo is a third-party aggregator, not an ECMWF-rooted source

This PR addresses **only the TIGGE archive flow's resilience**. A separate PR will handle the live-entry routing fix (ensuring `entry_primary` goes to ECMWF Open Data, not openmeteo).

### Current TIGGE coverage gap (2026-05-04)

```
last issue_time / target_date in ensemble_snapshots_v2 (model_version='tigge'):
  fetched up to 2026-05-01 18:50  →  target_dates ≤ 2026-05-04 (lead horizon)
fetchable today (today − 2d): 2026-05-02
fetchable in 24h: 2026-05-03
```

Backfill scope = `[2026-05-02 .. today−2d]` issue_dates per-track. Today that's a single issue_date (2026-05-02). Tomorrow, also 2026-05-03 becomes fetchable.

## Root cause (single-incident trace, 2026-05-03 19:26)

```
19:26:38  ingest_main daemon restart (PID 4571)
19:26:39  _tigge_startup_catch_up scheduled (date trigger, one-shot)
19:30:41  tigge mx2t6 download rc=4 (recoverable warning)
19:38:22  tigge mn2t6 download starts
20:08:22  ❌ download_mn2t6_low: TIMEOUT after 1800s        ← MARS upstream slow
20:11:10  ❌ extract_mn2t6_low: rc=1                          ← cascaded from incomplete download
20:11:10  ❌ _ingest_track failed: database is locked        ← Stage 3 SQL lock contention
20:11:10  ✓ Job "executed successfully"                       ← _scheduler_job wrapper swallows exception
              (scheduler_jobs_health.json marked FAILED, but no retry armed)

[next 56 hours: silent — no TIGGE attempt until daily cron fires 5/4 14:00 with target=5/2 only]
```

### Five surface symptoms = three structural decisions

| # | Symptom | Decision space |
|---|---|---|
| 1 | MARS download 1800s timeout | Either upstream is slow OR our timeout/retry strategy is single-shot |
| 2 | Both tracks die when one dies | `run_tigge_daily_cycle` couples mx2t6+mn2t6 in one call |
| 3 | Stage 3 DB lock | concurrent ingest jobs contend for zeus-world.db writer lock |
| 4 | apscheduler reports "executed successfully" despite failure | wrapper writes FAILED to scheduler_health but does not arm retry |
| 5 | No backfill until 5/4 14:00 single-date cron | startup catch-up is one-shot date trigger; archive cron only fetches T-2 single day |

→ **K=3 structural antibodies** (one per architectural seam):

## Antibody 1: Coverage-aware freshness guard (mirroring PR #42)

**Decision:** Boot AND every 30 min, check target-date coverage. If gap, force catch-up.

**Why this antibody (not "bump busy_timeout" or "just retry"):**
- PR #42 already proved this pattern for K2 forecasts/solar
- Coverage-driven (not time-driven) check is the right semantic: TIGGE is needed when target_date X is uncovered, not when "last write was N hours ago"
- Idempotent: running it when already covered is a cheap query + no-op

**Implementation:**

```python
# src/ingest_main.py — new constants
_TIGGE_FORWARD_HORIZON_DAYS = 5  # we need targets covered up to today + 5d
_TIGGE_FRESHNESS_RECHECK_MINUTES = 30  # interval of the new freshness job

def _tigge_coverage_gap(conn) -> list[str]:
    """Return list of target_dates in [today, today+horizon] missing tigge rows.

    Authority basis: training/serving alignment doctrine 2026-05-04.
    """
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=_TIGGE_FORWARD_HORIZON_DAYS)
    rows = conn.execute(
        "SELECT DISTINCT target_date FROM ensemble_snapshots_v2 "
        "WHERE model_version='tigge' AND target_date BETWEEN ? AND ?",
        (today.isoformat(), horizon.isoformat()),
    ).fetchall()
    have = {r[0] for r in rows}
    want = [(today + timedelta(days=i)).isoformat()
            for i in range(_TIGGE_FORWARD_HORIZON_DAYS + 1)]
    return [d for d in want if d not in have]


@_scheduler_job("ingest_tigge_freshness_guard")
def _tigge_freshness_guard_tick():
    """Coverage-aware TIGGE freshness check (replaces startup catch-up).

    Runs at boot and every 30 min. Computes target-date coverage gap;
    if non-empty, computes the corresponding T-2 issue dates and fires
    catch-up. Idempotent — no-op when coverage is complete.
    """
    if _is_source_paused("tigge_mars"):
        return {"status": "paused_by_control_plane"}
    from src.state.db import get_world_connection
    conn = get_world_connection()
    try:
        gap = _tigge_coverage_gap(conn)
        if not gap:
            return {"status": "ok", "covered": True}
    finally:
        conn.close()
    # Map target_date → T-2 issue_date that supplies forecasts for it
    today = datetime.now(timezone.utc).date()
    issue_dates = sorted({
        (datetime.fromisoformat(t).date() - timedelta(days=2)).isoformat()
        for t in gap
        if datetime.fromisoformat(t).date() - timedelta(days=2) >= today - timedelta(days=10)
    })
    return _tigge_run_for_issue_dates(issue_dates)
```

Schedule:
```python
_scheduler.add_job(
    _tigge_freshness_guard_tick, "interval",
    minutes=_TIGGE_FRESHNESS_RECHECK_MINUTES,
    id="ingest_tigge_freshness_guard",
    next_run_time=datetime.now(),  # fires at boot too
    max_instances=1, coalesce=True, misfire_grace_time=300,
)
```

Removes `_tigge_startup_catch_up` (subsumed) — keeps the 14:00 archive backfill cron as a defense-in-depth.

## Antibody 2: Track-independent ingest

**Decision:** Each track (mx2t6, mn2t6) runs as an independent `run_tigge_daily_cycle` call. mx2t6 success persists when mn2t6 dies.

**Why:** Today the all-or-nothing dual-track path means a single mn2t6 timeout zeros out mx2t6 success too. From 2026-05-03 evidence, mx2t6 likely had useful data that got rolled back.

**Implementation:**

```python
def _tigge_run_for_issue_dates(issue_dates: list[str]) -> dict:
    """Run TIGGE catch-up for given issue dates, per-track independently.

    Each track is a separate run_tigge_daily_cycle call so a failure in
    mn2t6 does not roll back mx2t6 progress.
    """
    from src.data.tigge_pipeline import run_tigge_daily_cycle
    overall: dict = {"per_track": {}, "errors": 0}
    for track in ("mx2t6_high", "mn2t6_low"):
        track_results: list[dict] = []
        for issue_date in issue_dates:
            try:
                r = run_tigge_daily_cycle(target_date=issue_date, tracks=(track,))
                track_results.append({"issue_date": issue_date, **r})
            except Exception as exc:
                logger.error("tigge per-track %s issue=%s failed: %s",
                             track, issue_date, exc, exc_info=True)
                overall["errors"] += 1
                track_results.append({"issue_date": issue_date,
                                      "status": "error", "error": str(exc)})
        overall["per_track"][track] = track_results
    return overall
```

Note: requires `run_tigge_daily_cycle` to accept `tracks=` kwarg — TODO confirm signature; may need to thread it through or use a single-track helper.

## Antibody 3: Lock-aware Stage 3 writer

**Decision:** Bump connection-level busy_timeout for the TIGGE writer connection AND batch the write per snapshot rather than one big transaction.

**Why:** zeus-world.db is 23GB with WAL. Concurrent writers (k2_hourly_instants_tick, opendata_*_cycle, harvester_truth_writer_tick) hold the write lock for short windows. The default 120s busy_timeout is enough for single-row contention but the TIGGE Stage 3 write does many rows in one transaction → if the start of the tx coincides with a long concurrent write, the timeout fires before the lock is acquired.

**Implementation (minimal):**

```python
# src/data/tigge_pipeline.py — _ingest_track
conn = get_world_connection()
conn.execute("PRAGMA busy_timeout=600000")  # 10 min, 5x default
# AND: split the per-snapshot write into individual transactions
for snapshot in to_write:
    with conn:
        conn.execute("INSERT INTO ensemble_snapshots_v2 ...", ...)
```

Trade-off: more commits = more fsyncs. Acceptable here since TIGGE writes <300 rows per cycle (vs k2 hourly which is 30k+).

## Antibody 4 (deferred — not blocking): Honest scheduler_health surfacing

**Status:** Not in this PR. The `_scheduler_job` wrapper already writes FAILED to scheduler_jobs_health.json on exception (verified ingest_main.py:86-100). The "executed successfully" line from apscheduler is benign — operator monitoring should consume scheduler_health JSON, not apscheduler logs.

**If after this PR we still see silent freezes:** add an alarm on scheduler_jobs_health.json showing FAILED state for `ingest_tigge_freshness_guard` over N consecutive ticks.

## Tests (relationship-test order — Fitz #1 methodology)

Following PR #42's pattern (`tests/test_ingest_main_boot_resilience.py`), add:

```
tests/test_tigge_freshness_guard.py
  test_freshness_guard_no_op_when_coverage_complete
  test_freshness_guard_fires_catchup_when_target_date_uncovered
  test_freshness_guard_maps_target_dates_to_T_minus_2_issue_dates
  test_freshness_guard_respects_paused_source

tests/test_tigge_per_track_isolation.py
  test_mx2t6_success_persists_when_mn2t6_fails
  test_per_track_errors_aggregated_in_overall_status

tests/test_tigge_pipeline_lock_handling.py
  test_busy_timeout_pragma_set_on_writer_connection
  test_per_snapshot_savepoint_releases_after_each
```

All tests must use `fn.__wrapped__` to bypass `_scheduler_job` decorator (PR #42 idiom).

## Rollout

Single PR, four atomic commits:

1. `feat(tigge): freshness-guard tick — coverage-aware boot+interval catch-up`
2. `feat(tigge): per-track isolation in catch-up loop`
3. `fix(tigge): busy_timeout + per-snapshot SAVEPOINT in _ingest_track`
4. `test(tigge): boot-resilience + per-track + lock-handling`

Branch: `tigge-ingest-resilience-2026-05-04`.
PR title: `feat(tigge): boot-resilience guard mirroring PR #42 — covers 5/5+ markets`.

## Acceptance criteria

After merge + restart:

```sql
-- Within 30 min of daemon boot, this returns 0 rows (no gap)
SELECT date(today + i) FROM (VALUES (0),(1),(2),(3),(4),(5)) v(i)
WHERE date(today + i) NOT IN (
  SELECT DISTINCT target_date FROM ensemble_snapshots_v2
  WHERE model_version='tigge' AND target_date >= date('now')
);

-- scheduler_jobs_health.json shows ingest_tigge_freshness_guard with status=OK
-- venue_order_facts row count > 0 within next opening_hunt cycle
```

## Out of scope

- ❌ Promoting openmeteo to entry_primary (correctly flagged by haiku as wrong direction — would create training/serving skew)
- ❌ Setting `entry_forecast.rollout_mode: "live"` (separate decision; only affects path selection, not data availability)
- ❌ Purging 100 stale BLOCKED readiness rows (separate task #134)
