# `_current_total_portfolio_capital` and the two `_realized_curve_with_deadline` hops — 2026-09-13

Follow-up to `capital_evidence_hop_timings_2026-09-13.md` (`_held_to_binary_settlement_quality`).
T-capchild's trace of the 77s capital-evidence child timeout named these as the
remaining un-frontiered hops: `_current_total_portfolio_capital` (~21.6s
measured by X-E2) and the two `_realized_curve_with_deadline` calls (up to 40s
worst case, "not measured" in either prior doc).

Measured directly against the live `state/zeus_trades.db` (199 GB) through
`sqlite3.connect('file:...?mode=ro', uri=True)`.

## `_current_total_portfolio_capital` — one statement, no index

Per-statement instrumentation (proxy connection wrapping `execute`), one call:

| statement | calls | wall time |
| --- | --- | --- |
| **`collateral_ledger_snapshots` latest-snapshot lookup** | **1** | **9.053 s** |
| `collateral_reservations` SUM | 1 | 0.005 s |
| `collateral_unsettled_proceeds` SUM | 1 | 0.0003 s |
| `position_current` active-phase list | 1 | 0.0001 s |
| `execution_feasibility_latest` per active position | 40 | 0.003 s total |

Whole hop: **9.063 s**, of which the collateral snapshot lookup is 99.9%.

```sql
SELECT id,pusd_balance_micro,reserved_pusd_for_buys_micro,captured_at,authority_tier
FROM collateral_ledger_snapshots
WHERE captured_at<=?
ORDER BY captured_at DESC,id DESC LIMIT 1
```

`EXPLAIN QUERY PLAN`: `SCAN collateral_ledger_snapshots` + `USE TEMP B-TREE FOR
ORDER BY`. The table carries **no index at all** on `captured_at` — `id` is the
only indexed column (the `INTEGER PRIMARY KEY`). 244,324 rows, ~626 bytes of
JSON payload (`ctf_token_balances_json` + `ctf_token_allowances_json` +
`reserved_tokens_for_sells_json`) per row, so the full-table read is roughly
150 MB to return one row.

### Why this hop gets an index, not a frontier

The three landed hops (`f4eef07d8`, `b3609685a`, `ccb93b1b5`) all resume a scan
of a **growing window of many candidate rows** by naming the survivors of a
prior run and walking only the extension, because the table's append key
(`sequence_no` / `id`) **is** the column the window is ordered and bounded by.
That mechanism requires the append order and the query's ordering key to be
the same monotone sequence, so a row below the frontier can never re-enter the
scanned set.

This hop's query returns **one row** — the single latest snapshot at or before
`as_of` — not a growing candidate set, so there is nothing to name. And its
ordering key (`captured_at`, a wall-clock value stamped by
`src/state/collateral_ledger.py:385` at insert time) is not the append key
(`id`). Checked directly against the live table:

```sql
SELECT COUNT(*) FROM (
  SELECT id, captured_at, LAG(captured_at) OVER (ORDER BY id) AS prev
  FROM collateral_ledger_snapshots
) WHERE captured_at < prev
```

**854 of 244,324 rows are out of order** (a lower-`id` row has a *later*
`captured_at` than the row inserted after it) — up to **600 seconds** apart on
one pair (`id=68852` at `...04:16:15`, `id=68853` at `...04:06:15`), and the
violations are not historical: the most recent one found is at `id=237580` of
a `MAX(id)=244335` table. Concurrent writers (the daemon's own 30 s collateral
refresh, `exchange_reconcile`, `wallet_balance_head`) each stamp
`datetime.now(timezone.utc)` independently and commit under lock contention, so
commit order and `captured_at` order are not the same sequence.

