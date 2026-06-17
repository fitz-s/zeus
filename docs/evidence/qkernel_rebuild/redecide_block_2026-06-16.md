# Redecide-block diagnosis: CANCEL_CONFIRMED day-ahead harvest families not re-deciding

```
# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: live evidence (state/zeus_trades.db, state/zeus-world.db, logs/zeus-live.log @ 02:00-03:00 CDT / 07:00-08:00 UTC 2026-06-16); running sha bef3671835 / HEAD 6dcfe36697
# Scope: ONE blocker (re-decision throughput); diagnose-only, no live edits
```

## VERDICT (6 lines)
1. **H-A REFUTED.** The families ARE re-emitted: Moscow|2026-06-17 and Singapore|2026-06-17 FORECAST_SNAPSHOT_READY events fire every reactor cycle, latest 07:40:58 UTC (the re-emission organ emits FSR, NOT EDLI_REDECISION_PENDING — that type has 0 rows ever, by design).
2. **H-B PARTIAL/secondary.** Both families' executable snapshots ARE fresh when sampled (captured 07:40-07:41 UTC, accepting_orders=1). The #122 oscillation is REAL (`fresh_executable_city_count` 0↔21) and caused by `database is locked` in the 20s warm-cycle — but it is a COMPOUNDING throttle, not the wall for these two families right now.
3. **H-C is the ROOT (throughput starvation, not budget-cap).** The reactor decides ~1 family/cycle (modal `processed=1`), the per-(tier,city) round-robin in `fetch_pending` is **49 cities deep**, and the cycle runs every ~2.5-3 min (60s schedule coalesced because cycle wall-time >> 60s). Full rotation ≈ 49 cycles ≈ **2-3 HOURS**; a given family gets one decision turn that rarely.
4. **decision_log has ZERO rows since the 07:13 cancel** — no family of any city has been fully decided in the 45+ min since cancel. The two families' FSR events EXPIRE (538k expired total) with attempt_count=0 before their rotation slot comes up.
5. The cross IS armed: `_family_rest_state` returns escalated_after_rest=True for both (CANCEL_CONFIRMED matched=0 within 24h). The only missing step is a timely re-decision turn.
6. **MINIMAL FIX:** have the escalation cancel job emit a high-tier, family-targeted re-decision event for the just-cancelled family so it bypasses the 49-deep per-city round-robin. File:line + exact change in §Fix below.

---

## 1. The two families (state/zeus_trades.db)

| market | city / date | order (trunc) | command | command.state | latest fact | matched | cancelled at |
|---|---|---|---|---|---|---|---|
| 2549630 | Moscow / 2026-06-17 | 0x4101ade364 | 7f383fb8dc4440a4 | EXPIRED | CANCEL_CONFIRMED | 0 | 07:13:35Z |
| 2549521 | Singapore / 2026-06-17 | 0xcc74a00176 | 79a038a233f44f6a | EXPIRED | CANCEL_CONFIRMED | 0 | 07:13:35Z |

Both are `intent_kind=ENTRY`, `side=BUY` (buy_no), unfilled. `event_slug` (executable_market_snapshots) = `highest-temperature-in-{moscow,singapore}-on-june-17-2026`, `market_end_at=2026-06-17T12:00:00Z`. **No new venue_commands for either market since the cancel** — only the original EXPIRED commands exist. That is the symptom: not re-decided.

The escalation job itself is healthy and looping: at 02:38:35 CDT it cancelled a DIFFERENT expired rest (command `7482169d914642d3`), logging *"the next certified decision for this family may cross as TAKER_ESCALATED_AFTER_REST"* — but that "next certified decision" never arrives in time, so the family re-rests later and the escalation re-cancels: a cancel→re-rest→cancel loop that never crosses.

## 2. H-A REFUTED — they ARE re-emitted (state/zeus-world.db)

`opportunity_events` entity_key is multi-line (`Moscow\n2026-06-17\nhigh\n<source>`), so a `|`-separated filter misses it. Raw FSR rows for these families fire every ~2-3 min, continuously across the 07:13 cancel:

