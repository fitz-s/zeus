# Afternoon Capture Fix — Investigation 2026-06-13

**Created:** 2026-06-14
**Authority basis:** investigation_2026-06-13/nowcast_backtest.md + docs/evidence/no_order_root_2026-06-13/diagnosis.md

---

## Root Mechanism (file:line)

`src/data/market_scanner.py`, function `_slug_pattern_target_dates()` (approx. line 1649):

```python
# BEFORE (broken):
first_offset = 1 if now.hour >= 12 else 0
…
for offset in range(first_offset, max_target_offset_days + 1)
```

After UTC 12:00, `first_offset` became 1, so **today was excluded from slug fallback discovery**. Markets whose Gamma tag entries had not yet propagated (or were tagged only by slug) could not be found. Snapshot capture therefore froze at the last pre-noon snapshot (~11:27–11:36Z) because the universe had no live rows for same-day families after 12:00 UTC. The EDLI warm cycle (`_refresh_pending_family_snapshots`) independently skips families where `family_venue_closed()` returns True (F1 anchor = 12:00 UTC), so it also went quiet at noon.

---

## Diff Summary

### 1. `src/data/market_scanner.py` — core fix

```diff
-    first_offset = 1 if now.hour >= 12 else 0
+    # Afternoon-capture fix (2026-06-14): always include today in slug discovery.
+    # The prior `first_offset = 1 if now.hour >= 12 else 0` excluded today after
+    # UTC noon.  After 12:00 UTC markets that resolved at 12:00Z simply return
+    # empty/404 from Gamma — the budget guard bounds the cost; no DoS risk.
     …
-        for offset in range(first_offset, max_target_offset_days + 1)
+        for offset in range(0, max_target_offset_days + 1)
```

**Effect:** `_slug_pattern_target_dates()` now always returns `[today, today+1, today+2]` regardless of UTC hour. Gamma returns 404/empty for already-expired slugs, which the existing status-code guard handles silently.

### 2. `src/main.py` — new afternoon capture scheduler job

New function `_afternoon_snapshot_capture_cycle()` with `@_scheduler_job("afternoon_snapshot_capture")` decorator. Registered in both the **legacy_cron** and **EDLI event-driven** scheduler blocks:

```python
scheduler.add_job(
    _afternoon_snapshot_capture_cycle,
    "interval",
    minutes=30,
    id="afternoon_snapshot_capture",
    next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 120.0),
    max_instances=1,
    coalesce=True,
)
```

The job:
1. Acquires `_market_substrate_refresh_lock` (non-blocking — skips if substrate refresh already running).
2. Calls `find_slug_pattern_weather_markets(min_hours_to_resolution=0.0)` (slug-only, no full tag scan).
3. Filters to `0 < hours_to_resolution <= 12.0` (same-day settlement window).
4. If any such markets exist, calls `refresh_executable_market_substrate_snapshots()` with a 60s budget.

### 3. `tests/test_scanner_slug_pattern.py` — test updates

- Updated `test_default_slug_pattern_dates_always_includes_today_and_covers_opening_hunt_horizon` (was `…_skip_expired_and_cover_opening_hunt_horizon`) to assert `["2026-05-19", "2026-05-20", "2026-05-21"]` (now includes today).
- Added `test_afternoon_slug_discovery_includes_today_with_hours_to_local_eod_in_range` (see below).

---

## Throttle / Rate-Limit Analysis (no DoS)

| Guard | Mechanism | Value |
|---|---|---|
| Wall-clock budget | `budget_seconds` param in `refresh_executable_market_substrate_snapshots` | 60s (env `ZEUS_AFTERNOON_CAPTURE_BUDGET_SECONDS`) |
| Per-city outcome cap | `max_outcomes` in same function | 4 (env `ZEUS_MARKET_DISCOVERY_SNAPSHOT_MAX_OUTCOMES`) |
| Concurrent fan-out | `_market_substrate_refresh_lock.acquire(blocking=False)` | Skip if already running |
| Stacked runs | `max_instances=1, coalesce=True` on APScheduler job | At most 1 inflight |
| Slug request guard | `status_code != 200` / empty-list guard in `_fetch_events_by_slug_pattern` | Already-closed slugs: empty/404 → 0 CLOB calls |
| Interval | 30 min; overlaps impossible via lock + coalesce | Max 2 runs/hour |
| CLOB timeout | `ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS` (default 5s) | 5s per request |

