# Local Economic Ledger Census — chain-derivable persisted surfaces (2026-07-12)

Read-only census. Methodology mirror of `docs/rebuild/quarantine_excision_2026-07-11.md`.
Scope: every LOCALLY-PERSISTED column that is (a) a copy of, or (b) locally computed
from, a fact the Polygon chain / Polymarket already knows authoritatively.

**Chain-knowable test:** CTF ERC-1155 balances; fills (price/size/tx_hash/order linkage);
USDC/pUSD balance; condition resolution payout vector; redemption payouts. A stored
column derivable from those + Zeus's own submitted-order identity = CHAIN-DERIVABLE.

**Classification key:** CHAIN-DERIVABLE = copy/derivation of chain fact. LOCAL-TRUTH =
Zeus's own decision provenance, beliefs (q/p), intent, action timestamps. MIXED = both
classes coexist in the table (per-column split given).

**K1 DB split (src/state/domains.py):** TRADE=`state/zeus_trades.db`,
WORLD=`state/zeus-world.db`, FORECASTS=`state/zeus-forecasts.db`.

All writer file:lines below verified by opening each site + walking back to the enclosing
`def`; column lists quoted from the actual INSERT/UPDATE statement. Schemas via `.schema`,
row counts via read-only `COUNT(*)`, routing from `domains.py`.

---

## TRADE DB — state/zeus_trades.db (authoritative money-path)

### position_current — 1002 rows — MIXED — **top blast radius**

Central position ledger. The INSERT/upsert is a single funnel; **economics columns also
receive direct UPDATE writes from ~11 recovery/reconcile sites** (chain_* zeroing, exit_price
COALESCE) — the funnel is NOT the sole writer of chain_*/exit_price.

| column(s) | class | notes |
|---|---|---|
| shares, cost_basis_usd, entry_price, size_usd | CHAIN-DERIVABLE | position economics from fills (venue_trade_facts → lots) |
| chain_shares, chain_avg_price, chain_cost_basis_usd | CHAIN-DERIVABLE | **explicit chain CTF-balance mirror columns** |
| realized_pnl_usd | CHAIN-DERIVABLE | `close_economics.py:45 compute_realized_pnl_usd` = `round(shares*exit_price − cost_basis_usd, 2)` (:67) |
| exit_price | CHAIN-DERIVABLE | exit fill price (= proceeds/share) |
| settlement_price | CHAIN-DERIVABLE | condition resolution payout (0/1); written ONLY via funnel |
| last_monitor_market_price, last_monitor_best_bid, last_monitor_best_ask, last_monitor_market_vig | CHAIN-DERIVABLE | venue/chain-observable quote; written ONLY via funnel |
| settled_at, chain_seen_at, chain_absence_at | MIXED-timestamp | chain-observation timestamps |
| position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label, direction, unit, token_id, no_token_id, condition_id, order_id, order_status, chain_state | LOCAL-TRUTH | identity + lifecycle |
| p_posterior, last_monitor_prob, last_monitor_edge, entry_ci_width | LOCAL-TRUTH | Zeus beliefs |
| decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode, exit_reason, updated_at, temperature_metric, fill_authority, recovery_authority, exit_retry_count, next_exit_retry_at | LOCAL-TRUTH | provenance / intent / action timestamps |

**Canonical writer (INSERT ... ON CONFLICT DO UPDATE, all cols):**
`src/state/projection.py:658` `upsert_position_current` (def at :562) — SINGLE FUNNEL for
INSERT. Called from `ledger.py:646`, `cycle_runtime.py:3157`, `exchange_reconcile.py:5016`/
`:5072`, `command_recovery.py:4153`/`:4269`/`:5552`/`:6183`/`:6279`, `edli_position_bridge.py:1660`.
chain_* set on the Position pre-funnel by `chain_reconciliation.py:1476-1506`/`:1612-1614`/
`:1822-1855`, `chain_mirror_reconciler.py:937`.

