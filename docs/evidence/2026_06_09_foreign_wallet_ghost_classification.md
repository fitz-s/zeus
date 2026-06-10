# Plan: foreign-wallet ghost-order classification (2026-06-09)

## Problem (live, observed)
6 unresolved `exchange_ghost_order` findings (recorded 18:04–18:27Z) tripped the
risk-allocator kill switch (`reconcile_finding_threshold`, limit=0) → ALL Zeus
entries blocked (reduce_only). The ghost subjects are LIVE GTC BUY orders on
`0xeec29813…` ("Will claude-opus-4-6-thinking be the best AI model on June 13,
2026?") and `0xe7bdd1ba…` ("Will Anthropic have the best AI Agent at the end of
June 2026?") — NOT weather markets. Same maker address (operator's proxy
wallet), zero rows in `venue_commands`, zero rows in `executable_market_snapshots`
(7488 distinct weather condition_ids present). Conclusion: operator manual
orders on the shared wallet. Zeus's exclusive-wallet assumption is false.

## Structural decision (K=1)
Zeus-domain membership defines whether a venue order can be a lost Zeus side
effect. A resting (size_matched=0) open order whose market is outside Zeus's
domain (condition_id ∉ executable_market_snapshots AND market_id ∉
venue_commands) is FOREIGN WALLET ACTIVITY: record for audit, resolve
immediately, never arm the kill switch on it.

Fail-closed boundaries kept strict:
- Foreign order WITH matched size > 0 → strict ghost (money moved; tripwire).
- Market in Zeus domain → strict ghost (the original disease).
- Snapshot table missing/empty → cannot prove foreign → strict ghost.
- Foreign FILLS still surface via `unrecorded_trade` strict path.

## Changes
- `src/execution/exchange_reconcile.py`: `_is_market_in_zeus_domain`,
  `_record_foreign_wallet_ghost` (audit row, immediate resolution, WARN, dedup
  on existing resolved row), classification branch in `run_reconcile_sweep`,
  `_resolve_foreign_wallet_ghost_findings` migration pass for the 6 existing
  unresolved rows.
- `tests/test_reconcile_foreign_wallet_orders.py`: relationship tests pinning
  the reconcile→governor boundary.

## Capability note
`on_chain_mutation` gate: this module performs NO new venue/chain side effects;
edits only reclassify read-only reconcile findings. Reversible (DB rows carry
resolution provenance).
