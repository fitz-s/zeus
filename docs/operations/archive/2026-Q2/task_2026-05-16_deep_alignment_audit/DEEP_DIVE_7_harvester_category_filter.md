# Deep Dive — Finding #7: Harvester "Category Filter" (Reframed)

Date: 2026-05-16
Worktree: `.claude/worktrees/zeus-deep-alignment-audit-skill`
Scope: READ-ONLY investigation, no DB writes, no code edits.

---

## Executive Summary

Run #2 framed Finding #7 as *"harvester has no category filter — it pulls every closed
Polymarket event (cricket/MLB/CS2/tennis/etc.)"*. That framing is **partially wrong**.

There is no `tag:weather` filter in the Gamma `/events?closed=true` call, but a
*city-alias filter* (`_match_city`) is applied downstream. The harvester does **not**
ingest sports settlements into `settlements_v2`. The real defect is upstream: the
**unbounded category sweep collides with the 120-second wall-cap and exhausts the
30-day paginator on sports/esports events before reaching weather events**, so the
in-window weather settlements are never seen by the writer. The cascade
`harvester_truth_writer → harvester_pnl_resolver → _settle_positions → clob.redeem`
is structurally wired and the feature flag is ON, but the truth writer is starved
of weather events.

Direct evidence (`logs/zeus-ingest.err`, last 5 MB):

- 11 wall-cap warnings 2026-05-13 → 2026-05-14 at offsets 10 600 → 24 700.
- 2026-05-14 03:47:56 — `harvester_truth_writer write failed for Karachi 2026-05-10: database is locked`.
- 2026-05-14 04:47:57 — `… Karachi 2026-05-09: database is locked`.
- Every subsequent tick reports `markets_resolved=0, settlements_written=0`.
- Hundreds of `WARNING … skipping <city> <date> ambiguous winners=N slug=<mlb|nba|atp|cs2|crint|cricipl|wta|lal>-…` proving the events being chewed through are sports / esports.

Last successful Karachi weather settlement: `target_date 2026-05-06`, `settled_at
2026-05-07T18:23:09+00:00`, authority VERIFIED (`settlements_v2 row 55967`). No
Karachi weather row for 2026-05-07 → 2026-05-16. Same coverage gap holds for the
other monitored cities except the 2025-12-19/20/21 batch back-fill on 2026-05-11
(IDs 749–768).

Verdict: **BUG-IN-FILTER (paginator starvation), with co-defect DB-LOCK-CONTENTION**.

---

## Code-Path Analysis

### 1. Open-markets selector — no Gamma-side filter

`src/ingest/harvester_truth_writer.py:239` `_fetch_open_settling_markets()`:

```python
resp = httpx.get(
    f"{GAMMA_BASE}/events",
    params={
        "closed": "true",
        "limit": _CLOSED_EVENTS_PAGE_LIMIT,   # = 100
        "offset": offset,
        "order": "endDate",
        "ascending": "false",
    },
    timeout=30.0,
)
```

Module-private constants (lines 39-41):

```python
_CLOSED_EVENTS_CUTOFF_DAYS = 30          # live scope: events closed ≤30d ago
_CLOSED_EVENTS_MAX_WALL_SECONDS = 120    # mandatory wall-cap antibody (Fitz §3)
_CLOSED_EVENTS_PAGE_LIMIT = 100
```

No `tag`, `category`, `series_id`, or `topic` parameter is sent. Gamma returns
every closed event in the last 30 days, sorted newest-first. Termination
conditions, in priority order:

1. Wall-cap exceeded (120 s) → log warning and break (`harvester_truth_writer.py:267-272`).
2. Empty batch → break (`:286-287`).
3. Oldest `endDate` in batch < cutoff_iso → break (`:295-296`).
4. Short page (< 100) → break (`:297-298`).

### 2. City filter is alias-keyword, not category

`src/data/market_scanner.py:1110` `_match_city(title, slug)` joins both
strings and tests boundary-aware regex matches of every alias / slug_name from
`cities.json`. If no city matches → caller (`harvester_truth_writer.py:683`)
hits `continue` and the event is dropped without writing anywhere. So sports
events with no city alias are silently discarded; **they never enter
settlements_v2**.

