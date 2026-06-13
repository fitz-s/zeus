# Plan: fill-bridge retry-storm pair (schema drift + orphan re-selection)

Date: 2026-06-13 (UTC). Incident: live heartbeat diagnosis 2026-06-12 23:37Z.

## Observed

1. `fill-bridge: could not update failure count ... NOT NULL constraint failed:
   edli_fill_bridge_dispositions.disposition` + `EDLI durable fill-bridge: failed to
   bridge ... (attempt 1/10 ...)` repeating every scan, attempt counter frozen at 1.
2. `EDLI rest-filled orphan bridge failed (non-fatal): user-channel event cannot
   append after terminal Reconciled projection` repeating every minute.
3. 423 `database is locked` errors in 7 minutes since boot 23:32Z; snapshot capture
   degraded (`fresh_executable_city_count: 0`, coverage flapping FULL/PARTIAL/NONE).
   Both loops above are write-amplifiers feeding the contention.

## Roots (design failures, not instances)

A. **`CREATE TABLE IF NOT EXISTS` cannot relax constraints.** The schema owner
   (`edli_fill_bridge_dispositions_schema.py`) was updated 2026-06-12 to a nullable
   `disposition` (accumulating rows), but the live table pre-existed with
   `disposition TEXT NOT NULL`. `ensure_table` only adds columns; constraint drift
   is invisible. Quarantine (task #53) therefore unreachable: the accumulating
   insert fails, `_increment_failure_count` returns 1 forever, threshold 10 never
   reached → infinite retry.

B. **Recovery scan re-selects rows its own ledger guard will reject.** The orphan
   candidate query does not exclude aggregates whose projection is terminal
   RECONCILED, while `_require_user_channel_submit_binding` raises exactly on that
   predicate. Also one raising row aborts the whole batch (other recoverable
   orphans starve behind the poison pill).

## Category kills

A. `ensure_table` gains constraint-drift detection: `PRAGMA table_info` notnull flag
   on `disposition` → 4-step rebuild (CREATE new, INSERT-copy, DROP old, RENAME).
   Idempotent, runs at every daemon boot via init_schema. Antibody test creates the
   legacy NOT NULL table and proves migration + quarantine reachability.

B. Candidate query mirrors the ledger guard (NOT EXISTS on terminal-RECONCILED
   projection) so the rejected class is unselectable; per-row try/except
   (poison-pill immunity, same shape as task #13) so a raising row never aborts the
   batch. Antibody tests pin both.

## Surfaces

- src/state/schema/edli_fill_bridge_dispositions_schema.py (rebuild migration)
- src/events/edli_trade_fact_bridge.py (query exclusion + per-row isolation)
- tests/test_fill_bridge_dispositions_migration.py (new)
- tests/test_edli_trade_fact_bridge.py (extend if exists; else in migration test file)
