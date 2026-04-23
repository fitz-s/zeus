# Zeus Data And Replay Reference

Purpose: canonical descriptive reference for Zeus data surfaces, provenance
expectations, replay limits, and current-state routing. This file is not
authority; source code, DB truth, machine manifests, authority docs, and active
packet evidence win on disagreement.

## Data Truth Layers

Zeus data is not one flat pool. It is layered:

1. canonical DB/event truth
2. current audited data-state surfaces
3. replay/backtest diagnostic outputs
4. reports/artifacts/workbooks as evidence

Rows existing in a table do not automatically mean training eligibility,
live-certification readiness, replay completeness, or authority to change
runtime behavior.

## Canonical DB Split

Current repo posture distinguishes:

- `state/zeus-world.db`: authoritative data/world DB
- `state/zeus_trades.db`: trades DB
- `state/zeus.db`: legacy DB, not the current canonical data surface

Any reference text still centered on older primary-store names rather than the
current split above is stale.

## Legacy Versus v2 Posture

Zeus currently has a structural split between:

- legacy populated tables
- v2 dual-track schema surfaces

The current audited posture is:

- v2 schemas exist
- several v2 tables remain structurally empty
- legacy daily and instant surfaces still carry the live historical data burden

Read `docs/operations/current_data_state.md` for the present-tense audited
posture. Do not duplicate volatile row counts here.

## Load-Bearing Data Concepts

Durable concepts:

- provenance on write
- point-in-time truth
- authority/verification gates
- metric identity: `temperature_metric`, `physical_quantity`,
  `observation_field`, `data_version`
- explicit distinction between canonical truth and diagnostic replay

Data writes that matter for training or runtime decisions must carry provenance,
authority, and point-in-time meaning. Rows existing in a table are not enough.

## Replay Remains Diagnostic Until Proven Otherwise

Replay is not made truthful by having some historical settlements, having some
forecasts, reconstructing a plausible outcome path, or producing attractive
metrics.

Replay is only trustworthy when it preserves decision-time truth and the
required point-in-time surfaces. Until then, it stays
`diagnostic_non_promotion`.

## Current Fact Routing

For present-tense data posture, read:

- `docs/operations/current_data_state.md`
- `docs/operations/known_gaps.md`
- relevant Gate F packet evidence when needed

This canonical reference is intentionally durable. It should not become a moving
inventory dump.

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

## What This File Is For

Use this file to answer:

- what data classes exist
- what makes a data surface authoritative versus diagnostic
- why replay promotion is hard
- how dual-track identity interacts with data truth

For deeper module-specific detail, route to:

- `docs/reference/modules/data.md`
- `docs/reference/modules/state.md`
- `docs/reference/modules/engine.md`

## What This File Is Not

- not a data inventory snapshot
- not a rebuild approval
- not a present-tense ingest dashboard
- not a replacement for active current-fact surfaces
