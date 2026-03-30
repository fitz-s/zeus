# Zeus Progress

## Session 4 (2026-03-30)

### URGENT FIX: Cities.json — Airport Coordinates ✓

All 10 original cities had WRONG coordinates (city center, not airport).
Example: NYC was 40.7128 (Manhattan) instead of 40.7772 (LaGuardia).
This affected ALL P_raw calculations — ENS was fetching the wrong grid point.

Fixed: replaced with rainstorm validated config (16 cities, airport coords, ICAO stations).
New cities: Seoul, Shanghai, Tokyo, Austin, Denver, Houston.

### Track A: Paper Trading Daemon — RUNNING ✓

Zeus daemon running as launchd service (com.zeus.paper-trading).
8 APScheduler jobs active. Collecting live ENS snapshots (correct coordinates).

**Live stats after first run cycle:**
- ENS snapshots (live_v1): 30
- Paper trades: 8 (7 buy_no shoulder + 1 buy_yes center)
- Cities traded: NYC, Seattle, Dallas, San Francisco
- Risk limits enforcing correctly

### Track B: Rainstorm Data ETL

#### B0: Schema ✓
Added 8 new tables to db.py: forecast_skill, model_bias, market_price_history,
hourly_observations, diurnal_curves, historical_forecasts, model_skill, temp_persistence.

#### B1: WU Settlement Audit ✓
1,385 settlements analyzed:
- US cities: 85-99% match+off-by-one → WU reliable for settlement
- London: 75.5% mismatch = unit confusion (°C vs °F encoding), not real error
- OFF_BY_ONE rate 28-47% → confirms bin boundary discretization edge is real

#### B2: Ladder Backfill ETL ✓
53,581/53,600 rows imported, 19 rejected (error > 30°).
120 model_bias entries computed.

**Key finding: ECMWF has systematic warm bias**
| City | ECMWF Bias | Best Model | Best MAE |
|------|-----------|------------|----------|
| NYC  | +3.63°F   | ICON       | 2.47°F   |
| Atlanta | +3.88°F | GFS       | 4.32°F   |
| London | +0.42°C  | ICON       | 1.08°C   |
| Dallas | +1.65°F  | ICON       | 3.39°F   |
| Miami | +3.50°F   | ICON       | 2.18°F   |

**Action needed:** Zeus uses ECMWF ENS for P_raw. The warm bias means P_raw
systematically overestimates probability of high-temperature bins. Platt calibration
should correct this, but with only 6 MAM models, winter/summer bias is uncompensated.
When TIGGE provides historical ENS data, we can quantify and correct this directly.

#### B6: calibration_records.jsonl Inspection ✓
309 MB, ~1.34M records. Format: (city, target_date, source, lead_days, forecast, observed, error, unit).
Not probability calibration — temperature forecast errors. Usable for bias correction and skill analysis.

#### Remaining ETLs (deferred to Session 5)
- B3: Token price log → market_price_history (Opening Hunt timing validation)
- B4: Hourly observations → hourly_observations + diurnal_curves (Day0 signal)
- B5: Forecasts → historical_forecasts + model_skill (dynamic α)
- B7: Temperature persistence (ENS anomaly detection)
- B8: TIGGE priority index

### Data Assets (current)
| Asset | Count | Notes |
|-------|-------|-------|
| ENS snapshots | 306+ (276 backfill + 30 live) | Live growing continuously |
| Calibration pairs | 562 | From backfill settlements |
| Active Platt models | 6 | All MAM buckets |
| forecast_skill | 53,581 | 5 models × 7 leads × 10 cities |
| model_bias | 120 | Per city×season×source |
| Paper trades | 8 | 7 buy_no + 1 buy_yes |

### Test Summary: 154 tests all passing

---

## Previous Sessions
- Session 3: Paper trading single-cycle validated, all 5 limitations fixed
- Session 2: Integration layer, discovery pipelines, data clients
- Session 1: Phase 0 (GO) + Phase A (signal/calibration) + Phase C (execution)

---

## Next Session: Paper Trading Analysis + More ETL

**Priority 1:** After 24-48h of daemon running, analyze:
- Trade count, win rate on settled positions
- Edge source distribution (which edge_source is generating trades?)
- FDR filter effectiveness (how many edges found vs passed?)
- ENS snapshot growth rate

**Priority 2:** Remaining ETL (B3-B5, B7-B8)

**Priority 3:** Investigate ECMWF warm bias correction:
- Can we apply model_bias as a correction to P_raw before Platt?
- Would this improve calibration for uncovered buckets (DJF, JJA, SON)?

**Priority 4:** TIGGE data integration (when agent completes)

**Codebase stats:**
- 36 source files in src/
- 16 test files with 154 tests
- 8 script files
- 17 commits on main
