# Insertion points

This file tells you exactly where the runtime cutover should happen.

## 1. `src/strategy/market_analysis.py`

### Problem
Current `find_edges()` only returns hypotheses where:

- point edge > 0
- and bootstrap `ci_lo > 0`

That means the active evaluator never sees the full tested family.

### What to add
Do **not** delete the current `find_edges()` yet.

Add a new helper call path using `src/strategy/market_analysis_family_scan.py`:

- scan every bin
- scan both directions
- compute `edge`, `p_value`, `ci_lower`, `ci_upper`
- mark `passed_prefilter = edge > 0 and ci_lower > 0`

That gives you the full family while keeping the existing `BinEdge` path intact.

### Runtime pattern
1. `all_hypotheses = scan_family_hypotheses(analysis)`
2. persist family rows
3. apply BH over all tested hypotheses
4. keep only selected rows with `passed_prefilter = 1`
5. map that selected key set back onto the existing `edges` list

---

## 2. `src/engine/evaluator.py`

### Current shape
The current flow is roughly:

1. build `analysis`
2. `edges = analysis.find_edges(...)`
3. `filtered = fdr_filter(edges)`
4. turn filtered edges into decisions

### Replace with
1. build `analysis`
2. `edges = analysis.find_edges(...)`  (keep for compatibility)
3. `all_hypotheses = scan_family_hypotheses(analysis, ...)`
4. `persist_selection_family(...)`
5. `selected_keys = selected_prefilter_keys_after_familywise_fdr(...)`
6. `filtered = [edge for edge in edges if (edge.bin.label, edge.direction) in selected_keys]`
7. persist `probability_trace_fact` rows for **every** decision, including rejections

### Important
Probability traces must be written for:

- `MARKET_FILTER`
- `SIGNAL_QUALITY`
- `EDGE_INSUFFICIENT`
- `FDR_FILTERED`
- `ANTI_CHURN`
- `RISK_REJECTED`
- actual `should_trade=True` decisions

The goal is one operator-facing authoritative record for the decision-time probability state.

---

## 3. `src/execution/harvester.py`

### Current shape
`harvest_settlement()` currently writes pair rows only.

### Add
- accept `bias_corrected: bool = False`
- generate `decision_group_id`
- write `calibration_decision_group`
- if the `calibration_pairs` table has `decision_group_id` / `bias_corrected`, populate them too
- log degraded / fallback context when snapshot learning context came from a non-authoritative surface

### Important
If settlement learning uses:

- `portfolio_open_fallback`
- or another degraded context source

that context must still be visible in the grouped fact row or event log.

---

## 4. `src/state/portfolio.py`

### Current problem
`partial_stale` is currently treated as loadable enough to continue.

### Immediate safe rule
Until stale positions are explicitly merged from another authoritative surface:

- `ok` => use DB
- `partial_stale` => fallback to JSON compatibility surface (degraded but safer)
- anything worse => fallback + warning / escalate

### Later improvement
Once you have an explicit merge path for stale positions, you can promote `partial_stale` back into a DB-first path.

---

## 5. `src/signal/day0_residual.py`

### Current problem
The fact writer exists, but several feature fields are hardcoded to `None`.

### Replace
Use the helper in `src/signal/day0_residual_features.py` to compute:

- `daylight_progress`
- `obs_age_minutes`
- `post_peak_confidence`
- `ens_q50_remaining`
- `ens_q90_remaining`
- `ens_spread`

### Why this matters
Without these fields, `day0_residual_fact` is only a shell. It cannot support real model fitting or blocked OOS evaluation.
