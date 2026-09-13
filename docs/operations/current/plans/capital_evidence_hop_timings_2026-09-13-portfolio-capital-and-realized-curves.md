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
and, on a 244,324-row synthetic fixture at the live table's blob size with the
same 0.35% injected out-of-order rate, the indexed and unindexed query return
the byte-identical row.

### Not measured / not done in this change

The DDL was **not** applied to the live `state/zeus_trades.db` — this task's
DBs are read-only and `state/` is off limits. The index takes effect the next
time a writer connection bootstraps against that file (idempotent
`CREATE INDEX IF NOT EXISTS`, one-time cost proportional to the table's
244,324 rows, not its 199 GB total size). No live before/after wall-clock pair
for this hop exists yet; the 9.053 s "before" figure above is measured live,
the "after" is a mechanism proof (index scan replaces table scan, byte-
identical output) plus a synthetic-scale timing check, not a live re-measure.

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
