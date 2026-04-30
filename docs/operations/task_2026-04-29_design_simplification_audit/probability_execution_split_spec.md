# Probability And Executable Price Split Spec

Status: critic-approved implementation sequencing spec.
Date: 2026-04-30.
Scope: Zeus pricing, edge, FDR, execution, monitor/exit, persistence, and
reporting semantics after the native multi-bin `buy_no` investigation.

This document does not authorize live deploy, production DB mutation, config
flips, schema migration, source-routing changes, or strategy promotion by
itself. Source edits still require topology admission, scoped AGENTS reads,
planning-lock evidence where required, focused tests, and closeout evidence.

## Problem

Zeus currently lets one overloaded object/field family represent different
mathematical quantities:

- `p_market`: sometimes market-implied probability prior, sometimes token quote
  price, sometimes a sparse monitor price vector;
- `entry_price`: sometimes executable cost, sometimes the same scalar as
  `p_market`;
- `vwmp`: sometimes a quote observation, sometimes an edge/Kelly authority;
- `BinEdge` and `EdgeContext`: transport posterior, quote, edge, Kelly context,
  and later executable-snapshot mutations as one bundle.

This is mathematically wrong and live-trading unsafe. The multi-bin `buy_no`
work exposed the issue because `P_no = 1 - P_yes` is valid for payoff
probability but `NO_entry_price = 1 - YES_VWMP` is not an executable Polymarket
NO-token price. The same abstraction error also affects `buy_yes`, monitor and
exit logic, backtests, and reports.

## First Principles

### 1. Settlement Probability Plane

Settlement probability is a distribution over mutually exclusive weather bins.

- `P_cal_yes[i]`: calibrated model probability that bin `i` resolves YES.
- `P_posterior_yes[i]`: posterior probability that bin `i` resolves YES.
- `buy_yes` payoff probability: `P_posterior_yes[i]`.
- `buy_no` payoff probability for selected child/bin `i`:
  `1 - P_posterior_yes[i]`.

These values are beliefs about resolution. They are not executable prices.

### 2. Market-Prior Plane

API and orderbook prices are observations, not priors by default.

A market prior exists only as a named estimator with documented lineage:

- estimator version;
- family completeness;
- side convention;
- de-vig rule;
- freshness;
- liquidity and spread filters;
- source quote hashes;
- negative-risk and augmented-market policy;
- out-of-sample validation status.

Without a validated estimator, corrected live entry may use `model_only_v1`
plus executable quote economics. It must not silently treat token quotes as a
probability distribution.

### 3. Executable Quote And Cost Plane

Polymarket execution is token based. A live order is constrained by:

- selected `token_id`;
- order side;
- limit price;
- size;
- order type and time-in-force;
- tick size;
- minimum order size;
- fee;
- negative-risk metadata;
- book depth;
- quote freshness;
- market identity and tradability.

A token quote is not a settlement-bin probability distribution.

Relevant official documentation:

- Polymarket orderbook: https://docs.polymarket.com/trading/orderbook
- Polymarket orders: https://docs.polymarket.com/trading/orders/overview
- Polymarket fees: https://docs.polymarket.com/trading/fees
- Negative-risk markets: https://docs.polymarket.com/advanced/neg-risk

### 4. Trade Hypothesis Plane

A live trade hypothesis is:

```text
(bin, direction, selected token, payoff probability, order policy,
 executable cost basis, fee/tick/min-order/depth/snapshot lineage)
```

Economic edge is computed against executable cost under the declared order
policy, not against a raw probability prior.

## Immediate Policy Decisions

### No Silent Legacy Live Default

Current behavior is named `legacy_vwmp_prior_v0` and must be treated as:

```text
pricing_semantics_version = legacy_price_probability_conflated
```

First implementation must add an explicit operator flag such as:

```text
ALLOW_LEGACY_VWMP_PRIOR_LIVE = false
```

Default is fail-closed for live entry when corrected executable-cost semantics
are unavailable. Legacy live use requires explicit operator opt-in and cannot
serve as promotion-grade economics evidence.

