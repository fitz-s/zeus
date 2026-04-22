# Zeus Market And Settlement Reference

Purpose: canonical descriptive reference for market structure, discrete
settlement semantics, provider/source risk classes, and mismatch triage
routing. This file is not authority; executable contracts, manifests, authority
docs, tests, and current audited source evidence win on disagreement.

## Why Settlement Semantics Dominate

Zeus is not predicting a continuous atmospheric quantity for its own sake. It is
trading venue contracts that resolve on:

- city
- local date
- settlement unit
- discrete integer/bin semantics
- provider/source-specific reporting behavior

Continuous intuition must always be converted through settlement semantics
before it becomes market truth.

## Durable Settlement Rules

Keep these durable concepts visible:

- `bin_contract_kind`
- `bin_settlement_cardinality`
- `settlement_support_geometry`
- WMO half-up style integer settlement where applicable
- unit-specific contract structure
- source/provider/date interactions as a risk model

Use `SettlementSemantics` instead of ad hoc rounding. Do not use Python
`round()`, `numpy.round`, or integer coercion for settlement values.

## Durable Source-Risk Classes

The durable source lesson is not a frozen city table. It is the risk taxonomy:

- stable provider routes
- provider/source changes
- station mismatch between observed data and PM resolution source
- provider/API versus website/UI disagreement
- date-mapping and rounding bugs in Zeus
- partial or quarantined observation data

Those classes belong in the canonical reference. The exact current city/provider
map does not.

## Current Source Truth

For present-tense city/provider validity, read:

- `docs/operations/current_source_validity.md`

For dated older audit evidence, read reports and artifacts. Do not treat dated
audit tables as durable current reference.

## Market Structure

Weather markets are not isolated binary bets. They are bin families with exact
coverage and discrete settlement support. A bad parse or bad source assumption
corrupts calibration, family definition, edge selection, replay interpretation,
and settlement validation.

Execution semantics matter because Zeus trades live CLOB markets:

- entry orders are limit orders
- fill probability, bid/ask, queue, fees, and adverse selection affect whether
  modeled edge is executable
- market price is derived context for posterior/edge computation; it is not
  settlement truth

## Mismatch Triage Routing

When Zeus and Polymarket settlement disagree, triage should consider:

1. wrong station or wrong provider
2. source/provider drift
3. bad or partial observation data
4. date-mapping/local-day bug
5. rounding/semantic bug inside Zeus

Operator procedure:

- `docs/runbooks/settlement_mismatch_triage.md`

## External Execution Reminder

Modeled edge is not executable edge. Venue order lifecycle, fees, and partial
fills matter. Those are durable market truths, but they belong here as concepts,
not as a stale changelog dump.

## What This File Is Not

- not a frozen city/provider table
- not a current source-validity dashboard
- not a packet diary
- not authority law
