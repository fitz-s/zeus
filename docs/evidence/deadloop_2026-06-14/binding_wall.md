# Binding Wall — Dead Decision Loop 2026-06-14

**Provenance**: daemon pid=54550, HEAD=ee1604440c, traced 2026-06-14 ~17:25 UTC  
**DBs**: state/zeus-world.db, state/zeus-forecasts.db, state/zeus_trades.db

---

## Classification: W-DECISION (honest edge rejection, not plumbing failure)

The single binding wall for covered June 15 high-metric families is:

> **`capital_efficiency_lcb_ev` rejects every candidate on every cycle — q_lcb is real but ev_per_dollar is consistently negative or below threshold.**

The plumbing (snapshot gate, refresher, topology) is functioning. The wall is the decision engine's economic verdict.

---

## End-to-End Trace: London high 2026-06-15

### 1. Topology coverage confirmed

`state/zeus-forecasts.db`.`market_events`: 11 rows for `city=London, target_date=2026-06-15, temperature_metric=high`.  
Condition IDs present, none NULL. Gate passes at `_event_family_market_topology_rows`.

### 2. Executable snapshots — FRESH

`state/zeus_trades.db`.`executable_market_snapshots` for London high 06-15:
```
('0x696346b24cc70f919969541e955f661a7277d6c229560f54781392066ca8f7a7',
 'highest-temperature-in-london-on-june-15-2026',
 '2026-06-14T17:07:30.473665+00:00',   # captured_at
 '2026-06-14T17:08:00.473665+00:00',   # freshness_deadline = captured_at + 30s
 active=1, closed=0, accepting_orders=1)
```
Gate simulation at 17:12 UTC: `_latest_snapshot_rows_for_event_family` returns 5 rows. Gate passes.

### 3. Decision engine rejection — W-DECISION

From cycle log at **2026-06-14 12:00:31** (17:00:31 UTC):
```
EDLI reactor cycle result: processed=11 proof_accepted=0 rejected=11 retried=58
reasons=[
  'EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=18 other=4;
   best=Will the highest temperature in Beijing be 34°C on June 15?
   buy_yes q_lcb=0.0588 price=0.0040 ev_per_dollar=13.7059',
  ...
  'EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=19 other=3;
   best=Will the highest temperature in Shanghai be 27°C on June 15?
   buy_yes q_lcb=0.0784 price=0.0900 ev_per_dollar=-0.1285',
]
```

At **2026-06-14 11:50:46** (Singapore June 15):
```
EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=19 other=3;
best=Will the highest temperature in Singapore be 31°C on June 15?
buy_yes q_lcb=0.1295 price=0.4600 ev_per_dollar=-0.7184
```

Pattern: `capital_efficiency_lcb_ev` accounts for 17-22 of 22 candidates being rejected on the best cycle. The "best" surviving candidate (after capital_efficiency) typically has negative ev_per_dollar or direction_law block.

### 4. q_lcb values — real but thin edge

From cycle reasons across recent cycles:
- Singapore high 06-15 best: `q_lcb=0.1295 price=0.4600 ev_per_dollar=-0.72` (buy_yes)
- Beijing high 06-15 best: `q_lcb=0.0588 price=0.0040 ev_per_dollar=13.71` (buy_yes, but direction_law=2 blocks)
- Seoul low 06-15 best: `q_lcb=0.0326 price=0.3200 ev_per_dollar=-0.90`
- Shanghai high 06-15 best: `q_lcb=0.0784-0.0980 price=0.08-0.09 ev_per_dollar=-0.13 to +0.23`

The q_lcb values are genuine (not a q=0 collapse). The problem is not broken q_lcb computation — it is that q_lcb is low (0.03-0.20) against market prices that have already absorbed the forecast edge, leaving ev_per_dollar at or below the capital_efficiency threshold.

---

## Secondary Patterns (Not the Binding Wall)

### A. EXECUTABLE_SNAPSHOT_BLOCKED (Auckland, Wellington — 22/17 attempts)

Auckland/Wellington June 15 high have **zero rows** in `executable_market_snapshots`. The decision-triggered refresher at `main.py:7266` calls `reconstruct_weather_market_from_static_topology` which returns None (no prior snapshots → token map reconstruction fails at line 3572: `if timing_snapshot is None: return None`). Refresher returns False silently. These cities spin on EXECUTABLE_SNAPSHOT_BLOCKED.

**Root cause**: The warm job's Gamma slug fetch is needed for cities with zero snapshot history but the Gamma path is budget/time-box constrained and these cities are not getting captured in the per-cycle budget.

Log evidence (Auckland `edli_evt_22ac0b01...`):
```
2026-06-14 09:51:29,647 [zeus.events.reactor] INFO: reactor: money-path transient requeued
  event_id=edli_evt_22ac0b01b091396f578fb564902178e9c0af68ff...
  count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
```
(Repeats every ~2 min since 09:25 UTC, 22 attempts total.)