```
Moscow|2026-06-17|high|...  created 07:40:58, 07:37:58, 07:34:58, 07:32:58, ... 07:15:58 (every cycle)
Singapore|2026-06-17|high|... created 07:40:58, 07:37:58, ... 07:15:58 (every cycle)
```

The re-emission organ (`src/main.py:5839-5871`) calls `_edli_emit_forecast_snapshot_events` → `ForecastSnapshotReadyTrigger.scan_committed_snapshots` with a per-cycle distinct `source` (`cycle-N`) so each cycle writes a fresh FSR (different idempotency_key). **EDLI_REDECISION_PENDING is NOT used in production** (COUNT=0 ever); the resurrection rides FSR re-emit. So "0 EDLI_REDECISION_PENDING" is expected and is NOT evidence of a dark organ. **The re-emission organ is working.**

`edli_redecision: enqueued=0 batch=60 skipped_pending=114` every cycle: the continuous-redecision *second* emit adds 0 because all candidate families already have a pending FSR (`already_pending_keys` skip). The FSR re-emit (first emit) is what keeps these alive.

## 3. The actual wall — re-emitted events EXPIRE unclaimed (H-C)

Processing status of Moscow/Singapore June-17 FSR events created since 07:13:

```
Moscow|2026-06-17    expired (attempt_count=0) x8,  pending x2
Singapore|2026-06-17 expired (attempt_count=0) x7,  pending x2,  processed x1 (07:18:49, attempt=1)
```

- **attempt_count=0 on every `expired` row** → the reactor NEVER claimed them; they sat `pending` until `expires_at` passed and the working-set prune marked them `expired`.
- Only **1** Singapore event (07:18) and **0** Moscow events were processed in the whole window.
- Global FSR processing table: `pending=170, processed=284758, expired=538198, dead_letter=14038`. So the reactor IS draining FSR — just far too slowly relative to the re-emit/expire rate.

### Why so slow — the throughput math (decisive)
- **Reactor decides ~1 family/cycle.** `EDLI reactor cycle result` modal = `processed=1` (17 cycles) / `processed=0` (15) / `processed=2` (14); `proof_accepted` almost always 0 (the few decisions are day-of June-16 longshots being rejected: `TRADE_SCORE_NON_POSITIVE`, `EVENT_BOUND_ALL_CANDIDATES_REJECTED ... q_lcb=0.0000 price=0.0010`).
- **Per-cycle budget = 30s** (`DEFAULT_REACTOR_CYCLE_BUDGET_SECONDS`, `src/events/reactor.py:61`), with a PRE-event check (`reactor.py:694`) that returns the instant the budget is spent. A single slow family decision (comment cites live p99=59s, max=460s for a 22-candidate family) exhausts the budget → 1 decision/cycle.
- **`fetch_pending` per-(tier,city) round-robin is 49 cities deep** (confirmed: 49 distinct pending cities, each 3-6 events). The outer sort is `_claim_tier ASC, _city_round ASC, priority DESC` (`src/events/event_store.py:254-260`): one event per city before any city's second event. A budget of K≈1 reaches K distinct cities/cycle → full rotation ≈ ceil(49/1) = 49 cycles.
- **Cycle real cadence ≈ 2.5-3 min, not 60s.** Reactor is scheduled `interval[0:01:00]` but APScheduler coalesces because the cycle wall-time blows past 60s (e.g. job start 07:43:52 → next run 07:56:52 = **13 min gap**). The overrun is the end-of-cycle `_drain_substrate_refreshes` network I/O plus world/trade-DB lock contention.

⇒ **A given city's family gets ONE decision turn roughly every 49 cycles × ~3 min ≈ 2-3 hours.** Moscow/Singapore are 2 of those 49 cities. The cross window opened at 07:13; by 07:58 neither had come up again (decision_log empty since 07:13). That is the block.

### `priority` cannot rescue it
`priority DESC` is SECONDARY to `_city_round ASC` (`event_store.py:259` before `:260`; authority comment `src/events/event_priority.py:45-48`). Bumping an escalated family's FSR priority only reorders WITHIN its city's round — it still waits for `_city_round=1` in the 49-deep rotation. So a priority-stamp fix is INEFFECTIVE here.

