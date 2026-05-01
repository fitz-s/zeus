You are working in the Zeus repo on branch `data-improve`.

Hard constraints:
- Do not change runtime/source behavior unless explicitly allowed.
- Do not treat graph as authority.
- Do not make archives default-read.
- Do not add broad manifests before ownership is decided.
- Keep topology outputs backward-compatible where possible.
- Run relevant topology_doctor commands and record evidence.

# Codex Prompt — P3 Normalize Manifest Ownership

Read:
- `05_manifest_ownership_audit.md`
- `module_books_expansion/manifests_system_expanded.md`
- `repair_blueprints/p3_manifest_ownership_normalization.md`

Objective:
Make canonical manifest ownership explicit and add conflict/duplicate ownership checks.

Allowed files:
- selected `architecture/*.yaml`
- `architecture/topology_schema.yaml`
- `scripts/topology_doctor*.py`
- `tests/test_topology_doctor.py`
- `docs/reference/modules/manifests_system.md`

Requirements:
1. One canonical owner per fact type.
2. Blocking issues name `owner_manifest`.
3. Repeated data is labeled derived/link-only.
4. Add conflict tests.
5. Do not create a new registry.

Verification:
```bash
python scripts/topology_doctor.py --strict --json
pytest -q tests/test_topology_doctor.py -k "ownership or manifest"
```
