# Live fill-observation DEAD for ~22h — root-cause diagnosis

- Created: 2026-06-23
- Last audited: 2026-06-23
- Authority basis: live-chain evidence only (operator law: no backtest/replay). DBs read `?mode=ro`.
- Scope: diagnose-only. No code changed. Operator verifies + implements.

## TL;DR (root cause)

The authenticated Polymarket **user-channel WebSocket TRADE subscription stopped delivering
trade frames at 2026-06-22T04:37:49Z** and never recovered, while the daemon process, its
scheduler, and the WS *transport* (ping/pong) all stayed alive. Because the WS is the only
real-time fill-truth path, `UserTradeObserved` (and the inbox it feeds) went silent for ~22h.
The break coincides exactly with a **+5h apscheduler clock discontinuity at 04:37:29Z** (every
job's `next run at` jumps from `04:xx UTC` to `09:xx UTC` in one tick) — the signature of a
host sleep/suspend or wall-clock jump. The WS `_runner` thread later reconnected at the TCP
level (pongs → "transport healthy") but the **CLOB authenticated trade subscription was never
re-established**, so MATCHED/MINED/CONFIRMED trade frames stopped arriving. Liveness telemetry
(`daemon-heartbeat-price-channel-ingest.json`, `scheduler_jobs_health.json`) still reports
`alive`/`OK`, so nothing alarmed and nothing forced a re-subscribe.

**This is an OPERATIONAL restart/re-auth, not a code bug** (the retry loop did its job;
the gap is "transport reconnected but subscription not re-proven + no liveness alarm on
trade-frame silence"). A daemon restart of `com.zeus.price-channel-ingest` re-runs the full
`asyncio.run(ingestor.start())` → fresh `subscription_message()` → re-subscribe and will
resume `UserTradeObserved`. A durable fix (so this self-heals) is proposed in §5.

---

## 1. The fill-observation path (file:line)

WS trade-frame ingest → inbox → `UserTradeObserved`:

- `src/ingest/polymarket_user_channel.py:451` `PolymarketUserChannelIngestor` — the authenticated
  user WS client. `:516` `start()` opens `wss://ws-subscriptions-clob.polymarket.com/ws/user`
  (`:39`), sends `subscription_message()` (`:499`, carries `auth` + `markets`), then
  `async for raw in ws: await self.handle_raw_message(raw)` (`:540-541`).
- `:574` `handle_raw_message` → `:788` `handle_message`: `family in {"trade"}` or status in
  `{MATCHED,MINED,CONFIRMED,RETRYING,FAILED}` → `:884` `_handle_trade`. PING/PONG → `:578-580`
  `_record_subscribed_message` (transport-only liveness).
- `:560` `_heartbeat_loop` sends a protocol ping every `PING_INTERVAL_SECONDS=10` (`:40`); a pong
  refreshes the guard via `_record_transport_keepalive` (`:624`) — **proves transport, not the
  trade subscription** (explicit comment `:625-647`).
- WS thread driver: `src/ingest/price_channel_ingest.py:505` `_runner()` → `:516`
  `asyncio.run(ingestor.start())`; on any exit it logs + `_time.sleep(backoff)` (`:530-539`) and
  reconnects. Thread launched `:541-546` (`name="polymarket-user-channel"`, daemon).
- `UserTradeObserved` is appended by the reconcile cycle, NOT by the WS handler directly:
  `src/ingest/price_channel_ingest.py:974` `_edli_user_channel_reconcile_cycle` drains the inbox
  (`:1028 pending_user_channel_inbox_messages` → `:1046 append_user_channel_message`). The WS
  thread is what *fills* that inbox. Emission grammar: `src/events/live_order_reconcile.py:75-81`
  (`UserTradeObserved requires polymarket_user_channel source`).
- Durable fill→position bridge: `src/ingest/price_channel_ingest.py:733`
  `_edli_durable_fill_bridge_scan` + `:1132-1162` bridge pass →
  `src/events/edli_position_bridge.py:materialize_position_current_from_edli_fill`.
- Order-daemon WS authority read: `src/control/ws_gap_guard.py:178` `_durable_sidecar_status`
  reads `state/daemon-heartbeat-price-channel-ingest.json` (`:192`) + `scheduler_jobs_health.json`
  (`:193`) with `DURABLE_SIDECAR_STALE_AFTER_SECONDS=180` (`:35`). **This is the decoupling: it
  trusts daemon/scheduler liveness as WS truth.**

## 2. WHY no `UserTradeObserved` for 22h — failure mode = (a) WS subscription dead, masked by live transport

Real-chain evidence:

**a. Last fill, exact instant.** `zeus-world.db edli_live_order_events`: last
`UserTradeObserved (source_authority='user_channel')` = **2026-06-22T04:38:31.489394Z**; none
after. `zeus_trades.db venue_trade_facts`: last `WS_USER` fact `CONFIRMED` =
**2026-06-22T04:37:49.318Z** (MINED 04:37:43, MATCHED 04:37:41). Daily `UserTradeObserved`
counts collapse to 0 from 06-23 onward.

**b. The 04:37 clock discontinuity (smoking gun).** `logs/zeus-price-channel-ingest.log` around
the cutover — every apscheduler job's `next run at` jumps +5h in a single tick:
```
2026-06-22 04:37:29,041 ... _edli_market_channel_ingestor_cycle ... next run at: 2026-06-22 09:37:29 UTC ... executed successfully
2026-06-22 04:37:30,023 ... _edli_user_channel_reconcile_cycle  ... next run at: 2026-06-22 09:38:29 UTC ... executed successfully
```
(Immediately prior runs showed `next run at: 2026-06-22 04:4x UTC`.) The log wall-clock stays at
`04:3x` while scheduled-UTC jumps to `09:3x` — a ~5h forward wall-clock/suspend event. This is
the moment trade-frame delivery stopped.

**c. Transport reconnected, subscription did NOT.** `logs/zeus-price-channel-ingest.log`:
```
2026-06-22 10:07:21 ... M3 user-channel ingestor will retry in 5s (attempt 1)
2026-06-22 10:07:39 ... M3 user-channel transport healthy (pong, post-gap reconnect
                        (websocket_disconnect:ConnectionClosedError)); M5 latch stays armed ...
2026-06-22 13:22:37 ... will retry in 10s (attempt 2)
2026-06-22 13:23:01 ... transport healthy (pong, post-gap reconnect ...)
```
The connect loop saw `ConnectionClosedError` (the dropped socket after the suspend), reconnected,
and pongs flow — but the only post-04:37 inbound *data* frames are sparse ORDER/cancel events:
```
2026-06-22 21:58:49 ... M3 user-channel deferred unmatched order event: order_id=0x4ea460b0...
```
`zeus_trades.db venue_order_facts`: last `WS_USER` = 2026-06-22T06:06:34 (CANCEL_CONFIRMED);
exactly **1** WS_USER order fact after that. So even the order-frame trickle is near-dead, and
**zero** WS_USER trade frames since 04:37:49. Conclusion: failure mode **(a)** — the WS is
connected at the socket layer but the authenticated CLOB *trade subscription* is not delivering;
the per-attempt reconnect re-`send()`s `subscription_message()` but the venue side is not
re-emitting the user trade stream to this session (subscription not re-proven by an inbound
trade/order frame).

NOT (b) parse failure: no trade frames are arriving to parse (no `_handle_trade` /
`deferred unmatched trade` entries after 04:37; the last such was 2026-06-21T06:32).
NOT (c) REST-fallback erroring as the cause: REST still runs (it wrote `venue_order_facts
source=REST` through 06-23T03:03) but REST only yields order-level LIVE/CANCEL state, never the
MATCHED trade economics — REST is not a trade-fill authority here.
NOT (d) creds expired: pongs + post-reconnect "transport healthy" prove auth still succeeds
(an `AUTH_FAILED` frame would have latched `auth_failed` at `polymarket_user_channel.py:790`);
no `AUTH_FAILED` / `auth_failed` records appear.

**d. Liveness masks the silence.** `state/daemon-heartbeat-price-channel-ingest.json`
`alive_at=2026-06-23T03:05:29Z` (fresh) and `scheduler_jobs_health.json
edli_user_channel_reconcile status=OK`. The order daemon's `_durable_sidecar_status`
(`ws_gap_guard.py:178`) therefore treats the WS as live truth. Nothing measures *trade-frame
recency*, so a 22h trade-stream death raises no alarm and triggers no forced re-subscribe.

## 3. Why operator fills stay `local_only` — reconcile gap root

The operator's Shenzhen-31C/Warsaw-26C June-24 entries are **Zeus-submitted ENTRY/BUY commands**,
not foreign orders. `zeus_trades.db`:

- `venue_commands`: `command_id=c61b5b7e49194d8c venue_order_id=0x178420...ec37 state=CANCELLED
  intent_kind=ENTRY side=BUY` (so `_lookup_command` *would* match — these are not foreign).
- `venue_order_facts` for those `venue_order_id`s show **only `source=REST`**, transitions
  `LIVE → CANCEL_CONFIRMED`, with **`matched_size=0`** throughout (e.g. `0x178420...ec37`:
  LIVE 02:13:05 → CANCEL_CONFIRMED 02:18:51, matched 0; `0x1ffa8a7d...`: LIVE 01:58 → CANCEL
  02:12, matched 0).
- `position_current`: `chain_state='local_only', shares=0, entry_price=0, chain_shares=NULL,
  order_status='canceled'` for the Shenzhen/Warsaw June-24 rows.

Root of the reconcile gap = **fetched-but-the-fill-frame-never-arrived → matched-as-0**.
The fill bridge (`_edli_durable_fill_bridge_scan`, `price_channel_ingest.py:733`) only
materialises a `position_current` row from an aggregate that carries a
`UserTradeObserved(FILL_CONFIRMED)`. With the WS trade stream dead, no such event exists, so the
bridge has nothing to bridge; REST sees the order go LIVE→CANCEL with matched_size=0 and Zeus
concludes "no fill". The operator's real venue fills are invisible to Zeus purely because the WS
trade-frame path — the sole real-time fill authority — is down. (The thousands of
`command position-link sync failed ... already-bridged` / `EDLI_BRIDGE_STRATEGY_MISSING` WARNINGs
in `.err` are a SEPARATE, pre-existing already-bridged/settled-market accounting nuisance on old
aggregates; they are not the 22h cause and do not block new fills.)

Scope note (`position_current` aggregate, 06-23): `local_only=299`,
`chain_absent_confirmed_position_unattributed=16` (latest 02:56), `synced=59`. 56 `local_only`
rows are target_date 06-23 and 27 are 06-24 — the unreconciled-fill backlog.

## 4. Single root cause

**A host wall-clock/suspend jump at 2026-06-22T04:37 dropped the user-channel WS; the connect
loop re-established the TCP socket (pongs OK) but the venue's authenticated *trade subscription*
did not resume on the reconnected session, and the daemon's liveness/heartbeat telemetry has no
trade-frame-recency check — so the silent trade-stream death was never detected and never
force-recovered.** Everything downstream (inbox → `UserTradeObserved` → durable fill bridge →
`position_current` → monitor/exit) is starved of the only real-time fill input. Submit-side
gating (`ws_gap_guard`) is in-memory per-process and the order daemon trusts the stale sidecar,
so it does not even register the gap.

## 5. Fix

**Immediate (operational — restores observation now):** restart `com.zeus.price-channel-ingest`
(`launchctl kickstart -k gui/$(id -u)/com.zeus.price-channel-ingest`, or the project's standard
respawn). A cold `start()` re-runs `subscription_message()` against a fresh session and the venue
will re-emit the user trade stream; `UserTradeObserved` resumes and the next
`_edli_user_channel_reconcile_cycle` (60s) drains the inbox. The durable fill-bridge scan
(`price_channel_ingest.py:733`) is idempotent and self-healing, so any operator fills that the
venue still reports will bridge into `position_current` once the trade frames arrive. NOTE: fills
that the venue only delivered transiently during the 22h gap may NOT be re-broadcast on reconnect
— after restart, run an explicit REST/CLOB trade-history reconcile for the operator's
Shenzhen/Warsaw June-23/24 markets to back-fill anything the live stream won't replay
(`run_ws_gap_reconcile_and_clear`, `src/execution/exchange_reconcile.py:291`, invoked from
`src/main.py:1681-1685` — the live daemon's M5 sweep).

**Durable (surgical, so this self-heals — proposed; operator to verify):**
1. **Trade-frame liveness, not just transport liveness.** In
   `src/ingest/polymarket_user_channel.py` `start()` (`:516-554`), add a watchdog that tracks the
   timestamp of the last *inbound application frame* (any `handle_message` that reaches
   `_handle_order`/`_handle_trade`, not pongs). If no application frame arrives for N minutes
   while subscribed, force-close the socket so `_runner` (`price_channel_ingest.py:511`)
   reconnects with a fresh subscription. Today only pongs refresh liveness
   (`_record_transport_keepalive`, `:624`), which is exactly what masked the 22h silence.
2. **Surface trade-frame recency in the sidecar.** Have the price-channel daemon write a
   `last_user_trade_frame_at` (or `last_ws_application_frame_at`) into
   `state/daemon-heartbeat-price-channel-ingest.json`, and make
   `ws_gap_guard._durable_sidecar_status` (`src/control/ws_gap_guard.py:178-225`) treat a stale
   application-frame timestamp as a gap even when `alive_at` is fresh. This closes the
   "daemon alive but WS deaf" blind spot universally (any future suspend/network glitch trips it).

Both are universal (no per-market/per-city special-casing, no caps) and address the exact gap:
*the system measured the wrong liveness signal.*
