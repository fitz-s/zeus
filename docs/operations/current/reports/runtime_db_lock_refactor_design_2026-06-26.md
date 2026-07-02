# Runtime DB Lock Contention Refactor Report

Date: 2026-06-26
Scope: Zeus runtime SQLite lock contention across `zeus-world.db`,
`zeus_trades.db`, and `zeus-forecasts.db`.
Status: design report and refactor requirements, not an implemented fix.

## Executive Summary

The current lock failures are not a single bug. They are a structural mismatch
between the runtime write graph and the locking model.

Zeus currently has three large WAL-mode SQLite databases and many live sidecar
processes. The split across `world`, `trade`, and `forecast` databases reduces
some contention, but it does not make writes independent:

- WAL still permits only one writer per database at a time.
- Several live paths re-couple databases through `ATTACH`.
- `write_class="live"` is metadata, not a lock.
- Cross-process flock locks are optional and not uniformly enforced.
- `live` and `bulk` lock files are separate, so they do not serialize writers
  that ultimately target the same SQLite file.
- Price-channel correctness work made `world.opportunity_events` and
  `trades.execution_feasibility_evidence` travel through one attached
  connection, which improves paired write consistency but concentrates a large
  world+trade write unit behind the world mutex.
- Some write critical sections are bounded by item counts rather than wall time,
  SQLite page/index cost, WAL state, or downstream sink cost.

The ideal fix is not to raise busy timeouts or add one more retry loop. The
right refactor is a DB write authority layer: every write must enter through one
transaction envelope that declares its database set, write class, deadline,
hold-time budget, and failure policy. That layer should own lock acquisition,
`BEGIN IMMEDIATE`, commit/rollback, observability, and cross-DB ordering.

## Current Runtime Evidence

### Live Processes

The live process sample taken during the investigation showed these long-running
writers:

- `python -m src.riskguard.riskguard`
- `python -m src.ingest_main`
- `python -m src.ingest.forecast_live_daemon`
- `python -m src.control.heartbeat_supervisor`
- `python -m src.ingest.post_trade_capital_daemon`
- `python -m src.ingest.substrate_observer_daemon`
- `python -m src.ingest.price_channel_daemon`

At the same time, `com.zeus.live-trading` had no stable running PID. The launchd
job was repeatedly restarted, and the latest startup failure was a registry
assertion, not a DB lock:

`assert_db_matches_registry FAILED ... tail_stress_scenarios`

That is a separate live-readiness blocker and must not be conflated with the
SQLite lock root cause.

### Active Lock Holder

`lsof -nP +fg -- state/zeus-world.db.writer-lock.live` showed:

```text
Python 85057 ... R,W,LCK;CX ... state/zeus-world.db.writer-lock.live
```

The non-blocking flock probe showed only `state/zeus-world.db.writer-lock.live`
held; the forecast and trade live/bulk writer lock files were free at that
sample.

`/usr/bin/sample 85057` showed the `edli-market-channel` thread spending the
sample window in SQLite insertion:

```text
edli-market-channel
  pysqlite_connection_execute
    sqlite3_step
      sqlite3VdbeExec
        sqlite3BtreeInsert
```

This points to a long-running DB write section, not just a leaked flock file.

### Logs Showing Systemic Contention

Recent log families included:

- `logs/zeus-price-channel-ingest.err`: repeated market-channel REST seed budget
  exhaustion and `maximum number of running instances reached`.
- `logs/zeus-live.err`: `edli_command_recovery`, `exit_monitor`, Day0 emit, and
  redecision confirm-refresh hit `database is locked` before the live process
  later failed on registry mismatch.
- `logs/zeus-ingest.err`: `scripts.obs_live_tick` repeatedly retried and then
  failed in `src/data/observation_instants_writer.py` with
  `sqlite3.OperationalError: database is locked`.
- `logs/zeus-substrate-observer.err`: executable market substrate refresh often
  inserted no snapshots, with `failure_samples` containing `database is locked`.
