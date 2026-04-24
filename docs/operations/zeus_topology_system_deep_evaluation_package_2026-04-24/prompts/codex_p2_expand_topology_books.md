You are working in the Zeus repo on branch `data-improve`.

Hard constraints:
- Do not change runtime/source behavior unless explicitly allowed.
- Do not treat graph as authority.
- Do not make archives default-read.
- Do not add broad manifests before ownership is decided.
- Keep topology outputs backward-compatible where possible.
- Run relevant topology_doctor commands and record evidence.

# Codex Prompt — P2 Expand Topology Books

Read:
- `09_module_book_density_audit.md`
- `10_topology_system_material_extraction_plan.md`
- `module_books_expansion/*.md`

Objective:
Rehydrate topology-system cognition into durable reference-only module/system books.

Allowed files:
- `docs/reference/modules/*.md`
- `docs/reference/AGENTS.md`
- `docs/reference/modules/AGENTS.md`
- `architecture/docs_registry.yaml`
- `architecture/module_manifest.yaml`

Requirements:
1. Keep all books reference-only.
2. Do not put current packet status in module books.
3. Register added books in docs registry and module manifest.
4. Explain ownership, hidden obligations, false assumptions, validation, and planning-lock triggers.
5. Do not make archives default-read.

Verification:
```bash
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py context-pack --profile package_review --files docs/reference/modules/topology_system.md --json
pytest -q tests/test_topology_doctor.py -k "module or docs"
```
