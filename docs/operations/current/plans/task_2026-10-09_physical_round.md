# Physical-current round: bounded WORLD write, reseed off the round

Branch: `fix/physical-round-bounded-write-batched-enqueue` (from origin/live 9ebbd044d).
Status: IN PROGRESS. Rollback point: 140aae290 (anchor). Code-only; no schema, registry or config change.

## Goal and acceptance

The `physical_current_db` round (`_day0_fmi_temperature_tick`) sets the fetch cadence
for every physical-current route, WRH/asos5 included. Inside
`_day0_current_temperature_source_tick` it runs
`_enqueue_fusion_upgrade_reseeds_if_needed` synchronously, once per advanced route.
Its WORLD write also has no bound on the connect-time wait.

Acceptance:
- The round makes no inline enqueue call. A single daemon thread, created lazily,
  drains `_physical_current_pending_wakes` under a lock. Each wake makes one call over
  the union of `current_temperature_delivery_scopes` for the pending cities.
- Keys are discarded only on a non-failsoft report. A key added during a batch goes
  into the next batch. If the batch raises, every key is kept and the next signal
  retries.
- Log lines:
  - PHYSICAL_CURRENT_REDECISION_SEED: one line per batch.
  - PHYSICAL_CURRENT_CHAIN_TRACE: one line per route at commit. It carries
    `enqueue_status=DEFERRED_TO_RESEED_WORKER` and no `world_to_enqueue_return_ms`.
  - PHYSICAL_CURRENT_RESEED_BATCH_TRACE (new): carries the batch's enqueue return time.
- WORLD write: one budget covers the mutex wait, the lease deadline and the connect
  busy timeout, each set to the time remaining. Expiry returns WRITE_DEFERRED, and the
  next tick commits with no duplicates.
- Shutdown never waits on the worker.

## Measured facts (previous agent, 10-09 10:00-19:59 CDT, 463 rounds; scratchpad pr/)

Round duration: p50 1.0 s, p90 59 s, p99 443 s, max 1189 s. About 93% of round time
is the inline reseed. One batched call costs f = 0.6-0.87 of the equivalent single
calls (replay_16city.jsonl), so batching alone saves little.

Latency per advanced route, from round start to enqueue return, at f = 0.6 (seconds):

| placement | p50 | p90 | max |
|---|---|---|---|
| current | 80 | 496 | 1189 |
| batch at round end | 112 | 702 | 748 |
| background worker | 58 | 513 | 664 |
| delivery-job drain | 208 | 746 | 1142 |

In replay with a background worker, round duration drops to p90 21 s, p99 50 s,
max 86 s.

## Provenance verdicts

- `src/ingest_main.py` `_day0_current_temperature_source_tick`: STALE_REWRITE, local.
  Introduced in fabbf0e94 (2026-09-27) and 38e728941 (2026-09-29); last changed in
  248cc37c2 (2026-10-09).
  - Current and sound: one WORLD connection, INV-37 holds, `append_print` is idempotent
    on identity, and the asos5 and G10 savepoints are current law.
  - Drift 1: it assumes the scoped reseed is cheap enough to run inline. The
    measurements refute that.
  - Drift 2: it assumes `busy_timeout=100` bounds the write. It does not bound connect:
    `_connect` can wait up to the 30 s default on `PRAGMA journal_mode=WAL`.
  - Live .err has 1998 `PHYSICAL_CURRENT_WRITE_DEFERRED error=OperationalError` lines
    and 395 `error=WriteLeaseTimeout` lines. Deferral is an existing, frequent and
    lossless path.
- `_physical_current_pending_wakes` (38e728941): CURRENT_REUSABLE. It is an in-memory
  retry set, now shared by two threads under a lock.
- `src/data/physical_current_delivery.py` (295d8e458 and 103bf5c7f, 2026-09-30):
  CURRENT_REUSABLE, not modified. `reconcile_current_temperature_delivery` rebuilds
  the debt from WORLD observations against consumed posterior provenance, never from an
  ack flag. It is the durable backstop (below).
- METAR `_commit_pending_day0_metar` (ingest_main ~775-830): CURRENT_REUSABLE as the
  shape to mirror. It does not pass `busy_timeout_ms` to connect; the physical tick
  does.
- `_defer_anchor_residual_reseed` (ingest_main ~4497): the precedent for a lazily
  created daemon reseed thread (lock, set, one thread that exits when the set is
  empty). Its shape is followed but the function is not reused, because its failure
  path drops the batch, which the retry rule here forbids.

## Durable backstop (exactly-once argument)

`_current_temperature_delivery_tick` is scheduled on `forecast_repair_db` every 1 s,
coalesced. On 10-09 its starts were p50 91 s apart, p90 291 s, max 837 s. It calls
`reconcile_current_temperature_delivery`, which runs the same enqueue with
`changed_sources=("day0_current_temperature_state",)` over at most 12 scopes per pass.
The scopes are taken round-robin from every runtime city's current scopes plus the
held and resting families.

`scope_capture_offers_larger_provider_set` compares the current-temperature identity in
WORLD with the identity the latest posterior consumed. So a committed print that is not
yet consumed is re-detected after a restart or a lost in-memory key, without any
in-memory state. The in-memory set is the latency path, not the correctness path. If
the worker or the process is lost, the reseed is delayed by at most one round-robin
sweep, and no revision is lost.

At the enqueue layer, exactly-once is the trigger's own property: it reserves seeds by
semantic transition key, and a duplicate reports `already_enqueued`. A key enqueued by
both the worker and the reconcile pass therefore yields one seed.

For the in-memory key:
- Add: the round thread adds it under the lock, after the WORLD commit.
- Take: the worker snapshots the set under the lock and does not clear it.
- Discard: under the lock, the worker removes only the snapshot's keys, and only after
  a non-failsoft report.

A key that was re-added during the batch was already in the snapshot, so a successful
batch discards it. That is correct: the enqueue's `computed_at` is taken after the
snapshot, so the batch read WORLD after that commit and covers the newer print. A key
first added during the batch is not in the snapshot, so it survives into the next
batch.

## Next action

Implement the worker and the bounded write, then the tests, the failure-set diff and
the metrics script.
