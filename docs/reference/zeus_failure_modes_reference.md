# Zeus Failure Modes Reference

Purpose: canonical descriptive reference for durable failure classes,
pathologies, and mitigation patterns. This file is not law; current authority,
manifests, tests, and executable source win on disagreement.

Extracted from: `docs/reports/zeus_pathology_registry_2026-04-16.md`,
`docs/reports/task_2026-04-18_external_reality_review.md`,
`docs/operations/known_gaps.md`, bug-audit reassessment notes, and
`architecture/history_lore.yaml`.

## Truth And Authority Failure Modes

Recurring class: a convenient projection, report, archive, or JSON file is
treated as if it were canonical truth.

Examples:

- JSON/status exports promoted above DB/event truth.
- Backtest/replay diagnostics used as live authorization.
- Archive evidence treated as current law.
- LLM summary accepted without manifest/test/evidence support.

Mitigation: classify the surface, route through manifests/tests/current packet
state, and preserve point-in-time authority.

## Runtime And Lifecycle Failure Modes

Recurring class: orchestration code bypasses lifecycle or control authority.

Examples:

- exit intent confused with local close
- settlement confused with exit
- arbitrary state strings
- chain unknown treated as empty
- RED risk made advisory-only

Mitigation: lifecycle transitions belong to lifecycle authority; risk outputs
must alter behavior; chain-truth states must distinguish synced, empty, and
unknown.

## Data And Replay Failure Modes

Recurring class: data that exists is treated as data that is certified.

Examples:

- calibration rows without provenance/authority gates
- replay using hindsight or synthetic decision-time facts
- stale DST aggregates reused after runtime code fixes
- forecast/observation source mismatch hidden behind clean tables

Mitigation: preserve provenance, training eligibility, causality status, and
decision-time truth. Backtest output stays diagnostic until certified.

## Market And Settlement Failure Modes

Recurring class: market settlement is modeled with continuous or generic
temperature intuition instead of discrete city/source semantics.

Examples:

- Python/banker rounding
- missing `bin_contract_kind`
- station/provider source drift
- shoulder bins treated as finite ranges
- Celsius/Fahrenheit bin families mixed in calibration

Mitigation: use `SettlementSemantics`, explicit bin topology, source provenance,
and settlement mismatch triage.

## Agentic Workspace Failure Modes

Recurring class: agents repair confusion by adding prose or moving files without
machine registration.

Examples:

- untracked operations packets treated as durable work
- completed packets left as live control surfaces
- runtime-local `.omx` plans not inventoried
- top-level docs accumulating mixed authority/reference/evidence roles
- graph output treated as authority instead of derived context

Mitigation: update `architecture/docs_registry.yaml`, scoped `AGENTS.md`,
`current_state.md`, receipts, work logs, and topology checks with every
material docs/workspace move.

## Mitigation And Antibody Crosswalk

- Authority/projection boundary: `architecture/invariants.yaml`,
  `architecture/negative_constraints.yaml`, `architecture/history_lore.yaml`
- Docs classification: `architecture/docs_registry.yaml`
- Runtime packet truth: `docs/operations/current_state.md`
- Archive access: `docs/archive_registry.md`
- Code structure context: `.code-review-graph/graph.db` as derived context only
- Tests/checks: `tests/test_topology_doctor.py`, `scripts/topology_doctor.py`

## What This File Is Not

- not an incident report
- not a bug backlog
- not an authority document
- not a complete archive of every pathology
