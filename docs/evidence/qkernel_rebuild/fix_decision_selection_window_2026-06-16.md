# Fix: decision-SELECTION price window separated from EXECUTION (30s JIT) authority

- Created: 2026-06-16
- Last reused or audited: 2026-06-16
- Authority basis: operator design law 2026-05-30 (event_reactor_adapter.py
  `_latest_snapshot_rows_for_event_family` docstring: "freshness 针对价格不针对市场; 市场捕捉了
  不会突然消失"); the verified submit-time JIT witness re-validation
  (`TAKER_BUY_TOUCH_EXCEEDS_RESERVATION`, event_reactor_adapter.py ~4355). RULE 1: zero
  continuous fills is OUR defect — root-caused to an over-conservative SELECTION gate, not
  absent alpha, and NOT loosening any money-safety gate.

## Observed binding constraint
Live 2026-06-15/16: families decide only a few times/day. `refresh_pending_family_snapshots`
oscillates `families_needing_refresh` 1→116 of 188; reactor cycles log `processed=0`. Root: the
decision path rejected the elected snapshot row `EXECUTABLE_SNAPSHOT_STALE` whenever it was older
than the 30s `_K1_DEFAULT_PRESUBMIT_FRESHNESS_SECONDS` window, but the warm-capture per-family
cadence is ~5.4min — so a family is decidable only ~9% of wall-clock and most transient-requeue
forever (the Qingdao 2026-06-13 q=0.679-vs-ask-0.30 6-min-old row "blocked all day" class).

## Why the 30s gate was misapplied (design-law contradiction)
The operator design law states PRICE-freshness is for the actually-traded bin and is enforced at
SUBMISSION. The submit path re-fetches a FRESH JIT /book and rejects the taker if
`fresh_best_ask > reservation` (q_lcb-derived) — verified at event_reactor_adapter.py ~4355.
Therefore execution can NEVER cross above the conservative bound regardless of decision-time book
age. Applying the 30s EXECUTION window at DECISION-selection both contradicted the law and
throttled throughput to ~0.

## Fix (selection ≠ execution)
`_snapshot_price_stale_reason` (decision-selection gate, called only at the decision seam ~2275/2376,
never in submit) now grades the elected row against a wide SELECTION window
`_DECISION_SELECTION_PRICE_WINDOW_SECONDS = 600s` measured from `captured_at` (spans one warm-capture
interval + jitter), falling back to the stored 30s execution deadline only when `captured_at` is
absent (fail-safe, never looser than legacy). The 30s JIT/execution authority is UNCHANGED.

## Safety
A TAKER fill occurs ONLY when, at submit, `fresh_best_ask ≤ reservation` on the FRESH book — so a
price-drifted selection cannot fill above q_lcb; it is clean-rejected at submit
(`TAKER_BUY_TOUCH_EXCEEDS_RESERVATION`). The change cannot produce a bad fill; it only lets more
captured families reach submit, where the real price authority runs. This is NOT a 1-order hack and
loosens NO money-safety gate — it removes a selection gate redundant with (and contradicting) the
submit-time authority. Interim 600s pending #64 staleness fitting; correctness is bounded by the
submit authority, not the value.

## Test / rollback
tests/test_decision_selection_window.py (6-min row selectable; 20-min stale; missing captured_at →
fail-safe to stored deadline). Rollback: `git revert`; restart daemon. Config-free; no migration.
