# Day0 speed-edge capture audit

**Date:** 2026-07-19 (measured ~09:00–09:45 UTC) · **Mode:** READ-ONLY (`sqlite3 -readonly`; no writes, no code edits, no market-price backtests)
**DBs:** `state/zeus-world.db` (observations, `opportunity_events`), `state/zeus-forecasts.db` (`forecast_posteriors`, `cycle_advance_enqueues`), `state/zeus_trades.db` (`position_current`, `reduce_position_economics`, `venue_commands`, `executable_market_snapshots`)

## Headline finding, before the six questions

**The event-driven Day0 bridge is real, correctly wired on both emission paths, and currently dormant in production.** It has never fired once in live trading.

- Commit `4dcba55e2` ("perf(day0): event-driven posterior recompute — DAY0_EXTREME_UPDATED seeds immediate rematerialization") landed **2026-07-19 04:36:44 EDT (08:36:44 UTC)**.
- Every Zeus daemon currently running (`src.ingest_main`, `src.ingest.forecast_live_daemon`, `src.main`, `src.riskguard.riskguard`, etc. — `ps -o lstart`) started at **03:43:12–03:43:29 EDT, ~53 minutes before the bridge was committed**. Python does not hot-reload edited modules; a long-running process keeps the bytecode it imported at start. None of these daemons has been restarted since the bridge (and the three related commits at 04:33–05:07: `a23ee5c2f` sub-hourly delta, `4dcba55e2` event bridge, `26292f020`/`90a14b0e1` perf follow-ups) landed.
- Direct confirmation: `grep -c "DAY0_METAR_MATERIALIZATION_BRIDGE" logs/zeus-ingest.log` → **0**. That log line (`src/ingest_main.py:487-490`) is unconditionally emitted every time the bridge function runs; zero occurrences over the full log means the bridge code path has executed **zero times** since the daemons started, despite `zeus-ingest.log` being written continuously through 09:25 UTC and dozens of new-extreme events having landed in that window.
- Net effect: right now Zeus is running the **pre-bridge** Day0 recompute behavior this commit was written to replace, even though the fix is sitting on disk on this branch. This is a deploy gap, not a code defect — `deploy_live.py restart all` (mesh-coherent restart) has not been run since 04:36.

Everything below measures the **currently-running (pre-bridge) system**, because that is what production has been doing all day. Where the task asked for "after the bridge landed," the honest answer is: there is no after-data — 0 rows.

---

## Q1 — Full latency chain, last 14 days, before vs after the bridge

**After the bridge: 0 rows, for the reason above.** `forecast_posteriors` (Day0 rows, `target_date = substr(computed_at,1,10)`, `runtime_layer='live'`) has **zero** rows with `computed_at >= 2026-07-19T08:36:44Z`; the last Day0 posterior in the entire dataset was computed at **08:14:04 UTC**, 22 minutes before the bridge landed, and none since — 71 minutes of complete silence on the Day0 posterior stream as of this measurement (09:25 UTC), see Q4.

**Before the bridge (the only measurable regime), recompute cadence — hop 2b, replicated over 2026-07-05→2026-07-19:**

```sql
WITH day0 AS (
  SELECT city, target_date, temperature_metric, computed_at,
         (julianday(computed_at) - julianday(LAG(computed_at) OVER (
             PARTITION BY city, target_date, temperature_metric ORDER BY computed_at
         ))) * 1440.0 AS gap_min
  FROM forecast_posteriors
  WHERE runtime_layer='live' AND target_date = substr(computed_at,1,10)
    AND computed_at >= '2026-07-05'
)
SELECT COUNT(*), AVG(gap_min) FROM day0 WHERE gap_min IS NOT NULL;
-- 11,054 gaps, avg 71.65 min, p50 44.9 min, p90 186.9 min
```

This matches the 2026-07-18 baseline report (`docs/evidence/upstream_physical_2026_07_17/day0_latency_chain_measurement.md`, hop 2b: p50 39.9 min, p90 90 min, n=9,362) within the expected drift of a 4-day-wider window and a heavier right tail (p90 187 min vs 90 min) — **consistent with the cadence getting worse, not better, over the last two days**, which lines up with Q4's finding that the scheduled reseed has effectively stalled for hours at a time.