- `logs/riskguard-live.err`: auxiliary bookkeeping repeatedly lost the
  `zeus_trades` write lock and skipped non-risk-degrading bookkeeping.
- `logs/zeus-forecast-live.err`: Bayesian fusion persistence encountered
  transient writer locks.
- `logs/zeus-post-trade-capital.err`: older collateral and chain canonical write
  paths also hit `database is locked`.

### WAL State

The runtime DBs were in WAL mode. Passive checkpoints returned `busy=0` for the
sampled databases, so the immediate symptom was not an obvious checkpoint floor
pin.

File sizes at the sample:

```text
64G  state/zeus-world.db
7M   state/zeus-world.db-wal
66G  state/zeus_trades.db
12M  state/zeus_trades.db-wal
37G  state/zeus-forecasts.db
260K state/zeus-forecasts.db-wal
```

The important point is size and index cost: even a numerically small batch can
take a long time when it updates large indexed append logs.

## SQLite Constraints That Bound The Design

SQLite WAL mode improves reader/writer coexistence, but it does not create
multi-writer concurrency. Official SQLite WAL documentation also says checkpoint
strategy is workload-dependent, and checkpoint starvation can occur when other
connections keep WAL state active.

Official `ATTACH DATABASE` documentation has a critical caveat for the current
Zeus shape: transactions spanning multiple attached databases are crash-atomic
only when the main database is not `:memory:` and `journal_mode` is not WAL. In
WAL mode, each individual database file remains atomic, but a host crash during
COMMIT can leave some attached database files changed and others unchanged.

That means the current source comment describing one WAL-mode attached
connection as a "single atomic commit" across `world` and `trades` is too strong.
It is transaction-consistent under normal process execution, but not crash-atomic
across database files in the strict SQLite sense.

Sources:

- SQLite `ATTACH DATABASE`: https://sqlite.org/lang_attach.html
- SQLite WAL: https://sqlite.org/wal.html
- SQLite locking and multi-file commit: https://sqlite.org/lockingv3.html

## Current Mechanism Map

### Base Connection Layer

`src/state/db.py::_connect` sets pragmas such as WAL mode, busy timeout, cache,
and mmap, and records write-class counters. Its own comment says the
`write_class` value is classification only; flock acquisition is reserved for
callers that wrap a block in `db_writer_lock`.

Implication: any code that opens `get_*_connection(write_class="live")` without
a surrounding writer lock is still relying on SQLite's own busy handling.

### Intent Locks

`src/state/db_writer_lock.py::db_writer_lock` uses `fcntl.flock` on sentinel
files:

- `state/zeus-world.db.writer-lock.live`
- `state/zeus-world.db.writer-lock.bulk`
- `state/zeus-forecasts.db.writer-lock.live`
- `state/zeus-forecasts.db.writer-lock.bulk`
- `state/zeus_trades.db.writer-lock.live`

These are advisory intent locks. They do not affect SQLite unless every writer
participates. They also do not serialize `live` versus `bulk` writers for the
same database because those are different lock files.

### World Mutex

`src/state/db.py::world_write_mutex` returns a guarded process-global mutex that
also acquires `state/zeus-world.db.writer-lock.live`. This was added because
process-local mutexes in separate launchd daemons did not coordinate.

This is directionally correct, but it is currently a special world-only
mechanism rather than a general DB write authority.

### Cross-DB Helpers

There are flocked helpers:

- `trade_connection_with_world_flocked`
- `world_connection_with_trades_flocked`

There are also required/optional attached helpers that open cross-DB connections
without holding the flock for the connection lifetime.

Price-channel intentionally uses `get_world_connection_with_trades_required`
for the forever-loop connection because holding cross-DB flock locks for the
daemon lifetime would starve every other writer. The code then relies on
`world_write_mutex` around per-unit writes.

### Price-Channel Write Unit

In `src/events/triggers/market_channel_ingestor.py`, websocket message handling
does this inside `with _world_mutex`:

