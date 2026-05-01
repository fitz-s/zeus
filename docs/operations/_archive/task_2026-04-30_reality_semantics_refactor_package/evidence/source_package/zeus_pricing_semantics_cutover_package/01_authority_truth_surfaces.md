# 01 — Authority Order and Truth Surfaces

## Authority order

Do not create a new highest authority. Modify the existing Zeus authority surfaces so they stop contradicting live CLOB reality.

1. **External venue facts and live CLOB behavior**
   - Polymarket orderbook is token-level reality: bids, asks, sizes, tick size, minimum order, negative-risk flag, hash, timestamp.
   - Buy token price is ask; sell token price is bid.
   - Midpoint / displayed implied probability is not executable cost.
   - Fees are match-time protocol facts, not feelings and not optional decorations.

2. **Zeus K0/K1 law surfaces**
   - Root `AGENTS.md` money path.
   - `architecture/invariants.yaml`.
   - `architecture/negative_constraints.yaml`.
   - Scoped `AGENTS.md` under `src/strategy`, `src/execution`, `src/state`, `docs/reference`.

3. **Active current-fact surfaces**
   - `docs/operations/current_state.md`.
   - `docs/operations/known_gaps.md`.
   - `docs/operations/current_source_validity.md`.
   - `docs/operations/current_data_state.md`.

4. **Executable source and canonical DB/event truth**
   - Source code defines current behavior.
   - Canonical DB/event facts outrank reports, CSV, status JSON, notebooks.

5. **Uploaded split spec**
   - Critic-approved sequencing spec.
   - Not deploy authorization.
   - Not production DB mutation authorization.
   - Not source-routing authorization.
   - Not strategy-promotion authorization.

6. **Reference docs and reports**
   - Useful only after harmonization.
   - Old `P_market` formulas must be explicitly superseded for live economic trading.

## Truth surfaces to physically separate

| Surface | Definition | Allowed consumers | Forbidden misuse |
|---|---|---|---|
| Settlement probability | Belief over weather bins resolving YES | posterior, payoff probability, model diagnostics | token price, entry cost, sell quote |
| Market prior | Named estimator with lineage, de-vig, freshness, family completeness, validation | posterior fusion | raw VWMP, midpoint, sparse monitor quote vector |
| Microstructure quote | Token orderbook facts: bid/ask/depth/tick/min-order/hash/fee/neg-risk | cost basis simulation, liquidity gates | posterior probability, market prior |
| Executable cost | Snapshot + order policy + sweep/depth + fee + tick/min-order + freshness | edge, Kelly, final intent, reporting | model belief |
| FDR hypothesis | Fixed executable hypothesis family after cost basis is frozen | selection | late repriced or rematerialized rows |
| Executor truth | Immutable final intent + venue envelope validation | command submission or rejection | new price invention |
| Monitor/exit truth | Held-token sell executable value vs hold value | exit decision | `p_market` fallback vector |
| Reporting truth | Semantics-versioned economic cohorts | diagnostics, promotion reports | legacy/corrected aggregation |

## Authority changes required

### Root `AGENTS.md`

Replace old money path:

```text
contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning
```

with live-money path:

```text
contract semantics
-> source truth
-> forecast signal
-> calibrated settlement distribution
-> optional named market-prior distribution
-> posterior settlement distribution
-> token-level executable snapshot
-> executable cost basis
-> full executable hypothesis family
-> live economic FDR
-> cost-basis Kelly sizing
-> immutable final execution intent
-> venue command/submission envelope
-> fill facts
-> monitor/exit executable sell quote
-> settlement
-> learning
```

Add rule:

```text
No raw price-like scalar may cross a live-money boundary without semantic type,
origin, snapshot lineage, order policy, and pricing_semantics_version.
```

### `architecture/invariants.yaml`

Add:

```yaml
- id: INV-33
  statement: Probability, market-prior, microstructure quote, and executable cost are distinct authority planes.

- id: INV-34
  statement: Posterior fusion may consume only calibrated probabilities and named MarketPriorDistribution, never raw quotes.

- id: INV-35
  statement: Live Kelly sizing may consume only fee/depth/tick/min-order-aware ExecutableCostBasis.

- id: INV-36
  statement: Live economic FDR hypothesis identity must include bin, direction, token, snapshot, cost basis, and order policy.

- id: INV-37
  statement: Corrected executor validates immutable final intent and must not recompute price.

- id: INV-38
  statement: Corrected live entry is not promotable unless monitor/exit uses held-token sell executable quote semantics.
```

### `architecture/negative_constraints.yaml`

Add:

```yaml
- id: NC-20
  statement: Raw quote, VWMP, midpoint, last trade, or sparse monitor vector may not be passed as MarketPriorDistribution.

- id: NC-21
  statement: Kelly may not consume BinEdge.entry_price, BinEdge.vwmp, or p_market unless wrapped by ExecutableCostBasis.

- id: NC-22
  statement: Any snapshot/cost-basis change after FDR invalidates the selected live economic hypothesis.

- id: NC-23
  statement: Corrected executor may reject invalid final intent but must not invent, jump, or recompute a limit price.

- id: NC-24
  statement: Promotion-grade economics must hard-fail or segregate mixed pricing_semantics_version cohorts.

- id: NC-25
  statement: Certified live path may not use legacy venue envelope identities or collapsed YES/NO token ids.
```

## Known current conflicts to fix

- Current Zeus root money path goes from posterior to edge/execution without explicit executable cost basis.
- `docs/reference/zeus_math_spec.md` still treats `P_market` as a market-implied probability in formulas for posterior and edge.
- `src/strategy/market_fusion.py` still uses `compute_posterior(p_cal, p_market, alpha)`.
- `src/engine/cycle_runtime.py` has late executable snapshot repricing that mutates selected decision economics.
- `src/execution/executor.py` still describes dynamic limit behavior and may derive limit from posterior/VWMP inputs.
- `src/engine/monitor_refresh.py` can still use held-token quote-like information as a probability vector.

## Interpretation rule

When the repo and Polymarket disagree about executable behavior, Polymarket CLOB reality wins. When the uploaded spec and Zeus authority disagree, do not silently override; modify existing Zeus authority surfaces and attach tests.
