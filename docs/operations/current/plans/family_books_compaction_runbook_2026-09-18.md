# Runbook — reclaim ~26.8 GB from family_books.db without losing data

Operator-run. I could not execute these myself: stopping the writer and even a read-only
query of the file were both denied by the auto-mode classifier ("Interfere With
Workloads"), which is correct — it is a long-running process and a 55 GB file. Nothing
below is destructive until step 7, and step 7 only deletes a file whose replacement has
already been verified.

## Why this is the cheapest large win on the host

| fact | value |
|---|---|
| file | 55.5 GB (13,550,017 pages x 4096) |
| freelist (dead space) | **26.8 GB** (6,530,785 pages, 48% of the file) |
| live pages → `VACUUM INTO` output floor | **28.8 GB** |
| free space at time of writing | ~39.7 GB (37 GiB) |
| reclaim | **~26.8 GB, losing nothing** |

The file holds only ~5 days of order-book ticks because the capture script runs an
hourly `DELETE` with `--retain-days 5` and **never VACUUMs**, while `auto_vacuum=NONE`.
Freed pages are reused by later inserts but never returned to the OS, so the file sits
at its high-water mark. That is the whole explanation — 48% of a 55 GB file is dead.

`VACUUM INTO` fits because the original is already-allocated space, not newly consumed:
it needs only the 28.8 GB output, leaving ~11 GB margin. (An in-place `VACUUM` would
need original+final ≈ 88 GB and does **not** fit — do not run it.)

Re-check free space before starting; the host was losing several GB/hour to other growth
while this was written.

## Preconditions

- Producer: `scripts/research/family_book_capture.py`, at time of writing **pid 90803**,
  running from an orphaned scratchpad worktree (`/private/tmp/claude-501/.../wt-fbc2/`),
  on unmerged branch `research/family-book-capture`. Companion log follower pid 98397.
  Re-resolve before killing: `pgrep -f family_book_capture`.
- Consumers: **none**. `rg 'family_books|book_top|book_depth|feed_health'` across the
  whole tree returns zero hits outside the capture script itself. Re-verify before
  starting; this is what makes the exclusive lock harmless.
- Not part of the trading mesh, so **no `deploy_live.py restart` and no trading pause is
  required.** Do not restart the mesh for this.

## Steps

**1. Record the truth you will verify against** (before stopping anything):

```bash
sqlite3 "file:state/research/family_books.db?mode=ro" \
  "SELECT 'book_top', count(*) FROM book_top
   UNION ALL SELECT 'book_depth', count(*) FROM book_depth
   UNION ALL SELECT 'feed_health', count(*) FROM feed_health
   UNION ALL SELECT 'token_meta', count(*) FROM token_meta;" | tee /tmp/fb_before.txt
sqlite3 "file:state/research/family_books.db?mode=ro" \
  "SELECT MIN(received_at_utc), MAX(received_at_utc) FROM book_top;" | tee -a /tmp/fb_before.txt
```

**2. Stop the writer** (the step I was denied):

```bash
pgrep -f family_book_capture          # confirm the pid first
kill -TERM 90803                      # graceful; closes its connection on SIGTERM
kill -TERM 98397                      # its `tail -F` log follower
sleep 5; ps -p 90803                   # must print no process line
```

If it does not exit, do **not** `kill -9` while it holds a write transaction — wait, or
checkpoint first. A hard kill mid-write leaves the WAL to recover, which is survivable
but pointless here.

**3. Confirm no writer remains, and drain the WAL:**

```bash
lsof state/research/family_books.db state/research/family_books.db-wal   # expect nothing
sqlite3 state/research/family_books.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

**4. Compact** (~29 GB written; minutes, not seconds):

```bash
sqlite3 state/research/family_books.db \
  "VACUUM INTO 'state/research/family_books.compact.db';"
ls -l state/research/family_books.compact.db     # expect ~28-29 GB
```

**5. Verify the copy before trusting it.** `integrity_check` alone is necessary but not
sufficient — compare the logical content:

```bash
sqlite3 state/research/family_books.compact.db \
  "PRAGMA integrity_check; PRAGMA foreign_key_check;"
sqlite3 "file:state/research/family_books.compact.db?mode=ro" \
  "SELECT 'book_top', count(*) FROM book_top
   UNION ALL SELECT 'book_depth', count(*) FROM book_depth
   UNION ALL SELECT 'feed_health', count(*) FROM feed_health
   UNION ALL SELECT 'token_meta', count(*) FROM token_meta;" > /tmp/fb_after.txt
diff /tmp/fb_before.txt /tmp/fb_after.txt && echo "ROW COUNTS MATCH"
```

`integrity_check` must return exactly `ok`, and the row counts must match step 1. If
either fails, **stop and keep the original** — you have lost nothing.

Note: `VACUUM` can change implicit rowids in tables without an explicit
`INTEGER PRIMARY KEY`. None of these four tables has one, and nothing reads this DB, so
it does not matter here — but do not reuse this runbook on a table whose rowids are
referenced elsewhere without checking that first.

**6. Swap, and set `auto_vacuum` so this cannot recur:**

```bash
mv state/research/family_books.db state/research/family_books.db.preswap
mv state/research/family_books.compact.db state/research/family_books.db
sqlite3 state/research/family_books.db "PRAGMA auto_vacuum=INCREMENTAL; VACUUM;"
sqlite3 "file:state/research/family_books.db?mode=ro" "PRAGMA integrity_check;"
```

`auto_vacuum=INCREMENTAL` only takes effect immediately after a VACUUM, which is why it
is paired here. Once set, the hourly retention DELETEs can self-reclaim via
`PRAGMA incremental_vacuum` instead of growing the file forever. That second VACUUM
needs ~29 GB again transiently, so keep the `.preswap` file until it completes.

**7. Only after step 6 verifies, delete the original** (this releases the 26.8 GB):

```bash
rm state/research/family_books.db.preswap
df -h /System/Volumes/Data
```

**8. Decide the producer's future** — the standing question, not part of the reclaim:

- **Abandon it**: leave it stopped. Nothing reads this DB.
- **Keep it**: promote `scripts/research/family_book_capture.py` from the unmerged
  `research/family-book-capture` branch onto `live` and relaunch it from
  `/Users/leofitz/zeus` (not a scratchpad worktree), with `auto_vacuum=INCREMENTAL` set
  at creation and an `incremental_vacuum` call after each retention pass. Otherwise the
  file regrows to 55 GB in about two weeks.

If it is kept, fix the schema waste while it is stopped: `book_top` re-stores
`token_id` (77 B) + `condition_id` (66 B) + `event_slug` (55 B) on all 16.4M rows for
only 13,860 distinct tokens — 64% of each row — while `token_meta` already holds those
fields keyed by `token_id`. An INTEGER FK removes ~4.1 GB of row bytes plus the same
repetition inside two `(token_id, received_at_utc)` indexes. See
`docs/operations/current/encoding_audit_all_sources_2026-09-17.md`.

Also pending: `state/research/archives/family_books-20260910T233354Z.db.zst` (6.0 GB, a
one-off manual snapshot with no consumer).

## What NOT to do

- Do not run a bare in-place `VACUUM` on the original (needs ~88 GB peak; will fail or
  fill the disk).
- Do not `VACUUM INTO` while the capture process is alive — you would capture a
  mid-write snapshot and the output would not match step 1's counts.
- Do not delete the original before step 5 passes.
- Do not restart the trading mesh for any of this.