After 12:00 UTC, already-settled markets return 404 or an empty list from Gamma. No CLOB calls are issued for them. The job is structurally a no-op when no same-day markets have `0 < hours_to_resolution <= 12`.

The total additional CLOB call budget per afternoon = (cities with live same-day markets) × (max_outcomes=4) × (within 60s wall-clock). For the verified universe (≤20 cities) worst-case is 80 CLOB calls / 30 min = 2.7 calls/min. Compared to the existing `edli_market_substrate_warm` cycle (20s interval, full pending families): order of magnitude less aggressive.

---

## RED-on-revert Test

In `tests/test_scanner_slug_pattern.py`:

```python
def test_afternoon_slug_discovery_includes_today_with_hours_to_local_eod_in_range():
    """RED-ON-REVERT: for a same-day market with hours-to-local-EOD in (0, 12],
    the capture schedule includes afternoon instants (UTC 12:00-23:59).

    Revert `first_offset = 1 if now.hour >= 12 else 0` → today drops from dates →
    same-day markets invisible to slug fallback in the afternoon window → RED.
    """
    from datetime import date
    afternoon_utc = datetime(2026, 5, 19, 17, 30, 0, tzinfo=timezone.utc)
    dates = ms._slug_pattern_target_dates(afternoon_utc)
    today_str = date(2026, 5, 19).strftime("%Y-%m-%d")
    assert today_str in dates, (
        f"REGRESSION: today ({today_str}) must be in slug discovery dates at 17:30 UTC. "
        "Without this, same-day markets with hours_to_resolution in (0, 12] cannot "
        "be discovered after UTC noon and afternoon capture breaks."
    )
```

Sedvert: reintroduce `first_offset = 1 if now.hour >= 12 else 0` → `today_str` not in `dates` → assertion fails (RED).

---

## Deploy Procedure

### What merges
- `src/data/market_scanner.py` (1-line functional change + comment)
- `src/main.py` (new `_afternoon_snapshot_capture_cycle` function + 2 scheduler registrations)
- `tests/test_scanner_slug_pattern.py` (updated + new antibody test)
- This document

No DB schema changes. No migration required.

### Restart needed?
**Yes — daemon restart required.** The new `_afternoon_snapshot_capture_cycle` APScheduler job is only registered at daemon startup. Without restart the job never runs.

### Confirmation: afternoon snapshots appearing
After restart, verify via:

```bash
# From zeus/
python3 -c "
import sqlite3, os
db = os.environ.get('ZEUS_TRADES_DB', 'data/zeus_trades.db')
conn = sqlite3.connect(db)
rows = conn.execute('''
    SELECT condition_id, substr(captured_at,1,19) as ts, top_bid, top_ask
    FROM executable_market_snapshots
    WHERE refresh_reason = 'afternoon_snapshot_capture'
    ORDER BY captured_at DESC
    LIMIT 20
''').fetchall()
for r in rows: print(r)
conn.close()
"
```

Expect rows with `refresh_reason = 'afternoon_snapshot_capture'` and `ts` >= 12:00 UTC for same-day settlement cities (Amsterdam, Denver, Jeddah, etc.).

Monitor scheduler health:

```bash
cat .zeus_run/scheduler_jobs_health.json | python3 -m json.tool | grep -A5 afternoon_snapshot_capture
```

Should show `last_success` updating at ~30-min cadence.

If no rows appear after first 30-min interval post-restart: check whether `find_slug_pattern_weather_markets` returns any same-day events (`hours_to_resolution` in (0,12]) — if zero same-day markets are live the job silently no-ops (correct behavior).
