# tests AGENTS

Tests defend Zeus kernel law, runtime safety, and delivery guarantees. This
file is the human route into the test suite; the machine-readable test map is
`architecture/test_topology.yaml`.

Module book: `docs/reference/modules/tests.md`

## Machine Registry

Use `architecture/test_topology.yaml` for:

- law-gate membership
- test categories
- high-sensitivity skip accounting
- reverse-antibody status
- test-to-law routing

Use `python3 scripts/topology_doctor.py --tests --json` to check that active
`tests/test_*.py` files are classified.

## Local Registry

| Path | Purpose |
|------|---------|
| `__init__.py` | Package marker for pytest/import tooling |
| `contracts/` | Spec-owned validation manifests; see `tests/contracts/AGENTS.md` |

Top-level `test_*.py` files are intentionally not duplicated here. Query
`architecture/test_topology.yaml` instead of hand-maintaining another file list.

## Test Trust Policy

Tests are **untrusted by default**. Only 36/162 tests have lifecycle headers
and are trusted to run without prior audit. The machine-readable registry is:

`architecture/test_topology.yaml` → `test_trust_policy.trusted_tests`

### Trust classification

| Class | Criteria | Agent action |
|-------|----------|-------------|
| **trusted** | Has `# Created: YYYY-MM-DD` + `# Last reused/audited: YYYY-MM-DD` | May run directly |
| **reviewed_only** | Has Created + last_reviewed but `last_reused=never` | Audit required before running |
| **audit_required** | No lifecycle header | Audit required before running |

### Before running an untrusted test

1. Read the test source — verify it tests current code contracts, not deleted APIs
2. Check `architecture/test_topology.yaml` for category and skip status
3. If the test is valid, add lifecycle headers and register in `trusted_tests`
4. Only then run it

### When creating or reusing a test

Every test file must have these headers in the first 15 lines:
```python
# Created: YYYY-MM-DD
# Last reused/audited: YYYY-MM-DD
# Authority basis: <packet or task that created/validated this test>
```

## Core Rules

- Breaking an architecture/law test means the code or plan is wrong, not that
  the test is inconvenient.
- Canonical file/function naming and test freshness rules live in
  `architecture/naming_conventions.yaml`; do not redefine them here.
- Touched, newly created, or evidence-reused top-level `tests/test_*.py` files
  must satisfy the freshness header contract in `architecture/naming_conventions.yaml`.
- Old tests are not proof by age. Before relying on an old/unknown test file as
  evidence, inspect its current code, `architecture/test_topology.yaml`, skip
  status, and update `last_reviewed` / `last_reused` as appropriate.
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
| Find tests for a law/invariant | `python3 scripts/topology_doctor.py --tests --json` |
| Find cross-module validation manifests | `tests/contracts/spec_validation_manifest.py` |
| Edit source behavior | digest task + `architecture/source_rationale.yaml` + targeted tests |
| Edit test topology | `architecture/test_topology.yaml` + `tests/test_topology_doctor.py` |
| Review old/stale tests | `architecture/test_topology.yaml` categories before deleting anything |
