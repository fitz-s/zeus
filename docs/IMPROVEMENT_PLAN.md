#  System Status & Data-Driven Improvement PlanZeus 

## 1. What Is Zeus?

Zeus is a Polymarket weather probability trading system. It:
- Monitors temperature prediction markets across 46 global cities
- Forecasts daily high temperatures using ensemble models (ECMWF ENS, TIGGE)
- Calibrates raw forecasts into tradeable probabilities via Platt scaling
- Executes paper/live trades on Polymarket when edge is detected

The system runs as launchd daemons (paper + live modes) with scheduled jobs for data collection, calibration, and trading cycles.

---

## 2. Current Data Inventory

### Core Tables (zeus-shared. 2.0 GB)db 

| Table | Rows | Cities | Description |
|-------|------|--------|-------------|
| **settlements** | 34,198 | 46 | Historical daily high temps (ground truth) |
| **observations** | 30,171 | 46 | WU weather observations |
| **observation_instants** | 1,107,567 | 46 | Hourly temperature readings |
| **market_events** | 7,926 | 47 | Polymarket contract definitions |
| **ensemble_snapshots** | 8,884 | 46 | ECMWF ENS probability forecasts |
| **calibration_pairs** | 22,781 | **9** | (forecast, outcome) training pairs |
| **solar_daily** | 34,718 | 46 | Sunrise/sunset times per city |
| **temp_persistence** | 915 | 46 | Temperature autocorrelation records |
| **diurnal_curves** | 4,416 | 46 | Hourly temperature shape curves |
| **platt_models** | 36 active | 9 clusters | Calibration models |

### Settlement History

- **38 cities**: Full history from 2023-11-09 to 2026-04-07 (~881 days each)
- **8 new cities**: Shorter history from 2026-01-11 to 2026-04-10 (~90 days)
  - Auckland, Busan, Cape Town, Jakarta, Jeddah, Kuala Lumpur, Lagos, Panama City

### Data Quality

- 0 fractional settlement values (all integer-rounded, enforced by 5-layer system)
- All 46 cities have settlements, observations, solar, persistence, diurnal data
- Southern Hemisphere season mapping fixed (6 SH cities)

---

## 3. Calibration  The Core BottleneckStatus 

### Current Coverage

**Only 9 cities have calibration pairs:**
> Atlanta, Chicago, Dallas, London, Miami, Munich, NYC, Paris, Seattle

These 9 cities have **36 Platt models** across 19 clusters:
- Average Brier score: **0.034** (excellent)
- Average training samples: **553 per model**
- Range: 0.001 (best) to 0.127 (worst)

### Why Only 9 Cities?

Calibration pairs require **both** historical ENS forecasts **and** settlement outcomes for the same date. Only 9 cities had sufficient TIGGE data.

### TIGGE  In ProgressBackfill 

- Status: **~31% complete** (~47h remaining)
- When complete: unlock calibration for **36 more cities**
- Expected: **45/46 cities** with Platt models (Auckland has no contracts)

---

## 4. Trading Readiness

| Tier | Cities | Count | Status |
|------|--------|-------|--------|
| **Tier 1: READY** | ATL, CHI, DAL, LDN, MIA, MUN, NYC, PAR, SEA | 9 | Calibrated + active markets |
| **Tier 2: MARKETS ONLY** | 36 cities (Austin, Beijing, etc.) | 36 | Active markets, no Platt models |
| **Tier 3: NO MARKETS** | Auckland | 1 | No contracts |

- **1,893 active market events** across **45 cities**
- Paper daemon running (PID 56068), live daemon stopped

---

## 5. Improvement Roadmap

### Phase A: After TIGGE (~47h)

| ID | Improvement | Impact | Effort |
|----|------------|--------|--------|
| A1 | Expand Platt to 45 cities | 5x tradeable cities | Auto (scripts exist) |

### Phase B: Immediately Actionable

| ID | Improvement | Data Available | Impact | Effort |
|----|------------|---------------|--------|--------|
| B1 | Observation instant features | 1.1M readings | Better Day0 floor | Medium |
| B2 | Cross-city correlation | 34K settlements | Portfolio risk mgmt | Medium |
| B3 | Persistence Bayesian prior | 915 records | Stale-ENS fallback | Low |
| B4 | Solar-adjusted high timing | 34K solar records | Better high-hour est | Low-Med |
| B5 | Diurnal p_high_set | 4.4K curves | Day0 confidence | Low |

### Phase C: Infrastructure

| ID | Improvement | Impact | Effort |
|----|------------|--------|--------|
| C1 | ECMWF bias correction | Better forecast inputs | Medium |
| C2 | Wire 7 unenforced contracts | Runtime safety | Medium |
| C3 | Live DB stability | Stable live trading | Low |

### Recommended Order

```
NOW:     B3, B5, C3
SOON:    B1, B4
TIGGE:   A1 (highest impact)
LATER:   B2, C1, C2
```