**Direct UPDATE writers of economics columns (bypass funnel):**
| site | fn | econ cols set |
|---|---|---|
| position_duplicate_consolidator.py:186 | _void_row | shares=0, cost_basis_usd=0 |
| position_duplicate_consolidator.py:369 | _merge_equivalent_rows | shares, cost_basis_usd, size_usd, chain_shares, entry_price |
| command_recovery.py:2461 | _append_exit_order_fill_projection | exit_price (COALESCE) |
| command_recovery.py:6215 | _append_exit_filled_projection | exit_price (COALESCE) |
| command_recovery.py:8282 | repair_confirmed_phantom_voids | chain_shares=0, chain_avg_price=0, chain_cost_basis_usd=0 |
| command_recovery.py:8387 | repair_confirmed_phantom_voids | chain_shares/avg/cost (CASE-preserve) |
| command_recovery.py:8618/8683 | repair_confirmed_chain_absence_positive_projections | chain_shares/avg/cost (CASE / zero) |
| exit_lifecycle.py:4680 | _close_pending_exit_from_trade_fact | exit_price(COALESCE), chain_shares=0, chain_avg_price=0, chain_cost_basis_usd=0 |
| exchange_reconcile.py:1418 | _restore_position_to_pending_exit_for_recovered_sell | shares, chain_shares, cost_basis_usd, realized_pnl_usd (+=), exit_price |
| exchange_reconcile.py:1813 | _tag_external_operator_closed_position_holdings | chain_shares=0 |
| edli_position_bridge.py:1003 | _absorb_same_order_duplicate_bridge_fill | shares, cost_basis_usd, size_usd, entry_price |

**Consumers:** `portfolio.py` (Position load → total_exposure/risk), `riskguard.py`,
`cycle_runtime.py` monitor+exit, `projection.py` read-back.

### position_lots — 205 rows — CHAIN-DERIVABLE (economics) — append-only

`shares, entry_price_avg, exit_price_avg` — trigger-enforced to equal a MATCHED/MINED
`venue_trade_facts` fill (`position_lots_*_trade_authority` triggers). LOCAL: state,
captured_at, local_sequence, raw_payload_hash, source; `source_trade_fact_id`/
`source_command_id` = FK link to authoritative fill.
**Writer:** `venue_command_repo.py:3238 append_position_lot` (INSERT only; no UPDATE anywhere).
**Consumers:** portfolio cost-basis reconstruction, family_exclusive_dedup.

### position_events — 162364 rows — MIXED — append-only

Event envelope LOCAL-TRUTH (Zeus lifecycle transitions + own timestamps); embedded economics
in `ENTRY_ORDER_FILLED`/`EXIT_ORDER_FILLED`/`SETTLED` payloads are CHAIN-DERIVABLE.
**Writer:** `ledger.py append_many_and_project`. **Consumers:** replay, projection rebuild.

### collateral_ledger_snapshots — 101291 rows — MIXED — **pure chain-balance mirror**

| column(s) | class | notes |
|---|---|---|
| pusd_balance_micro, pusd_allowance_micro, usdc_e_legacy_balance_micro, ctf_token_balances_json, ctf_token_allowances_json | CHAIN-DERIVABLE | **direct on-chain balance/allowance copy** (authority_tier='CHAIN') |
| reserved_pusd_for_buys_micro, reserved_tokens_for_sells_json | LOCAL-TRUTH | Zeus reservation overlay |
| authority_tier, captured_at, raw_balance_payload_hash | LOCAL-TRUTH | provenance |

**Writer:** `collateral_ledger.py:799 CollateralLedger._persist_snapshot` (INSERT only).
**Consumers:** available-balance math + `trg_reservations_no_overreserve` trigger; executor
pre-submit collateral gate. Highest row count of any economics table; grows per refresh.