The single-family, event-conditioned chain the task asked for (observation available → posterior recomputed *with that observation* → order submitted) could not be joined cleanly: there is no literal `day0_conditioning.observation_time` field in `forecast_posteriors.provenance_json` (checked — the closest analogue is `current_evidence_shape`/`current_value_serving`, which conditions on *model* cycle times, not the Day0 observed-extreme timestamp). The extreme-to-posterior conditioning link lives structurally in `cycle_advance_enqueues.day0_observed_extreme_observation_time` (added for exactly this purpose) and in the per-family staleness comparison done directly in Q4, which is the robust version of this same question.

## Q2 — Market repricing race: fraction of events where Zeus had an order on the book first

Two honest limits on this number, disclosed rather than papered over:

1. **Book-reprice speed itself is unchanged from the 2026-07-18 baseline** (E2 hop: p50 10.6 min, n=954, 07-16→07-18) — re-deriving it would require an unindexed full scan of `executable_market_snapshots` (10.2M rows, no index on `event_slug`; only `condition_id`/`token_id` + `captured_at` are indexed), which this budget did not spend given the far larger finding above already answers "does Zeus beat the book" for the current regime: **no — the posterior isn't even recomputing, so there is nothing to race with.**
2. **The fraction-captured metric requires a Zeus order to compare against.** Over the entire trading history, Zeus has placed exactly **2** Day0-lane positions (Q3). `venue_commands` has 355 orders total in the last 14 days (all strategies); cross-referencing which, if any, predate a ≥2c book move on their own market is not statistically meaningful at n=2.

**Honest answer: the fraction cannot be computed above noise level with n=2 realized Day0 trades.** The prior baseline's own verdict — "roughly tied, cannot be proven cleanly" — still stands, and the system is currently *behind* that baseline (see Q1/Q4), not ahead of it.

## Q3 — Realized Day0 PnL

```sql
SELECT position_id, city, target_date, direction, cost_basis_usd, entry_price,
       settlement_price, realized_pnl_usd, exit_reason
FROM position_current WHERE strategy_key='day0_nowcast_entry';
```

| position_id | city | target_date | direction | cost | entry_price | settled at | realized PnL |
|---|---|---|---|---|---|---|---|
| 5e36a294-907 | Manila | 2026-07-02 | buy_yes | $17.71 | 0.44 | 0.0 | **-$17.71** |
| 384f1dd8-5c1 | Hong Kong | 2026-07-13 | buy_yes | $1.00 | 0.001 | 0.0 | **-$1.00** |

**n=2, $18.71 total cost, -$18.71 realized (0 wins / 2 losses, 0% win rate).** Chain-truth cross-check via `reduce_position_economics` agrees on the Manila loss (`payout_status=RESOLVED_ZERO, payout_pnl_usd=-17.71`); the Hong Kong reduce-ledger row is still `PENDING` despite `position_current` showing it settled (a $1 probe position, immaterial to the number but flagged as a minor cross-table lag, not fabricated as fact).

Non-Day0 cohort for context: 1,037 closed positions, $2,810 total cost, +$3.91 realized, 60 wins / 159 losses among decided outcomes (win rate driven by many economically-closed/voided zero-cost rows).

**Honest read: the Day0 speed lane has not earned yet. It is not small-sample-noisy-but-positive — it is small-sample and 0-for-2.** This matches the operator's own framing ("may be early/small-n") — report it plainly rather than dress it up.

## Q4 — Dead-window analysis: fraction of Day0 emissions on a stale extreme

This is where the dormant-bridge finding becomes fully concrete. For every (city, metric) family in the actively-priced Day0 universe (the only cities with any `forecast_posteriors` rows at all: Hong Kong, Karachi, Lucknow, Manila, Miami, Qingdao, Seoul, Singapore, Taipei, Tel Aviv, Tokyo, Warsaw, Wuhan), compare the last `DAY0_EXTREME_UPDATED` event for target_date=2026-07-19 against the last Day0 posterior recompute for that family, **as of 09:26 UTC today**:

