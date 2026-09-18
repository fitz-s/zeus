# Redecision queue — the fix is already deployed; the delay is elsewhere (2026-09-17)

This supersedes the mechanism claimed in `storage_and_latency_verdict_2026-09-17.md` §6
and in the latency audit. The measured delay is real, but **every root cause attributed
to it was wrong**, and the consult's recommended design is already in production.

## What was claimed vs what is true

| Claim | Verdict |
|---|---|
| Sink runs synchronously on the WS asyncio event loop | **FALSE for the live path** |
| Opens 3 fresh SQLite connections per call (`reuse_read_connections=False`) | **FALSE for the live path** |
| Retries only when the next quote for that token arrives, no timer | **FALSE** — timer + exponential backoff exists |
| `SnapshotChanged` silently drops work | **FALSE** — 0 occurrences in the live log |
| p50 is ~225s | Partly — see corrected numbers below |

### The live daemon wires the off-loop coalescing sink

`price_channel_daemon.py:593,670` runs `_edli_market_channel_ingestor_cycle`, which at
`price_channel_ingest.py:6438` wires `_edli_coalesced_price_channel_redecision_sink()`
with `market_event_sink_independently_coordinated=True`. That factory
(`price_channel_redecision_router.py:1392`) wraps the sink in
`_CoalescingPriceChannelRedecisionSink` — whose docstring is literally *"Keep decision
routing off the WS receive loop and retain latest per token"* — **and** passes
`reuse_read_connections=True`.

The worker (`:1299-1375`) already implements what the consult asked for: a dedicated
thread, a leading-edge batch window, **timer-driven retry with exponential backoff**
(`delay = min(30.0, 2 ** min(failures-1, 5))`, `time.sleep(delay)` — independent of quote
arrival), supersession of superseded keys by newer ones, and idle exit.

The blocking sink I traced (`_edli_price_channel_redecision_sink` at
`price_channel_ingest.py:5012,5415`) is a **different call site** —
`_edli_refresh_held_position_quote_evidence`, a held-position evidence refresh, not the
continuous websocket lane. I attributed one call site's properties to another.

## Corrected measurements

Grouping by `(available_at, entity_key)` — my earlier grouping by `available_at` alone
collapsed distinct tokens that share a timestamp:

```
distinct (observation, token) work items today: 508
time-to-first-durable-queue:
  min 3.7s   p50 105.4s   p90 772.3s   p99 2306.9s   max 5571.3s
rows per work item: max 40, mean 2.65
```

So p50 is **105s**, not 225s. And the per-hour split shows the shape plainly:

| hour (UTC) | n | p50 |
|---|---|---|
| 02:00 | 94 | **40.2s** |
| 03:00 | 62 | **24.2s** |
| 07:00 | 3 | **2098.3s** |
| 12:00 | 1 | **3078.2s** |

**Delay is inversely proportional to volume.** Busy hours are fast (24-40s); quiet hours
are slow (35-51 min). The sink is not the bottleneck — when work arrives, it drains in
seconds (consecutive "buckets held" log lines fire 1-2s apart).

## The real mechanism, and a much more consequential finding

`_edli_price_channel_redecision_events_for_events:892-895`: if a family already has a
pending redecision row, further quotes for it are skipped by design (the entity-key
debounce bounds the lane to one pending row per family). So a family's next price move
cannot queue until the reactor drains the current one — the delay is **reactor drain
latency in quiet periods**, not sink latency.

More important, from 1,761 live log lines of
`EDLI price-channel redecision buckets held=N screened=M resting=R`:

```
screened=0   in 1,761 of 1,761 lines   (100%)
held=1 (1268), held=2 (163), held=0 (145), held=3 (122), held=4 (56)
```

**The price-driven redecision lane has never produced a single newly-screened entry
candidate.** It only ever re-evaluates positions already held (`held=N` tracks the 5
currently-open positions).

### Why — and it is NOT a swallowed exception

`_edli_screened_entry_family_keys_for_price_channel` (`:321`) contains **seven bare
`except: return set()`** handlers, so any failure is indistinguishable from "no
candidates" and logs nothing. That is a genuine structural defect (a designed decline
must name its branch). But probing the internals with the excepts removed:

```
step 1 import continuous_redecision symbols  -> OK
step 2 _all_latest_beliefs(forecast_only_admissible=True) -> 5 beliefs
         London/Dallas/Hong Kong/Jeddah 2026-09-19 low, Seattle 2026-09-18 low
step 3 screen_entry_redecisions(min_edge=0.01) -> 0 redecisions
```

So `screened=0` is a **legitimate economic result**: beliefs exist, but nothing clears a
1-cent edge. Not a wiring bug. This is consistent with the standing finding that belief
lead is not executable edge (k*|dq| ~ 0.2c against a ~2c spread).

## What to actually do

1. **Do NOT rewrite the redecision sink.** The design is already correct and deployed.
   Re-check this document before anyone "fixes" it from the earlier reports.
2. **Instrument the drain, not the enqueue.** The quiet-hour delay is the reactor not
   draining a pending row promptly. Measure `EDLI_REDECISION_PENDING` row age at the
   point the reactor claims it, split by feed volume.
3. **Fix the seven bare excepts** in `_edli_screened_entry_family_keys_for_price_channel`
   so a future failure is distinguishable from an honest zero. It is currently only
   *lucky* that the answer is honest — nothing would have told us otherwise. Each decline
   branch should emit its own tag.
4. **`screened=0` is the standing question, not a bug to patch here.** The lane is
   economically inert by construction while no candidate clears 1c. That belongs to the
   edge/alpha work, not the latency work.

## Standing latency order, revised

| Target | Measured | Status |
|---|---|---|
| Pre-submit receipt store | p50 516ms, p99 2.3s, before every submit | **real, unfixed** |
| Quiet-hour redecision drain | p50 105s (24s busy / 2098s quiet) | real, but reactor-side |
| Redecision sink design | — | **already fixed in production** |
| ECMWF pipeline | ~2.3 min vs 400+ min provider lag | not worth work |

The pre-submit 516ms is now the highest-confidence *fixable* latency target.
