# Day0 obs→submit latency chain — measured hop by hop

**Date:** 2026-07-18 · **Mode:** READ-ONLY (all DBs opened `mode=ro`; no source edited)
**DBs:** `state/zeus-world.db` (obs prints, DAY0 events, durable obs), `state/zeus-forecasts.db` (posteriors), `state/zeus_trades.db` (submits, token prices, book snapshots)

## Operator question
"Day0 is a zero-sum race: the settlement source refreshes the running extreme every few tens of
minutes; we must compute the probability and execute FASTER than the market reprices." → measure the
actual end-to-end latency chain, hop by hop, and name the slowest hop with the number that proves it.

## Chain and the real timestamp columns used
| # | Hop | Table (DB) | Latency measured |
|---|-----|-----------|------------------|
| 0 | Source publishes → Zeus fetches print | `observation_prints` (world) | `fetched_at_utc − publish_ts_utc` |
| 1a | Obs hour → Zeus has print in hand | `opportunity_events` DAY0_EXTREME_UPDATED (world) | `available_at − observed_at` |
| 1b | Print in hand → DAY0 event persisted | `opportunity_events` (world) | `received_at − available_at` |
| 2raw | Obs valid hour → probability | `forecast_posteriors` Day0 (forecasts) | `computed_at − day0_conditioning.observation_time` |
| 2iso | Print in hand → probability (isolated) | posteriors ⋈ DAY0 events | `computed_at − available_at` |
| 2b | Probability refresh cadence | `forecast_posteriors` Day0 (forecasts) | consecutive `computed_at` gaps per (city,date,metric) |
| 3 | Probability → order submitted | `venue_commands` (trades) ⋈ posteriors | `created_at − nearest preceding Day0 computed_at` |
| E1 | Book-capture cadence (measurement floor) | `executable_market_snapshots` (trades) | consecutive `captured_at` gaps per condition_id |
| E2 | Obs in hand → book top-of-book moves | `executable_market_snapshots` (trades) | first ≥0.02 mid move after `available_at` |

Corrections to the brief's table pointers (K1 DB split): `observation_instants` / `observation_prints` /
DAY0 `opportunity_events` live in **zeus-world.db** (the copies in zeus-forecasts.db are empty shadows);
`token_price_log` and `executable_market_snapshots` live in **zeus_trades.db**.
`market_microstructure_snapshots` is **empty** (0 rows) and forecasts `market_price_history` stops
2026-05-28 — so the live book-reprice hop was measured off `executable_market_snapshots` instead.

---

## Ranked hop table (slowest first, by p50)

| Rank | Hop | n | p50 | p90 | p99 | Window | On binding decision path? |
|------|-----|---|-----|-----|-----|--------|---------------------------|
| 1 | **2b Probability refresh cadence** | 9,362 | **39.9 min** | 90.0 min | 210 min | 07-11→07-18 | **YES — dominant** |
| 2 | 2raw Obs-hour → probability | 9,802 | 47.1 min | 102.7 min | 311 min | 07-11→07-18 | YES (= fetch + cadence) |
| 3 | 0 wu_icao_history fetch (settlement lane) | 3,914 | 45.4 min | 128.7 min | 312 min | 07-11→07-18 | NO — bypassed by fast lane |
| 4 | 3 Posterior staleness at submit | 53 | 36.8 min | 155.8 min | 239 min | 07-01→07-17 | YES (cadence-bound) |
| 5 | E2 Book reprice (Zeus-observed) | 954 | 10.6 min | 163.8 min | 648 min | 07-16→07-18 | competitor clock |
| 6 | 2iso Print-in-hand → probability | 2,151 | 9.9 min | 46.5 min | 58.7 min | 07-11→07-18 | YES (phase-wait for tick) |
| 7 | 1a Obs hour → print in hand (event) | 3,523 | 5.5 min | 55.5 min | 107 min | 07-11→07-18 | YES |
| 8 | 1b Zeus event queue (in-hand→event) | 3,249 | 0.95 min | 14.6 min | 53.1 min | 07-11→07-18 | YES (heavy tail) |
| — | 0 aviationweather_metar fetch (fast lane) | 6,859 | 0.9 min | 190 min | 326 min | 07-11→07-18 | fast lane — <1 min p50 |
| — | E1 Book-capture cadence (floor) | 135,778 | ~0 (burst) | 31.7 min | 53.8 min | 07-16→07-18 | measurement floor |

