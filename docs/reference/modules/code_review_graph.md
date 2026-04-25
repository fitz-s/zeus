# Code Review Graph

> Status: reference, not authority. See `architecture/code_review_graph_protocol.yaml` for graph-use law.

## Purpose

The Code Review Graph is derived structural context for Zeus. It helps reviewers find dependencies, likely impacted tests, hub files, and review order, but it never proves semantic truth or current operational validity.

## Authority anchors

- `architecture/code_review_graph_protocol.yaml` defines the two-stage graph protocol.
- `.code-review-graph/graph.db` is tracked derived context, not law.
- `.code-review-graph/README.md` describes local graph artifact posture.
- `scripts/topology_doctor_code_review_graph.py` checks graph status.
- `scripts/topology_doctor_context_pack.py` may include bounded graph appendices.

## How it works

Graph usage has two stages. Stage 1 is semantic boot: identify task class, authority surfaces, fatal misreads, and current-fact requirements. Stage 2 is graph context: use structural relationships to reduce search cost and improve review coverage. Freshness/coverage issues are advisory unless a task profile explicitly requires graph evidence.

## Hidden obligations

- Graph output must be labeled `derived_not_authority` or equivalent when included in context packs.
- A stale or missing graph cannot waive tests, topology navigation, or planning lock.
- Graph edges do not determine settlement semantics, source truth, calibration identity, or lifecycle legality.
- Textual extracts must be bounded; the binary graph is not a default human-readable context source.

## Failure modes

- Structural proximity is mistaken for semantic ownership.
- Online-only agents see that graph exists but cannot inspect the binary and therefore miss useful context.
- Stale graph status is either over-promoted into a blocker or ignored entirely.
- A custom graph refresh path is invented instead of using official upstream graph operations.

## Repair routes

- Use `refresh_graph` only for graph freshness/coverage issues.
- Use context-pack graph appendices for small derived explanations, not authority claims.
- Keep graph checks advisory unless task boot profiles require them.
- Prefer official `code-review-graph status/update/watch` operations over repo-local inventions.

## Cross-links

- `docs/reference/modules/topology_system.md`
- `docs/reference/modules/topology_doctor_system.md`
- `architecture/code_review_graph_protocol.yaml`
- `.code-review-graph/README.md`
