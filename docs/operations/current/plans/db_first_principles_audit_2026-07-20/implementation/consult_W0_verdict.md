Verdict — NO-GO as specified; high confidence. The generalized table rebuild is the right operation and is crash-atomic in one single-file WAL transaction, but the live procedure has five blockers: the app writer-lock is not fleet-complete, SELECT * is unsafe under known schema drift, the old AUTOINCREMENT high-water may be lost, both post-check PRAGMAs are accidentally fleet-wide, and W0-c lacks a proven per-fill idempotency key. Correct those and W0-a is GO without changing journal mode.

Revisions and confirmations

[REVISED/HIGH] transaction model — W0_SPEC.md:W0-a — Revising my earlier W0 wording: a dedicated journal_mode=DELETE connection and a full reader fence are not required for crash atomicity here. BEGIN IMMEDIATE through COMMIT against one WAL database is an atomic transaction; readers continue on their old snapshot, and a crash exposes either the old committed schema or the new committed schema, not the intermediate DROP/RENAME state. WAL records the commit by appending a commit record, and SQLite permits readers concurrently with the sole writer. SQLite+2SQLite+2

[REVISED/CRITICAL] operational fence — W0_SPEC.md:W0-a/fence-vs-writer-lock — the required fence is all trades writers, not all readers. In this fleet, the named “trades writer-lock” is not sufficient because the audit already proved four non-mutually-exclusive write-admission schemes. The fact that trade_decisions is frozen only says writes to that table fail; it says nothing about writes to position_current, commands, lots, collateral, events, or any other table in the same file. Acquire a verified all-writer maintenance fence first, then use BEGIN IMMEDIATE as the database-level proof that no writer was already active. Pure read-only processes may remain; if writable versus read-only daemon roles cannot be proven, fence all daemons holding a writable trades handle.

[CONFIRMED/CRITICAL] repair authority — command_recovery.py:6500 — confirming the previous finding: trade_decisions is a projection and must not authorize fill-derived lot repair. The authority is the canonical positive entry fill fact, joined by an exact immutable command identity and protected by a per-fill idempotency key.

1. W0-a: the rebuild is sound, but not with the current runbook

[HIGH] journal mode — W0_SPEC.md:W0-a/connection-choice — changing the live database to DELETE mode adds risk without adding safety: WAL already gives atomicity for this single modified database, whereas journal mode is a database property and WAL mode persists across connections and restarts — leave journal_mode=wal unchanged and use a fresh direct mode=rw connection with no ATTACH. SQLite+1

The migration connection should assert:

sqlite_source_id() is the approved 3.53.2 build
PRAGMA database_list contains main only, apart from an unused temp schema
PRAGMA main.journal_mode = wal
PRAGMA foreign_keys = 1 before migration
PRAGMA legacy_alter_table = 0
PRAGMA synchronous = FULL
PRAGMA fullfsync = 1
PRAGMA wal_autocheckpoint = 0
exact database path, inode and role marker match zeus_trades.db

Use mode=rw, never rwc; do not call get_trade_connection_with_world() and do not ATTACH forecasts. Set synchronous=FULL and fullfsync=ON before beginning: FULL adds a WAL sync at each commit, while macOS fullfsync selects F_FULLFSYNC rather than relying only on ordinary fsync. Disable auto-checkpoint on the migration connection so this tiny schema commit does not unexpectedly become the thread that attempts a large PASSIVE checkpoint. SQLite+2SQLite+2

[HIGH] checkpoint/readers — W0_SPEC.md:W0-a/fence-vs-writer-lock — a concurrent reader or checkpoint cannot expose a half-rebuilt table. A WAL reader retains its original end mark; a checkpoint stops before pages required by that reader and resumes later. The practical hazards are a temporarily pinned WAL, checkpoint latency, and stale application schema caches—not data corruption. Pause the explicit trades checkpoint owner for the migration and reopen/restart every fenced writer connection before releasing the write fence. Do not issue FULL, RESTART, or TRUNCATE checkpoint as part of W0. SQLite+1

[BLOCKER] lock coverage — W0_SPEC.md:W0-a/preamble — acquiring one current application lock does not stop writers using the other three lock namespaces; those writers would receive SQLITE_BUSY while the migration owns the one SQLite write transaction, potentially losing fail-soft money-path writes — require one of these two run gates:

A maintenance lease demonstrably consulted by every trades writer, with zero unleased writers in report-only proof; or

A brief operator fence of all processes capable of writing zeus_trades.db.

Keep the fence from before the rollback capsule is captured until post-commit verification and writer-connection restart complete. Set the migration connection’s busy timeout to zero or very short: failure to acquire BEGIN IMMEDIATE is an abort condition, not a reason to wait through the existing 30-second lock ceiling. SQLite itself guarantees only one simultaneous writer and makes BEGIN IMMEDIATE fail with SQLITE_BUSY when another writer is active. SQLite

[BLOCKER] foreign-key toggle placement — W0_SPEC.md:W0-a/SQL — PRAGMA foreign_keys=OFF must execute and be verified before BEGIN IMMEDIATE; changing it inside a transaction is a no-op — use a fresh connection, assert foreign_keys=1, set it to zero, assert zero, then begin. Re-enable it only after a successful COMMIT or explicit ROLLBACK and assert the connection is back in autocommit mode. SQLite

On every exception, issue an explicit ROLLBACK when the connection is still in a transaction. Some SQLite errors roll back only the failing statement and leave earlier statements in the transaction intact, so “an exception occurred” is not equivalent to “the migration transaction is gone.” SQLite

2. Physical schema is the copy authority; SELECT * is still a blocker

[BLOCKER] schema source — W0_SPEC.md:W0-a/column-drift — using the physical schema as the data-preserving source is correct; using it as an unchecked runtime string transformation is not. The target must be a reviewed literal derived from the current sqlite_schema.sql, preserving every physical column—including p_calibrated—in the same order with the same type, default, collation, CHECK, PK and table options, with exactly one semantic change: remove the REFERENCES ensemble_snapshots(snapshot_id) clause.

The correct authority split is:

Physical sqlite_schema.sql and PRAGMA table_xinfo define what currently exists and must be preserved.

The current db.py comment establishes the intended change: forecast_snapshot_id is a soft reference.

The checked target DDL becomes the new authority and must also replace the divergent db.py CREATE statement in the same code change.

SQLite’s documented generalized ALTER procedure is exactly create-new, copy, drop-old, rename-new. A direct writable_schema text edit is nominally possible for removing a foreign key, but SQLite explicitly warns that a malformed edit can render the database corrupt and unreadable; for 3,106 rows, the rebuild is the superior live-money procedure. SQLite

Before beginning, pin all of these preconditions:

SHA-256 of exact current sqlite_schema.sql
complete PRAGMA table_xinfo('trade_decisions') tuple
PRAGMA table_list flags: ordinary rowid table, same strict/wr settings
complete PRAGMA foreign_key_list('trade_decisions')
trade_decisions_new does not exist in main or temp
no indexes or triggers appeared since census
all global views/triggers/schema objects mentioning trade_decisions inventoried
all incoming FKs whose parent is trade_decisions inventoried

Zero indexes and zero triggers whose tbl_name is trade_decisions do not prove that no view or trigger attached to another object references it. SQLite’s generalized procedure explicitly requires retaining and checking dependent indexes, triggers and views. SQLite

[BLOCKER] positional copy — W0_SPEC.md:W0-a/INSERT-SELECT — replace:

SQL

INSERT INTO trade_decisions_new SELECT * FROM trade_decisions;

with explicit, reviewed target and source column lists:

SQL

INSERT INTO main.trade_decisions_new (
    trade_id,
    ...,
    p_calibrated,
    forecast_snapshot_id,
    ...,
    env
)
SELECT
    trade_id,
    ...,
    p_calibrated,
    forecast_snapshot_id,
    ...,
    env
FROM main.trade_decisions
ORDER BY trade_id;

SELECT * silently converts a future physical column-order difference into value misplacement. The current p_calibrated drift is proof that this is not hypothetical. Generate the list from the reviewed target, commit it as code, and assert at runtime that the live table_xinfo list exactly matches the expected list. Also add a static gate prohibiting columnless INSERT INTO trade_decisions VALUES (...).

[HIGH] data equality — W0_SPEC.md:W0-a/verification — row count and max ID are insufficient — before dropping the old table, require:

old count = new count = 3106
old min/max trade_id = new min/max trade_id
two-way row difference is empty
SQLite storage class for each copied value is unchanged