### Order Policy Vocabulary

Near-term corrected policy:

```text
LIMIT_MAY_TAKE_CONSERVATIVE
```

Meaning:

- Zeus submits a bounded limit order.
- The order may rest or immediately match.
- Kelly uses final submitted limit plus worst-case taker fee.
- This sizes conditional on fill.
- It does not model fill probability, queue priority, maker rebate, or adverse
  selection.
- Fill quality, maker/taker realized status, partial fill, cancel remainder,
  and adverse selection are required telemetry before promotion.

The implementation must map this policy explicitly to venue behavior, for
example:

```text
LIMIT_MAY_TAKE_CONSERVATIVE -> GTC or GTD, post_only=false,
cancel policy=<mode timeout>
```

Do not allow risk or venue adapters to silently choose an incompatible order
type without recording that order policy in the cost basis.

Future policies are separate:

- `POST_ONLY_PASSIVE_LIMIT`: requires post-only support and rejects would-cross
  orders.
- `MARKETABLE_LIMIT_DEPTH_BOUND`: requires visible ask-depth cost curve,
  FAK/FOK/GTD semantics, max edge sacrifice, and explicit depth cap/reject.

### Live Economic FDR Scope

Keep these separate:

- research/model diagnostic FDR;
- live economic FDR.

Live economic FDR operates on all executable hypotheses in the same
candidate/snapshot family after the quote/cost snapshot is fixed. P-values are
conditional on the fixed quote/cost basis. Quote sensitivity is a separate
named robustness test and must not be mixed into model/bootstrap uncertainty.

The selected hypothesis identifier must include enough lineage to prevent
materialization drift:

```text
bin + direction + selected_token_id + snapshot_id/hash + cost_basis_id
```

### Executor Hardening Is An Early Blocker

No corrected mode can be live until executor accepts immutable final intent
fields and validates them against the selected executable snapshot and venue
submission envelope.

In corrected mode, executor must not derive a new limit from:

```text
p_posterior + edge.vwmp
```

It may reject an invalid final intent. It must not invent a new price.

## Implementation Plan

### Phase A: Safety Freeze And Tests First

Keep native multi-bin `buy_no` live disabled.

Add tests proving:

1. changing executable ask/depth changes entry cost/size but not posterior;
2. changing market-prior distribution changes posterior but not selected
   token/snapshot;
3. NO-token quote cannot be passed as a full-family prior component;
4. corrected executor mode rejects missing immutable final limit/cost basis;
5. corrected executor mode never recomputes from `p_posterior` or `vwmp`;
6. reporting/backtest cannot mix legacy and corrected
   `pricing_semantics_version` economics cohorts.

Add a live-entry gate that rejects legacy conflated semantics unless explicit
operator opt-in is present. Default false.

### Phase B: Minimal Contracts, Reusing Existing System

Add one prior contract:

```text
MarketPriorDistribution
```

Required fields:

- `values`;
- `estimator_version`;
- `source_quote_hashes`;
- `family_complete`;
- `vig_treatment`;
- `freshness_status`;
- `neg_risk_policy`;
- `validated_for_live`.

Add one execution-cost contract:

```text
ExecutableCostBasis
```

It is derived from `ExecutableMarketSnapshotV2` plus order policy and includes:

- `selected_token_id`;
- `selected_outcome_label`;
- `order_policy`;
- `final_limit_price`;
- `worst_case_fee_rate`;
- `fee_source`;
- `fee_adjusted_execution_price`;
- `tick_status`;
- `min_order_status`;
- `depth_status`;
- `quote_snapshot_id`;
- `quote_snapshot_hash`.

Reuse existing contracts:

- `ExecutionPrice`;
- `ExecutionIntent`;
- `ExecutableMarketSnapshotV2`;
- `VenueSubmissionEnvelope`.

Do not create a parallel venue model.

`BinEdge` may carry optional transition fields or sidecar metadata, but it must
not become the permanent semantic authority.

### Phase C: Posterior Fusion Split

`compute_posterior()` must accept:

```text
MarketPriorDistribution | None
```

It must not accept raw executable quote/VWMP floats.

Allowed modes:

- `model_only_v1`: corrected baseline; posterior derives from calibrated model
  and tail treatment only.
- `legacy_vwmp_prior_v0`: explicitly labeled legacy; live use requires
  operator opt-in and cannot serve as promotion evidence.
- `yes_family_devig_v1`: shadow-only at first; requires complete YES family,
  freshness, de-vig convention, liquidity filters, and no unsupported
  augmented placeholder or negative-risk coupling.

Promotion of any market-prior estimator requires out-of-sample Brier and ROI
evidence.

### Phase D: Live Economic Hypothesis And FDR Rebuild

Build executable hypotheses only when the selected side/token has valid
executable identity and snapshot lineage.

For `LIMIT_MAY_TAKE_CONSERVATIVE`:

- edge statistic uses final submitted limit plus worst-case taker fee;
- depth is a sanity/liquidity filter, not an immediate-fill guarantee;
- Kelly is conditional-on-fill.

For a future marketable policy:

- edge statistic uses depth-weighted ask cost for intended size;
- visible depth can cap or reject size.

Every FDR-selected row must materialize the same executable hypothesis.

### Phase E: Reprice And Submit Contract

Runtime selected-token snapshot is final pricing authority.

Before executor call, compute:

- final limit;
- fee-adjusted `ExecutionPrice`;
- Kelly size;
- tick alignment;
- min-order status;
- depth cap/reject;
- risk caps;
- family caps;
- pricing lineage.

Executor corrected path validates immutable final fields against snapshot and
envelope, then submits or rejects.

### Phase F: Monitor And Exit Symmetry

Active positions must carry or derive:

- held token;
- `pricing_semantics_version`;
- entry cost basis;
- quote snapshot lineage where available.

Monitor computes posterior distribution separately from held-side current quote.

Exit EV compares held-side sell executable value under policy against hold
value. No `p_market` vector fallback may masquerade as a sell quote.

This phase is not optional. Corrected entry with legacy exit fallback remains
unsafe.

### Phase G: Persistence And Reporting Staged Cut

Use additive fields only:

- `pricing_semantics_version`;
- `market_prior_version`;
- `entry_cost_source`;
- `quote_snapshot_id`;
- `quote_snapshot_hash`;
- `legacy_price_probability_conflated`.

For new probability rows, add market-prior estimator lineage to
`probability_trace_fact`.

Pass pricing version through envelope metadata when available.

Do not backfill old rows as corrected. Historical depth is assumed
unreconstructable unless an executable snapshot row with depth and hash exists.

Backtests without point-in-time depth/snapshot are excluded from
corrected-executable economics or run as model-only/research diagnostics.

Reporting must hard-fail or segregate mixed `pricing_semantics_version`
economics cohorts. Warning-only behavior is not enough.

## Promotion Gates

Corrected semantics ship shadow-only first.

Live corrected entry requires:

- Phase A invariant tests;
- executor no-recompute path;
- explicit order policy mapping;
- snapshot/envelope identity;
- quote staleness thresholds;
- depth/liquidity thresholds;
- pricing/reporting segregation;
- explicit operator flag;
- live safety cap.

Strategy promotion additionally requires:

- realized fill quality;
- maker/taker realized status;
- partial-fill and cancel-remainder accounting;
- adverse-selection telemetry;
- out-of-sample evidence;
- economics evidence, not only model-skill evidence.

Model-only skill evidence may block promotion. It cannot prove live economics
alone.

## Residual Risks Not Solved In First Packet

- Whether any market-prior estimator improves out-of-sample Brier or ROI.
- Negative-risk conversion/arbitrage estimator for event-coupled markets.
- Queue priority, fill probability, and adverse-selection model for resting
  limit orders.
- Corrected historical economics when executable depth snapshots are missing.

## Discovery Flow And Review Methodology

The `buy_no` investigation and the probability/executable-price split are now
canonical examples of hidden code-review killers in Zeus. They were not found
by inspecting one local guard. They were found by forcing every quantity to
answer: what real-world object is this, and what downstream layer is allowed to
use it as?

