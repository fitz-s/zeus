# Dead-loop Diagnosis 2026-06-14

**Written:** 2026-06-14 ~16:45 UTC  
**Daemon:** pid 449, started 2026-06-13 20:11:31, tree SHA 675aebca27 (branch live/iteration-2026-06-13)  
**Method:** read-only log+DB bisect — NO code edits, NO daemon signals

---

## Provenance

| Artifact | Value |
|---|---|
| Daemon PID | 449, started 2026-06-13 20:11:31 UTC |
| Tree SHA | 675aebca27 (branch live/iteration-2026-06-13) |
| Log file | logs/zeus-live.log, ~1.88 GB, mtime 11:29:59 local |
| edli_no_submit_receipts MAX(created_at) | 2026-06-12T12:12:20 — zero since daemon start |
| venue_commands MAX(created_at) | 2026-06-12T13:04:26 — zero since daemon start |
| executable_market_snapshots MAX(captured_at) | 2026-06-14T16:37:03 — actively flowing |

---

## First Severed Stage: Executable Snapshot Gate

The funnel breaks at **stage 5 (executable snapshot gate)**, inside every reactor cycle.

### Evidence — reactor cycle output

Every observed reactor cycle:

```
2026-06-14 11:31:18,318 [zeus] INFO: EDLI reactor cycle result:
  processed=0 proof_accepted=0 rejected=0 retried=9 dead=0 claim_lock_bounces=0 reasons=[]

2026-06-14 11:38:49,914 [zeus] INFO: EDLI reactor cycle result:
  processed=0 proof_accepted=0 rejected=0 retried=11 dead=0 claim_lock_bounces=0 reasons=[]
```

Zero events ever progress past the gate. All 9–11 events per cycle are transient-requeued.

### Two distinct failure modes

**Mode A — EXECUTABLE_SNAPSHOT_BLOCKED (entry gate, 4 events, permanent):**

```
2026-06-14 11:36:23,910 [zeus.events.reactor] INFO: reactor: money-path transient requeued
  event_id=edli_evt_c915707... count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
2026-06-14 11:36:23,913 [zeus.events.reactor] INFO: reactor: money-path transient requeued
  event_id=edli_evt_05bad84... count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
2026-06-14 11:36:28,379 [zeus.events.reactor] INFO: reactor: money-path transient requeued
  event_id=edli_evt_51e9332... count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
2026-06-14 11:36:28,381 [zeus.events.reactor] INFO: reactor: money-path transient requeued
  event_id=edli_evt_125c2b2... count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
```

**Mode B — EXECUTABLE_SNAPSHOT_STALE (deeper in adapter, 7 events, cycling):**

```
2026-06-14 11:38:47,905 [zeus.events.reactor] INFO: reactor: money-path transient requeued
  event_id=edli_evt_237710... count=1
  reason=EXECUTABLE_SNAPSHOT_STALE:freshness_deadline=2026-06-14T16:35:14.140063+00:00:
         decision_time=2026-06-14T16:38:17.306448+00:00
```

---

## Root Cause A — Missing Market Topology (BLOCKED events)

**File:** `src/engine/event_reactor_adapter.py:14387` (`_event_family_market_topology_rows`)  
**Gate:** `src/engine/event_reactor_adapter.py:1029` (entry gate inside `executable_snapshot_gate_from_trade_conn`)  
**Reactor set:** `src/main.py:1428`

The entry gate calls `_event_family_market_topology_rows(forecasts_conn, payload)`. This queries `market_events` in `zeus-forecasts.db` for `(city, target_date, temperature_metric)`. If it returns empty, the gate returns `False` → reactor assigns `EXECUTABLE_SNAPSHOT_BLOCKED` at `reactor.py:1439`.

The 4 persistently BLOCKED events are all **DAY0_EXTREME_UPDATED** events targeting the **low** temperature metric for cities whose `market_events` has only **high** rows (or no rows at all) for 2026-06-15:

| event_id (prefix) | city | metric | market_events rows for low |
|---|---|---|---|
| edli_evt_51e9332... | Manila | low | 0 (only high/11 exists) |
| edli_evt_125c2b2... | Jinan | low | 0 (no rows at all) |
| edli_evt_c91570... | Guangzhou | low | 0 (only high/11 exists) |
| edli_evt_05bad8... | Zhengzhou | low | 0 (no rows at all) |

Query confirming this:
```sql
-- zeus-forecasts.db
SELECT city, temperature_metric, COUNT(*) FROM market_events
WHERE city IN ('Manila','Jinan','Guangzhou','Zhengzhou') AND target_date='2026-06-15'
GROUP BY city, temperature_metric;
-- Result: Guangzhou|high|11, Manila|high|11  (Jinan and Zhengzhou: zero rows)
```

These four low-metric DAY0 events will NEVER clear without market topology being ingested for the `low` side of these families. The horizon check (venue-close / timeliness floor) is the only exit — they will terminalize at market close if the topology gap is not filled.

