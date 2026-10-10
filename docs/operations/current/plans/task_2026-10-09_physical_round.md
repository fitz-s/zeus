# Physical-current round: bounded WORLD write, reseed off the round

Branch: `fix/physical-round-bounded-write-batched-enqueue` (from origin/live 9ebbd044d).
Status: IMPLEMENTED on branch (see Implementation). Rollback point: 140aae290 (anchor). Code-only; no schema, registry or config change.

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

For the in-memory key (corrected 2026-10-10, see Implementation note):
- Add: the round thread bumps the key's generation under the lock, after the WORLD commit.
  The pending set is a dict key -> generation.
- Take: the worker snapshots `{key: generation}` under the lock and does not clear it.
- Discard: under the lock, the worker deletes only snapshot keys whose current generation
  equals the snapshot's, and only after a non-failsoft report. A key whose generation
  advanced stays, and the worker loops into another batch.

The earlier argument that a key re-added during the batch is covered by the batch was
wrong. `computed_at` is fixed at batch start, and the enqueue reads WORLD at some point
inside the call; a print committed after that read is newer than what the batch saw, yet
the round's re-add finds the live worker thread (no new thread) and the old batch then
discarded the key. That second revision missed the latency path until the reconcile pass.
The generation makes "re-added since the snapshot" observable instead of assumed.

## Implementation (2026-10-10)

Commits: 219d33c9c (worker), d06efd0ff (bounded write), bbbd76541 (existing tests follow the
deferral), 4c61dd1a0 (new tests).

What changed, all in `src/ingest_main.py` plus one pass-through in `src/state/db.py`:
- `_physical_current_reseed_worker` (~2156) and `_defer_physical_current_reseed` (~2209):
  one lazily created daemon thread `physical-current-reseed`. Each loop snapshots
  `_physical_current_pending_wakes` under `_physical_current_reseed_lock` without
  clearing, makes one `_enqueue_fusion_upgrade_reseeds_if_needed` call over
  `current_temperature_delivery_scopes` of the batch's cities, and discards only the
  snapshot's keys after a non-failsoft report. Failsoft, `None` report or an exception
  keeps every key and ends the worker (thread slot cleared); the next committed round
  re-signals and `reconcile_current_temperature_delivery` is the backstop. Not reusing
  `_defer_anchor_residual_reseed`, whose failure path drops the batch.
- `_day0_current_temperature_source_tick`: the inline enqueue is gone. After commit it adds
  the key under the lock and returns `enqueue_status=DEFERRED_TO_RESEED_WORKER`;
  `world_to_enqueue_return_ms` is removed from PHYSICAL_CURRENT_CHAIN_TRACE.
  PHYSICAL_CURRENT_REDECISION_SEED is now one line per batch;
  PHYSICAL_CURRENT_RESEED_BATCH_TRACE (new) carries `enqueue_return_ms`, routes, scopes,
  status. A worker exception logs PHYSICAL_CURRENT_RESEED_FAILED.
- Write bound: `_PHYSICAL_CURRENT_WRITE_BUDGET_S = 0.3` starts a deadline before the mutex
  wait; mutex timeout, lease `deadline_ms`, `busy_timeout_ms`, the post-connect PRAGMA, and
  a new `deadline_monotonic` on `get_world_connection` (already supported by `_connect`,
  which bounds the `PRAGMA journal_mode=WAL` wait) are each the time remaining. Expiry
  raises inside the existing try and returns WRITE_DEFERRED; before, mutex 0.1 s + lease
  200 ms + 100 ms busy let connect wait up to 30 s. asos5 and G10 savepoints untouched.
- Shutdown: the worker is a daemon thread and nothing joins it.

Tests: `tests/data/test_physical_round_worker.py`, 8 tests, all 8 ERROR at 9ebbd044d
(no `_physical_current_reseed_thread`; the budget test also needs the new constant) and
8 pass now: no inline enqueue; round returns while reseed blocks; key added during a batch
survives to the next batch; exception keeps all keys; failsoft keeps all keys and the next
signal retries; one enqueue over the union of scopes; budget expiry returns
WRITE_DEFERRED and the retry commits once, with a repeat inserting 0; one budget bounds
mutex, lease and connect. Existing tests that asserted the inline enqueue
(`test_fmi_airport_temperature.py` helsinki, `test_station_temperature_adapters.py`
reseeds_after_durable_world_commit) now join the worker and assert DEFERRED status.

Related modules (asos5_ingest, scheduler_adapter, fmi_airport_temperature,
station_temperature_adapters, fast_obs_receipt_chain, observation_reaction_chain,
page_print_absence_record):
- 9ebbd044d: 13 failed, 348 passed. This branch: 13 failed, 348 passed (361 tests; the 8 new tests
  in the new file, which are not in this count).
- Failure set diff by test id: identical, 0 NEW failures. The 13 are pre-existing:
  test_alias_cities_share_one_provider_fetch_even_when_it_fails (1),
  fmi helsinki[False] (1, base fails on `assert 5 == 1` in its enqueue stub),
  observation_reaction_chain materializes_then_serves (4), scheduler_adapter (7).

Residual risk:
- A worker whose batch is failsoft or raises exits; retry waits for the next advanced
  round on any route or the reconcile pass (p50 91 s, p90 291 s apart on 10-09), not a
  timer. The set is in memory, so a process loss relies on the reconcile backstop.
- A 0.3 s total budget is tighter than the old 0.1 + 0.2 sequence only in the worst case;
  expect WRITE_DEFERRED counts to move, not to rise materially. Check after deploy.
- Not measured live; replay estimate remains p90 round 21 s.
- A city missing from `runtime_cities_by_name` yields no scopes for its keys; the enqueue
  is then called with an empty tuple, which was not exercised in tests.
- Pytest here needs `config/settings.json` (gitignored); it was copied from the main tree
  into the worktree and is not committed.

### Note 2026-10-10: mid-batch re-add

Review found the key-loss above. Fixed in `_mark_physical_current_wake` and the worker's
discard step in `src/ingest_main.py`; `_physical_current_pending_wakes` is now
`dict[key, int]`. New test `test_key_re_added_during_its_own_batch_gets_a_second_enqueue`
fails with discard-all and passes with the generation check. Related modules after the fix:
13 failed, 357 passed (9 tests in the worker file), failure set identical to 9ebbd044d,
0 NEW failures.
