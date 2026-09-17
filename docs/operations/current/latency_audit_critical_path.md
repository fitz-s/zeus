# Latency audit — end-to-end decision critical path, 2026-09-17

Read-only audit of the price-move -> decision -> order-submit path. Live entry point
confirmed: `python -m src.main` (launchd `com.zeus.live-trading`, `KeepAlive=true`),
pid 51287, booted 2026-09-17T15:43:14Z; price feed daemon `python -m
src.ingest.price_channel_daemon` (`com.zeus.price-channel-ingest`), pid 10714, booted
2026-09-17T08:24:34Z. All DB reads used `mode=ro`. No process was restarted or
attached with a profiler.

**Critical DB-file gotcha**: `state/zeus_world.db` and `state/zeus_forecasts.db`
(underscore) are 0-byte decoys. The live DBs are `state/zeus_trades.db` (underscore,
199.5G), `state/zeus-forecasts.db` (hyphen, 98G), `state/zeus-world.db` (hyphen,
100G). All world-DB queries below hit `zeus-world.db`.

## 1. Ordered critical-path stages

| # | Stage | module:function | Waits on | Latency |
|---|---|---|---|---|
| 1 | WS message recv | `src/events/triggers/market_channel_ingestor.py` `run_websocket_forever` (asyncio) | Polymarket public WS push | UNVERIFIED — venue-side, not ours |
| 2 | Quote coalesce + feasibility-DB commit | `market_channel_ingestor.py:2474` `_flush_quote_projection_forever`, wake-driven, `MARKET_CHANNEL_QUOTE_MIN_COMMIT_INTERVAL_SECONDS=0.01s` (`:54`), batch 128 (`:35`), 50ms fallback poll (`:55`) | `feasibility_conn` (trades DB) | Architecturally sub-100ms; not directly measured (no populated instrumentation column — see §4) |
| 3 | Redecision routing (price -> family screen) | `src/events/price_channel_redecision_router.py:1071` `_PriceChannelRedecisionSink.__call__`, invoked SYNCHRONOUSLY (no executor/`to_thread`) from `market_channel_ingestor.py:1030/968` `_notify_market_event_sink`, on the asyncio event-loop thread | 3 fresh sqlite connections opened per call (`reuse_read_connections=False` is the wired default — `src/ingest/price_channel_ingest.py:5012,5415,6437`) + a cross-process WORLD write flock (`price_channel_ingest.py:1537` `_edli_price_channel_world_write_connection`) that explicitly **yields to the reactor** if it owns the writer rather than queueing | **MEASURED: p50=592s, p90=2057s, p99=4562s, max=5932s, min=11.3s, n=228** (query in §4) |
| 4 | Cross-process wake | `src/runtime/reactor_wake.py::publish_reactor_wake` -> AF_UNIX datagram -> `src/main.py:7538` listener `_run_edli_reactor_wake_listener`, `notifier.recv(1)`, 1.0s timeout fallback | socket write, or 1s poll fallback if the datagram is missed | UNVERIFIED, architecturally <=1s — not the bottleneck |
| 5 | Reactor claim + decide | `src/main.py:6762` `_edli_reactor_wake_poll_once` -> `src/events/reactor.py::run_edli_event_reactor_cycle`/`OpportunityEventReactor.process_pending` | Priority arbitration between held-sell / Day0-urgent / monitor-fairness / price / forecast event classes; commits per-event (fixed 2026-05-31 — previously held the WAL write lock for the whole ~330s cycle) | UNVERIFIED per-stage (no real per-stage timestamp exists — see §4) |
| 6 | Global-auction receipt store (pre-submit) | `src/engine/global_batch_runtime.py:9333` `_store_global_auction_receipt`, called once from inside `process_current_global_batch` (`:7416`+) | DB reads (candidate evaluations, holdings snapshot) + delta-compute vs. prior receipt + `zlib.compress(level=9)`+base64 on multiple sub-payloads (`:2361,2431,2478,3057,3110,3196`) + SQLite INSERT + `trade_conn.commit()` (fsync, `PRAGMA synchronous` never overridden anywhere in the codebase => default FULL) | **MEASURED live, n=3263, 2026-09-16T05:54–2026-09-17T17:45: min=0.007s, p50=0.516s, p90=0.966s, p99=2.300s, max=21.313s, mean=0.569s** (query in §4) — this is THE standout finding, see §3 |
| 7 | Preflight/pre-submit gates | `reactor.py:3743` `_check_one_pre_submit` / `:3866 _apply_pre_submit_check` | JIT CLOB `/book` fetch, kept warm by the `edli_presubmit_jit_keepalive` 25s job specifically to avoid a ~2.2-2.7s cold-TLS handshake (GATE #84, fixed 2026-06-22, previously timed out 118/120 JIT fetches) | UNVERIFIED post-fix — historically up to 2.7s before the fix |
| 8 | Actuation / venue submit | `global_batch_runtime.py:~10481` `checkpoint_final_actuation()` (`work_context.checkpoint("final_actuation:before_submit")`) -> `actuate_preflighted_winner.consume(...)` / `actuate_winner(...)` (`:~10498`) -> HTTP POST to venue | venue HTTP RTT | UNVERIFIED — no populated submit-time column found (see §4) |

No fixed-UTC-shard schedule gates this specific path. `edli_day0_hourly_refresh` (45s)
is a rolling cadence, not clock-aligned; `settlement_guard_report` (cron 09:15 UTC) and
`settlement_skill_attribution` (30min) are audit/reporting jobs, off the money path.

## 2. Interval-constant table (live path only)

| constant | value | file:line | gates |
|---|---|---|---|
| `edli_event_reactor` | 60s | `src/main.py:10961` (`reactor_scan_interval_seconds`) | durable recovery scan; the real-time path is the wake listener (stage 4), this is only the backstop |
| `edli_presubmit_jit_keepalive` | 25s | `src/main.py:10978` | keeps JIT CLOB TLS session warm inside a 90s keepalive_expiry |
| `edli_bankroll_warm` | 60s | `src/main.py:10993` | decoupled from the reactor cycle per its own comment |
| `edli_day0_hourly_refresh` | 45s (env `ZEUS_DAY0_HOURLY_REFRESH_JOB_SECONDS`) | `src/main.py:11002` | Day0 hourly vector refresh; offset +36s vs. exit monitor to avoid a gcd=15s collision (already handled) |
| `c3_staleness_cancel` | 5min | `src/main.py:11022` | maker-rest TTL/staleness cancel |
| `edli_continuous_redecision_screen` | 90s | `src/main.py:11038` | comment claims "well inside the executable-price freshness window" — **refuted by the §1 stage-3 measurement, which shows the real price->redecision-available path can take 10-99 minutes** |
| `edli_command_recovery` | 60s | `src/main.py:11050` (`_EDLI_COMMAND_RECOVERY_INTERVAL_SECONDS`) | stuck SUBMITTING/UNKNOWN sweep |
| `chain_mirror_reconcile` | 10min | `src/main.py:11073` | on-chain position mirror; HTTP correctly ordered before the short DB txn |
| `exit_monitor`/`exit_monitor_recovery` | 30s (`HELD_POSITION_MONITOR_RECOVERY_INTERVAL_SECONDS`, `main.py:204`) | `src/main.py:11124,11133` | held-position exit monitoring |
| `world_wal_checkpoint`/`trades_wal_checkpoint`/`forecasts_wal_checkpoint` | 90s each, offset 120/135/150s | `src/main.py:11176-11201` | WAL-bloat backstop, post-incident fixes, not on the decision hot path |
| `venue_heartbeat` | 5s (`ZEUS_HEARTBEAT_CADENCE_SECONDS`) | `deploy/launchd/com.zeus.venue-heartbeat.plist` | connectivity heartbeat |
| `ZEUS_DB_BUSY_TIMEOUT_MS` | 30000ms default | `src/state/db.py:193` | every sqlite write waits up to 30s under contention; `price-channel-ingest` does not override it, so a contended WORLD write can block the WS asyncio thread up to 30s per attempt |
| `ZEUS_REACTOR_REFRESH_BUDGET_SECONDS` | 19.0 (live-trading plist) / doc default 17s inside a 20s interval | `src/events/reactor.py:434` | reactor per-cycle refresh budget |
| `MARKET_CHANNEL_QUOTE_MIN_COMMIT_INTERVAL_SECONDS` | 0.01s | `market_channel_ingestor.py:54` | quote-flush min commit spacing — not a bottleneck |

## 3. THE HEADLINE DEFECT — synchronous, pre-submit receipt store

`_store_global_auction_receipt` (`src/engine/global_batch_runtime.py:3679`, single call
site `:9333`) runs **synchronously, in the same function, strictly before actuation**:

```
:9329  receipt_store_started = time.monotonic()
:9333  receipt_row_id = _store_global_auction_receipt(trade_conn, ...)
         -> internally calls _book_native_side_receipt / _book_native_side_delta_receipt
            (:2361,2431,2478), _candidate_evaluations_delta_receipt (:3057),
            _json_object_delta_receipt (:3110), _keyed_object_list_delta_receipt (:3196)
            -- every one zlib.compress(level=9) + base64.b64encode a JSON sub-payload
:9428  _LOG.info("global auction receipt store completed: elapsed_s=%.3f", ...)
:~9432 trade_conn.commit()                      # fsync, synchronous=FULL (never overridden)
...    (same function, further down)
:10465 def checkpoint_final_actuation(): ...
:10481     work_context.checkpoint("final_actuation:before_submit")
:10498 actuation_started = True
:10498 winner_receipt = actuate_preflighted_winner.consume(...) or actuate_winner(...)  # <- venue submit
```

No branch skips the receipt store while still reaching actuation — the only paths
between them are early `reject(...)` aborts (which also never submit) and
`held_completion_expired()` checks. This is plain sequential Python, no
threading/executor dispatch.

**Measured cost — real production timings, not a synthetic benchmark** (grep
`"global auction receipt store completed: elapsed_s="` in `logs/zeus-live.log`, n=3263,
spanning 2026-09-16T05:54 to 2026-09-17T17:45):

```
min=0.007s  p50=0.516s  p90=0.966s  p99=2.300s  max=21.313s  mean=0.569s
```

A companion micro-benchmark (zlib-9+base64 on a real 1.99MB payload: 65.0ms producing
1.33MB, vs. zstd-3 raw BLOB: 5.7ms producing 0.71MB — 11.4x faster, 46% smaller,
zero information loss) shows the compression codec alone is 8-40x cheaper than the
measured 516ms median. **The bulk of the 516ms is NOT the compression call** — it is
the DB reads (candidate evaluations, holdings snapshot), delta-computation against the
prior receipt, full-structure JSON serialization, and the SQLite INSERT+fsync around
it. Swapping the codec (zstd-3) fixes storage and a fraction of the latency; the
larger win requires profiling `_store_global_auction_receipt`'s internals, which this
pass did not have budget to break down further (UNVERIFIED split of DB-I/O vs.
delta-compute vs. serialize vs. codec).

**This sits directly between "decided to trade" and "submit," at exactly the
probability-flip moment the operator's thesis is about a median of 516ms and a p99 of
2.3s — not a storage-only concern.**

## 4. Synchronous blocking on the WS ingest path (stage 3 root cause)

`_PriceChannelRedecisionSink.__call__` (`price_channel_redecision_router.py:1071-1166`)
is a plain (non-async) method called directly, unwrapped, inside the asyncio WS-ingest
event loop (`market_channel_ingestor.py:1030/968` `_notify_market_event_sink`, called
from `_persist_prepared_quote_batch_fairly`, an `async def` with no
`run_in_executor`/`to_thread`). Every call:

1. Opens 3 fresh sqlite connections (world RO, trade RO, forecasts RO) —
   `reuse_read_connections=False` is the wired default at every live call site.
2. Runs the family-screening read.
3. If it decides to emit, opens `_edli_price_channel_world_write_connection`
   (`price_channel_ingest.py:1537`), whose docstring says it explicitly **yields/retries
   if the reactor owns the WORLD writer** — "retrying the latest tick is cheaper than
   queueing this producer ahead of the consumer" — rather than queueing.
4. On a stale `data_version` it raises `PriceChannelRedecisionSnapshotChanged`
   (`:1037,1124`) and the outer flush loop backs off up to
   `MARKET_CHANNEL_QUOTE_FLUSH_RETRY_MAX_SECONDS=1.0s`, doubling.

Because the write is debounced per-family (`_edli_write_price_channel_redecision_event_ids`,
"debounce and write one pending redecision per family"), the retry effectively only
re-fires when the NEXT WS quote for that same token arrives — confirmed by DB evidence
in §5 (multiple distinct `observed_at` values collapse onto one `received_at`). A quiet
family can therefore sit blocked behind reactor/writer contention indefinitely, and
because this all runs on the single asyncio event-loop thread, a slow/contended screen
also delays ingestion of every OTHER token's quotes, not just the one being screened.

No other new sequential-blocking defect was found on this path.
`run_edli_day0_hourly_refresh_cycle` (`reactor.py:6501`) already has deadline/budget
plumbing (bounded fetch reserve, city-rotation cursor) from a prior fix pass — not a
fresh finding.

## 5. Measured, read-only, quiet window (2026-09-17T16:00-21:41Z, no restart in window)

**Query — price-observed to redecision-available delay, n=228:**
```sql
-- state/zeus-world.db, mode=ro
with d as (
  select cast((julianday(received_at)-julianday(available_at))*86400.0 as real) as delay_s
  from opportunity_events
  where event_type='EDLI_REDECISION_PENDING'
    and available_at >= '2026-09-17T16:00:00'
)
select count(*), min(delay_s), max(delay_s), avg(delay_s),
  (select delay_s from d order by delay_s limit 1 offset cast(0.5*(select count(*) from d) as int)) as p50,
  (select delay_s from d order by delay_s limit 1 offset cast(0.9*(select count(*) from d) as int)) as p90,
  (select delay_s from d order by delay_s limit 1 offset cast(0.99*(select count(*) from d) as int)) as p99
from d;
-- result: n=228, min=11.3, max=5931.9, avg=922.4, p50=592.3, p90=2057.0, p99=4561.6
```
Uses the covering index `idx_opportunity_events_type_available(event_type,
available_at)`. `observed_at`/`available_at` are carried forward from the ORIGINAL
quote event; `received_at`/`created_at` are `datetime.now(timezone.utc)` stamped when
`_PriceChannelRedecisionSink._build` finally succeeds
(`price_channel_redecision_router.py:1089`) — a real causal measurement, not a labeling
artifact.

**Event volume, same window/index**: `EDLI_REDECISION_PENDING=226-228`,
`DAY0_EXTREME_UPDATED=888`, `FORECAST_SNAPSHOT_READY=508`. Forecast/Day0 volume
outnumbers price-driven redecision ~6:1, consistent with price-triggered work losing
arbitration to the higher-volume forecast lane in stage 5.

**Receipt-store timing query** (log, not SQL):
```
grep -o 'elapsed_s=[0-9.]*' after grep 'global auction receipt store completed' logs/zeus-live.log
-- n=3263, min=0.007 p50=0.516 p90=0.966 p99=2.300 max=21.313 mean=0.569
```

**What is NOT computable / NOT instrumented — say so explicitly:**
- `execution_feasibility_evidence.latency_ms/order_intent_time/submit_time`
  (`state/zeus_trades.db`) — schema exists with a dedicated `latency_ms INTEGER` column
  and 3 supporting indexes, but **0 of 47,220 rows in the last 24h have any of the three
  populated.** The column that would directly answer "quote-to-order latency" is dead
  in current code.
- `edli_live_order_events.occurred_at` (`state/zeus-world.db`) — the event-sourced order
  lifecycle (`DecisionProofAccepted -> SubmitPlanBuilt -> PreSubmitRevalidated ->
  LiveCapReserved -> ExecutionCommandCreated -> VenueSubmit*`) looks ideal for
  per-stage latency, but **every stage in every sampled aggregate shares one identical
  microsecond timestamp** — a single batch-write stamp applied at persist time, not
  real per-stage wall-clock. Internal reactor decision->submit latency (beyond stage 6
  above) is not measurable from this table as currently written.
- `venue_commands`/`venue_submission_envelopes` — legacy/dead tables (4 rows and 0 rows
  respectively), superseded by `edli_live_order_events`.
- `position_events.ENTRY_ORDER_POSTED` (`state/zeus_trades.db`, 1,698,304 rows total) is
  real (`src/contracts/position_truth.py:453`) but the only relevant index,
  `idx_position_events_entry_execution_occurred_at`, is a PARTIAL index whose `WHERE`
  clause covers `POSITION_OPEN_INTENT, ENTRY_ORDER_FILLED, ENTRY_ORDER_REJECTED,
  ENTRY_ORDER_VOIDED` and **deliberately excludes `ENTRY_ORDER_POSTED` itself** — a
  bare `event_type='ENTRY_ORDER_POSTED' AND occurred_at >= ...` scan took several
  minutes (no covering index) rather than the target's 120s bound. Ran it anyway
  (backgrounded, not polled) and it returned **count=0** for the quiet window —
  consistent with the 5 sampled `DecisionProofAccepted` aggregates in
  `edli_live_order_events` for the same window, all of which ended `SubmitRejected`,
  not `VenueSubmitAcknowledged`. Zero completed entries today, not a hidden data gap —
  but the missing index is real and independently worth fixing (add
  `ENTRY_ORDER_POSTED` to the partial index's `WHERE` list; cheap, additive, matches
  the existing index shape exactly).
- `opportunity_events.created_at` has no supporting index (only
  `(event_type, available_at)` and other event_type-scoped indexes) — a naive
  `created_at >=` scan on this 100GB table took over 2 minutes; use `available_at` with
  an `event_type` filter instead, as done above.

## 6. Ranked fix list

1. **[HIGH, code fix]** Move `_PriceChannelRedecisionSink.__call__` off the WS asyncio
   event-loop thread (`asyncio.to_thread`/executor), and replace the "retry only on the
   next quote for this exact token" debounce with a timer-based retry, so a slow or
   contended screen never stalls ingestion of unrelated tokens' quotes and a quiet
   family's pending redecision doesn't starve behind writer contention indefinitely.
   Directly measured: p50=592s, p90=2057s, max=5932s on exactly the probability-flip
   path — 2-4 orders of magnitude slower than the 90s screen-cadence assumption baked
   into the code comment.
2. **[HIGH, code fix, ~59ms+ per decision, likely more]** Replace `zlib.compress(level=9)`
   +base64 in `global_batch_runtime.py:2361,2431,2478,3057,3110,3196` with zstd-3 raw
   BLOB (measured: 65.0ms -> 5.7ms, 46% smaller, zero information loss on a real
   1.99MB payload). This is synchronous and pre-submit (§3) with a measured p50=516ms /
   p99=2.3s for the whole receipt-store operation it's embedded in. The codec swap
   captures part of that; the rest needs profiling `_store_global_auction_receipt`'s DB
   reads and delta-compute (see §3, UNVERIFIED split).
3. **[MEDIUM, config, cheap]** `reuse_read_connections=True` already exists in
   `_PriceChannelRedecisionSink` but is wired `False` at every live call site
   (`price_channel_ingest.py:5012,5415,6437`). Flipping it removes 3 fresh
   connection-opens per redecision call — low risk, the code path already exists.
4. **[MEDIUM, instrumentation, prerequisite for further work]** Populate
   `execution_feasibility_evidence.latency_ms/order_intent_time/submit_time` and make
   `edli_live_order_events.occurred_at` a real per-stage timestamp instead of one
   batch-write stamp. Also add `ENTRY_ORDER_POSTED` to
   `idx_position_events_entry_execution_occurred_at`'s WHERE list. Right now nobody can
   measure decision->submit or submit->ack latency from stored data outside the one
   receipt-store log line found in §3.
5. **[LOW, verify only]** GATE #84 keepalive (cold TLS 2.2-2.7s) and the per-event
   WAL-lock commit fix (previously up to 330s single-txn hold) both look already fixed
   in code with clear commit-dated comments; not re-proposing, just flagging as worth a
   live confidence check once entry volume picks up (today's quiet window had zero
   completed entries — see §5).

## OUR latency vs. PROVIDER latency

Everything above is OUR pipeline (redecision-router design, receipt-store design), not
venue/provider dissemination lag. The WS push itself (stage 1) is provider-controlled
and out of scope for a code fix; no provider-side latency claim is made here, and no
"faster observation feed" is re-proposed (already refuted per prior audit — aviationweather
was already 2-3 min faster than the alternative considered).