1. Parse one raw websocket message into channel messages.
2. `handle_message` for each message.
3. Queue/coalesce market events.
4. `flush_coalesced(market_budget=100)`.
5. Commit the attached world+trades connection.

Each inserted market event writes:

- `world.opportunity_events`
- two `trades.execution_feasibility_evidence` rows for YES/NO directions when
  applicable
- possibly a redecision sink through `_notify_market_event_sink`

This is an overloaded critical section. The item budget does not account for DB
page cost, index cost, trade-table upsert cost, sink cost, or commit cost.

## Root Causes

### RC1: Lock Ownership Is Advisory And Optional

The repo has the right primitive (`db_writer_lock`), but not a mandatory write
contract. Writers can and do open connections with `write_class="live"` and
write without holding the corresponding intent lock.

### RC2: Database Split Is Invalidated By Attached Cross-DB Write Units

The split into world/trade/forecast is a useful ownership model. It is not a
concurrency boundary when a write unit touches more than one file. Current
price-channel writes couple world and trades; other helpers couple trade/world
or forecast/trade.

### RC3: The Live/Bulk Split Is A Policy Label, Not A Same-DB Mutex

Separate `live` and `bulk` lock files can prevent one class from blocking the
other only if the lower layer can actually run both. SQLite cannot run two
writers to the same DB file. If live and bulk both write one SQLite file, they
must still meet at a same-file write gate.

### RC4: Critical Sections Are Not Time-Bounded

The code often bounds batches by count. Runtime cost is determined by table
size, index count, page cache state, checkpoint state, and whether a sink does
more work. Count budgets are insufficient.

### RC5: Attached WAL Writes Are Overstated As Atomic Across Files

The price-channel comment says the single attached connection gives a single
atomic commit across world and trades. In SQLite WAL mode, the official
guarantee is weaker under host crash. This matters for live-money truth because
the current design rationale depends on that claim.

### RC6: Observability Is After-The-Fact

The runtime can show `database is locked`, but it does not consistently emit:

- which logical writer held the DB write gate
- which DB set was locked
- how long the write section held the lock
- rows/pages changed
- commit duration
- whether a sink ran inside the lock
- the last successful checkpoint horizon

Without this, every lock incident becomes forensic work.

### RC7: Current Tables Are Too Large For Hot Append Paths Without Partitioning

The hot path writes to large append/provenance tables. As the table and index
footprint grows, write latency becomes less predictable. WAL and retry logic do
not solve unbounded hot table growth.

## Ideal Design Requirements

### R1: One Write Authority API

All runtime writes must go through one write coordinator. Direct write-capable
connections should be private to the coordinator.

Proposed API shape:

```python
with db_write_transaction(
    dbs={DBIdentity.WORLD, DBIdentity.TRADE},
    write_class=WriteClass.LIVE,
    owner="price_channel.market_event_flush",
    deadline_ms=750,
    max_hold_ms=250,
    mode=WriteMode.IMMEDIATE,
) as tx:
    tx.world.execute(...)
    tx.trade.execute(...)
```

The coordinator owns:

- canonical DB ordering
- same-file writer serialization
- cross-DB lock acquisition
- `BEGIN IMMEDIATE`
- commit/rollback
- busy handling
- hold-time telemetry
- timeout/fail-closed policy
- optional chunk continuation

### R2: Lock Class Is Priority, Not Separate Mutual Exclusion

`LIVE` and `BULK` should become scheduler priority classes on a per-DB gate.
They must not be separate same-file mutexes. For the same SQLite file:

- live writers should have bounded priority
- bulk writers should yield when live is pending
- only one writer may enter SQLite at a time

### R3: Cross-DB Writes Must Declare A DB Set

Any write touching multiple DB files must declare the full DB set before the
transaction starts. The coordinator then acquires locks in canonical order.

No code should attach another DB after entering a write section without the
coordinator knowing it.

### R4: Attached WAL Is Not The Durability Boundary

For cross-DB invariants, choose one of these explicitly:

1. Accept normal-execution consistency but not host-crash atomicity, and add
   reconciliation/repair records.
