# Wallet-history reconciliation — 2026-06-10
# Created: 2026-06-10
# Authority basis: operator directive "进行对账…表中无记录的订单,老于june的订单直接从本地清掉…
# 本地和表中共享的订单但是具体的记录内容不一样,说明我们有问题"; source of truth =
# /Users/leofitz/Documents/Polymarket-History-2026-06-10.numbers (operator's on-chain wallet
# history, weather-filtered, 123 rows, 2026-06-06..06-10T22:55:45Z).

## Method
Hash-level join: sheet tx hashes (116) × local tx hashes (85) from venue_commands→
venue_order_facts envelopes + settlement_commands. BOTH=65, SHEET-ONLY=51, LOCAL-ONLY=20.
Full table: /tmp/recon_report.txt (regenerable from /tmp/sheet_rows.json + /tmp/local_rows.json).

## Finding 1 — THE FEE LAW (the systemic "our problem", now solved exactly)
Every shared Buy showed sheet usdc > local price×size. Numeric fit across ALL 28 matched
buys: **on-chain taker fee = 0.5 × 10% × p × (1−p) × shares** — 28/28 exact to <0.5¢,
zero residual. Examples: Karachi 12.5sh@0.66 fee $0.1402; Milan 66.25sh@0.016 fee $0.0522.
- Our edge/Kelly math ALREADY contains this exact law (src/contracts/execution_price.py
  with_taker_fee: 0.05×p(1−p), fee_deducted enforced at Kelly boundary) — pricing is sound.
- venue_commands/CLOB-ack record the PRE-FEE matched amount; the chain debits POST-FEE cash.
  OPEN ITEM (agent): verify PnL/collateral booking uses post-fee cost — if it books the
  CLOB-ack amount, realized PnL is overstated by the fee on every taker fill.
- Strategic corollary for K4.0: maker orders pay ZERO taker fee → REST-THEN-CROSS saves
  spread (≈4% notional measured) PLUS fee (1.0-2.5% notional at our price band).

## Finding 2 — All 19 REDEEM_REVIEW_REQUIRED = on-chain $0 payouts (chain truth)
Every REVIEW_REQUIRED redeem appears in the sheet as Redeem usdcAmount=0.0: the tx executed
but transferred nothing — proceeds had already been collected by the operator's manual
redeems (shared wallet). Resolution: all 19 annotated in error_payload with
chain_truth_resolution=EXTERNALLY_REDEEMED_ZERO_PAYOUT (terminal state untouched —
REVIEW_REQUIRED ∈ _TERMINAL_STATES, no scheduler reachable). Formal EXTERNALLY_REDEEMED
enum lands with K3.6.b. The $19 redeem (231c365b, tx 0xd4780c8c) confirmed at $19 ✓ and its
proceeds re-deposited by operator 170s later — the live demonstration of the
Confirm-pending-deposit flow Zeus must own.

## Finding 3 — Pre-June purge EXECUTED (operator-ordered)
All 20 LOCAL-ONLY hashed rows were May (05-17..05-27) canary-era. Purged from
state/zeus_trades.db in one transaction (append-only triggers dropped and recreated
in-transaction; verified restored):
venue_commands=137, venue_order_facts=262, venue_command_events=607, venue_trade_facts=101,
trade_decisions=1539, settlement_commands=2, settlement_command_events=29.
Backup: /tmp/zeus_trades_backup_pre_purge_20260610.db (full online backup, pre-purge).
NOTE: the May resting-fact dataset behind the K4.0 KM curve lives in this backup now —
the curve evidence (docs/evidence/maker_taker/) is unaffected; future hazard recalibration
uses NEW resting facts.

## Finding 4 — SHEET-ONLY (51) = operator manual activity, absorption set
06-08 London 16°C YES accumulation (~$600 notional, many fills) + manual sells/redeems +
deposits ($149.50, $149.52, $379.88, $857.48, $5, $19) + a few non-weather strays
(AI-model/SpaceX markets). No Zeus action needed beyond the operator-activity absorption
family; these must never raise ghost/drift findings.

## Finding 5 — LA float-dust + stuck SUBMITTING (live defect, owned by task #28)
LA June-11 72-73°F NO: 22:41 size 8.7 REJECTED (maker amount 8.7×0.7=6.0899999… fails
venue amount grid), 22:50 VERBATIM RETRY same amounts REJECTED again, 22:54 size 8.5
broadcast and FILLED on-chain 22:55:13 ($6.039250000000001 — dust visible in chain history)
but local state stuck SUBMITTING 8+ min (ack/reconcile gap). Three sub-defects for #28:
amount-grid quantization at order build; rejection-class no-verbatim-retry; submit-ack
timeout must hand off to reconcile sweep promptly.
