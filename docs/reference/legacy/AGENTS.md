# docs/reference/legacy AGENTS

Canonical home for legacy reference snapshots demoted from `docs/reports/`.

## What belongs here

Durable reference data that is no longer actively maintained but retains
archival value for understanding historical design decisions and data lineage.
These are NOT periodic reports — they are permanent reference materials that
were mis-located in `docs/reports/` prior to the docs taxonomy reorg (W6,
2026-05-17).

## Contents

| File | Purpose |
|------|---------|
| `legacy_reference_data_inventory.md` | Legacy data inventory snapshot |
| `legacy_reference_data_strategy.md` | Legacy data strategy snapshot |
| `legacy_reference_market_microstructure.md` | Legacy market microstructure snapshot |
| `legacy_reference_quantitative_research.md` | Legacy quantitative research snapshot |
| `legacy_reference_repo_overview.md` | Legacy repo overview snapshot |
| `legacy_reference_settlement_source_provenance.md` | Legacy settlement source provenance snapshot |
| `legacy_reference_statistical_methodology.md` | Legacy statistical methodology snapshot |

## Rules

- Do NOT add new files here. New reference material belongs in `docs/reference/`
  proper with appropriate classification.
- Do NOT treat these files as current-fact authority. They are historical
  snapshots only — use `docs/operations/current_source_validity.md` and
  `docs/operations/current_data_state.md` for current audited facts.
- Do NOT add periodic reports or dated audits here. This directory is for
  demoted durable reference data only.
- Files registered in `architecture/docs_registry.yaml` with
  `doc_class: legacy_reference` and `lifecycle_state: historical`.