2. Move paired truth that must be crash-atomic into one physical SQLite DB.
3. Use an outbox/saga pattern: write the authoritative event in one DB, then
   materialize the dependent table with idempotent repair.
4. Switch specific paired writes away from WAL to rollback journal mode only if
   the operational cost is acceptable. This is unlikely to be attractive for
   hot live paths.

For Zeus, the preferred target is option 3 for derived/witness rows and option 2
only for genuinely inseparable live-money facts.

### R5: No Network, Discovery, Or Planning Inside Write Gates

A write gate may include only:

- deterministic row construction already derived before entry
- SQL writes
- commit/rollback
- local telemetry emission that cannot block on SQLite

Forbidden inside a write gate:

- HTTP/CLOB/Gamma/WU calls
- model/probability computation
- market discovery
- redecision scans
- arbitrary sink callbacks
- checkpoint management

### R6: Time-Bounded Chunks

Every hot writer must have both:

- max rows/events per chunk
- max lock-hold time per chunk

When the time budget is exhausted, it must commit/rollback, release the gate,
and reschedule continuation.

### R7: Hot Event Streams Need Compaction Or Partitioning

Do not keep appending all high-frequency quote events to the same large indexed
world/trade tables forever. Market quote evidence should split into:

- latest quote state table keyed by token/direction
- short retention append log for audit
- slower archival/cold table if full history is required

The order pre-submit path should read the latest state table, not scan a massive
append log.

### R8: All Writers Must Be Inventory-Checked

CI should fail if runtime code writes to a canonical DB outside the coordinator.
This can start as a static grep allowlist and mature into AST checks.

### R9: Runtime Lock Telemetry Is Required Evidence

Every write transaction must emit at least:

- `owner`
- `db_set`
- `write_class`
- `wait_ms`
- `hold_ms`
- `commit_ms`
- `rows_changed`
- `busy_retry_count`
- `deadline_exceeded`

There should be a live endpoint or status file showing current/last writer per
DB and the top lock waiters.

## Target Architecture

### Component 1: `src/state/write_coordinator.py`

New module with:

- `DBIdentity` to file mapping
- `WriteClass` priority handling
- `WriteOwner` structured owner IDs
- `WriteLease` context manager
- per-DB local mutexes plus cross-process flock gates
- cross-DB canonical lock ordering
- `BEGIN IMMEDIATE` transaction start
- watchdog telemetry

The coordinator should be the only place allowed to open write-capable
connections for canonical runtime DBs.

### Component 2: Read-Only Default Connections

Existing `get_*_connection()` helpers should default to read-only or explicitly
query-only behavior for runtime callers. Write-capable connections should be
named loudly and restricted.

Example:

- `get_world_read_connection()`
- `get_trade_read_connection()`
- `db_write_transaction(...)`

Deprecate ambiguous `get_world_connection(write_class="live")` in runtime code.

### Component 3: Price-Channel Refactor

Price-channel should stop writing world event rows, trade evidence rows, and
redecision enqueue rows inside one overloaded callback.

Target flow:

1. Websocket thread parses and coalesces messages in memory only.
2. It emits bounded `MarketQuoteUpdate` records to a per-process queue.
3. A single writer loop drains the queue through `db_write_transaction`.
4. Each transaction writes at most one small chunk and has a hard hold-time cap.
5. Redecision trigger emission happens after commit using the committed token
   set, or through a separate outbox row consumed by the reactor.
6. `execution_feasibility_evidence` becomes derived/latest witness state, not a
   second synchronous table that must be written in the same critical section.

### Component 4: Outbox For Cross-DB Derived Writes

For rows that need to appear in another DB but are derived from an authoritative
event, write an outbox item in the authoritative DB:

```text
world.market_quote_event
world.cross_db_outbox(target=trade.execution_feasibility_evidence, idempotency_key=...)
```

A separate repairable worker applies the outbox to trade with idempotent upsert.
The pre-submit gate should then require either:

