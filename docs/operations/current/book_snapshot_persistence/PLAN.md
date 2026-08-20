# book_snapshot_persistence -- Plan

Date: 2026-07-29 (redesigned same day after a deep-review NO-GO)
Branch: `claude/book-snapshot-persist`
Status: active

## 2026-07-29 redesign -- why the first version was replaced, not patched

The first version of this PR (commit `d0f69d155`) shipped a single
`family_book_snapshots` table, deduped by `UNIQUE(family_id, book_hash)`. An
independent deep review returned **NO-GO** with a verified, load-bearing bug:

> `compute_book_hash` (`src/execution/family_book.py:410`) hashes
> `captured_at_utc` directly into the digest. The live bridge
> (`src/engine/qkernel_spine_bridge.py:1787`) sets
> `captured_at_utc = decision_time` -- the reactor's own per-cycle clock, not
> a per-market capture instant. Every decision cycle therefore produces a
> DIFFERENT `book_hash` even for a byte-identical order book, so
> `UNIQUE(family_id, book_hash)` never deduplicates a live rebuild. The
> "dedup is the volume control" premise the whole design rested on was void:
> at >=60 cycles/hour x 51 families, the real write rate approached
> 73,440 rows/day of large JSON, not the near-zero the plan claimed.

I verified this myself by reading both cited lines before accepting the
redesign order (not by trusting the review's citation alone) -- see "STEP 0"
below for the same discipline applied to the review's own load-bearing claim
about `executable_market_snapshots` retention.

The review additionally found (full text: reviewer's report, sections 1-6):
BLOCKER -- synchronous JSON+SQLite on the live decision thread cannot bound
latency (the exception handler cannot undo elapsed WAL-writer-lock wait
time; repo default busy_timeout is 30s, SQLite WAL allows one writer at a
time); BLOCKER -- the table conflated an immutable state, a time-varying
decision observation, and telemetry I/O in one row with one hash serving
neither identity well; HIGH -- `market_center_c` silently computed a
"family-wide" center from a QUOTED SUBSET of bins, and the OLD test fixture
blessed exactly that; HIGH -- several actionability-retry veto paths reset
`_spine_fact_decision = None` before the old pre-branch hook, so a decision
that existed earlier in the same retry sequence could vanish from the
evidence population; HIGH -- `INSERT OR IGNORE` is statement-wide and can
mask a real integrity violation as a fake dedup hit; MEDIUM -- unbounded
append-only growth with no stated retention path; the hook-placement test
proved only source position, not runtime branch reachability.

This section documents the full redesign that closes every one of those
findings. Everything below "Problem" that is unchanged from the original
plan (the scout map, the hook site) is retained; everything about the table
design, the writer, and the tests is new.

## 2026-07-29 round-5 fixes -- CORRECT-BUT-INCOMPLETE -> converged (per
## reviewer: "after these five the existing X1/X3 implementations remain
## substantially unchanged")

Round-4 confirmed X1 (transaction poisoning) and X3 (per-observation
provenance) fully CLEARED, and the round-3 diagnostic-center fix needed no
further change. It found X2 only HALF-cleared: Stage 1 (frequent writes
isolated to a private spool) was sound, but Stage 2 (canonical delivery)
was not -- the "spool" was an unbounded, undeletable mirror of the
canonical schemas, replayed in FULL every 30 seconds; the worker's own
ingest pass opened a second, uncoordinated canonical writer that a normal
SQLite error could crash; and the production daemon never actually started
the worker at all. Five fixes, verified independently before implementing
(same discipline as every prior round):

**Y1 -- bounded outbox, not an unbounded mirror.** Verified the defect
myself: the round-4 spool reused `family_book_states`/
`family_book_observations` verbatim (append-only, no-delete triggers,
`SELECT *` with no `LIMIT`/watermark) -- it could structurally never
shrink. Replaced with a NEW, transport-specific, DELETABLE table
(`src/state/schema/family_book_telemetry_outbox_schema.py`,
`family_book_telemetry_outbox`), one compact row per sampled envelope, keyed
by an `AUTOINCREMENT spool_seq` (SQLite guarantees this is never reused
even across deletes, so `ORDER BY spool_seq` is a stable watermark). The
at-least-once outbox protocol: read a bounded batch (`ORDER BY spool_seq
LIMIT batch_size`, additionally capped by a byte budget summed as rows are
selected) -> insert into canonical in ONE transaction (targeted upserts,
idempotent) -> COMMIT canonical -> ONLY THEN `DELETE ... WHERE spool_seq <=
max_seq` in a SEPARATE spool transaction. A crash between the two commits
replays safely (idempotent). `pending_outbox_stats()` reports pending row
count, approximate bytes, and oldest-enqueued-age (Y1's required metrics).
A hard per-write disk budget (`spool_disk_budget_bytes`, default 500 MB)
stops CAPTURING (not just delivery) once exceeded, typed counter
`family_book_telemetry_spool_budget_exceeded_total`. Verified:
`TestBoundedOutbox` (ack-delete, idempotent repeated ingestion, a 5-row
backlog draining over multiple 2-row bounded passes -- never one unbounded
pass, pending-stats, disk budget).

**Y2 -- canonical connection hygiene.** Two paths violated "only guarded
ingest touches canonical," verified by re-reading my own round-4 code:
`_bootstrap_last_state_cache` opened a full read-write canonical connection
(WAL pragma etc.) outside any admission mechanism, and `_ingest_pass` ran
`_ensure_states_table`/`_ensure_observations_table` DDL BEFORE acquiring
its lock. **Fix**: bootstrap now seeds from the SPOOL's own pending rows
(`family_book_telemetry_outbox_schema.latest_per_family`, no canonical
touch needed at all) merged with a best-effort READ-ONLY canonical read
(`get_trade_connection_read_only()` -- no write_class, no DDL, fails soft
if canonical is unreachable). `run_bounded_ingest` (the new delivery
function, see Y3) contains ZERO DDL -- it assumes `init_schema_trade_only`
(the daemon's normal boot-time schema bootstrap, which runs long before any
scheduler job) already created the canonical tables, and fails closed
(typed counter, no crash) if they are somehow absent rather than mutating
schema on someone else's connection.

**Y3 -- live-write priority: delete the second writer, don't coordinate
it.** Verified directly in `src/state/db_writer_lock.py`: `WriteClass.LIVE`
and `BULK` are separate lock files with no mutual exclusion by themselves
(only `BulkChunker.yield_if_live_contended()` cooperatively bridges them,
and the primary `trade_conn` never took `WriteClass.LIVE`), so round-4's
standalone `db_writer_lock(BULK)` around the ingest pass gave the primary
no real priority. Per the review's preferred fix ("submit bounded ingest
work to the trade-DB owner and execute on the primary connection at a safe
post-live-write seam... a scheduler job in src/main.py that already owns
trade_conn"): `run_bounded_ingest(canonical_conn, spool_conn_factory, ...)`
is now a PURE function that takes the CALLER's own canonical connection --
this module never opens one. `src/main.py` registers a NEW APScheduler job,
`_family_book_telemetry_ingest_cycle` (`@_scheduler_job(...)`-wrapped, 30s
interval, `max_instances=1, coalesce=True`), that opens
`get_trade_connection(write_class="live")` -- the EXACT SAME pattern every
other periodic trade-DB touch in this daemon already uses (verified:
`_make_wal_checkpoint_cycle`'s WAL-checkpoint jobs, `_edli_bankroll_warm_cycle`,
etc. -- all open/close their own short-lived connection per tick, none hold
one persistently), calls `run_bounded_ingest`, closes. This removes the
standalone-second-writer class of risk entirely rather than adding a new
arbitration mechanism for it: the worker thread's spool writes and the
scheduler job's canonical deliveries are now two INDEPENDENT, already-
sanctioned daemon patterns, neither a novel construct.

**Y4 -- worker liveness + restart continuity.** Verified: my round-4
`_ingest_states`/`_ingest_observations` re-raised on any SQLite error with
no boundary around the call in `_worker_loop`, so an ordinary
SQLITE_BUSY/I/O/schema/commit failure would have crashed the sole worker
thread. Moot now that ingest runs on the daemon's scheduler job (Y3) --
`@_scheduler_job` never re-raises (fail-open, daemon keeps running) AND
`run_bounded_ingest` itself never raises (every failure mode returns a
typed `IngestOutcome`, with its own rollback-on-failure discipline). For
the CAPTURE side, `_process_one`'s body (row construction: hashing,
sampling, market-center, JSON) now sits in its OWN try/except separate from
X1's insert/commit try/except, so a malformed envelope (a bug upstream, not
a DB fault) is quarantined -- counted
(`family_book_telemetry_malformed_envelope_total`) and dropped -- rather
than propagating out of `_process_one` and killing the worker thread.
Verified: `test_malformed_envelope_is_quarantined_not_crashing` (a forced
exception in `compute_state_identity` is counted, and the NEXT well-formed
envelope still lands durably; the worker thread is still alive).
`_last_state_by_family` now seeds from max(canonical durable, PENDING
spool) -- `_bootstrap_last_state_cache` merges both sources by
`decision_time`, so a spool write that crashed before canonical ingestion
is still respected on restart. Verified by the review's exact required
regression test,
`test_restart_without_ingest_seeds_sampling_from_pending_spool`: write to
spool, do NOT ingest, shut down, restart against the same spool +
canonical DB, enqueue identical content one minute later -> sampled out
(not falsely relabeled STATE_CHANGE). The MEDIUM (replacement-connection
setup failure escaping `_rollback_or_replace`) is fixed: setup failure is
now caught and surfaced through the same path the caller already handles.
Ingest counters (`_CNT_INGESTED_STATES`/`_CNT_INGESTED_OBSERVATIONS`) are
accumulated locally during the batch and published via `_cnt_inc` ONLY
after `canonical_conn.commit()` succeeds, never inside the loop before
commit.

**Y5 -- production lifecycle + kill switch.** Verified: `rg -n
"start_worker\(" src` before this round showed zero production call sites
outside the module itself and tests -- the daemon never started the
worker, so the round-4 default-on capture plane would have silently filled
its queue once and then dropped every observation thereafter, forever.
**Fix**: `src/main.py`'s `main()` now calls `start_worker()` BEFORE
`_register_edli_live_jobs()` (before reactor activation), blocking for a
ready/failed handshake (readiness = sqlite version floor + spool opened +
schema ok + cache seeded; the worker thread signals a `threading.Event`
once all of that succeeds or fails). On failure, capture is disabled
TERMINALLY inside `start_worker()` itself (typed counter
`family_book_telemetry_startup_failed_total`; the decision thread's
`enqueue_family_book_observation` checks a module-level flag and never
retries) -- daemon boot itself never fails on this, since telemetry is
evidence-only, never decision authority. The ingest scheduler job is only
registered if the worker actually became ready. `shutdown()` is called from
the daemon's existing `except (KeyboardInterrupt, SystemExit):`
finalization block, after `scheduler.shutdown(wait=True)`. Kill switch
(`ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED`, default on) now stops BOTH halves,
not just enqueues: checked before `start_worker()` is even called (skips
starting the worker AND registering the ingest job), and re-checked on
EVERY `run_bounded_ingest()` invocation (returns `disabled=True`
immediately, no spool/canonical access at all) -- so flipping it off
mid-run halts canonical I/O on the very next scheduled tick, without
needing a daemon restart. Verified:
`test_disabled_env_var_stops_canonical_draining_too` (a pending spool
backlog is NOT delivered while disabled; delivers normally once
re-enabled).

**Shutdown sentinel (MEDIUM) + allowlist scoping (MEDIUM), also fixed:**
the round-4 blocking `queue.put(_STOP, timeout=...)` sentinel could itself
raise `queue.Full` if the queue stayed full while the worker was wedged --
replaced with a `threading.Event` the worker polls at 0.1s intervals (no
periodic ingest left in this loop to coordinate with anymore, so a short
poll is cheap and simplifies shutdown to a pure event-set + join, no queue
interaction at all). The `SQLITE_CONNECT_ALLOWLIST` entry
(`src/state/db_writer_lock.py`) is file-scoped by the antibody's own
design (it cannot be narrowed to a function), so a NEW dedicated AST test
(`TestSingleConnectAntibody`) compensates: asserts exactly one
`sqlite3.connect()` call exists anywhere in
`family_book_telemetry_writer.py`, that it is lexically inside
`_default_spool_conn_factory`, and (a second test) that the factory's
runtime `spool_path.resolve() != trade_db_path.resolve()` assertion holds
given the real path-construction logic.

## 2026-07-29 round-3 fixes -- CORRECT-BUT-NOT-YET-SAFE -> the remaining
## transaction/admission/provenance blockers

A second review of commit `69745d0cc` confirmed the redesign's data model
(dedup identity, state/observation split, q-vector evidence authority, the
tightened center estimator) as fully correct, but found three NEW blockers
introduced or exposed by that redesign, plus a set of HIGH/MEDIUM findings.
Verified each independently before fixing (same discipline as STEP 0):

**X1 -- transaction poisoning after a partial write.** `_write_observation`
did state INSERT -> observation INSERT -> COMMIT with no explicit rollback
on failure. A failing observation INSERT (default SQLite ABORT policy backs
out only the failing statement, not the whole transaction) or a failing
COMMIT could leave `conn.in_transaction` True indefinitely -- the worker
loops back to `queue.get()` still holding SQLite's sole writer lock.
**Fix**: `_process_one` now wraps the whole state+observation write in a
single try/except; on ANY failure it calls `_rollback_or_replace`, which
tries `conn.rollback()` first and, only if THAT also fails, closes the
connection and opens a fresh one (the only way to guarantee a clean slate).
Counters and the sampling cache (`_last_state_by_family`) are updated ONLY
after a durable `commit()` succeeds. Verified by
`TestTransactionSafety.test_observation_insert_failure_rolls_back_state_insert_too`
(asserts `conn.in_transaction is False`, zero durable rows, a separate
connection can write immediately) and two commit-failure tests
(`test_failed_commit_replaces_the_connection_and_no_later_observation_commits_stale_rows`
-- rollback alone recovers when only `commit()` is poisoned;
`test_rollback_failure_also_replaces_the_connection` -- when BOTH `commit()`
and `rollback()` fail, the connection is replaced).

**X2 -- writer admission (no real mutual exclusion with the primary).**
Verified directly in `src/state/db_writer_lock.py`: `WriteClass.LIVE` and
`WriteClass.BULK` are SEPARATE lock files (`.writer-lock.live` /
`.writer-lock.bulk`); taking `db_writer_lock(path, WriteClass.BULK)` alone
does not probe or yield to a LIVE holder by itself (only `BulkChunker`'s
cooperative `yield_if_live_contended()` does that, and the primary
`trade_conn` doesn't take `WriteClass.LIVE` at all -- confirmed with
team-lead research, Phase 1+ of the same plan). So round-2's per-observation
`db_writer_lock(BULK)` around the trade-DB write gave no real priority
guarantee to the primary. **Fix (the review's preferred realization)**:
ordinary observation writes no longer touch `zeus_trades.db` AT ALL. The
writer thread writes every observation to a PRIVATE spool SQLite file
(`family_book_telemetry_spool.db`, own file, own WAL, zero contention risk
by construction -- nothing else ever opens it) via
`_default_spool_conn_factory`. A periodic (every 30s, or forced via
`force_ingest()`) batched ingest pass -- `_ingest_pass` -- is the ONLY code
in the repo that opens a second connection to the trade DB; it copies spool
rows into the durable `family_book_states`/`family_book_observations`
tables under `db_writer_lock(trade_db_path, WriteClass.BULK,
blocking=False)`; contention there just defers to the next cycle (typed
counter `family_book_telemetry_ingest_contended_total`, no wait). This
dissolves the auto-checkpoint concern too (the primary's WAL checkpoint
cadence is no longer affected by continuous telemetry writes). Verified by
`TestSpoolArchitecture` (writes land in the spool, never the trade DB,
until an ingest pass runs; ingest is idempotent across repeated passes;
ingest contention skips without blocking) and
`TestNonblockingEnqueue.test_enqueue_never_blocks_under_held_wal_write_lock`
(unaffected by construction now, since ordinary writes never touch a shared
file at all).

**X3 -- per-observation provenance (first-seen snapshot identity silently
reused).** `family_book_states.canonical_payload` stored the FULL manifest
including `executable_snapshot_id`/`source_captured_at`, so when identical
content was captured under different snapshot IDs across cycles, the
shared state row permanently retained only the FIRST capture's identity/
time -- every later heartbeat or selected observation of that same content
pointed at stale provenance. **Fix**: `family_book_states.canonical_payload`
now carries ONLY content-identity fields (same subset as `content_hash`,
`_HASH_FIELDS` in `src/events/family_book_manifest.py`); per-bin
`executable_snapshot_id`/`source_captured_at` moved to a NEW
`source_manifest_json` column on `family_book_observations` --
`build_source_manifest(envelope)`, populated on EVERY observation from that
observation's OWN capture. Verified by
`TestComputeStateIdentity.test_canonical_payload_excludes_snapshot_identity_and_capture_time`,
`TestBuildSourceManifest.test_two_observations_of_identical_content_carry_distinct_source_manifests`,
and end-to-end by
`TestPerObservationProvenance.test_heartbeat_reobservation_carries_its_own_source_manifest`
(1 shared state, 2 observations 31 minutes apart with identical content but
DISTINCT `source_manifest_json` per row).

**H1 -- compact envelope (memory safety).** The original envelope held a
reference to the WHOLE `FamilyDecision` (and therefore, transitively,
`FamilyDecision.band.samples` -- a large NumPy draw matrix the writer never
even reads) for as long as the item sat queued; at the default 2,048-item
bound this is gigabytes of unrelated retained memory during writer
degradation. **Fix**: `src/events/family_book_manifest.py`
`project_observation_envelope` runs ON THE DECISION THREAD and extracts
ONLY the small scalars/mappings the writer needs (per-bin condition/token
ids, best bid/ask, tick/min-order/fee, snapshot identity; model/market q as
plain dicts; predictive mu/sigma/identity_hash; selected bin/side) into a
frozen `ObservationEnvelope` dataclass that holds NO reference back to
`FamilyDecision`/`family`/the proofs. Benchmarked at 51 bins:
`project_observation_envelope` p50=127us/p99=207us -- sub-millisecond, the
entire decision-thread cost including this projection.

**H2 -- hot-path purity.** `enqueue_family_book_observation` no longer
calls `_ensure_worker_started()` (an OS-thread-creation check) on every
call -- the worker is started ONCE, explicitly, by the daemon at init via
`start_worker()`; a dead/never-started worker is never resurrected from the
decision thread (items just queue up, bounded, until an operator restarts
it). The success path (the common case) touches no lock at all -- not even
the canonical counters sink; only the rare `queue.Full`/exception paths
route through `src.observability.counters.increment`.

**H3 -- sampling semantics.** `DECISION` renamed `PRE_VETO_SELECTED`
(`family_book_observations.sampling_reason` CHECK constraint) -- a
selection at the decision-production seam CAN still be vetoed by the three
downstream actionability checks in the same cycle, so this was never a
record of final/submitted status. Three new orthogonal boolean columns
(`state_changed`, `heartbeat_due`, `pre_veto_selected`) are persisted on
EVERY row regardless of which one "won" by sampling precedence (STATE_CHANGE
> HEARTBEAT > PRE_VETO_SELECTED) -- selection-triggered sampling is not
missing-at-random, so analysts must be able to stratify on it, not just see
the winning reason. `selected_bin_id`/`selected_side` (nullable) record
which candidate was pre-veto-selected, correlated from
`decision.candidate_decisions` by `candidate_id`.

**H4 -- capacity truth + kill switch.** See "Row-rate math" below for the
corrected baseline/expected/hard-max table.
`ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED` (default `"1"`) is checked FIRST in
`enqueue_family_book_observation`, before any other work -- an operator
rollout guard.

**M1 -- shutdown lifecycle.** `shutdown()` now reserves sentinel capacity
with a BLOCKING `put(_STOP, timeout=...)` (a full queue no longer makes
shutdown silently no-op) and only clears `_worker_thread` after `join()`
actually confirms the thread dead; `start_worker()` REFUSES (returns
`False`) while a worker is already alive rather than silently starting a
second one on top of it.

**M2 -- restart continuity.** `_bootstrap_last_state_cache()` runs once at
worker startup, seeding `_last_state_by_family` from each family's latest
DURABLE observation in the trade DB (`MAX(decision_time)` grouped by
`family_id`) -- a worker restart no longer falsely relabels an unchanged
state `STATE_CHANGE` or resets the heartbeat clock. Best-effort: any
failure just leaves the cache empty (matches pre-fix behavior).

**M3 -- center diagnostic basis.** `market_center_and_status` now
unconditionally excludes non-executable (shoulder) bins from the weighted
sum, regardless of whether they happen to be quoted -- previously a quoted
shoulder silently pulled into the average while an unquoted one (same
executable coverage) did not, so two `status=OK` centers could rest on
different support. Verified by
`TestMarketCenter.test_shoulder_quoted_or_not_yields_the_same_center_m3`.

**M4 -- orphan table cleanup.** Verified directly rather than assumed: the
real trade DB (`state/zeus_trades.db`) was queried for any
`family_book*`-prefixed table and returned none. No live/staging/dev
database in this environment ever initialized the original (`d0f69d155`)
`family_book_snapshots` schema, so no cleanup migration is needed -- the
table name was never created outside this branch's own now-reverted commit.

## Problem

Every decision cycle, `FamilyDecisionEngine.decide()` assembles the full
executable family order-book ladder (`FamilyBook`) to price routes and build
the de-frictioned market-implied q. That object is carried on
`FamilyDecision.family_book` and discarded once the cycle's trade/no-trade
call is made. Nothing persists the decision-time book. This is the data
prerequisite for the campaign's center-evidence work (market-implied center
vs our posterior mu) -- without a durable history there is nothing to
compare our forecast against over time.

## STEP 0 -- manifest vs content-addressed payload (verified, not assumed)

The redesign's preferred realization is a MANIFEST: persist
`(bin_id -> executable_snapshot_id + raw_orderbook_hash)` referencing the
existing `executable_market_snapshots` rows the family-book builder already
reads off the proofs (`src/engine/qkernel_spine_bridge.py:1962-2068`,
`_family_book_builder_from_proofs`), rather than re-serializing ladder
levels into a second JSON blob. This wins ONLY if those referenced rows are
immutable and retained for the research horizon. I verified this directly:

- `src/state/snapshot_repo.py` `ensure_snapshot_schema` installs
  `BEFORE UPDATE`/`BEFORE DELETE` triggers on `executable_market_snapshots`
  that `RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)')`
  -- structurally immutable, not just append-only by convention.
- `insert_snapshot` (`src/state/snapshot_repo.py:254`) is a plain `INSERT`,
  never an upsert.
- `scripts/ops/archive_pre_epoch_trades.py` is the ONE archival/delete tool
  in the repo that removes trade-DB rows; its `EXCLUDED_TABLES` frozenset
  explicitly lists `executable_market_snapshots` with the comment "market
  snapshot data whose rotation is a separate op ... never touched here."
  No other script references it for pruning.
- Growth census (`docs/operations/current/plans/db_first_principles_audit_2026-07-20/findings/growth_census_analysis.md`)
  confirms it is a live, monotonically-appended table (46.29 GB, ~49,614
  rows/day as of 2026-07-21), consistent with append-only, not with silent
  pruning.

**Verdict: manifest design wins.** `family_book_states.canonical_payload`
stores per-bin `(executable_snapshot_id, raw_orderbook_hash,
source_captured_at)` plus execution metadata (`condition_id`, token ids,
`neg_risk`, `min_tick_size`, `min_order_size`, `fee_rate` -- these come from
`FamilyBook.MarketBook`, since `FamilyBook`/`MarketBook` do NOT carry
snapshot identity themselves; only the proofs do
(`_CandidateProof.executable_snapshot_id`, `.row["raw_orderbook_hash"]`,
`.row["captured_at"]`, both confirmed by reading
`src/engine/event_reactor_adapter.py:3002` `_CandidateProof` and the `SELECT
*` queries that populate `.row`)).

## Scout map (unchanged from the original plan; re-verified against the
current hook site after the capture point moved -- see "Hook" below)

