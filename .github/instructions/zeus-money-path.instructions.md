---
applyTo: "src/engine/**/*.py,src/contracts/**/*.py,src/data/market_scanner.py,src/strategy/**/*.py"
---

# Zeus money-path — economic object semantics

These paths define the money-path core: contract semantics, market
selection, probability → edge pipeline, and strategy routing.

## Price semantics

`p_raw` → calibration → `p_cal` → fusion → `p_posterior` → minus costs
→ `edge`. Each step must be explicit; skipping calibration produces an
uncalibrated posterior masquerading as edge.

`display_price` and `market_price` / `current_price` are ambiguous legacy
aliases. Reject any new use that does not declare its role
(executable bid/ask vs display-only). Ambiguous aliases flowing into
`final_limit_price` or `edge` computation are Critical.

Executable prices: `orderbook_top_ask` is the BUY cost; `orderbook_top_bid`
is the SELL price. Reversing them submits limit orders on the wrong side.

## Probability provenance

`p_raw` is forbidden from `src/contracts/**`, `src/strategy/**`, and
`src/engine/evaluator.py` output paths. Only `p_cal`, `p_posterior`,
or `edge` may enter sizing or limit-price computation.

Flag any crossing: `p_raw` into `edge`, `p_market` into posterior
without explicit prior tag, model probability into executable cost
without spread/fee deduction (INV-21, INV-33, INV-34, INV-35).

## Strategy key

`strategy_key` is the sole governance identity (INV-04). Check:
- Not derived from market_id or condition_id at runtime.
- Stored unmodified in decision_events, no_trade_events.
- Not a computed string that changes across schema versions.
- Any new dimension (market type, tier, family) added to key must go
  through architecture review (planning-lock on src/contracts/**).

## Market identity

`condition_id` and `token_id` must never be conflated. `market_id`
is an ambiguous Gamma alias — any new use must declare its surface
(condition_id or Gamma market). YES/NO outcome_label is always a
literal string constant, never derived from an index.

## Evaluator invariants

`evaluator.py` produces decisions and no-trade events. Both must be
written atomically to DB before any derived JSON export (INV-17).
The source field distinguishes live_decision / shadow_decision /
phase0_backfill — this must not be inferred from context; it must be
passed explicitly so evidence queries can scope correctly.
