# Live Math Frontier Repair Plan

## Scope

Implement the current `task.md` short-term recovery slice for the latest live finding:
`day0_capture` reached evaluator with one candidate, but `MarketAnalysis.find_edges()`
returned zero edges before family, snapshot, reprice, or final-intent paths ran.

## Invariants

- Do not lower CI, FDR, economic floors, model-conflict gates, or force live orders.
- Preserve existing generic ENS bootstrap behavior for non-Day0 modes.
- Day0 edge confidence must sample the same observation-fused probability object as Day0 `p_raw`.
- `edges=0` must carry frontier evidence: raw edge, CI, executable mask, and native NO quote availability classes.
- Source-health writer freshness is observability evidence, not a live trading hard blocker in this patch.

## Implementation

- Add `MarketAnalysis.find_edges_with_trace()` and `EdgeScanTrace` while preserving `find_edges()` compatibility.
- Add a bootstrap probability sampler seam to `MarketAnalysis`; evaluator injects Day0 `day0.p_vector(..., n_mc=1, rng=...)`.
- Add evaluator rejection details so zero-edge/FDR rejections include edge-scan trace summaries and a legal no-trade category.
- Add non-blocking `source_frontier.source_writer_status` to money-path frontier reports.
- Preserve no-trade attribution fields (`strategy_key`, `event_source`, `shadow_runtime`) when cycle runtime persists rejected decisions.

## Verification

- Relationship tests for empty edge trace, missing native NO quote trace, injected Day0 bootstrap sampling, evaluator Day0 handoff, math-frontier classification, source-writer observability degradation, and no-trade attribution.
- Focused module gate: `tests/test_market_analysis.py`.