An `id > frontier` extension scan would be **unsound** here: a row with
`id <= frontier` can carry a `captured_at` that lands after the prior run's
`as_of` (exactly the 600 s case above), and the extension walk would never
visit it because it only looks above the frontier. This is the inverse of the
proof the three landed hops rely on ("a row can only leave the scanned set,
never re-enter it") — on this table a row genuinely can enter a later window
without its `id` changing. The table *is* append-only (only `INSERT` in
`src/state/collateral_ledger.py`; no `UPDATE`/`DELETE` on it anywhere in
`src/` or `scripts/` — the one `UPDATE collateral_ledger_snapshots` hit is
`tests/integration/test_w3_solve_seam_g3.py` fixture setup, not production
code), but append-only is necessary, not sufficient, for a frontier: the
ordering key must also be the append key, and here it is not.

**Fix**: `CREATE INDEX IF NOT EXISTS
idx_collateral_ledger_snapshots_captured_at ON
collateral_ledger_snapshots(captured_at, id)` in `_TRADE_CLASS_DDL`
(`src/state/db.py`), matching the existing `idx_decision_log_ts` /
`idx_unsettled_open` idempotent-bootstrap pattern. This is correct for every
row regardless of insertion order — the index does not encode an ordering
assumption the way a frontier would — and turns the same statement into a
`SEARCH ... USING INDEX idx_collateral_ledger_snapshots_captured_at`, no
`TEMP B-TREE`. Proved via `EXPLAIN QUERY PLAN` on a freshly-initialized schema
(`tests/test_db.py::test_latest_collateral_snapshot_query_uses_captured_at_index`)
and, on a 2,000-row fixture with a 5% injected out-of-order `captured_at` rate
(mirroring the live table's 600 s reversals), the indexed query's answer
matches a brute-force Python scan over every row
(`tests/test_db.py::test_latest_collateral_snapshot_query_is_correct_with_out_of_order_captured_at`).

### Index build cost — measured on a real-data copy, not asserted

R-V review flagged that `captured_at` sits after the table's three JSON blob
columns in row layout, the same risk shape as `96fb1a947` (`idx_decision_log_mode`
on `decision_log`), which measured a **238 s** build for a `(mode, timestamp)`
index specifically because `timestamp` follows `decision_log`'s 89-262 KB
`artifact_json` and building it walked every row's overflow-page chain —
`96fb1a947` deliberately indexed `mode` alone (a leading, non-overflowing
column) to avoid that cost.

Checked whether the same risk applies here. `collateral_ledger_snapshots`'
three JSON columns average 626 bytes combined and max out at **11,356 bytes**
per row on the live table; SQLite's per-page local-storage threshold at the
live `page_size=4096` is roughly 4,061 bytes, so **10,519 of 244,324 rows
(4.3%) do overflow** to external pages — a real but far smaller share than
`decision_log`, where the mean row is 89-262 KB and effectively every row
overflows.

Measured directly rather than assumed: copied the **entire live table**
(all columns, real data, including the 10,519 genuinely-overflowing rows) —
not a same-shape synthetic fixture — into a fresh on-disk SQLite file via
`ATTACH DATABASE 'file:state/zeus_trades.db?mode=ro' AS live` (read-only
attach; no write to the live file) and `CREATE TABLE ... AS SELECT * FROM
live.collateral_ledger_snapshots`, then built the exact same index on the
copy in a fresh connection (no page-cache warmth carried over from the copy
step):

| step | wall time |
| --- | --- |
| copy 244,394 rows (all columns) off the live DB | 5.632 s |
| `CREATE INDEX idx_collateral_ledger_snapshots_captured_at(captured_at, id)` | **0.166 s** |

Three orders of magnitude below the 238 s precedent and well under the
10 s threshold for keeping this in the idempotent `_TRADE_CLASS_DDL` bootstrap
rather than moving it to a fenced, operator-run migration script (the
`202607_drop_redundant_trade_indexes.py` pattern). This tracks the size
difference between the two tables: `decision_log` averages hundreds of KB per
row and is scanned in the millions; this table averages under a KB per row
across 244k rows, so even its overflowing 4.3% cannot approach
`decision_log`'s cost.

Caveat: the copy is a freshly-written, compact file, not the live 199 GB
file's actual fragmented physical layout, so this is a lower bound on true
cold/live build cost the same way the sibling hop-timing docs' live numbers
are lower bounds on cold-cache reads. Given the three-orders-of-magnitude
margin, this does not change the DDL-vs-migration-script decision.

Concurrent writers: `collateral_ledger_snapshots` is written by the daemon's
own 30 s collateral refresh plus `exchange_reconcile`/`wallet_balance_head`.
`scripts/deploy_live.py`'s `LIVE_TRADING_PREREQUISITE_LABELS` stops
`post-trade-capital` and `riskguard-live` (writers to this DB) ahead of a
live-trading restart, narrowing the window `init_schema_trade_only` runs in,
though the exact statement-level interleaving inside `_cmd_restart_locked` was
not traced end-to-end. Given the measured ~0.17 s build cost, any residual
write-lock contention during a restart is bounded by that, not by an unbounded
or minutes-scale hold — the concern the fencing precedent exists for.

### Not measured / not done in this change

The DDL was **not** applied to the live `state/zeus_trades.db` — this task's
DBs are read-only and `state/` is off limits. The index takes effect the next
time a writer connection bootstraps against that file (idempotent
`CREATE INDEX IF NOT EXISTS`, now measured at ~0.17 s on a real-data copy of
the table, not merely asserted). No live before/after wall-clock pair for the
*read* hop exists yet; the 9.053 s "before" figure above is measured live, the
"after" is a mechanism proof (index scan replaces table scan, byte-identical
output including a committed out-of-order regression test —
`tests/test_db.py::test_latest_collateral_snapshot_query_is_correct_with_out_of_order_captured_at`)
plus the build-cost measurement above, not a live re-measure of the read
query with the index actually in place.

### `tests/test_ddl_copies_normalized_identical.py` — run, pre-existing failures confirmed

This file's `TestCollateralDDLLockstep` asserts the world-copy and trade-copy
DDL for `collateral_ledger_snapshots`/`collateral_reservations` stay in
lockstep. Both fail identically on `4ec37eaf5` (HEAD before this change) and
after it: `world_info == []` because `init_schema` never defines a world-side
copy of either table at all. `architecture/db_table_ownership.yaml` confirms
this is by design (`db: trade` only, no world ghost). Pre-existing, unrelated
to this commit; not previously named in the commit's test report.

## The two `_realized_curve_with_deadline` hops — already fast, not touched

Both prior docs flagged these as "not measured" and budgeted up to 40 s worst
case (`deadline_seconds=20.0` each) against the shared production function
`src/riskguard/riskguard.py::_live_realized_capital_curve` (used outside the
evaluator too — RiskGuard's live tick calls it directly). Measured directly:

| call | wall time |
| --- | --- |
| `rg._day0_live_realized_capital_curve(trades, window_days=35.0, as_of=now)` | **0.032 s** |
| `rg._qkernel_live_realized_capital_curve(trades, window_days=35.0, as_of=now)` | **0.013 s** |

The underlying tables are small on the live DB: `execution_fact` 2,725 rows,
`venue_commands` 4,002, `position_current` 2,444, `venue_submission_envelopes`
10,889 — nowhere near the multi-million-row / multi-GB-blob shape of the other
hops. **No frontier or optimization applied here.** The 40 s worst-case budget
in the daemon's deadline comment was never observed in practice; giving an
already-sub-40ms function a resume mechanism would be pure overhead with
nothing to resume. If this hop is ever slow, it will be a different regime
(e.g. many more open positions) and should be re-measured then, not
pre-optimized against a bound that was never hit.

## Net effect on the 77s budget

Before this change, the two remaining un-frontiered hops were budgeted at
"40s (curves, worst case) + 21.6s (portfolio capital) = 61.6s". Measured
today: the curves cost ~0.05s combined (not 40s), and the portfolio-capital
hop's entire cost is the one un-indexed statement (9.06s), fixed by the index
above. Once the index is live, every hop in `evaluate()` has either a frontier
or an index; none of the previously-dominant costs remain un-addressed. The
77 s deadline itself is unchanged in this commit, per the boxed scope.
