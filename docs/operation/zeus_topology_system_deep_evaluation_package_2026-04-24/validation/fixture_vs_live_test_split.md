# Fixture vs Live Test Split

## Purpose

Prevent topology_doctor code changes from failing because the live repo has unrelated drift.

## Proposed markers

```ini
[pytest]
markers =
    live_topology: live repo health checks that may fail due to active branch drift
```

## Deterministic fixture tests

Use fixtures for:

- docs registry missing/stale entries,
- source rationale missing/stale entries,
- test topology duplicate/missing classification,
- script manifest missing/stale/expired metadata,
- map maintenance companion requirements,
- navigation unrelated drift warnings,
- closeout changed-file blockers,
- graph sqlite status cases,
- module book missing headings,
- module manifest missing fields.

## Live tests

Keep live tests for:

- one strict health smoke,
- docs live registry health,
- source live rationale health,
- scripts live manifest health,
- tests live topology health,
- graph live status,
- compiled topology shape.

## Commands

```bash
pytest -q tests/test_topology_doctor.py -m "not live_topology"
pytest -q tests/test_topology_doctor.py -m live_topology
```

## Acceptance

- Deterministic tests pass independent of active packet drift.
- Live failures are visible and classified.
- No law is removed; law explanations move into module books.