- the derived witness row is fresh, or
- the outbox has been applied for the relevant event.

This gives explicit recovery semantics instead of pretending WAL+ATTACH gives
cross-file crash atomicity.

### Component 5: Forecast Ingest Writer Lane

Observation instant writes and Bayesian fusion writes should use the same
coordinator with forecast-only `LIVE` leases. The writer should batch by city and
time budget, not by a whole scheduler tick.

### Component 6: RiskGuard And Sidecar Bookkeeping

RiskGuard auxiliary bookkeeping should become either:

- best-effort outbox updates with clear telemetry, or
- coordinator-managed short writes with low max hold time.

It should not compete invisibly with attached trade writers.

### Component 7: Table Retention And Index Review

Add an explicit retention/partition plan for:

- `world.opportunity_events`
- `trades.execution_feasibility_evidence`
- forecast observation revision tables

The hot live read path should use compact latest-state tables. Append logs
should be audit/provenance surfaces with retention windows or cold archival.

## Refactor Work Plan

### Phase 0: Stop Misleading Claims And Restore Live Baseline

- Fix or quarantine `tail_stress_scenarios` registry mismatch so live-trading
  can boot.
- Correct source comments that claim WAL `ATTACH` gives strict cross-file atomic
  commit.
- Add temporary lock-holder telemetry around `world_write_mutex`.

### Phase 1: Build Coordinator Skeleton

- Add `write_coordinator.py`.
- Wrap one low-risk writer first.
- Add unit tests for canonical ordering, timeout, rollback, and telemetry.
- Add a static inventory of runtime DB write call sites.

### Phase 2: Migrate Price-Channel

- Move `_notify_market_event_sink` out of the world mutex.
- Replace `flush_coalesced(market_budget=100)` with time-budgeted chunks.
- Introduce `MarketQuoteUpdate` queue and a single writer loop.
- Choose outbox versus physical co-location for feasibility witness writes.

### Phase 3: Migrate Forecast And Trade Sidecars

- Move `obs_live_tick` forecast writes under forecast coordinator leases.
- Move Bayesian fusion persistence under coordinator leases.
- Move substrate observer and riskguard writes under coordinator leases.

### Phase 4: Enforce The Contract

- Make direct runtime writes outside coordinator fail tests.
- Add metrics/status surfaces for current writer and recent contention.
- Add a lock-stress test with concurrent price-channel, obs ingest,
  substrate observer, and riskguard writers.

### Phase 5: Reduce Hot Table Pressure

- Add latest-state quote witness tables.
- Backfill from append logs.
- Move hot readers to latest-state tables.
- Add retention/archive policy for high-frequency append rows.

## Non-Goals

- Do not solve this by raising `busy_timeout`.
- Do not add retries around every `database is locked`.
- Do not rely on launchd restart loops as recovery.
- Do not hold cross-DB flocks for daemon lifetimes.
- Do not make a second local mutex per process and call it global.
- Do not keep adding attached DB helpers without a declared DB-set lease.

## Acceptance Criteria

The refactor is done only when:

- No runtime source write path can write a canonical DB without the coordinator.
- Price-channel no longer holds world lock while invoking redecision sinks.
- Cross-DB writes declare the DB set before acquiring a lease.
- Lock telemetry identifies the holder and waiters during contention.
- A concurrent runtime stress test completes without unbounded
  `database is locked` cascades.
- WAL checkpoint logs remain healthy under the stress scenario.
- The design explicitly documents which cross-DB invariants are crash-atomic,
  normally consistent, or repairable through outbox.

## CodexPro Run Result

CodexPro local bridge was started through `/Users/leofitz/.local/bin/codexpro-zeus`
in handoff mode. Because CodexPro is a bridge and not a model, the direct CLI
does not itself return a ChatGPT Pro answer. The usable fallback path is to
generate a `.ai-bridge/pro-context.md` bundle for a Pro-capable planning model,
or to run a local handoff executor.

Artifacts produced for this review:

