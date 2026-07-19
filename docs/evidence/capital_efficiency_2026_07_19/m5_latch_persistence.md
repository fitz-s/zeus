# M5 reconcile-required latch — cause split, loss verdict, fix spec (2026-07-19)

Read-only investigation. Scope: why `reduce_only_mode_active` trips on
`m5_reconcile_required` (`src/risk_allocator/governor.py`, commit `7893ac0dc`) and
whether `record_message_persistence_gap("ws_message_persistence_db_locked")`
(`src/control/ws_gap_guard.py:393`) — suspected benign DB-lock noise — is the
actual driver.

**Headline correction first**: the premise handed to this investigation (55% of
GREEN reduce-only trips = `ws_gap_active` noise) is an overstatement in the prior
audit. The real number is **91 rows, not 1808** (§2). The db-locked cause has
**never fired** in the observable log window (§2). No latch loosening is
warranted (§5).

---

## 1. Mechanism — every path that sets/clears `m5_reconcile_required`

All in `src/control/ws_gap_guard.py`.

**Sets `m5_reconcile_required=True`** (two distinct causes):
- `record_gap(reason, ...)` (`ws_gap_guard.py:368-390`) — real disconnects/auth
  problems. Sets `connected=False`, `subscription_state` to the given state
  (usually `DISCONNECTED`). Callers, all in `src/ingest/polymarket_user_channel.py`:
  `websocket_disconnect:{exc}` (line 549), `auth_missing` (545), `auth_failed`
  (792), `market_subscription_mismatch` (797), `stale_last_message` (826, also
  reachable via `ws_gap_guard.py:230-236` `_materialize_stale_gap`). Also
  `record_gap("condition_ids_missing"/gap_reason)` in
  `src/ingest/price_channel_ingest.py:762,853` for the market-data channel (does
  not feed the user-channel M5 latch directly but shares the module).
- `record_message_persistence_gap(reason="ws_message_persistence_db_locked")`
  (`ws_gap_guard.py:393-419`) — a DB write failure while handling an already-
  received user-channel message. Sets `connected=True`,
  `subscription_state="SUBSCRIBED"` (transport/auth stay proven), only
  `gap_reason` changes. Single caller: `polymarket_user_channel.py:591`, inside
  `handle_raw_message`'s `except sqlite3.OperationalError` branch, gated by
  `_is_sqlite_locked` (`polymarket_user_channel.py:449`, string-matches
  "database is locked").

**Clears `m5_reconcile_required=False`** (three distinct paths, all fail-closed
by default):
1. `record_message()` clean-boot fast-clear (`ws_gap_guard.py:240-285`,
   `polymarket_user_channel.py:609-624`) — ONLY when the prior state was
   `not_configured` (process never had a connection, so nothing could have been
   missed) transitioning straight to `SUBSCRIBED`. A genuine mid-run gap
   (`gap_reason` anything else) is explicitly excluded (`ws_gap_guard.py:268-272`).
2. `clear_after_no_local_side_effects()` (`ws_gap_guard.py:288-322`) — requires a
   healthy (`AUTHED`/`SUBSCRIBED`, non-stale) subscription AND caller-proven empty
   local side-effect surface (`_local_side_effect_surface_empty`,
   `polymarket_user_channel.py:746-788`: zero unresolved `venue_commands`, zero
   unresolved `position_lots`, zero unresolved `exchange_reconcile_findings`).
   Invoked from `_record_subscribed_message` on the first inbound message after
   reconnect (`polymarket_user_channel.py:609-624`) and from
   `_record_transport_keepalive`'s clean-boot branch (632-690).
3. `clear_after_m5_reconcile()` (`ws_gap_guard.py:325-365`) — the full M5 sweep.
   Requires healthy non-stale subscription AND zero `findings_count`/
   `unresolved_findings_count` from a **fresh REST venue enumeration**
   (`src/execution/exchange_reconcile.py:274 run_ws_gap_reconcile_and_clear`).
   Scheduled every main-loop cycle: `src/main.py:1535
   _run_ws_gap_reconcile_if_required`, called from `main.py:1689`, gated only by
   `_cycle_lock.locked()` / `_edli_reactor_active()` (no fixed cadence — it is
   attempted continuously, so dwell time ≈ one cycle once the subscription is
   healthy again). On success it also releases exit-retries blocked by the same
   latch (`exit_lifecycle._release_ws_gap_blocked_exit_retries_after_m5_clear`).

