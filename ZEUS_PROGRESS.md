# Zeus Progress

## Session 1 (2026-03-30)

### Phase 0: Baseline Experiment — GO ✓

Baseline confirmed structural mispricing exists. Center bins underpriced 2.9×, shoulder bins overpriced. Details in state/baseline_results.json.

### Phase A: Signal Infrastructure — COMPLETE ✓

#### A1: Signal Generation
- [x] src/types.py — Bin + BinEdge dataclasses
- [x] src/data/ensemble_client.py — Open-Meteo ECMWF/GFS fetch
- [x] src/signal/ensemble_signal.py — P_raw with MC instrument noise (σ=0.5°F, 5000 MC)
- [x] src/signal/model_agreement.py — JSD conflict detection (AGREE/SOFT_DISAGREE/CONFLICT)

#### A2: Calibration Foundation
- [x] src/calibration/platt.py — ExtendedPlattCalibrator (3-param, 200 bootstrap)
- [x] src/calibration/store.py — SQLite CRUD for calibration_pairs + platt_models
- [x] src/calibration/manager.py — 24-bucket routing, maturity gate (4 levels), fallback

#### A3: Edge Detection + Strategy
- [x] src/strategy/market_analysis.py — MarketAnalysis with double bootstrap CI (3σ)
- [x] src/strategy/market_fusion.py — compute_alpha (4-level + dynamic), VWMP, posterior
- [x] src/strategy/fdr_filter.py — BH FDR control with exact bootstrap p-values

### Phase B: Calibration Seeding — SKIPPED (needs ENS backfill data)

Requires running scripts/backfill_ens.py which makes real API calls over hours. Deferred to Phase D prep.

### Phase C: Execution Layer — COMPLETE ✓

#### Strategy
- [x] src/strategy/kelly.py — per-bin Kelly + dynamic multiplier
- [x] src/strategy/risk_limits.py — portfolio constraint enforcement

#### Execution
- [x] src/execution/executor.py — limit-order-only, paper fills at VWMP
- [x] src/execution/exit_triggers.py — 6 triggers, 2-confirmation EDGE_REVERSAL
- [x] src/execution/harvester.py — settlement → 11 calibration pairs
- [x] src/state/chronicler.py — append-only trade log
- [x] src/state/portfolio.py — atomic JSON, exposure queries

#### RiskGuard
- [x] src/riskguard/risk_level.py — GREEN/YELLOW/ORANGE/RED
- [x] src/riskguard/metrics.py — Brier, directional accuracy, win rate
- [x] src/riskguard/riskguard.py — independent 60s tick process

#### Main
- [x] src/main.py — APScheduler with Mode A/B/C + harvester + monitor

### Test Summary

130 tests passing across 12 test files. Every src/ module has corresponding tests.

### Key Data Discoveries (from Phase 0)

1. **token_price_log has NO range_label** — Rainstorm bug; all empty
2. **market_events range_low/range_high ALL NULL** — boundaries in label text only
3. **City name mismatch** — market_events: "LA"/"SF" vs settlements: "Los Angeles"/"San Francisco"
4. **London WU observations contaminated** — use openmeteo_archive as primary
5. **Only 41 multi-bin structured markets** — historical are single-bin binary

### Decisions Made (Spec Divergences)

1. **MarketAnalysis constructor** takes pre-computed vectors (p_raw, p_cal, p_market) instead of (ens, bins, calibrator, alpha). More flexible for handling maturity level 4 (no calibrator). CLAUDE.md interface updated.

2. **compute_posterior normalizes** output to sum=1.0, since p_market sums to vig (~0.95-1.05). CLAUDE.md p_posterior type requires sum=1.0.

3. **Bootstrap .astype(int)** in market_analysis._bootstrap_bin is intentional — it simulates the same WU settlement chain as p_raw_vector, not a temperature type violation.

4. **trade_decisions schema** updated with attribution fields (edge_source, bin_type, discovery_mode, market_hours_open, fill_quality) per CLAUDE.md update.

### Schema Updates Applied
- trade_decisions: added edge_source, bin_type, discovery_mode, market_hours_open, fill_quality
- token_price_log: added city, target_date, range_label columns

### Next Session: Integration + Paper Trading Prep

**Priority 1:** Wire discovery modes (opening_hunt, update_reaction, day0_capture) to the full pipeline:
  ENS fetch → EnsembleSignal → calibrate → MarketAnalysis → FDR → Kelly → executor

**Priority 2:** ENS backfill script (scripts/backfill_ens.py) for historical Platt fitting

**Priority 3:** Polymarket CLOB client (src/data/polymarket_client.py) — extract from Rainstorm v1

**Priority 4:** Market scanner (src/data/market_scanner.py) — Gamma API market discovery

**Files remaining (not yet created):**
```
src/data/polymarket_client.py    — CLOB API (extract from Rainstorm)
src/data/market_scanner.py       — Gamma API market discovery
src/data/observation_client.py   — IEM ASOS + WU + Meteostat for Day0
src/data/climatology.py          — NOAA GHCND historical distributions
src/data/nws_client.py           — NWS extreme weather alerts
src/signal/day0_signal.py        — Observation + residual ENS
src/calibration/drift.py         — Hosmer-Lemeshow drift detection
src/strategy/correlation.py      — Heuristic correlation matrix
src/execution/opening_hunt.py    — Mode A full pipeline
src/execution/update_reaction.py — Mode B full pipeline
src/execution/day0_capture.py    — Mode C full pipeline
src/execution/monitor.py         — Variable-frequency exit checking
src/state/ensemble_store.py      — ENS snapshot archive
src/analysis/dashboard.py        — Dash web UI
src/analysis/performance.py      — P&L analysis
scripts/backfill_ens.py          — 92-day ENS backfill
```
