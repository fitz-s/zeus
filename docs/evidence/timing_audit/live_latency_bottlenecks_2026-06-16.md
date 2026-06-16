# Live Latency Bottleneck Audit — 2026-06-16

Evidence-driven, read-only query against live DBs (zeus-world.db, zeus-forecasts.db, zeus_trades.db).
Data window: 2026-06-01 to 2026-06-16 where available.

---

## Executive Summary — Ranked Bottlenecks

| Rank | Bottleneck | Measured Lag | Avoidable? |
|------|-----------|-------------|-----------|
| 1 | **Openmeteo data publish delay** (~15h after ECMWF cycle time) | 8–20h avg 9–10h | EXTERNAL — openmeteo re-processes ECMWF IFS9 data; we cannot pull it earlier |
| 2 | **Posterior queue stalls** (60 gaps >30min since Jun 7; 57% of events expire) | up to 800min; 60+ gaps >30min | AVOIDABLE — stall pattern is irregular, not cadence-driven |
| 3 | **ECMWF open-data publish lag** (ecmwf_open_data arrives ~8.3h after 00Z/12Z cycle) | 8.1–8.5h | EXTERNAL — ECMWF dissemination schedule |
| 4 | **Eval cycle cadence gaps** (reactor fires ~60s normally but gaps reach 2h+) | p50=60s, p90=120s, long tail >3600s | AVOIDABLE — p99/max gaps indicate stalls |
| 5 | **High submit-rejection rate** (44–100% of venue submits rejected on many days) | ~60–70% rejected overall | AVOIDABLE — mostly price/stale-quote rejections |
| 6 | **Cold gamma/CLOB cache misses** | no direct telemetry found in DBs | Likely AVOIDABLE but unobservable from DB alone |

---

## 1. ECMWF Data Publish Lag (External)

**Source:** `source_run` in zeus-forecasts.db (n=64 SUCCESS since Jun 1)

ECMWF open data (ecmwf_open_data) publishes approximately **8.1–8.5h** after cycle time:
- 00Z cycle → data available ~08:16–08:26 UTC (lag 8.3–8.4h)
- 12Z cycle → data available ~20:08–20:24 UTC (lag 8.1–8.4h)

Fetch job duration (fetch_started_at → fetch_finished_at): **recorded as 0s** — timestamps are identical, suggesting the fetch timestamp is recorded at submission, not completion, or the fetch is near-instantaneous (pre-cached data).

Forecast job (job_run) duration:
- `mn2t6_low`: avg=245s, min=22s, max=667s (n=28)
- `mx2t6_high`: avg=129s, min=24s, max=507s (n=28)

**Verdict: EXTERNAL.** The 8h+ wait is ECMWF's dissemination schedule. Not avoidable.

---

## 2. Openmeteo Data Publish Delay (External — dominant bottleneck for posteriors)

**Source:** `forecast_posteriors.source_available_at` in zeus-forecasts.db (n=5977 since Jun 7)

All forecast posteriors use `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` as source (not ecmwf_open_data directly). This source publishes substantially later:

| Cycle hour | Avg avail lag | Min | Max |
|-----------|--------------|-----|-----|
| 00Z | 9.6h | 2.0h | 20.2h |
| 06Z | 10.3h | 6.0h | 19.2h |
| 12Z | 10.2h | 6.0h | 16.3h |
| 18Z | 8.3h | 5.9h | 16.4h |

Once openmeteo data is available, the first posterior is computed within **3–51 minutes** (median ~10min, well-behaved). So:
- **The 140min median posterior latency (source_available_at → computed_at) measured previously is misleading**: source_available_at in forecast_posteriors is set to openmeteo availability, not ecmwf availability. After openmeteo publishes, first posteriors arrive in <1h.

**The real bottleneck:** cycle_time → openmeteo availability = **8–20h** (avg ~10h). This is the structural delay before any posterior can be computed for a given ECMWF cycle.

**Verdict: EXTERNAL.** Openmeteo re-processes IFS9 data; we receive it when they publish it. The variance (2h–20h) is on their side.

---

## 3. Posterior Queue Stalls (Avoidable)

