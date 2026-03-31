# Zeus Data Strategy

## Current Utilization: 4.1%

Zeus uses 56K of 1.35M available records (4.1%). This is not a design decision — it is the attention boundary of Claude Code. Data that was explicitly mentioned in session prompts got imported. Data requiring inference chains ("this table could calibrate that constant") did not.

| Data Asset | Available | Zeus Uses | Used % |
|------------|----------|-----------|--------|
| TIGGE ENS vectors | 5,026 | 483 | 9.6% |
| ECMWF Open Data | 406 | 0 | 0% |
| Rainstorm token_price_log | 365,444 | 0 | 0% |
| Rainstorm forecast_log | 337,227 | 0 | 0% |
| Rainstorm observations | 240,234 | 0 | 0% |
| Rainstorm forecasts (5 models) | 171,003 | 0 | 0% |
| Rainstorm ladder backfill | 53,600 | 53,581 | 99.9% |
| Rainstorm settlements | 1,643 | 1,390 | 84.6% |
| **TOTAL** | **~1.35M** | **~56K** | **~4.1%** |

## Why 95.9% Is Unused

The 222:1 price-to-settlement ratio (365K token prices vs 1,643 settlements) reveals a structural gap: Zeus listens to the market once at entry (VWMP), then goes deaf until settlement. But those 222 intermediate price snapshots contain more information than a single ENS update — they aggregate all participants' models including HRRR, ICON, and private sources.

Zeus's edge thesis says "market is mostly right" (α < 1.0). But it only hears the market at entry. During holding, it becomes a pure model system. This contradicts its own thesis.

## Complete Asset Inventory

### Location 1: rainstorm/state/ (7.9 GB)
- **rainstorm.db**: 2.5 GB, 20 tables, 1.2M+ rows
- **chronicler.db**: 8.9 MB, 204 engine trades
- **risk_state.db**: 1.0 MB, 12,400 risk log entries
- File data: 5.4 GB across backtests (2.6 GB), raw-data (2.1 GB), journal (77 MB), etc.

### Location 2: 51 source data/ (368 MB)
- **TIGGE**: 5,026 city-date vectors, 38 cities, 2006-2026, steps 24-168h
- **ECMWF Open Data**: 406 files, 38 cities, rolling 2-3 day window

### Location 3: zeus/state/ (96 MB)
- **zeus.db**: 91 MB — calibration_pairs (1,126), ensemble_snapshots (483), forecast_skill (53,581), platt_models (6)

## ETL Roadmap

| Priority | ETL Script | Source → Target | Impact |
|----------|-----------|----------------|--------|
| **P0** | `etl_tigge_calibration.py` | 5,026 TIGGE → calibration_pairs | Unlocks 18 Platt buckets (DJF/JJA/SON) |
| **P1** | `etl_asos_wu_offset.py` | WU+ASOS → asos_wu_offsets | Day0 accuracy per city×season |
| **P1** | ECMWF Open Data client | 406 JSON → daily collection | Eliminates 429 crashes |
| **P2** | `etl_diurnal_curves.py` | 219K hourly → diurnal_curves | Day0 peak_hour precision |
| **P2** | `etl_token_prices.py` | 365K → market_price_history | Opening Hunt timing validation |
| **P2** | `etl_forecasts.py` | 171K → model_skill | Dynamic α calibration |
| **P3** | `etl_forecast_volatility.py` | 337K → forecast_volatility | Edge opportunity index |
| **P3** | `etl_persistence.py` | daily obs → temp_persistence | ENS anomaly detection |

**P0 is the only ETL that blocks Phase D.** Others provide incremental improvements during paper trading.

## Data-Driven Constant Replacement

| Constant | Current | Data to Replace | Priority |
|----------|---------|----------------|----------|
| SIGMA_INSTRUMENT 0.5/0.28 | Literature | TIGGE underdispersion (P2) |
| lead_days decay | Estimates | TIGGE multi-step MAE (P1) |
| diurnal rise constants | Rainstorm fit | 240K hourly obs (P2) |
| JSD thresholds 0.02/0.08 | Theoretical | TIGGE vs GFS historical (P3) |
| alpha base values | Theoretical | Model vs market Brier (after 200+ settlements) |
| Opening Hunt 30-min | Hypothesis | Token price trajectory (P2) |

## The SQL Conversion Principle

ALL data enters Zeus through zeus.db via ETL scripts. No module reads raw files or rainstorm.db directly at runtime. ETL validates units, reconstructs timestamps, rejects contaminated rows, and logs rejection counts.
