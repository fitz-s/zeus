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

**Note on evidence:** schemas via `.schema` (clean), row counts via read-only COUNT(*)
(clean), domain routing from domains.py (clean). Raw multiline `rg` output in this repo
is mangled by RTK tool-result compaction (table/function names collapse to single
letters — see memory `9router-rtk-toolresult-compaction`); writer file:lines below were
captured pre-mangle or via the single-funnel call graph, which is authoritative.

---

## TRADE DB — state/zeus_trades.db (authoritative money-path)

### position_current — 1002 rows — MIXED — **top blast radius**

Central position ledger. Single write funnel `upsert_position_current` at
`src/state/projection.py:562`.

| column(s) | class | notes |
|---|---|---|
| shares, cost_basis_usd, entry_price, size_usd | CHAIN-DERIVABLE | position economics derived from fills (venue_trade_facts → lots) |
| chain_shares, chain_avg_price, chain_cost_basis_usd | CHAIN-DERIVABLE | **explicit chain CTF-balance mirror columns** |
| realized_pnl_usd | CHAIN-DERIVABLE | `close_economics.py:67` = `round(shares*exit_price − cost_basis_usd, 2)` |
| exit_price | CHAIN-DERIVABLE | exit fill price (= proceeds/share) |
| settlement_price | CHAIN-DERIVABLE | condition resolution payout (0/1) |
| last_monitor_market_price, last_monitor_best_bid, last_monitor_best_ask, last_monitor_market_vig | CHAIN-DERIVABLE | venue/chain-observable market quote |
| settled_at, chain_seen_at, chain_absence_at | MIXED-timestamp | chain-observation timestamps (chain-derivable event) |
| position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label, direction, unit, token_id, no_token_id, condition_id, order_id, order_status, chain_state | LOCAL-TRUTH | identity + lifecycle |
| p_posterior, last_monitor_prob, last_monitor_edge, entry_ci_width | LOCAL-TRUTH | Zeus beliefs |
| decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode, exit_reason, updated_at, temperature_metric, fill_authority, recovery_authority, exit_retry_count, next_exit_retry_at | LOCAL-TRUTH | provenance / intent / action timestamps |

**Writers (funnel + callers):** `projection.py:562 upsert_position_current` (SINGLE FUNNEL),
called from `ledger.py:646` (append_many_and_project), `cycle_runtime.py:3157`,
`exchange_reconcile.py:5016`, `:5072`, `command_recovery.py:4153`, `:4269`, `:5552`,
`:6183`, `:6279`, `edli_position_bridge.py:1660`.
**chain_\* setters** (on Position before funnel): `chain_reconciliation.py:1476-1506`
(_correct/_materialize), `:1612-1614` (terminal restore), `:1822-1855` (reconcile loop);
`chain_mirror_reconciler.py:937`. **realized_pnl_usd:** `close_economics.py:45
compute_realized_pnl_usd`.
**Consumers:** `portfolio.py` (Position load → total_exposure/risk), `riskguard.py`,
`cycle_runtime.py` monitor+exit, `projection.py` read-back.

### position_lots — 205 rows — CHAIN-DERIVABLE (economics) — append-only

| column(s) | class | notes |
|---|---|---|
| shares, entry_price_avg, exit_price_avg | CHAIN-DERIVABLE | trigger-enforced to equal a MATCHED/MINED `venue_trade_facts` fill (see `position_lots_*_trade_authority` triggers) |
| source_trade_fact_id, source_command_id | LOCAL-link | FK to the authoritative fill fact |
| state, captured_at, local_sequence, raw_payload_hash, source | LOCAL-TRUTH | lot lifecycle + provenance |

**Writer:** `src/state/venue_command_repo.py` (lot-append path; minting site cited in
excision doc at venue_command_repo.py:3291-3329). Append-only (update/delete triggers abort).
**Consumers:** portfolio lot reconstruction / cost-basis, `family_exclusive_dedup`.

### position_events — 162364 rows — MIXED — append-only