### collateral_reservations — 1102 rows — LOCAL-TRUTH

Per-command pending-order reservation; `amount` computed from Zeus's own order intent (not
chain), shadows chain-committed collateral.
**Writers (INSERT):** `collateral_ledger.py:527 _cas_insert_pusd_reservation`, `:558
_cas_insert_ctf_reservation`, `:636 _insert_reservation` (all set `amount`).
**Updates:** `:663 _release_reservation` / `:912 release_reservation_for_command_state`
(released_at), `:1014 convert_reservation_on_fill` (converted_amount).

### collateral_unsettled_proceeds — 58 rows — LOCAL-TRUTH

In-flight OUTGOING_DEDUCTION / INCOMING_PROCEEDS between submit and chain settle; heals on
chain confirm. **Writers (INSERT):** `collateral_ledger.py:1028`/`:1041 convert_reservation_on_fill`
(amount_micro). **Update:** `:1063 _clear_matured_unsettled_proceeds` (settled_at).

### venue_trade_facts — 636 rows — CHAIN-DERIVABLE — append-only ingest boundary

`filled_size, fill_price, fee_paid_micro, tx_hash, block_number, confirmation_count` = **the
local canonical record of chain/venue fills.** LOCAL: state, source, observed_at,
local_sequence, raw_payload_hash; trade_id/venue_order_id/command_id = order-identity link.
**Writer:** `venue_command_repo.py:3120 append_trade_fact` (append-only; UPDATE-block trigger
db.py:1468/5630). The ingest boundary — a mirror, but the authoritative local landing of
chain truth (source-tagged REST/WS/CHAIN). **Consumers:** position_lots authority triggers,
projection, harvester.

### venue_order_facts — 35639 rows — CHAIN-DERIVABLE (order-state mirror) — append-only

`remaining_size, matched_size`. **Writer:** `venue_command_repo.py:3028 append_order_fact`
(append-only; UPDATE-block trigger db.py:1432/5595). **Consumers:** order lifecycle projection.

### execution_fact — 534 rows — MIXED

`submitted_price` LOCAL; `fill_price`, `shares` CHAIN-DERIVABLE; fill_quality/latency derived.
**Writer:** `db.py:9887 log_execution_fact` (upsert). **Consumers:** attribution, strategy_health.

### outcome_fact — 148 rows — MIXED

`pnl`, `outcome` (0/1) CHAIN-DERIVABLE (from settlement payout); entered_at/exited_at/
settled_at/monitor_count/chain_corrections_count/hold_duration_hours LOCAL.
**Writer:** `db.py:9963 log_outcome_fact` (upsert). **Consumer:** `replay.py` diagnostics —
flagged legacy, learning-ineligible (`LEGACY_OUTCOME_FACT_*`).

### trade_decisions — 3106 rows — MIXED (decision-core LOCAL, attribution CHAIN)

Core LOCAL-TRUTH: p_raw/p_calibrated/p_posterior/edge/ci_lower/ci_upper/kelly_fraction,
strategy, edge_source, JSON snapshots. CHAIN-derived: fill_price, filled_at, fill_quality,
and Phase-3 attribution `entry_alpha_usd/execution_slippage_usd/exit_timing_usd/
risk_throttling_usd/settlement_edge_usd`.
**Writers:** `db.py:10286 log_trade_exit` (INSERT full row incl settlement_edge_usd),
`db.py:10369 update_trade_lifecycle` (UPDATE status/size_usd/price/filled_at/fill_price/
fill_quality/…), `trade_decisions_synthesizer.py:105 synthesize_missing_bridge` (status=
'synthesized' bridge row), `harvester.py:2729 _settle_positions` (UPDATE settlement_edge_usd,
exit_reason←'SETTLEMENT', status→'settled'). **Consumer:** attribution, learning loop.

### settlement_commands — 92 rows — MIXED (redemption payouts)

