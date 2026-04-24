# P2 Patch Blueprint

## Intent

Make the tracked Code Review Graph lane portable, transparent, and more useful to online reviewers without promoting it into authority.

## Exact target state

### `scripts/code_review_graph_mcp_readonly.py`

- remove hardcoded `DEFAULT_REPO_ROOT`
- resolve repo root via explicit env var, current repo, or CLI/constructor injection
- preserve Zeus read-only safety boundary
- where upstream CRG already supports tool filtering or repo-root env vars, prefer using that capability over bespoke path hacks

### `scripts/topology_doctor_code_review_graph.py`

- surface path mode more clearly (`absolute`, `repo_relative`, `mixed`)
- optionally read `graph_meta.json` if present
- make graph usability summaries easier for online review consumption
- keep `derived_code_impact_not_authority` label intact

### `scripts/topology_doctor_context_pack.py`

If `graph_meta.json` exists, include a compact graph summary section in context-pack output:

- graph usable?
- warnings
- path mode
- generated_at
- head/branch parity
- file/node/edge counts

### `.code-review-graph/graph_meta.json`

Add only if local verification is strong enough.
Suggested shape:

```json
{
  "schema_version": 1,
  "generated_at": "...",
  "git_head": "...",
  "git_branch": "...",
  "graph_schema": "...",
  "builder_version": "...",
  "path_mode": "absolute|repo_relative|mixed",
  "counts": {"files": 0, "nodes": 0, "edges": 0, "flows": 0, "communities": 0},
  "usability": {"ok": true, "warnings": []}
}
```

### `.gitignore` and `.code-review-graph/.gitignore`

If sidecar is adopted, unignore it explicitly while preserving “everything else local” policy.

### `architecture/artifact_lifecycle.yaml` and `architecture/topology.yaml`

Classify the sidecar as tracked derived online context.
Do not blur it into authority.

### `tests/test_topology_doctor.py`

Add tests for:

- wrapper repo-root portability
- path-mode disclosure
- graph-meta parity or presence rules if sidecar is introduced

## Acceptance criteria

- no hardcoded workstation root remains in the CRG wrapper
- graph status clearly discloses usability and path mode
- optional sidecar gives online humans a fast trust read
- graph remains non-authority throughout

## Local safety note

**LOCAL_VERIFICATION_REQUIRED:** do not regenerate and commit graph artifacts until you confirm the real local builder path, version, and outputs.