Because trade_id is unique, a generated two-way EXCEPT over explicit columns is suitable; additionally include typeof(column) or compare typed Python tuples ordered by trade_id. Abort before DROP on any mismatch.

3. AUTOINCREMENT: copying IDs preserves the present maximum, not necessarily history

[BLOCKER] sequence high-water — W0_SPEC.md:W0-a/AUTOINCREMENT — seq >= 4645 is the wrong invariant. AUTOINCREMENT promises that automatically generated IDs exceed the largest committed ID ever used, and SQLite records that historical high-water in sqlite_sequence. Dropping the old table automatically deletes its sequence row; copying existing IDs into the new table establishes a new sequence based on copied IDs. Therefore, if a row above 4645 was previously inserted and later deleted, a plain rebuild can reduce the high-water to 4645 and reuse previously consumed IDs. SQLite+1

The required sequence procedure is:

SQL

-- Inside BEGIN IMMEDIATE, before CREATE/DROP:
SELECT seq
FROM main.sqlite_sequence
WHERE name = 'trade_decisions';

Require exactly one row, integer seq, and seq >= MAX(trade_id). Save that exact old_seq; do not merely save 4645.

After copy, DROP and RENAME:

SQL

SELECT COUNT(*)
FROM main.sqlite_sequence
WHERE name = 'trade_decisions';
-- require exactly 1

UPDATE main.sqlite_sequence
SET seq = :old_seq
WHERE name = 'trade_decisions';

SELECT changes();
-- require exactly 1

SELECT seq
FROM main.sqlite_sequence
WHERE name = 'trade_decisions';
-- require exact equality to old_seq

SELECT COUNT(*)
FROM main.sqlite_sequence
WHERE name = 'trade_decisions_new';
-- require 0

Do not INSERT OR REPLACE: sqlite_sequence.name is not declared UNIQUE, and manual duplicate rows would create an ambiguous sequence state. SQLite permits deliberate updates to sqlite_sequence but warns that arbitrary modifications perturb AUTOINCREMENT behavior, so make one checked UPDATE and abort on any unexpected cardinality. SQLite+1

What a lower reused ID breaks: any durable reference, idempotency key, audit join or external artifact that treated trade_id as never reused can alias a deleted historical decision to a new one. In this repository, the old recovery predicate even compares CAST(trade_id AS TEXT) to command identifiers, which makes preserving non-reuse especially important.

Required sequence antibody: create a clone fixture in which ID 9000 was committed and deleted, leaving MAX(trade_id)=4645 and sqlite_sequence.seq=9000. After migration, the next generated ID must be 9001, not 4646.

4. The proposed post-checks are accidentally heavy and partly tautological

[BLOCKER] integrity scope — W0_SPEC.md:W0-a/postchecks — unqualified:

SQL

PRAGMA integrity_check;

checks the entire 93.9 GiB trades database, not the tiny rebuilt table. Replace it with:

SQL

PRAGMA main.integrity_check('trade_decisions');

and require the sole result ok, both inside the transaction before COMMIT and from a fresh connection afterward. SQLite documents the table argument as a partial integrity check and separately notes that integrity_check does not detect foreign-key violations. SQLite

[BLOCKER] FK-check scope — W0_SPEC.md:W0-a/postchecks — unqualified:

SQL

PRAGMA foreign_key_check;

checks unrelated child tables throughout the database and may turn a tiny hotfix into an uncontrolled live scan. Conversely, foreign_key_check('trade_decisions') after removing its only FK is nearly tautological: a table-name argument checks only constraints declared by that child table. SQLite

Use three bounded checks instead:

PRAGMA foreign_key_list('trade_decisions') exactly equals the expected post-migration list and contains no ensemble_snapshots edge.

Metadata-only fleet antibody: for every main-schema FK, verify the named parent table and columns exist in that same schema and the parent key is valid.

For each child table previously inventoried as having an incoming FK to trade_decisions, run PRAGMA main.foreign_key_check('<child>'). Also run it for trade_decisions if any legitimate outgoing FKs remain.

[HIGH] live smoke test — W0_SPEC.md:W0-a/regression — do not commit a synthetic fake decision row to live solely as a test — prepare the exact production INSERT statement on the clone, and on live use either EXPLAIN of the exact statement or a savepoint-wrapped no-op UPDATE that is rolled back. The old dangling-parent failure occurs during statement preparation, so this proves the missing-table edge is gone without polluting the audit projection. The final proof is the first genuine post-cutover write plus a zero increment in the main.ensemble_snapshots error counter.

