# Patch 01 — evaluator probability + family cutover

## Goal

Keep the current evaluator contracts, but add **two explicit side effects**:

1. durable per-decision `probability_trace_fact` writes
2. durable full-family `selection_family_fact` / `selection_hypothesis_fact` writes

## Current-mainline note

Do not apply the older fragment below literally. The current `data-improve`
branch keeps probability trace writes in `src.state.db`, uses a candidate-level
family id for full-family FDR truth, and preserves per-hypothesis strategy keys
inside hypothesis metadata. In particular, do not set
Do not derive discovery mode from selected method, and do not key the full
tested family by strategy key.

## Current imports

```python
from src.state.db import log_probability_trace_fact
from src.strategy.market_analysis_family_scan import scan_full_hypothesis_family
from src.strategy.selection_family import apply_familywise_fdr, make_family_id
```

## Insertion point A — immediately after `analysis = MarketAnalysis(...)`

Keep the current `edges = analysis.find_edges()` line for compatibility, but add a full-family scan in parallel:

```python
full_family = scan_full_hypothesis_family(analysis, n_bootstrap=edge_n_bootstrap())
family_id = make_family_id(
    cycle_mode=current_mode,
    city=city.name,
    target_date=candidate.target_date,
    strategy_key="",  # candidate-level family; per-hypothesis strategy is metadata
    discovery_mode=str(candidate.discovery_mode or ""),
    decision_snapshot_id=snapshot_id,
)
rows = apply_familywise_fdr([...])
selected_family = [
    row for row in rows
    if row["selected_post_fdr"] and row["passed_prefilter"]
]
```

## Insertion point B — replace `filtered = fdr_filter(edges)` in shadow mode first

Until you fully cut over selection, keep both paths and compare:

```python
legacy_filtered = fdr_filter(edges)
family_selected_keys = {(h.range_label, h.direction) for h in selected_family}
filtered = [e for e in legacy_filtered if (e.bin.label, e.direction) in family_selected_keys]
```

For the first shadow window, do **not** yet delete the legacy result. Log both counts.

## Insertion point C — when writing each `EdgeDecision`

After creating `EdgeDecision(...)`, call the current canonical DB helper. Do
not instantiate a packet-local trace writer:

```python
log_probability_trace_fact(conn, candidate=candidate, decision=decision, recorded_at=recorded_at, mode=current_mode)
```

## Why this patch exists

The current branch already stores `decision_snapshot_id`, `p_raw`, `p_cal`, `p_market`, `alpha`, `agreement`, and `n_edges_found` on the decision object, but the durable truth surface is still not complete. This patch closes that loop without waiting for TIGGE.