`pusd_amount_micro, token_amounts_json, tx_hash, block_number, confirmation_count,
winning_index_set` = CHAIN-DERIVABLE (redemption payout + resolution index set); state/
requested_at/error_payload/anchor/skew = LOCAL.
**Writers:** `settlement_commands.py:489 request_redeem` (INSERT), `:446` (backfill
winning_index_set), `:850 reconcile_pending_redeems`, `:1247 _transition`, `:1311
_atomic_transition` (CAS tx_hash/block/confirmation/state), `:316 ensure_settlement_schema_ready`
(migration), `executor.py:5707 _persist_exit_ack_facts` (timing). Per memory
`redeem-abandoned-third-party`, Zeus does not submit redeem tx — tracks third-party
redemption chain facts.

### ctf_conversion_commands — 0 rows — MIXED (empty)

`amount_micro, tx_hash, block_number` CHAIN-DERIVABLE. **Writers:**
`ctf_conversion_commands.py:264 _enqueue`, `:543 _atomic_transition`. No live weight.

### wrap_unwrap_commands — MIXED (pUSD↔USDC wrap tx)

`amount_micro, tx_hash, block_number, confirmation_count` CHAIN-DERIVABLE. **Writers:**
`wrap_unwrap_commands.py:201`/`:587`/`:614`/`:869` (INSERT), `:489 _transition` (CAS UPDATE).

### token_price_log / market_price_history — market-data logs — CHAIN/venue-observable

Recorded quotes (price/bid/ask/best_bid/best_ask/spread). Chain/venue-observable but these
are observation logs, not position economics — low excision blast radius. **Writers:** gamma
scanner ingest; `scripts/backfill_current_market_price_snapshots.py`.

---

## WORLD DB — state/zeus-world.db

| table | rows | class | writers / notes |
|---|---|---|---|
| position_current, position_lots, position_events | **0** | — | GHOST SHELLS (legacy_archived, ownership yaml: "drop after 2026-08-15"); authoritative on TRADE. No live weight. |
| settlements | 0 | — | dead shell; **no direct INSERT/UPDATE anywhere in src/** (only migration-copy `db.py:3245` + authority guard triggers `db.py:3294`/`:3382`). |
| settlement_attribution | 286 | MIXED | CHAIN-DERIVABLE: avg_fill_price, won, market_in_bin_prob, settled_value/in_bin. LOCAL: q_live/q_lcb_5pct/q_in_bin, category, skill attribution, posterior provenance. Writer: `src/analysis/settlement_skill_attribution.py:1183 persist_grade` (INSERT ON CONFLICT(position_id) re-grade). Consumer: learning/attribution. |
| edli_live_profit_audit | (world) | MIXED | **local realized-P&L audit — see dedicated note below.** |
| shoulder_exposure_ledger | 0 | LOCAL | notional_usd exposure accounting; empty. |

### edli_live_profit_audit — MIXED — **locally-computed realized P&L (flag)**

CHAIN-DERIVABLE: avg_fill_price, filled_size, fees, best_bid/ask, `pnl_usd`, `realized_edge`,
`edge_value_usd`, settlement_outcome. LOCAL: q_live/q_lcb_5pct, expected_* projections, kelly_
size_usd, order policy, certificate hashes. **Two writers:**
- `src/events/live_profit_audit.py:238 insert_record` (full row, INSERT ON CONFLICT(audit_id)
  DO UPDATE) — called via `record_edli_live_profit_audit_from_aggregate` (:400) from
  `live_order_aggregate.py:298-300`.
- `src/analysis/settlement_skill_attribution.py:1128 writeback_settlement_pnl_to_audit`
  (UPDATE pnl_usd, settlement_outcome only). **pnl_usd is computed locally at :1124** =
  `(settled_payoff − avg_fill_price) * filled_size − fee_total`; settlement_outcome='WON'/'LOST'.
  A locally-persisted realized-P&L column derived from chain fills+payout = disease-class.

## FORECASTS DB — state/zeus-forecasts.db (settlement authority)

| table | rows | class | writers / notes |
|---|---|---|---|
| settlement_outcomes | **8958** | MIXED | **live settlement authority.** `winning_bin` = CHAIN-DERIVABLE (maps to condition payout vector). `settlement_value` (physical temperature) = **LOCAL-TRUTH — chain does NOT know the temperature** (weather observation). resolution_state/settlement_station/settlement_unit/authority = LOCAL. Writers: `db.py:7965 log_settlement` (INSERT ON CONFLICT(city,target_date,temperature_metric)), `scripts/drain_settlement_disputes.py:522 _apply_verify` + `:549 _insert_missing_verified`. Consumers: calibration, replay, forecast skill, position settlement grading. |
| settlements | 8956 | MIXED | parallel/legacy of settlement_outcomes; same winning_bin(chain)/settlement_value(local) split. No live writer (see WORLD note). |

---

## Wholly-parallel-ledger ranking (by blast radius)

1. **position_current economics + chain_\* mirror** (TRADE, 1002 rows) — HIGHEST.
   Central position truth. chain_shares/chain_avg_price/chain_cost_basis_usd are a literal
   chain CTF-balance mirror; cost_basis_usd/shares/realized_pnl_usd/exit_price/settlement_price
   are all chain-derivable. INSERT is single-funnel (`projection.py:658`), but chain_*/exit_price
   are ALSO written by ~11 direct-UPDATE recovery/reconcile sites (table above) — the excision
   must re-point every one. Every risk/exposure/exit/monitor consumer reads it.

