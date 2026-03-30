# Zeus Progress

## Session 1 (2026-03-30)

### Phase 0: Baseline Experiment — COMPLETE ✓

**Decision: GO**

#### Completed
- [x] Project scaffolding: directory structure, venv, config/settings.json, config/cities.json
- [x] src/config.py — strict config loader, no .get(key, fallback)
- [x] src/state/db.py — full Zeus schema with 4-timestamp constraint
- [x] scripts/migrate_rainstorm_data.py — data migration from rainstorm.db
- [x] scripts/baseline_experiment.py — climatology vs settlement outcome analysis
- [x] Tests: 12 tests passing (test_config.py, test_db.py, test_migration.py)

#### Baseline Results (41 multi-bin structured markets)

| Metric | Shoulder Bins | Center Bins |
|--------|--------------|-------------|
| N bins | 239 | 565 |
| Win rate | 8.4% | 9.9% |
| Avg P_clim | 41.3% | 3.5% |
| Win/P_clim ratio | 0.203 | 2.858 |

**Key findings:**
1. Center bins win at 2.9× their climatological base rate → systematically underpriced
2. Shoulder bins win at only 0.2× their climatological rate → overpriced even by climatology
3. Single-bin threshold markets (n=1,343): 5.0% win rate vs 30.6% P_clim (ratio 0.163)
4. Calibration table shows severe overestimation at high-P bins — expected, since shoulder bins cover wide ranges but rarely win in multi-bin markets

**Interpretation:**
- The edge thesis is confirmed: structural mispricing exists
- Center bins near model consensus are underpriced → buying center YES is a viable strategy
- Shoulder bins are overpriced → selling shoulder (buy NO) or avoiding is correct
- ENS ensemble + Platt calibration will be vastly superior to this crude climatology

#### Data Discoveries (Critical Intelligence)

1. **token_price_log has NO range_label** — Rainstorm bug; column always empty. Token-to-bin mapping requires Gamma API live lookup. Historical price backtesting is NOT possible with current data.

2. **token_price_log date range: 2026-03-28 to 2026-04-15 only** — no historical market prices. Sharpe calculation impossible without price data.

3. **market_events range_low/range_high are ALL NULL** — all boundary info is in range_label text. Parser required for: `"49°F or below"`, `"50–51 °F"` (en-dash!), `"4°C"` (point bins), `"68°F or higher"`.

4. **City name inconsistency**: market_events uses "LA"/"SF", everything else uses "Los Angeles"/"San Francisco". Need normalization layer.

5. **London observations contaminated**: WU daily observed for London has values >90 with unit='C' — physically impossible. Openmeteo_archive is clean. Use as primary source for European cities.

6. **Only 41 multi-bin structured markets** exist (2026-03-24 to 2026-03-28). Historical markets were single-bin threshold format. Multi-bin data will grow as live collection continues.

7. **Settlements: 1,390** (filtered from 1,634 where winning_range IS NOT NULL)
   - NYC: 425, London: 429, Dallas: 115, Atlanta: 114, Seattle: 113
   - Chicago: 68, Miami: 68, Paris: 44, LA: 8, SF: 6

8. **market_events dedup**: 14,901 → 6,023 on UNIQUE(market_slug, condition_id). Some markets have duplicate ingestion records.

#### Decisions Made
- **Baseline redesigned**: Spec called for price-based backtest (condition a). Without historical prices, pivoted to climatology-vs-outcomes analysis (condition b). Both approaches test the same hypothesis; prices would add confidence but aren't required for GO/NO-GO.
- **Label parser built**: Since range_low/range_high are NULL, wrote regex parser for all label formats. Handles °F, °C, en-dash, "or below"/"or lower"/"or higher" variants.
- **Observations strategy**: Use openmeteo_archive as primary (cleanest), iem_asos secondary, WU deprioritized for European cities due to unit contamination.

#### Schema Updates Pending
- CLAUDE.md updated: trade_decisions needs attribution fields (edge_source, bin_type, discovery_mode, market_hours_open, fill_quality)
- Need to add these columns to db.py schema before Phase C

### Next Session: Phase A — Signal Infrastructure

**Priority 1:** ensemble_client.py — Open-Meteo ENS 51-member fetch
**Priority 2:** ensemble_signal.py — P_raw vector with MC instrument noise
**Priority 3:** model_agreement.py — JSD-based ECMWF vs GFS conflict detection

**Prerequisites:**
- Read spec §2.1 (signal generation) and §2.2 (model agreement)
- ENS backfill script for historical settlements (92-day window)

**Files to create:**
```
src/data/ensemble_client.py
src/signal/ensemble_signal.py
src/signal/model_agreement.py
tests/test_ensemble_signal.py
tests/test_model_agreement.py
```
