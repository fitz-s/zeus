# Adversarial verify — W0-a trade_decisions dangling-FK rebuild (live-money)

HEAD (worktree db-impl): 61506ec6346c5ff0127e5c0d8ccb429811deb150
Verifier: verifier (adversarial)
Date: 2026-07-21
Target: scripts/migrations/202607_trade_decisions_drop_dangling_fk.py
Suite:  tests/test_trade_decisions_fk_rebuild.py — 14 passed (re-run, venv SQLite 3.53.2, source_id matches APPROVED_SOURCE_IDS)

## Claim under test
The migration rebuilds `trade_decisions` in zeus_trades.db to drop the dangling
`REFERENCES ensemble_snapshots(snapshot_id)` FK, crash-atomically in one WAL
transaction, preserving all rows/values/storage-classes and the AUTOINCREMENT
high-water, encoding the 5 consult corrections, safe to run against the 94 GB live DB.

## VERDICT: SAFE TO PRESENT — WITH 3 NAMED FIXES (no BLOCKING defect found)

Every fault I injected — row loss in the copy, a dependent view, a schema drift,
each pre-COMMIT kill point — **failed safe**: the transaction rolled back to the
exact pre-migration state (FK present, row count intact, no `_new` residue). I could
not produce data corruption, row loss, a reused id, or a committed mixed state. The
money-safety core is sound. Three MATERIAL items are operational-robustness /
recoverability, not data-safety, and should be fixed before the live run.

---

## The 5 consult corrections — each verified against reality, not the claim

**B1 all-writer fence — IMPLEMENTED (proven).** `_assert_writer_plane_fenced`
(line 177) refuses without `--operator-confirms-fenced` (suite
`test_refuses_without_operator_confirms_fenced`), AND the ps-scan
(`_live_zeus_processes`, line 147) refuses when a daemon-pattern process runs.
PROBE: I launched a sentinel `python … src.engine.cycle_runner` and called the real
scan (no skip env): it returned **8** daemon hits — my sentinel **plus 7 real zeus
daemons currently running in the main tree** — and `_assert_writer_plane_fenced(True)`
raised `REFUSED: … a zeus daemon is still running`. The fence would block a live run
right now. Note: no antibody exercises this half — every test sets
`ZEUS_W0A_SKIP_PROCESS_CHECK=1` (see MINOR-2).

**B2 no SELECT\*, explicit pinned columns — IMPLEMENTED, but its "two-way diff"
layer is defective (MATERIAL-1).** Copy uses explicit `EXPECTED_COLUMNS` both sides
(line 411-413); `_assert_schema_pinned` sha256-pins the CREATE and asserts the
column tuple; `test_schema_drift_aborts_before_write` passes. The typed digest
(line 421) is the sound data-equality guard. The "two-way EXCEPT" (line 424-428) is
NOT two-way — see MATERIAL-1.

**B3 exact sequence high-water — IMPLEMENTED (proven).** PROBE
(probe_seq9000.py): fixture with id 9000 committed-then-deleted, max(trade_id)=4645,
sqlite_sequence.seq=9000. Ran the real migration → rc 0 → post seq=9000 → next
inserted id = **9001, not 4646**. No id reuse. The live shape (seq==max==4645) is
covered by the suite's `(4, False, 5)` param, which exercises the same-value
`UPDATE … SET seq=old_seq` / `changes()==1` path. The exact consult-mandated
9000/4645 antibody is nonetheless absent from the suite (MINOR-1).

**B4 table-scoped checks — IMPLEMENTED (proven).** `grep` of the whole script: the
only integrity check is `PRAGMA main.integrity_check('trade_decisions')` (line 458);
there is **no** `foreign_key_check` anywhere (only metadata-only `foreign_key_list`,
lines 267/451/487). No unqualified full-DB scan can slip through on any path,
including `_verify_fresh`.

**B5 W0-c not in this script — CONFIRMED.** The script only rebuilds trade_decisions;
no position_lots / fill-to-lot logic. Docstring B5 note correct.

---

## MATERIAL findings (fix before live run; none lose money data)