5. W0-b: the exact safe gate is canonical entry fill identity

[BLOCKER] recovery predicate — command_recovery.py:6500 — the replacement predicate must mean:

“This exact persisted ENTRY command has at least one canonical, non-reverted, positive venue fill that the normal lot materializer recognizes as final enough, and this exact fill—or an exact quantity/cost delta from it—is not yet represented in position_lots.”

If the outer query already joins venue_trade_facts fact to the command, do not add another EXISTS(venue_trade_facts). That would be tautological. Remove the trade_decisions EXISTS and strengthen the existing row predicate:

SQL

cmd.<command_role> = 'ENTRY'
AND cmd.position_id IS NOT NULL

AND fact.command_id = cmd.command_id          -- exact equality; no OR alias chain
AND fact.<fill_quantity> > 0
AND fact.<state> IN ENTRY_LOT_MATERIALIZATION_FINAL_STATES
AND fact is the current canonical/non-reverted fact

AND fact.token_id = cmd.token_id
AND (
    fact.position_id IS NULL
    OR fact.position_id = cmd.position_id
)

AND the economic quantity represented in position_lots
    for this exact immutable fill identity
    is less than the canonical filled quantity

ENTRY_LOT_MATERIALIZATION_FINAL_STATES must be the same shared predicate used by the normal successful lot writer, not a newly copied list in recovery. W0-b changes only the invalid decision-projection gate; it must not broaden MATCHED/MINED/CONFIRMED semantics at the same time.

Use the fill fact’s quantity, execution price, fees and venue identity. Never reconstruct a lot from command target quantity, requested notional, trade_decisions, or position_current.

[CRITICAL] wrong authority choices — command_recovery.py:6500 — each candidate fails differently:

Candidate used as sole gateFailure mode

position_current has “confirmed entry”Missed repair: the projection may itself be missing or stale because of the same crash. Over-repair: it may have been created optimistically, administratively, or from chain reconciliation without this exact fill.

position_events has ENTRYOver-repair: ENTRY may denote intent, submit, or lifecycle transition before final fill. Missed repair: event persistence may be the failed side of the same partial commit. Duplicate events can also duplicate lots.

Bare EXISTS venue_trade_factsOver-repair: may match an exit fill, zero/cumulative status row, duplicate observation, reverted fill, wrong token or wrong command.

trade_decisionsMissed repair: fail-soft projection is absent for legitimate fills; the current incident proves it.

Exact ENTRY command + exact canonical positive fillCorrect money authority, provided idempotency is keyed to fill identity.

position_current remains useful as an invariant and escalation surface, not as authorization. After identifying a canonical entry fill:

Matching position/token/direction: proceed.

Missing position_current: the economic exposure still exists; materialize the immutable fill lot and raise a critical position-projection recovery case.

Conflicting position/token/direction: quarantine; do not auto-repair either projection.

[BLOCKER] fill granularity — command_recovery.py:6500; position_lots schema — W0-c is not safe until the fact grain is explicit:

If venue_trade_facts is one immutable row per fill, key the lot by that stable fill ID.

If it stores cumulative order-state observations, do not make one lot per fact row. Select the canonical latest cumulative fact and insert only the exact positive shortfall versus already represented cumulative quantity/cost.

If facts can be reversed or replaced, recovery must honor the canonical/reverted marker.

Verify locally: establish whether venue_trade_facts is fill-grain or cumulative-snapshot-grain and identify the exact normal-path state predicate. This is the one runtime fact that determines the final SQL.

[BLOCKER] idempotency — position_lots schema; W0_SPEC.md:W0-c — “run the scan idempotently” is not an implementation. Require a database-enforced deterministic identity such as:

UNIQUE(source_venue, source_fill_id, lot_role)

or an existing equivalent. A position-level NOT EXISTS(position_lots WHERE position_id=...) is wrong because one partial fill can mask another missing partial fill. If no stable source-fill key or deterministic cumulative-repair key exists, W0-c is NO-GO until one is introduced; a check-then-insert without a UNIQUE constraint is not crash/retry idempotency.

6. W0-c: repair lots, do not fabricate decisions