### How The `buy_no` Breakpoint Was Found

The investigation started from the product contract, not from executor code:

1. `buy_no` pays when the selected child market resolves false.
2. Therefore `P_no_i = 1 - P_yes_i` is valid for payoff probability.
3. That complement says nothing about executable entry price.
4. Polymarket execution routes orders to a selected token orderbook.
5. Multi-bin `NO_i` must therefore use the selected child market's native NO
   token quote, not `1 - YES_VWMP`.

The first pass then traced the money path:

```text
contract semantics -> probability -> edge/FDR -> Kelly -> intent -> snapshot
-> venue envelope -> order submit -> monitor/exit -> settlement/learning
```

That trace found the original local breakpoint:

- evaluator built `p_market` only from YES token books;
- `MarketAnalysis.supports_buy_no_edges()` intentionally fail-closed
  multi-bin NO because it had no native NO executable quote;
- full-family FDR used the same predicate;
- executor and executable snapshot routing were already capable of selecting
  `no_token_id`.

The important move was not removing the guard. The important move was asking
why the guard was correct: upstream did not possess an executable NO price.

### How The Deeper Probability/Price Error Was Found

After native NO quote support existed, the review asked whether the fix solved
only `buy_no` or exposed a general type error. The decisive observation was
that the same scalar traveled under multiple meanings:

```text
p_market -> posterior prior
p_market -> edge denominator
p_market -> entry_price
p_market -> vwmp
entry_price/vwmp -> Kelly cost
edge.vwmp -> executor limit derivation
snapshot VWMP -> rewritten BinEdge fields
```

That is a semantic aliasing bug. The code may be locally type-correct while the
real-world units are wrong.

The review then cross-checked against official venue semantics:

- Polymarket orderbooks are token-level quote surfaces;
- orders submit token, side, price, size, and order type;
- tick size, min order size, fee, negative risk, and depth constrain execution;
- multi-outcome and negative-risk markets have event-level coupling.

This falsified the hidden assumption that a token quote can be both an
executable cost and a settlement-bin probability distribution.

### Methodology For Future Code Reviews

Use this sequence for every high-risk trading review. It is designed to catch
bugs that ordinary line-by-line review misses.

#### 1. Name The Physical Quantity

For every field crossing a module boundary, write the sentence:

```text
This value is a <real-world object> measured at <time> in <unit> and may be
used by <downstream layer> as <specific authority>.
```

If the sentence changes by call site, the field is overloaded and must be
split, renamed, or wrapped in a contract.

High-risk names in Zeus:

- `p_market`;
- `price`;
- `entry_price`;
- `vwmp`;
- `p_posterior`;
- `edge`;
- `size_usd`;
- `limit_price`;
- `token_id`;
- `snapshot_id`;
- `current_price`.

#### 2. Separate Belief, Quote, Cost, And Order Lifecycle

Every trading path must keep four planes distinct:

```text
belief:      resolution probability
prior:       estimator built from market observations
quote/cost:  executable token-side price under an order policy
lifecycle:   order placement, fill, cancel, settlement, learning
```

A code review should fail any path where one scalar is used across two planes
without an explicit conversion function and provenance.

#### 3. Trace Both Directions

Trace forward from source to submit:

```text
forecast -> probability -> posterior -> edge -> size -> intent -> submit
```

Then trace backward from venue API to model:

```text
submitted token/price/size -> executable snapshot -> cost basis -> edge
-> payoff probability -> posterior construction
```

The hidden killers are usually found where the two traces disagree.

For `buy_no`, the backward trace said "submitted token is NO token"; the
forward trace had "market price came from YES token or complement." That
contradiction exposed the bug.

#### 4. Force A Counterfactual Test

Ask two counterfactuals for every pricing-related change:

1. If the executable quote changes but the model distribution does not, what
   code changes?
2. If the market-prior estimator changes but the selected token quote does not,
   what code changes?

Correct design:

