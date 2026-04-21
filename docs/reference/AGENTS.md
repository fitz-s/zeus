# docs/reference AGENTS

Reference material — domain model, technical orientation, data status, methodology, and research. Read-only context for understanding Zeus. Not authority; authority lives in `docs/authority/`.

## Default vs conditional read path

**Default reads** (when a digest requests reference context):
- `zeus_domain_model.md`

**Conditional reads** (load only when the task directly requires them):
- `zeus_architecture_reference.md` for deep architecture orientation
- `zeus_market_settlement_reference.md` for settlement/market provenance
- `zeus_data_and_replay_reference.md` for data/replay status
- `zeus_failure_modes_reference.md` for failure-class reviews
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
| `zeus_architecture_reference.md` | Canonical deep architecture reference anchor; P1 extraction target |
| `zeus_market_settlement_reference.md` | Canonical market/settlement reference anchor; P1 extraction target |
| `zeus_data_and_replay_reference.md` | Canonical data/replay reference anchor; P1 extraction target |
| `zeus_failure_modes_reference.md` | Canonical failure modes reference anchor; P1 extraction target |
| `repo_overview.md` | Technical orientation for first-time readers — architecture, runtime, testing, operations |
| `data_inventory.md` | Current data source status — what's available, what's missing, utilization status, quality assessments |
| `data_strategy.md` | Data improvement roadmap and priorities |
| `statistical_methodology.md` | Statistical methods — Monte Carlo, calibration, FDR, Kelly, bootstrap |
| `quantitative_research.md` | Research findings and experiment results |
| `market_microstructure.md` | Polymarket CLOB mechanics — order types, spreads, fill quality |
| `zeus_math_spec.md` | Reference math/specification notes; executable law and authority manifests win on disagreement |