Distinguishing real gap vs persistence gap: `gap_reason` string
(`websocket_disconnect:*`, `auth_*`, `stale_last_message` vs
`ws_message_persistence_db_locked`) plus `connected`/`subscription_state` (real
gap → `DISCONNECTED`; persistence gap → stays `SUBSCRIBED`). Both set the same
boolean `m5_reconcile_required=True` and both require the same M5 proof to clear
— by design (`ws_gap_guard.py:399-403` docstring: "A DB write failure can hide a
user-channel fact, so new submits still need M5 proof").

---

## 2. Frequency + cause split (last 14/30 days, numbers not vibes)

**Log evidence directly covers 2026-07-07 → 2026-07-19** (12 days; `logs/*.log`
have no rotation/archive, so earlier dates aren't in raw logs — see DB
cross-check below).

```
grep -c "ws_message_persistence_db_locked\|ws_message_persistence_deferred_db_locked" logs/*.log
→ 0 (all files, all 12 days)
grep -c "M3 user-channel deferred message persistence" logs/*.log
→ 0
```

**The db-locked cause has never fired in the observable window.** What *did*
fire, in `logs/zeus-price-channel-ingest.log` (12 days):
- `websocket_disconnect:ConnectionClosedError` reconnects: 92
- `websocket_disconnect:ConnectionResetError` reconnects: 21
- Ingestor thread relaunches (`M3 user-channel ingestor thread launched for N
  condition_ids`): 400+, with exponential-backoff retry logs up to attempt 12+
  (5s→10s→20s→40s→80s→160s→300s cap) — i.e. the WS thread genuinely
  crashes/reconnects often; some outages run several minutes before reconnect
  succeeds.

**DB cross-check (durable, `state/zeus_trades.db.decision_log`, full 30-day
window 2026-06-19→2026-07-19, 15,251 `exit_monitor` rows, 3223 `reduce_only=true`
≈ 21.1%, matches the prior audit's 15,408/3269/21.2% within row-count noise from
a slightly different query boundary):**

Breaking `reduce_only=true` down by **actual cause** (not just `risk_level`,
which the prior audit used as a proxy and which mixes causes):

| cause | rows | % of reduce_only |
|---|---|---|
| `kill_switch_armed` (`kill_switch_reason=heartbeat_lost`) | ~1874 | 58% |
| `risk_level` non-GREEN, no kill-switch, no ws_gap (DATA_DEGRADED/RED/ORANGE/YELLOW) | ~1073 | 33% |
| `ws_gap_active=true` (the real M5-latch driver), any risk_level | ~130 | 4% |
| — of which `risk_level=GREEN` specifically | **91** | **2.8%** |
| `systemic_unknown_side_effect_count>0`, GREEN, no kill-switch/ws_gap | ~412 | — (this is the bulk of what the prior audit's "GREEN=1808" bucket actually was) |

**The prior audit's "GREEN=1808 (55%)... reduce_only=true because
ws_gap_active=true" claim is wrong.** It reported the total GREEN-risk-level
reduce_only count (1808, confirmed: 1297 heartbeat_lost + 412
systemic_unknown_side_effect + 91 ws_gap_active + 8 other ≈ 1808) but attributed
the *entire* bucket to `ws_gap_active` based on inspecting one sample row that
happened to show it, without running the full breakdown. The actual
`ws_gap_active`-driven GREEN count is **91 rows out of 15,251 total cycles
(0.6%)**, not 1808/15408 (11.7%).

**Dwell time** (the 91 GREEN ws_gap rows, timestamps pulled and inspected):
- One sustained episode: 2026-06-19 09:40–10:33 UTC, 28 consecutive cycles at
  ~1-3 min cadence ≈ **53 minutes**.
- Everything else: isolated single-cycle blips (one row, then clear) scattered
  across 06-20, 06-22 through 06-26, 07-01, 07-04, 07-08, 07-11, 07-12, 07-14,
  07-15, 07-17, 07-18 — never more than a handful per day, cleared by the very
  next cycle.

**Correlation check** (do the post-07-07 blips line up with real disconnects, not
db-lock?): `zeus-price-channel-ingest.log` is in **local time** (UTC-4 this
period; DB timestamps are UTC — see `[[zeus-logs-and-ps-are-local-time-db-is-utc]]`).
Example: decision_log GREEN ws_gap blip at `2026-07-18T11:00:04.487508+00:00`
matches `2026-07-18 07:00:10,203 [...polymarket_user_channel] INFO: M3
user-channel transport healthy (pong, post-gap reconnect
(websocket_disconnect:ConnectionClosedError)); M5 latch stays armed pending the
full ws-gap reconcile sweep` (07:00 local = 11:00 UTC — exact match). Same
pattern at the 04:38 and 05:27 local reconnects near the earlier 07-18 blips.
**Every correlatable blip lines up with a real `ConnectionClosedError`/
`ConnectionResetError` reconnect, never with a db-lock event** (which, again,
never appears in any log).

---

## 3. The db-locked case: lost or retried?

Read `polymarket_user_channel.py:576-607` (`handle_raw_message`): it calls
`self.handle_message(message)` inside a bare `try`; on `sqlite3.OperationalError`
matching "database is locked" it logs a warning, calls
`record_message_persistence_gap(...)`, and **returns** a small dict
(`{"reason": "ws_message_persistence_deferred_db_locked", ...}`). There is **no
retry, no queue, no re-delivery** anywhere in this path — the specific
`append_order_fact` / trade-fact insert that `handle_message` was attempting
(`_handle_order` at line 831, `_handle_trade` at line 886) is abandoned outright
for that message. The async `for raw in ws:` loop simply moves on to the next
message.

This is not a millisecond-blip scenario if it *does* fire: the connection this
ingestor uses (`get_trade_connection_with_world` → `_connect`,
`src/state/db.py:234-289`) applies `PRAGMA busy_timeout` (`_apply_busy_timeout`,
default `ZEUS_DB_BUSY_TIMEOUT_MS=30000`, i.e. 30 seconds) on top of
`sqlite3.connect(timeout=)`'s C-level busy handler. `sqlite3.OperationalError:
database is locked` can only surface *after* SQLite's own busy handler already
waited up to 30 seconds and still failed to get the lock. So if this cause ever
fires in production, it means a writer held the lock for 30+ sustained seconds —
a real, severe contention event, not transient noise — and the message write is
then genuinely dropped with zero compensating retry.

**Verdict: if it fires, it is a real potential missed-fill, not a false
positive.** The latch's fail-closed behavior on this cause is currently
*correct*, not overcautious — there is no evidence in the code that the write is
"retried/buffered and eventually written." It categorically is not. However
(§2), this cause has **not fired even once** in the 12 days of logs available,
so it is not what is producing the measured 91-row GREEN figure — real
websocket disconnects are.

---

## 4. Blocked-entry cost

Reactor-level requeues explicitly attributing the block to
`reduce_only_mode_active` (`grep "reason=risk_allocator_pre_submit_blocked:
reduce_only_mode_active" logs/zeus-live.log`, 12-day window): **3 events total**,
each requeued exactly once (`count=1`, no repeat/stuck occurrences) — consistent
with the single-cycle dwell times in §2. `best_rejected=...` reactor summary
lines (which quote `rejected_ev_per_dollar` for the top rejected candidate each
cycle) **never** cite `reduce_only_mode_active` as the reason in 12 days of
logs — every `best_rejected` candidate in that window was rejected for edge/
sizing/duplicate reasons unrelated to the latch. The money-path reactor already
requeues transiently-blocked events "no cap; horizon-bounded," and since dwell
time is ~1-3 minutes (one auction cycle), a blocked candidate gets another shot
almost immediately once the latch clears. **Measured cost: negligible** — no
identified EV-positive candidate was actually lost to this specific cause in the
observable window.

---

## 5. Fix spec

**No change to the trip condition.** `reduce_only_mode_active`'s unconditional
trip on `m5_reconcile_required` (`governor.py:422-423`, added in `7893ac0dc`) is
correctly wired: the driver is real websocket disconnects (§2, §3), each
correctly demanding M5 proof before resuming submits, at a measured cost of ~91
GREEN cycles / 15,251 total (0.6%) and 3 actual requeue events in 12 days — not
the 21%/55% figure that motivated today's earlier commit. Loosening this latch
(e.g. adding a seconds-threshold, or auto-clearing on persistence-gap
specifically) would trade real fill-integrity correctness for a saving that
doesn't exist. The 2026-07-19 commit message itself flagged this as "a separate,
larger investigation outside this surgical fix's scope" — this is that
investigation's conclusion: **leave `reduce_only_mode_active` and
`ws_gap_guard.py` exactly as they are.**

**Two non-latch follow-ups, both process/evidence, not code:**
1. Correct `docs/evidence/capital_efficiency_2026_07_19/capital_utilization.md`
   §"Ranked idle-capital causes" item 3: replace "~55% of those trips occur at
   `risk_level=GREEN` ... i.e., a data-quality flag rather than genuine risk
   elevation" with the actual breakdown (§2 above) — GREEN-ws_gap is 91 rows
   (2.8% of reduce_only, 0.6% of all cycles), not 1808/55%, and the fix already
   shipped in `7893ac0dc`/`f795792a1` addressed the *unconditional-trip* framing
   bug, not a volume problem that needed fixing.
2. `kill_switch_armed` / `heartbeat_lost` is actually the dominant reduce_only
   driver (58% of all reduce_only cycles, ~1874 rows/30d) — an order of
   magnitude larger than the ws_gap cause this investigation was scoped to. It
   is out of scope here but is the real lever if capital-utilization work
   continues in this area; worth its own root-cause pass (why heartbeat is
   "LOST" so often) rather than further attention on the M5 WS latch.

**Invariant test to guard the "no change" conclusion**: no new test needed for
`governor.py` (existing `tests/test_risk_allocator.py` from `7893ac0dc` already
covers `m5_reconcile_required` tripping unconditionally and `ws_gap_active`
alone not tripping below threshold). If anyone later proposes gating
`m5_reconcile_required` by cause (e.g. skip-latch on persistence-gap), the
guarding test must assert `record_message_persistence_gap` still sets
`m5_reconcile_required=True` and that `reduce_only_mode_active` still returns
`True` for it — i.e. any future change must prove the write is provably
loss-proof (retry-to-success or durable queue) *before* relaxing the trip, per
§3's verdict.
