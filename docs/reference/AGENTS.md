# docs/reference AGENTS

Canonical reference material for Zeus. Reference docs explain durable concepts
and orientation; they are not authority. Authority lives in `docs/authority/**`,
machine manifests, tests, and executable source.

## Default vs conditional read path

**Default reads** (when a digest requests reference context):
- `zeus_domain_model.md`

**Conditional reads** (load only when the task directly requires them):
- `zeus_architecture_reference.md` for architecture orientation
- `zeus_market_settlement_reference.md` for settlement/market concepts
- `zeus_data_and_replay_reference.md` for data/replay concepts
- `zeus_failure_modes_reference.md` for failure-class reviews
- `zeus_math_spec.md` when math fact/spec context matters
- `modules/AGENTS.md` when the task is module-sensitive and needs a dense module
  book route
- `modules/state.md`, `modules/engine.md`, and `modules/data.md` for the first
  landed high-risk module books

Current data/source facts live under operations current-fact surfaces, not in
this directory. Dated analytical/support snapshots live under the reports
subroot.

Replacement/deletion eligibility is now governed by
`architecture/docs_registry.yaml` and packet evidence; do not recreate the old
support-reference layer.

Dense module books live under `docs/reference/modules/`. They are reference
surfaces that explain module behavior, hazards, and tests deeply enough for
zero-context work. They do not outrank authority docs, machine manifests,
current-fact surfaces, tests, or executable source.

## File registry

| File | Purpose |
|------|---------|
| `zeus_domain_model.md` | Short domain model and first conceptual reference |
| `zeus_architecture_reference.md` | Durable descriptive architecture reference |
| `zeus_market_settlement_reference.md` | Durable market/settlement concepts and source-risk taxonomy |
| `zeus_data_and_replay_reference.md` | Durable data/replay concepts and current-fact routing |
| `zeus_failure_modes_reference.md` | Durable failure-mode and mitigation reference |
| `zeus_math_spec.md` | Deep math/specification reference; executable law and authority manifests win on disagreement |
| `modules/AGENTS.md` | Router for dense module books under `docs/reference/modules/` |
| `modules/state.md` | Dense state truth / lifecycle / projection module book |
| `modules/engine.md` | Dense engine orchestration / replay / sequencing module book |
| `modules/data.md` | Dense source-routing / ingest / data-versioning module book |

## Rules

- Do not add stale support, dated audit, packet-evidence, workbook, or current
  operational fact files here.
- Do not route canonical references to demoted legacy-reference reports for
  present-tense facts.
- Use `docs/operations/current_data_state.md` and
  `docs/operations/current_source_validity.md` for current audited facts.
- Do not treat module books as authority or current-fact surfaces; use them as
  dense orientation after the scoped router or `architecture/module_manifest.yaml`
  tells you which module matters.
- Do not recreate a frozen support layer.
