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

Frozen conditional support:
- `repo_overview.md` -> first read `zeus_architecture_reference.md`,
  root `AGENTS.md`, and `workspace_map.md`
- `data_inventory.md` -> first read `zeus_data_and_replay_reference.md` and
  `architecture/data_rebuild_topology.yaml`
- `data_strategy.md` -> first read `zeus_data_and_replay_reference.md`,
  `architecture/data_rebuild_topology.yaml`, and active operations packets
- `statistical_methodology.md` -> first read `zeus_math_spec.md`,
  executable contracts, and targeted tests
- `quantitative_research.md` -> first read `zeus_domain_model.md`,
  `zeus_failure_modes_reference.md`, and `zeus_math_spec.md`
- `market_microstructure.md` -> first read
  `zeus_market_settlement_reference.md` and execution-price/vig contracts

Use frozen conditional support only when canonical references are not sufficient.
Do not cite these files as durable defaults or authority.

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
| `repo_overview.md` | Frozen conditional orientation support; superseded for default routing by architecture reference, root AGENTS, and workspace map |
| `data_inventory.md` | Frozen conditional data inventory; keep until generated machine inventory replaces detailed coverage/source facts |
| `data_strategy.md` | Frozen conditional data-roadmap evidence; extract lore before deletion |
| `statistical_methodology.md` | Frozen conditional math methodology; executable contracts and `zeus_math_spec.md` win |
| `quantitative_research.md` | Frozen conditional research evidence; extract lore before deletion |
| `market_microstructure.md` | Frozen conditional market research; settlement reference and execution contracts win |
| `zeus_math_spec.md` | Reference math/specification notes; executable law and authority manifests win on disagreement |