---

## Root Cause B — captured_at Ceiling Excludes Targeted-Refresh Rows (STALE events)

**File:** `src/engine/event_reactor_adapter.py:12921–12924`

The 7 STALE events belong to cities that DO have market topology (e.g., Guangzhou/high, Chongqing/high, etc.). The entry gate passes, but the selected snapshot row has `freshness_deadline` in the past at `decision_time`.

The adapter invokes the targeted refresher (`_edli_decision_family_snapshot_refresher`, `src/main.py:7115`). The refresher:
1. Calls `reconstruct_weather_market_from_static_topology` — succeeds (snapshot rows exist).
2. Opens its own write connection and runs a PolymarketClient CLOB fetch — inserts fresh rows with `captured_at ≈ decision_time + 1s`.
3. Returns `True` (inserted > 0).

The adapter then re-elects via `_latest_snapshot_rows_for_event_family` with `fresh_at=decision_time`. At `src/engine/event_reactor_adapter.py:12921`:

```python
if fresh_at is not None and "captured_at" in columns:
    checked_at = fresh_at.astimezone(UTC) ...
    predicates.append("captured_at <= ?")
    params.append(checked_at.isoformat())
```

The re-elect query applies `captured_at <= decision_time`. The rows just inserted by the targeted refresher have `captured_at > decision_time` — they are **excluded**. The re-elect returns the pre-refresh stale rows. `_snapshot_price_stale_reason` fires again → `EXECUTABLE_SNAPSHOT_STALE` → requeue.

**This is a structural loop**: the targeted refresh succeeds at capturing fresh rows, but the re-elect ceiling (`captured_at <= decision_time`) makes those rows invisible to the same decision pass. The event requeuees with a slightly more recent stale deadline each cycle (tracking the substrate warm's rotating refreshes), but never clears.

Evidence: freshness_deadline in STALE reasons tracks forward over successive cycles (16:10 → 16:35) matching the substrate warm cadence, confirming the targeted refresh inserts ARE landing but are immediately invisible to the re-elect.

---

## Why There Were No Decisions Since 2026-06-12

The `edli_no_submit_receipts` and `venue_commands` cut at 2026-06-12 predates the current daemon (started 2026-06-13). The current daemon has produced zero output since start because:

1. All active DAY0_EXTREME_UPDATED events (the only currently-queued event type) hit one of the two failure modes above — BLOCKED (missing topology) or STALE (captured_at ceiling excludes targeted-refresh rows).
2. The `edli_redecision` screener shows `enqueued=0 skipped_pending=123` — 123 pending events already exist, 0 new ones being added, so the screener is not the bottleneck.
3. The substrate warm captures ~513 rows per big cycle but with a 30s freshness window, any city not refreshed within the LAST 30s is stale at decision time.

This is **BROKEN** (not honest zero). The 373k snapshot rows in `executable_market_snapshots` are real and flowing. 373k rows / 22 rows per family / every 20s = evidence of active capture. The reactor simply cannot consume them due to the two structural defects above.

---

## Whether a Daemon Restart Fixes Anything

The targeted-refresh `captured_at` ceiling bug (Root Cause B) is in the current tree code (`675aebca27`). A restart on the current tree would reproduce the same behavior. The missing-topology bug (Root Cause A) is a data gap in `market_events` — a restart does not help.

A restart is **not needed** and **would not fix** either root cause.

---

## Minimal Run-Confirmed Test

For Root Cause A (BLOCKED): Query `zeus-forecasts.db` market_events for the event's `(city, metric, target_date)` — zero rows confirms the block. No live probe needed.

For Root Cause B (STALE): Add a `print` to the refresher's re-elect query and confirm `captured_at > decision_time` for the newly inserted rows, OR observe that removing the `captured_at <= ?` predicate from the re-elect (the call with `require_fresh=False` at adapter line 2299) resolves the stale loop — that predicate is only needed for the price-freshness path (which uses `require_fresh=True`), not the post-refresh re-elect.

---

## Single Highest-Value Fix

**Root Cause B is the higher-value fix** because it affects 7 of 9–11 events per cycle, covers all cities with active topology, and the targeted refresh already captures good data — it is thrown away by one predicate.

**The fix:** In the re-elect call inside `build_event_bound_no_submit_receipt` (adapter ~line 2299), pass `fresh_at=None` instead of `fresh_at=decision_time` — or introduce a separate `post_refresh_re_elect` parameter that skips the `captured_at` ceiling. The `require_fresh=False` path should not apply a future-excluding `captured_at` ceiling; that ceiling is a holdover from the initial "snapshot must precede decision" design that predates the targeted-refresh path.

**Root Cause A fix:** Ingest `market_events` rows for the `low` metric families that Gamma currently has empty slugs for, OR let the horizon check terminalize those specific events (the four low-metric events for Manila/Jinan/Guangzhou/Zhengzhou whose low-side Gamma markets may not exist).
