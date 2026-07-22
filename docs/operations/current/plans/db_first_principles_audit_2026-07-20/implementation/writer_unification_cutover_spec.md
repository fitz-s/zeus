# W1 writer-unification cutover — corrected spec (post adversarial review)

Lifecycle: created=2026-07-21. Design of record for unifying the four write-intent lock
schemes + the ~336 no-intent-lock direct-connection writers onto the WriteCoordinator, on
the 24/7 live-money fleet. **Supersedes** the initial design in `writer_unification_context.md`
(scratchpad), which an opus adversarial review (`cutover-critic`, 2026-07-21) found had **two
CRITICAL deadlocks**. Both were verified against the code before this rewrite.

Authority: `findings/connections_throughput.md` (the 4-scheme + 336-no-lock + live-storm
measurement); `src/state/write_coordinator.py`, `src/state/db_writer_lock.py`, `src/state/db.py`.

## The problem (measured)
Four mutually-invisible write-intent lock schemes guard the same 3 DB files: (1)
`db_writer_lock(db,LIVE)`→`.writer-lock.live`; (2) `db_writer_lock(db,BULK)`/BulkChunker→
`.writer-lock.bulk`; (3) `WriteCoordinator.lease`→unified `.writer-lock` (already live in 3
ingest processes — a THIRD scheme, not a fix); (4) `world_write_mutex`. PLUS ~336 direct
`get_*_connection` writers that take NO intent lock (`_resolve_write_class` only increments a
counter, db.py:289-291). Cross-scheme writers don't exclude → the live `database is locked`
storm (13,749 + 10,004 + 3,906, recurring).

## What the review corrected (each verified against code)

**C1 (CRITICAL, was a deadlock) — do NOT fold the world in-process lock into the bridge union.**
The initial design acquired the world in-process `threading.Lock` as part of the per-DB union
(and last). But `_GuardedWorldMutex.acquire()` is a TWO-LAYER lock (db.py:519-542, docstring
508-516): it takes the in-process `threading.Lock` FIRST (db.py:526), THEN the world
`.writer-lock.live` flock (db.py:531-535). A bridge acquiring world `.live` then the in-process
lock is the exact inverse → AB-BA deadlock with the EDLI reactor holding the in-process lock and
waiting on world `.live`.
**Fix:** never fold the in-process lock. Scheme-4 is ALREADY subsumed by the world `.live`
FLOCK, because `_GuardedWorldMutex` itself takes that flock and `flock()` excludes two
open-file-descriptions even within one process. The bridge acquiring world `.live` already
excludes every scheme-4 writer, in-process and cross-process. The subsuming mechanism is the
`.live` flock — NOT the coordinator's own per-DB `threading.Lock` (a different object).

**C2 (CRITICAL, was a fleet-wide self-deadlock) — implement lease re-entrancy BEFORE any pushdown.**
The coordinator's per-DB gate is non-reentrant on two axes: the per-DB `threading.Lock`
(write_coordinator.py:166-168) blocks a same-thread re-acquire, and each `lease()` does a fresh
`os.open`+`flock(LOCK_EX|LOCK_NB)` (write_coordinator.py:366-390) so a nested lease on the same
DB gets a SEPARATE OFD and self-blocks to `WriteLeaseTimeout`. Every flocked helper opens a
connection WHILE holding its locks (e.g. `forecasts_connection_with_trades_flocked` does
`_connect(...)` at db.py:898 inside the double flock; `trade_connection_with_world_flocked` does
`get_trade_connection(...)` at db.py:1231). So "pushdown into `_connect` after migrating the
flocked helpers" makes each migrated helper `lease()` then open a connection that re-acquires the
same DB gate → self-deadlock everywhere at the pushdown step.
**Fix:** make the coordinator gate re-entrant via a **thread-local per-DB lease depth** that
no-ops the nested acquire — reusing the tree's own prior art `_world_mutex_tls().held_depth`
(db.py:540-541, 550-553). Tell that this is required: the coordinator's own `transaction()`
already dodges re-entrancy by opening its connection with `write_class=None`
(`_default_connection_factory`, write_coordinator.py:438-441) — i.e. the factory must NOT
re-acquire from inside a lease, which is exactly what pushdown would do without re-entrancy.

**M1 (MAJOR) — "no per-site edit" is false; `_connect` cannot tell reads from writes.**
`_connect` returns a full RW handle and only counts the class (db.py:289-291). The same
no-write-class factory serves writers (`ingest_main.py:2959`) AND readers (`main.py:4663/4951/
5379`); 73 of 154 direct `get_*_connection(` sites pass no `write_class`. Fire the gate on every
write-factory connection → serialize the readers too and kill WAL reader-concurrency; fire only
when `write_class` is set → miss the 73 (some of which write). Either way, write intent must be
made EXPLICIT at the factory (bounded per-site edits + a read/write audit). Also: default
`isolation_level=""` writers issue no explicit BEGIN (lazy DEFERRED on first DML), so a
BEGIN-IMMEDIATE-only hook misses them.