- `FamilyBook` (`src/execution/family_book.py:214`): `omega`, `markets:
  Mapping[bin_id, MarketBook]`, `captured_at_utc`, `book_hash`,
  `complete_book` (structural set-equality, never a free-standing flag).
- `MarketBook` (`:150`): `condition_id`, `bin_id`, `yes_token_id`,
  `no_token_id`, `neg_risk`, the four `ExecutableLadder` sides. Ladders are
  best-first `QuoteLevel(price: Decimal, size: Decimal)` tuples -- the
  redesign no longer serializes these at all (manifest, not ladder dump).
- `OutcomeBin` (`src/probability/outcome_space.py:46`): `lower_native`/
  `upper_native` (`None` = open shoulder), `executable`. Bounds are in the
  family's native settlement unit (`"C"` or `"F"` per
  `EventResolution.measurement_unit`) -- not always Celsius.
- `FamilyDecision` (`src/decision/family_decision_engine.py:608`):
  `family_book`, `predictive` (`mu_native`, `sigma_native`,
  `identity_hash`), `joint_q` (`Optional[JointQ]`, has `q_by_bin_id` +
  `identity_hash`), `market_implied_q` (`Optional[MarketImpliedQ]`, has `q`
  (ndarray aligned to `omega.bins`), `basis`, `depth_score`, `spread_score`,
  `projection_error`, `book_hash`), `selected`, `receipt_hash`, `decision_id`.

