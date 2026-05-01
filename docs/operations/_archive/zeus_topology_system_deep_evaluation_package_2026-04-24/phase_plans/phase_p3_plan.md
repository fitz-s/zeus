# Phase Plan — P3: Manifest Ownership Normalization

**Companion to:** `../repair_blueprints/p3_manifest_ownership_normalization.md`,
`../prompts/codex_p3_normalize_manifest_ownership.md`,
`../05_manifest_ownership_audit.md`,
`../MAIN_ROUTE_IMPLEMENTATION_PLAN.md` §3 (P3 row).

## 1. Goal restated

Make manifest ownership explicit, declared, and enforced. Today
`architecture/topology.yaml`, `docs_registry.yaml`,
`module_manifest.yaml`, `source_rationale.yaml`, `test_topology.yaml`,
`script_manifest.yaml`, `map_maintenance.yaml` overlap without an
explicit ownership contract. After P3, each *fact type* has exactly
one canonical owner manifest, and `topology_doctor` enforces that
contract.

The audit data and prose-level matrix already exist:

- Audit prose: `../05_manifest_ownership_audit.md`
- Machine-readable matrix: `16_machine_readable_summary.json`
  field `manifest_ownership`
- P2 already wrote the human-readable version into
  `docs/reference/modules/manifests_system.md`

P3 promotes that matrix to enforceable schema + validator.

## 2. Anchor points

| What | Where |
|------|-------|
| Schema file | `architecture/topology_schema.yaml` |
| Manifest files | `architecture/{topology,docs_registry,module_manifest,source_rationale,test_topology,script_manifest,map_maintenance,context_budget,artifact_lifecycle,code_review_graph_protocol,history_lore,invariants,negative_constraints}.yaml` |
| Existing registry/source/test/script checks | `scripts/topology_doctor_{registry,source,test,script}_checks.py` |
| Issue producer base (P1 typed) | `scripts/topology_doctor.py:71` (post-P1 dataclass) |
| Manifests system book | `docs/reference/modules/manifests_system.md` (post-P2) |

## 3. Pre-decisions (resolves OQ-2 and OQ-13)

- **OQ-2 decision**: add `maturity` field to `module_manifest.yaml`
  (`stable | provisional | placeholder`). Do **not** add
  `canonical_owner_refs` at module-level; instead, declare ownership
  in `topology_schema.yaml` keyed by *fact type*, not by file. This
  centralizes ownership and avoids module-manifest bloat.
- **OQ-13 decision**: when `docs_registry` and `module_manifest`
  disagree about a module book, `docs_registry` is authoritative for
  *doc classification* (class, default_read, freshness) and
  `module_manifest` is authoritative for *module routing* (book pointer,
  module hazards). Conflict on either side → blocking issue with
  `repair_kind: propose_owner_manifest` and a clear hint pointing to
  the canonical owner.
- **Fact-type taxonomy** (initial set; extensible):
  `doc_classification`, `module_routing`, `source_rationale`,
  `test_category_and_law_gate`, `script_lifecycle_and_safety`,
  `companion_update_rule`, `context_budget_posture`,
  `artifact_class`, `graph_protocol`, `history_lore_card`,
  `invariant_definition`, `negative_constraint`.

## 4. Ordered atomic todos

1. **Add `ownership` section** to `architecture/topology_schema.yaml`:
   For each fact type from §3, declare:
   - `canonical_owner: <manifest path>`
   - `derived_owners: [<manifest paths that may reference but not redefine>]`
   - `companion_update_rule: <link to map_maintenance section>`
2. **Add `maturity` field** schema to `module_manifest.yaml` (allowed
   values `stable | provisional | placeholder`); set existing rows to
   their best-current value (default `provisional` if unclear).