## 4. H-B — #122 snapshot oscillation (real, secondary, compounding)

The market-substrate warm cycle (`refresh_pending_family_snapshots`, every 20s) is in a `database is locked` failure spiral on `zeus_trades.db`:

```
... 'attempted':1-2, 'inserted':0, 'failed':1-2, 'fresh_executable_city_count':0,
    'executable_substrate_coverage_status':'NONE',
    'failure_samples':[{'error':'database is locked'}]   (repeated 02:36-02:46 CDT)
... and occasionally a good one: 'fresh_executable_city_count':21, coverage 'PARTIAL',
    'budget_truncated_city_count':48, 'uncaptured_candidate_city_count':28, 'budget_exhausted':1
```

That `0 ↔ 21` is exactly task #122's `fresh_executable_city_count` oscillation. **Root cause of the oscillation: write-lock contention on zeus_trades.db between the 20s warm-cycle, the reactor's own per-event writes, and the venue-fact ingestor — most warm cycles capture 0 fresh cities; the rare lock-free window lands ~21.** When 49 cities all need refresh, the 14s snapshot budget + lock losses cover only ~21, deferring ~28 (`topology_deferred_families` 104-161).

Why secondary for THESE two families: when sampled at 07:40-07:41 their NO-side snapshots were fresh (`captured_at` within the minute, `freshness_deadline` +3 min, `accepting_orders=1`). The entry gate (`executable_snapshot_gate_from_trade_conn`, `src/engine/event_reactor_adapter.py:1031-1077`) uses `require_fresh=False` (identity-only at entry; price-freshness enforced at submit), so it passes for them. The oscillation means that on a given turn the snapshot MIGHT be stale → `EXECUTABLE_SNAPSHOT_BLOCKED`/`STALE` requeue (seen in log) → another full-rotation wait. So #122 lengthens the effective wait but is not the primary gate; even with perfectly fresh snapshots the 49-deep × 1-per-cycle rotation alone starves them for hours.

## 5. The cross is armed (no fix needed there)
`_family_rest_state` (`src/engine/event_reactor_adapter.py:8159-8248`): for `fact_state in ('CANCEL_CONFIRMED','EXPIRED')` within 24h it sets `escalated=True` (the `matched<=0` disqualifier was removed 2026-06-16). Both families satisfy this. So `select_rest_then_cross_mode` (mode_consistent_ev.py lane 3) WILL cross as TAKER_ESCALATED_AFTER_REST the moment a re-decision runs. The armed cross is correct; it is simply never invoked in time.

---

## FIX (minimal, no new cap/gate)

**Root cause to fix:** a just-cancelled escalated family must get a re-decision turn promptly instead of waiting ~2-3 h for the 49-deep per-city round-robin. The escalation job already KNOWS the exact family at cancel time and already logs "the next certified decision … may cross" — but emits nothing to trigger it.

**Change:** when `run_cancels_for_expired_rests` confirms a cancel, emit ONE family-targeted re-decision opportunity event for that family that the reactor claims ahead of the round-robin backlog. The `EDLI_REDECISION_PENDING` type already exists, is already in `_FORECAST_DECISION_EVENT_TYPES` (`src/events/reactor.py:124`), and already carries an FSR-shaped payload — reuse it, do not invent a new type.

Primary site — `src/execution/maker_rest_escalation.py`, inside the success branch of `run_cancels_for_expired_rests` (right after `stats["cancelled"] += 1`, line 144-155). After a confirmed cancel, write an `EDLI_REDECISION_PENDING` opportunity_event for that family (city/target_date/metric + condition_id are all recoverable from the cancelled `venue_commands` row / `executable_market_snapshots`). Keep the network-cancel phase connection-free per its existing contract: collect the cancelled families in the loop and do the single event-write in a short world-DB write unit in the caller `_maker_rest_escalation_cycle` (`src/main.py:6295`), which already owns DB access, mirroring the existing `_edli_emit_*` mutex pattern (`src/main.py:5802-5902`).