Sports events whose slug happens to contain a city alias (e.g. `mlb-tex-nyy-…`
matches "NYY" → NYC alias? No — but `slug=mlb-bal-mia-2026-05-07` matches
"MIA" → Miami; `slug=lal-rea-ovi-2026-05-14` matches "REA"/"OVI" or
"Madrid" via `lal` cluster mapping) reach `_extract_resolved_market_outcomes`,
which returns several `yes_won` outcomes because event has many sub-markets,
each binary YES/NO. Caller rejects them at:

```python
if len(winning_outcomes) != 1:
    if winning_outcomes:
        logger.warning("… ambiguous winners=%d slug=%s", …)
    continue
```

So they still never enter `settlements_v2`. The Run-2 framing
("category-less harvester pollutes the writer") is **incorrect**; sports
events are inert to writes, but they are **expensive to fetch** and they
**eat the wall-cap budget**.

### 3. Cascade after a settlements_v2 VERIFIED row

| Step | Module : function | Trigger |
| --- | --- | --- |
| 1. Write truth | `src/ingest/harvester_truth_writer.py` `_write_settlement_truth` | hourly cron, `minute=45`, `ingest_harvester_truth_writer` job in `src/ingest_main.py:1142` |
| 2. Resolve P&L | `src/execution/harvester_pnl_resolver.py:38` `resolve_pnl_for_settled_markets` | called from `src/main.py:143` inside live-trading daemon loop |
| 3. Settle positions | `src/execution/harvester.py:800` `_settle_positions` | called by step 2; updates `position_current.phase='settled'`, writes `position_events`, decision_log |
| 4. Redeem on-chain | `src/execution/harvester.py:923` `clob.redeem(pos.condition_id)` | for winning positions; alerts via `alert_redeem` |

Feature flag `ZEUS_HARVESTER_LIVE_ENABLED=1` confirmed in
`~/Library/LaunchAgents/com.zeus.data-ingest.plist`.
Daemon `com.zeus.data-ingest` is loaded (`launchctl list`, PID 34316).
Daemon `com.zeus.live-trading` is loaded (PID 86141).

---

## Evidence Trail

### settlements_v2 health

```
authority counts last 14d:
  ('QUARANTINED', 382)
  ('VERIFIED',   3605)

settled_at distribution last 14d:
  ('2026-05-07', 3220)
  ('2026-05-11',  767)
  -- nothing 2026-05-12 → 2026-05-16
```

The 2026-05-11 batch wrote settlements with `target_date='2025-12-21'`, i.e.
historical back-fill via `scripts/backfill_settlements_via_gamma_2026.py`, not
live harvester output. The live cron has written **zero rows since 2026-05-11
because every tick reports `markets_resolved=0, settlements_written=0`**.

### Harvester tick events (zeus-ingest.err)

```
2026-05-14 05:46:07 INFO harvester_truth_writer: found 9765 settled events
2026-05-14 06:45:42 INFO harvester_truth_writer: found 9775 settled events
... (every hour)
2026-05-14 16:45:00 INFO harvester_truth_writer: found 0 settled events
2026-05-14 17:45:00 INFO harvester_truth_writer: found 0 settled events
2026-05-14 18:45:00 INFO harvester_truth_writer: found 0 settled events
2026-05-14 19:45:00 INFO harvester_truth_writer: found 0 settled events
2026-05-14 21:50:16 INFO harvester_truth_writer: found 0 settled events
2026-05-14 22:51:21 INFO harvester_truth_writer: found 0 settled events
2026-05-14 23:45:41 INFO harvester_truth_writer: found 9757 settled events
2026-05-15 00:46:22 INFO harvester_truth_writer: found 9745 settled events
2026-05-16 11:45:46 INFO harvester_truth_writer: found 9786 settled events
```

Six consecutive "found 0 settled events" between 2026-05-14 16:45 and 22:51
PDT — Gamma `/events?closed=true&ascending=false` returned an empty payload
for those windows, likely a Polymarket API outage or rate-limit response.
Recoverable; not the primary defect.

### Karachi-specific write failures

```
2026-05-14 03:47:56 WARNING harvester_truth_writer write failed for Karachi 2026-05-10: database is locked
2026-05-14 03:47:56 ERROR   harvester_truth_writer error for event highest-temperature-in-karachi-on-may-10-2026: database is locked
2026-05-14 04:47:56 WARNING harvester_truth_writer write failed for Karachi 2026-05-09: database is locked
2026-05-14 04:47:56 ERROR   harvester_truth_writer error for event highest-temperature-in-karachi-on-may-9-2026: database is locked
```

