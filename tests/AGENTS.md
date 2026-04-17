# tests AGENTS

Tests defend Zeus kernel law, runtime safety, and delivery guarantees. This
file is the human route into the test suite; the machine-readable test map is
`architecture/test_topology.yaml`.

## Machine Registry

Use `architecture/test_topology.yaml` for:

- law-gate membership
- test categories
- high-sensitivity skip accounting
- reverse-antibody status
- test-to-law routing

Use `python scripts/topology_doctor.py --tests --json` to check that active
`tests/test_*.py` files are classified.

## Local Registry

| Path | Purpose |
|------|---------|
| `__init__.py` | Package marker for pytest/import tooling |
| `contracts/` | Spec-owned validation manifests; see `tests/contracts/AGENTS.md` |

Top-level `test_*.py` files are intentionally not duplicated here. Query
`architecture/test_topology.yaml` instead of hand-maintaining another file list.

## Core Rules

- Breaking an architecture/law test means the code or plan is wrong, not that
  the test is inconvenient.
- Touched or newly created top-level `tests/test_*.py` files need a freshness
  header in the first 30 lines: `Lifecycle: created=YYYY-MM-DD;
  last_reviewed=YYYY-MM-DD; last_reused=YYYY-MM-DD|never`, plus `Purpose:` and
  `Reuse:` lines.
- Old tests are not proof by age. Before relying on an old/unknown test file as
  evidence, inspect its current code, `architecture/test_topology.yaml`, skip
  status, and update `last_reviewed` / `last_reused` as appropriate.
- Test helper/function names should state the behavior or law being protected;
  avoid generic names such as `test_process`, `helper`, or `check` unless the
  surrounding domain noun makes the contract obvious.
- Do not delete or xfail high-sensitivity tests without a written sunset plan
  and packet evidence.
- Prefer relationship tests for cross-module work: prove what must remain true
  when one module's output flows into the next.
- Mark transitional/advisory tests explicitly; do not let them masquerade as
  active law.
- Historical doc claims are not active law unless backed by code, manifest, or
  a current authority surface.

## Common Routes

| Task | Start With |
|------|------------|
| Find tests for a law/invariant | `python scripts/topology_doctor.py --tests --json` |
| Find cross-module validation manifests | `tests/contracts/spec_validation_manifest.py` |
| Edit source behavior | digest task + `architecture/source_rationale.yaml` + targeted tests |
| Edit test topology | `architecture/test_topology.yaml` + `tests/test_topology_doctor.py` |
| Review old/stale tests | `architecture/test_topology.yaml` categories before deleting anything |