### MATERIAL-1 — the "two-way typed EXCEPT" diff is actually ONE-WAY (masked, not exploitable as written)
Line 423-430. Comment claims "two-way typed EXCEPT over explicit columns." SQLite
compound set-operators are **equal-precedence, left-associative**, so
`A EXCEPT B UNION ALL C EXCEPT D` parses as `((A EXCEPT B) UNION ALL C) EXCEPT D`,
which algebraically reduces to `count(NEW \ OLD)` — it detects rows ADDED in new but
is blind to rows LOST from new.

PROBE (direct SQL, exact expression shape):
- lost-row (old has it, new doesn't): migration-diff = **0** (false pass); correct = 1
- altered-value (counts equal):        migration-diff = 1;  correct = 2
- extra-row:                           migration-diff = 1;  correct = 1

Why NOT BLOCKING: the diff runs AFTER two strictly-stronger guards. I fault-injected
a real row loss into the copy statement (`… WHERE trade_id != (SELECT min…)`):
- count/digest KEPT (as shipped): aborted, `caught_by='copy count/min/max'`, rolled
  back, original 5 rows + FK intact.
- count/digest DISABLED (buggy diff as sole guard): **committed with 4 rows — silent
  data loss.**
So the count check (line 418) catches loss; the typed digest (line 421) catches
value/type/storage-class change (EXCEPT treats 1==1.0, so the diff couldn't catch a
storage-class flip anyway — the digest is what does). The diff contributes ZERO
independent protection for the loss direction and its label lies.
Antibody catches it? No — count/digest mask it, and no test isolates the diff.
FIX: parenthesize the two halves via subqueries/CTE, or delete it and rely on the
count+digest (which are sufficient) — do not ship a verification layer that doesn't
do what its comment claims.

### MATERIAL-2 — dependent view/trigger not inventoried + legacy_alter_table=OFF → RENAME aborts mid-fence
Line 237 sets `legacy_alter_table = OFF`; the pin check (line 274-279) only rejects
index/trigger with `tbl_name='trade_decisions'` — it does **not** inventory global
VIEWS or cross-table TRIGGERS that *reference* trade_decisions. The consult's
mandatory precondition list includes "all global views/triggers/schema objects
mentioning trade_decisions inventoried" — unimplemented. T5 (the pattern this script
claims to mirror) sets `legacy_alter_table=ON` around its RENAME precisely to survive
this.

PROBE: fixture + `CREATE VIEW v_recent AS SELECT trade_id, price FROM trade_decisions`.
The pin check PASSED (view invisible to it); the copy/count/digest PASSED; DROP
succeeded; then `ALTER TABLE trade_decisions_new RENAME TO trade_decisions` (line 434)
raised `sqlite3.OperationalError: error in view v_recent: no such table:
main.trade_decisions`. The `except BaseException → ROLLBACK` fired → post-state
fk=True, rows=5, no `_new` residue (FAIL-SAFE). Same for a trigger on another table
referencing trade_decisions.
Confirmed fix works: replaying the DROP/RENAME with `legacy_alter_table=OFF` → RENAME
FAILS; with `=ON` → RENAME OK and the view still resolves `[(1,'a'),(2,'b')]`.
Live exposure: `rg` finds NO repo-defined view/trigger on trade_decisions — but the
consult itself proved the physical schema has drifted from db.py (the p_calibrated
column), so an ad-hoc object not in the repo cannot be excluded from code alone. If
one exists, the FIRST real run aborts at RENAME with a raw traceback **after** the
operator has stopped every daemon and taken the fence — disruptive, though not
corrupting.
Antibody catches it? No — no fixture adds a dependent view/trigger.
FIX: before BEGIN, inventory `sqlite_master WHERE type IN ('view','trigger') AND
sql LIKE '%trade_decisions%' AND name != 'trade_decisions'` and refuse early with a
clear message; and/or set `legacy_alter_table=ON` around the RENAME (T5 pattern) so a
legitimate dependent view auto-updates instead of aborting. Add a drift-matrix test.

