# Zeus Market And Settlement Reference

Purpose: canonical descriptive reference for market structure, settlement
semantics, source provenance, and mismatch triage. This file is not authority;
source code, tests, `SettlementSemantics`, manifests, and `docs/authority/**`
win on disagreement.

Extracted from: `docs/reference/settlement_source_provenance.md`,
`docs/runbooks/settlement_mismatch_triage.md`,
`docs/reference/market_microstructure.md`,
`docs/artifacts/polymarket_city_settlement_audit_2026-04-14.md`, and
`docs/reference/zeus_domain_model.md`.

## Why Settlement Semantics Dominate

Polymarket weather markets resolve to discrete settlement values. Temperature
intuition based on continuous intervals is unsafe unless converted through the
city/unit settlement contract.

Rules to remember:

- WU-style integer settlement uses WMO asymmetric half-up rounding:
  `floor(x + 0.5)`.
- Some oracle/provider families may use different semantics, such as HKO floor
  behavior.
- Settlement support is discrete: `point`, `finite_range`, or `open_shoulder`.
- Shoulder bins stay in raw probability space and are not width-normalized
  density bins.

## Market Structure

Weather markets are bin families, not isolated binary propositions. The full
family must cover all possible integer settlement values exactly once. A single
bad parse can corrupt calibration, FDR family definition, and edge selection.

Execution semantics matter because Zeus trades live CLOB markets:

- Entry orders are limit orders.
- Fill probability, bid/ask, queue, fees, and adverse selection affect whether
  modeled edge is executable.
- Market price is derived context for posterior/edge computation; it is not
  settlement truth.

## Source Provenance Model

Settlement truth depends on city, station/source, local date, unit, and provider
semantics. `docs/reference/settlement_source_provenance.md` records current
known city/provider behavior and source-change evidence.

Durable source-risk classes:

- stable WU cities with no observed source changes
- cities with source/provider changes
- station mismatch between Zeus observations and PM settlement source
- data quality incidents where provider/API summaries disagree with final PM
  resolution
- date mapping or rounding bugs in Zeus code

## Rounding And Bin Semantics

Use `SettlementSemantics` instead of ad hoc rounding. Do not use Python
`round()`, `numpy.round`, or integer coercion for settlement values. Celsius and
Fahrenheit families differ in bin width/cardinality and cannot be mixed blindly.

Open-ended shoulder bins, range bins, and point bins must keep their
`bin_contract_kind` visible through probability, calibration, replay, and
settlement scoring.

## Mismatch Triage Model

When Zeus and Polymarket settlement disagree, do not assume the model is wrong
or the source is wrong without triage.

Triage categories:

1. Wrong station or data source.
2. Polymarket source/provider changed.
3. Bad or partial observation data.
4. Date mapping, local day, or rounding bug.

The durable operator procedure lives in
`docs/runbooks/settlement_mismatch_triage.md`.

## Supporting Tables And Evidence

- Source/provenance registry:
  `docs/reference/settlement_source_provenance.md`
- Operator procedure:
  `docs/runbooks/settlement_mismatch_triage.md`
- City audit evidence:
  `docs/artifacts/polymarket_city_settlement_audit_2026-04-14.md`
- Domain overview:
  `docs/reference/zeus_domain_model.md`

## What This File Is Not

- not live settlement law
- not an operator runbook
- not a PM source override
- not a packet diary
