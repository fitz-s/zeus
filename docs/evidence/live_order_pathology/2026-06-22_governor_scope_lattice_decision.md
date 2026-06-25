# Governor scope-lattice decision — global-freeze → scoped isolation (2026-06-22)

## Problem (real forward chain)
Engine in GLOBAL `reduce_only` (all entries blocked across 1000+ markets) from a
SINGLE scoped unknown: governor `unknown_side_effect_count=1`,
`unknown_side_effect_markets=['2615258']` (Hong Kong, token
`53977777150130000657872223857484214695512559999068546445524492969922535787487`).
That command (`7e07c586`) has a real venue_order_id + 18 venue_order_facts + 42
venue_trade_facts = **634 YES shares confirmed on-chain** (MATCHED/MINED/CONFIRMED,
@0.001 ≈ $0.63) but position_current = `chain_absent_confirmed_position_unattributed`,
no settlement record — i.e. a market-scoped attribution gap, almost certainly
shared-wallet commingling (operator co-trades the same proxy wallet; foreign
activity is EXPECTED).

Governor mechanics (verified): `governor.py:236` trips GLOBAL reduce_only on
`unknown_side_effect_count>0`. `count_unknown_side_effects` (`:743`) counts
`venue_commands` in REVIEW_REQUIRED carrying submit-side-effect risk
(`_review_required_carries_submit_side_effect_risk` `:718` → True when venue_order_id
or order/trade facts present). Per-market isolation ALREADY exists (`:186`,
`unknown_side_effect_same_market`). So a single scoped sub-dollar anomaly freezes
the entire book — the "observe but never act" regression on 999+ healthy markets.

## Decision (frontier consult REQ-20260621-211850, Pro Extended, HIGH confidence)
**Isolate the affected market/token by default; reserve GLOBAL freeze for evidence of
SYSTEMIC failure** (collateral, chain/indexer, venue adapter, order-ID namespace,
token-map, settlement-state-machine, cross-market pattern, or low scope-confidence).
Replace `count(unknown_side_effects)>0` with `count(SYSTEMIC_unknown_side_effects)>0`
globally PLUS per-market reduce_only for scoped unknowns.

**Critical safety caveat (the fail-closed rule):** if the code cannot scope an unknown
to a token/market with HIGH confidence, it MUST fail closed globally until that
scoping exists. Scope lattice: GLOBAL > venue/collateral > market_group > token_id >
order_intent — apply the narrowest scope containing the evidence.

Global gating predicate (target): global_reduce_only = chain_data_unhealthy OR
reconciliation_epoch_stale OR reorg_unresolved OR collateral_residual_unexplained OR
collateral_not_conservative OR token_market_mapping_failed OR order_id_namespace_corrupt
OR engine_order/tx_cannot_map_to_one_intent OR duplicate/missing_facts_in_common_path
OR (any unknown with scope_confidence < MIN) OR (any unknown scoped to GLOBAL/WALLET/
COLLATERAL/VENUE_ADAPTER/INDEXER/ORDER_ID_NAMESPACE/TOKEN_MAP/SETTLEMENT_STATE_MACHINE)
OR (distinct_independent_market_unknown_count >= SYSTEMIC_MARKET_COUNT_LIMIT) OR
(unresolved_unknown_notional >= GLOBAL_UNKNOWN_NOTIONAL_LIMIT) OR risk_scope_store_write_failed.

Per-market quarantine for scoped MARKET/TOKEN unknowns; conservatively reserve the
quarantined notional so other markets don't trade on ambiguous collateral.

Shared-wallet attribution = omnibus sub-ledger: engine trades off
`engine_attributed_position`, NOT raw wallet balance; operator/external activity
excluded explicitly (absence from engine command ledger / signer-session / tx-hash).

## This instance → ISOLATE (not global)
Scoped to ONE market (2615258), reconcile_finding_count=0, ws_gap=False, risk GREEN,
no drawdown, $0.63 notional → market-specific. Per the decision: isolate 2615258,
keep trading the other 999+. (If a residual-grouping report ever shows the SAME
reconciliation failure across ≥2 independent markets, that flips to systemic → global.)

Full consult: /tmp/cgc_answer_REQ-20260621-211850-d63b45.txt
