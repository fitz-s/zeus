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
    decision_id, receipt_hash, state_id, decision_time, causal_snapshot_id,
    predictive_identity_hash, our_mu_native, our_sigma_native, measurement_unit,
    model_q_json, model_q_identity_hash,
    market_q_json, market_q_basis, market_q_depth_score, market_q_spread_score,
    market_q_projection_error, market_q_book_hash,
    market_center_native, market_center_status, market_center_version,
    complete_book, sampling_reason, sampling_policy_version, capture_seam,
    schema_version
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

`sampling_reason` (`STATE_CHANGE` | `HEARTBEAT` | `DECISION`) is the ACTUAL
row-volume control (see "Capture plane / sampling policy" below) --
`family_book_states`' content-hash dedup controls STATE row volume only.

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

## Capture plane -- nonblocking, off the live decision thread (fixes BLOCKER 1)

`src/events/family_book_telemetry_writer.py`. The decision thread calls
ONLY `enqueue_family_book_observation(...)`: a bounded
`queue.put_nowait()` of a small immutable envelope (object REFERENCES --
`decision`, `family`, `active_proofs`, `candidate_bin_id`, `decision_time`,
`causal_snapshot_id` -- no copying, no serialization). A full queue
increments a `dropped_queue_full` counter and returns immediately; any other
exception increments `enqueue_error` and is swallowed. Verified by
`test_enqueue_never_blocks_under_held_wal_write_lock` (a SEPARATE
file-backed connection holds `BEGIN IMMEDIATE` on the trade DB for the
whole test; 20 enqueue calls each complete in <50ms) and
`test_full_queue_drops_and_increments_counter_without_blocking`.

All manifest-building, hashing, JSON serialization, sampling-policy
evaluation, and SQLite I/O happen on a separate, owner-local writer thread
(`daemon=True`) that opens its OWN connection via
`get_trade_connection(busy_timeout_ms=250)` -- a short budget, not the
live default 30s, so it yields to live writers rather than contending for
the WAL write lock. Queue/worker lifecycle (sentinel-based shutdown pushed
through the queue, `_ensure_worker_started`/`_stop_current_worker` guarding
against orphaning a worker blocked on a since-reassigned queue object) is
modeled on `src/data/replacement_cycle_advance_trigger.py`'s existing
day0-materialization-bridge pattern (`_DAY0_BRIDGE_STOP` sentinel,
`_day0_bridge_worker`/`_start_day0_bridge_workers_locked`) per team-lead
research -- the worker blocks on `queue.get()` with no poll timeout, so
shutdown is immediate rather than the up-to-500ms poll-loop latency an
earlier revision of this module had.

**INV-37, verified against the invariant text itself, not inferred:**
`architecture/invariants.yaml:882-897` (INV-37): *"No Zeus write transaction
may span more than one physical DB via independent connections."* Confirmed
by team-lead research and independently re-read: this writer's connection
touches ONLY `zeus_trades.db`, never world/forecasts in the same
transaction -- outside INV-37's scope as written.

**Second-connection safety, verified, not assumed:** `src/state/db.py`'s
`_connect` docstring states explicitly: *"Callers doing optional derived
publication may choose a shorter budget so they yield to live writers...
Connection PRAGMA only — INV-37 / txn semantics unchanged."*
`get_world_connection` already exposes `busy_timeout_ms` for exactly this
precedent; this PR extends `get_trade_connection` to the same shape
(`src/state/db.py`, ~8 line diff) rather than reaching into a private
helper from `src/events/`. SQLite library version in this environment:
`3.53.2` -- above the `3.51.3+`/backport threshold for the multi-connection
WAL-reset fix, asserted defensively at worker startup
(`_MIN_SQLITE_VERSION_INFO = (3, 51, 3)`; `sqlite3.sqlite_version_info` is
checked before the worker ever connects -- below the floor, it logs one
ERROR and refuses to start, verified by
`test_worker_refuses_to_start_below_the_wal_reset_fix_floor`) since this
module is the first thing in the repo to run a second live writer
connection against the trade DB concurrently with the primary.

