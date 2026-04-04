# Zeus discrete settlement support amendment

Status: `ACCEPTED`
Classification: `P0-class foundation amendment`
Authority intent: elevate market contract settlement support from implementation detail to explicit architecture authority.

## Why this amendment exists

Zeus currently carries critical market-contract truths in runtime types and scattered docs, but not yet as a top-level authority surface. This allows mathematically coherent packets and reviews to begin from an incorrect world model.

The immediate failure mode already observed is incorrect reasoning about finite bin width and settlement support geometry. That is not a local bug. It is a foundation-authority gap.

## Amendment

### 1. Discrete settlement support is authority

Discrete settlement support is a semantic atom, not an implementation detail.

Any packet touching:
- uncertainty
- calibration
- hit-rate analysis
- edge math
- pricing
- settlement interpretation

must treat market settlement support as authority before reasoning from continuous physical intuition.

### 2. Required semantic concepts

The following concepts are now mandatory architecture concepts:

1. `bin_contract_kind`
   - `point`
   - `finite_range`
   - `open_shoulder`

2. `bin_settlement_cardinality`
   - number of discrete settled values that resolve the bin to YES

3. `settlement_support_geometry`
   - the exact discrete support implied by the venue contract

### 3. Current Zeus market law

For the currently traded weather contracts in Zeus:

1. Fahrenheit non-shoulder bins are finite-range bins with settlement cardinality `2`
   - example: `50-51°F` resolves on `{50, 51}`

2. Celsius non-shoulder bins are point bins with settlement cardinality `1`
   - example: `10°C` resolves on `{10}`

3. Shoulder bins are open-shoulder bins
   - they are not ordinary finite bins
   - they must not be reasoned about as symmetric bounded ranges

### 4. Forbidden shortcuts

No packet, implementation, or review may infer bin semantics from:

1. label punctuation alone
2. informal human intuition about “1 degree” vs “2 degree” width
3. continuous interval width without checking discrete settlement support
4. continuous-model variance collapse without justifying discrete contract support

### 5. Packet requirement

Any future packet touching market math or settlement semantics must include:

1. `domain_assumptions`
2. authority source for each assumption
3. invalidation condition if the assumption is false

### 6. Review requirement

All third-party review on market-math or calibration packets must verify:

1. bin contract kind
2. settlement cardinality
3. shoulder semantics
4. whether the packet respects discrete support rather than only continuous intuition

## Non-goals

This amendment does not:

1. change runtime code immediately
2. choose new sigma floors immediately
3. settle final quantitative regularization constants immediately

It upgrades discrete settlement support to authority so future packets cannot reason from a false world model.
