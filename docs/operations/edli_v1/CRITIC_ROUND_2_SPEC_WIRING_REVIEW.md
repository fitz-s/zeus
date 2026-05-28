# EDLI v1 Critic Round 2 - Spec And Wiring Review

Date: 2026-05-24
Critic: Codex subagent `019e5a3b-f3c0-7472-a48b-0298f214226e`
Constraint: critic was instructed to read EDLI operation docs/spec after Round 1 and verify contract wiring.

## Findings Returned

| Severity | Finding | Resolution |
| --- | --- | --- |
| P0 | Day0 tiny live cap was in-memory only and `tiny_live_max_notional_usd` was unused. | Fixed: added world table `edli_live_cap_usage`, durable cap ledger, and reactor enforcement for per-day order count and notional usage across scheduler ticks. |
| P1 | Forecast/Day0 catch-up scanners could starve newer rows behind old duplicate windows. | Fixed for implemented scanners by prioritizing newest eligible rows within each bounded catch-up window. |
| P1 | Market-channel online service wrote quote events but did not populate `execution_feasibility_evidence`. | Fixed: quote/book/BBA handling, REST seed, and reconnect seed now insert evidence-only feasibility rows with fill fields null. |
| P2 | EDLI executable snapshot gate/submit are not bound to the event-specific city/date/metric/snapshot. | Accepted with clarification: EDLI events wake the existing discovery mode; event/candidate-specific executable authority remains inside the existing cycle/final-intent/executor path. |

## Verification Added

- `src/state/schema/edli_live_cap_usage_schema.py`
- `tests/events/test_reactor.py::test_live_day0_tiny_cap_persists_across_reactor_instances`
- `tests/events/test_reactor.py::test_live_day0_tiny_notional_cap_persists_across_reactor_instances`
- `tests/events/test_market_channel_ingestor.py::test_market_channel_quote_writes_feasibility_evidence_only`
- Registry updates in `architecture/db_table_ownership.yaml`, `architecture/money_path_objects.yaml`, `architecture/money_path_ci.yaml`, and `architecture/source_rationale.yaml`.

## Status

Round 2 blocking findings are addressed. Remaining P2 is a documented topology choice: EDLI is an event-sourced wake-up/reactor layer over the existing Zeus cycle authority, not a separate event-specific order builder.
