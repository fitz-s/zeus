# src/execution AGENTS — Zone K2 (Execution)

Module book: `docs/reference/modules/execution.md`
Machine registry: `architecture/module_manifest.yaml`

## WHY this zone matters

Execution translates sized trading decisions into actual orders on Polymarket's CLOB. This is the live-money boundary — every dollar of P&L flows through this layer.

Critical invariant: **exit is not local close** (INV-01). A monitor decision produces `EXIT_INTENT` as a lifecycle event. The execution layer then handles the mechanics of selling. Settlement is yet another separate lifecycle event (INV-02).

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `executor.py` | Limit-order execution engine (live) | CRITICAL — real money flows here |
| `exit_triggers.py` | 8-layer churn defense for exit decisions | HIGH — prevents false exits |
| `exit_lifecycle.py` | Exit lifecycle management | HIGH — state transitions |
| `fill_tracker.py` | Order fill tracking and timeout | MEDIUM |
| `collateral.py` | Compatibility sell-collateral facade over CollateralLedger | HIGH — fail-closed live exits |
| `wrap_unwrap_commands.py` | Durable USDC.e↔pUSD command state | HIGH — no live chain side effects in Z4 |
| `settlement_commands.py` | Durable settlement/redeem command ledger | HIGH — Q-FX-1 gated, crash-recoverable tx hash anchor |
| `harvester.py` | Settlement harvesting | MEDIUM |

## Domain rules

- **Limit orders ONLY** — never market orders. Zeus always provides liquidity on entry
- Live Polymarket V2 placement must route through `src/venue/polymarket_v2_adapter.py` and preserve venue-command pre-side-effect persistence.
- Mode-based timeouts: Opening Hunt 4h, Update Reaction 1h, Day0 15min
- Share quantization: BUY rounds UP, SELL rounds DOWN (0.01 increments)
- Whale toxicity: cancel on adjacent bin sweeps (legacy predecessor lesson)
- Dynamic limit: if within 5% of best ask, jump to ask for guaranteed fill
- Live execution, backtest, shadow, simulation, and test doubles are separate evidence classes; execution code must not route live orders through non-live evidence or test-double paths.
- All probabilities in exit triggers are in NATIVE space of position direction (buy_yes→P(YES), buy_no→P(NO))
- Settlement redemption side effects flow through `settlement_commands.py`; do not call adapter redeem paths directly from harvester or collateral code.

## Common mistakes

- Flipping probability direction for buy_no positions in exit triggers → false exits (legacy-predecessor incident: 7/8 positions force-exited in 30–90min)
- Bypassing the 8-layer churn defense "for speed" → cascade of false exits
- Using market orders instead of limits → guaranteed adverse fill
- Treating exit as local close (modifying lifecycle directly) → violates INV-01
- Not recording execution facts → learning pipeline has no fill data

## Settlement Truth Writes (harvester.py)

`_write_settlement_truth()` writes `winning_bin` and `settled_at` to `settlements` table
after Gamma API confirms a market settled. Uses `INSERT OR REPLACE` (SQLite DELETE+INSERT idempotency).
`settlement_source_type` mapped from config lowercase to DB uppercase via `_SOURCE_TYPE_MAP`.
