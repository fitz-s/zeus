# Live State Tracker — 2026-06-13 ~23:08 UTC

## A. Verdict

The reactor is running and evaluating candidates but the **M5 WS-gap submit latch is closed** due to 1 unresolved `position_drift` reconcile finding (recorded 2026-06-12T22:58 UTC), blocking all new order submission; 0 new edli_no_submit_receipts, 0 new venue_commands, and 0 new decisions since daemon restart.

---

## B. Tracked Items

### 1. Reactor — New Decisions Since Restart

- **newest edli_no_submit_receipts.created_at**: `2026-06-12T12:12:20.258848+00:00`
- **count where created_at >= '2026-06-13'**: 0
- **count where created_at >= '2026-06-13T17:00'**: 0

Query evidence:
```
timeout 25 sqlite3 "file:state/zeus-world.db?mode=ro" \
  "SELECT created_at FROM edli_no_submit_receipts ORDER BY created_at DESC LIMIT 1;"
→ 2026-06-12T12:12:20.258848+00:00

timeout 25 sqlite3 "file:state/zeus-world.db?mode=ro" \
  "SELECT COUNT(*) FROM edli_no_submit_receipts WHERE created_at >= '2026-06-13';"
→ 0
```

**Headline: ZERO new decisions or receipts of any kind since daemon restart.**

---

### 2. Upstream Alive? EMS Freshness + Forecast Freshness

**executable_market_snapshots** (timestamp col: `captured_at`):
- Newest: `2026-06-14T04:05:16.351356+00:00` (current, ~3 min ago at query time)
- Count in last 3h (>= 2026-06-14T01:00): **114,003 rows**

```
timeout 25 sqlite3 "file:state/zeus_trades.db?mode=ro" \
  "SELECT MAX(captured_at) FROM executable_market_snapshots;"
→ 2026-06-14T04:05:16.351356+00:00

timeout 25 sqlite3 "file:state/zeus_trades.db?mode=ro" \
  "SELECT COUNT(*) FROM executable_market_snapshots WHERE captured_at >= '2026-06-14T01:00';"
→ 114003
```

**zeus-forecasts.db**:
- Newest forecasts.captured_at: NULL (no recent captures via `captured_at` col; `imported_at >= '2026-06-13'` = 0 rows)
- Newest settlement_outcomes.settled_at: `2026-06-14T03:46:17+00:00` (fresh)

The ingest daemon (pid 75222) is writing heartbeats every minute and running replacement forecast materialization every 5m (last seen 23:06 UTC). ECMWF open data cycle `2026-06-13T12Z` already journaled. Upstream feeds are alive.

---

### 3. Daemon Activity — What Is the Reactor Doing RIGHT NOW?

**All 3 daemons are actively logging** (zeus-live.log, zeus-ingest.log, zeus-forecast-live.log all modified within last 30 min).

**zeus-live.log** (pid 449, reactor/main) — last line timestamp: `2026-06-13 23:08:11 UTC`. The reactor is firing `_edli_event_reactor_cycle` every ~1–2 minutes (confirmed by `[apscheduler.executors.reactor]` lines). Most recent cycle result (23:07:44 UTC verbatim):

```
2026-06-13 23:07:44,264 [zeus] INFO: EDLI reactor cycle result: processed=4 proof_accepted=0 rejected=4 retried=204 dead=0 claim_lock_bounces=0 reasons=['EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=13 coverage_unlicensed_tail=1 direction_law=1 other=7; best=Will the highest temperature in Kuala Lumpur be 35°C or higher on June 14? buy_yes q_lcb=0.0392 price=0.0080 ev_per_dollar=3.9020', 'EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=14 coverage_unlicensed_tail=2 other=6; best=Will the highest temperature in Tel Aviv be 32°C on June 14? buy_yes q_lcb=0.0275 price=0.0010 ev_per_dollar=26.5000', 'TRADE_SCORE_NON_POSITIVE', 'EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=19 other=3; best=Will the highest temperature in Munich be 26°C or higher on June 14? buy_yes q_lcb=0.0000 price=0.0010 ev_per_dollar=-1.0000']
```

And the submit latch (23:08:02 UTC verbatim):
```
2026-06-13 23:08:02,870 [zeus] INFO: M5 WS-gap reconcile kept submit latch closed: {'status': 'blocked', 'findings': 1, 'unresolved_findings': 1, 'captured_surfaces': ['open_orders', 'positions', 'trades'], 'unavailable_surfaces': [], 'reason': 'm5_findings_unresolved'}
```

**zeus-ingest.log** (pid 75222): Normal heartbeat ticks every minute, `_k2_daily_obs_tick` running at 23:00, source health probe complete (7 sources), no errors.

