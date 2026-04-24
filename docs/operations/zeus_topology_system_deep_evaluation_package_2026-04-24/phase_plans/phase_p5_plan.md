# Phase Plan — P5: Topology Doctor Test Resegmentation

**Companion to:** `../repair_blueprints/p5_topology_doctor_test_resegmentation.md`,
`../validation/fixture_vs_live_test_split.md`,
`../MAIN_ROUTE_IMPLEMENTATION_PLAN.md` §3 (P5 row).

## 1. Goal restated

Split deterministic topology-doctor regression tests from live
repo-health tests. Today `tests/test_topology_doctor.py` is 3,724 lines
and mixes:

- pure fixture tests (lane policy, issue model, ownership validator,
  graph appendix, closeout fixture);
- live repo-health tests (current docs registry contents, current
  source rationale rows, current script manifest entries);
- output-contract / CLI parity tests.

A single test run conflates "topology code regression" with "current
repo drift". After P5, the two purposes are separate test selections.

## 2. Anchor points

| What | Where |
|------|-------|
| Test file | `tests/test_topology_doctor.py` (3,724 lines) |
| pytest config | `pyproject.toml` or `pytest.ini` (markers section) |
| Validation guide | `../validation/fixture_vs_live_test_split.md` |
| Live health command list | `../validation/live_repo_health_commands.md` |
| Topology doctor surface (now post-P0..P4) | `scripts/topology_doctor*.py` |

## 3. Pre-decisions (resolves OQ-15)

- **OQ-15 decision**: live repo-health tests run on a separate CI lane
  scheduled per PR opt-in (label `topology-live-health`) and on a
  nightly cron; default CI runs only `not live_topology`. No
  enforcement change required to satisfy the decision — only
  documentation in `validation/fixture_vs_live_test_split.md`.
- **Marker name**: `live_topology` (already named in the blueprint).
  Add to pytest markers config to avoid `PytestUnknownMarkWarning`.
- **Promotion rule**: a test gets `@pytest.mark.live_topology` when it
  asserts about the *current* contents of any file under `architecture/**`,
  `docs/**`, `src/**`, or `scripts/**`, rather than against a fixture
  workspace.

## 4. Ordered atomic todos

1. **Register pytest marker.** Add the `live_topology` marker to the
   project's pytest configuration (`pyproject.toml` `[tool.pytest.ini_options]`
   `markers = [...]` or `pytest.ini`); the marker description is
   "depends on live repo state, not deterministic fixtures".
2. **Classify every test** in `tests/test_topology_doctor.py`. Produce
   a temporary scratch list (in working memory only — not committed)
   tagging each `def test_*` as one of:
   - `fixture_unit` (lane policy, issue model factory, validator with
     fixture manifests),
   - `live_health` (asserts on current repo manifests/files),
   - `cli_parity` (output shape, JSON schema),
   - `output_contract` (renderer/grouping golden tests),
   - `graph_fixture` (uses fake graph cache or fixture appendix),
   - `closeout_fixture` (uses fixture changed-files set).
3. **Apply markers**:
   - `@pytest.mark.live_topology` on every `live_health` test.
   - No marker on the rest (fixture-default).
4. **Lift inline live-state into fixtures** where cheap:
   - When a test reads `architecture/foo.yaml` to assert generic
     structure, prefer a small fixture YAML created via
     `tmp_path / "foo.yaml"`. Keep this small — only do it when the
     edit is a few lines.
   - Do **not** rewrite tests that genuinely audit the live repo;
     those stay as `live_topology` markers.
5. **Build a small fixture-repo helper** if not present:
   `tests/conftest.py` (or extend it) with a `fixture_repo_root`
   factory that materializes a minimal `architecture/`, `docs/`,
   `src/` skeleton. The fixture is reused by lane-policy /
   issue-model / ownership / graph / closeout fixture tests added in
   P0..P4.
6. **Fixture coverage cases** (the laws that must remain provable):
   - unrelated docs drift does not block source navigation (P0)
   - changed source missing rationale still blocks closeout (P0)
   - typed issue JSON preserves legacy keys (P1)
   - blocking issue without `owner_manifest` is caught (P3)
   - graph appendix marks `derived_not_authority` (P4)
   - graph appendix respects size budget (P4)
7. **Update `validation/fixture_vs_live_test_split.md`** with the
   final commands and the live-health schedule decision from §3.
8. **Update `validation/live_repo_health_commands.md`** with the exact
   `pytest -m live_topology` invocation and the expected drift
   surfaces.
9. **Run twice for determinism check**:
   ```bash
   pytest -q tests/test_topology_doctor.py -m "not live_topology"
   pytest -q tests/test_topology_doctor.py -m "not live_topology"
   ```
   The two runs must produce identical pass/fail sets.
10. **Validation matrix row** for P5.

## 5. Verification

```bash
pytest -q tests/test_topology_doctor.py -m "not live_topology"
pytest -q tests/test_topology_doctor.py -m live_topology
pytest -q tests/test_topology_doctor.py --collect-only -q | wc -l
python3 scripts/topology_doctor.py closeout --changed-files tests/test_topology_doctor.py --summary-only
```

## 6. Definition of done

- `live_topology` marker registered and described.
- Every `live_health` test in `tests/test_topology_doctor.py` carries
  `@pytest.mark.live_topology`.
- `pytest -m "not live_topology"` is deterministic across two
  consecutive runs on the same commit.
- `pytest -m live_topology` runs without import errors and reports
  current live drift if any.
- Validation guide and live-health command list updated.
- No law was deleted from any test; no test was made to pass by
  weakening assertions.
- Validation matrix row green.

## 7. Rollback

Marker removal is mechanical (`@pytest.mark.live_topology` deletions).
Fixture extractions can stay or revert independently. Configuration
addition is a single `pyproject.toml` / `pytest.ini` line.

## 8. Critic focus

- Was a marker used to *hide* a failing live-health test? It must not
  be. Failing live-health tests must continue to fail under
  `-m live_topology`.
- Are any fixture extractions accidentally simulating laws that the
  live test was actually proving against the live repo? Spot-check the
  six fixture coverage cases.

## 9. Risks specific to P5

- **R-P5-1**: A genuinely live-only test gets misclassified as
  fixture and starts asserting against a fixture workspace, hiding
  drift in the real repo. Mitigation: §3 promotion rule is explicit
  about the markers' semantics; the validation matrix row records
  before/after counts so a sudden drop in live tests is auditable.
- **R-P5-2**: `tmp_path` fixtures collide with monkeypatched
  `architecture/` paths in topology_doctor. Mitigation: use the
  fixture_repo_root helper that monkeypatches the relevant constants
  in `scripts/topology_doctor.py` (`ARCH_PATH`, `TOPOLOGY_PATH`, etc.)
  for the test scope only.

## 10. Lore commit message

`Topology P5: split deterministic topology regressions from live repo health`
