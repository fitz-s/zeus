<!--
Created: 2026-06-01
Last reused/audited: 2026-06-01
Authority basis: #95 SEV-2.1 (pre-arm live-money defect) — world_write_mutex held across network I/O.
-->

# SEV-2.1 (#95): world-DB write mutex held across network I/O — pre-arm fix

## 1. Defect (derived from original code, not from the brief summary)

When the EDLI daemon is armed, the process-global **world-DB write mutex**
(`world_write_mutex` → `_WORLD_DB_WRITE_MUTEX`, `src/state/db.py:253-264`) and
the underlying **SQLite WAL write lock** were both held across the per-event
**network submit**, which performs:

- the **JIT `/book` HTTP GET** — `src/main.py:3896`
  (`with PolymarketClient() as clob: clob.get_orderbook_snapshot(token_id)`,
  wired via `_edli_pre_submit_jit_book_quote_provider`,
  `src/main.py:3882-3899`), and
- the **venue order HTTP POST** — `executor_submit(final_intent, command)`,
  `src/engine/event_reactor_adapter.py:357`.

Both network calls live inside the injected `self._submit` callable, which the
reactor invoked at the OLD `reactor.py` `_process_one` line `submit_result =
self._submit(...)` — **while the mutex was held** from `mutex.acquire()` to
`mutex.release()` spanning the entire `_process_event_unit` body.

This violates the explicit contract on the lock itself:

- `src/state/db.py:247` — *"It MUST NOT be held across network/HTTP calls."*
- `src/state/db.py:262` — *"Hold it around the write txn only; never across HTTP."*
- `src/state/db.py:283-285` — *"Never wrap a venue fetch / HTTP call / long
  compute inside this block — that would hold both the mutex and the WAL write
  lock across I/O and re-introduce starvation."*

Consequence: every world-DB write (the market-channel ingestor, and the reactor
itself across events) serialized behind a slow per-candidate book fetch + venue
POST. On a contended WAL DB the other writer waited out the 30 s `busy_timeout`
→ `database is locked` → reactor cycle hung/skipped (status=FAILED). This is the
same SQLite WAL multi-writer starvation class recorded in memory
(`feedback_sqlite_wal_multi_writer_starvation`).

### Why the Python mutex alone was not the whole root

`EventStore.claim()` (`src/events/event_store.py:148-178`) issues an
`UPDATE opportunity_event_processing` **before** the submit. That UPDATE opens a
write transaction and takes the **WAL write lock immediately**. So even if only
the Python mutex were released around `self._submit`, the WAL write lock opened
by `claim()` would still be held across the HTTP calls. The real invariant is on
the **transaction boundary**, not just the in-process mutex. The fix must commit
the world write unit (closing the WAL write lock) AND release the mutex before
any network I/O.

## 2. Design failure (Fitz level 2-4), not a patch

- **Level 2 (relationship, not function):** the broken relationship is between
  the reactor's *world-DB write unit* (claim → ledgers → mark → commit) and the
  *injected submit seam* (which does network I/O and writes a **different** DB,
  `zeus_trades.db`, never `zeus-world.db`). The world write unit's transaction
  boundary was drawn AROUND the network seam. Semantic context lost at the
  module boundary: the reactor "knew" submit was an in-unit step; it did not
  know submit performs network I/O against a different DB.
- **Level 3 (predecessor):** the codebase already had the right primitive —
  per-event commit (`_commit_event_unit`) that "releases the WAL write lock
  between events". The fix re-uses that primitive at a finer grain (twice per
  event, around the network boundary) rather than inventing a new mechanism.
- **Level 4 (make the category impossible):** split each event into **two
  committed world-DB write windows** with the network submit strictly between
  them, holding **neither** the mutex **nor** an open world txn during the
  network call. The relationship test (`test_world_write_lock_not_held_across_
  network_submit`) makes any future reintroduction fail in CI — an antibody, not
  an alert.

## 3. The fix (only `src/events/reactor.py` + a new test)

`_process_event_unit` (`src/events/reactor.py:181`) is restructured into three
phases. `_process_one` is split at the submit seam into `_process_one_pre_submit`
(`reactor.py:403`) and `_process_one_post_submit` (`reactor.py:464`).

