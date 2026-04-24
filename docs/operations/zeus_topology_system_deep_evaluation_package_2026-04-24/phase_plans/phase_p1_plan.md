# Phase Plan — P1: Typed Issue Model

**Companion to:** `../repair_blueprints/p1_issue_model_repair.md`,
`../prompts/codex_p1_execute_issue_model.md`,
`../MAIN_ROUTE_IMPLEMENTATION_PLAN.md` §3 (P1 row).

## 1. Goal restated

Extend the topology issue object so it can drive routing, gating,
repair drafts, and ownership — without breaking any existing JSON
consumer. Today `scripts/topology_doctor.py:71-76`:

```
@dataclass(frozen=True)
class TopologyIssue:
    code: str
    path: str
    message: str
    severity: str = "error"
```

After P1, the dataclass carries optional metadata
(`lane`, `scope`, `owner_manifest`, `repair_kind`, `blocking_modes`,
`related_paths`, `companion_of`, `maturity`, `expires_at`,
`confidence`, `authority_status`, `repair_hint`) and JSON output
preserves the four legacy keys.

## 2. Anchor points in current code

| What | Where |
|------|-------|
| `TopologyIssue` dataclass | `scripts/topology_doctor.py:71-76` |
| Issue serialization (`asdict`) in navigation | `scripts/topology_doctor.py:1098-1101` |
| Issue serialization in `_print_strict` | `scripts/topology_doctor.py:1153-1178` |
| `format_issues` / `summarize_issues` renderers | `scripts/topology_doctor.py:1131-1150` |
| Issue producers across helpers | every `scripts/topology_doctor_*_checks.py` (≈20 files) |

## 3. Pre-decisions (resolves OQ-1)

- **OQ-1 decision**: typed schema lives in **two** places.
  - The Python dataclass remains the canonical runtime contract.
  - A new section in `architecture/topology_schema.yaml` describes the
    *JSON contract* (field names, types, allowed values) so external
    consumers and module books can cite it.
  - This is additive. No existing schema rows change.
- **Compatibility rule**: `code`, `path`, `message`, `severity` keep
  their names, types, and ordering in JSON output. New fields are
  emitted only when set (no `null` flooding); add a `--issue-schema-version`
  CLI flag defaulting to `"1"` (legacy) and `"2"` for typed output.
  Default stays `"1"` until P3 needs `owner_manifest` enforcement.
- **Annotated families first** (per blueprint §5): `docs_registry`,
  `source_rationale`, `test_topology`, `script_manifest`,
  `map_maintenance`, `code_review_graph`, `module_books`. Other lanes
  emit issues with `owner_manifest` omitted (treated as `unknown`).

## 4. Ordered atomic todos

1. **Extend dataclass** at `scripts/topology_doctor.py:71`:
   - Convert to `@dataclass(frozen=True)` with new optional fields,
     all `= None` or sensible defaults. Keep `frozen=True` to preserve
     hashability.
   - Add module-level enums (or string constants) for `repair_kind`
     (`add_registry_row`, `update_companion`, `extract_law_to_book`,
     `propose_owner_manifest`, `refresh_graph`, `none`),
     `maturity` (`stable`, `provisional`, `placeholder`),
     `authority_status` (`authority`, `derived`, `evidence`, `unknown`).
2. **Add issue factories** near the dataclass:
   - `issue(...)` (default `severity="error"`),
   - `warning(...)`, `advisory(...)`, `blocking(...)`,
   - `global_drift(...)` (sets `blocking_modes={"global_health"}`,
     `severity="warning"` for navigation/closeout).
   - Provide `legacy_issue(code, path, message, severity="error")`
     that returns a TopologyIssue with all new fields unset; this is
     the safe fallback path for the rollback plan.
3. **Add JSON serializer** `_issue_to_json(issue, schema_version)` that:
   - Always emits the four legacy keys.
   - When `schema_version == "2"`, emits any new field whose value is
     not None.
   - Used by `run_navigation`, `_print_strict`, closeout output.