- `.ai-bridge/runtime-db-lock-refactor/current-plan.md`
- `.ai-bridge/pro-context.md` at 176991 bytes, not truncated
- `.ai-bridge/runtime-db-lock-refactor/agent-status.md`
- `.ai-bridge/runtime-db-lock-refactor/execution-log.jsonl`
- `.ai-bridge/runtime-db-lock-refactor/implementation-diff.patch`

The local `codexpro execute-handoff --agent codex` run exited 0 after 97267 ms,
but it did not create the requested
`.ai-bridge/runtime-db-lock-refactor/codexpro-review.md` artifact. Its captured
stdout was empty and the status file is primarily session, hook, and command
log output. The generated `implementation-diff.patch` should not be treated as a
task patch; it captures the current worktree diff surface rather than an
accepted implementation for this refactor.

Therefore the CodexPro handoff artifact itself should be treated as an
unsuccessful external review attempt, not as a completed Pro verdict. Any
external Pro verdict must be captured separately and treated as advisory until
each claim is rechecked against current source and runtime evidence.

## ChatGPT Pro Consult Result

After the CodexPro handoff run failed to produce a review artifact, a
`chatgpt-pro-consult` run was completed through the visible ChatGPT Claude Code
Project using the normal Chrome bridge. The file-upload path was blocked by a
file chooser timeout, so the final submission used a text-only evidence pack:
the consult prompt, the full report, and selected source excerpts from the
runtime write paths.

Conversation:

`https://chatgpt.com/g/g-p-6a2990f77bdc81919f9702e3cb6ae20d-claude-code/c/6a3ec942-0808-83ea-b307-82173e8cdb4c`

Captured answer:

`.ai-bridge/runtime-db-lock-refactor/chatgpt-pro-consult/answer_REQ-20260626-132906-myba1mxy.md`

The consult verdict was:

- `CONDITIONAL GO`, confidence `0.78`, for the architecture direction.
- `NO-GO` for claiming the current design already eliminates runtime DB lock
  failures.

Material changes requested by the consult:

- Make writer coverage mandatory. The coordinator design is only sufficient if
  no runtime source path can execute canonical DB write SQL outside it.
- Replace same-file `LIVE`/`BULK` lock separation with one per-DB gate plus
  priority/queueing metadata.
- Move price-channel earlier than a low-risk pilot because the sampled lock
  holder was in that path and it couples world+trade writes.
- Move `_notify_market_event_sink` and any reentrant sink work after commit or
  behind outbox processing.
- Treat substrate observer as a high-risk hidden trade writer because current
  excerpts show `PolymarketClient` work under a trade writer lock.
- Treat observation writers and RiskGuard auxiliary writes as required migration
  targets, not follow-up cleanup.
- Add checkpoint/long-reader telemetry; a passive checkpoint sample with
  `busy=0` is not enough to rule out checkpoint or reader-driven commit stalls.
- Move hot latest-state/retention work earlier because serialization alone does
  not bound write latency on 64G+ indexed append surfaces.

The consult also strengthened the preferred completion criterion: the goal
should be "no unbounded or unattributed runtime writer contention, no hidden
cross-DB write units, and no live-money decision depending on unrepairable
WAL-attached cross-file atomicity", not "SQLite never returns `SQLITE_BUSY`".

## Implementation Status

First implementation slice completed on 2026-06-26:

- Added `src/state/write_coordinator.py` as the Phase 0 coordinator skeleton.
  It provides a unified per-DB writer gate shared by LIVE and BULK classes,
  canonical path-ordered multi-DB leases, telemetry, and single-DB
  `BEGIN IMMEDIATE` transaction wrapping.
- Explicitly rejected fake independent multi-DB transactions in the coordinator;
  cross-DB work must later use a single attached connection with explicit schema
  ownership or a durable outbox/repair path.
- Added `tests/state/test_write_coordinator.py` antibodies for same-file
  LIVE/BULK serialization, canonical multi-DB lease ordering, single-DB
  commit/rollback telemetry, and multi-DB transaction rejection.