Event envelope is LOCAL-TRUTH (Zeus's lifecycle transitions + own timestamps). Embedded
economics in `ENTRY_ORDER_FILLED` / `EXIT_ORDER_FILLED` / `SETTLED` events (payload JSON)
are CHAIN-DERIVABLE. **Writer:** `ledger.py append_many_and_project`. **Consumers:** replay,
projection rebuild.

### collateral_ledger_snapshots — 101291 rows — MIXED — **pure chain-balance mirror**

| column(s) | class | notes |
|---|---|---|
| pusd_balance_micro, pusd_allowance_micro, usdc_e_legacy_balance_micro, ctf_token_balances_json, ctf_token_allowances_json | CHAIN-DERIVABLE | **direct on-chain balance/allowance copy** when authority_tier='CHAIN' |
| reserved_pusd_for_buys_micro, reserved_tokens_for_sells_json | LOCAL-TRUTH | Zeus's own reservation overlay |
| authority_tier, captured_at, raw_balance_payload_hash | LOCAL-TRUTH | provenance |

**Writer:** `src/state/collateral_ledger.py` snapshot INSERT (~:800, persist path).
**Consumers:** `collateral_ledger.py` available-balance math + `trg_reservations_no_overreserve`
trigger; executor pre-submit collateral gate. Highest row count of any economics table;
actively growing (snapshot-per-refresh).

### collateral_reservations — 1102 rows — LOCAL-TRUTH

Per-command pending-order reservation. `amount` computed from Zeus's own order intent (not
chain), but shadows chain-committed collateral. **Writer:** `collateral_ledger.py`.
**Consumer:** over-reserve trigger, available-balance.

### collateral_unsettled_proceeds — 58 rows — LOCAL-TRUTH

In-flight OUTGOING_DEDUCTION / INCOMING_PROCEEDS Zeus tracks between submit and chain
settle; heals when chain confirms. **Writer:** `collateral_ledger.py`. Derived from Zeus's
own commands, not chain.

### venue_trade_facts — 636 rows — CHAIN-DERIVABLE — append-only ingest boundary

| column(s) | class | notes |
|---|---|---|
| filled_size, fill_price, fee_paid_micro, tx_hash, block_number, confirmation_count | CHAIN-DERIVABLE | **the local canonical record of chain/venue fills** |
| trade_id, venue_order_id, command_id | LOCAL-link | order identity linkage |
| state, source, observed_at, local_sequence, raw_payload_hash | LOCAL-TRUTH | ingest provenance |

**Writer:** `venue_command_repo.py` (append_trade_fact path). This is the ingest boundary —
a mirror, but the authoritative local landing of chain truth (source-tagged REST/WS/CHAIN).
**Consumers:** `position_lots` authority triggers, projection, harvester.

### venue_order_facts — 35639 rows — CHAIN-DERIVABLE (order-state mirror) — append-only

`remaining_size`, `matched_size` = venue/chain order-state mirror. **Writer:**
`venue_command_repo.py`. **Consumers:** order lifecycle projection, reconcile.

### execution_fact — 534 rows — MIXED

