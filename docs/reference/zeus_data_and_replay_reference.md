# Zeus Data And Replay Reference

Purpose: canonical descriptive reference for data availability, load-bearing
surfaces, replay limitations, rebuild status, and data-quality risks. This file
is not authority; runtime code, DB truth, manifests, active operations packets,
and tests win on disagreement.

Extracted from: `docs/reference/data_inventory.md`,
`docs/reference/data_strategy.md`, `docs/operations/known_gaps.md`, and
`docs/authority/zeus_dual_track_architecture.md`.

## Current Data Reality

Zeus has multiple data classes with different authority levels:

- Canonical runtime/trade truth lives in DB/event/projection surfaces.
- Weather/world truth lives in world-data tables and must preserve provenance.
- Backtest/replay outputs are diagnostic and cannot authorize live behavior.
- Reports, workbooks, and JSON exports are evidence/projections, not authority.

The active gap register now lives at `docs/operations/known_gaps.md`. It should
be read for present-tense blockers, but it is still an operations surface, not
architecture law.

## Load-Bearing Data Surfaces

Key load-bearing surfaces include:

- observations and observation provenance
- hourly/local-time observation facts
- solar/daylight context
- settlement rows and PM outcome truth
- ensemble snapshots and `p_raw` materialization
- calibration pairs and Platt models
- token price and execution facts
- position events/projections for trade lifecycle truth

Data writes that matter for training or runtime decisions must carry provenance,
authority, and point-in-time meaning. Rows existing in a table are not enough.

## Replay Bottlenecks

Replay remains diagnostic unless it can reconstruct the decision-time truth
surface. Major blockers historically include:

- missing forecast references
- vector shape mismatch between live decisions and historical reconstruction
- synthetic timestamps or fabricated decision-time context
- settlement/source mismatch
- incomplete price/microstructure history
- calibration rows without authority/provenance separation

Backtest output is `diagnostic_non_promotion`: useful for investigation, not
live authorization.

## Dual-Track Implications

High and low metric families require explicit identity:

- `temperature_metric`
- `physical_quantity`
- `observation_field`
- `data_version`

Historical forecast rows missing causal issue-time semantics may be useful for
runtime degradation, but not canonical training. Daily-low Day0 causality is not
a mirror of high Day0 and must route through nowcast behavior when the local day
has already started.

## Open Data Risks

Known high-level risks:

- DST-safe historical rebuild for diurnal/hourly aggregates remains an active
  certification concern.
- Alternative data sources can be collected but still be unverified.
- Forecast and observation coverage may be uneven across cities/time windows.
- Root workbooks and reports can contain useful evidence but must be promoted
  through manifests/tests/packets before changing behavior.

## What This File Is Not

- not a rebuild approval
- not a data inventory dump
- not runtime DB truth
- not a replacement for `architecture/data_rebuild_topology.yaml`

Where to go next:

- Current blockers: `docs/operations/known_gaps.md`
- Rebuild law: `architecture/data_rebuild_topology.yaml`
- Dual-track law: `docs/authority/zeus_dual_track_architecture.md`
- Data source details pending extraction:
  `docs/reference/data_inventory.md`, `docs/reference/data_strategy.md`