[HIGH] reconciliation scope — W0_SPEC.md:W0-c — the correct W0-c action is a fill-to-lot reconciliation, not synthetic trade_decisions backfill — generate a dry-run manifest containing at least:

source_fill_id or deterministic cumulative fact key
command_id
position_id
token_id
canonical fill state
filled quantity
filled cost and fee
currently represented lot quantity/cost
proposed delta
eligibility reason
quarantine reason, if any

Run read-only detection over all history, but make the first mutation cohort the known July 2→cutover gap. Apply by exact source key, emit a repair-audit event linking the source fact to the created/corrected lot, then rerun the same detector and require zero eligible rows.

[HIGH] projection gap — W0_SPEC.md:W0-c/backfill — do not synthesize historical trade_decisions rows as though they were original decisions. Record an explicit trade_decisions projection-gap interval and preserve it in operational documentation/metrics. A later projection rebuild is acceptable only when every field is sourced from authoritative artifacts and the row is unmistakably tagged recovered_projection, with original versus recovery timestamps kept distinct.

[HIGH] reader inventory — trade_decisions readers fleet-wide — W0-b closes the known money-path dependency, but “no historical backfill required” is valid only after every other SELECT, JOIN, and EXISTS reader of trade_decisions is classified.

Verify locally: grep all runtime readers and classify each as ledger authority, projection/reporting, diagnostics, or dead code. Any second money-path reader must be re-anchored before the gap is declared harmless.

[HIGH] unfreeze backlog — trade_decisions writer paths — removing the FK causes every currently executing writer path that formerly failed to start succeeding immediately. Before releasing the fence, prove there is no durable retry/outbox backlog that will replay July 2→present writes as if they were current, and that repeated lifecycle/exit-audit writes have stable idempotency. Do not assume “fail-soft” means “discarded rather than queued.”

7. Safest rollback point and artifact

[HIGH] rollback — W0_SPEC.md:RUN-GATE — the primary rollback mechanism is the SQLite transaction itself. Any failed assertion before a successful COMMIT must cause an explicit ROLLBACK; no table restoration should be necessary. The commit record is the state boundary in WAL. SQLite

Before BEGIN IMMEDIATE, while the all-writer fence is held, create a scoped rollback capsule using .venv/bin/python/SQLite 3.53.2—not the forbidden 3.51.2 system CLI. The capsule should contain:

exact original sqlite_schema.sql
complete ordered table_xinfo and foreign_key_list
all 3,106 rows with SQLite storage classes preserved
original sqlite_sequence.seq
all schema dependency SQL mentioning trade_decisions
row count, min/max trade_id and typed row digest
source database identity, inode, schema_version and sqlite_source_id
capsule file SHA-256

A tiny standalone SQLite file containing the one exported table plus metadata is safer than an ad hoc CSV. Close it cleanly, reopen it independently, validate its digest, fsync the file and parent directory, then make it read-only.

After COMMIT, do not restore the whole DB and do not casually reintroduce the dangling FK. If verification uncovers a data-copy problem, fence writers again and perform a reverse single-table rebuild from the capsule. If an exception occurs during COMMIT and its outcome is uncertain, close the connection and classify the live state from a fresh process using the old/new schema fingerprints, row digest and sequence—never assume which side won.

8. Correct execution order

[HIGH] wave ordering — W0_SPEC.md:W0-a/W0-b/W0-c — W0-b does not need to become active before W0-a. The lowest-risk operational sequence is:

Implement W0-b and run its old-versus-new predicate in shadow/report-only mode. It must not insert lots.

Land the exact target db.py DDL, migration script, antibodies and schema fingerprint.

Acquire the all-trades-writer fence and pause the explicit checkpoint owner.

Capture and validate the scoped rollback capsule.

Execute corrected W0-a.

Reopen on a fresh connection, verify schema/data/sequence, perform the rolled-back compile smoke test, and restart/reopen every fenced writer connection.

Release writers and verify genuine trade_decisions writes resume with no new main.ensemble_snapshots errors.

Activate W0-b’s fill-authority predicate.

Run W0-c dry-run, operator-review the manifest, apply, and require the second run to find zero repairs.

Why A before active B: W0-a restores the existing gate for new post-cutover decisions, so it stops creating additional projection-caused misses while the safer fill predicate finishes validation. Activating an insufficiently proven B first can over-repair money immediately. A-first leaves only the already-known historical gap for the short interval; it does not create a doubly broken state.