**For that emitted event to actually jump the queue (this is the load-bearing half):** give the escalation-triggered re-decision its own claim tier ABOVE the FSR round-robin, OR exempt it from the per-(tier,city) `_city_round` partition, in `fetch_pending`. Concretely, in `src/events/event_priority.py:claim_tier_expr_sql` add a clause that ranks an escalation-originated `EDLI_REDECISION_PENDING` (distinguish via its `source` or a payload flag set by the emit) at a tier numerically below 1 (e.g. tier 0 regardless of `day0_is_tradeable`, since this is a confirmed-armed cross with proven settlement edge, not a shadow). Because the round-robin window is `PARTITION BY _claim_tier, _city_key` (`event_store.py:231-239`) and the outer sort is `_claim_tier ASC` first, a strictly-lower tier is claimed before the entire 49-deep FSR rotation — so the cross fires on the very next cycle (~seconds-minutes), not in hours. This adds NO cap and NO throttle; it is a priority lane for an event the system already knows is armed and +EV.

Smallest viable variant if a tier change is judged too broad: keep the new event at Tier 1 but exclude escalation-origin re-decisions from the `_city_round` ROW_NUMBER partition (treat each as `_city_round=0`), so they sort ahead of every city's rank-1 within Tier 1. Either way the decisive edit is in `fetch_pending`/`claim_tier_expr_sql` ordering, because `priority DESC` alone is provably too weak (it sits below `_city_round`).

**Why not "just raise the reactor budget / shorten the rotation":** that is a global throughput change touching every family and risks the live-incident regressions those budgets were added to fix (claim-storm, scheduler coalescing). The targeted re-decision lane fixes exactly the armed-cross-starvation case and nothing else.

**Secondary (separate ticket, #122):** the warm-cycle `database is locked` spiral on zeus_trades.db is a real throughput tax on ALL families and the source of the `fresh_executable_city_count` 0↔21 oscillation. It is NOT required to unblock these two families (their snapshots are fresh), so fix it independently; do not bundle it into the cross-unblock change.

## References
- `state/zeus_trades.db` venue_commands / venue_order_facts — both families CANCEL_CONFIRMED matched=0, no new commands since 07:13.
- `state/zeus-world.db` opportunity_events / opportunity_event_processing — FSR re-emitted every cycle (latest 07:40:58Z), but expired attempt_count=0; decision_log empty since 07:13.
- `logs/zeus-live.log` — `EDLI reactor cycle result: processed=1` modal; reactor 60s-scheduled but 13-min real gaps; `database is locked` warm-cycle spiral; escalation job cancelling expired rests each 5-min tick.
- `src/events/reactor.py:61` (30s budget), `:127-165` (`_fair_lane_interleave`), `:623-712` (`process_pending` budget loop), `:124` (`_FORECAST_DECISION_EVENT_TYPES`).
- `src/events/event_store.py:176-290` (`fetch_pending` per-(tier,city) round-robin; `:254-260` outer sort `_claim_tier ASC, _city_round ASC, priority DESC`).
- `src/events/event_priority.py:92-133` (`claim_tier_expr_sql` tier authority), `:45-48` (priority is within-tier sub-sort only).
- `src/main.py:5839-5871` (continuous re-decision FSR re-emit), `:6295` (`_maker_rest_escalation_cycle`), `:6842-6885` (`_edli_emit_forecast_snapshot_events`).
- `src/execution/maker_rest_escalation.py:116-178` (cancel job — emits no re-decision; the fix site).
- `src/engine/event_reactor_adapter.py:1031-1079` (entry snapshot gate, require_fresh=False), `:8159-8248` (`_family_rest_state` → escalated=True).

## Provenance verdicts (helpers read)
- `src/events/reactor.py` — CURRENT (last touched bef3671835, 2026-06-16 00:23, forecast-first interleave; matches running sha).
- `src/events/event_store.py` fetch_pending ordering — CURRENT (per-city fairness 2026-06-11 incident law; consistent with live behavior).
- `src/execution/maker_rest_escalation.py` — CURRENT (split network/DB phases 2026-06-11; cancel-only, no re-emit by design — that absence IS the gap).
- `src/engine/event_reactor_adapter.py` `_family_rest_state` — CURRENT (matched<=0 disqualifier removed 2026-06-16, same day as the rest-then-cross fix; armed correctly).
