# Code Review Graph Topology Bridge Plan

Date: 2026-04-19
Branch: data-improve

## Objective

Integrate Code Review Graph as a Zeus-safe derived code-impact sensor without
letting its graph-first defaults bypass topology routing, planning-lock,
manifests, route receipts, or canonical truth rules.

## Decision

Implement P0/P1 only in this packet:

- Classify `.code-review-graph/` as local scratch/derived diagnostic cache.
- Add a warning-first `topology_doctor --code-review-graph-status` lane.
- Make repository hygiene blocking when `graph.db` is tracked or no ignore guard exists.
- Expose Codex through a Zeus-owned MCP facade that omits source-writing `apply_refactor_tool`.
- Add a short Claude repo instruction that topology comes before graph tools.

## Non-Goals

- Do not add code-impact context-pack appendix yet.
- Do not enable stock `code-review-graph serve`, because it exposes source-writing tools.
- Do not run installer instruction injection into AGENTS/CLAUDE surfaces.
- Do not treat graph risk scores as closeout authority.