So the API **did** return Karachi 5/9 and 5/10 weather events; the writer
**did** attempt them; the write failed because `zeus-forecasts.db` was held
by a concurrent writer (forecast-live or bulk lock). On the next tick the
event is in `market_events_v2` either way (INSERT OR IGNORE) — but no — the
event is dropped before insert, so retry must come from the next 30-day
paginator pass. With wall-cap firing at offsets 10 600-24 700 the same event
is unlikely to be reached again. **DB-LOCK-CONTENTION compounds the
paginator starvation**: a transient lock during the 60-second window the
event is in scope = silent permanent skip.

### Why no Karachi 5/11 → 5/16 row

- Gamma slug `highest-temperature-in-karachi-on-may-17-2026` is **`closed=False`**
  (`endDate=2026-05-17T12:00:00Z`); will not appear in the closed-events feed
  until ~2026-05-17 19:00 UTC + UMA-resolution delay.
- 5/11–5/15 Karachi weather events have `endDate ≈ target_date + 1 day` and
  *are* in the 30-day window, but the harvester's wall-cap (or the 0-event
  windows around 5/14) skipped them and no retry tick has reached them since.

### Gamma API live-probe (this session)

```python
GET https://gamma-api.polymarket.com/events
    ?slug=highest-temperature-in-karachi-on-may-17-2026
→ 1 event, closed=False, endDate=2026-05-17T12:00:00Z, id=486870
  37°C YES market:
    conditionId=0xc5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae
    YES token=53911939…757884
    outcomePrices=["0.415","0.585"]   (YES=0.415)
```

Position (`zeus_trades.db.position_current`) confirmed pointing at the same
conditionId and YES token.

---

## Verdict

**BUG-IN-FILTER (paginator starvation) + DB-LOCK-CONTENTION co-defect**

1. Primary: the 120 s wall-cap + 100/page limit + descending-endDate sweep is
   insufficient to reach all weather events in the 30-day window when
   sports/esports closure traffic dominates (~10 000 events / 30 d).
2. Secondary: when a weather event *is* reached, an `OperationalError:
   database is locked` raised during write silently consumes the only retry
   opportunity that paginator pass will see.
3. Tertiary: occasional Gamma API empty-response windows (six 0-event ticks
   on 2026-05-14 PDT) further reduce coverage; no retry semantics in
   `_fetch_open_settling_markets`.

Run-2's "no category filter" wording mis-targets the symptom. The harvester
**does** have a (city-alias) filter; it's the absence of a Gamma-side
filter that wastes paginator budget on inert sports events.

---

## Fix Recommendation

Short-term (operator-attended, today):
- Run `python -m scripts.backfill_harvester_settlements --days 14` whenever a
  weather settlement is suspected to have been missed. This script reuses
  `write_settlement_truth_for_open_markets` with a 900 s wall-cap. Already
  authority-blessed (PLAN §10, INV-Harvester-Liveness) and idempotent.

Structural (single design decision, makes the defect category impossible):
- Pass a Gamma-side tag filter to `/events?closed=true&tag=weather` (or the
  closest supported tag/series id) in `_fetch_open_settling_markets`. Cuts
  per-page candidates ~99% and removes the wall-cap pressure entirely.
  Verify Gamma's tag taxonomy first; if no single `weather` tag exists,
  enumerate the per-city series_ids from `cities.json` and OR-join them.
- Add bounded retry-on-lock in `_write_settlement_truth`: catch
  `sqlite3.OperationalError: database is locked`, sleep 0.5-2 s with jitter,
  retry up to 3 times. Use the same envelope used by
  `src.state.canonical_write.commit_then_export`.
- Track a per-event "last_attempted" cursor so wall-cap truncation can resume
  next tick from the offset that was reached, instead of starting at 0.

Test invariant (relationship test):
- Given a 14-day window with at least one weather event per monitored city
  per day, the harvester writes ≥1 settlement per city per day within
  ≤24 h of the event's `endDate`. Express as a SQL invariant in CI replay.

Anti-pattern to avoid:
- Do **not** raise `_CLOSED_EVENTS_MAX_WALL_SECONDS` past 120 s in the live
  twin. The wall-cap is the antibody. The fix is to reduce candidate
  volume, not to lengthen the budget.
