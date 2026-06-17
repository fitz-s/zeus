# ARCH_PLAN_EVIDENCE — #122: pre-submit collateral db-lock must retry, not reject the order

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: GOAL #83 (continuous settlement-graded fills) + #122 root-cause
  (docs/evidence/qkernel_rebuild/dblock_122_rootcause_2026-06-16.md). RULE 1: a decided
  order lost to a transient lock is OUR defect, not absent edge.
- Capability touched: `live_venue_submit` / `on_chain_mutation` (T0, reversibility ON_CHAIN)
  — `src/execution/executor.py::_refresh_entry_collateral_snapshot_for_submit`.

## Defect (live, settlement-graded evidence)
The pre-submit collateral refresh (`executor.py:518`) wrapped EVERY exception — including a
TRANSIENT `sqlite3.OperationalError: database is locked` — into `CollateralInsufficient`, and
the caller (`executor.py:3458-3469`) REJECTED the decided order with
`pre_submit_collateral_refresh_failed`. Live log shows 9 such rejects: a decided, armed harvest
cross (the alpha) discarded on a transient `zeus_trades.db` write-lock. Root cause of the lock:
zeus_trades.db has no in-process trade-write mutex (unlike zeus-world.db's `_GuardedWorldMutex`),
so the 20s snapshot-capture writer (short busy_timeout) races the WAL write lock with the submit
path (dblock_122_rootcause_2026-06-16.md).

## Change (minimal, no new cap/gate; no behavior change except transient-lock survival)
`_refresh_entry_collateral_snapshot_for_submit`: a `sqlite3.OperationalError` whose message
contains "lock" is now RETRIED a bounded number of times (5 × 0.4s ≈ ≤2s worst case; the lock
clears far faster) before surfacing. A GENUINE `CollateralInsufficient`, a non-lock error, or a
lock persisting past every retry still raises exactly as before — the order is then re-decided
next cycle via FSR re-emission (never a silent loss, never a fabricated insufficiency). This does
NOT loosen any economic gate: insufficient collateral still rejects; it only stops a TRANSIENT
infra lock from being misclassified as an economic refusal.

## Reversibility / safety
ON_CHAIN capability, but this change makes the path STRICTER-correct, not more permissive: it
adds a bounded wait on a transient lock before the same `CollateralInsufficient` it always raised.
It cannot cause an over-spend (the downstream `_assert_collateral_allows_buy` + envelope checks
are unchanged) and cannot submit on insufficient collateral. Rollback: `git revert`; restart.

## Test / verification
- Unit: `_refresh_entry_collateral_snapshot_for_submit` retries on `OperationalError("database is
  locked")` then succeeds (returns the capability component); raises `CollateralInsufficient`
  immediately on a genuine insufficiency and on a non-lock error; raises after retries exhaust.
- Regression: `tests/money_path/` introduces ZERO new failures (the 3 `test_finding_b_free_cash_bound`
  reds are pre-existing — bankroll-provider harness).
- Live signal: `pre_submit_collateral_refresh_failed: ... database is locked` rejects drop to ~0;
  the armed escalation cross reaches `VenueSubmitAcknowledged` instead of being rejected.

## Companion (separate, not in this change)
Rank-2 cure for #122 (a process-global trade-write mutex mirroring `_GuardedWorldMutex`) +
Rank-1 (`ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS=30000`) remain follow-ups; this change makes the
submit path resilient regardless.
