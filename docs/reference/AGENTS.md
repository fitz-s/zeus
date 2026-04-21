# docs/reference AGENTS

Reference material — domain model, technical orientation, data status, methodology, and research. Read-only context for understanding Zeus. Not authority; authority lives in `docs/authority/`.

## Default vs conditional read path

**Default reads** (when a digest requests reference context):
- `zeus_domain_model.md`

**Conditional reads** (load only when the task directly requires them):
- `zeus_architecture_reference.md` for compact deep architecture orientation
- `zeus_market_settlement_reference.md` for compact settlement/market routing
- `settlement_source_provenance.md` when city/source settlement evidence matters
- `zeus_data_and_replay_reference.md` for compact data/replay status
- `zeus_failure_modes_reference.md` for compact failure-class reviews
- `zeus_math_spec.md` when math fact/spec context matters; executable law and authority manifests win

Temporary extraction sources:
- `repo_overview.md`
- `data_inventory.md`
- `data_strategy.md`
- `statistical_methodology.md`
- `quantitative_research.md`
- `market_microstructure.md`

Use temporary extraction sources only when the new canonical references are not
yet sufficient. Do not cite them as durable defaults.

Replacement/deletion eligibility is tracked in `architecture/reference_replacement.yaml`.

## File registry

| File | Purpose |
|------|---------|
| `zeus_domain_model.md` | "Zeus in 5 minutes" — probability chain, four strategies, alpha decay, settlement semantics (incl. discrete support), worked examples, translation loss law, structural decisions methodology, data provenance model, DST case study |
| `zeus_architecture_reference.md` | Compact canonical architecture reference extracted from legacy snapshots |
| `zeus_market_settlement_reference.md` | Compact canonical market/settlement reference and triage routing |
| `settlement_source_provenance.md` | Detailed settlement source/station provenance registry; reference evidence only |
| `zeus_data_and_replay_reference.md` | Compact canonical data/replay reference extracted from inventory/strategy/gaps |
| `zeus_failure_modes_reference.md` | Compact canonical failure-mode reference extracted from pathology/gap evidence |
| `repo_overview.md` | Technical orientation for first-time readers — architecture, runtime, testing, operations |
| `data_inventory.md` | Current data source status — what's available, what's missing, utilization status, quality assessments |
| `data_strategy.md` | Data improvement roadmap and priorities |
| `statistical_methodology.md` | Statistical methods — Monte Carlo, calibration, FDR, Kelly, bootstrap |
| `quantitative_research.md` | Research findings and experiment results |
| `market_microstructure.md` | Polymarket CLOB mechanics — order types, spreads, fill quality |
| `zeus_math_spec.md` | Reference math/specification notes; executable law and authority manifests win on disagreement |