---

## Verdict — the slowest hop is the probability recompute cadence, not fetch or execution

**The bottleneck is the ~40-minute posterior recompute cadence (HOP 2b, p50 39.9 min, p90 90 min).**
A fresh Day0 observation does not trigger an immediate recompute; each (city, date, metric) family
recomputes on a ~40-min cadence, so a new extreme waits on average ~20 min (up to 90 min p90) before
the probability reflects it. This single hop dominates the whole chain and shows up twice: as the raw
obs-hour→probability latency (2raw p50 47 min) and as the posterior age at submit time (HOP 3 p50 37 min).

**The fetch and event hops are NOT the bottleneck.** Zeus already triggers the DAY0 event off a fast
lane — the DAY0 event fires when the print is only ~5.5 min old (1a p50), and aviationweather METAR is
fetched in <1 min p50 (HOP 0 fast lane). The settlement-authority mirror `wu_icao_history` is slow
(45 min p50), but it is a confirmation lane, not the decision trigger. Zeus's own internal queue (1b)
is fast at median (57 s) but has a **heavy tail** (p90 14.6 min, p99 53 min) worth watching.

**Does Zeus beat the book? — Not decisively; roughly tied, and cannot be proven cleanly.**
The market's top-of-book, as Zeus can observe it, moves materially within ~10.6 min p50 of the same obs
(E2). Zeus's probability refresh is ~40 min cadence-bound — i.e. **the same order as, or slower than,
the reprice window.** So the measured chain shows Zeus does *not* hold a decisive speed edge over the
book; closing the ~40-min recompute cadence is the highest-leverage optimization. The exact
"fraction of killed-bin events where Zeus acts before the book" could **not** be computed robustly
(see coverage gaps).

---

## Honesty about coverage and hops not cleanly measured

- **2raw vs 2iso disagree** on the obs-in-hand→probability figure (47 min vs 10 min). They use different
  obs-time definitions (2raw = obs valid hour; 2iso = fast-lane `available_at`) and 2iso matched only
  2,151/9,502 Day0 posteriors (hourly-floor join). The recompute cadence (2b, 40 min) is the robust,
  join-free anchor and is the number to trust for the headline.
- **HOP 3 is confounded.** It measures *posterior staleness at submit* (cadence-bound), not a pipeline
  compute delay — a submit uses the latest posterior, which is on average ~half a recompute-cadence old.
  Sample is also thin: submits are sparse and **stop 2026-07-17** (venue_commands has no 07-18 rows;
  entries appear paused), only 53/432 submits joined to a Day0 posterior. Treat as directional.
- **HOP E is floor-limited.** `executable_market_snapshots` is Zeus's *own* book capture at a ~2–30 min
  cadence (E1: p90 31.7 min), with sub-second bursts around decisions. E2's 10.6 min p50 is therefore an
  **upper bound** on true reprice speed — the book may move faster than Zeus samples it. `market_microstructure_snapshots`
  (the intended reprice table) is empty, so a finer floor was unavailable.
- **Backfill contamination** in HOP 0/2raw tails was bounded by dropping deltas ≥6 h; percentiles are
  robust but the extreme max values reflect catch-up/backfill fetches, not live decision latency.
- Windows differ by hop (noted per row) because lanes have different live spans: the submit lane is the
  sparsest and required a wider 07-01→07-17 window; obs/event/posterior lanes are dense over 07-11→07-18.

## Reproduction
Scripts (read-only): `scratchpad/measure_latency.py` (HOP 0/1/2/2b), `scratchpad/measure_part2.py`
(HOP 3/E). Percentiles computed in-process (linear interpolation) over per-row deltas; timestamps
normalized to epoch-UTC.