3. **Build the ownership validator** as a new helper module
   `scripts/topology_doctor_ownership_checks.py`:
   - Load `architecture/topology_schema.yaml` ownership section.
   - For each fact type, walk all manifests and detect rows that
     *redefine* (vs reference) a fact type they do not canonically own.
   - Emit P1-typed issues with
     `owner_manifest=<canonical>`,
     `repair_kind=propose_owner_manifest`,
     `blocking_modes={"strict_full_repo", "closeout"}` when changed-file
     scope intersects, otherwise `{"global_health"}` only.
   - Emit `confidence` field reflecting how unambiguous the owner is.
4. **Wire the validator** into the existing strict pipeline near
   `scripts/topology_doctor.py:368` (`run_strict`). Add a CLI flag
   `--ownership` for direct invocation.
5. **Promote `owner_manifest` requirement** for the seven first-wave
   issue families annotated in P1: blocking issues without
   `owner_manifest` now fail the `--ownership` lane (warning-only
   for the first run; promote to error after one clean cycle —
   resolves part of OQ-6).
6. **Mark known duplicate references** as derived/link-only:
   - In `architecture/topology.yaml`, replace any per-file rationale
     content with pointers to `source_rationale.yaml`.
   - In `architecture/module_manifest.yaml`, ensure book pointers
     reference `docs_registry.yaml` rows rather than restating
     `default_read` / `freshness_class`.
   - **Do not delete** any data without an owner reassignment plan
     and a passing test for the conflict path.
7. **Add tests** in `tests/test_topology_doctor.py`:
   - `test_ownership_matrix_loadable_from_schema()`
   - `test_two_canonical_owners_for_same_fact_type_blocks()`
   - `test_blocking_issue_without_owner_manifest_raises()` (after
     promotion)
   - `test_module_manifest_maturity_field_validated()`
   - `test_doc_classification_owned_only_by_docs_registry()`
   - `test_module_routing_owned_only_by_module_manifest()`
8. **Update `manifests_system.md`** to point at the schema section as
   the canonical machine source for the matrix it describes (close the
   loop between P2 prose and P3 enforcement).
9. **Validation matrix row** for P3.

## 5. Verification

```bash
python3 -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -k "ownership or manifest or maturity"
python3 scripts/topology_doctor.py --ownership --json
python3 scripts/topology_doctor.py --strict --json
python3 scripts/topology_doctor.py closeout --changed-files architecture/topology_schema.yaml architecture/module_manifest.yaml --summary-only
```

## 6. Definition of done

- `architecture/topology_schema.yaml` has an `ownership` section
  covering all 12 fact types in §3.
- `module_manifest.yaml` carries `maturity` on every row.
- `--ownership` lane runs cleanly on the baseline commit.
- No two manifests claim canonical ownership of the same fact type.
- All seven first-wave issue families have `owner_manifest` populated
  on every blocking issue they emit.
- Six new ownership tests pass.
- `manifests_system.md` cross-links the schema section.
- Validation matrix row green.

## 7. Rollback

Order matters: revert the validator + schema-additions + tests first
(safe, additive), then any manifest *content* edits second (riskier).
The blueprint explicitly cautions against deleting manifest data
without owner reassignment — the rollback respects this.

## 8. Critic focus

- Is ownership real or cosmetic? Show one fact type whose conflict
  the validator now catches that the previous repo did not.
- Are derived references obviously distinguished from canonical
  declarations in YAML?
- Did any data get deleted? If yes, where is the owner-reassignment
  test?

## 9. Risks specific to P3

- **R-P3-1**: P1's first-wave annotations carry incorrect
  `owner_manifest` values. Mitigation: P3 step 5 begins as warning-only
  and promotes to error only after a clean run.
- **R-P3-2**: `topology.yaml` cleanup over-routes after pointers
  replace inline content. Mitigation: keep the digest profile tests
  as a smoke gate — `python3 scripts/topology_doctor.py --navigation`
  must still build a digest for every existing task profile.
- **R-P3-3**: Schema becomes a dumping ground. Mitigation: any new
  fact type entry requires a corresponding section in
  `manifests_system.md`.

## 10. Lore commit message

`Topology P3: normalize manifest ownership and detect conflicting owners`
