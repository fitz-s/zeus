# P4 Context Pack and Graph Extraction Blueprint

## Objective

Make graph useful for online-only agents through derived textual context while preserving non-authority boundaries.

## Allowed files

- `scripts/topology_doctor_code_review_graph.py`
- `scripts/topology_doctor_context_pack.py`
- `docs/reference/modules/code_review_graph.md`
- `docs/reference/modules/topology_system.md`
- optional generated sidecar under an approved generated/reference path
- tests

## Forbidden

- Treating graph as authority.
- Custom graph refresh scripts.
- Runtime/source behavior changes.
- Unapproved `graph.db` staging.

## Implementation steps

1. Add graph appendix output section:
   - `authority_status: derived_not_authority`
   - freshness status
   - limitations
   - changed nodes
   - likely tests
   - impacted files
   - missing graph coverage
2. Add optional text sidecar generation if approved.
3. Keep stale/missing graph advisory except graph-required tasks.
4. Include graph limitations in context packs.
5. Test stale/missing/usable graph cases.

## Verification

```bash
python scripts/topology_doctor.py --code-review-graph-status --json
python scripts/topology_doctor.py context-pack --profile package_review --files <files> --json
pytest -q tests/test_topology_doctor.py -k "graph or context_pack"
```

## Review focus

Authority safety. Graph should surface routes, not decide truth.
