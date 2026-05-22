# Live Math Frontier Repair Plan

## Scope

Implement the current `task.md` short-term recovery slice for the latest live finding:
`day0_capture` reached evaluator with one candidate, but `MarketAnalysis.find_edges()`
returned zero edges before family, snapshot, reprice, or final-intent paths ran.

Follow-up scope for the same live math-frontier packet: opening_hunt now reaches
the evaluator but stops on `MODEL_CONFLICT` and ultra-low price policy. This
packet therefore also covers model-conflict evidence/comparability, physical
temperature conflict policy, normal-vs-tail ultra-low decomposition, and
family-frontier cause separation. It does not authorize production DB mutation,
daemon restarts, economic-floor relaxation, or forced live orders.

## Invariants

- Do not lower CI, FDR, economic floors, model-conflict gates, or force live orders.
- Preserve existing generic ENS bootstrap behavior for non-Day0 modes.
- Day0 edge confidence must sample the same observation-fused probability object as Day0 `p_raw`.
- `edges=0` must carry frontier evidence: raw edge, CI, executable mask, and native NO quote availability classes.
- Source-health writer freshness is observability evidence, not a live trading hard blocker in this patch.
- `MODEL_CONFLICT` must mean comparable forecast probability objects disagree;
  non-comparable issue/valid/local-day windows reject as crosscheck unavailable.
- GFS crosscheck probability must use the same MC/noise/settlement probability
  space as the primary vector before it can hard-kill a live candidate.
- Hard conflict must require physical temperature disagreement, not only bin
  index argmax distance.
- Ultra-low live authorization is tail-topology aware; profile permission alone
  must not authorize non-tail penny orders.
- Family preselection/sibling drops must not be reported as existing exposure.

## Implementation

- Add `MarketAnalysis.find_edges_with_trace()` and `EdgeScanTrace` while preserving `find_edges()` compatibility.
- Add a bootstrap probability sampler seam to `MarketAnalysis`; evaluator injects Day0 `day0.p_vector(..., n_mc=1, rng=...)`.
- Add evaluator rejection details so zero-edge/FDR rejections include edge-scan trace summaries and a legal no-trade category.
- Add non-blocking `source_frontier.source_writer_status` to money-path frontier reports.
- Preserve no-trade attribution fields (`strategy_key`, `event_source`, `shadow_runtime`) when cycle runtime persists rejected decisions.
- Add `ModelConflictEvidence` and `CrosscheckComparableContext`, and persist
  compact evidence in model-conflict / comparability no-trade detail.
- Replace GFS direct member-count crosscheck with the shared MC probability
  generator over target-day extrema.
- Normalize low-price rejection detail and expose price-policy frontier counters.
- Split `family_selection_dedup` from `blocked_existing_family_exposure`.

## Verification

- Relationship tests for empty edge trace, missing native NO quote trace, injected Day0 bootstrap sampling, evaluator Day0 handoff, math-frontier classification, source-writer observability degradation, and no-trade attribution.
- Relationship tests for model-conflict evidence, physical-temperature conflict
  policy, crosscheck non-comparability, ultra-low tail topology, and family
  frontier cause separation.
- Focused module gate: `tests/test_market_analysis.py`.
