You are working in the Zeus repo on branch `data-improve`.

Hard constraints:
- Do not change runtime/source behavior unless explicitly allowed.
- Do not treat graph as authority.
- Do not make archives default-read.
- Do not add broad manifests before ownership is decided.
- Keep topology outputs backward-compatible where possible.
- Run relevant topology_doctor commands and record evidence.

# Codex Prompt — P1 Execute Typed Issue Model

Read:
- `04_issue_model_and_lane_model_audit.md`
- `repair_blueprints/p1_issue_model_repair.md`
- P0 result/work log

Objective:
Extend topology issues with optional typed metadata while preserving old JSON keys.

Required fields:
- lane
- scope
- owner_manifest
- repair_kind
- blocking_modes
- related_paths
- maturity
- expires_at
- confidence
- repair_hint

Rules:
- Keep `code`, `path`, `message`, `severity`.
- Do not require every existing issue to be fully annotated in first pass.
- Annotate high-value issue families first.
- Render grouped repairs where possible.

Verification:
```bash
python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -k "issue or json or closeout or navigation"
python scripts/topology_doctor.py --navigation --task "issue model" --files scripts/topology_doctor.py --json
```