**Source:** `forecast_posteriors.computed_at` timestamps, Jun 7–Jun 16 (n=5988)

While steady-state throughput is ~0.74 posteriors/min (≈10/min in recent bursts), there are **60 gaps >30 minutes** and **423 gaps >5 minutes** in the computed_at stream:

Selected large gaps:
- 2026-06-08: 737min gap (12h stall)
- 2026-06-09: 800min gap (~13h stall)
- 2026-06-07: 510min gap (~8.5h stall)
- 2026-06-11: 460min gap (~7.7h stall)
- 2026-06-11: 354min gap (~5.9h stall)

Stalls are irregular (not at fixed offsets from cycle times) — not explainable by ECMWF schedule alone. Pattern is most severe Jun 7–11 and has improved since Jun 13 (recent gaps are smaller).

**FORECAST_SNAPSHOT_READY event processing (all time):**
- Expired: 540,047 (65% of FORECAST_SNAPSHOT_READY events expire before processing)
- Processed: 284,790 (34%)
- Dead-letter: 14,038 (1.7%)
- Average processing lag for expired events: 4045s (~67min wait then expire)
- Average processing lag for processed events: 1054s (~17min)

This high expiry rate (65%) means that for most forecast snapshots, the reactor evaluated them too late — the event had already expired before the consumer got to it.

**Overall opportunity_event_processing breakdown (all event types):**
- Expired: 4,050,871 (57.7%)
- Ignored: 2,096,200 (29.9%)
- Dead-letter: 564,484 (8.0%)
- Processed: 305,482 (4.4%)

**Verdict: AVOIDABLE.** The stalls and high expiry rate suggest the reactor (or posterior computation worker) is not keeping up. Stalls up to 13h indicate process restarts or hangs, not normal backpressure.

---

## 4. Eval Cycle Cadence (Partially Avoidable)

**Source:** `decision_certificates` (claim_type='belief', mode='NO_SUBMIT') in zeus-world.db (n=61,839 Jun 1+)

Normal cadence: **p50=60s, p90=120s**

Long-tail gaps in the distribution (Jun 1+):
- `<30s`: 0% — minimum gap is 37s
- `30–60s`: 44%
- `60–120s`: 45%
- `120–300s`: 6.4%
- `300–600s`: 2.1%
- `>600s`: 1.9% (n≈90 events with gaps >10min)
- p99: 1087s (~18min)
- max: 3581s (~60min)

The 1.9% of gaps >10min represent genuine reactor stalls (process pause, not normal scheduling jitter).

**Verdict:** Normal cadence (60s cycle) is EXTERNAL/by-design. Long-tail gaps (>300s) are AVOIDABLE — they indicate process hangs or restart latency.

---

## 5. Submit Rejection Rate (Avoidable)

**Source:** `edli_live_order_events` in zeus-world.db (Jun 1+)

| Day | Attempted | Acked | Rejected | Unknown | Filled | Ack Rate |
|-----|-----------|-------|----------|---------|--------|---------|
| Jun 16 | 17 | 8 | 5 | 4 | 2 | 47% |
| Jun 15 | 9 | 2 | 6 | 1 | 1 | 22% |
| Jun 12 | 29 | 5 | 15 | 9 | 12 | 17% |
| Jun 11 | 31 | 1 | 30 | 0 | 1 | 3% |
| Jun 10 | 23 | 7 | 16 | 1 | 5 | 30% |
| Jun 7 | 40 | 10 | 28 | 2 | 7 | 25% |
| Jun 6 | 39 | 13 | 22 | 5 | 12 | 33% |
| Jun 1 | 45 | 0 | 44 | 1 | 0 | 0% |

Overall: **~60–70% of venue submit attempts are rejected** on most days (Jun 11 was 97% rejected). Jun 16 shows improvement (47% acked).

Rejection payloads (`SubmitRejected`) contain `raw_response_hash=None` consistently — the rejection happens before a venue response is received, implying pre-submission validation failure (stale quote, price moved, size constraint).

`UserTradeObserved` count (actual fills confirmed via trade events): ranges from 0–12/day, with 40 total fills across the Jun 1–16 period out of 239 total attempts (17% actual fill rate).

