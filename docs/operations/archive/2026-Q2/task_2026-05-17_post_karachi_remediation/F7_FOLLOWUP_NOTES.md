# F7 Follow-up Completion Notes

**Date**: 2026-05-17
**Branch**: fix/f7-followup-position-decision-id-2026-05-17
**Authority**: WAVE_2_PLAN.md §WAVE-B #27

## Problem

`c30f28a5-d4e:exit` execution_fact row had `decision_id=NULL`. Root cause: `Position`
dataclass had no `decision_id` field, so `log_exit_lifecycle_event` could not forward it.

## Files Touched

| File | Change |
|---|---|
| `src/state/portfolio.py` | Added `decision_id: Optional[str] = None` field to `Position` dataclass (near audit-trail section, before JSON snapshots) |
| `src/engine/cycle_runtime.py` | `materialize_position()`: thread `EdgeDecision.decision_id` → `Position(decision_id=...)` ctor |
| `src/state/db.py` | `log_exit_lifecycle_event()`: added `decision_id=getattr(pos, "decision_id", None) or None` kwarg to inner `log_execution_fact()` call |

## Callers Not Updated

- `portfolio.py:1229` (`**filtered` from JSON) — automatic; Position field default=None, no breakage
- `portfolio.py:1353` (`**payload` from DB row) — automatic; ditto
- `portfolio.py:1397` (`_chain_only_quarantine_position_from_row`) — quarantine positions have no decision_id source; left at default None
- `riskguard.py:114` — loader row from `query_portfolio_loader_view` does not yet carry `decision_id`; riskguard-loaded positions will have `decision_id=None` until the DB load path is extended (separate task)
- `chain_reconciliation.py:788` — excluded per task brief

## DB Load Path (Deferred)

`query_portfolio_loader_view` does not yet JOIN `execution_fact` for `decision_id`. Positions loaded from DB will have `decision_id=None` on restart. Future positions created via `materialize_position` will carry decision_id correctly in memory and forward it on exit. The DB load-path enrichment is a follow-on (low urgency: the critical Karachi gap is the exit forwarder, not reload correctness).

## Antibody Outcome

`tests/state/test_position_decision_id_propagation.py` — **2 passed** post-fix.
`tests/state/test_lineage_join_keys.py` — **3 passed** (no regression).

## Commits

- `f66450678a` — antibody test (pre-fix, confirmed failing)
- `3c341414a7` — source edits (all 3 files)
