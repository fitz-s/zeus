# src/strategy AGENTS — Zone K3 (Math/Data)

## WHY this zone matters

Strategy converts calibrated probabilities into trading decisions: edge computation, statistical filtering, and position sizing. Three critical subsystems work together:

1. **MarketAnalysis**: double-bootstrap CI captures three σ sources (ensemble, instrument, parameter)
2. **FDR filter**: Benjamini-Hochberg controls false discovery rate across ~220 simultaneous hypotheses
3. **Kelly**: fractional Kelly with dynamic multiplier sizes positions

If you break the statistical pipeline, Zeus either overtrades (false edges) or undersizes (missed real edges).

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `market_analysis.py` | Edge scan + double-bootstrap CI | HIGH — core edge computation |
| `fdr_filter.py` | Benjamini-Hochberg FDR control | HIGH — false discovery prevention |
| `kelly.py` | Fractional Kelly sizing + dynamic mult | HIGH — position sizing |
| `market_fusion.py` | Bayesian P_cal + P_market fusion | HIGH — posterior computation |
| `correlation.py` | Cross-city/bin correlation | MEDIUM |
| `risk_limits.py` | Strategy-level risk limits | MEDIUM |

## Domain rules

- p-values come from bootstrap: `p = mean(bootstrap_edges ≤ 0)` — **never** from approximation formulas (see `market_analysis.py`)
- FDR α default is from config (`edge.fdr_alpha`), not hardcoded
- Kelly cascade product must stay bounded in [0.001, 1.0] — tested by `test_kelly_cascade_bounds`
- All probabilities at cross-layer seams must carry provenance (INV-12)
- Unregistered numeric constants in Kelly cascades are forbidden (INV-13)

## Common mistakes

- Using normal approximation for p-values instead of bootstrap empirical distribution
- Applying FDR per-city instead of across all 220 hypotheses → defeats the purpose
- Adding a new Kelly multiplier adjustment without registering it in `provenance_registry.yaml`
- Confusing gross edge (before fees) with net edge → fee leakage
