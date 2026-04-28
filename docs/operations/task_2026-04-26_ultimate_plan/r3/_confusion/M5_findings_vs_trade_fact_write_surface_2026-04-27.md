# Confusion checkpoint — M5 findings vs trade-fact write surface

Date: 2026-04-27
Phase: M5 — Exchange reconciliation sweep

## Ambiguity

The M5 slice says the sweep creates `exchange_reconcile_findings` and never inserts commands for exchange-only state. Its invariant text says the findings table is the only write surface, while acceptance also requires:

- `test_trade_at_exchange_missing_locally_emits_trade_fact_if_order_linkable_else_finding`

That acceptance needs one additional durable write path when exchange truth reports a trade for an already-known local command.

## Resolution

For M5 implementation:

- Exchange-only open orders become `exchange_ghost_order` findings, never command rows.
- Exchange-only trades without a local command become `unrecorded_trade` findings, never command rows or trade facts.
- Linkable missing trades for an existing local command may append `venue_trade_facts` and legal fill command events, because the command foreign key already exists and the trade fact is venue truth, not a new command authority plane.
- Local RESTING/open orders absent from exchange open-order enumeration produce context-specific findings (`local_orphan_order`, `heartbeat_suspected_cancel`, `cutover_wipe`) without silently canceling or closing the command.

## Guardrail

Absence must be based on a successful venue enumeration. Stale/error/unauthorized venue reads are not proof of absence. M5 tests use fakes only and do not authorize live venue side effects or production DB mutation.