### MATERIAL-3 — rollback capsule is never reopened/validated
`_write_capsule` (line 317) closes the capsule, fsyncs file+dir, computes a
file-level sha256, writes `.sha256`, and chmods 0444 — but never reopens it to
recompute the typed row digest over its stored rows and compare to the digest it
saved in `_capsule_meta`. Consult §7 explicitly requires "reopen it independently,
validate its digest." The capsule is the named post-COMMIT reversal artifact.
PROBE: I reopened a produced capsule (mode 444, 4 rows) and recomputed the typed
digest → **matched** the stored digest. So capsules are faithful in practice; the gap
is purely the missing self-check — a silently-corrupt capsule (write-time disk error,
future storage-class coercion) would pass undetected and give the operator false
rollback confidence.
Antibody catches it? No test corrupts a capsule.
FIX: after close+fsync, reopen mode=ro, recompute `_typed_row_digest` over the
capsule's rows, assert == stored digest AND row_count/min/max match, then chmod.

---

## MINOR findings
- **MINOR-1** — the consult-mandated antibody "id 9000 committed+deleted, max=4645,
  seq=9000 → next id 9001" is absent; the suite's sequence coverage is only gap-of-1
  `(5,True,6)`/`(4,False,5)`. Migration handles the wide-gap case correctly (proven
  above), but the required green-gate test is missing. Add it (fixture:
  insert id 9000, delete it, assert next id 9001).
- **MINOR-2** — the ps-scan half of the fence is bypassable by a stray env var
  `ZEUS_W0A_SKIP_PROCESS_CHECK=1` (line 153). The `--operator-confirms-fenced` flag
  remains mandatory, and this matches T5, but a leaked env var silently disables half
  the fence and no test/audit surfaces it. Operator note at minimum.
- **MINOR-3** — capsule fsync is `os.fsync` on an O_RDONLY fd and the capsule DB is
  not F_FULLFSYNC'd; best-effort durability on a secondary artifact.
- **MINOR-4** — no explicit inode / role-marker assertion (consult listed it);
  effectively covered by the CREATE-sha256 pin (wrong DB → no matching trade_decisions
  → REFUSED), so redundant, but not literally implemented.
- **MINOR-5** — capsule filename is second-granularity (`[:15]`); a second run within
  the same wall-clock second refuses (`capsule already exists`). Safe (refuse, not
  overwrite), but a dry-run immediately followed by the real run in the same second
  would block the real run.
- **MINOR-6** — the post-COMMIT `PRAGMA foreign_keys=ON` + `in_transaction` check
  (line 471-473) sits inside the try; if it raised after a successful COMMIT,
  `_verify_fresh` would be skipped even though the migration already committed.
  Confusing, not unsafe.

## Crash atomicity / idempotency / failure handling — verified OK
- Kill matrix: single `BEGIN IMMEDIATE … COMMIT`; every pre-COMMIT statement is in
  the one atomic unit, so no untested kill point between two statements can leave a
  committed `_new` residue or mixed rows. Suite `test_crash_leaves_old_state_intact`
  (6 params) reopens from a fresh process and asserts old-state survival — all green.
  (kill -9 proves app-crash; true power-loss durability rests on
  synchronous=FULL+fullfsync, both set line 234-235, acknowledged in the docstring.)
- Failure handling: proven via fault injection — any assertion failure mid-txn →
  `except BaseException: if conn.in_transaction: ROLLBACK; raise`. No swallowing
  except on the money path (the ps-scan `except` is fail-open but flag-gated). FK is
  re-enabled after COMMIT.
- Idempotency: `test_idempotent_second_run_refuses` passes (post-migration sha
  mismatch → REFUSED). A pre-COMMIT crash leaves the original table, so a re-run
  proceeds cleanly.
- Transaction model matches the [REVISED] consult: stays journal_mode=wal, no ATTACH,
  mode=rw not rwc, no reader fence, no DELETE-mode switch, no explicit checkpoint.

## One-line verdict
SAFE TO PRESENT TO THE OPERATOR WITH NAMED FIXES — no BLOCKING data-loss/corruption
path exists (every fault rolls back to the exact pre-migration state); fix MATERIAL-2
(dependent-view inventory / legacy_alter_table=ON) and MATERIAL-3 (capsule
self-validation) before the live run, and MATERIAL-1 (one-way diff) for honesty of
the verification layer.
