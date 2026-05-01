You are working in the Zeus repo on branch `data-improve`.

Hard constraints:
- Do not change runtime/source behavior unless explicitly allowed.
- Do not treat graph as authority.
- Do not make archives default-read.
- Do not add broad manifests before ownership is decided.
- Keep topology outputs backward-compatible where possible.
- Run relevant topology_doctor commands and record evidence.

# Codex Prompt — P4 Graph and Context Pack Extraction

Read:
- `07_context_pack_and_graph_integration_audit.md`
- `module_books_expansion/code_review_graph_expanded.md`
- `repair_blueprints/p4_context_pack_and_graph_extraction.md`

Objective:
Add graph-derived textual context for online-only agents while preserving graph as derived/non-authority.

Allowed files:
- `scripts/topology_doctor_code_review_graph.py`
- `scripts/topology_doctor_context_pack.py`
- `docs/reference/modules/code_review_graph.md`
- tests
- optional approved generated sidecar path

Requirements:
1. Graph output includes `authority_status: derived_not_authority`.
2. Context packs include graph freshness/usability/limitations.
3. Stale/missing graph is advisory unless task explicitly requires graph.
4. Use official graph commands; do not invent refresh scripts.
5. Do not stage graph.db changes unless explicitly authorized.

Verification:
```bash
python scripts/topology_doctor.py --code-review-graph-status --json
python scripts/topology_doctor.py context-pack --profile package_review --files <files> --json
pytest -q tests/test_topology_doctor.py -k "graph or context_pack"
```
