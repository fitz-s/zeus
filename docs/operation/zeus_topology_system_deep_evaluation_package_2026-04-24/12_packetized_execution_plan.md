# 12 — Packetized Execution Plan


## P0 — Scope and lane repair

**Objective:** Separate navigation, closeout, strict, and global health blocking policy without changing source/runtime behavior.

**Allowed files:**
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_closeout.py`
- minimal related tests in `tests/test_topology_doctor.py`

**Forbidden files:**
- Runtime/source behavior files under `src/**`
- broad manifest rewrites
- graph.db
- docs archives

**Expected outputs:**
- navigation returns route digest with direct blockers vs repo_health warnings
- closeout gates changed files + companions
- `--navigation --strict-health` or equivalent remains available

**Tests / verification:**
- `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py`
- targeted pytest for navigation/closeout
- fixture proving unrelated docs drift does not block source closeout

**Closeout commands:**
- `python scripts/topology_doctor.py --navigation --task "topology lane repair" --files scripts/topology_doctor.py --json`
- `python scripts/topology_doctor.py closeout --changed-files scripts/topology_doctor.py tests/test_topology_doctor.py --summary-only`

**Rollback:**
Revert topology_doctor lane policy changes and restore previous navigation/closeout tests.

**Lore commit message:**
`Topology P0: separate navigation/closeout/global-health lane policy without runtime behavior changes`

**Pre-close critic/verifier focus:** semantics and noise


## P1 — Typed issue model

**Objective:** Extend topology issues with lane/scope/owner/repair/blocking metadata while preserving JSON backward compatibility.

**Allowed files:**
- topology_doctor issue dataclass/factory/renderers
- helper modules only to add metadata
- tests

**Forbidden files:**
- broad manifest changes except optional schema docs
- runtime/source behavior

**Expected outputs:**
- `TopologyIssue` additive fields
- mode policy uses `blocking_modes`
- grouped JSON/human output by repair_kind/owner

**Tests / verification:**
- py_compile
- pytest targeted issue JSON compatibility
- old `code/path/message/severity` consumers still pass

**Closeout commands:**
- navigation JSON sample
- closeout JSON sample
- strict JSON sample

**Rollback:**
Keep helper validators returning legacy issue shape via compatibility factory.

**Lore commit message:**
`Topology P1: add typed issue metadata and mode-aware blocking`

**Pre-close critic/verifier focus:** ownership and compatibility


## P2 — Module book rehydration

**Objective:** Promote hidden topology/docs/manifest/doctor/closeout knowledge into dense reference-only module/system books.

**Allowed files:**
- `docs/reference/modules/*.md`
- `docs/reference/AGENTS.md`
- `architecture/docs_registry.yaml`
- `architecture/module_manifest.yaml` only for registrations

**Forbidden files:**
- scripts behavior
- runtime/source behavior
- archives as default-read

**Expected outputs:**
- expanded system books
- docs/module manifest registrations
- explicit non-authority labels

**Tests / verification:**
- docs mode
- module book/context-pack checks
- markdown link/path sanity

**Closeout commands:**
- `python scripts/topology_doctor.py --docs --json`
- `python scripts/topology_doctor.py context-pack --profile package_review --files docs/reference/modules/topology_system.md --json`

**Rollback:**
Revert added/expanded books and registry entries atomically.

**Lore commit message:**
`Topology P2: rehydrate topology cognition into reference-only module/system books`

**Pre-close critic/verifier focus:** cognition density and drift


## P3 — Manifest ownership normalization

**Objective:** Define canonical owner per fact type and add duplicate/conflict checks.

**Allowed files:**
- `architecture/*manifest*.yaml`
- `architecture/topology_schema.yaml`
- topology_doctor ownership validator/tests
- module books for ownership explanation

**Forbidden files:**
- runtime/source behavior
- graph.db
- broad docs cleanup outside ownership surfaces

**Expected outputs:**
- ownership matrix
- owner_manifest issue metadata
- conflict checks
- reduced duplication where safe

**Tests / verification:**
- py_compile
- targeted ownership tests
- docs/source/test/script lanes

**Closeout commands:**
- strict or ownership lane JSON
- closeout on edited manifests

**Rollback:**
Revert schema/check additions first, then manifest edits.

**Lore commit message:**
`Topology P3: normalize manifest ownership and detect conflicting owners`

**Pre-close critic/verifier focus:** ownership and drift


## P4 — Context pack and graph extraction

**Objective:** Add derived graph/context textual extracts and context-pack integration without making graph authority.

**Allowed files:**
- `scripts/topology_doctor_code_review_graph.py`
- `scripts/topology_doctor_context_pack.py`
- graph/module books
- generated derived text sidecar if approved

**Forbidden files:**
- graph authority promotion
- custom graph refresh scripts
- runtime/source behavior
- unapproved graph.db staging

**Expected outputs:**
- graph usability/limitation section
- small textual graph appendix
- changed-file impacted tests/routes where graph usable

**Tests / verification:**
- graph status lane
- context-pack package_review/debug profile tests
- proof graph stale is advisory

**Closeout commands:**
- `python scripts/topology_doctor.py --code-review-graph-status --json`
- context-pack graph profile command

**Rollback:**
Remove generated sidecar/context-pack section and restore graph helper behavior.

**Lore commit message:**
`Topology P4: add graph-derived textual context while preserving non-authority boundaries`

**Pre-close critic/verifier focus:** graph authority safety


## P5 — Topology doctor test resegmentation

**Objective:** Split deterministic fixture tests from live repo-health tests and stabilize topology_doctor regression suite.

**Allowed files:**
- `tests/test_topology_doctor.py`
- pytest markers/config
- fixtures
- docs/module book validation notes

**Forbidden files:**
- source/runtime behavior
- broad manifest fixes to make tests pass artificially

**Expected outputs:**
- `live_topology` marked tests
- deterministic fixture tests for lane policy/issue model/ownership/graph/docs
- live health command list

**Tests / verification:**
- `pytest -q tests/test_topology_doctor.py -m "not live_topology"`
- `pytest -q tests/test_topology_doctor.py -m live_topology` as advisory/live health

**Closeout commands:**
- validation matrix commands
- closeout changed-files command

**Rollback:**
Restore previous test selection if marker split breaks collection; keep fixture additions if valid.

**Lore commit message:**
`Topology P5: split deterministic topology regressions from live repo health`

**Pre-close critic/verifier focus:** test semantics

## Packet order

Run P0 before P1. Run P1 before P3/P4. Run P2 can begin after P0 design stabilizes, but implementation should avoid contradicting P1 issue ownership. Run P5 after P0/P1 have enough stable behavior to fixture.
