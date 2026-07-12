# Census — Chain/Venue Data Zeus Can Already Sync

Scope: read-only code inspection at `/Users/leofitz/zeus`, no external API calls.
Question grounded: can every chain-derivable local column be replaced by a synced
chain fact TODAY, and through which existing client?

Two client modules own all Polymarket/Polygon I/O:

- `src/data/polymarket_client.py` — thin HTTP wrapper. Public CLOB
  (`https://clob.polymarket.com`) for books/markets/fee-rate (no auth); Polymarket
  `data-api` (`https://data-api.polymarket.com`) for `/positions`. Delegates all
  authenticated venue I/O to the V2 adapter.
- `src/venue/polymarket_v2_adapter.py` — the ONLY place `py_clob_client_v2` and
  Polygon JSON-RPC are touched. Authenticated CLOB L2 (orders/trades/balance),
  data-api `/positions` fallback, and direct `eth_call` RPC
  (`https://polygon-bor-rpc.publicnode.com`) for ERC20 allowance + CTF `balanceOf`.

There is NO Gamma-for-execution, NO subgraph, and NO generic web3 lib — RPC is a
hand-rolled `urllib` JSON-RPC (`_json_rpc_call`). `redeem()` is
`REDEEM_SUBMISSION_FORBIDDEN` unconditionally (operator law); the autonomous
broadcast body was deleted.

---

## Fact-family table

