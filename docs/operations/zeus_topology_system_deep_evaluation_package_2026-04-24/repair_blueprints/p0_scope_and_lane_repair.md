# P0 Scope and Lane Repair Blueprint

## Objective

Separate navigation, closeout, strict, and global health behavior. The goal is not to hide drift. The goal is to make each mode block only for the correct reason.

## Current failure to fix

Navigation acts too much like full repo health. Closeout has partial changed-file scoping but not a complete policy model.

## Allowed files

- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_closeout.py`
- `tests/test_topology_doctor.py`

## Forbidden files

- `src/**`
- runtime DB/state files
- `.code-review-graph/graph.db`
- broad `architecture/**` manifest rewrites
- docs archives

## Implementation steps

1. Add a mode policy table or small helper:
   - `navigation`
   - `navigation_strict_health`
   - `closeout`
   - `strict_full_repo`
   - `global_health`
2. Split navigation output into:
   - `direct_blockers`
   - `route_context`
   - `repo_health_warnings`
   - `global_health_counts`
3. For navigation, block only when:
   - required route surfaces unreadable,
   - requested files cannot be classified,
   - requested file has directly relevant blocking issue,
   - semantic boot/current fact required for requested task is missing.
4. For closeout, evaluate:
   - changed files,
   - required companions from map maintenance,
   - planning/work/receipt requirements,
   - selected lanes by changed-file class,
   - global health sidecar.
5. Keep `--strict` or `--navigation --strict-health` behavior for full blocking.
6. Add tests:
   - unrelated docs drift does not block source navigation,
   - unrelated source drift does not block docs closeout,
   - changed source missing source rationale still blocks,
   - changed module book missing module_manifest/docs_registry companions blocks,
   - global strict still sees all errors.

## Expected output

Navigation JSON should include a digest even with repo-health warnings. Closeout JSON should show scoped blockers and unrelated warnings separately.

## Verification

```bash
python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -k "navigation or closeout"
python scripts/topology_doctor.py --navigation --task "topology lane repair" --files scripts/topology_doctor.py --json
python scripts/topology_doctor.py closeout --changed-files scripts/topology_doctor.py tests/test_topology_doctor.py --summary-only
```

## Rollback

Revert the lane policy helper and tests as a unit.

## Critic focus

- Does navigation still surface important drift as warnings?
- Does closeout still block changed-file obligations?
- Did the patch hide global health rather than separate it?
