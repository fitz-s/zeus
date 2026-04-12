# Live-ready gates for the data-improve cutover

This is the minimum bar I would use before calling the repaired data-improve path live-ready.

## Gate A — Probability truth

Required:

- `probability_trace_fact` row count tracks evaluator decision count closely
- every `should_trade=True` decision has:
  - `decision_id`
  - `decision_snapshot_id`
  - `bin_labels_json`
  - `p_raw_json`
  - `p_cal_json`
  - `p_market_json`
  - `p_posterior_json`
  - `trace_status='complete'`
- rejection decisions are still written, with explicit `trace_status`

Fail if:

- shadow-only vectors are still the only detailed probability surface
- `opportunity_fact` still carries null probability payloads after cutover

## Gate B — Family-wise selection truth

Required:

- every evaluated family has one `selection_family_fact` row
- every tested hypothesis has one `selection_hypothesis_fact` row
- BH runs over all tested rows in a family
- `selected_post_fdr=1` implies `passed_prefilter=1`

Fail if:

- active trading still relies only on `fdr_filter(edges)` over prefiltered edges

## Gate C — Calibration grouped truth

Required:

- `calibration_decision_group` exists and is populated
- grouped row count matches grouped calibration sample count
- group anomalies are auditable
- maturity / OOS / promotion consume grouped truth, not raw pair counts

Fail if:

- sample maturity still depends on pair-row counts only

## Gate D — Day0 learning truth

Required:

- `day0_residual_fact` rows materialize with real feature coverage
- missingness is explicit in `missing_reason_json`
- `post_peak_confidence` and ENS remaining quantiles are non-empty for a meaningful fraction of usable rows

Fail if:

- the table exists but feature columns are still placeholders

## Gate E — Portfolio stale-truth policy

Required:

- `partial_stale` has an explicit policy
- no silent disappearance of stale open positions
- operator diagnostics clearly expose when the runtime is degraded

Fail if:

- stale positions can fall out of exposure/risk calculations without an explicit degraded-state record

## Gate F — Post-TIGGE evaluation

Required after TIGGE:

- blocked OOS runs exist in `model_eval_run` / `model_eval_point`
- promotion rows exist for any model influencing live behavior
- wider city coverage is backed by real grouped-sample truth

Fail if:

- city expansion happens before grouped truth and OOS evidence

## Practical live-ready statement

The repaired path is live-ready when:

1. truth is singular enough to replay decisions,
2. selection is family-wise enough to defend the FDR claim,
3. grouped calibration truth exists,
4. stale-truth handling is explicit,
5. and Day0 features are real rather than placeholder.
