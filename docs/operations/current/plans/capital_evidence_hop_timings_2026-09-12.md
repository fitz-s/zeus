# `_order_capital_ledger` per-statement timings — 2026-09-12

Hop: `scripts/evaluate_current_regime_capital_advantage.py::_order_capital_ledger`
(~line 1085). 66.8 s of a 123 s incremental run in today's cProfile.

Measured read-only against the live `state/zeus_trades.db` (199 GB, 14 GB WAL
pinned by a foreign reader) through
`sqlite3.connect('file:...?mode=ro', uri=True)`, every statement under a
`threading.Timer(N, conn.interrupt)` watchdog. `WINDOW_DAYS = 35.0`,
`as_of = datetime.now(timezone.utc)`.

## Table sizes (live, this run)

| table | rows |
| --- | --- |
| `venue_commands` | 3,964 |
| `venue_trade_facts` | 5,390 |
| `execution_fact` | 2,687 |
| `venue_submission_envelopes` | (small, PK-only index) |
| `position_events` | 1,521,742 |

Window scope: 1,764 `venue_commands` rows, of which **349 are `intent_kind='EXIT'`**,
spread over **280 distinct `position_id`s**.

## Statements

The hop issues four distinct statements. Two run once; two run once per EXIT
command inside the `for item in grouped.values()` loop.

| # | statement | calls | wall time | status | plan |
| --- | --- | --- | --- | --- | --- |
| S1 | `canonical_trade_fact` + `economic_trade_fact` CTE over `venue_trade_facts` joined to `venue_commands scope` | 1 | 0.946 s | OK | `SCAN fact USING INDEX idx_trade_facts_command` + `SEARCH scope USING INDEX sqlite_autoindex_venue_commands_1` |
| S2 | `venue_commands` LEFT JOIN `venue_submission_envelopes` LEFT JOIN `execution_fact` | 1 | 0.249 s | OK | `SCAN vc` (3,964 rows) + envelope PK seek + `AUTOMATIC PARTIAL COVERING INDEX (command_id=?)` on `execution_fact` |
| S3 | `position_events` `EXIT_ORDER_FILLED` lookup per EXIT command | 349 | 0.267 s total | OK | `SEARCH position_events USING INDEX idx_position_events_position_type_sequence (position_id=?, event_type=?)` |
| **S4** | **`position_events` partial-exit-fill lookup per EXIT command** | **349** | **131.597 s total** (worst single call 3.168 s) | OK | `SEARCH position_events USING INDEX sqlite_autoindex_position_events_3 (position_id=?)` |

S4 returns **78 rows in total** for 131.6 s of work.

### Why S4 dominates

```sql
SELECT payload_json FROM position_events
 WHERE position_id=?
   AND caused_by IN ('partial_exit_fill','partial_exit_economics_repair')
   AND occurred_at<=?
   AND (command_id=? OR lower(COALESCE(order_id,''))=lower(?)
        OR json_extract(payload_json,'$.command_id')=?
        OR lower(COALESCE(json_extract(payload_json,'$.venue_order_id'),''))=lower(?))
 ORDER BY sequence_no,event_id
```

`position_id` is the only indexed term. Every remaining predicate — including
two `json_extract` calls over the full `payload_json` blob — is evaluated per
row of that position's event history. The 280 positions behind the window's
349 EXIT commands hold a large share of the 1.52M `position_events` rows, and
the same position is re-walked once per EXIT command on it (349 calls over 280
positions).

`caused_by` is not indexed on any index of this table:

```
sqlite_autoindex_position_events_1/2/3        (event_id PK; idempotency_key; position_id,sequence_no)
idx_position_events_position_phase_after_sequence      (position_id, phase_after, sequence_no DESC)
idx_position_events_position_type_sequence             (position_id, event_type, sequence_no DESC)
idx_position_events_settled_env_position_sequence      (env, position_id, sequence_no DESC) WHERE event_type='SETTLED'
idx_position_events_entry_execution_occurred_at        (occurred_at DESC, event_type, strategy_key) WHERE event_type IN (...)
```

### Rewrite attempt 1 — `MATERIALIZED` CTE (rejected)

Pushing `position_id`/`caused_by`/`occurred_at` into a `WITH ... AS MATERIALIZED`
inner select so the OR-with-`json_extract` only runs on survivors changes the
plan to the better index but **does not** reduce wall time:

| variant | wall time (349 calls) |
| --- | --- |
| shipped S4 | 131.597 s |
| `MATERIALIZED` CTE | 145.231 s |

Plan after the rewrite:
`MATERIALIZE candidate` → `SEARCH position_events USING INDEX idx_position_events_position_type_sequence (position_id=?)` → `SCAN candidate`.
The cost is the per-position row walk itself, not the OR/json_extract layer, so
a predicate rewrite alone cannot fix this hop.

## Other findings

- `venue_commands.created_at` legacy rows confirmed: exactly **3** rows with
  `created_at NOT LIKE '20%'` (`adopted_exit_95bc07ed1e1b6764` = `1782750705`,
  `adopted_exit_dbd38392154aee63` = `1782934949`,
  `adopted_exit_0c789d808c4901f3` = `1783770572`). `datetime()` of each is
  `NULL`, so they are excluded by the shipped `datetime()` predicates.
  Lexical vs `datetime()` comparison over the window differs on exactly those
  3 rows and no others (1,764 rows match either way).