`submitted_price` LOCAL (Zeus's price); `fill_price`, `shares` CHAIN-DERIVABLE;
`fill_quality`, `latency_seconds` derived. **Writer:** execution/harvester fact path.
**Consumers:** attribution, strategy_health.

### outcome_fact — 148 rows — MIXED

`pnl`, `outcome` (0/1) CHAIN-DERIVABLE (from settlement payout); `entered_at`, `exited_at`,
`settled_at`, `monitor_count`, `chain_corrections_count`, `hold_duration_hours` LOCAL.
**Writer:** `db.py log_outcome_fact` via harvester. **Consumer:** `replay.py` diagnostics —
flagged legacy, learning-ineligible (`LEGACY_OUTCOME_FACT_*`).

### settlement_commands — 92 rows — MIXED (redemption payouts)

`pusd_amount_micro`, `token_amounts_json`, `tx_hash`, `block_number`, `confirmation_count`,
`winning_index_set` = CHAIN-DERIVABLE (redemption payout + resolution index set); `state`,
`requested_at`, `error_payload`, anchor/skew fields = LOCAL. **Writer:** `harvester.py`,
`src/execution/settlement_commands.py`. Note: per memory `redeem-abandoned-third-party`,
Zeus does not submit redeem tx — these track third-party redemption chain facts.

### ctf_conversion_commands — 0 rows — MIXED (empty)

`amount_micro`, `tx_hash`, `block_number` CHAIN-DERIVABLE. No live weight.

### trade_decisions — 3106 rows — MIXED (decision-core LOCAL, attribution CHAIN)

Core is LOCAL-TRUTH decision provenance: `p_raw`, `p_calibrated`, `p_posterior`, `edge`,
`ci_lower/upper`, `kelly_fraction`, `strategy`, `edge_source`, `entry_method`, JSON
snapshots. CHAIN-derived columns: `fill_price`, `filled_at`, `fill_quality`, and the Phase-3
attribution decompositions `entry_alpha_usd`, `execution_slippage_usd`, `exit_timing_usd`,
`risk_throttling_usd`, `settlement_edge_usd` (computed from realized chain outcomes).
**Writer:** `db.py` (decision insert + fill/attribution update). **Consumer:** attribution
reporting, learning loop.

### token_price_log / market_price_history — market-data logs — CHAIN/venue-observable

Recorded market quotes (`price`, `bid`, `ask`, `best_bid`, `best_ask`, `spread`). Chain/
venue-observable but these are observation logs, not position economics — low excision blast
radius. **Writers:** gamma scanner ingest; `scripts/backfill_current_market_price_snapshots.py`.

---

## WORLD DB — state/zeus-world.db

| table | rows | class | notes |
|---|---|---|---|
| position_current, position_lots, position_events | **0** | — | GHOST SHELLS (legacy_archived, ownership yaml: "drop after 2026-08-15"); authoritative copies on TRADE. No live weight. |
| settlements | 0 | — | dead shell (see forecasts.settlement_outcomes for live authority) |
| settlement_attribution | 286 | MIXED | `avg_fill_price`, `won`, `market_in_bin_prob` (from fill price) = CHAIN-DERIVABLE; `q_in_bin`, `q_live`, `q_lcb_5pct`, `category`, skill-attribution = LOCAL analytics. Writer: `src/cron/settlement_attribution.py`. Consumer: learning/attribution reports. |
| shoulder_exposure_ledger | 0 | LOCAL | `notional_usd` exposure accounting; empty. |

## FORECASTS DB — state/zeus-forecasts.db (settlement authority)

| table | rows | class | notes |
|---|---|---|---|
| settlement_outcomes | **8958** | MIXED | **live settlement authority.** `winning_bin` = CHAIN-DERIVABLE (maps to condition payout vector / resolution outcome). `settlement_value` (physical temperature) = **LOCAL-TRUTH — chain does NOT know the temperature**; this is a weather observation. `resolution_state`, `settlement_station`, `settlement_unit`, `authority` = LOCAL. Writer: `harvester.py log_settlement_event` / `settlement_writers.py`. Consumers: calibration, replay, forecast skill, position settlement grading. |
| settlements | 8956 | MIXED | parallel/legacy of settlement_outcomes; same winning_bin(chain)/settlement_value(local) split. |

---

## Wholly-parallel-ledger ranking (by blast radius)

1. **position_current economics + chain_\* mirror** (TRADE, 1002 rows) — HIGHEST.
   Central position truth. `chain_shares/chain_avg_price/chain_cost_basis_usd` are a literal
   chain CTF-balance mirror; `cost_basis_usd/shares/realized_pnl_usd/exit_price/
   settlement_price` are all chain-derivable. Single funnel (`projection.py:562`) but ~10
   caller sites + 2 chain-reconcile setter modules. Every risk / exposure / exit / monitor
   consumer reads it. Excising local economics here rewrites the money-path spine.

2. **collateral_ledger_snapshots** (TRADE, 101291 rows) — pure on-chain balance/allowance
   mirror (pUSD/USDC/CTF), authority_tier='CHAIN'. Highest row count, actively growing
   (snapshot-per-refresh). Feeds collateral gate + over-reserve trigger. A local re-copy of
   a fact `balanceOf` answers directly.

3. **venue_trade_facts → position_lots fill spine** (TRADE, 636 + 205 rows). venue_trade_facts
   is the local mirror of chain fills (tx_hash, fill_price, filled_size, block_number);
   position_lots reconstructs cost basis from it under authority triggers. This derive-from-
   fills chain is what feeds every position_current economics number — the second-order
   parallel ledger behind #1.

Runner-up (NOT a pure parallel ledger): `settlement_outcomes.winning_bin` mirrors the payout
vector, but `settlement_value` is genuinely local weather truth — so the table is MIXED, not
a wholly-parallel chain ledger.

### Flags / ambiguities
- `settlements` tables in TRADE and WORLD are 0-row dead shells; the live settlement authority
  is `settlement_outcomes` (FORECASTS). Any excision must target forecasts, not the shells.
- WORLD `position_*` are 0-row ghost shells scheduled for drop 2026-08-15 — exclude from scope.
- `collateral_reservations` / `collateral_unsettled_proceeds` shadow chain collateral but are
  computed from Zeus's OWN order intent (LOCAL-TRUTH), not read from chain — classify as intent
  overlays, not chain mirrors, despite sitting on the collateral surface.
- Exhaustive per-INSERT-site writer lists for venue_*/settlement_outcomes/trade_decisions are
  being backfilled by two enumeration sub-agents; funnel-level writers above are authoritative.
