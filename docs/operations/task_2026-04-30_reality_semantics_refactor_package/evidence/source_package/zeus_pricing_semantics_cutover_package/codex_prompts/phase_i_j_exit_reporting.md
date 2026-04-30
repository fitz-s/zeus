# Codex Prompt — Phase I/J Monitor Exit and Reporting

Task: pricing semantics authority cutover, Phases I and J only.

Goal: corrected entry must have corrected monitor/exit and reporting semantics.

Monitor must split:

- probability refresh.
- held-token quote refresh.

Exit EV:

```text
hold_value = payoff_probability * payout_value
sell_value = held_token_sell_quote_after_fee
```

No `p_market` vector fallback may masquerade as sell quote.

Persistence:

- Additive fields only.
- `pricing_semantics_version` on trade/position/probability/decision facts.
- snapshot id/hash and cost basis id/hash.
- final limit and fee-adjusted execution price.

Reporting:

- Promotion reports hard-fail or segregate mixed legacy/corrected cohorts.
- Backtests without point-in-time depth/snapshot/hash are diagnostic only.

Tests:

- partial sell fill reduces remaining exposure.
- corrected entry + legacy exit blocks promotion.
- mixed cohort promotion report fails.
- historical rows without snapshot cannot be marked corrected.