- `execution_fact.filled_at` non-ISO row count: **0**.
  `venue_trade_facts.observed_at` non-ISO row count: **0**. The existing
  comments in the hop are accurate.
- `SELECT COUNT(*) FROM position_events WHERE occurred_at='QUARANTINE'` →
  **INTERRUPTED >90 s** (full 1.52M-row scan with no usable index). Not needed
  by the hop; recorded because it bounds how expensive any unindexed
  `position_events` predicate is on this DB.

### Rewrite attempt 2 — two-stage rowid join (rejected)

Keeping `payload_json` out of the inner select so non-candidate rows never
touch the blob's overflow pages is likewise no faster, and proves the row set
is unchanged:

| variant | wall time (349 calls) | rows |
| --- | --- | --- |
| shipped S4 | 143.397 s | 78 |
| two-stage rowid join | 141.375 s | 78 |

`equality.TWO_vs_BASE: identical = true`. The cost is the per-position row
walk itself — `caused_by` is unindexed, so every one of a position's events is
visited no matter which columns the projection names.

## Root cause

The 280 positions behind the window's 349 EXIT commands hold **514,668**
`position_events` rows between them (largest single position: 10,642 rows).
S4 walks one position's entire history per EXIT command, and the same position
is re-walked once per EXIT command on it. It returns 78 rows for that work.

## Fix — per-position partial-exit scan index (applied)

`sequence_no` **is** indexed, as the second column of
`sqlite_autoindex_position_events_3 (position_id, sequence_no)`. Measured on
the live tables inside one pinned WAL snapshot:

| step | wall time | result |
| --- | --- | --- |
| frontier `(sequence_no, event_id)` per position, 280 positions | 0.122 s | — |
| `MAX(sequence_no)` per position, 280 positions | 0.158 s | — |
| full walk collecting partial-exit `sequence_no`s, 280 positions | 172.826 s | 78 candidates over 73 positions |
| **seek by `sequence_no IN (...)` per EXIT command, 349 calls** | **0.098 s** | **78 rows** |
| incremental extension scan `sequence_no > frontier`, 280 positions | 4.715 s | 3 new rows |

Extension-scan cost scales with how many events were appended since the prior
run, and the tail of the table grows by roughly 70 rows per 5-minute cycle
(20,000 rowids span 2026-09-10T21:19 → 2026-09-12T23:16):

| backoff below the frontier | wall time | rows walked |
| --- | --- | --- |
| 5 | 0.875 s | 1,400 |
| 50 | 6.507 s | 13,781 |
| 500 | 65.087 s | 123,110 |

So the artifact carries, per position, the frontier `sequence_no`, the
`event_id` sitting at it, and the partial-exit `sequence_no`s found at or below
it. The next run re-reads the event at the recorded frontier; a matching
`event_id` proves nothing at or below it was rewritten (the table is
append-only — no `UPDATE`/`DELETE`/`REPLACE` against `position_events` exists
anywhere in `src/` or `scripts/` — and every writer assigns
`MAX(sequence_no)+1` under `UNIQUE(position_id, sequence_no)`), so the run
walks only `sequence_no > frontier` and unions the hits. A missing prior, a
malformed entry, a candidate above its own frontier, or a different `event_id`
at the frontier drops that position back to a full walk.

The index names **rows, never economics**: every payload at a candidate row is
re-read and re-graded on every run, so a rewritten `realized_pnl_delta_usd`
still changes the answer.

Seek plan:
`SEARCH position_events USING INDEX sqlite_autoindex_position_events_3 (position_id=? AND sequence_no=?)`.

## Byte-equality on the live DBs

One read-only connection, one pinned WAL snapshot (`BEGIN` … `rollback`), one
`as_of`, `_canonical_json_bytes` over the whole hop output:

| run | wall time | `command_count` | bytes |
| --- | --- | --- | --- |
| full walk (`prior=None`) | 73.970 s | 1,761 | 2,164,408 |
| incremental (`prior=` the full walk's output) | 1.954 s | 1,761 | 2,164,408 |
| incremental again (`prior=` the incremental output) | 1.483 s | 1,761 | — |

`BYTE_EQUALITY: identical = true`. `BYTE_EQUALITY_2ND_HOP: identical = true` —
resuming from a resumed artifact also reproduces the full walk exactly, so the
index does not decay across chained runs.

**38x on this hop** (73.97 s → 1.95 s), against 66.8 s of a 123 s incremental
run in today's cProfile.

## What was not measured

- A cold-cache run. The live `-wal` is 14 GB and pinned by a foreign reader;
  every timing here is on whatever page cache the running system had warm, so
  the absolute numbers are lower bounds on cold cost. The full-walk-versus-
  incremental ratio is measured inside one snapshot, so it is not affected.
- `COUNT(*) … WHERE occurred_at='QUARANTINE'` never finished under the 90 s
  watchdog, so the exact count of quarantined `position_events` rows is
  unknown. It does not affect the hop: `occurred_at<=?` excludes `QUARANTINE`
  lexically either way (documented in the hop since 53af70848), and the scan
  index selects rows by `sequence_no`, never by `occurred_at`.