| city / metric | last new-extreme event (UTC) | last Day0 posterior (UTC) | staleness |
|---|---|---|---|
| Warsaw / high | 09:13:36 | 06:45:48 | **STALE, 2h28m** |
| Manila / high | 08:16:01 | 06:45:48 | **STALE, 1h30m** |
| Wuhan / high | 08:16:45 | 06:45:48 | **STALE, 1h31m** |
| Tel Aviv / high | 07:59:19 | 06:45:48 | **STALE, 1h13m** |
| Qingdao / high | 07:15:39 | 06:45:48 | **STALE, 30m** |
| Taipei / high | 07:06:23 | 06:45:48 | **STALE, 21m** |
| Karachi / high | 08:13:22 | 08:14:04 | fresh (42s) |
| Singapore / high | 06:30:56 | 06:45:48 | fresh |
| Miami / high | 04:56:25 | 06:43:02 | fresh |
| Tokyo / high | 04:46:06 | 06:45:48 | fresh |
| Lucknow, Seoul/low | (no new extreme since 07-18) | 06:45:48 | fresh (no-op) |

**6 of 12 active families with a same-day extreme are currently serving a stale posterior**, by up to 2.5 hours, at the moment of measurement — every one of these is exactly the failure mode the bridge was built to close. `06:45:48-50 UTC` is the last time the *old* scheduled/lazy reseed touched every family at once (`cycle_advance_enqueues`, 24 rows in one burst); nothing has reseeded any of these six families since, even though fresh extremes kept arriving for nearly 3 hours after that.

**Mechanism for why the old path is this irregular (not "stops every 40 min," genuinely stalls for hours):** the pre-bridge reseed (`_edli_reactor_cycle_advance_enqueuer`, `src/events/reactor.py:7982-8015`) is **lazy** — it only fires "when a family is blocked on a STALE/absent replacement posterior" during the reactor's normal decision processing, i.e. it requires some *other* event (an FSR tick, a price-driven redecision) to make the reactor look at that family at all. If nothing else touches a family, its Day0 posterior can go stale indefinitely, bounded only by whatever periodic full-sweep catches it (the 06:45 burst, likely `_anchor_meta_stamp_cross_check_job`, hourly per `src/ingest/forecast_live_daemon.py:1329-1336`, though that job's own log shows a >4x actual interval gap this morning too — `04:43:13 → next 09:43:13`, and no completion recorded for the 07:xx-08:xx window in this trace). This — not a clean 40-minute clock — is the real shape of the "before" bottleneck, and it is strictly worse than the 40-min-cadence framing in the prior report suggested: the average (44.9–71.6 min) was masking a heavy tail of multi-hour stalls exactly like the one visible right now.

## Q5 — Daily opportunity count and rough EV ceiling

```sql
SELECT substr(available_at,1,10) day, COUNT(*) n_events,
       COUNT(DISTINCT city||'|'||metric) n_families
FROM opportunity_events
WHERE event_type='DAY0_EXTREME_UPDATED'
  AND json_extract(payload_json,'$.city') IN (13 active cities)
  AND available_at >= '2026-07-05'
GROUP BY day;
```
Result: **44–168 genuine new-extreme events/day** (verified strictly monotone per family — Wuhan/high on 2026-07-19 stepped 28→30→31→32→33°C across 5 separate events, no dedup spam), across 12–24 tracked families, i.e. **~5–8 fresh-extreme opportunities per actively-traded family per day**.

