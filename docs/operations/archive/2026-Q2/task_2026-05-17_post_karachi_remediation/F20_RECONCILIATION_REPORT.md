# F20: position_lots Reconciliation Report

**Date**: 2026-05-17  
**Investigator**: executor (cc/claude-sonnet-4-6)  
**DB**: `state/zeus_trades.db` (live, read-only)

---

## Executive Summary

The live probe counts (17 orphan lots / 76 no-lot positions / 13 shares mismatches)
were generated using a broken join key: `CAST(pl.position_id AS TEXT) = pc.position_id`.
This is wrong because `position_lots.position_id` stores the **INTEGER**
`trade_decisions.trade_id`, not the UUID string `position_current.position_id`.
The correct three-table bridge is:

```
position_lots.position_id (INT) = trade_decisions.trade_id
trade_decisions.runtime_trade_id = position_current.position_id (UUID)
```

After correcting for this:

| Probe bucket | Brief count | Corrected count | Root cause |
|---|---|---|---|
| Orphan lots | 17 | **0 true orphans** | Wrong join key; all resolve via bridge |
| No-lot positions (non-voided) | 13* | **2** | Singapore: pre-rollout; Karachi: no `trade_decisions` row |
| Shares mismatch | 13* | **5** | Naive `SUM(shares)` ignores append-only lifecycle state |

\* The 13-count for mismatches was itself produced by the wrong join; it returns the same
no-lot positions (NULL lot_sum → delta = pc.shares).

---

## Bucket 1 — Orphan Lots: 0 True Orphans

**Evidence (SQL)**:
```
SELECT COUNT(*) FROM position_lots pl
JOIN trade_decisions td ON td.trade_id = pl.position_id
JOIN position_current pc ON pc.position_id = td.runtime_trade_id;
-- Result: 17   (all 17 lots resolve through the bridge)

SELECT COUNT(*) FROM position_lots pl
WHERE NOT EXISTS (SELECT 1 FROM trade_decisions td WHERE td.trade_id = pl.position_id);
-- Result: 0   (no lots lack a trade_decisions parent)
```

The 17 lots (integer position_ids 7, 11, 13, 39, 42, 53, 55, 56, 57, 58, 59) all have
valid `trade_decisions` rows, and those `runtime_trade_id` values all exist in
`position_current` (active, pending_exit, economically_closed phases).

**Root cause**: The original probe used `CAST(pl.position_id AS TEXT) = pc.position_id`,
which compares integers like `"7"` against UUIDs like `"7211cc19-e02"` — always false.

**Repair**: NONE — no data is corrupt.

**Risk classification**: N/A

---

## Bucket 2 — No-Lot Positions: 2 Real Cases

### 2a. Singapore (8f02dc01-b6b) — Pre-Rollout Fill

**Phase**: `economically_closed`  
**Shares**: 11.62  
**Evidence**:
- `ENTRY_ORDER_FILLED` at `2026-05-17T07:34:42` (from `position_events`)
- First lot ever written to `position_lots`: `2026-05-17T09:24:57` (from `MIN(captured_at)`)
- **No row in `trade_decisions`** with `runtime_trade_id = '8f02dc01-b6b'`

Singapore's entry fill completed ~1h55m before the lots writer was deployed. There is
no `trade_decisions` row to anchor a lot. The position is already `economically_closed`
(exit filled at `2026-05-17T20:38:26`).

**Repair recommendation**: SAFE_AUTO — document as pre-rollout legacy; position is
closed, no capital at risk. No lot backfill needed. Acceptable to leave as-is with
a `position_events` annotation.

### 2b. Karachi (c30f28a5-d4e) — Missing trade_decisions Row

**Phase**: `day0_window`  
**Shares**: 1.5873 (active capital)  
**Evidence**:
- `SELECT * FROM trade_decisions WHERE runtime_trade_id = 'c30f28a5-d4e'` → **0 rows**
- Full lifecycle in `position_events`: POSITION_OPEN_INTENT (2026-05-16T00:32)
  → ENTRY_ORDER_POSTED → ENTRY_ORDER_FILLED (2026-05-16T06:40) → DAY0_WINDOW_ENTERED
  (2026-05-16T19:01)