- quote changes affect cost, size, limit, and execution evidence;
- prior changes affect posterior and model diagnostics;
- neither silently changes the other.

If both counterfactuals mutate the same field, the design is suspect.

#### 5. Check The API Reality Before Trusting Local Abstractions

Local abstractions are not authority when they model a venue. For every venue
path, review against the actual API object:

- token discovery;
- orderbook shape;
- bid/ask sorting and depth;
- tick and min-order;
- fee basis;
- negative risk;
- order type;
- submitted payload;
- fill lifecycle.

The review question is:

```text
Does the local object preserve every API fact needed to prove this order is the
same economic object we sized?
```

If not, the code may pass tests while submitting a different trade.

#### 6. Search For Semantic Rewrites

Search not only for where a field is assigned, but where it is reassigned after
new evidence appears. Snapshot/reprice paths are especially dangerous.

Patterns to grep:

```text
p_market
entry_price
vwmp
p_posterior
forward_edge
edge_context_json
limit_price
best_ask
best_bid
token_id
no_token_id
selected_outcome_token_id
```

Any mutation after initial decision must either:

- create a new versioned semantic object; or
- prove it is updating only the same plane with fresher evidence.

Writing executable snapshot VWMP back into a legacy `BinEdge.vwmp` field is a
transition shim, not a durable contract.

#### 7. Treat Tests As Unit-Meaning Assertions

The strongest tests are not value snapshots. They assert that changing one
real-world plane cannot mutate another:

- changing ask/depth changes cost and size, not posterior;
- changing prior changes posterior, not selected token/snapshot;
- changing token side changes execution route, not payoff distribution;
- changing order policy changes cost basis, not model belief;
- changing reporting cohort version changes aggregation eligibility, not PnL
  arithmetic.

These tests catch hidden semantic coupling before it becomes a live-money
failure.

#### 8. Use Subagents For Breadth, But Keep Judgment In Main Thread

Use subagents to map broad surfaces:

- core math and field flow;
- venue/API semantics;
- persistence/reporting/backtest lineage;
- monitor/exit symmetry;
- critic review for overbuild and real-world mismatch.

Do not outsource the final judgment. The main thread must personally hold the
money path and decide whether the real-world quantities are still aligned.

#### 9. Require A Critic To Attack Overbuild And Reality Fit

Every major semantic repair should survive two questions:

1. Is this mathematically necessary, or only cleaner naming?
2. Does this match how the venue actually trades?

For this spec, the first two critic passes forced important corrections:

- API quote is not a prior by default;
- execution cost is a policy-specific cost basis, not just a scalar;
- passive vs may-take limit policy must be explicit;
- Kelly at submitted limit is conditional-on-fill;
- corrected mode must harden executor before live use.

The final critic approval matters because it approved sequencing, not live
deployment.

### Review Smells That Should Trigger This Methodology

Trigger the full methodology when a diff contains any of these:

- one field named `price`, `p_market`, or `vwmp` crossing strategy and execution;
- complement math used near execution price;
- token routing changes;
- FDR family changes;
- Kelly size changes;
- executable snapshot or envelope changes;
- monitor/exit quote fallback changes;
- reporting/backtest economics cohort changes;
- feature flags that change money semantics;
- claims that a live path is "supported" because a downstream executor can route
  a token.

The central lesson from `buy_no` is that downstream execution capability does
not prove upstream economic evidence exists. A correct review must prove the
entire causal chain from settlement semantics to submitted order.

## Critic Verdict

Third-pass critic verdict: APPROVE for implementation sequencing.

The approved strongest guardrails are:

1. order policy must map explicitly to venue order type, post-only status, and
   cancel policy;
2. corrected-mode authority is `ExecutableCostBasis`, not
   `BinEdge.entry_price` or `BinEdge.vwmp`;
3. FDR hypothesis identity must include side, token, snapshot, and cost-basis
   lineage;
4. exit symmetry is required before corrected live promotion;
5. historical economics must segregate or hard-fail mixed pricing semantics.

Live promotion remains blocked until the phased gates above pass.
