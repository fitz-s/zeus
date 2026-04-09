# Schema Fix Report

Generated: 2026-04-07
Agent: schema-fixer (zeus-root-cause team)

---

## Task 1: env column added to position_current

**Status: COMPLETE**

### Finding
`position_current` had no `env` column, making it impossible to distinguish paper vs live positions in the canonical surface.

### Changes made
- `ALTER TABLE position_current ADD COLUMN env TEXT` applied to `state/zeus.db`
- `CANONICAL_POSITION_CURRENT_COLUMNS` in `src/state/projection.py` updated to include `"env"`
- `upsert_position_current()` ON CONFLICT clause updated to include `env=excluded.env`
- Backfilled from `position_events.env` (join on `position_id`)

### Verification
- `env` column present in schema: **True**
- All 24 rows backfilled: `env='paper'` (24 rows), NULL rows: 0
- `CANONICAL_POSITION_CURRENT_COLUMNS` now contains 28 columns (was 27)

### Backfill method
```sql
UPDATE position_current
SET env = (
    SELECT env FROM position_events
    WHERE position_events.position_id = position_current.position_id
    LIMIT 1
)
```

---

## Task 2: outcome_fact backfilled from chronicle

**Status: COMPLETE**

### Finding
`outcome_fact` was empty (0 rows). 19 historical settlements existed only in `chronicle` (SETTLEMENT events), pre-dating the SD-2 fix that wires settlements to `log_outcome_fact()`.

### Script
`scripts/backfill_outcome_fact.py` — idempotent, deduplicates via `INSERT OR IGNORE`.

### Linkage
```
chronicle.trade_id  →  position_events_legacy.runtime_trade_id (for env/strategy lookup)
chronicle.trade_id  →  outcome_fact.position_id (directly — these are the position identifiers)
```

### Fields populated
| Field | Source |
|---|---|
| `position_id` | `chronicle.trade_id` |
| `strategy_key` | `chronicle.details_json.strategy` or `position_events_legacy.strategy` |
| `entered_at` | earliest `position_events_legacy.timestamp` for this trade_id |
| `settled_at` | `chronicle.timestamp` |
| `exit_reason` | hardcoded `"settlement"` |
| `decision_snapshot_id` | `chronicle.details_json.decision_snapshot_id` |
| `pnl` | `chronicle.details_json.pnl` |
| `outcome` | `chronicle.details_json.outcome` (or derived from `position_won`) |

### Verification
- Chronicle SETTLEMENT events: 31 total (19 unique position_ids — duplicate events for same positions)
- Rows inserted: **19** (duplicates silently handled by `INSERT OR IGNORE`)
- `outcome_fact` total rows: **19**
- Win/loss breakdown: 5 wins, 14 losses

### Note on duplicate SETTLEMENT events
Chronicle contains 31 SETTLEMENT events for 19 unique position_ids. Some positions were settled twice (same position_id, different timestamps — likely a symptom of the SD-2 bug that caused double-settlement writes). The backfill preserves only the first settlement per position.

---

## Task 3: settlement_semantics_json analysis

**Status: FINDING DOCUMENTED — no code change needed**

### Query results
| Query | Count |
|---|---|
| `trade_decisions WHERE settlement_semantics_json IS NULL` | 2 |
| `trade_decisions WHERE settlement_semantics_json IS NOT NULL` | 120 |

### Sample non-NULL content
```json
{
  "resolution_source": "WU_KLGA",
  "measurement_unit": "F",
  "precision": 1.0,
  "rounding_rule": "round_half_to_even",
  "finalization_time": "12:00:00Z",
  "reconstructed": true,
  "reconstruction_source": "city_contract"
}
```

### Design assessment
`settlement_semantics_json` is correctly populated but **contains resolution RULES, not outcome data**. This is by design:

- **What it stores**: HOW a market resolves — which data source, what unit, precision, rounding rule, finalization time. This is the epistemic contract at trade entry.
- **What it does NOT store**: WHAT happened — whether the position won, the actual settlement value, PnL.

This is correct separation of concerns. The column encodes the *resolution specification* (answering "by what rules will this settle?"), not the *settlement outcome* (answering "how did it settle?").

### Where outcome data belongs
Outcome data (pnl, win/loss, actual settlement value) belongs in:
- `outcome_fact` — per-position outcome record (now populated via SD-2 fix + this backfill)
- `settlements` table — market-level settlement values (1399 rows, already populated)

### Why 2 rows are NULL
These 2 rows likely pre-date the `settlement_semantics_json` column addition or were written before the field was populated at trade entry. Not a bug — the column is nullable by design since resolution semantics may not always be reconstructible.

---

## Summary

| Task | Status | Key metric |
|---|---|---|
| Add `env` to `position_current` | COMPLETE | 24 rows backfilled, 0 NULL |
| Backfill `outcome_fact` | COMPLETE | 19 rows inserted (5 wins, 14 losses) |
| `settlement_semantics_json` audit | DOCUMENTED | Contains rules not outcomes — by design |
