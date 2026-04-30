# Codex Prompt — Phase A Safety Freeze and Tests

Task: pricing semantics authority cutover, Phase A only.

Goal: make legacy probability/price conflation fail closed in live by default and add tests that expose current unsafe behavior.

Do not implement the full fix yet. Add or update tests first.

Add tests proving:

1. Changing executable ask/depth changes entry cost/size but not posterior.
2. Changing named MarketPriorDistribution changes posterior but not selected token/snapshot.
3. NO-token quote cannot be passed as full-family market prior.
4. Corrected executor rejects missing final limit/cost basis.
5. Corrected executor never recomputes from `p_posterior`, `vwmp`, or `BinEdge.entry_price`.
6. Reporting/backtest cannot mix legacy/corrected economics in promotion-grade reports.
7. Monitor held-token quote cannot become `p_market` vector.

Add feature flags if topology allows:

```text
ALLOW_LEGACY_VWMP_PRIOR_LIVE=false
CORRECTED_PRICING_SHADOW_ONLY=true
CORRECTED_PRICING_LIVE_ENABLED=false
```

Stop if implementation requires live/prod/config flips or broad runtime rewrites.

Closeout: list failing tests if tests intentionally expose old behavior; do not claim full implementation.