## Hook site (moved -- fixes the population-bias finding)

`src/engine/event_reactor_adapter.py`, inside
`_build_event_bound_no_submit_receipt_core`'s actionability retry loop
(`while True:` at the nonempty-proofs branch). The OLD hook fired once,
AFTER the loop exited, immediately before the
`prepare_global_auction`/`global_actuation` branch. The review found this
lets a LATER veto in the SAME cycle (near-day0 qkernel reason,
rest-then-cross not-actionable, same-token fill-up exclusion) reset
`_spine_fact_decision = None` and `break`, silently erasing a decision that
existed earlier in this same retry sequence from the evidence population.

**Fix**: the capture call now sits immediately after
`_spine_fact_decision = _spine_result.decision` -- the decision-PRODUCTION
seam, inside the loop, BEFORE any of the three veto checks that follow it in
the same iteration. It now fires once per `decide_family_via_spine` call
(every retry attempt that produces a decision with a book), not once per
reactor invocation, and does so regardless of `prepare_global_auction`/
`global_actuation` (those are read much later, downstream of this point).
`_active_spine_entry_proofs` (the retry-narrowed proof set THIS iteration's
`family_book_builder` was actually bound to --
`qkernel_spine_bridge.py`'s `route_proofs = tuple(selection_proofs) if
selection_proofs is not None else belief_proofs`) is passed alongside the
decision so the manifest references exactly the proofs that built this
book, not the full unfiltered set.

```python
enqueue_family_book_observation(
    decision=_spine_result.decision,
    family=family,
    active_proofs=_active_spine_entry_proofs,
    candidate_bin_id=_candidate_bin_id,
    decision_time=decision_time,
    causal_snapshot_id=event.causal_snapshot_id,
)
```

Verified: `test_capture_precedes_every_actionability_veto_reset_point`
(`tests/engine/test_family_book_observation_hook_placement.py`) asserts the
capture call's source position is strictly before all three veto markers
within the same function body.

## Table home: TRADE, not WORLD (unchanged reasoning)

Executable-market substrate, same class as `executable_market_snapshots`
and `book_hash_transitions` (both trade-owned). The writer thread opens its
own trade-DB connection (see "Capture plane" below) -- single-DB, no
ATTACH, so INV-37 (no independent-connection cross-DB writes) is not
implicated at all.

## Table design (state/observation split, replacing the single broken table)

### `family_book_states` -- immutable, content-addressed manifest

```sql
CREATE TABLE family_book_states (
    state_id               TEXT PRIMARY KEY,   -- sha256(family_id|content_hash)
    family_id               TEXT NOT NULL,
    content_hash            TEXT NOT NULL,      -- NEW, versioned, timestamp-free
    hash_version             INTEGER NOT NULL,
    topology_hash            TEXT NOT NULL,
    complete_book            INTEGER NOT NULL,
    canonical_payload        TEXT NOT NULL,      -- full manifest (identity + metadata)
    payload_schema_version   INTEGER NOT NULL,
    first_seen_decision_time TEXT NOT NULL,      -- informational only, NOT hashed
    schema_version           INTEGER NOT NULL
)
```

`content_hash` is computed over ONLY the fields whose change means the book
actually changed: per-bin `raw_orderbook_hash` + venue identity/execution
metadata (`condition_id`, token ids, `neg_risk`, `min_tick_size`,
`min_order_size`, `fee_rate`). It deliberately EXCLUDES
`executable_snapshot_id` and `source_captured_at` -- those change on every
fresh capture even when content is byte-identical, which is exactly the
failure being fixed. This is the direct, verified fix for BLOCKER 2/3: two
FamilyBooks built from proofs with different `executable_snapshot_id`s and
different `source_captured_at`s, but the SAME `raw_orderbook_hash`/fee/tick
per bin, hash to the SAME `content_hash` (proven by
`test_identical_content_at_different_capture_times_hashes_equal`).

Dedup: `UNIQUE(family_id, content_hash)` via a targeted
`INSERT ... ON CONFLICT(family_id, content_hash) DO NOTHING` -- never a
statement-wide `INSERT OR IGNORE`, so an unrelated NOT NULL/PK violation
raises instead of masquerading as a dedup hit (the HIGH "integrity" finding).

### `family_book_observations` -- append-only, SAMPLED decision time series

```sql
CREATE TABLE family_book_observations (
    observation_id            TEXT PRIMARY KEY,  -- sha256(family_id|receipt_hash|decision_time)
    family_id, city, target_date, temperature_metric,
    decision_id, receipt_hash, state_id,
    source_manifest_json,  -- X3: THIS observation's per-bin snapshot identity/capture time
    decision_time, causal_snapshot_id,
    predictive_identity_hash, our_mu_native, our_sigma_native, measurement_unit,
    model_q_json, model_q_identity_hash,
    market_q_json, market_q_basis, market_q_depth_score, market_q_spread_score,
    market_q_projection_error, market_q_book_hash,
    market_center_native, market_center_status, market_center_version,
    complete_book,
    sampling_reason,           -- STATE_CHANGE | HEARTBEAT | PRE_VETO_SELECTED | WORKER_BOOTSTRAP (H3)
    state_changed, heartbeat_due, pre_veto_selected,  -- H3: orthogonal, always persisted
    selected_bin_id, selected_side,                   -- H3: nullable identity of the pre-veto selection
    sampling_policy_version, capture_seam, schema_version
)
```

**Evidence authority is the ordered q vectors, not a scalar** (HIGH finding
2.4): `model_q_json`/`market_q_json` (bin_id -> probability, from
`decision.joint_q.q_by_bin_id` and `decision.market_implied_q.q` zipped
against `decision.omega.bins`), plus `market_implied_q`'s own quality fields
(`depth_score`, `spread_score`, `projection_error`, `basis`). These are
already computed by Stage 9 (`src/decision/market_coherence.py`) and were
being thrown away by the original design in favor of a weaker recomputed
scalar.

`market_center_native` is demoted to a versioned diagnostic
(`market_center_version = "market_center_native_v1"`), never authority, and
its coverage rule is TIGHTENED per the review: it now requires a two-sided
YES quote on EVERY `executable` bin (non-executable tail/shoulder bins are
exempt -- known-illiquid by design), returning `NULL` /
`market_center_status = "INSUFFICIENT_COVERAGE"` otherwise. The OLD test
fixture that quoted only 2 of 11 bins and asserted a non-null center was
deleted; `test_partial_coverage_on_complete_book_is_now_null_not_a_number`
replaces it with the opposite assertion.

`sampling_reason` (`STATE_CHANGE` | `HEARTBEAT` | `PRE_VETO_SELECTED` |
`WORKER_BOOTSTRAP`, H3 -- renamed from `DECISION`, which read as final/
submitted status when it is only a pre-veto selection at the production
seam) is the ACTUAL row-volume control (see "Capture plane / sampling
policy" below) -- `family_book_states`' content-hash dedup controls STATE
row volume only. `state_changed`/`heartbeat_due`/`pre_veto_selected` are
persisted as three ORTHOGONAL booleans regardless of which one wins by
precedence, since selection-triggered sampling is not missing-at-random and
analysts must be able to stratify on it.

`capture_seam = "DECISION_PRODUCTION"` documents where in the reactor's
control flow capture happens (see "Hook site" above) -- the disposition
column the review asked for, so future work can filter/attribute by capture
point if a second seam is ever added.

Append-only triggers (`BEFORE UPDATE`/`BEFORE DELETE` -> `RAISE(ABORT, ...)`)
on both tables, matching the `observation_prints_schema.py` precedent.

### Unit caveat (unchanged)

`our_mu_native`/`our_sigma_native`/`market_center_native` are stored in the
family's native settlement unit (`measurement_unit` column, `"C"` or
`"F"`) -- renamed from the original `_c`-suffixed columns per the review's
MEDIUM "units" finding (a name that lies about always being Celsius).

## Capture plane -- nonblocking, off the live decision thread, two-stage (X1/X2/H1/H2)

> **SUPERSEDED by "2026-07-29 round-5 fixes" above for everything about
> canonical delivery.** Stage 1 (below) is still accurate: the decision
> thread only enqueues, the worker thread only writes to the private spool.
> Stage 2 as described below (a periodic `_ingest_pass` inside THIS
> module's worker thread, gated by a standalone `db_writer_lock(BULK)`) is
> the ROUND-3/4 design and is NO LONGER HOW DELIVERY WORKS -- round-5
> deleted that ingest pass entirely. Canonical delivery is now
> `run_bounded_ingest`, a pure function called ONLY by
> `src/main.py`'s `_family_book_telemetry_ingest_cycle` scheduler job on
> its own `write_class="live"` connection; this module never opens a
> canonical connection at all. Kept below for its still-accurate Stage-1
> material and INV-37/SQLite-version reasoning, which round-5 did not
> revisit.

`src/events/family_book_telemetry_writer.py`, round-3/4 design (Stage 2
superseded -- see note above).

**Stage 1 -- decision thread.** `enqueue_family_book_observation` calls
`project_observation_envelope` (H1 -- `src/events/family_book_manifest.py`;
extracts only the small scalars/mappings the writer needs into a frozen
`ObservationEnvelope`, holding NO reference to `FamilyDecision`/`family`/
the proofs graph, so `FamilyDecision.band.samples` and similar large
objects are not kept alive by a queued item) and does a bounded
`queue.put_nowait(envelope)`. That is the ENTIRE hot-path cost: no worker
start/health-check (H2 -- the daemon starts the worker once at init via
`start_worker()`; a dead worker is never resurrected from here), no
counters-sink lock on the success path (only the rare `queue.Full`/
exception paths touch `src.observability.counters.increment`). A full queue
increments `telemetry_drop_total`; any other exception increments
`family_book_telemetry_enqueue_error_total`. Verified by
`test_enqueue_never_blocks_under_held_wal_write_lock` (a SEPARATE
file-backed connection holds `BEGIN IMMEDIATE`; 20 enqueue calls each
complete in <50ms -- now true by construction, since enqueue never touches
any shared file at all) and `test_full_queue_drops_and_increments_counter_without_blocking`.

**Stage 2 -- writer thread.** A single owner-local thread (`daemon=True`,
started explicitly by the daemon at init, never by the decision thread --
H2/M1) does everything else:

- Writes every envelope to a PRIVATE spool SQLite file
  (`family_book_telemetry_spool.db`, own file, own WAL) -- X2's core fix.
  Round-2 wrapped each trade-DB write in `db_writer_lock(WriteClass.BULK)`,
  but a second review found `WriteClass.LIVE`/`BULK` are SEPARATE lock
  files with no mutual exclusion by themselves (only `BulkChunker`'s
  cooperative `yield_if_live_contended()` bridges them, and the primary
  `trade_conn` doesn't take `WriteClass.LIVE` -- that retrofit is Phase 1+
  of the same plan, out of scope here), so that gave no real priority
  guarantee to the primary. Writing to a PRIVATE file removes the
  contention question entirely: nothing else ever opens
  `family_book_telemetry_spool.db`, so there is no writer to arbitrate
  against.
- Every spool write is transaction-safe (X1): state INSERT + observation
  INSERT + COMMIT in one try/except; on ANY failure,
  `_rollback_or_replace` tries `conn.rollback()` first and, only if that
  ALSO fails, closes and reopens the connection -- the only way to
  guarantee `conn.in_transaction` is False afterward. Counters and the
  sampling cache are updated ONLY after a durable commit.
- Periodically (every 30s, or immediately via the test/ops
  `force_ingest()`), `_ingest_pass` -- the ONLY code in the repo that opens
  a second connection to `zeus_trades.db` -- copies spool rows into the
  durable `family_book_states`/`family_book_observations` tables via
  `get_trade_connection(busy_timeout_ms=250)` wrapped in
  `db_writer_lock(trade_db_path, WriteClass.BULK, blocking=False)`.
  `WriteClass.BULK` is still the right class (telemetry always yields,
  never contends to win) but now only gates the infrequent, batched
  ingest window, not every observation -- a fundamentally smaller and
  bounded exposure. Contention increments
  `family_book_telemetry_ingest_contended_total` and simply defers to the
  next cycle -- no wait, no partial writes (each of `_ingest_states`/
  `_ingest_observations` is its own transaction with the same
  rollback-on-failure discipline as the spool write).

Queue/worker lifecycle (sentinel-based shutdown pushed through the queue,
`shutdown()`/`start_worker()` guarding against orphaning a worker blocked
on a since-reassigned queue object, `start_worker()` refusing a second
worker while one is alive -- M1) is modeled on
`src/data/replacement_cycle_advance_trigger.py`'s existing
day0-materialization-bridge pattern (`_DAY0_BRIDGE_STOP` sentinel,
`_day0_bridge_worker`/`_start_day0_bridge_workers_locked`) per team-lead
research -- the worker blocks on `queue.get(timeout=_INGEST_INTERVAL_SECONDS)`,
reacting immediately to a pushed item or the stop sentinel while still
polling the ingest cadence when idle.

**INV-37, verified against the invariant text itself, not inferred:**
`architecture/invariants.yaml:882-897` (INV-37): *"No Zeus write transaction
may span more than one physical DB via independent connections."* This
writer's connections touch, respectively, the private spool file only, or
`zeus_trades.db` only (the ingest pass) -- never two physical DBs in one
transaction -- outside INV-37's scope as written either way.

**Second-connection safety, verified, not assumed:** `src/state/db.py`'s
`_connect` docstring states explicitly: *"Callers doing optional derived
publication may choose a shorter budget so they yield to live writers...
Connection PRAGMA only — INV-37 / txn semantics unchanged."*
`get_world_connection` already exposes `busy_timeout_ms` for exactly this
precedent; `get_trade_connection` was extended to the same shape
(`src/state/db.py`, ~8 line diff). SQLite library version in this
environment: `3.53.2` -- above the `3.51.3+`/backport threshold for the
multi-connection WAL-reset fix, asserted defensively at worker startup
(`_MIN_SQLITE_VERSION_INFO = (3, 51, 3)`; checked before the worker ever
connects -- below the floor, it logs one ERROR and refuses to start,
verified by `test_worker_refuses_to_start_below_the_wal_reset_fix_floor`)
since the ingest pass runs a second live writer connection against the
trade DB.

**`db_writer_lock` -- first production wiring:** no production caller of
`src/state/db_writer_lock.py`'s `db_writer_lock`/`WriteClass` existed
before this PR (Phase 0 of the v4 sqlite-contention plan landed the helper
surface only). `_ingest_pass` is now the first, scoped to the infrequent
batched ingest window rather than every observation write. `SQLITE_CONNECT_ALLOWLIST`
(`src/state/db_writer_lock.py`) gained an entry for this module's raw
`sqlite3.connect()` on the private spool file -- outside the world-db BULK
lock universe by construction (no other writer ever touches that file).

**Counters -- canonical sink, not ad-hoc:** all telemetry counters route
through `src.observability.counters.increment`/`read` instead of a bespoke
in-module class: `telemetry_drop_total` (full-queue drops), `telemetry_queue_high_water_total`
(incremented once per NEW queue high-water record -- the sink is
documented monotonic-only, so the raw peak value is tracked separately via
`queue_high_water()` and the counter records the EVENT of a new record),
plus `family_book_telemetry_{enqueue_error,sampled_out,write_failures,
ingest_contended,ingest_failures,written_states,written_observations,
ingested_states,ingested_observations}_total` (repo convention favors
specific, collision-safe names, per existing counters like
`db_write_lock_timeout_total`). `reset_all()` is called from this module's
`reset_for_test()`, matching the sink's own documented "test isolation
only" contract.

### Sampling policy v2 -- the ACTUAL row-volume control (H3)

Replaces the broken per-cycle-unique-hash "dedup" entirely. The writer
thread keeps an in-memory `family_id -> (last_state_id, last_decision_time)`
cache (single writer thread, no lock needed; seeded from the durable trade
DB at worker startup -- M2, `_bootstrap_last_state_cache`) and computes
three ORTHOGONAL booleans on every envelope, persisted regardless of which
one wins by precedence:

- `state_changed` -- this family's `state_id` differs from the last one
  recorded (or this is the first observation ever for the family);
- `heartbeat_due` -- >= 30 minutes have elapsed since the last recorded
  observation for this family with an unchanged state;
- `pre_veto_selected` -- `decision.selected is not None` at the
  decision-production seam (renamed from `DECISION` -- H3: this can still
  be vetoed by a later actionability check in the SAME cycle, so it was
  never a record of final/submitted status).

`sampling_reason` is `STATE_CHANGE` > `HEARTBEAT` > `PRE_VETO_SELECTED` by
precedence; if none hold, the observation is sampled OUT (counted via
`family_book_telemetry_sampled_out_total`, never written). Verified:
`test_repeat_same_state_no_heartbeat_no_selection_is_sampled_out`,
`test_heartbeat_fires_after_interval_even_without_change`,
`test_selected_trade_forces_a_pre_veto_selected_observation_with_identity`
(also asserts `selected_bin_id`/`selected_side`, correlated from
`decision.candidate_decisions` by `candidate_id`).

### Fault injection

`test_write_exception_increments_counter_and_does_not_crash_worker`:
monkeypatches the state-insert call to raise on every attempt across 5
enqueues; asserts `family_book_telemetry_write_failures_total == 5` (every
failure counted) while the rate-limited logger (`_LOG_RATE_LIMIT_SECONDS =
60`) emits at most one WARNING record for the whole burst (no log storm),
and the worker thread survives (schema still present, no crash). Distinct
from lock contention (`family_book_telemetry_ingest_contended_total`,
`BlockingIOError` from `db_writer_lock`, now scoped to the ingest pass
only) which is counted separately and is
NOT treated as a fault -- `TestSpoolArchitecture.test_ingest_contention_skips_this_pass_without_blocking`
asserts the ingest pass returns promptly (<1s) while an external
`db_writer_lock(WriteClass.BULK)` holder is active, increments
`family_book_telemetry_ingest_contended_total`, and succeeds normally once
the external lock releases.

## Row-rate math (H4: baseline / expected / hard-max, not one "worst case")

Scout figures: >=60 decision cycles/hour, 51 families.

| Scenario | STATE rows | OBSERVATION rows | Basis |
|---|---|---|---|
| **Baseline** (no book ever changes, no trade ever selected) | ~0/day after first observation per family | 1 `HEARTBEAT`/30min x 51 families = 2,448/day | Sampling policy floor -- this is what the ORIGINAL plan mislabeled "worst case." |
| **Expected** (realistic book churn + occasional selection) | bounded by actual `raw_orderbook_hash` change frequency (not measured in production yet) | baseline + real `STATE_CHANGE`/`PRE_VETO_SELECTED` rows | Requires production measurement (not yet available -- flagged, not fabricated). |
| **Hard max** (every cycle changes content AND selects) | 60/hr x 51 = 3,060/hr = 73,440/day (state table, same content-hash-driven bound as observations at full churn) | 73,440/day | The scenario the ORIGINAL (round-1) design produced UNCONDITIONALLY; the redesign only reaches it if books genuinely change every single cycle. |

At the measured 10,556-byte content-only `canonical_payload` (round-3; down
from round-2's 14,784 bytes after X3 removed snapshot-identity fields from
the state payload), the hard-max state-table scenario is approximately
`73,440 * 10,556 bytes ~= 0.78 GB/day` of state JSON alone, before SQLite
page/index overhead, WAL amplification, backups, and fragmentation -- a
scenario bound requiring production measurement to rule in/out, not a
prediction. The kill switch (`ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED`, H4) is
the rollout guard while that measurement is gathered.

## Benchmark (production-shaped, N_BINS=51, n_iters=200; measured this
environment, `tests/events/test_family_book_telemetry_benchmark.py -s`)

Decision-thread cost (the entire hot-path -- H1's compact projection):

```
project_observation_envelope (us): p50=122.8-130.0  p95=143.8-155.6  p99=201.6-225.0
compute_state_identity (us):        p50=149.2-156.5  p95=170.5-187.0  p99=215.9-281.7
canonical_payload bytes (content-only, X3): 10556 (constant -- fixed bin count/field set)
```

Writer-thread cost, full `_process_one` (ONE bounded-outbox-row INSERT,
explicit transaction, COMMIT -- round-5: the insert target moved from the
canonical tables directly to the outbox) against a real file-backed WAL
spool DB, worst case (every iteration has distinct content -- every write
is a genuine insert, never the sampled-out fast path):

```
_process_one (ms): p50=1.4  p95=3.0-3.2  p99=3.6-5.3
```

Daemon-scheduler-job cost, `run_bounded_ingest` against a realistic
500-row pending batch on a file-backed canonical DB (round-4's flagged gap
-- the earlier benchmarks validated Stage 1 only):

```
run_bounded_ingest (500 rows, 500 states, 500 observations): ~41-47ms
```

Both the decision-thread and writer-thread benchmarks run OFF the decision
thread except `project_observation_envelope`/`compute_state_identity`,
which ARE the decision-thread cost and are sub-millisecond even at p99
(H1's whole point). The writer-thread/ingest numbers bound the spool's own
throughput headroom and the ONE bounded canonical transaction's duration
per scheduler tick, never decision latency, and never contend with the
primary trade_conn via a standalone writer (Y3 -- ingestion runs on the
daemon's own `write_class="live"` connection, not this module's).

## Tests

- `tests/events/test_family_book_manifest.py` (18): the compact envelope
  projection (H1), the timestamp-free content-hash fix (the round-1 core
  bug, proven both ways), the tightened `market_center_and_status` coverage
  rule plus M3 (shoulder bins always excluded from the weighted sum), X3
  (state payload excludes snapshot identity/capture time; two observations
  of identical content produce DISTINCT source manifests). Unchanged by
  round 5 (`family_book_manifest.py` was not touched).
- `tests/events/test_family_book_telemetry_writer.py` (26): readiness
  handshake (Y5 -- ready on success, terminally-disabled + typed counter on
  a version-floor failure); the kill switch stopping BOTH capture AND
  canonical draining (Y5, verified with a pending backlog already spooled);
  nonblocking enqueue under real WAL contention + full-queue drop; the
  bounded outbox (Y1 -- ack-delete, idempotent repeated ingestion, a
  multi-batch backlog draining bounded pass by bounded pass never all at
  once, pending-stats metrics, the disk budget stopping capture); t-vs-t+1
  live-rebuild cardinality and per-observation provenance end-to-end
  through capture->outbox->bounded-ingest; the sampling-policy v2 branches
  with orthogonal booleans and selected bin/side identity (H3); X1
  transaction safety UNCHANGED in structure (three tests: observation-
  insert failure rolls back cleanly; a failed commit recovers via rollback
  alone when rollback succeeds; rollback failure forces connection
  replacement -- all via dedicated `sqlite3.Connection` subclasses since
  `.commit`/`.rollback` can't be monkeypatched as instance attributes on
  the C type) adapted only to target the outbox insert; Y4 worker liveness
  (a malformed envelope is quarantined -- counted, dropped, the worker
  stays alive and processes the next envelope normally) and the review's
  exact required restart-without-ingest regression test (spool-write
  without ingesting, restart, identical content one minute later is
  sampled out, not falsely STATE_CHANGE); the SQLite version guard; M1
  shutdown/second-worker-refusal; the round-4 MEDIUM AST antibody (exactly
  one `sqlite3.connect()` call site in the module, lexically inside
  `_default_spool_conn_factory`) plus the runtime path-inequality
  assertion.
- `tests/events/test_family_book_telemetry_benchmark.py` (3): the
  decision-thread projection benchmark, the end-to-end outbox-write
  benchmark, and the NEW bounded-canonical-ingest benchmark (round-4's
  flagged Stage-2 benchmark gap) above.
- `tests/engine/test_family_book_observation_hook_placement.py` (2):
  source-position proof that capture precedes all three veto-reset points
  and that it's wired to `_active_spine_entry_proofs` (not the full proof
  set) -- unchanged by round 3 (the hook call signature didn't change).

### Disclosed gap -- full dynamic three-branch reactor execution

The review's test (d) asked for the retry loop's three actionability
branches (and the pre-hook reset paths) to be EXECUTED with a capture spy,
not proven by `inspect.getsource`. I attempted this: `EventBoundDecisionEngine`
(class-level, monkeypatchable), `_selection_scoped_proofs` and
`decide_family_via_spine` (both module-level, confirmed monkeypatchable even
though the latter is locally re-imported inside the giant function, since
`from module import name` re-resolves the module attribute at each call) are
individually reachable seams. Reaching them in sequence, however, requires
also satisfying or bypassing `_forecast_lane_phase_admits` /
`_edli_forecast_lane_phase_evidence`, real `row`/`proofs` construction, and
further gates each revealing another -- a fixture investment comparable to
the codebase's existing 1000+ line reactor integration test files
(`tests/engine/test_s3_native_side_candidate_materialization.py`,
`tests/engine/test_s6_submit_recapture_gate.py`). I stopped rather than
force a fragile shortcut that risked its own bugs for a telemetry-only
capture path, and instead paired the source-position test (still a real,
precise regression net -- it pins exact call ordering by name, not just "a
call exists somewhere") with fully-executed unit tests
(`test_family_book_telemetry_writer.py`) that exercise the actual
enqueue -> sampling -> write pipeline end-to-end with real code, just not
threaded through the full reactor from raw event ingestion. This is a
disclosed, reasoned gap, not a silent one.

---

## Round 6 (final): the two blockers, and one reasoned divergence

Round-5 deep review returned NO-GO for a default-ON merge at `ca6dee133`
with two blockers. Both are addressed below. On Z1 the review's *concern* is
implemented; its *prescribed remedy* is not, for a reason grounded in a
constraint the reviewer could not see from outside. That reasoning is
recorded here, not deferred.

### Z1 — telemetry delivery must never compete with the money path

**The concern (accepted).** A 30s periodic job opening the live-money trade
DB is a second writer racing the reactor for the single SQLite write lock.
Under WAL, `busy_timeout` is an error-return budget, not a priority
mechanism, so an optional telemetry pass can make a money-path write wait.

**The prescribed remedy (declined, with cause).** The review asked that
delivery execute on the reactor's own `trade_conn` at a post-commit seam.
That is not available here. The reactor's connection is
`get_trade_connection_with_world_required(write_class=None)` (`reactor.py`)
with `world` and `forecasts` ATTACHed, and the cycle commits money-path
truth on it mid-cycle (`reactor.py:7086`) while continuing to work. Running
telemetry INSERTs on that connection places optional writes inside the money
path's transaction scope: a telemetry failure must roll back, and a rollback
there discards whatever reactor work shares the transaction. That is exactly
the transaction-poisoning failure class review X1 established must not exist
— so the round-6 remedy, applied literally, would have reintroduced the
round-2 defect. The reviewer could not weigh this: it depends on the
reactor's connection flavor and mid-cycle commit, which were outside the
reviewed diff.

**What is implemented instead — the repo's own idiom for this exact
problem.** Zeus already has optional periodic work that must not contend
with the money path, and it does not solve it by sharing a transaction; it
solves it by *yielding*: `if _cycle_lock.locked() or _edli_reactor_active():
return` (`main.py:1761`, `:1846`, `:1896`, `:1920`). The telemetry ingest job
now takes the same pair of guards, plus a spool-only pending precheck
(`outbox_has_pending`) so an idle tick — the overwhelmingly common case —
never opens a canonical connection at all. Net effect: telemetry touches the
trade DB only when it has work AND the money path is idle. This achieves the
review's stated goal (no contention) without its structural cost (shared
transaction fate).

Note also that a periodic `write_class="live"` trade writer is not novel
here: `_c3_staleness_cancel_cycle`, `_run_ws_gap_reconcile_if_required`, and
`_refresh_reconcile_findings_if_required` already are exactly that. The
telemetry job is strictly more deferential than any of them.

### Z2 — the spool must be computationally and physically bounded

- The admission check was `COUNT(*) + SUM(LENGTH(...))` over the whole
  outbox, per envelope — cost growing with the very backlog it bounded, so
  capture got slower precisely when the spool was in trouble. Replaced with
  O(1) `pending_count`/`pending_bytes` metadata maintained transactionally,
  in the same transaction as each insert/delete. Counter rows are created by
  the first row they count (upsert), which removes any need for a startup
  backfill — and with it the open-time `commit()` such a pass would require.
- Budget read failure now **fails closed** (drops the capture, typed
  counter). Capture is optional; the live-money DB's disk is not. Admitting
  writes against an unknown backlog is how an optional plane eats the money
  path's storage.
- The ceiling is now **physical**, not advisory: `PRAGMA max_page_count`
  (~1 GiB) makes SQLite itself return `SQLITE_FULL` to this module's writes,
  plus `journal_size_limit` to bound the WAL. The two layers are now named
  for what each is — `_spool_pending_budget_bytes` (soft admission
  threshold, 500 MB) and `_spool_max_page_count` (hard file ceiling) —
  rather than one misleading "hard disk budget".
- `(family_id, spool_seq DESC)` index for the restart-bootstrap query, which
  runs a correlated `MAX(spool_seq)` per family on the startup path.

### Folded-in HIGH/MEDIUM findings

- Supervisor boundary around the whole per-item worker body: this is the
  SOLE capture thread, so any unanticipated escape would kill capture
  silently for the daemon's lifetime.
- Ack failure gets a typed outcome (`ack_failed`) and counter. Safe is not
  silent: a persistently failing ack means the outbox never drains while
  canonical ingestion keeps reporting success.
- Startup timeout now sets `_stop_event` and JOINs. A timeout means the
  thread is slow, not absent — it could otherwise come up after we declared
  capture dead and write unsupervised. Cleanup is keyed on "was started".
- `cache_seeded` is a separate `ReadinessResult` field, logged at WARNING:
  an unseeded cache labels the first observation per family `STATE_CHANGE`
  regardless of content, a real analysis caveat that must be visible rather
  than folded into `ready=True`.
- Daemon lifecycle moved to `try/finally` with a bounded `drain()` before
  worker stop, so enqueued envelopes reach the spool instead of dying in the
  in-memory queue.
- Corrected comments that claimed "no canonical access at all" — the worker
  does open canonical READ-ONLY once at startup to seed its cache. A comment
  that lies about its own module is worse than none.

### Validation

Six new tests, in `TestMoneyPathYield`, `TestSpoolHardBounds`,
`TestAckFailureReplay`, `TestStartupTimeoutLeavesNoLiveThread`. Two of them
were **mutation-checked** — the yield guard and the fail-closed budget branch
were each deleted and the corresponding test confirmed to fail. Both
initially passed against the mutant (the first because an empty outbox
short-circuited the path under test; the second because dropping the metadata
table also broke the insert, so the row was missing for an unrelated reason)
and were rewritten until they failed for the right reason. A test that cannot
fail is not evidence.

Suite state: 57/57 feature tests pass. `tests/state/`, `tests/events/`,
`tests/engine/`, and `tests/test_main_module_scope.py` were diffed against a
clean `origin/live` baseline worktree: the failure sets are IDENTICAL — zero
failures introduced by this branch. (The pre-existing failures there are
unrelated and predate this work.) The one antibody this branch legitimately
tripped, `test_a4_trade_tables_init_schema_creates_runtime_tables_and_migration_ledger`,
was the registry-parity net doing its job: `EXPECTED_TRADE_DB_TABLES` now
declares `family_book_states`/`family_book_observations`. The transport
outbox is deliberately absent from the registry — it lives in a private
spool file, never the canonical trade DB.

### Measured: what `max_page_count` actually bounds under WAL

`PRAGMA max_page_count` bounds the MAIN database file, not the `-wal`, so
"1 GiB ceiling" is not by itself a 1 GiB footprint claim. Measured on this
interpreter (SQLite 3.53.2) rather than assumed:

- Under normal operation the WAL is bounded by `wal_autocheckpoint` (1000
  pages) with `journal_size_limit` (64 MiB) capping what is retained after a
  checkpoint. Steady-state WAL stayed at 64 KiB across 2000 committed writes;
  a TRUNCATE checkpoint took it to 0.
- AT the ceiling the interesting case appears: checkpointing cannot drain the
  WAL into a main file that is not allowed to grow, so the WAL holds the
  frames. Total footprint therefore exceeds the page ceiling — but it
  CONVERGES rather than running away, because once the wall is hit this
  module's own writes fail and no new frames are appended. Verified by
  hammering 3000 further inserts after the wall: WAL size was byte-identical
  before and after (3143592 both times at a deliberately pathological
  256-page ceiling).

So the guarantee is: bounded, and self-limiting at the wall — not "≤1 GiB
exactly". In practice the 500 MB soft admission gate binds first and the file
ceiling is never approached; the ceiling exists so that a bug in the soft gate
still terminates in a failed telemetry write rather than a full disk.

---

## 2026-08-19 DB split — family-book evidence gets its own physical file

Round-6 (Z1) accepted that telemetry delivery must never compete with the
money path, and closed that gap by *yielding*: the daemon's ingest job
checks `_cycle_lock`/`_edli_reactor_active` before opening a canonical
connection to `zeus_trades.db`. That is correct as far as it goes, but it is
still a **cooperative** guarantee — it depends on every future touch-point
continuing to check the guard correctly. This revision removes the
dependency: `family_book_states`/`family_book_observations` move off
`zeus_trades.db` onto a new physical file,
`state/zeus-family-book-evidence.db` (`get_family_book_evidence_connection`,
`init_schema_family_book_evidence`, `src/state/db.py`). After the split,
evidence writes and money-path writes never share a file's WAL writer lock,
full stop — a telemetry write is now structurally incapable of contending
with, or being contended by, the reactor's trade-DB transaction, independent
of timing or whether the yield guard is ever reached. The guard is kept
anyway as a courtesy (see `_family_book_telemetry_ingest_cycle`'s docstring
in `src/main.py`), but the DB boundary — not the guard — is what now makes
this safe. `tests/events/test_family_book_telemetry_writer.py`
`test_evidence_delivery_cannot_contend_with_a_held_trade_db_write` is the
deterministic proof: holding an exclusive write transaction open on the
trade DB has zero measurable effect on evidence delivery, because
`run_bounded_ingest`'s canonical connection now points at a different file.

Registry: `family_book_states`/`family_book_observations` are now
`family_book_evidence_class` on the new `DBIdentity.FAMILY_BOOK_EVIDENCE` /
`Domain.FAMILY_BOOK_EVIDENCE` (`architecture/db_table_ownership.yaml`,
`src/state/table_registry.py`, `src/state/domains.py`), boot-asserted the
same way world/trade already are
(`assert_db_matches_registry(DBIdentity.FAMILY_BOOK_EVIDENCE)`, `src/main.py`).
The private transport outbox (`family_book_telemetry_outbox`/
`family_book_telemetry_meta`) still lives only in the separate spool file
and is registered as a non-canonical sidecar in
`scripts/ci/check_db_table_delta.py`'s `_KNOWN_SIDECAR_TABLES` — unchanged
in kind, just accompanied now by the canonical DB's own real identity.

### Folded-in must-fix findings from the split review

- **Growth ceiling, real byte budget.** Both the evidence DB
  (`init_schema_family_book_evidence`) and the private spool
  (`_open_spool`) now convert a byte budget to `PRAGMA max_page_count` via
  `src.state.db.page_count_ceiling_for_byte_budget`, which reads the
  connection's ACTUAL `page_size` rather than assuming 4 KiB. The evidence
  DB's ceiling is armed *before* any table DDL runs (`PRAGMA max_page_count`
  refuses to go below the current page count once the schema already
  exists, so ordering is load-bearing, not cosmetic — see
  `tests/state/test_table_registry_coherence.py`
  `test_family_book_evidence_db_has_a_real_growth_ceiling_end_to_end`).
  Hitting the ceiling surfaces through the existing `run_bounded_ingest`
  failure path (typed `IngestOutcome(failed=True)`, counted, logged) plus a
  DEDICATED counter (`family_book_telemetry_evidence_db_ceiling_hit_total`)
  so an operator can tell "our own ceiling did its job" apart from a
  genuine disk-full condition, both of which SQLite reports with the same
  error text.
- **Outbox meta counters fail closed, not silently.** Two invariants that
  used to be silently masked now raise `OutboxMetaInvariantError`
  (`family_book_telemetry_outbox_schema.py`): a delta that would drive
  `pending_count`/`pending_bytes` negative (previously clamped to zero via
  `MAX(0, ...)`), and a partial counter pair — exactly one of the two keys
  present, which `_bump_meta` can never produce in normal operation and so
  can only mean corruption (previously an absent key read as "definitely
  zero pending," even when its sibling key proved rows existed).
- **`_bootstrap_last_state_cache` no longer swallows its own failure.**
  HIGH-2: the function used to catch its OWN canonical-connection-open
  failure internally (`except Exception: return`) and return normally
  either way, so the caller's `try/except` around it never fired and
  `cache_seeded` stayed `True` unconditionally — even when the canonical
  seed read never ran at all. It now raises a typed
  `CanonicalSeedUnavailableError` for every outcome except a positively
  completed read (rows fetched, however many) or a positively-identified
  fresh-empty-schema ("no such table" — there is nothing to seed, so that
  case legitimately counts as seeded). `cache_seeded=True` can now only
  ship when the seed read actually completed.
- **Canonical-path rejection, centralized.** The spool's own path-safety
  check used to compare only against the trade DB, and only by `resolve()`
  string equality. `_assert_path_is_not_canonical`
  (`family_book_telemetry_writer.py`) now checks world/forecasts/trade AND
  the evidence DB, and additionally uses `os.path.samefile()` when both
  paths exist on disk — catching a symlink or hardlink alias that
  `resolve()` equality alone would miss (regression tests:
  `test_spool_factory_refuses_when_spool_path_is_a_symlink_to_a_canonical_db`,
  `..._hardlink_...`).

### Rollback

Because the evidence tables now live on their own file rather than being
interleaved into `zeus_trades.db`'s DDL, rollback is simple: an older binary
that does not know about `state/zeus-family-book-evidence.db` simply never
opens it. It has no code path that references the file, so it ignores it
entirely — no migration to reverse, no column to drop, no shared-file DDL to
undo. Concretely, rolling back this commit:

1. Redeploy the prior binary. `_family_book_telemetry_ingest_cycle` and the
   writer's read-only bootstrap revert to their old `get_trade_connection`/
   `get_trade_connection_read_only` call sites, which is consistent with the
   evidence tables having previously lived on `zeus_trades.db` at THAT
   binary's schema version.
2. `state/zeus-family-book-evidence.db` is simply left on disk, unopened and
   inert, until either (a) the split is rolled forward again, or (b) an
   operator explicitly deletes it (it holds no data the trade DB depends on
   — evidence-only, never decision authority).
3. The prior binary's `init_schema_trade_only` still creates
   `family_book_states`/`family_book_observations` on `zeus_trades.db` as
   before (this commit does not touch that binary's code, only this
   branch's). Any evidence captured AFTER the split and BEFORE a rollback
   lives on the new file and is NOT visible to the rolled-back binary — a
   real, bounded data-continuity gap for the rollback window, disclosed
   here rather than assumed away. Evidence-only, never decision authority,
   so this has no effect on trading correctness; it only means the
   center-evidence campaign has a gap for observations captured during that
   window until the new file is re-attached to a binary that reads it again.
4. No forward-migration script is needed either way: both schemas
   (`family_book_states_schema.py`/`family_book_observations_schema.py`)
   are byte-identical regardless of which physical file hosts them, so
   moving the split forward or backward again never requires a DDL
   migration, only a binary swap.

### Retention — OPEN operator decision, not resolved here

Both tables are structurally append-only (`BEFORE UPDATE`/`BEFORE DELETE`
triggers `RAISE(ABORT, ...)`, matching `observation_prints_schema.py`'s
precedent), and this revision adds a hard physical ceiling
(`ZEUS_FAMILY_BOOK_EVIDENCE_MAX_BYTES`, default 20 GiB, real-page-size-based
— see above) on top of that. The combination means: the store fills, and
then — correctly, per the append-only design — refuses new evidence rather
than silently dropping it (typed `IngestOutcome.failed`, counted, logged;
never a silent drop, per this round's explicit instruction). But nothing in
this design *drains* the store once it nears the ceiling. That is a real
operational question this PR does not have standing to answer on its own
authority: relaxing the append-only triggers is a decision about what
"evidence" means for this table, and belongs to whoever owns that
definition, not to whoever happened to build the storage layer under it.

Two options, and what each costs:

1. **Stay strictly append-only; accept a hard stop.** Nothing changes
   beyond what is already implemented. At current unmeasured-but-bounded
   row rates (see "Row-rate math" above — baseline ~2,448 rows/day, hard-max
   ~146,880 rows/day across both tables), the default 20 GiB ceiling is
   generous enough that reaching it is not an imminent concern, but it is
   not "never" either: at the hard-max content-JSON estimate
   (~0.78 GB/day for the state table alone) the ceiling is reachable within
   roughly a month of sustained full-churn capture. Cost: eventually, and
   without further intervention, new evidence capture stops entirely (typed,
   observable, never silent) until an operator manually archives/expands.
   Benefit: the evidence history is permanently complete and auditable —
   correct for research provenance (the center-evidence campaign this table
   exists to feed), where a silently-thinned history would be a worse
   failure than a bounded hard stop.
2. **Bounded FIFO reaper by age or size.** Add a periodic job (the same
   `_scheduler_job` idiom every other periodic daemon touch-point already
   uses) that deletes the oldest rows past an age or total-size threshold,
   the way `scripts/ops/archive_pre_epoch_trades.py` already does for other
   trade-DB tables (though that script explicitly EXCLUDES
   `executable_market_snapshots`, the precedent this design followed for
   "why append-only" in the first place — see STEP 0 above). Cost: requires
   relaxing (or narrowly carving an exception into) the append-only
   triggers, which is exactly the invariant this design leaned on to keep
   `content_hash`/`state_id` referential integrity simple; a reaper also
   needs its own retention window decision (how far back is "recent enough"
   for the center-evidence campaign to still be useful) that has not been
   scoped. Benefit: the store never hard-stops, and disk footprint stays
   bounded by policy rather than by a physical wall that, once hit, requires
   manual intervention to clear.

This PR implements the ceiling and makes reaching it explicit and
observable, per instruction, and stops there. **The operator should rule on
which of the two options above applies**, and if (2), scope the retention
window as a separate follow-up.