**`db_writer_lock` -- first production wiring, per team-lead research:**
team-lead's research confirmed no production caller of
`src/state/db_writer_lock.py`'s `db_writer_lock`/`WriteClass` exists today
(Phase 0 of the v4 sqlite-contention plan landed the helper surface only).
Each observation write here is wrapped in
`db_writer_lock(trade_db_path, WriteClass.BULK, blocking=False)` --
`WriteClass.BULK` because telemetry must always yield, never contend to
win. Honest caveat, stated plainly rather than implied away: `WriteClass`
arbitrates LIVE vs BULK via `BulkChunker.yield_if_live_contended()`
cooperatively checking a SEPARATE `.writer-lock.live` file (LIVE and BULK
are different lock files, not mutually exclusive at the flock() level by
themselves), and the PRIMARY `trade_conn` does not yet take
`WriteClass.LIVE` around its writes (that retrofit is explicitly Phase 1+
of the same plan, out of this PR's scope). So taking `WriteClass.BULK` here
does NOT yet provide direct arbitration against the primary connection
specifically -- today's actual protection against blocking the primary is
the short `busy_timeout` above plus SQLite's own WAL semantics, exactly as
already documented. Taking the lock now is still correct and valuable: (1)
it correctly self-classifies this writer so the moment the primary path IS
retrofitted to `WriteClass.LIVE` (Phase 1+), this writer automatically
yields via the existing cooperative mechanism with zero further changes;
(2) it prevents two instances of this SAME writer (daemon restart race, or
any future second `WriteClass.BULK` caller) from writing concurrently; (3)
it establishes the first real precedent for a dormant mechanism the repo
already built and intended for exactly this class of problem.
`blocking=False`: contention is treated as a normal, benign, expected event
for best-effort telemetry -- the write is skipped (typed counter, no wait,
no retry), never blocked on. Verified by
`test_external_bulk_lock_holder_causes_contended_skip_not_a_write` (an
external `db_writer_lock(db_path, WriteClass.BULK)` held for the duration
of an enqueue+drain; the write is skipped and counted, not silently lost
nor blocking; a subsequent write after the external lock releases succeeds
normally).

**Counters -- canonical sink, not ad-hoc:** per team-lead research
(`src/observability/counters.py` is "the canonical typed counter sink for
Zeus telemetry"), all telemetry counters route through
`increment`/`read` there instead of a bespoke in-module counter class:
`telemetry_drop_total` (full-queue drops -- team lead's exact suggested
name), `telemetry_queue_high_water_total` (incremented once per NEW queue
high-water record -- the sink is documented monotonic-only, "NO... gauge
semantics", so the raw peak value is tracked separately via
`queue_high_water()` and the counter records the EVENT of a new record,
matching the sink's own contract rather than forcing gauge semantics onto
a counter primitive), plus `family_book_telemetry_{enqueued,enqueue_error,
sampled_out,write_failures,write_contended,written_states,
written_observations}_total` (repo convention favors specific,
collision-safe names over a bare generic one, per existing counters like
`db_write_lock_timeout_total`/`cost_basis_chain_mutation_blocked_total`).
`reset_all()` is called from this module's `reset_for_test()`, matching
the sink's own documented "test isolation only" contract.

### Sampling policy v1 -- the ACTUAL row-volume control

Replaces the broken per-cycle-unique-hash "dedup" entirely. The writer
thread keeps an in-memory `family_id -> (last_state_id, last_decision_time)`
cache (single writer thread, no lock needed) and appends an observation iff:

- `STATE_CHANGE` -- this family's `state_id` differs from the last one
  recorded (or this is the first observation ever for the family), or
- `HEARTBEAT` -- >= 30 minutes have elapsed since the last recorded
  observation for this family with an unchanged state, or
- `DECISION` -- `decision.selected is not None` (a trade was actually
  chosen), regardless of state/heartbeat timing.

Otherwise the observation is sampled OUT (counted via
`family_book_telemetry_sampled_out_total`, never written). Verified:
`test_repeat_same_state_no_heartbeat_no_selection_is_sampled_out`,
`test_heartbeat_fires_after_interval_even_without_change`,
`test_selected_trade_forces_a_decision_observation`.

### Fault injection

`test_write_exception_increments_counter_and_does_not_crash_worker`:
monkeypatches the state-insert call to raise on every attempt across 5
enqueues; asserts `family_book_telemetry_write_failures_total == 5` (every
failure counted) while the rate-limited logger (`_LOG_RATE_LIMIT_SECONDS =
60`) emits at most one WARNING record for the whole burst (no log storm),
and the worker thread survives (schema still present, no crash). Distinct
from lock contention (`family_book_telemetry_write_contended_total`,
`BlockingIOError` from `db_writer_lock`) which is counted separately and is
NOT treated as a fault -- `test_external_bulk_lock_holder_causes_contended_skip_not_a_write`
asserts `write_contended == 1` and `write_failures == 0` for the same
attempt.

## Row-rate math (revised)

Scout figures: >=60 decision cycles/hour, 51 families. Per-cycle production
call count depends on retry-loop iterations (>=1 per family per reactor
invocation reaching the spine), so the STATE table's write rate is bounded
by actual book-content-change frequency (order-book churn), not decision
frequency -- exactly the property `UNIQUE(family_id, content_hash)` now
correctly provides (verified fixed by
`test_identical_content_at_different_capture_times_hashes_equal`). The
OBSERVATION table's write rate is bounded by the sampling policy: worst
case (a family's book never changes, no trade ever selected) is one
`HEARTBEAT` row every 30 minutes per family = 2/hour x 51 families =
102 rows/hour = 2,448 rows/day, plus real `STATE_CHANGE` rows (bounded by
market activity) and real `DECISION` rows (bounded by actual trade
frequency) -- roughly two orders of magnitude below the original design's
73,440 rows/day, and this bound holds regardless of decision-cycle
frequency (unlike the original, which scaled 1:1 with cycles).

## Benchmark (production-shaped, N_BINS=51, n_iters=200; measured this
environment, `tests/events/test_family_book_telemetry_benchmark.py -s`)

```
canonical_payload bytes: mean=14784  p50=14784  p95=14784  p99=14784
manifest+hash build (ms): p50=0.206  p95=0.228-0.346  p99=0.282-0.496
state insert+commit (ms): p50=0.354-0.359  p95=0.457-0.575  p99=2.229-3.527
```

All of this runs on the OFF-decision-thread writer, never the decision
path -- these numbers bound the writer thread's own throughput headroom
(sub-millisecond per observation at the median, low single-digit
milliseconds at p99), not decision latency.

## Tests

- `tests/events/test_family_book_manifest.py` (13): manifest identity
  sourcing (proofs, not FamilyBook), the timestamp-free content-hash fix
  (the core bug, proven both ways -- unchanged content hashes equal across
  different capture times; changed `raw_orderbook_hash`/fee/tick hashes
  different), the tightened `market_center_native` coverage rule (full
  coverage -> value; partial coverage on a "complete" book -> NULL, proving
  the reviewed defect is fixed; incomplete book -> NULL), `model_q_fields`/
  `market_q_fields`.
- `tests/events/test_family_book_telemetry_writer.py` (10): nonblocking
  enqueue under real WAL writer contention + bounded latency; full-queue
  drop counter; t-vs-t+1 live-rebuild state/observation cardinality (state
  dedups to 1, observation correctly sampled-out per policy -- the direct
  fix for BLOCKER 2/3); a genuine content change (fee) produces a second
  state row; the three sampling-policy branches; commit-time fault
  injection with typed counters and rate-limited logging; the SQLite
  version guard refusing to start below the WAL-reset-fix floor; the
  `db_writer_lock(WriteClass.BULK)` contention skip (typed counter, no
  block, no silent write) and clean recovery once the external lock
  releases.
- `tests/events/test_family_book_telemetry_benchmark.py` (1): the
  production-shaped serialization/insert benchmark above.
- `tests/engine/test_family_book_observation_hook_placement.py` (2):
  source-position proof that capture precedes all three veto-reset points
  and that it's wired to `_active_spine_entry_proofs` (not the full proof
  set).

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
