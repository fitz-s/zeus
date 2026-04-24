You are working in the Zeus repo on branch `data-improve`.

Hard constraints:
- Do not change runtime/source behavior unless explicitly allowed.
- Do not treat graph as authority.
- Do not make archives default-read.
- Do not add broad manifests before ownership is decided.
- Keep topology outputs backward-compatible where possible.
- Run relevant topology_doctor commands and record evidence.

# Codex Prompt — P0 Execute Topology Lane Repair

Read first:
- `AGENTS.md`
- `workspace_map.md`
- `docs/operations/current_state.md`
- `docs/reference/modules/topology_system.md`
- this package: `00_executive_ruling.md`, `04_issue_model_and_lane_model_audit.md`, `repair_blueprints/p0_scope_and_lane_repair.md`

Objective:
Separate navigation, closeout, strict, and global health blocking behavior.

Allowed files:
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_closeout.py`
- `tests/test_topology_doctor.py`

Implementation requirements:
1. Navigation should return a route digest when unrelated global drift exists.
2. Navigation should distinguish direct blockers from repo-health warnings.
3. Strict/global health behavior must remain available.
4. Closeout should block changed-file obligations and companions.
5. Do not hide global drift; report it separately.

Verification:
```bash
python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -k "navigation or closeout"
python scripts/topology_doctor.py --navigation --task "topology lane repair" --files scripts/topology_doctor.py --json
python scripts/topology_doctor.py closeout --changed-files scripts/topology_doctor.py tests/test_topology_doctor.py --summary-only
```

Close with a short work log: changed files, behavior changes, verification, residual risks.