2. **collateral_ledger_snapshots** (TRADE, 101291 rows) — pure on-chain balance/allowance
   mirror (pUSD/USDC/CTF, authority_tier='CHAIN'). Highest row count, grows per refresh. A
   local re-copy of a fact `balanceOf` answers directly. Single writer
   (`collateral_ledger.py:799`) but the over-reserve trigger + collateral gate read it.

3. **venue_trade_facts → position_lots fill spine** (TRADE, 636 + 205 rows).
   venue_trade_facts (`venue_command_repo.py:3120`) is the local mirror of chain fills
   (tx_hash/fill_price/filled_size/block_number); position_lots (`:3238`) reconstructs cost
   basis from it under authority triggers. The derive-from-fills chain feeding every
   position_current economics number — the second-order parallel ledger behind #1.

Runner-up (locally-computed P&L, small but disease-shaped): `edli_live_profit_audit.pnl_usd`
+ `realized_edge` — a stored realized-P&L recomputed at `settlement_skill_attribution.py:1124`
from chain fills+payout. Same clobber-class as position_current.realized_pnl_usd.

### Flags / ambiguities
- `settlement_outcomes` is MIXED, not a wholly-parallel ledger: `winning_bin` mirrors the
  payout vector but `settlement_value` (temperature) is genuine LOCAL weather truth the chain
  never knows. Excising must keep settlement_value, derive winning_bin.
- `settlements` tables (TRADE + WORLD) are 0-row dead shells with no live writer; live
  settlement authority is `settlement_outcomes` (FORECASTS). `settlements_v2` is written by
  `scripts/backfill_settlements_via_gamma_2026.py:360` (different table — flag if in scope).
- WORLD `position_*` are 0-row ghost shells (drop 2026-08-15) — exclude from scope.
- `collateral_reservations` / `collateral_unsettled_proceeds` shadow chain collateral but are
  computed from Zeus's OWN order intent (LOCAL-TRUTH), not read from chain — intent overlays,
  not chain mirrors.
- Direct-UPDATE economics writers on position_current (esp. the chain_* zeroing in
  command_recovery/exit_lifecycle/exchange_reconcile) are the drift-and-repair machinery the
  excision predicts: a derive-on-read layer would delete both the columns and these repairers.