### B. EXECUTABLE_SNAPSHOT_STALE (Ankara/Chicago June 14 — market_close_at passed)

Ankara/Chicago high 06-14 markets closed at `market_close_at=2026-06-14T12:00:00+00:00` (5+ hours ago). Their snapshots were last captured at 11:52-11:57 UTC with 30s freshness. The warm job correctly skips them as `venue_closed_skipped=308`. The decision-triggered refresher fires (CLOB fetches visible in log adjacent to STALE requeues at 11:05:27) but the returned snapshot is still stale because `freshness_deadline` in the re-elected row predates `decision_time`. The `fresh_at=None` fix at `event_reactor_adapter.py:2304-2321` (commit ee1604440c) was intended to fix this but these particular families are genuinely closed venues — no fresh snapshot is possible because the warm job's `venue_closed_skipped` gate correctly excludes them.

These STALE events should terminalize (market expired) rather than requeue indefinitely.

### C. FSR events for June 15 (0 attempts) — not yet scheduled

168 `FORECAST_SNAPSHOT_READY` events for June 14-16 are pending with 0 attempts. London/Singapore June 15 FSR events were emitted at 15:59-17:24 UTC and haven't been claimed yet by `edli_reactor_v1`. This is normal — the reactor cycles and will pick them up. However if they are also rejected by `capital_efficiency_lcb_ev`, they join the rejection stream.

### D. Low-metric families (June 15 low) — honest topology gap

Only 8 cities have `temperature_metric=low` in `market_events` for June 15. Their EXECUTABLE_SNAPSHOT_BLOCKED rejections are honest — Polymarket lists min-temp for few cities only. Not a plumbing issue.

---

## Warm Job Coverage Gap

From the `refresh_pending_family_snapshots` summary at **12:23:36**:
```
venue_closed_skipped=308   # closed/expired markets — correct behavior
budget_truncated_city_count=21   # 21 cities not refreshed due to budget
uncaptured_candidate_city_count=13   # 13 cities without fresh snapshots
fresh_executable_city_count=24   # only 24 of 37 candidate cities refreshed
executable_substrate_coverage_status=PARTIAL
```

Of 49 topology-covered June 15 high cities: **32 are stale, 17 are fresh** at time of probe. The 30s freshness window and ~2-min warm cycle mean a city is fresh only ~25% of wall-clock time. The decision-triggered refresher is supposed to bridge this gap but it requires prior snapshots to reconstruct the token map.

---

## Single Highest-Value Fix

**The binding wall is W-DECISION (honest)**: covered June 15 families ARE reaching the decision engine (proof_accepted=0, rejected with real q_lcb values). The fix is NOT a plumbing fix.

**What's actually happening**: `q_lcb` for June 15 tomorrow markets is genuine but thin (0.03-0.20), and `ev_per_dollar = (q_lcb / price - 1)` is negative or below threshold because prices have already absorbed the prior forecast. This is correct no-edge behavior, not a broken gate.

**Secondary fix with real value** — terminalize genuinely-closed-venue STALE events:  
In the reactor's STALE requeue path, check if `market_close_at <= decision_time` on the stale snapshot row. If so, emit a terminal no-trade receipt instead of requeueing — this would eliminate the 308 `venue_closed_skipped` phantom churn and clean up the retry queue.

File: `src/engine/event_reactor_adapter.py` near line 2246 (`_snapshot_price_stale_reason`).  
Add: if `row.get("market_close_at")` is not None and parsed market_close_at <= decision_time, return a distinct reason like `MARKET_CLOSED_PAST_RESOLUTION` instead of `EXECUTABLE_SNAPSHOT_STALE` so the reactor can terminalize rather than requeue.

**For no_trade_event terminalization of low-metric families**: the no_trade_events table records the no-trade but the low-metric families keep re-entering the pending queue each cycle because `opportunity_event_processing` doesn't terminalize them — they stay `pending` until horizon. No immediate fix needed but worth noting.

---

## Discriminating Probe

To confirm whether the W-DECISION diagnosis is complete or whether there is a hidden q_lcb≈0 collapse masked by the surface `capital_efficiency_lcb_ev` label:

Run:
```sql
-- In state/zeus_trades.db or state/zeus-forecasts.db
SELECT * FROM decision_log
WHERE created_at > datetime('now', '-2 hours')
ORDER BY created_at DESC LIMIT 20;
```
If `q_lcb` values in decision_log are all 0.00 (despite the log showing 0.03-0.20), then there is a q_lcb computation bug. If they match the log, the rejection is honest.

Also check `replacement_q_market_anchor_enabled` / `q_lcb_settlement_coverage_gate_enabled` flags in settings or DB — if either gate is enabled and misconfigured, it could collapse q_lcb to zero AFTER the surface log is written.
