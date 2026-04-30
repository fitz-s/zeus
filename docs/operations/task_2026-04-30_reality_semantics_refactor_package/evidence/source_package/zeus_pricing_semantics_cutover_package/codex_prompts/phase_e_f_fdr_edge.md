# Codex Prompt — Phase E/F Executable Edge and Live Economic FDR

Task: pricing semantics authority cutover, Phases E and F only.

Goal: build live economic edge and FDR on fixed executable hypotheses.

For each bin/direction:

- Map to selected native token.
- Compute payoff probability from posterior.
- Build ExecutableCostBasis from snapshot + order policy.
- Compute `live_economic_edge = payoff_probability - fee_adjusted_execution_price`.
- Reject before Kelly if edge <= 0 under the configured size/cost policy.

FDR family must include all executable hypotheses and hypothesis id must include:

```text
bin + direction + selected_token_id + snapshot_id/hash + cost_basis_id/hash + order_policy
```

If snapshot/cost basis changes after FDR, reject or recompute FDR.

Tests:

- NO payoff uses `1-P_yes`; NO cost uses NO token book.
- FDR-selected row materializes same token/snapshot/cost.
- Late reprice invalidates selected hypothesis.
- Research FDR and live economic FDR remain separate.