- Position is real on-chain (entry was confirmed via `exchange_reconcile`)
- No `trade_decisions` materialization ever wrote `runtime_trade_id` for this position

The `trade_decisions.runtime_trade_id` field is set during entry materialization.
Something in the materialization path skipped or failed for this position silently.

**KARACHI SAFETY CONSTRAINT — OPERATOR ACTION REQUIRED**

Karachi (c30f28a5-d4e) holds live capital in `day0_window`. Any repair touching it
must be reviewed by the operator before execution. Do NOT apply automated repair.
The recommended investigation is:
1. Check `venue_commands` for the Karachi entry order to confirm whether
   `position_id` was set correctly at command creation time.
2. Determine why `trade_decisions.runtime_trade_id` was never set.

**Repair recommendation**: NEEDS_OPERATOR — surface immediately; no automated path.

---

## Bucket 3 — Shares Mismatch: 5 Definitional Mismatches (No Data Corruption)

**Evidence (SQL)**:
```
SELECT pc.position_id, pc.shares, SUM(CAST(pl.shares AS REAL)) as lot_sum
FROM position_current pc
JOIN trade_decisions td ON td.runtime_trade_id = pc.position_id
JOIN position_lots pl ON pl.position_id = td.trade_id
GROUP BY pc.position_id
HAVING ABS(pc.shares - SUM(CAST(pl.shares AS REAL))) > 0.0001;
```
Results: 5 positions, all with `lot_sum = 2 × pc.shares`, delta negative.

Example (Miami / 43822a1f-e9e):
```
lot_id=12  OPTIMISTIC_EXPOSURE  shares=35.6  captured_at=2026-05-17T21:02:31
lot_id=13  CONFIRMED_EXPOSURE   shares=35.6  captured_at=2026-05-17T21:02:37
```

`position_lots` is an append-only state-transition log. For each fill, the writer
records two rows: `OPTIMISTIC_EXPOSURE` (from WS MATCHED event), then `CONFIRMED_EXPOSURE`
(from WS CONFIRMED event). Both carry the same `shares` value. Naive `SUM(shares)` without
a state or sequence filter double-counts every confirmed fill.

**Correct aggregation** (as implemented in `load_position_lots` in
`src/risk_allocator/governor.py`):
```sql
SELECT lot.shares FROM position_lots lot
JOIN (
  SELECT position_id, MAX(local_sequence) AS max_sequence
  FROM position_lots GROUP BY position_id
) latest ON latest.position_id = lot.position_id
       AND latest.max_sequence = lot.local_sequence
WHERE lot.state IN ('OPTIMISTIC_EXPOSURE', 'CONFIRMED_EXPOSURE', 'EXIT_PENDING')
```

This correctly reads the latest state per `position_id` (not sum-all).

**Root cause**: The brief's probe used raw `SUM(pl.shares)` without the
latest-sequence dedup that the production reader applies.

**Repair recommendation**: NONE — data is correct. The invariant antibody must
encode the state-aware aggregation, not the naive sum.

---

## Karachi Safety Check

**c30f28a5-d4e appears in Bucket 2b** (no `trade_decisions` row, live capital in
`day0_window`). Per task constraint: no repair proposed. Surfaced to operator above.
The position itself is not at risk of misreporting in the capital allocator because
`load_position_lots` reads via `trade_decisions` bridge — since no TD row exists,
it simply has zero lots in the allocator's view (potential undercount of exposure).

---

## Repair Migration

**Not written.** All three buckets either have no corrupt data (Buckets 1 and 3)
or require operator decision before any action (Karachi). A migration script would
touch nothing real. Singapore is closed. The correct outputs are:
- Bucket 1: fix the probe/detection query
- Bucket 2a (Singapore): document as legacy
- Bucket 2b (Karachi): operator investigation
- Bucket 3: fix the invariant's aggregation rule
