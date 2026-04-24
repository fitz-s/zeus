# P5 Topology Doctor Test Resegmentation Blueprint

## Objective

Separate deterministic topology behavior tests from live repo-health tests.

## Allowed files

- `tests/test_topology_doctor.py`
- pytest marker config if needed
- fixture data/helpers
- validation docs

## Forbidden

- Weakening topology laws.
- Editing manifests solely to make live tests pass.
- Runtime/source behavior changes.

## Implementation steps

1. Add marker `live_topology`.
2. Classify tests:
   - fixture/unit behavior,
   - live repo health,
   - CLI parity,
   - output contract,
   - graph fixture,
   - closeout fixture.
3. Build fixture repos/manifests for drift cases.
4. Keep one live smoke group.
5. Update validation docs and commands.
6. Ensure default CI can run deterministic tests separately.

## Required commands

```bash
pytest -q tests/test_topology_doctor.py -m "not live_topology"
pytest -q tests/test_topology_doctor.py -m live_topology
```

## Review focus

Test semantics. Do not hide real live debt; isolate it.