- **Window A — pre-submit world write unit** (`reactor.py:215-247`): acquire
  mutex → `claim()` → `SAVEPOINT` → run all non-network gates
  (`_process_one_pre_submit`). If a gate rejects/retries/dead-letters, finalize
  (`_finalize_disposition`, `reactor.py:296`), `RELEASE`, `commit`, release
  mutex. If all gates pass, `RELEASE` + `commit` (closes the WAL write lock) and
  release the mutex — **before** any network call.
- **Network submit** (`reactor.py:249-260`): `self._submit(...)` runs with **no
  mutex held and no open world txn**. This is the JIT `/book` fetch + venue POST.
- **Window B — post-submit world write unit** (`reactor.py:262-294`): re-acquire
  mutex → `BEGIN IMMEDIATE` (deterministic WAL-write-lock acquisition under
  `busy_timeout`, mirroring `claim()`-first discipline; `reactor.py:266-273`) →
  `SAVEPOINT` → consume the receipt and write decision/receipt ledgers
  (`_process_one_post_submit`) → `_finalize_disposition` (mark_processed / retry)
  → `RELEASE` + `commit` → release mutex.

Counting/disposition semantics are preserved byte-for-byte by routing **both**
windows through the single `_finalize_disposition` helper, which reproduces the
legacy single-pass accounting exactly (`_FSR_PARTIAL_DEAD_LETTER` → no extra
work; `_EXECUTABLE_SNAPSHOT_RETRY` → requeue/dead-letter by attempt count; `None`
→ `mark_processed` + `result.processed += 1`).

### INV-37 compliance

INV-37 governs **cross-DB** writes (single ATTACH+SAVEPOINT connection, never
independent connections). This fix does not change any cross-DB write: the world
ledger writes still go through the one `store.conn`; the trades-DB writes still
happen inside the adapter's own `_run_live_order_build_savepoint`
(`event_reactor_adapter.py`, unchanged). Splitting the **world** write unit into
two single-conn windows is orthogonal to INV-37 and does not introduce a second
world connection.

## 4. 10-step downstream trace (Fitz: trace the fix through all callers)

1. `process_pending` (`reactor.py:165`) → loops events → `_process_event_unit`.
   Unchanged signature/behaviour; same `ReactorResult` fields populated.
2. `_process_event_unit` Window A `claim()` — same UPDATE, same lease semantics;
   a lost claim still commits and returns early (`reactor.py:214-218`).
3. Gate-reject events (SOURCE_TRUTH, RISK_GUARD, MARKET_CHANNEL, REACTOR_NOT_LIVE,
   DAY0_HARD_FACT, FSR-not-COMPLETE) now resolve fully **inside Window A** — they
   never reach the network. Their ledgers (`_reject_event`, `mark_dead_letter`)
   are written and committed exactly as before; `result.processed`/`rejected`/
   `dead_lettered`/`retried` increment identically (verified: events suite 259
   pass).
4. Gate-pass events commit the claim unit, then call submit with the WAL write
   lock **released** → the market-channel ingestor and other reactor events get a
   write window during the HTTP round-trips. This is the starvation cure.
5. Network submit failure (submit raises): caught at `reactor.py:254-259`,
   re-acquires mutex, dead-letters UNKNOWN_REVIEW_REQUIRED. In production the
   adapter `_submit` catches its own exceptions and returns a fail-closed receipt
   (`event_reactor_adapter.py:411-418`), so this path is defensive only.
6. Post-submit NO_SUBMIT receipts: `_process_one_post_submit` runs the
   `compile_no_submit` + cert/regret persistence under Window B; VERIFIED →
   `_no_submit_receipt_ledger.insert_idempotent`; non-VERIFIED → reject or
   `_EXECUTABLE_SNAPSHOT_RETRY` (the "after decision_time" transient class)
   exactly as before.
7. Post-submit terminal EXECUTION_RECEIPT statuses: `_execution_receipt_
   certificate_bundle` persisted under Window B; missing-cert → reject. Identical.
8. `mark_processed` now fires in Window B (post-submit) for gate-pass events and
   in Window A for gate-reject events, never twice — the event drains and is not
   re-claimed next cycle. Verified: concurrency smoke test asserts every event
   ends `processing_status='processed'`.