**Verdict: AVOIDABLE.** High rejection rate wastes order slots and may reflect stale CLOB prices at decision time. The Jun 1 (0% ack, 98% reject) and Jun 11 (3% ack) outliers suggest systematic stale-book conditions.

---

## 6. Cold Gamma / CLOB Cache Problem (Insufficient DB Telemetry)

**What was sought:** `bp_evaluated`, cycle evaluation budget exhaustion, gamma/CLOB timeout telemetry.

**What was found:**
- `decision_log` in zeus_trades.db: 8,522 entries since Jun 1 — but all are `chain_sync_monitor` (not cycle_runner). No cycle_runner decision artifacts found in any DB.
- `no_trade_events`: only populated May 20–28 (world DB); empty in forecasts DB for Jun 1+.
- `opportunity_events`: 6.3M BEST_BID_ASK_CHANGED events since Jun 1 (polymarket_market_channel), confirming CLOB stream is active.
- No direct telemetry for ZEUS_DISCOVERY_GAMMA_TIMEOUT or per-cycle `bp_evaluated` count was found in any DB table.

**What can be inferred:** The absence of `no_trade_events` since May 28 does not mean no no-trade decisions — the table appears to have stopped being written. The opportunity_event_processing expired rate (65% for FORECAST_SNAPSHOT_READY) indirectly suggests the reactor is not catching all windows. Direct cycle_runner timing (gamma fetch duration, CLOB fetch per market) is logged to files, not to DB.

**Verdict:** Cannot confirm or deny from DB evidence. Needs log file analysis (cycle_runner stderr/stdout).

---

## Timing Flow Summary (Per Cycle, Steady-State)

```
ECMWF cycle fires (00Z or 12Z)
  └─ +8.1–8.5h: ecmwf_open_data available (EXTERNAL)
  └─ +8–20h (avg ~10h): openmeteo_ecmwf_ifs9 data available (EXTERNAL)
       └─ +6–51min: first forecast_posterior computed (avoidable via throughput)
       └─ +span 200–700min: last posterior for this cycle computed (stall-driven)
            └─ FORECAST_SNAPSHOT_READY event fired
                 └─ 65% expire before reactor consumes them
                 └─ 35% processed avg 17min after creation
                      └─ Reactor evaluation cycle: p50=60s
                           └─ Decision → VenueSubmitAttempted: sub-second (same timestamp resolution)
                                └─ ~60–70% rejected pre-venue; ~30–40% acked
                                     └─ ~17% become actual UserTradeObserved fills
```

---

## Avoidable Latency Targets

| Item | Measured loss | Fix direction |
|------|-------------|--------------|
| Posterior stall gaps (>30min) | 60 stall events; worst 800min | Investigate posterior worker restart/hang cause |
| FORECAST_SNAPSHOT_READY expiry (65%) | Most forecast events not acted on | Increase expiry window or prioritize processing |
| Eval cycle long-tail gaps (>300s, 1.9%) | Process restarts; 60min max stall | Heartbeat monitoring / watchdog for reactor |
| Submit rejection rate (60–97%) | Most LIVE orders never fill | Pre-submit CLOB freshness check; staleness gate |

## Data Limitations

- `source_run` fetch timestamps show fetch_start = fetch_end (0s), suggesting timestamp granularity or recording artifact — actual fetch duration for ecmwf_open_data is not measurable from DB alone.
- `no_trade_events` stopped being written after May 28; no no-trade reason telemetry available for Jun 1+.
- Gamma/CLOB timeout telemetry (ZEUS_DISCOVERY_GAMMA_TIMEOUT, ZEUS_DISCOVERY_CLOB_TIMEOUT) is not recorded in any DB table; needs log-file analysis.
- `decision_certificates` mode='LIVE' has 446 entries total — all 8 certificate types per trade, so only 446/8 = ~56 actual LIVE trade decisions since Jun 1.
- Submit timing resolution: `occurred_at` in `edli_live_order_events` is recorded at decision_time granularity (all events in a batch share the same timestamp), so sub-second submit→ack latency cannot be measured.
