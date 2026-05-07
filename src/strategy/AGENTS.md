# src/strategy AGENTS — Zone K3 (Math/Data)

Module book: `docs/reference/modules/strategy.md`
Machine registry: `architecture/module_manifest.yaml`

## WHY this zone matters

Strategy converts calibrated probabilities into trading decisions: edge computation, statistical filtering, and position sizing. Three critical subsystems work together:

1. **MarketAnalysis**: double-bootstrap CI captures three σ sources (ensemble, instrument, parameter)
2. **FDR filter**: Benjamini-Hochberg controls false discovery rate across the active tested candidate family
3. **Kelly**: fractional Kelly with dynamic multiplier sizes positions

If you break the statistical pipeline, Zeus either overtrades (false edges) or undersizes (missed real edges).

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `market_analysis.py` | Edge scan + double-bootstrap CI | HIGH — core edge computation |
| `market_analysis_family_scan.py` | Full tested-family scan for FDR truth | HIGH — FDR accounting |
| `fdr_filter.py` | Benjamini-Hochberg FDR control | HIGH — false discovery prevention |
| `kelly.py` | Fractional Kelly sizing + dynamic mult | HIGH — position sizing |
| `market_fusion.py` | Bayesian P_cal + P_market fusion | HIGH — posterior computation |
| `selection_family.py` | Family-wise hypothesis-selection substrate | MEDIUM |
| `correlation.py` | Cross-city/bin correlation | MEDIUM |
| `risk_limits.py` | Strategy-level risk limits | MEDIUM |

## Domain rules

- p-values come from bootstrap: `p = mean(bootstrap_edges ≤ 0)` — **never** from approximation formulas (see `market_analysis.py`)
- FDR α default is from config (`edge.fdr_alpha`), not hardcoded
- Kelly cascade product must stay bounded in [0.001, 1.0] — tested by `test_kelly_cascade_bounds`
- All probabilities at cross-layer seams must carry provenance (INV-12)
- Unregistered numeric constants in Kelly cascades are forbidden (INV-13)

## Live selection rules (post-Phase 1)

- **Full tested hypothesis family is the FDR budget basis.** `attempted` = COUNT of ALL hypotheses tested in the active candidate/market/snapshot family, not just those with positive CI. Legacy prefiltered-edge paths under-count the family, creating false statistical significance. Zeus does not currently claim whole-cycle BH across all candidate markets.
- Per-trade Kelly hard caps were **REMOVED 2026-05-04** along with fixed config bankroll and smoke-test cap authority. Per-cycle exposure discipline now lives in posture / RiskGuard / max-exposure gates only. See `config/settings.json::_bankroll_doctrine_2026_05_04`.
- Strategy gates carry `GateDecision` provenance with `ReasonCode` enum — no bare bool gates
- `reason_refuted()` returns False for all codes in Phase 1 (conservative); auto-un-gate recommendations require explicit refutation logic

## Common mistakes

- Using normal approximation for p-values instead of bootstrap empirical distribution
- Applying FDR only to prefiltered positive-CI edges instead of the full tested family → defeats the purpose
- Adding a new Kelly multiplier adjustment without registering it in `provenance_registry.yaml`
- Confusing gross edge (before fees) with net edge → fee leakage

## Oracle Penalty (`oracle_penalty.py`)

Kelly multiplier penalty for cities where WU/HKO oracle diverges from PM settlement.
- `get_oracle_info(city)` → `OracleInfo(status, error_rate, threshold)`
- `OracleStatus.BLACKLISTED` blocks trading; `CAUTION` applies `1 - error_rate` multiplier
- Reads `data/oracle_error_rates.json`
- Thresholds: <3% OK, 3–10% CAUTION, >10% BLACKLISTED
