# P1 Issue Model Repair Blueprint

## Objective

Make topology issues machine-routable while preserving backward compatibility.

## Required fields

Add optional fields:

- `lane`
- `scope`
- `owner_manifest`
- `repair_kind`
- `blocking_modes`
- `related_paths`
- `companion_of`
- `maturity`
- `expires_at`
- `confidence`
- `authority_status`
- `repair_hint`

## Compatibility rule

Do not remove or rename:

- `code`
- `path`
- `message`
- `severity`

## Implementation steps

1. Extend `TopologyIssue`.
2. Add issue factories:
   - `issue(...)`
   - `warning(...)`
   - `advisory(...)`
   - `blocking(...)`
   - `global_drift(...)`
3. Update JSON serialization.
4. Update renderers to group by:
   - severity,
   - blocking mode,
   - owner manifest,
   - repair kind.
5. Annotate high-value issue families first:
   - docs registry,
   - source rationale,
   - test topology,
   - script manifest,
   - map maintenance,
   - graph,
   - module books.
6. Leave unannotated issues valid but mark `owner_manifest: unknown` or omit field.

## Tests

- JSON has old keys.
- New fields appear for annotated issues.
- Closeout can use `blocking_modes`.
- Renderer groups repair actions.
- Unknown fields do not break old consumers.

## Verification

```bash
python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -k "issue or json or closeout or navigation"
```

## Rollback

Keep old issue factory path as fallback. Remove policy use of typed fields before reverting dataclass if needed.
