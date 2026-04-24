# Phase Plan — P4: Context Pack and Graph Extraction

**Companion to:** `../repair_blueprints/p4_context_pack_and_graph_extraction.md`,
`../prompts/codex_p4_graph_and_context_pack.md`,
`../07_context_pack_and_graph_integration_audit.md`,
`../validation/graph_context_validation.md`,
`../MAIN_ROUTE_IMPLEMENTATION_PLAN.md` §3 (P4 row).

## 1. Goal restated

Make the Code Review Graph useful to online-only agents through small
derived textual extracts, while keeping graph output strictly
non-authoritative. The tracked `.code-review-graph/graph.db` is binary
and unreadable online; today
`scripts/topology_doctor_code_review_graph.py` (546 lines) and
`scripts/topology_doctor_context_pack.py` (1,058 lines) do not emit a
textual sidecar that an online agent can consume directly.

After P4, the graph status output and context-pack output include a
small, freshness-marked, advisory graph appendix that an online agent
can read without opening the binary.

## 2. Anchor points

| What | Where |
|------|-------|
| Graph helper | `scripts/topology_doctor_code_review_graph.py` (546 lines) |
| Context pack helper | `scripts/topology_doctor_context_pack.py` (1,058 lines) |
| Graph protocol manifest | `architecture/code_review_graph_protocol.yaml` |
| Context pack profiles | `architecture/context_pack_profiles.yaml` |
| Graph status CLI | `scripts/topology_doctor_cli.py:50` (`--code-review-graph-status`) |
| Code Review Graph book | `docs/reference/modules/code_review_graph.md` (post-P2 expanded) |

## 3. Pre-decisions (resolves OQ-4 and OQ-5)

- **OQ-4 decision**: graph-derived textual sidecars are emitted
  *only* in CLI/JSON output (graph status and context-pack), never
  committed as files. This satisfies "graph is derived, not authority"
  and avoids a stale committed snapshot.
- **OQ-5 decision**: per-appendix size budget = 2 KB
  (`GRAPH_APPENDIX_BUDGET_BYTES = 2048`). When the appendix would
  exceed this, emit a truncation marker and a `repair_hint` directing
  to run the graph CLI directly.
- **Graph appendix payload** (machine-readable shape):
  ```yaml
  authority_status: derived_not_authority
  graph_freshness: {fresh|stale|missing}
  graph_freshness_reason: <string>
  limitations: [<short strings>]
  changed_nodes: [<short refs>]   # may be omitted if no changed-files
  likely_tests: [<test_ids>]
  impacted_files: [<paths>]
  missing_coverage: [<short strings>]
  truncation: {applied: bool, hint: <string>}
  ```
- **Blocking semantics**: stale or missing graph is **always advisory**
  except when the task profile in `architecture/context_pack_profiles.yaml`
  declares `requires_graph_evidence: true`. None of the existing
  profiles set this today; introducing the field is an enabling change
  but no profile flips to true in P4.

## 4. Ordered atomic todos

1. **Add appendix builder** in
   `scripts/topology_doctor_code_review_graph.py`:
   - `build_graph_appendix(files: list[str], task: str | None) -> dict`
   - Reads graph status + impacted nodes for the requested files.
   - Always sets `authority_status="derived_not_authority"`.
   - Honors `GRAPH_APPENDIX_BUDGET_BYTES`.
2. **Wire appendix into `--code-review-graph-status`** output:
   include `appendix` key in JSON when `--files` is supplied.
3. **Wire appendix into context-pack** output in
   `scripts/topology_doctor_context_pack.py`:
   - For every profile that mentions code review or task routing,
     attach `graph_appendix` field.
   - When the profile sets `requires_graph_evidence: true` (after this
     enabling change lands), stale/missing graph escalates to error.
4. **Add `requires_graph_evidence` schema field** to
   `architecture/context_pack_profiles.yaml` (default `false`). Add a
   schema check in P3's ownership validator extension or in
   `scripts/topology_doctor_policy_checks.py`.
5. **Use P1 typed issues** for all graph-related signals:
   `authority_status="derived"`, `repair_kind="refresh_graph"`,
   `blocking_modes={"global_health"}` by default.
6. **Update `code_review_graph.md`** (already expanded in P2):
   document the appendix schema, the budget, and the
   `requires_graph_evidence` opt-in.
7. **Tests**:
   - `test_graph_appendix_marks_derived_not_authority()`
   - `test_graph_appendix_respects_size_budget()`
   - `test_graph_appendix_stale_is_advisory_by_default()`
   - `test_graph_appendix_stale_blocks_when_required_by_profile()`
   - `test_context_pack_includes_graph_appendix_for_code_review_profile()`
   - `test_context_pack_handles_missing_graph_db_gracefully()`
8. **Validation matrix row** for P4.

## 5. Verification

```bash
python3 -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -k "graph or context_pack or appendix"
python3 scripts/topology_doctor.py --code-review-graph-status --json
python3 scripts/topology_doctor.py --code-review-graph-status --files scripts/topology_doctor.py --json
python3 scripts/topology_doctor.py context-pack --profile package_review --files scripts/topology_doctor.py --json
```

## 6. Definition of done

- `--code-review-graph-status --files ...` emits an appendix with the
  shape in §3 and respects the 2 KB budget.
- Context-pack output for code-review profiles includes a
  `graph_appendix` field carrying the same shape.
- Stale/missing graph stays advisory unless
  `requires_graph_evidence: true` on the profile.
- No code path treats graph data as authority — verified by
  `test_graph_appendix_marks_derived_not_authority`.
- `code_review_graph.md` documents the appendix schema and budget.
- Six new tests pass.
- Validation matrix row green.

## 7. Rollback

- Revert order: tests + appendix wiring + appendix builder + schema
  field. The graph helper module returns to its pre-P4 surface
  cleanly; nothing depends on the appendix outside the new code paths.

## 8. Critic focus

- Did the appendix become a parallel authority surface? It must not.
- Does the budget enforce a hard ceiling, or is it advisory text?
  (Hard ceiling required.)
- Could a graph regression silently blank the appendix? Test
  `test_context_pack_handles_missing_graph_db_gracefully` covers this.

## 9. Risks specific to P4

- **R-P4-1**: Future profiles flip `requires_graph_evidence: true`
  carelessly. Mitigation: P3-class ownership conflict — adding this
  flag must be reviewed against the schema check from step 4.
- **R-P4-2**: Appendix becomes a UX crutch and grows past 2 KB.
  Mitigation: budget is a constant in code, not a manifest field.
- **R-P4-3**: Custom graph refresh logic creeps in. Forbidden by
  `14_not_now.md`; the code reads the official cache only.

## 10. Lore commit message

`Topology P4: add graph-derived textual context while preserving non-authority boundaries`