9. Idempotency / lease reclaim: if the process dies between Window A commit and
   Window B, the event is claimed-but-not-marked → the lease staleness path
   re-claims it next cycle and re-runs submit. Submit-side writes
   (`insert_idempotent`, aggregate events) are idempotent by event_id, so re-run
   does not double-write. (Same durability posture as the prior single-window
   code, which also re-ran on crash before its single commit.)
10. Riskguard / supervisor / control plane: unaffected — no signatures changed,
    no new DB connection, no schema change, no new module import. The venue and
    trades-DB write path in `event_reactor_adapter.py` / `executor.py` is byte-
    identical; only WHEN the world mutex is held around it changed.

## 5. RED → GREEN evidence (relationship test FIRST)

New relationship test:
`tests/events/test_reactor_no_lock_across_submit.py::test_world_write_lock_not_held_across_network_submit`.
It instruments the injected submit callable (the exact production seam for the
`/book` fetch + venue POST) and asserts, at submit time, BOTH:
`world_write_mutex().locked() is False` AND `store.conn.in_transaction is False`
(the WAL-write-lock proxy).

- **RED (original code):**
  `AssertionError: world_write_mutex was HELD while the (network) submit callable
  ran ... assert True is False` — the mutex (and the open world txn) were held
  across submit.
- **GREEN (after fix):** `1 passed`.

## 6. Regression counts (fresh output, base vs branch)

| Suite | Base (HEAD 9b47b5f3) | This branch |
|---|---|---|
| `tests/events/` | 2 failed, 258 passed, 2 xfailed | 2 failed, **259** passed, 2 xfailed |
| `tests/money_path/test_edli_live_canary.py` | 9 failed, 23 passed | 9 failed, 23 passed (identical set) |
| `tests/events/test_reactor.py` concurrency smoke (`test_pr332_db_concurrency_smoke_reactor_world_writes`) | passed | passed |

Net effect: **+1 passing test** (the new RED→GREEN relationship test); **zero new
failures**.

The 2 `tests/events/` failures and the 9 canary failures are **pre-existing on
the base commit** and unrelated to #95:
- `tests/events/` pair (`test_processed_event_terminal_surface_includes_
  execution_receipt_certificate`, `test_live_submitted_execution_receipt_
  certificate_is_terminal_when_submit_enabled`) fail with
  `CertificateVerificationError: pre_submit.tick_size != execution_command.
  tick_size: 0.01 != '0.01'` (a float-vs-string cert-verification defect in
  `src/decision_kernel/verifier.py`).
- canary 9-failure set is identical base-vs-branch (`diff` of the FAILED lists =
  `IDENTICAL_FAILURE_SET`); driver e.g. `taker FOK/FAK live disabled by execution
  policy` — an execution-policy/cert defect outside #95 scope and outside the
  allowed-files lane.

## 7. Files changed (allowed-files lane only)

- `src/events/reactor.py` — `_process_event_unit` split into two committed world
  write windows around the network submit; `_process_one` split into
  `_process_one_pre_submit` + `_process_one_post_submit`; added
  `_finalize_disposition` and `_dead_letter_unknown` helpers; Window B opens
  `BEGIN IMMEDIATE` for deterministic WAL-write-lock acquisition. (+202 / −57)
- `tests/events/test_reactor_no_lock_across_submit.py` — new RED→GREEN
  relationship test (the antibody).
- `docs/operations/SEV21_MUTEX_HTTP_FIX_2026-06-01.md` — this document.

`src/state/db.py`, `src/main.py`, `src/execution/executor.py`, and
`src/engine/event_reactor_adapter.py` were **not** modified: the lock contract in
db.py was already correct, and the violation was entirely in the reactor's
lock-hold span. No edit to `event_reactor_adapter.py` or
`src/strategy/live_inference/**` was required.

## 8. Citations (all grep-verified in the worktree on 2026-06-01)

- `src/state/db.py:247,262,283-285` — lock contract ("never across HTTP").
- `src/state/db.py:253` — `_WORLD_DB_WRITE_MUTEX = threading.Lock()`.
- `src/events/event_store.py:148-178` — `claim()` UPDATE opens the WAL write lock.
- `src/main.py:3882-3899` — JIT `/book` provider; `:3896` `with PolymarketClient()`.
- `src/engine/event_reactor_adapter.py:282,344,357,403` — `_submit` closure;
  `pre_submit_authority_provider` book authority; `executor_submit` venue POST.
- `src/events/reactor.py:181,215-294,296,341,403,464` — the fix.
