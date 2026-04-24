# Code Review Graph Topology Bridge Plan

Date: 2026-04-19
Branch: data-improve

## Objective

Integrate Code Review Graph as a Zeus-safe derived code-impact sensor without
letting its graph-first defaults bypass topology routing, planning-lock,
manifests, route receipts, or canonical truth rules.

## Decision

Supersession note (2026-04-20): the original P0 decision classified
`.code-review-graph/` as a local scratch cache. That is superseded for
`.code-review-graph/graph.db` by
`docs/operations/task_2026-04-20_code_review_graph_online_context/plan.md`,
which tracks the DB as a derived online-context artifact for Pro/review agents.
The non-authority rule remains unchanged.

Implement P0/P1 only in this packet:

- Classify `.code-review-graph/` byproducts as local scratch/derived diagnostic cache.
- Add a warning-first `topology_doctor --code-review-graph-status` lane.
- Make repository hygiene blocking when `graph.db` is tracked or no ignore guard exists.
- Expose Codex through a Zeus-owned MCP facade that omits source-writing `apply_refactor_tool`.
- Add a short Claude repo instruction that topology comes before graph tools.

## Non-Goals

- Do not add code-impact context-pack appendix yet.
- Do not enable stock `code-review-graph serve`, because it exposes source-writing tools.
- Do not run installer instruction injection into AGENTS/CLAUDE surfaces.
- Do not treat graph risk scores as closeout authority.