9. Minimal corrected W0-a procedure

[GO-AFTER-CORRECTION] live schema migration — W0_SPEC.md:W0-a — use this shape:

SQL

-- Fresh direct mode=rw connection; no ATTACH.
-- Assert path/inode/role/source_id, journal_mode=wal, expected source DDL.
PRAGMA synchronous=FULL;
PRAGMA fullfsync=ON;
PRAGMA wal_autocheckpoint=0;
PRAGMA legacy_alter_table=OFF;

PRAGMA foreign_keys=ON;
-- assert 1
PRAGMA foreign_keys=OFF;
-- assert 0

BEGIN IMMEDIATE;

-- Recheck every precondition inside the write transaction:
-- source DDL hash, table_xinfo, FK list, dependencies,
-- absence of trade_decisions_new, count/min/max, and old_seq.
-- Require exactly one integer sqlite_sequence row and old_seq >= max(trade_id).

CREATE TABLE main.trade_decisions_new (
    -- reviewed physical schema, exact order/defaults/checks,
    -- forecast_snapshot_id INTEGER with no REFERENCES clause
);

INSERT INTO main.trade_decisions_new (
    trade_id,
    -- every remaining column explicitly, including p_calibrated
)
SELECT
    trade_id,
    -- same explicit list
FROM main.trade_decisions
ORDER BY trade_id;

-- Require exact typed row equality, count/min/max equality,
-- and the expected new-table FK list before destroying old data.

DROP TABLE main.trade_decisions;
ALTER TABLE main.trade_decisions_new RENAME TO trade_decisions;

-- Require exactly one final sequence row, then preserve historical high-water.
UPDATE main.sqlite_sequence
SET seq = :old_seq
WHERE name = 'trade_decisions';
-- require changes() = 1, seq = old_seq, and no _new sequence row

-- Require exact final DDL/table_xinfo/FK fingerprints.
-- Run only table-scoped integrity and affected-child FK checks.
PRAGMA main.integrity_check('trade_decisions');

COMMIT;

PRAGMA foreign_keys=ON;
-- assert 1 and no active transaction

Then close that connection and verify from a fresh direct connection before releasing the writer fence.

Mandatory green gates

[BLOCKER] crash matrix — tests/test_*trade_decisions_fk_rebuild*.py — kill at after BEGIN, after CREATE, midway through copy, after DROP, after RENAME and immediately before COMMIT; reopen from a separate process and require exactly old state or exactly new state, never _new residue or mixed rows. A kill -9 matrix proves application-crash behavior, not actual power-loss durability; the live run still requires FULL plus macOS fullfsync. SQLite+1

[BLOCKER] drift matrix — same tests — include physical p_calibrated, an unexpected extra column, changed default, incoming view/FK, pre-existing _new, and source-DDL hash mismatch; every unexpected shape must abort before DROP.

[BLOCKER] sequence matrix — same tests — include old_seq=max_id, old_seq>max_id, gaps, and second-run no-op.

[BLOCKER] concurrency matrix — same tests — hold a long reader across migration and prove old-snapshot/new-snapshot behavior; start an uncoordinated writer and prove the operator script aborts at fence/BEGIN rather than continuing. Readers retaining a historical snapshot while another connection commits are normal SQLite transaction semantics. SQLite+1

[BLOCKER] recovery matrix — tests/*command_recovery* — include old pre-gap fill, post-July-2 fill without decision projection, multiple partial fills, duplicate recovery invocation, exit fill, zero fill, reverted fill, mismatched token/position, missing position_current, optimistic ENTRY event without fill, and fill without ENTRY event. Only exact canonical entry-fill shortfalls may create lots.

Final call

W0-a as currently written: NO-GO.

W0-a after the corrected fence, explicit schema/copy, exact sequence preservation and scoped checks: GO.

DELETE journal mode: reject.

Full reader fence: unnecessary.

Verified all-writer fence: mandatory in the current fragmented-lock topology.

W0-b anchor: exact canonical entry fill joined to its persisted ENTRY command.

W0-c: NO-GO unless position_lots has a database-enforced per-fill or deterministic cumulative-repair identity.

Activation order: B shadow → corrected A → B active → C dry-run/apply.

Historical trade_decisions synthesis: reject for W0.