| fact family | source (client + endpoint) | fields / keys available | cadence / daemon | existing consumer | gaps (not obtainable / lossy) |
|---|---|---|---|---|---|
| **Order books / prices** | public CLOB `GET /book`, `POST /books`, `GET /markets/{condition_id}`, `GET /fee-rate` — `polymarket_client.py:609/668/697/753` | bids/asks price+size, `asset_id`, `tick_size`, `min_order_size`, `neg_risk`, `last_trade_price`; market `enable_order_book`/`accepting_orders`/`archived`; fee `base_fee`/`feeRate` | price-channel-ingest 60s (`price_channel_daemon.py:335-338`); substrate-observer 20s universe/warm (`substrate_observer_daemon.py:84`) → writes `executable_market_snapshots`, `book_hash_transitions` on trades.db | `market_scanner.capture_executable_market_snapshot`, submit-time JIT book (GATE #84) | none material; book is NOT a tradability authority (that's the `/markets` read) |
| **Open orders** | auth CLOB SDK `get_open_orders`/`get_orders`, `get_order(order_id)` — adapter `polymarket_v2_adapter.py:1011/1004`; wrapper `polymarket_client.py:990/896` | `order_id`, `status`, plus raw order dict; parsed by `parse_order_status` | on demand (reactor/exit/recovery); `venue_order_facts` stream from WS user channel + REST reconcile | `exchange_reconcile`, `command_recovery`, `chain_reconciliation` | key is the wallet's own L2 API creds → returns Zeus+operator orders on the shared wallet; disambiguated only by joining `order_id`→`venue_commands` |
| **Fills / trades** | auth CLOB SDK `get_trades(since)` — adapter `polymarket_v2_adapter.py:1035`, wraps `TradeFact(raw)` | raw trade dict carrying `taker_order_id`, `maker_orders[].order_id`, `trade_id`/tx-hash, matched size/price, `state`/`status` | on demand inside live-trading reconcile sweep + command-recovery; NOT its own scheduled poller | `exchange_reconcile.py:455` (`get_trades()`), `command_recovery.py:13383`, `edli_absence_resolver.py:139` | **shared-wallet**: trades returned for the whole wallet; Zeus-attribution = join `taker/maker order_id`→`venue_commands.venue_order_id`. Lossy: same real fill seen as `trade_id=tx_hash` aggregate AND as child trade IDs → 1x–4x double-count; fixed by `fill_dedup.canonical_trade_fact_cte` + economic-identity reducer (PLAN 2026-07-11) |
| **Positions (holdings)** | data-api `GET /positions?user={funder}&sizeThreshold=0.01` — `polymarket_client.py:1017`; adapter fallback `polymarket_v2_adapter.py:1054` | `asset`(token_id), `conditionId`, `size`, `avgPrice`, `initialValue`, `currentValue`, `cashPnl`, `curPrice`, `redeemable`, `title`, `endDate`, `outcome` | pulled inside reconcile/bankroll paths (cycle_runtime, fill_tracker, bankroll_provider) — no dedicated cadence | `chain_mirror_reconciler.py:1281`, `cycle_runtime.py:2120`, `bankroll_provider.py:480`, `fill_tracker.py:1121` | **wallet-aggregate, NOT order-scoped**: no `order_id` on a position → cannot separate Zeus vs operator holdings; `avgPrice`/`cashPnl` are venue-computed across ALL wallet fills. This is the shared-wallet contamination surface |
| **pUSD (USDC) collateral** | auth CLOB `get_balance_allowance(COLLATERAL)` — adapter `polymarket_v2_adapter.py:1237/1075`; chain ERC20 allowance fallback `eth_call 0xdd62ed3e` `polymarket_v2_adapter.py:1310` | `pusd_balance_micro`, `pusd_allowance_micro`, `authority_tier` (CHAIN/DEGRADED), `pusd_allowance_source` | post-trade-capital `collateral_snapshot_refresh_cycle` 30s (`post_trade_capital_daemon.py:24`) → `CollateralLedger` | `CollateralLedger.refresh`, executor preflight, RiskGuard (`get_wallet_balance`) | none; wallet-scalar is correct (collateral is not per-strategy) |
| **CTF outcome-token balance (shares held)** | auth CLOB `get_balance_allowance(CONDITIONAL, token_id)` — adapter `polymarket_v2_adapter.py:1276`; on-chain `CTF.balanceOf(safe, positionId)` for winners `polymarket_v2_adapter.py:1406/1543` | per-token `balance` (units), `allowance`; winner path derives `positionId` from `conditionId`+index_set via RPC | full enum in `get_collateral_payload` (30s cycle); targeted `get_ctf_collateral_payload(token_ids)` at exit submit | exit chain-truth gate (sell inventory proof), CollateralLedger CTF map | **which tokens to query comes from data-api `/positions`** (SDK has no `get_positions`) → CTF enumeration inherits the wallet-aggregate token set; per-token balanceOf itself is exact and Zeus-independent |
| **Market resolved (signal)** | Gamma poll for settled weather markets (world-side) — `harvester_truth_writer.py:250`; per-position `redeemable` bool from data-api `/positions` | `redeemable` (bool per token), Gamma closed/settled flag | harvester `_harvester_cycle` 1h (`post_trade_capital_daemon.py:15`) | `harvester`, `chain_mirror_reconciler` SettlementFact/redeemable | gives only "it settled"; no payout numerics |
| **Winning outcome / payout vector** | DERIVED from weather, not chain: `SettlementSemantics` on WU observations → `settlement.winning_bin` (forecasts.db) — `harvester_truth_writer.py:78/471`, read via `chain_mirror_reconciler.load_settlement_lookup:555` | `winning_bin`, `authority`(VERIFIED), city/target_date/metric | harvester 1h | settlement writers, exit grading (`grade_bin`), P&L resolver | **on-chain `payoutNumerators` is NOT read as settlement authority.** Only touched inside redeem-preflight balance derivation (`get_negrisk/standard_ctf_winning_position_balance`), never wired to settlement/P&L |
| **Freshness / latency** | `ws_gap_guard` (WS user channel), `heartbeat_supervisor` | `stale_after_seconds` SLO, `is_stale`, `subscription_state` (AUTHED/SUBSCRIBED), `m5_reconcile_required`; heartbeat `cadence_seconds`, status_max_age | venue-heartbeat 2s, status max-age 8s, restart-seed max-age 30s (`heartbeat_supervisor.py:32-35`); WS gap continuous | submit gate (WS gap blocks submits), live_health | measures WS message-gap + heartbeat age; per-fact `chain_seen_at`/`observed_at` timestamps exist on order/trade/position facts (source-issued vs fetched vs written) |

### The identity chain (attribution linkage — end to end)

`venue_commands.command_id` (Zeus intent)
→ submit response populates `VenueSubmissionEnvelope.order_id` + `trade_ids` +
  `transaction_hashes` (`contracts/venue_submission_envelope.py:65-67`)
→ persisted as `venue_commands.venue_order_id`, `trade_ids_json`,
  `transaction_hashes_json` (`venue_command_repo.py:754/796`)
→ WS/REST trade events carry `taker_order_id` / `maker_orders[].order_id`
  (`polymarket_user_channel.py:267-291`, `exchange_reconcile.py:5961 _trade_order_ids`)
→ joined to command via `_local_command_for_trade` / `_local_commands_by_order`
  (`exchange_reconcile.py:5164/5454`)
→ deduped into `venue_trade_facts(command_id, trade_id, filled_size, fill_price)`
→ aggregated per command by `reconcile/chain_truth.py:140` (canonical CTE, never bare SUM).

**Where the join is lossy / where it holds:**
- HOLDS: `order_id` is the through-key. A foreign (operator) fill has an
  `order_id` absent from `venue_commands` → it is quarantined as
  `unrecorded_trade` / `exchange_trade_unlinked_to_local_command`
  (`exchange_reconcile.py:477-491`) and never stacked onto Zeus positions. This is
  the mechanism that makes Zeus-only P&L derivable on a shared wallet.
- LOSSY #1: data-api `/positions` has NO order key — it is wallet-aggregate, so
  `avgPrice`/`cashPnl`/`size` conflate operator co-holdings. Zeus does not trust it
  for per-strategy P&L; it uses the command→trade join instead, and the diff
  engine (`reconcile/diff_engine.py`) reconciles local (command-attributed) vs
  chain (wallet-aggregate).
- LOSSY #2: tx-hash-vs-child-trade-id aliasing double-counts one real fill
  (PLAN 2026-07-11 work record); mitigated by the canonical dedup CTE + economic
  identity reducer, not eliminated at the source.

---

## Sufficiency verdicts

**Fills + order linkage — SUFFICIENT TODAY.** `get_trades()` (auth CLOB) returns
trade records carrying `taker_order_id` + `maker_orders[].order_id`, and
`exchange_reconcile` already joins them to `venue_commands.venue_order_id` →
`command_id`; foreign fills are detectable (order_id not in venue_commands) and
quarantined. The end-to-end key (`envelope.order_id`/`trade_ids` →
`venue_command_repo` → WS/REST trade → `venue_trade_facts` → canonical CTE) exists.
Residual work is dedup hygiene (tx-hash vs child trade IDs), not a missing
ingester.

**Balances — SUFFICIENT TODAY.** pUSD via CLOB `get_balance_allowance(COLLATERAL)`
(+ chain ERC20 allowance fallback), refreshed 30s by post-trade-capital →
CollateralLedger. Per-outcome CTF share balance is readable via
`get_balance_allowance(CONDITIONAL, token_id)` and exactly via on-chain
`CTF.balanceOf` for winners. Caveat: the SDK has no `get_positions`, so the *set*
of CTF tokens to enumerate is discovered from the wallet-aggregate data-api
`/positions`; per-token balances themselves are exact and wallet-scalar-correct.

**Resolution payouts — NEEDS NEW INGESTER (for the on-chain payout vector).**
Today Zeus reads only the "market settled" signal (Gamma poll 1h) and a per-
position `redeemable` boolean from data-api; the actual winning outcome / payout
is DERIVED from WU weather via `SettlementSemantics` (forecasts.db `settlement`),
not read from chain. On-chain `payoutNumerators` is touched only inside redeem-
preflight balance math, never wired as settlement authority. If "chain facts sole
authority" requires the on-chain payout vector / condition resolution as ground
truth, a new read path (ConditionalTokens `payoutNumerators` / redemption events
via the existing `_json_rpc_call`) must be added — the RPC plumbing exists, the
ingester + settlement wiring does not.

**Open orders — SUFFICIENT TODAY.** `get_open_orders()` and point-read
`get_order(order_id)` via the auth CLOB SDK, plus the `venue_order_facts` WS stream
reconciled against REST, all keyed by `order_id` and joinable to `command_id`;
`ws_gap_guard` gates submits when the stream is stale. Same shared-wallet caveat as
fills (returns wallet-wide orders), resolved by the same order_id→command join.
