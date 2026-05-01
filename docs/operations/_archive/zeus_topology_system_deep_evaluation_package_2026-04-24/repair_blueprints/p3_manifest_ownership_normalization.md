# P3 Manifest Ownership Normalization Blueprint

## Objective

Make manifest ownership explicit and enforceable.

## Deliverables

- Ownership matrix in `manifests_system.md`.
- Optional schema/manifest section defining canonical fact owners.
- Topology doctor ownership validator.
- Issue `owner_manifest` mappings for major issue families.

## Allowed files

- `architecture/topology_schema.yaml`
- selected `architecture/*.yaml` manifests
- `scripts/topology_doctor*.py`
- `tests/test_topology_doctor.py`
- `docs/reference/modules/manifests_system.md`

## Forbidden

- Runtime/source behavior changes.
- Creating a new parallel registry.
- Deleting manifest data without tests and owner reassignment.

## Implementation steps

1. Define fact types.
2. Assign canonical owner manifest per fact type.
3. Mark duplicated references as derived/link-only.
4. Add validator:
   - conflict if two manifests claim canonical owner for same fact type/path,
   - error if blocking issue lacks owner_manifest after P1,
   - warning for placeholder fields without maturity status.
5. Add tests with fixture manifests.

## Verification

```bash
python scripts/topology_doctor.py --strict --json
pytest -q tests/test_topology_doctor.py -k "ownership or manifest"
```

## Review focus

Ownership and drift. Do not optimize for fewer lines; optimize for one owner per fact.
