# LIVE-PROB-P0 — Operator Binding Spec (2026-05-23)

Authority: operator directive. Controlling spec for the edge-bin probability-object validity gate. Supersedes prior brief where stricter.

## Confirmed root (do NOT re-litigate)
- Live-path PHANTOM edge: large p_cal−p_market on sub-5¢ low-temp bins reaches the economic floor as if a valid trade object. Floor prevents loss but phantom must never be PRODUCED as a tradeable object. Amsterdam May24 HIGH 23°C: p_cal≈0.186 vs market≈0.047, edge≈14.4¢; ladder ≤23°C ours 21%+ vs market 5.9%.
- NOT execution-path, NOT day0-only, NOT observation, NOT market-quiet, NOT floor-too-high.
- p_raw family-normalized (MC noise→WMO-round→bin-count; member support POST-rounding). Calibration = per-bin Platt then family renorm. probability_trace_fact stores full p_raw/p_cal/p_market vectors.
- TIGGE→OpenData calibration transfer = deliberate same-ECMWF-ENS bridge, NOT a bug. MC settlement noise NOT proven wrong. DO NOT touch calibration or MC.
- Existing probability_sanity hard ONLY for day0 HIGH; non-day0 HIGH = shadow/log → phantom reaches floor. Insert new gate after p_cal finalized, before MarketAnalysis (~evaluator.py:4622, confirm current line).
- Family-level cumulative tail-ratio hard gate = ~21% FALSE POSITIVES (blocks genuine fills). REJECTION UNIT MUST BE THE SELECTED EDGE BIN; family/tail stats are telemetry only.

## A. REPORT FIRST (before code)
Write docs/reports/live_prob_p0_edge_bin_sanity_20260523.md:
1. Amsterdam fixture reconstruction: decision ts; city/date/metric; selected snapshot_id/source_run_id/source_cycle_time; bin labels; full p_raw/p_cal/p_market vectors; selected edge bin; edge-bin p_raw/p_cal/p_market; settled-member support for edge bin; immediate-neighbor support; lower/upper cumulative mass around edge bin; exact reason it is phantom.
2. Historical FP replay (May20–23 or latest set): label each PHANTOM_TRUE_POSITIVE / LEGIT_EDGE_SHOULD_PASS (historically filled OR priced ≥5¢ with credible support) / NOISE_UNKNOWN. Counts before/after proposed predicate.
3. Reuse-vs-new-predicate verdict.
4. Final mode verdict: shadow_only | hard_ready | hard_with_operator_flag.
Do NOT implement until the report shows a predicate catching Amsterdam with ZERO replay FP on filled/priced-credible edges.

## B. PREDICATE = per-edge-bin (settled-member-support centric)
`probability_edge_bin_sanity(selected_bin_idx, bins, p_raw, p_cal, p_market, settled_member_samples, direction, metric, strategy_key, market_phase, config)`.
Reject ONLY when the SELECTED edge bin is suspicious — most/all true:
- edge_price = p_market[edge] <= low_price_threshold (0.05)
- edge_gap = p_cal[edge] − p_market[edge] >= min_edge_gap (0.03)
- p_cal[edge]/max(p_market[edge],eps) >= odds_ratio_threshold (3.0)
- settled_member_support_edge_bin < min_edge_bin_support (0.05)
- neighbor/family evidence does not justify edge-bin mass
- edge bin is point/finite (not a deterministic shoulder proof)
CRITICAL: if the edge bin has STRONG settled-member support and the market is merely low, do NOT reject (that is the FP risk). Member support is the discriminator, not the ratio alone.

## Reason codes (no vague strategy_economic_floor)
PROBABILITY_EDGE_BIN_UNSUPPORTED, PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT, PROBABILITY_TAIL_SHAPE_ANOMALY_SHADOW, PROBABILITY_TAIL_SHAPE_ANOMALY_HARD.

## C. Placement
After p_raw/p_cal/p_market finalized, before MarketAnalysis builds edge objects + before economic floor. day0 HIGH keeps existing hard gate. non-day0 HIGH = new gate. LOW = implement symmetric L/R OR keep shadow-only until symmetry tests exist. Do NOT claim metric-agnostic if only HIGH covered.

## D. Production-path tests (function tests alone insufficient)
1. Amsterdam RED via evaluate_candidate() real path → rejected before MarketAnalysis/floor with PROBABILITY_EDGE_BIN_*/TAIL_SHAPE_*; goes RED if wiring removed.
2. LEGIT edge PASSES: strong settled-member support, low/moderate p_market, real edge → not rejected, reaches existing checks. (Prevents "no phantom"→"no fills".)
3. Right-side/LOW symmetry test that fails if only left mask checked.
4. Replay test scripts/replay_probability_edge_bin_sanity.py: total / Amsterdam-like rejected / historical-filled rejected / priced≥5¢ rejected / FP / known-FN. HARD acceptance: Amsterdam rejected AND historical-filled rejected=0 AND priced≥5¢-credible rejected=0. Else shadow-only.

## E. Telemetry (existing probability_trace_fact; populate or don't add)
probability_sanity_mode(shadow|hard), probability_sanity_reason, edge_bin_idx, edge_bin_label, edge_bin_p_raw, edge_bin_p_cal, edge_bin_p_market, edge_bin_member_support, edge_bin_odds_ratio, near_tail_p_cal, near_tail_p_market. Any schema column added MUST be populated in production (prior critic caught dead columns).

## F. Config (default conservative)
```
"probability_edge_bin_sanity": {
  "mode": "shadow",
  "low_price_threshold": 0.05,
  "min_edge_gap": 0.03,
  "odds_ratio_threshold": 3.0,
  "min_edge_bin_member_support": 0.05,
  "min_neighbor_support": 0.05,
  "apply_to_strategies": ["opening_inertia","imminent_open_capture","center_buy"],
  "apply_to_metrics": ["high"],
  "log_only_until_replay_fp_zero": true
}
```
Set "mode":"hard" ONLY if replay FP=0 against historical filled/credible edges.

## G. DO NOT
lower min_entry_price / min_expected_profit; relax model_conflict; promote day0; modify Kelly; modify reprice/final-intent; remove MC instrument noise; disable TIGGE→OpenData transfer.

## H. DONE means
1. Amsterdam phantom rejected in production-path test. 2. Same predicate does not reject historical filled candidates. 3. Right-side/LOW limitation explicitly tested. 4. Replay FP=0 before hard; else shadow. 5. Telemetry records edge-bin evidence. 6. Committed WITH the report file.
Commit: docs/reports/live_prob_p0_edge_bin_sanity_20260523.md; tests/test_live_probability_edge_bin_sanity.py; tests/test_evaluator_probability_edge_bin_sanity_path.py.
