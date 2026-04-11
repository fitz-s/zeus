# docs/reference AGENTS

Reference material — domain model, technical orientation, data status, methodology, and research. Read-only context for understanding Zeus. Not authority; authority lives in `docs/authority/`.

## Default vs conditional read path

**Default reads** (most tasks):
- `zeus_domain_model.md`
- `repo_overview.md`
- `data_inventory.md` when data/runtime status matters

**Conditional reads** (load only when the task directly requires them):
- `data_strategy.md`
- `statistical_methodology.md`
- `quantitative_research.md`
- `market_microstructure.md`

## File registry

| File | Purpose |
|------|---------|
| `zeus_domain_model.md` | "Zeus in 5 minutes" — probability chain, four strategies, alpha decay, settlement semantics (incl. discrete support), worked examples, translation loss law, structural decisions methodology, data provenance model, DST case study |
| `repo_overview.md` | Technical orientation for first-time readers — architecture, runtime, testing, operations |
| `data_inventory.md` | Current data source status — what's available, what's missing, utilization status, quality assessments |
| `data_strategy.md` | Data improvement roadmap and priorities |
| `statistical_methodology.md` | Statistical methods — Monte Carlo, calibration, FDR, Kelly, bootstrap |
| `quantitative_research.md` | Research findings and experiment results |
| `market_microstructure.md` | Polymarket CLOB mechanics — order types, spreads, fill quality |