**M2 (MAJOR) — migrate per-daemon-restart, not per-writer.**
A process boots one code version, so all ~66 writers in `src.main` migrate TOGETHER at its
restart; you cannot have 1 migrated and 65 not in the same live process. Reversibility is
per-deploy (revert + restart), not per-writer. Because `deploy_live.py restart all` is a ROLLING
restart (new + old daemons coexist), the bridge must live in the NEW code from the first restart,
and the flag that drops old-file acquisition can flip only AFTER a full fleet cycle — and after
scheme-4 world writers are migrated off `world_write_mutex` (that mutex keys on `.live`, which
the post-flag coordinator no longer takes).

**M3 (MAJOR) — BULK-yield needs a LIVE-intent signal file; the end state is not literally one file.**
Two-file scheme: LIVE never queues behind BULK; `BulkChunker._is_live_contended` probes the
`.live` file (db_writer_lock.py:311-337) and yields the SQLite write lock. On one unified
`.writer-lock`, LIVE must acquire the same file BULK holds, so yield-detection needs a SEPARATE
LIVE-intent signal — and `flock` is not FIFO, so after BULK release-then-reacquire
(db_writer_lock.py:339-378) another BULK writer can win and LIVE starves.
**Fix / accepted end state:** one EXCLUSION file (`.writer-lock`) + one LIVE-intent SIGNAL file
(keep `.live` as a pure signal). BULK probes the signal and yields; FORBID BULK re-acquire while
the signal is set. Not literally "one file" — one exclusion + one signal.

**MINOR — unify the canonical lock-order function.** Coordinator orders DBs by FULL resolved path
(write_coordinator.py:182-184); the flocked helpers order by BASENAME (`canonical_lock_order`→
`p.name`, db_writer_lock.py:934-940). They agree ONLY because all three DBs share STATE_DIR
(db.py:68-70,199-201). `_zeus_trade_db_path()` is relocatable; if any DB moves dirs the orders
diverge → cross-DB lock-order inversion. Unify on one order function.

## Corrected cutover (GO on this shape; the review's refined plan)
1. **Bridge lives in new code from day one.** During the rolling-restart coexistence window the
   coordinator acquires the UNION of per-DB FILE locks: `.writer-lock` + `.writer-lock.live` +
   `.writer-lock.bulk`, in the single global DB order (forecasts < world < trades) the flocked
   helpers already use → acyclic wait-for graph, deadlock-free (un-migrated single-DB writers
   hold one lock; the bridge acquires all of a DB's union before the next DB). **Not** the world
   in-process lock (C1).
2. **Lease re-entrancy first** (thread-local per-DB depth, C2) — land + fixture BEFORE pushdown.
3. **Explicit write-intent at the factory** (M1) — bounded edits + a read/write audit of the 154
   sites; the gate fires on declared write intent, never on read connections.
4. **Pushdown** the gate into the write-connection lifecycle for the now-declared writers.
5. **BULK-yield** via the `.live` LIVE-intent signal (M3); forbid BULK re-acquire while set.
6. **Migrate per-daemon-restart** (M2); flip the drop-old-locks flag only after a full fleet
   cycle + scheme-4 migration. Unify the order function (minor).

## Path to ACCEPT (review's bar) + open item
Specify the re-entrancy primitive; do not fold the world in-process lock; discriminate read/write
at the factory; ship TWO deadlock fixtures — (i) same-process reactor+bridge on world (proves C1
gone), (ii) nested lease+pushdown (proves C2 gone). INV-37 preserved throughout —
`transaction()`'s `CrossDatabaseTransactionUnsupported` (write_coordinator.py:248-305) is a keeper.
**Open grep before relying on the C1 fix:** does any live path hold `world_write_mutex` and THEN
call a flocked helper that flocks world `.live` again on a separate fd? (pre-existing self-block
surface, not introduced here.)

## Status / sequencing
W1 (survival). The implementation is a LARGE, operator-fenced build (re-entrancy primitive +
write-intent audit across 154 sites + bridge + LIVE-intent signal + per-daemon-restart migration +
the two deadlock fixtures), shipped as worktree-authored + tested + operator-gated deploy behind
the rolling-restart fence. It is NOT a blind single-flip change. W5-8 fail-open connect
(`must_exist` fail-closed, `w5_8_fail_open_connect_design.md`) co-locates in `_connect` and lands
in the same connection-layer pass. A second independent review (GPT-5.6 consult
`REQ-20260721-204133`) was fired in parallel; if it lands it is corroboration, not a gate — the
opus critic's review above is verified and sufficient to proceed to implementation design.
