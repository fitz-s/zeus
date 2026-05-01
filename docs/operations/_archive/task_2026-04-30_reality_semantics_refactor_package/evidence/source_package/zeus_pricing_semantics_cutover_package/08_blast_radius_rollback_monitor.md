# 08 — Blast Radius / Rollback / Monitor Points

| Decision | Blast radius | Rollback | Monitor points |
|---|---|---|---|
| Modify authority money path | All future agents, tests, reference docs | Supersession note; revert authority diff before source phases | Old `P_market` formula reappears |
| Add semantics version | DB queries, reports, backtests | Additive only; no old-row relabel | Null semantics rows, mixed cohorts |
| Add MarketPriorDistribution | Posterior fusion and tests | `model_only_v1` default | Raw quote prior rejects, prior validation status |
| Add ExecutableCostBasis | Strategy, Kelly, executor, reports | Shadow-only, corrected live flag off | Missing snapshot/cost basis, fee drift |
| Add CLOB sweep | Sizing and edge | Use top-of-book conservative probe until stable | Cost vs size curve, insufficient depth |
| Move snapshot before FDR | Runtime selection | Corrected path disabled | Materialization drift count |
| Executor no-recompute | Fill behavior and order creation | Legacy explicit path only, non-promotion-grade | Submitted limit vs final intent |
| Add final intent | Venue command facts, idempotency | Shadow final intents only | Rejection reasons, envelope identity |
| Corrected exit symmetry | Position lifecycle and sell path | No corrected live promotion | Remaining shares, partial fills, exit quote freshness |
| Reporting hard-fail | Dashboards, backtests, promotion reports | Diagnostic side-by-side reports | Blocked report count, mixed cohort attempts |
| Add import fences | Developer workflow | Phase-specific exemptions only with authority | Rule violations and false positives |

## Irreversible or high-risk changes

### Additive schema/persistence

Never backfill old rows as corrected. Rollback is to stop writing corrected fields, not to rewrite history.

### Final intent executor

Once corrected path is live, price recomputation cannot be reintroduced without violating authority. Rollback is to disable corrected live path, not to let executor invent prices.

### FDR identity change

Old and new FDR reports are not comparable unless explicitly labeled. Rollback is to report legacy research-only semantics.

### Report hard-fail

This will break dashboards that previously aggregated everything. That is intended. Diagnostic reports may show both cohorts but promotion reports must not combine them.

## Monitors required before canary

```text
quote_staleness_reject_rate
snapshot_hash_mismatch_rate
cost_basis_missing_rate
fee_metadata_missing_rate
tick_reject_rate
min_order_reject_rate
depth_insufficient_rate
fdr_materialization_drift_rate
executor_intent_reject_rate
submitted_limit_mismatch_count
partial_fill_count
cancel_remainder_count
maker_taker_realized_distribution
realized_fee_vs_assumed_fee_delta
sell_quote_freshness
residual_exposure_after_exit
mixed_cohort_report_block_count
```
