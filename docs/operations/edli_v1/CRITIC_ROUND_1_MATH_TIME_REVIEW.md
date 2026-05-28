# EDLI v1 Critic Round 1 - Math And Time Semantics

Date: 2026-05-24
Critic: Codex subagent `019e5a3a-8e0c-7c03-aeb7-9e098bcc17ad`
Constraint: critic was instructed not to read the saved spec, plans, or EDLI operation docs.

## Findings Returned

| Severity | Finding | Resolution |
| --- | --- | --- |
| P1 | `received_at` was not part of the causal availability gate. | Fixed: `assert_available_for_decision()` and `EventStore.fetch_pending()` now require `received_at <= decision_time`. |
| P1 | Forecast/Day0 catch-up scanners could starve newer rows behind old duplicate windows. | Fixed for implemented scanners by prioritizing newest eligible rows within the bounded catch-up window. |
| P1 | SELL executable economics added taker fee instead of subtracting fee from proceeds. | Fixed: BUY returns ask plus fee; SELL returns bid minus fee. |
| P1 | Claimed events could remain stuck forever after a crash. | Fixed: `EventStore` now supports stale `processing` lease reclaim. |
| P2 | VWAP/book-walk average was required to be tick-aligned, rejecting valid multi-level fills. | Fixed: tick validation is per executable level; VWAP may be non-tick-aligned. |
| P2 | Executable snapshot gate is coarse and not event-specific. | Accepted with clarification: EDLI event is a wake-up signal; existing cycle path remains event/candidate executable authority. |

## Verification Added

- `tests/events/test_opportunity_event.py::test_received_at_future_rejected`
- `tests/events/test_event_store_idempotency.py::test_pending_fetch_excludes_future_received_at`
- `tests/events/test_event_store_idempotency.py::test_stale_processing_claim_is_reclaimed_after_lease`
- `tests/events/test_day0_extreme_updated_trigger.py::test_day0_scanner_limit_prioritizes_newest_rows_not_old_duplicates`
- `tests/strategy/live_inference/test_executable_cost.py::test_multilevel_vwap_need_not_be_tick_aligned_when_levels_are_valid`

## Status

Round 1 findings are addressed except the event-specific executable snapshot concern, which remains a documented coarse wake-up boundary and is rechecked by the existing cycle path.