- Corrected misleading WAL/ATTACH comments in the price-channel and state DB
  helpers so they no longer claim cross-file host-crash atomicity.
- Registered the new source and tests in `architecture/module_manifest.yaml`,
  `architecture/source_rationale.yaml`, `architecture/test_topology.yaml`,
  `src/state/AGENTS.md`, and `docs/reference/modules/state.md`.
- Migrated the price-channel held/candidate REST seed chunk writes and forever
  market-channel write loop to the unified `world+trade` coordinator lease
  before entering the existing world mutex.
- Moved market-event sink execution out of the write+commit critical section:
  seed/reconnect/websocket batches now defer sink calls until after commit, and
  post-commit sink failure is logged without rolling back already-persisted
  quote evidence.
- Added `trades.execution_feasibility_latest`, a compact latest-state mirror
  keyed by `(token_id, direction)`, and upsert it alongside append evidence.
  `_edli_order_token_ids_by_feasibility_age` now reads latest state with a
  bounded batch query and only falls back to the append log for missing tokens,
  removing the prior per-token `ORDER BY created_at DESC LIMIT 1` loop from the
  hot refresh path.
- Migrated executable snapshot refresh writers in the main daemon, P2 substrate
  observer, and P3 price-channel lane away from the legacy
  `db_writer_lock(_zeus_trade_db_path(), WriteClass.LIVE)` long-lock pattern.
  The outer substrate refresh locks still serialize refresh orchestration, but
  the durable `executable_market_snapshots` / `book_hash_transitions` write unit
  now enters a per-row `TRADE` coordinator lease through
  `snapshot_write_context_factory`.
- Added a low-level antibody proving the new persist context wraps
  `insert_snapshot`, optional book-hash transition, and `commit` together, with
  commit telemetry recorded on the coordinator lease. This keeps CLOB/network
  prefetch and candidate selection outside the trade writer lease and makes the
  intended lock-hold window milliseconds rather than refresh-duration seconds.
- Migrated the price-channel market-action snapshot invalidation path to the
  same `TRADE` coordinator lease, with commit duration and row-change telemetry.
- Added `trades.executable_market_snapshot_latest`, a compact latest-state
  mirror keyed by `(condition_id, selected_outcome_token_id)`. The immutable
  `executable_market_snapshots` append log remains the audit surface, while the
  latest mirror gives refresh-priority checks a bounded two-row read for YES/NO
  freshness instead of scanning condition history.
- Moved `_snapshot_condition_refresh_state` to read
  `executable_market_snapshot_latest` first and fall back to the append table
  when a legacy database has no mirror rows. Tests now prove latest upserts do
  not mutate the append log, out-of-order old snapshots do not overwrite newer
  latest state, and the hot refresh-priority path does not query
  `executable_market_snapshots` when latest rows exist.
- Moved `latest_snapshot_for_market` to resolve through the latest mirror first:
  it reads the newest fresh `snapshot_id` from
  `executable_market_snapshot_latest`, then hydrates the immutable append row by
  primary key. This keeps full snapshot provenance while avoiding
  `condition_id ORDER BY captured_at` history scans.
- Moved the runtime YES/NO freshness predicate in both the main daemon and the
  P2 substrate observer to read `executable_market_snapshot_latest` first. The
  legacy append query remains as a compatibility fallback, but current databases
  with latest rows avoid append scans for completed-condition pruning.
- Moved main-daemon token-to-condition resolution for cancelled escalation rests
  and open maker rests to read `executable_market_snapshot_latest` first. These
  screens still fall back to the append log for old databases, but current
  latest-backed reads avoid window functions and broad
  `selected_outcome_token_id/yes_token_id/no_token_id` scans on the append table.

Not completed in this slice: full production writer coverage, durable outbox
for post-commit sink retries, RiskGuard/observation writer migration, checkpoint
telemetry, retention/archive enforcement for high-frequency append rows, a static
runtime-writer inventory gate, or live runtime verification. Those remain
required before claiming the runtime lock problem is thoroughly solved.