**zeus-forecast-live.log** (pid 67248): Heartbeat ticks every 30s, replacement forecast materialization running every 5m, ECMWF open data cycle already journaled, no errors.

---

### 4. Blocking? Recent Errors or Crashes

No crash loops or fatal errors found. Identified signals:

- **DEGRADED live_health_composite**: `failing_surface=business_plane reason=CANDIDATES_WITHOUT_FINAL_INTENTS_OR_NO_TRADE_REASONS` — recurring every ~1 min since at least 23:00 UTC. This is a symptom of the reactor rejecting all candidates, not a cause.

- **ERROR at 23:04:42** (zeus-live.err, verbatim):
  ```
  2026-06-13 23:04:42,260 [src.execution.command_recovery] ERROR: recovery: filled entry projection repair failed for command 84fb2c4c685a4040: filled entry projection repair requires matching decision_log trade_case
  ```

- **BELIEF_AUTHORITY_FAULT at 23:05:17** (zeus-live.err, verbatim):
  ```
  2026-06-13 23:05:17,107 [src.engine.monitor_refresh] ERROR: BELIEF_AUTHORITY_FAULT: position a4a2d274-897 (Beijing 2026-06-14 Direction.NO) has had stale belief for 87 consecutive monitor cycles while the market price is fresh — the exit organ is blind on a live position
  ```

- **M5 reconcile finding blocking submissions** — every cycle since at least 22:46 UTC, persistent 1 unresolved finding. DB query confirms:
  ```
  timeout 25 sqlite3 "file:state/zeus_trades.db?mode=ro" \
    "SELECT finding_id, kind, subject_id, context, recorded_at FROM exchange_reconcile_findings WHERE resolved_at IS NULL;"
  → 5bbc2be2-350c-4bdf-ac0e-f080e41f9012|position_drift|2599807256...235694|ws_gap|2026-06-12T22:58:12.242163+00:00
  ```

- **Gamma returning empty event lists** for multiple cities (Amsterdam, Wellington, Sao Paulo, Munich, Lucknow, Tel Aviv, Karachi, Jakarta/2026-06-14) — families stuck at FDR gate, bin identity unknown. Recurring every reactor cycle.

- **Deployment SHA divergence**: `boot_sha=99050c14 current_sha=ac7a7558 uptime_hours=2.9 — within grace window, no action` (warning only).

- **_edli_market_substrate_warm_cycle** skipping repeatedly (`maximum number of running instances reached (1)`) — substrate warm is a slow job, backing up. Not fatal.

---

### 5. Spot-Check: 3 FILLED venue_commands — Zeus-Originated?

Query:
```
SELECT created_at, state, market_id, side, decision_id, snapshot_id
FROM venue_commands WHERE state='FILLED' ORDER BY created_at DESC LIMIT 3;
```

Results:

| created_at | side | decision_id prefix | snapshot_id prefix |
|---|---|---|---|
| 2026-06-11T17:18:17 | SELL | `edli_exec_cmd:edli_evt_6bd07a7b...` | `ems2-eccd9d24...` |
| 2026-06-10T22:54:50 | BUY | `edli_exec_cmd:edli_evt_55c1b403...` | `ems2-013ea5e5...` |
| 2026-06-10T22:24:50 | BUY | `edli_exec_cmd:edli_evt_7b7583eb...` | `ems2-aca2ec13...` |

All 3 carry `edli_exec_cmd:edli_evt_*` prefixed decision_ids and `ems2-*` snapshot_ids. These are Zeus-originated EDLI decisions, not operator manual co-trades.

---

## C. Most Likely Lead

**LEAD (not verdict)**: The submit latch has been closed since before the restart due to 1 unresolved `position_drift / ws_gap` reconcile finding (finding_id `5bbc2be2-350c-4bdf-ac0e-f080e41f9012`, recorded `2026-06-12T22:58`, subject token `2599807256...235694`). The M5 reconciler scans it every cycle, cannot auto-resolve it, and keeps the `allow_submit=False` latch. The reactor IS evaluating and rejecting candidates on edge grounds (mostly `capital_efficiency_lcb_ev` — prices too tight), but even if it found a positive edge, new entry orders cannot reach the venue while the latch is closed.

**The one next probe that would confirm it**: Query the full `evidence_json` of that finding to see what the WS-gap position drift is and whether it auto-resolves on the next chain-sync or requires operator acknowledgment:

```sql
SELECT evidence_json FROM exchange_reconcile_findings
WHERE finding_id = '5bbc2be2-350c-4bdf-ac0e-f080e41f9012';
```

If `evidence_json` shows a token that is no longer in `position_current`, the reconciler may be unable to mark it resolved without a manual `resolve` call or a position lot repair — which is the specific mechanism to confirm.