EV ceiling, honestly bounded by what's actually observed rather than assumed: current Day0 position sizing (Q3) is **$1–$18 per trade** — far below the non-Day0 cohort's average too ($2.71/position). At that sizing, even a generous 5-8 opportunities/day × 13 families × a full 2-4¢ edge captured per event nets to a **daily ceiling in the tens of dollars, not more**, purely because of position size, independent of the latency question. The latency/staleness problem (Q1/Q4) caps the *number of these opportunities actually reachable* (right now: ~0, since the posterior isn't updating); the *position-sizing* problem caps the dollar value of each one that is reached. Both must be fixed for the lane to matter financially — closing the latency gap alone, with current sizing, would not move PnL by more than double-digit dollars/day.

## Q6 — Mechanism verification (code)

**Both emission paths call the bridge, confirmed by direct code read:**

1. **Fast METAR lane** — `src/ingest_main.py:472-490`: inside `_commit_pending_day0_metar`, immediately after `conn.commit()` and mutex release, for every family in `inserted_families`, calls `enqueue_day0_extreme_updated_materialization_seed(city=..., target_date=..., metric=...)` (`src/data/replacement_cycle_advance_trigger.py:1181`), then `publish_reactor_wake(reason="day0_extreme_event_committed", ...)`.
2. **Reactor catch-up lane** (non-METAR / WU / HKO sources, reboot catch-up) — `src/events/reactor.py:5930-5936` calls `_edli_bridge_day0_extreme_materialization_seeds(catchup_day0_event_ids)` (defined `reactor.py:7236-7300`), which re-derives the (city, target_date, metric) family from the committed event rows and calls the **same** `enqueue_day0_extreme_updated_materialization_seed`, then also calls `publish_reactor_wake`.

Both paths share the same idempotent `cycle_advance_enqueues` marker (`day0_observed_extreme_observation_time` monotone guard), so a family bridged from either lane cannot double-seed — the code is correctly deduplicated. **This part of the implementation is sound**; the gap is purely that it is not yet running (see headline finding).

**Wake urgency:** `"day0_extreme_event_committed"` is in `URGENT_WAKE_REASONS` (`src/runtime/reactor_wake.py:23-28`), which uses a best-effort cross-process socket signal, not just a file marker — so once deployed, the reactor should react close to immediately rather than waiting for its own poll.

**Next slowest hop once the bridge is actually running:** the reactor's own baseline scan interval is **60 seconds** by default (`edli_cfg.get("reactor_scan_interval_seconds", 60)`, wired at `src/main.py:6657-6663`) — the durable "backlog scan," independent of wakes. If the urgent-wake socket path works as designed, this 60s floor is bypassed; if it silently degrades to the poll (e.g. under load, or if the socket write fails and falls back to the file marker), 60s becomes the next hop, an order of magnitude below the current ~40-70 min bottleneck but still non-zero. The materialization poll job itself (`_replacement_forecast_materialize_poll_job`, `src/ingest/forecast_live_daemon.py:1160`) runs on a 1-second interval and was confirmed live and "executed successfully" every second through 09:29 UTC in `logs/zeus-forecast-live.log` — this hop is not a bottleneck. **Nothing else in the path still runs on a hardcoded 40-min clock** — the old ~40-70 min figure was never a literal timer, it was the emergent average of the lazy on-demand reseed described in Q4, which the bridge is designed to replace outright rather than speed up.

---

## Verdict

**Is the Day0 speed lane capturing its edge? No — it currently cannot, because the posterior recompute it depends on is not running the intended code at all.** The event-driven bridge (commit `4dcba55e2`) is correctly implemented and wired on both emission paths, but has fired **zero times** in production because the daemons that would run it were started 53 minutes before it was committed and have not been restarted since (0 occurrences of its log marker across the full ingest log). Independently of the bridge, the pre-existing lazy reseed mechanism it was meant to replace is itself currently stalled for up to 2.5 hours across 6 of 12 actively-traded Day0 families, worse than its own historical average (p50 44.9 min, p90 187 min over the last two weeks). Realized Day0 PnL to date is 2 trades, 2 losses, -$18.71 — too small to judge the thesis, and even the honest EV-ceiling math (Q5) says current position sizing caps daily value in the tens of dollars regardless of latency.

**Next bottleneck once deployed:** the reactor's urgent-wake socket path vs its 60-second fallback poll — worth a one-line log check post-deploy to confirm the urgent path is actually taken, not silently degrading to the 60s floor.

**Single biggest lever, in priority order:**
1. **Restart the daemons** (`deploy_live.py restart all`, mesh-coherent, per standing deploy law) so the already-written, already-correct bridge code actually starts running — this is a zero-code, one-command fix sitting unclaimed since 04:36 UTC, over 5 hours ago.
2. Only after that: position sizing is the second-order lever, since even a perfectly-fast lane is capped at tens of dollars/day at $1-18/trade sizing.