4. **Wire the renderers** at `scripts/topology_doctor.py:1131-1178`:
   - `format_issues` gains optional grouping by `repair_kind` /
     `owner_manifest` when typed fields are present.
   - `summarize_issues` adds counts by `repair_kind` and
     `owner_manifest` when present.
5. **Update lane policy from P0** to consume `blocking_modes` when
   present. Behavior preservation rule: if `blocking_modes` is unset,
   fall back to the P0 lane allow-list.
6. **Annotate first wave of issue producers**:
   - `scripts/topology_doctor_registry_checks.py` (docs_registry)
   - `scripts/topology_doctor_source_checks.py` (source_rationale)
   - `scripts/topology_doctor_test_checks.py` (test_topology)
   - `scripts/topology_doctor_script_checks.py` (script_manifest)
   - `scripts/topology_doctor_map_maintenance.py` (map_maintenance)
   - `scripts/topology_doctor_code_review_graph.py`
     (code_review_graph_protocol)
   - `scripts/topology_doctor_docs_checks.py` (module books subset)
   Each annotation sets at minimum `owner_manifest`, `repair_kind`,
   `blocking_modes`. Leave `expires_at`, `confidence`,
   `repair_hint` for follow-up.
7. **Add schema YAML section** to `architecture/topology_schema.yaml`
   describing the issue JSON contract (field names, types, allowed
   values). This section is reference data; add a corresponding
   `topology_doctor` check that the dataclass field set and the YAML
   field set are equal (drift guard).
8. **Tests** (additive, fixture-driven):
   - `test_issue_legacy_json_keys_preserved()`
   - `test_issue_v2_emits_owner_manifest_when_present()`
   - `test_issue_v1_omits_new_fields()`
   - `test_issue_factories_set_blocking_modes()`
   - `test_renderer_groups_by_repair_kind()`
   - `test_blocking_modes_drives_lane_policy()`
   - `test_issue_schema_drift_guard()`
9. **Compile + targeted pytest.**
10. **Validation matrix row** for P1.

## 5. Verification

```bash
python3 -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -k "issue or json or factory or schema or blocking_modes"
python3 scripts/topology_doctor.py --navigation --task "p1 schema check" --files scripts/topology_doctor.py --json | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'issues' in d; print('legacy keys ok')"
python3 scripts/topology_doctor.py --navigation --issue-schema-version 2 --task "p1 v2 check" --files scripts/topology_doctor.py --json | python3 -c "import sys,json; d=json.load(sys.stdin); print('v2 ok')"
```

## 6. Definition of done

- All four legacy keys still present in default-mode JSON.
- v2 mode emits typed metadata for at least the seven first-wave
  families.
- P0 lane policy now consumes `blocking_modes` when present and
  behaves identically when absent.
- Drift guard test enforces equality between Python dataclass fields
  and `topology_schema.yaml` issue contract field set.
- No existing test removed or weakened.
- Validation matrix row green.

## 7. Rollback

- Step 1 (cheap): switch default `--issue-schema-version` permanently
  to `"1"` and drop the v2 branch in `_issue_to_json`.
- Step 2 (full): revert the `TopologyIssue` extension and replace all
  factory calls with `legacy_issue(...)`. The compatibility factory
  exists exactly to make this revert mechanical.

## 8. Critic focus

- Are any new fields *required* for legacy consumers? They must not be.
- Is the schema drift guard a real test, or a tautology?
- Does `blocking_modes` consumption respect P0's mode taxonomy?

## 9. Risks specific to P1

- **R-P1-1**: First-wave annotation introduces incorrect
  `owner_manifest` values that get cited by P3. Mitigation: P3 begins
  with an audit pass over annotated values before its validator is
  enabled.
- **R-P1-2**: Renderers regress human-readable output. Mitigation:
  golden-output tests for `format_issues`/`summarize_issues` on a
  small fixture set.
- **R-P1-3**: `frozen=True` + many optional fields → noisy `__repr__`.
  Mitigation: keep `repr=False` on optional fields if needed.

## 10. Lore commit message

`Topology P1: add typed issue metadata and mode-aware blocking`
