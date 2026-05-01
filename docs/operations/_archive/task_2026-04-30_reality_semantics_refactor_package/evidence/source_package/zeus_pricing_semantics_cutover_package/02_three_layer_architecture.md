# 02 — Three-Layer Physical Isolation Architecture

## Core rule

Zeus must implement physical isolation between:

1. Epistemic Layer — pure belief.
2. Microstructure Layer — cold venue/orderbook reality.
3. Execution & Risk Layer — economic trade decision, Kelly, FDR, executor, monitor/exit.

A raw scalar must never travel between layers without a typed contract declaring what it is.

---

# 1. Epistemic Layer — pure belief

## Purpose

This layer answers only:

> What is the probability each weather bin resolves YES?

It does not know Polymarket exists. It does not know bid/ask. It does not know fees. It does not know tick size. It does not know token ids. It does not know Kelly.

## Inputs

```text
forecast ensemble facts
settlement semantics
source truth
calibration model
optional MarketPriorDistribution
```

## Internal quantities

```text
P_raw              # Monte Carlo pure physical probability
P_cal              # Platt-calibrated probability
MarketPrior        # named long-run market prior estimator, if validated
P_posterior_yes    # fused final posterior belief
```

## Allowed outputs

```python
PosteriorBelief:
    distribution_yes: tuple[Probability, ...]
    bins: tuple[BinId, ...]
    posterior_mode: Literal[
        "model_only_v1",
        "legacy_vwmp_prior_v0",
        "yes_family_devig_v1_shadow",
        "yes_family_devig_v1_live"
    ]
    market_prior_id: str | None
    calibration_trace_id: str
    source_truth_trace_id: str
```

## Import fence

`src/epistemic/**` or the equivalent refactored zone must not import:

```text
polymarket
clob
orderbook
bid
ask
token_id
fee
tick
min_order
execution
kelly
venue
snapshot_repo
```

## Forbidden

```python
# forbidden
p_market = best_ask
p_posterior = alpha * p_cal + (1-alpha) * raw_token_quote
entry_price = p_market
```

## Correct use

```python
payoff_probability = posterior.distribution_yes[i]
# This is still belief, not money.
```

---

# 2. Microstructure Layer — cold venue reality

## Purpose

This layer answers only:

> What can be bought or sold on the venue right now, under the orderbook, tick, min-order, fee, negative-risk, and freshness constraints?

It does not know weather. It does not know bins. It does not know posterior probability. It does not compute edge. It does not run Kelly.

## Inputs

```text
ExecutableMarketSnapshotV2
Polymarket L2 orderbook
market metadata
fee metadata
negative-risk metadata
tick/min-order facts
quote freshness thresholds
```

## Internal quantities

```text
Best_Ask_Yes
Best_Bid_Yes
Best_Ask_No
Best_Bid_No
Order_Book_Depth
Fee_Tier / Protocol_Fee
Tick_Size
Min_Order_Size
Orderbook_Hash
```

## Allowed outputs

```python
ExecutableCostCurve:
    token_id: str
    side: Literal["BUY", "SELL"]
    orderbook_hash: str
    timestamp: datetime
    levels: tuple[PriceLevel, ...]
    fee_rate: Decimal
    fee_formula: str
    tick_size: Decimal
    min_order_size: Decimal
    neg_risk: bool

ExecutableCostBasis:
    selected_token_id: str
    selected_outcome_label: Literal["YES", "NO"]
    direction: Literal["BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"]
    order_policy: OrderPolicy
    requested_size: SizeSpec
    final_limit_price: Decimal
    expected_fill_price_before_fee: Decimal
    fee_adjusted_execution_price: Decimal
    sweep_result: ClobSweepResult
    snapshot_id: str
    snapshot_hash: str
    cost_basis_hash: str
```

## Import fence

`src/microstructure/**` must not import:

```text
weather
forecast
settlement_semantics
calibration
platt
posterior
fdr
kelly
strategy families
```

It may import contracts and venue/snapshot clients.

## CLOB sweep function

The real cost function must be a curve, not just a scalar:

```python
def simulate_clob_sweep(
    snapshot: ExecutableMarketSnapshotV2,
    direction: OrderDirection,
    target: SizeSpec,
    fee_rate: Decimal,
) -> ClobSweepResult:
    ...
```

### BUY semantics

For a BUY order, sweep asks from lowest to highest price.

For each level:

```text
level_cash = fill_shares * level_price
level_fee  = fill_shares * fee_rate * level_price * (1 - level_price)
```

All-in per-share cost:

```text
(total_cash + total_fee) / total_filled_shares
```

### SELL semantics

For a SELL order, sweep bids from highest to lowest price.

For each level:

```text
level_proceeds = fill_shares * level_price
level_fee      = fill_shares * fee_rate * level_price * (1 - level_price)
```

All-in realized sell value per share:

```text
(total_proceeds - total_fee) / total_sold_shares
```

## Important fee correction

A hardcoded `fee_rate=0.02` may be a test fixture, but it is not a production authority. Production must read `feesEnabled` and market-specific fee parameters. Polymarket fees are applied at match time and are price-dependent, not a flat fee.

## Depth status

Depth is not one thing. It must be named:

```text
TOP_OF_BOOK_ONLY
VISIBLE_DEPTH_SUFFICIENT
DEPTH_INSUFFICIENT
DEPTH_UNKNOWN
STALE_BOOK
```

---

# 3. Execution & Risk Layer

## Purpose

This layer combines belief and executable cost to decide whether a trade has positive economic edge and whether risk allows it.

It is the only layer allowed to know both:

```text
P_posterior_yes
ExecutableCostBasis
```

## Core formula

```python
payoff_probability = (
    P_posterior_yes[i]
    if side == "BUY_YES"
    else 1 - P_posterior_yes[i]
)

fee_adjusted_execution_price = simulate_clob_sweep(
    snapshot=ExecutableMarketSnapshotV2,
    direction=side,
    target_size=trade_size,
    fee_rate=market_fee_rate,
).all_in_price

live_economic_edge = payoff_probability - fee_adjusted_execution_price

if live_economic_edge <= 0:
    reject_before_kelly()
else:
    enter_kelly_with_cost_basis()
```

## Size-dependent cost caveat

The user-supplied formula is correct in spirit but has a hidden circularity: `fee_adjusted_execution_price` depends on `trade_size`, while Kelly often determines `trade_size`.

The implementation must solve this with one of these policies:

### Policy A — conservative scalar MVP

1. Compute preliminary cost at minimum order or a configured probe size.
2. If edge <= 0, reject.
3. Run Kelly using the conservative top-of-book/worst-case taker cost.
4. Re-run sweep at Kelly size.
5. If edge remains positive and cost drift is within budget, accept; otherwise shrink or reject.

### Policy B — correct cost-curve Kelly

1. Microstructure returns `ExecutableCostCurve`.
2. Kelly optimizer chooses size over the cost curve.
3. Edge positivity is tested at the chosen size.

Phase 1 should use Policy A with strict reject/shrink. Phase M can upgrade to Policy B.

## Live economic edge

```text
live_economic_edge(q) = payoff_probability - all_in_cost(q)
```

The edge is a function of size when sweeping depth.

## Entry into Kelly

Kelly may receive only:

```text
payoff_probability
ExecutableCostBasis or ExecutableCostCurve
risk caps
bankroll/collateral state
FDR-selected hypothesis id
```

Kelly may not receive:

```text
raw p_market
raw vwmp
raw midpoint
raw last trade
BinEdge.entry_price
```

## FinalExecutionIntent

Executor receives only immutable final intent:

```python
FinalExecutionIntent:
    hypothesis_id: str
    selected_token_id: str
    direction: Direction
    final_limit_price: Decimal
    size: SizeSpec
    order_policy: OrderPolicy
    order_type: str
    time_in_force: str
    post_only: bool
    cancel_after: datetime | None
    snapshot_id: str
    snapshot_hash: str
    cost_basis_id: str
    cost_basis_hash: str
    max_slippage_bps: int
    tick_size: Decimal
    min_order_size: Decimal
    fee_rate: Decimal
    neg_risk: bool
```

Executor can submit or reject. It cannot invent a new price.
