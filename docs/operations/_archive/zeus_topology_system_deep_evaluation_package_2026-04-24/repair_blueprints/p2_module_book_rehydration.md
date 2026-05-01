# P2 Module Book Rehydration Blueprint

## Objective

Promote hidden topology-system knowledge into dense reference-only module/system books.

## New or expanded books

- `docs/reference/modules/topology_system.md`
- `docs/reference/modules/code_review_graph.md`
- `docs/reference/modules/docs_system.md`
- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/topology_doctor_system.md`
- `docs/reference/modules/closeout_and_receipts_system.md`

## Allowed companion files

- `docs/reference/AGENTS.md`
- `docs/reference/modules/AGENTS.md`
- `architecture/docs_registry.yaml`
- `architecture/module_manifest.yaml`

## Forbidden

- Runtime/source behavior edits.
- Archives as default-read.
- Graph authority changes.
- Broad docs cleanup outside registration.

## Implementation steps

1. Copy/adapt the expansion drafts from this package.
2. Keep every book explicitly reference-only.
3. Register new books in docs registry and module manifest.
4. Route via scoped AGENTS rather than bloating AGENTS.
5. Add cross-links only where durable.
6. Run docs/module checks.

## Verification

```bash
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py context-pack --profile package_review --files docs/reference/modules/topology_system.md --json
pytest -q tests/test_topology_doctor.py -k "module or docs"
```

## Review focus

Density and ownership. Books should explain hidden obligations without becoming authority or packet logs.
