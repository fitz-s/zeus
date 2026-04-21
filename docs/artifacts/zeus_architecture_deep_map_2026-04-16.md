# Zeus Architecture Deep Map — Refactor Reference

> LEGACY EXTRACTION SOURCE - NOT DEFAULT READ.
> Durable orientation has been extracted into
> `docs/reference/zeus_architecture_reference.md`. Treat this file as
> historical evidence only; it does not create current architecture law.

> Generated: 2026-04-16. Scope: first-principles understanding for incremental refactor.
> System status: **STOPPED**. Data contamination cleaned. Rebuild in progress.

---

## 1. What Zeus Is

Zeus is a **live weather probability trading system** on Polymarket. It:
1. Downloads 51-member ECMWF ENS weather forecasts
2. Converts them to **P(daily high ≥ threshold)** via Monte Carlo simulation
3. Calibrates P_raw through extended Platt scaling
4. Fuses model probability with market probability (α-weighted Bayesian posterior)
5. Detects edge (P_posterior − market_price) with bootstrap confidence intervals
6. Sizes positions via quarter-Kelly with multi-level risk caps
7. Executes limit orders on Polymarket CLOB
8. Monitors held positions for exit signals (edge reversal, divergence panic)
9. Harvests settlements and updates calibration training data

**51 cities** | **4 settlement source types** | **40 °C / 11 °F markets** | **35 timezones**

---

## 2. Codebase Scale

| Layer | Files | LOC |
|-------|-------|-----|
| `src/` | 116 | 37,456 |
| `scripts/` | 114 | 25,424 |
| `tests/` | 117 | 49,488 |
| **Total** | **347** | **~112,368** |

### Largest files (symptoms of structural problems)

| File | LOC | Problem |
|------|-----|---------|
| `src/state/db.py` | 4,472 | God file: 41 CREATE TABLE + all queries + all connections |
| `src/engine/replay.py` | 2,068 | Historical replay engine |
| `src/engine/evaluator.py` | 1,632 | Monolithic edge detector with 60+ field dataclasses |
| `src/state/portfolio.py` | 1,605 | Position god object with 90+ fields |
| `src/data/daily_obs_append.py` | 1,324 | Multi-source observation collector |
| `src/engine/cycle_runtime.py` | 1,197 | Extracted runtime helpers (deps monkeypatch) |
| `src/execution/harvester.py` | 990 | Post-settlement processing with dual-write |
| `src/execution/exit_lifecycle.py` | 799 | Exit state machine |
| `src/engine/monitor_refresh.py` | 690 | Monitoring probability recompute |
| `src/main.py` | 637 | APScheduler daemon |
| `src/state/chain_reconciliation.py` | 589 | Chain API reconciliation (3 rules) |

---

## 3. Zone Architecture (K0–K4)

Zeus uses a 5-zone governance model defined in `architecture/zones.yaml`:

### K0 — Frozen Kernel (semantic atoms, lifecycle law)
**Files:** `src/contracts/`, `src/types/`, `src/state/ledger.py`, `src/state/projection.py`, `src/state/lifecycle_manager.py`
**Policy:** Schema packet required. Principal architect review. Spec + invariant + schema diff + tests + parity.

### K1 — Governance (policy, risk, overrides, strategy governance)
**Files:** `src/riskguard/`, `src/control/`
**Policy:** Feature packet. Policy resolution example + behavior change example + tests.

### K2 — Runtime (orchestration, execution, reconciliation)
**Files:** `src/engine/`, `src/execution/`, `src/state/`, `src/observability/`, `src/data/`
**Policy:** Refactor packet. Runtime trace + tests.

### K3 — Extension (math, signal, calibration)
**Files:** `src/signal/`, `src/strategy/`, `src/calibration/`
**Policy:** Feature packet. No-authority-diff statement + tests + replay or waiver.

### K4 — Experimental (disposable)
**Files:** `notebooks/`, `exploratory/`, `scripts/ad_hoc/`
**Policy:** Isolation statement only.

### Forbidden Import Rules
| ID | Source | Cannot Import | Rationale |
|----|--------|---------------|-----------|
| BI-01 | observability | executor, polymarket_client | Read-only surfaces |
| BI-02 | control | signal, strategy, calibration | Control ≠ math |
| BI-03 | signal/strategy/calibration | riskguard, control | Extension ≠ governance |
| BI-04 | signal/strategy/calibration | ledger, projection, lifecycle_manager | Math ≠ lifecycle authority |
| BI-05 | engine, execution | portfolio | Move from JSON to canonical ledger |

---

## 4. Invariants (architecture/invariants.yaml)

| ID | Statement | Why |
|----|-----------|-----|
| INV-01 | Exit is not local close | Monitor decisions ≠ terminal economic closure |
| INV-02 | Settlement is not exit | Exit execution and settlement are distinct lifecycle facts |
| INV-03 | Canonical authority is append-first and projection-backed | Event stream + deterministic projection |
| INV-04 | strategy_key is sole governance key | edge_source/discovery_mode/entry_method are metadata |
| INV-05 | Risk must change behavior | Advisory-only risk = theater |
| INV-06 | Point-in-time truth beats hindsight truth | Decision-time snapshot ≠ latest snapshot |
| INV-07 | Lifecycle grammar is finite and authoritative | No arbitrary state strings |
| INV-08 | Canonical write path has one transaction boundary | Event + projection succeed/fail together |
| INV-09 | Missing data is first-class truth | Explicit facts, not log noise |
| INV-10 | LLM output is never authority | Only valid after packet + gates + evidence |

---

## 5. Probability Chain (complete formula)

```
51 ENS members (ECMWF IFS 0.25°)
    │
    ▼  per-member daily max temperature extraction
    │
    ▼  sensor noise injection (instrument_noise_f=0.5°F, instrument_noise_c=0.28°C)
    │  + WMO rounding (±0.5°F or ±0.28°C)
    │  Override: HKO 0.1°C, Taipei CWA 0.1°C
    │
    ▼  Monte Carlo simulation (n_mc=5000 per cycle, 10000 for ensembles)
    │  p_raw_vector = fraction of MC draws exceeding threshold
    │
    ▼  Extended Platt calibration
    │  P_cal = sigmoid(A × logit(P_raw) + B × lead_days + C)
    │  4 maturity levels: L1(≥150 pairs), L2(≥50), L3(≥15), L4(fallback)
    │  200 bootstrap parameter sets
    │  Authority filter: VERIFIED only
    │
    ▼  Bayesian fusion (market_fusion.py)
    │  P_posterior = α × P_cal + (1-α) × P_market
    │  compute_alpha(): base_alpha[level] ± spread/agreement/lead/freshness adjustments
    │  Tail scaling factor: 0.5
    │  Vig treatment for complete market vectors
    │
    ▼  Edge detection + sizing (evaluator.py)
    │  edge = P_posterior - entry_price (for BUY_YES)
    │  500 bootstrap CIs
    │  Family hypothesis FDR scanning (BH procedure, α=0.10)
    │  Kelly sizing: f* = edge / (1 - entry_price) × kelly_multiplier(0.25)
    │  Position caps: max_single=10%, max_heat=50%, max_correlated=25%, max_city=20%
    │
    ▼  Limit order execution
        order_type: limit_only
        limit_offset: 2%
        Mode timeouts: opening_hunt=4h, update_reaction=1h, day0=15min
        Share quantization: BUY rounds UP
```

---

## 6. Discovery Modes

| Mode | Schedule | Purpose |
|------|----------|---------|
| `opening_hunt` | Every 30min | Fresh markets, new opportunities |
| `update_reaction` | UTC 07:00, 09:00, 19:00, 21:00 | React to forecast updates |
| `day0_capture` | Every 15min | Near-settlement (≤6h) high-conviction trades |

### Cycle Flow (`cycle_runner.run_cycle → cycle_runtime.*`)

```
1. chain_sync          — Polymarket CLOB API → reconcile positions
2. reconcile_pending   — Track pending fills/cancels
3. monitoring_phase    — For held positions:
   a. monitor_refresh: recompute P_posterior
   b. evaluate_exit: Position.evaluate_exit(ExitContext)
   c. execute_exit: exit_lifecycle state machine
4. entry_blockers (8)  — Check: mode active, bankroll, risk limits, etc.
5. discovery_phase     — Scan markets → evaluate → execute → record
6. save                — Atomic portfolio write
```

### 8 Entry Blockers (implicit state machine in cycle_runner)
These are if/elif checks that prevent discovery:
1. Mode not active
2. No bankroll (wallet check failed)
3. Risk limits breached
4. Portfolio heat exceeded
5. Too many pending orders
6. Cycle lock contention
7. Data staleness
8. Control plane override

---

## 7. Database Architecture

### Physical Layout

| DB File | Purpose | Current State |
|---------|---------|---------------|
| `zeus-world.db` | Shared world data (observations, ENS, calibration) | 424 MB, 10 tables |
| `zeus_trades.db` | Trade data (positions, decisions, chronicle) | Empty shell (0 tables) |
| `zeus.db` | Legacy (deprecated) | Empty shell (0 tables) |
| `risk_state.db` | Risk state | 1 table, 0 rows |
| `zeus_backtest.db` | Backtest/audit | Does not exist |

**Connection pattern:** `get_trade_connection_with_world()` ATTACHes zeus-world.db as `world` to zeus_trades.db connection. All 4 DBs use WAL mode.

### Complete Table Registry (41 CREATE TABLE in db.py)

#### zeus-world.db (shared world data)

| Table | Rows | Purpose |
|-------|------|---------|
| `observations` | 42,414 | Daily high temperature (settlement truth) |
| `observation_instants` | 859,668 | Hourly temperature readings |
| `solar_daily` | 38,271 | Sunrise/sunset times |
| `data_coverage` | 114,991 | Data ingestion ledger (WRITTEN/MISSING/PENDING) |
| `ensemble_snapshots` | 0 | 51-member ENS forecasts |
| `calibration_pairs` | 0 | P_raw vs actual outcome training pairs |
| `platt_models` | 0 | Calibration model parameters |
| `settlements` | 0 | Polymarket settlement results |
| `control_overrides` | 1 | Control plane override flags |
| `calibration_decision_group` | 0 | Canonical bucket assignments |
| `forecasts` | **TABLE MISSING** | NWP model forecasts (5 models) |

#### zeus_trades.db (trade data — all tables need init_schema())

| Table | Purpose |
|-------|---------|
| `trade_decisions` | Full audit trail per trade decision (~40 columns) |
| `chronicle` | Append-only trade event log |
| `decision_log` | Legacy decision log (settlement dual-write target #1) |
| `position_events` | Canonical event ledger (settlement dual-write target #2) |
| `position_current` | Projection of latest position state (30 columns) |
| `shadow_signals` | Shadow/advisory signal outputs |
| `probability_trace_fact` | Full probability snapshot per evaluation |
| `selection_family_fact` | FDR family definition |
| `selection_hypothesis_fact` | Individual hypotheses within FDR family |
| `model_eval_run` | Calibration model evaluation runs |
| `model_eval_point` | Per-point model evaluation data |
| `promotion_registry` | Model promotion history |
| `strategy_health` | Strategy performance tracking |
| `token_suppression_history` | Anti-churn token cooldown log |
| `control_overrides_history` | Control override audit trail |
| `market_events` | Market lifecycle events |
| `token_price_log` | Token price history |
| `market_price_history` | VWMP price history |
| `forecast_skill` | Forecast accuracy by city/model/lead |
| `forecast_error_profile` | Error distribution by city/model |
| `model_bias` | Systematic bias by city/model |
| `hourly_observations` | Derived hourly observations |
| `diurnal_curves` | Diurnal temperature curves |
| `diurnal_peak_prob` | Peak probability from diurnal analysis |
| `day0_residual_fact` | Day-0 residual analysis |
| `historical_forecasts` | Historical forecast snapshots |
| `model_skill` | Model skill scores |
| `temp_persistence` | Temperature persistence/autocorrelation |
| `availability_fact` | Data availability tracking |
| `replay_results` | Replay/backtest results |

#### zeus_backtest.db (separate)

| Table | Purpose |
|-------|---------|
| `backtest_runs` | Backtest run metadata |
| `backtest_outcome_comparison` | Per-trade backtest vs live comparison |

---

## 8. Settlement Sources — Complete Map

### 4 Source Types, 51 Cities

| Type | Implementation | DB Source Column | Cities | Count |
|------|---------------|-----------------|--------|-------|
| **wu_icao** | WU ICAO History API (`daily_obs_append.py:CITY_STATIONS`) | `wu_icao_history` | All US (11), Americas (4), Europe (8), Asia (15), Oceania (2), Africa (2), Middle East (2), SE Asia (3) | 47 |
| **hko** | HKO Open Data API (separate handler in `daily_obs_append.py`) | `hko_daily_api` | Hong Kong | 1 |
| **ogimet_metar** | Ogimet METAR parsing (`daily_obs_append.py:OGIMET_CITIES`) | `ogimet_metar_ltfm` / `ogimet_metar_uuww` | Istanbul (LTFM), Moscow (UUWW) | 2 |
| **noaa** | Configured in `cities.json` but NOT separately implemented | N/A | Tel Aviv (actually uses wu_icao via LLBG) | 1* |

**Known discrepancies:**
1. Istanbul: `cities.json` says `settlement_source_type: "noaa"` but actual collection uses Ogimet METAR
2. Moscow: Same as Istanbul
3. Tel Aviv: `cities.json` says `settlement_source_type: "noaa"` but `CITY_STATIONS` has LLBG as wu_icao
4. Taipei: `config.py` City dataclass has `cwa_station` field type, but Taipei uses wu_icao (RCSS) in practice
5. Hong Kong: Intentionally NOT in CITY_STATIONS dict — HKO is authoritative settlement source

### WU ICAO Station Assignments (47 cities)

```
US (°F): NYC=KLGA, Chicago=KORD, Atlanta=KATL, Austin=KAUS, Dallas=KDAL,
         Denver=KBKF, Houston=KHOU, LA=KLAX, Miami=KMIA, SF=KSFO, Seattle=KSEA
Americas: Buenos Aires=SAEZ, Mexico City=MMMX, Sao Paulo=SBGR, Toronto=CYYZ
Europe: London=EGLC, Paris=LFPG, Munich=EDDM, Madrid=LEMD, Milan=LIMC,
        Warsaw=EPWA, Amsterdam=EHAM, Helsinki=EFHK, Ankara=LTAC, Tel Aviv=LLBG
China: Beijing=ZBAA, Shanghai=ZSPD, Shenzhen=ZGSZ, Chengdu=ZUUU,
       Chongqing=ZUCK, Wuhan=ZHHH, Guangzhou=ZGGG
Asia: Tokyo=RJTT, Seoul=RKSI, Taipei=RCSS, Singapore=WSSS, Lucknow=VILK,
      Karachi=OPKC, Manila=RPLL, Busan=RKPK
Oceania: Wellington=NZWN, Auckland=NZAA
Africa: Lagos=DNMM, Cape Town=FACT
Middle East: Jeddah=OEJN
SE Asia: Kuala Lumpur=WMKK, Jakarta=WIHH
Latin America: Panama City=MPMG
```

---

## 9. Core File Map — Function Signatures

### src/main.py (637 LOC) — APScheduler daemon

```python
# Entry point
main()                              # ZEUS_MODE=live validation, APScheduler setup
run_single_cycle()                  # One-shot test (all modes + harvester)

# Scheduled jobs
_run_mode(mode: DiscoveryMode)      # Wrapper: error handling + _cycle_lock
_harvester_cycle()                  # Settlement harvester
_wu_daily_collection()              # WU observation fetch
_ecmwf_open_data_cycle()            # ECMWF open ENS
_etl_recalibrate()                  # Weekly: ETL + Platt refit + replay audit
_automation_analysis_cycle()        # Daily calibration diagnostics
_write_heartbeat() -> None          # 60s heartbeat JSON
_startup_wallet_check(clob=None)    # P7 fail-closed wallet gate
_startup_data_health_check(conn)    # Deferred data action warnings
```

### src/engine/cycle_runner.py (~350 LOC) — Orchestrator

```python
run_cycle(mode: DiscoveryMode) -> dict   # Main entry: delegates to cycle_runtime via deps

# Constants
KNOWN_STRATEGIES = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}
MODE_PARAMS: dict[DiscoveryMode, ...]    # Scanner params per mode
get_connection = get_trade_connection_with_world  # DI seam

# DI pattern: deps = sys.modules[__name__]
# All heavy logic in cycle_runtime.py functions that take deps parameter
```

### src/engine/cycle_runtime.py (1,197 LOC) — Runtime helpers

```python
# All functions take deps=sys.modules[cycle_runner] for monkeypatch DI

run_chain_sync(portfolio, clob, conn, *, deps)
cleanup_orphan_open_orders(portfolio, clob, *, deps, conn) -> int
entry_bankroll_for_cycle(portfolio, clob, *, deps) -> (float|None, dict)
materialize_position(..., *, state, env, bankroll_at_entry, deps) -> Position
reconcile_pending_positions(portfolio, clob, tracker, *, deps) -> dict
execute_monitoring_phase(conn, clob, portfolio, artifact, tracker, summary, *, deps) -> (bool, bool)
execute_discovery_phase(conn, clob, portfolio, ..., *, env, deps) -> (bool, bool)
```

### src/engine/evaluator.py (1,632 LOC) — Edge detection

```python
@dataclass
class MarketCandidate:    # 60+ fields: city, threshold, direction, prices, ENS, calibration...
class EdgeDecision:       # 60+ fields: edge, kelly_fraction, confidence, p_values, sizing...

evaluate_candidate(candidate: MarketCandidate, ...) -> EdgeDecision  # Pure function
# Contains: FDR scanning, bootstrap CI, family hypothesis testing
```

### src/state/portfolio.py (1,605 LOC) — Position management

```python
@dataclass
class Position:           # ~90 fields
    # Identity: trade_id, market_id, city, direction, threshold
    # Sizing: size_usd, entry_price, shares, cost_basis_usd
    # Entry: entry_method, decision_snapshot_id
    # Strategy: strategy_key, edge_source
    # Lifecycle: state (LifecycleState), exit_state (ExitState)
    # Chain: chain_state (ChainState), chain_shares, token_id
    # Monitor: neg_edge_count, last_monitor_*
    # Exit: exit_retry_count, last_exit_order_id
    # P&L: exit_price, pnl
    
    def evaluate_exit(ctx: ExitContext) -> ExitDecision   # Position knows how to exit itself
    # Properties: effective_shares, unrealized_pnl, is_quarantine_placeholder, is_admin_exit

@dataclass(frozen=True)
class ExitContext:        # 14 fields of exit authority
    def missing_authority_fields() -> list[str]

@dataclass
class ExitDecision:       # should_exit, reason, urgency, selected_method, trigger

class PortfolioState:     # positions, bankroll, recent_exits, ignored_tokens

# Functions
load_portfolio() -> PortfolioState     # DB-first, JSON-fallback
save_portfolio(portfolio)              # Atomic JSON write
add_position(portfolio, position)      # Dedup merge
void_position(portfolio, trade_id)
total_exposure_usd(portfolio) -> float
portfolio_heat_for_bankroll(portfolio, bankroll) -> float
```

### src/state/db.py (4,472 LOC) — Database god file

```python
# Connections
get_trade_connection() -> sqlite3.Connection      # zeus_trades.db
get_world_connection() -> sqlite3.Connection      # zeus-world.db
get_backtest_connection() -> sqlite3.Connection    # zeus_backtest.db
get_trade_connection_with_world() -> sqlite3.Connection  # ATTACH world
init_schema(conn)                                  # Create all tables

# Key query functions (~60 total)
record_trade_decision(conn, ...)
record_execution_fact(conn, ...)
record_outcome_fact(conn, ...)
record_settlement_event(conn, ...)
log_probability_trace(conn, ...)
record_opportunity_fact(conn, ...)
update_strategy_health(conn, ...)
load_portfolio_loader_view(conn, ...) -> list[dict]
query_active_positions(conn) -> list[dict]
# ... many more
```

### src/state/lifecycle_manager.py (~340 LOC) — Lifecycle FSM

```python
class LifecyclePhase(Enum):
    PENDING_ENTRY, ACTIVE, DAY0_WINDOW, PENDING_EXIT,
    ECONOMICALLY_CLOSED, SETTLED, VOIDED, QUARANTINED, ADMIN_CLOSED, UNKNOWN

# Legal transitions (LEGAL_LIFECYCLE_FOLDS)
fold_lifecycle_phase(current, target) -> LifecyclePhase  # Validates transitions

# 9 transition functions
enter_pending_exit_runtime_state()
enter_day0_window_runtime_state()
rescue_pending_runtime_state()
enter_chain_quarantined_runtime_state()
enter_economically_closed_runtime_state()
enter_settled_runtime_state()
enter_admin_closed_runtime_state()
enter_voided_runtime_state()
release_pending_exit_runtime_state()
```

### src/state/ledger.py (~170 LOC) + projection.py (~120 LOC) — Event sourcing

```python
# ledger.py
CANONICAL_POSITION_EVENT_COLUMNS  # 18 fields
append_event_and_project(conn, event, projection)     # Transactional pair
append_many_and_project(conn, events, projection)

# projection.py
CANONICAL_POSITION_CURRENT_COLUMNS  # 30 fields
upsert_position_current(conn, ...)
validate_event_projection_pair(event, projection)
```

### src/state/chain_reconciliation.py (589 LOC) — Chain API sync

```python
class ChainPosition:      # token_id, size, avg_price, cost, condition_id
class ChainPositionView:  # Frozen snapshot with has_token(), get_position()

reconcile(portfolio, chain_positions, conn=None) -> dict
# Rules: match→SYNCED, local-only→VOID, chain-only→QUARANTINE
# Guard: skip_voiding when API returns 0 positions (incomplete API)
check_quarantine_timeouts(portfolio) -> int  # 48h expiry
```

### src/execution/executor.py (404 LOC) — Limit order execution

```python
execute_entry(clob, candidate, sizing, *, deps) -> dict  # BUY limit order
execute_exit(clob, position, *, deps) -> dict              # SELL limit order
cancel_order(clob, order_id) -> dict
# Mode timeouts: OH=4h, UR=1h, D0=15min
# Share quantization: BUY rounds UP
# Dynamic limit price with 2% offset
```

### src/execution/harvester.py (990 LOC) — Post-settlement

```python
harvest(conn, portfolio, clob, *, deps) -> dict
# Flow: detect settlement → match to position → compute P&L → update state
# DUAL WRITE: decision_log (legacy) FIRST, THEN position_events (canonical)
# Fallback: portfolio.positions for snapshot resolution (stale JSON risk)
# Creates calibration_pairs from settlement outcome
```

### src/execution/exit_lifecycle.py (799 LOC) — Exit state machine

```python
# Exit states: "" → exit_intent → sell_placed → sell_pending → sell_filled
#              OR → retry_pending → backoff_exhausted
# 10 retries max, exponential backoff
# NO fallback after backoff exhaustion (hold to settlement forever)
# Golden rule: confirmed CLOB fill creates economic close, not settlement
```

### src/engine/monitor_refresh.py (690 LOC) — Monitoring

```python
refresh_probabilities(conn, positions, *, deps) -> dict
# Full P recompute: ENS → MC → calibrate → fuse → update ExitContext
# Authority gate: can be silently bypassed via exception swallowing
```

### src/signal/ensemble_signal.py (439 LOC) — ENS → P_raw

```python
p_raw_vector_from_maxes(member_maxes, threshold, noise_std, n_mc) -> np.ndarray
# "Naive member counting is forbidden"
# 51 members → instrument noise injection → MC → P_raw vector
# Override noise: HKO=0.1°C, Taipei CWA=0.1°C
```

### src/strategy/market_fusion.py (~200 LOC) — α-weighted posterior

```python
compute_alpha(level, spread, agreement, lead_days, freshness) -> float
compute_posterior(p_cal, p_market, alpha) -> float
# Raises AuthorityViolation if authority_verified=False (K4 contract)
# Tail scaling at 0.5
# Vig treatment for complete market vectors
```

### src/calibration/ — Platt calibration system

```python
# platt.py (230 LOC)
# P_cal = sigmoid(A × logit(P_raw) + B × lead_days + C)
fit_extended_platt(pairs, n_bootstrap=200) -> PlattParams

# manager.py (300 LOC)
# Bucket routing, maturity gate, hierarchical fallback
get_calibration(city, season, lead_days, conn) -> PlattParams

# store.py (350 LOC)
# SQLite CRUD for calibration_pairs + platt_models
# Authority filter: VERIFIED only (default)

# decision_group.py (170 LOC)
# compute_id() → SHA-1. ONLY permitted producer. Rejects naive datetimes.

# drift.py (100 LOC)
# Hosmer-Lemeshow χ², directional failure detection

# effective_sample_size.py (280 LOC) — SHADOW_ONLY
# blocked_oos.py (280 LOC) — SHADOW_ONLY
```

### src/data/daily_obs_append.py (1,324 LOC) — Observation collection

```python
CITY_STATIONS: dict[str, tuple[str, str, str]]   # 47 cities → (ICAO, CC, unit)
OGIMET_CITIES = {"Istanbul": "LTFM", "Moscow": "UUWW"}
WU_SOURCE = "wu_icao_history"

fetch_wu_daily(city, date) -> WuDailyFetchResult      # WU ICAO History API
fetch_hko_daily(date) -> HkoDailyFetchResult           # HKO Open Data API
fetch_ogimet_metar(city, icao, date) -> OgimetResult   # Ogimet METAR parsing
append_daily_observation(conn, city, date, temp_high, source, authority)
```

### src/data/ecmwf_open_data.py (139 LOC) — Per-cycle ENS collector

```python
# STEP_HOURS = [24, 48, 72, 96, 120, 144, 168]
# data_version = 'open_ens_v1'
# Downloads from ECMWF Open Data API
# DISTINCT from TIGGE bulk store (which is in 51 source data/scripts/)
```

---

## 10. Contracts Layer (20 files, 2,517 LOC)

Typed semantic boundaries preventing drift between subsystems:

| Contract | Purpose | Key Type |
|----------|---------|----------|
| `semantic_types.py` | Direction, DecisionSnapshotRef, EntryMethod | Enums |
| `settlement_semantics.py` | Settlement truth encoding | SettlementSemantics |
| `execution_price.py` | VWMP+fee execution price (not implied probability) | ExecutionPrice |
| `edge_context.py` | Edge calculation context | EdgeContext |
| `epistemic_context.py` | Epistemic state at decision time | EpistemicContext |
| `alpha_decision.py` | Alpha blending rationale | AlphaDecision |
| `calibration_bins.py` | Canonical calibration bin grids | CalibrationBins |
| `decision_evidence.py` | Entry vs exit evidence asymmetry | DecisionEvidence |
| `execution_intent.py` | Order intent before execution | ExecutionIntent |
| `expiring_assumption.py` | Time-bombed assumptions with TTL | ExpiringAssumption[T] |
| `hold_value.py` | Carry value accounting (opp cost) | HoldValue |
| `tail_treatment.py` | Extreme probability handling | TailTreatment |
| `vig_treatment.py` | Market vig decomposition | VigTreatment |
| `ensemble_snapshot_provenance.py` | data_version quarantine | Provenance |
| `provenance_registry.py` | INV-13: constant registration | ProvenanceEntry |
| `reality_contract.py` | P10: external reality assumptions | RealityContract |
| `reality_contracts_loader.py` | YAML loader for reality contracts | Loader |
| `reality_verifier.py` | Runtime verification of contracts | Verifier |
| `exceptions.py` | ZeusError hierarchy | ZeusError |

---

## 11. Settings Configuration (config/settings.json)

```
Capital: $150 base, $5 live safety cap, $5 smoke test cap
Mode: "live"
Ensemble: ECMWF IFS 0.25° (51 members) primary, GFS 0.25° (31 members) crosscheck
Monte Carlo: n_mc=5000 per cycle
Calibration: Extended Platt, n_bootstrap=200
Edge: n_bootstrap=500, FDR α=0.10
Alpha: base_alpha by maturity level (L1=0.65, L2=0.55, L3=0.40, L4/5=0.25)
Sizing: Quarter-Kelly (0.25), max single=10%, max heat=50%, max correlated=25%, max city=20%
Execution: Limit only, 2% offset, 600s timeout
Exit: 2 consecutive confirmations, 4h near-settlement window
Feature flags: EXECUTION_PRICE_SHADOW=true, CANONICAL_EXIT_PATH=false
21 HARDCODED parameters with self-documenting replacement criteria
```

---

## 12. Data Rebuild Status

### What Exists (Layer 0)

| Data | Rows | Status | Source |
|------|------|--------|--------|
| observations | 42,414 | ✅ ALL VERIFIED | WU/HKO/Ogimet |
| observation_instants | 859,668 | ✅ | Open-Meteo hourly |
| solar_daily | 38,271 | ✅ | Open-Meteo solar |
| data_coverage | 114,991 | ✅ (3,464 MISSING) | Audit ledger |
| TIGGE GRIB on disk | 167,666 files | 🔄 Downloading | 46 cities, 2023-10→2026-04 |

### What's Empty/Missing

| Data | Rows | Blocker |
|------|------|---------|
| ensemble_snapshots | 0 | TIGGE GRIB not ingested to DB |
| calibration_pairs | 0 | Needs ensemble_snapshots + observations |
| platt_models | 0 | Needs calibration_pairs |
| settlements | 0 | Needs observations + Polymarket results |
| forecasts | TABLE MISSING | DDL not run + 5 NWP backfill needed |
| zeus_trades.db | 0 tables | Needs init_schema() |

### TIGGE Pipeline

- Location: `51 source data/scripts/` (OUTSIDE zeus repo)
- Scripts: `tigge_daily_pipeline.py` (15min cron), `tigge_full_history_pipeline.py` (hourly cron)
- GRIB data: 167,666 files, 46 cities, step_024 only (24h lead)
- Clean windows: A=220 days (2023-11-09→2024-06-15), B=224 days (2024-06-17→2025-01-26)
- DB repair 2026-04-12: switched zeus-shared.db → zeus-world.db
- After canary: ensemble_snapshots 0→65,151, calibration_pairs 0→627,737
- NOTE: Current DB audit shows 0 rows — possible subsequent wipe

### Data Rebuild Dependency Chain

```
Layer 0: Physical data (DONE or IN-PROGRESS)
  ├── observations ✅
  ├── observation_instants ✅
  ├── solar_daily ✅
  ├── data_coverage ✅
  └── TIGGE GRIB files 🔄

Layer 1: Ingest / Create
  ├── forecasts TABLE (DDL + 5 NWP model backfill via Open-Meteo Previous Runs)
  ├── ensemble_snapshots (TIGGE GRIB → DB via 51 source data/scripts/)
  └── settlements (observations + Polymarket settlement events)

Layer 2: Derived tables (needs Layer 1)
  ├── calibration_pairs (ensemble_snapshots + observations)
  ├── forecast_skill (forecasts + observations)
  ├── model_skill (forecasts + observations)
  ├── forecast_error_profile (forecasts + observations)
  ├── model_bias (forecasts + observations)
  └── temp_persistence (observations)

Layer 3: Models (needs Layer 2)
  ├── platt_models (calibration_pairs via refit_platt.py)
  ├── diurnal_curves (observation_instants)
  ├── hourly_observations (observation_instants)
  └── historical_forecasts (forecasts)

Layer 4: Runtime readiness (needs Layer 3)
  ├── init_schema() → create zeus_trades.db tables
  ├── risk_state initialization
  └── Zeus daemon can start
```

---

## 13. Structural Pathologies (6 confirmed)

### P1: Settlement Dual-Write Order Inversion
**Where:** `harvester.py`
**What:** Writes to `decision_log` (legacy) BEFORE `position_events` (canonical). If crash between writes, legacy has record but canonical doesn't. Violates INV-03.
**Fix:** Swap order or make atomic.

### P2: Position God Object
**Where:** `portfolio.py:Position`
**What:** ~90 fields mixing identity, snapshot, state machine, monitoring state, exit logic. `evaluate_exit()` makes Position responsible for its own exit decision.
**Fix:** Decompose into Position (identity + sizing) + PositionState (lifecycle) + MonitorState + ExitEvaluator.

### P3: Opaque Dependency Injection
**Where:** `cycle_runner.py` → `cycle_runtime.py`
**What:** `deps=sys.modules[__name__]` passes the entire module as a bag. Runtime functions call `deps.evaluator`, `deps.executor`, etc. with no type safety.
**Fix:** Explicit dependency container with typed fields.

### P4: Evaluator Monolith
**Where:** `evaluator.py` (1,632 LOC)
**What:** Single file contains MarketCandidate (60+ fields), EdgeDecision (60+ fields), and the entire evaluation pipeline without abstraction.
**Fix:** Pipeline pattern: ENS → Calibrate → Fuse → Edge → Size as composable stages.

### P5: Shadow API Layer
**Where:** `scripts/` (114 files, 25K LOC)
**What:** Nearly 1:1 with src files. Many scripts directly import and call internal functions, bypassing safety gates defined in src/.
**Impact:** Changes to src/ internal APIs can break scripts silently. Scripts can write to DB without going through canonical write paths.

### P6: Implicit State Machine
**Where:** `cycle_runner.py`
**What:** 8 sequential if/elif checks determine whether discovery phase runs. No explicit state type, no exhaustive pattern matching.
**Fix:** Explicit CycleState enum with transition table (like lifecycle_manager.py).

---

## 14. Contamination Vectors (7 found)

| # | Location | Vector | Risk |
|---|----------|--------|------|
| C1 | harvester.py | Fallback to `portfolio.positions` for snapshot resolution | Stale JSON overwrites DB truth |
| C2 | monitor_refresh.py | Authority gate silently bypassed on exception | Unverified data enters probability chain |
| C3 | chain_reconciliation.py | In-memory state overrides DB lifecycle phase | Phantom positions preserved |
| C4 | exit_lifecycle.py | Backoff exhaustion → no fallback | Position held to settlement forever |
| C5 | replay.py | Synthetic timestamp fallback for missing decision references | Corrupted point-in-time truth |
| C6 | harvester.py | Dual-write order (legacy before canonical) | Crash = inconsistent truth surfaces |
| C7 | ledger.py | sequence_no not enforced unique per position | Projection replay may skip/duplicate events |

---

## 15. Scripts Inventory (114 files, categorized)

### Data Management (backfill + ETL)
`backfill_wu_daily_all.py`, `backfill_hko_daily.py`, `backfill_ogimet_metar.py`, `backfill_hourly_openmeteo.py`, `backfill_solar_openmeteo.py`, `backfill_openmeteo_previous_runs.py`, `backfill_ens.py`, `backfill_tigge_snapshot_p_raw.py`, `backfill_observations_from_settlements.py`, `backfill_cluster_taxonomy.py`, `backfill_current_market_price_snapshots.py`, `backfill_exit_telemetry.py`, `backfill_outcome_fact.py`, `backfill_probability_traces_from_opportunities.py`, `backfill_recent_exits_attribution.py`, `backfill_semantic_snapshots.py`, `backfill_trade_decision_attribution.py`, `backfill_truth_metadata.py`, `onboard_cities.py`

### ETL (derived table builders)
`etl_asos_wu_offset.py`, `etl_diurnal_curves.py`, `etl_forecast_error_profiles.py`, `etl_forecast_skill_from_forecasts.py`, `etl_historical_forecasts.py`, `etl_hourly_observations.py`, `etl_solar_times.py`, `etl_temp_persistence.py`, `etl_tigge_calibration.py`, `etl_tigge_direct_calibration.py`, `etl_tigge_ens.py`, `ingest_grib_to_snapshots.py`

### Calibration
`generate_calibration_pairs.py`, `rebuild_calibration_pairs_canonical.py`, `rebuild_calibration.py`, `refit_platt.py`, `validate_dynamic_alpha.py`, `baseline_experiment.py`, `investigate_ecmwf_bias.py`, `build_correlation_matrix.py`

### Audit / Monitoring
`audit_architecture_alignment.py`, `audit_city_data_readiness.py`, `audit_divergence_counterfactual.py`, `audit_divergence_exit_counterfactual.py`, `audit_divergence_hindsight.py`, `audit_divergence_postchange.py`, `audit_divergence_thresholds.py`, `audit_paper_explainability.py`, `audit_polymarket_city_settlement.py`, `audit_realtime_pnl.py`, `audit_replay_completeness.py`, `audit_replay_fidelity.py`, `audit_time_semantics.py`, `data_completeness_audit.py`, `automation_analysis.py`, `check_advisory_gates.py`, `check_daemon_heartbeat.py`, `check_kernel_manifests.py`, `check_module_boundaries.py`, `check_work_packets.py`, `semantic_linter.py`, `verify_truth_surfaces.py`, `diagnose_truth_surfaces.py`, `diagnose_center_buy_failure.py`, `validate_assumptions.py`

### Topology Doctor (meta-system)
`topology_doctor.py`, `topology_doctor_artifact_checks.py`, `topology_doctor_cli.py`, `topology_doctor_closeout.py`, `topology_doctor_context_pack.py`, `topology_doctor_core_map.py`, `topology_doctor_data_rebuild_checks.py`, `topology_doctor_digest.py`, `topology_doctor_docs_checks.py`, `topology_doctor_map_maintenance.py`, `topology_doctor_packet_prefill.py`, `topology_doctor_policy_checks.py`, `topology_doctor_receipt_checks.py`, `topology_doctor_reference_checks.py`, `topology_doctor_registry_checks.py`, `topology_doctor_script_checks.py`, `topology_doctor_source_checks.py`, `topology_doctor_test_checks.py`

### Replay / Backtest
`run_replay.py`, `replay_parity.py`, `capture_replay_artifact.py`, `profit_validation_replay.py`

### Migration
`migrate_add_authority_column.py`, `migrate_cluster_to_city.py`, `migrate_to_isolated_dbs.py`, `nuke_rebuild_projections.py`, `deprecate_legacy_state_files.py`

### Operations
`live_smoke_test.py`, `healthcheck.py`, `deep_heartbeat.py`, `heartbeat_dispatcher.py`, `force_lifecycle.py`, `cleanup_ghost_positions.py`, `apply_recommended_controls.py`, `analyze_paper_trading.py`, `venus_autonomy_gate.py`, `venus_sensing_report.py`, `equity_curve.py`, `generate_monthly_bounds.py`, `parse_change_log.py`, `rebuild_strategy_tracker_current_regime.py`, `rebuild_settlements.py`, `refresh_paper_runtime_artifacts.py`

### System
`_verify_isolation.py`, `_yaml_bootstrap.py`, `antibody_scan.py`

---

## 16. ENS Data Path — TIGGE Only

Zeus uses **one** ENS data acquisition path for production:

| Path | Script | Storage | data_version | Purpose |
|------|--------|---------|--------------|---------|
| **TIGGE Bulk** | `51 source data/scripts/tigge_*.py` | `51 source data/raw/tigge_ecmwf_ens/` | varies | Historical archive for calibration + live forecast |

**Note:** `src/data/ecmwf_open_data.py` (139 LOC) exists but is NOT a usable production path. TIGGE is the sole ENS source.

The evaluator also writes `data_version='live_v1'` snapshots to `ensemble_snapshots` during live inference cycles via `_store_ens_snapshot()` — these are fetched from Open-Meteo's API (not ECMWF directly), see `src/data/ensemble_client.py`.

---

## 17. Cross-Module Dependency Graph

```
main.py
  ├── cycle_runner.run_cycle()
  ├── db.init_schema()
  ├── harvester.harvest()
  ├── daily_obs_append (via subprocess)
  └── ecmwf_open_data (via subprocess)

cycle_runner.py
  ├── cycle_runtime.* (ALL heavy logic via deps)
  ├── portfolio.*
  ├── chain_reconciliation.*
  ├── db.*
  ├── evaluator.*
  ├── executor.*
  └── riskguard.*

cycle_runtime.py
  ├── lifecycle_manager.enter_*()
  ├── db.log_*(), record_*()
  ├── fill_tracker
  ├── exit_lifecycle.*
  └── monitor_refresh.*

portfolio.py
  ├── lifecycle_manager.enter_*()
  ├── contracts.*
  ├── db.query_*()
  └── portfolio_loader_policy

evaluator.py
  ├── ensemble_signal.*
  ├── market_fusion.*
  ├── calibration.manager.*
  └── contracts.*

harvester.py
  ├── db.record_settlement_event()
  ├── db.record_trade_decision()  (legacy: decision_log)
  ├── ledger.append_event_and_project()  (canonical: position_events)
  ├── calibration.store.*
  └── portfolio.positions  (FALLBACK — contamination risk C1)

chain_reconciliation.py
  ├── lifecycle_manager.enter_*()
  ├── portfolio.void_position()
  ├── db.record_token_suppression()
  └── ledger.append_event_and_project()
```

---

## 18. Open Questions

1. **DB row count discrepancy:** TIGGE progress doc (2026-04-12) says ensemble_snapshots=65,151 after canary, but current DB audit shows 0 rows. Was there a subsequent wipe?
2. **forecasts table:** Progress doc says 1,188,896 rows rebuilt, but current DB shows table doesn't exist. Same question.
3. **HKO data gap:** Last HKO data at 2026-03-31. 16+ days stale. Is this expected?
4. **CWA station type:** `config.py` City dataclass has `cwa_station` field, but Taipei uses wu_icao (RCSS) in `CITY_STATIONS`. Is CWA planned but not implemented?
5. **ecmwf_open_data.py:** Is this code path dead? If TIGGE is the sole ENS source, should this be removed?
6. **ensemble_snapshot_provenance.py:** Contract file for quarantining certain `data_version` values. Which versions are quarantined?

---

## 19. Complete Mathematical Implementation

This section documents every mathematical operation in the probability chain with exact code references, parameter values, and invariants.

### 19.1 ENS → Member Maxes (`ensemble_signal.py`)

**Input:** 51-member ECMWF IFS 0.25° hourly forecasts, shape `(51, hours)`
**Output:** Per-member daily max temperatures, shape `(51,)`

```
Step 1: Timezone-aware day boundary
  select_hours_for_target_date(target_date, city.timezone, times)
  → indices where forecast timestamp .astimezone(tz).date() == target_date
  → REJECT if < 20 hours found (hard gate)

Step 2: Per-member daily max
  member_maxes = members_hourly[:, tz_hours].max(axis=1)  # shape (51,)
  For low-temperature markets: .min(axis=1)

Step 3: Optional bias correction (GATED by settings.bias_correction_enabled=false)
  correction = model_bias.bias × discount_factor (default 0.7)
  member_maxes -= correction
  INVARIANT: If applied live, ALL calibration_pairs must also use bias correction
  Currently DISABLED (bias_correction_enabled=false)
```

### 19.2 Member Maxes → P_raw (`ensemble_signal.p_raw_vector_from_maxes`)

**The ONLY permitted MC path. Naive member counting is FORBIDDEN.**

```
Input: member_maxes (51,), bins, city, settlement_semantics
Output: P_raw vector (n_bins,), sums to 1.0
Parameters: n_mc=5000 (live), 10000 (default)

For each of n_mc iterations:
  1. noised = member_maxes + N(0, σ_instrument²)
     σ_instrument:
       - ASOS/AWOS stations (44 cities): 0.5°F / 0.28°C
       - HKO (Hong Kong): 0.10°C (override, tighter sensor)
       - CWA Taipei: 0.10°C (override, tighter sensor)
       - Istanbul, Moscow: ASOS default (international ICAO standard)
  
  2. measured = settlement_semantics.round_values(noised)
     Rounding: WMO half-up → floor(x × (1/precision) + 0.5) / (1/precision)
     precision=1.0 for all current markets (integer degrees)
  
  3. p += bin_counts_from_array(measured, bins)
     Count how many of 51 measured values fall in each bin

p = p / (51 × n_mc)
p = p / sum(p)  # normalize to 1.0
```

### 19.3 P_raw → P_cal (Extended Platt Calibration)

**Formula:** `P_cal = sigmoid(A × logit(P_raw) + B × lead_days + C)`

```
logit(p) = log(clip(p, 0.01, 0.99) / (1 - clip(p, 0.01, 0.99)))

Width-normalized density mode (current):
  p_input = p_raw / bin_width  for finite bins
  p_input = p_raw              for shoulder bins (open-ended)

Training: sklearn LogisticRegression(C=regularization_C, solver='lbfgs', max_iter=1000)
  Features: X = [logit(p_input), lead_days]
  Target: binary outcome (0/1)
  
  Maturity gates (n_eff = unique decision_group_ids):
    n_eff ≥ 150: Level 1, C=1.0 (standard regularization)
    n_eff ≥ 50:  Level 2, C=0.1 (strong regularization)
    n_eff ≥ 15:  Level 3, C=0.1
    n_eff < 15:  Level 4, use P_raw directly (no Platt)

  Bootstrap: 200 parameter sets (A_i, B_i, C_i)
    Resample by decision_group_id (blocked bootstrap, not i.i.d.)
    Skip degenerate samples (all same class)

Prediction: P_cal = 1/(1+exp(-(A×logit(p_input) + B×lead_days + C)))
  Clamp output to [0.001, 0.999]

Post-calibration: normalize P_cal vector to sum=1.0
  (Platt trained per-bin independently, so calibrated bins don't sum to 1)
```

### 19.4 Bayesian Fusion (market_fusion.py)

**Formula:** `P_posterior = normalize(α_per_bin × P_cal + (1-α_per_bin) × P_market)`

```
Alpha computation:
  base_alpha = {L1: 0.65, L2: 0.55, L3: 0.40, L4: 0.25}
  
  Adjustments (additive):
    ENS spread < SPREAD_TIGHT (2.0°F):  α += 0.10
    ENS spread > SPREAD_WIDE (5.0°F):   α -= 0.15
    Model agreement SOFT_DISAGREE:       α -= 0.10
    Model agreement CONFLICT:            α -= 0.20  (but CONFLICT → reject market)
    Lead ≤ 1 day:                        α += 0.05
    Lead ≥ 5 days:                       α -= 0.05
    Hours since open < 12:               α += 0.10
    Hours since open < 6:                α += 0.05 (cumulative)
  
  Clamp: [0.20, 0.85]
  
  K4 hard gate: REFUSES UNVERIFIED calibration data (raises AuthorityViolation)

Tail scaling:
  α_tail = max(0.20, α × 0.5)  for shoulder bins (open-ended)
  Validated: sweep [0.5-1.0], 0.5 is Brier-optimal (improvement -0.042)

VWMP (Volume-Weighted Micro-Price):
  vwmp = (best_bid × ask_size + best_ask × bid_size) / (bid_size + ask_size)
  HARD GATE: total_size=0 → ValueError (never fall back to mid-price)

Vig treatment (complete market vectors):
  If sum(p_market) ∈ [0.90, 1.10] and ≥2 positive bins:
    p_market = VigTreatment.from_raw(p_market).clean_prices  (remove vig)
  Else: use raw observed prices (sparse monitor vectors)

Final posterior normalized to sum=1.0
```

### 19.5 Model Agreement (model_agreement.py)

**Metric:** Jensen-Shannon Divergence (symmetric). KL divergence is FORBIDDEN.

```
JSD = jensenshannon(ecmwf_p, gfs_p)²   # scipy returns sqrt(JSD)
mode_gap = |argmax(ecmwf_p) - argmax(gfs_p)|

Classification:
  AGREE:          JSD < 0.02 AND mode_gap ≤ 1
  SOFT_DISAGREE:  JSD < 0.08 OR mode_gap ≤ 1  (but not AGREE)
  CONFLICT:       otherwise → SKIP MARKET ENTIRELY

GFS (31 members) is NEVER blended into probability. Conflict detection ONLY.
GFS member maxes use simple counting (no MC), with settlement rounding applied.
```

### 19.6 Double Bootstrap Edge Detection (market_analysis.py)

**Three σ layers in each bootstrap iteration:**

```
For each of n_bootstrap=500 iterations:
  Layer 1: σ_ensemble — resample 51 ENS members with replacement
    sample = rng.choice(members, 51, replace=True)
    noised = sample + N(0, σ_instrument²)
    measured = settle(noised)  # WMO half-up rounding
    p_raw_boot = bin_probability(measured, bins)  # ALL bins (Bug #8 fix)

  Layer 2: σ_parameter — sample Platt params from bootstrap set
    If calibrator has bootstrap_params:
      (A, B, C) = random choice from 200 bootstrap params
      p_cal_boot[j] = sigmoid(A × logit(p_input_j) + B × lead + C)  for each bin j
    Else:
      p_cal_boot = p_raw_boot

  Layer 3: compute posterior and edge
    p_posterior_boot = compute_posterior(p_cal_boot, p_market, α, bins)
    edge_boot[i] = p_posterior_boot[bin_idx] - p_market[bin_idx]

Results:
  p_value = mean(edge_boot ≤ 0)  — EXACT, never approximated
  CI = [percentile(edge_boot, 5), percentile(edge_boot, 95)]  — 90% CI

Entry gate: BOTH edge > 0 AND ci_lower > 0
```

### 19.7 FDR Control (fdr_filter.py + market_analysis_family_scan.py)

```
Full-family hypothesis scan:
  For each bin × {buy_yes, buy_no}: generate FullFamilyHypothesis
  buy_no restricted to binary markets (≤2 bins)
  This gives the TRUE tested family size (denominator for BH)

Benjamini-Hochberg procedure:
  Sort hypotheses by p_value ascending
  Find largest k where p_value[k] ≤ fdr_alpha × k / m
    fdr_alpha = 0.10 (from settings)
    m = total hypotheses (full family, not just positive-edge subset)
  Accept hypotheses 1..k

If full-family scan fails: FAIL CLOSED (no entries)
If scan returns 0 hypotheses: FAIL CLOSED (anomalous)
Legacy fdr_filter() preserved for audit comparison only
```

### 19.8 Kelly Sizing (kelly.py)

```
Base Kelly:
  f* = (p_posterior - entry_price) / (1 - entry_price)
  raw_size = f* × kelly_mult × bankroll
  
  kelly_mult = dynamic_kelly_mult(base=0.25, ...)
    Adjustments (multiplicative, cumulative):
      CI width > 0.10:        × 0.7
      CI width > 0.15:        × 0.5 (cumulative with above → 0.35)
      Lead ≥ 5 days:          × 0.6
      Lead ≥ 3 days:          × 0.8
      Win rate < 0.40:        × 0.5
      Win rate < 0.45:        × 0.7
      Heat > 0.40:            × max(0.1, 1-heat)
      Drawdown:               × max(0.0, 1 - drawdown/max_drawdown)
    INV-05: multiplier MUST NOT reach 0 or NaN → raise ValueError

  safety_cap: clipped to $5 (live_safety_cap_usd)
  
  Execution price correction (EXECUTION_PRICE_SHADOW=true):
    Fee-adjusted entry price replaces raw market price for Kelly input
    Fee rate from Polymarket CLOB API per token

Risk limits (multi-level caps):
  max_single_position: 10% of bankroll
  max_portfolio_heat: 50%
  max_correlated_exposure: 25%
  max_city_exposure: 20%
  min_order: $1

Regime throttling:
  Cluster exposure > 10%:  × 0.5
  Global heat > 25%:       × 0.5 (cumulative)

Strategy policy:
  threshold_multiplier > 1.0: kelly_mult /= multiplier
  allocation_multiplier ≠ 1.0: size × multiplier
```

### 19.9 Day0 Signal (day0_signal.py)

```
Day-0 mode: settlement date is TODAY, partial observation available
  observed_high_so_far: actual max temp recorded today
  hours_remaining: hours until finalization
  member_maxes_remaining: ENS maxes for remaining hours only

P_raw computation:
  If current_temp ≥ threshold (obs dominates):
    P_raw → 1.0 for bins containing current_temp
  Else:
    Blend observation constraint with remaining ENS forecast
    n_mc=5000 simulations

Calibration uses lead_days=0.0 for Day0
```

### 19.10 Exit Decision Logic (portfolio.py:Position.evaluate_exit)

```
Exit evaluation via ExitContext (14 authority fields):
  Missing authority fields → skip exit evaluation (safe default)

Buy YES exit triggers:
  forward_edge = p_posterior_now - entry_price
  Scaling: forward_edge × buy_yes_scaling_factor (0.3)
  Floor: -0.01, Ceiling: -0.10
  Consecutive confirmations: 2 cycles of negative forward_edge

Buy NO exit triggers:
  Same structure, buy_no_scaling_factor (0.5)
  Floor: -0.02, Ceiling: -0.15

Divergence panic (model-market):
  Soft threshold: 0.20 (requires velocity confirmation at -0.05/h)
  Hard threshold: 0.30 (immediate exit)

Near-settlement: force close within 4h of settlement
```

### 19.11 Settlement Semantics (settlement_semantics.py)

```
Per-market resolution rules:
  resolution_source: identifies weather station
  measurement_unit: "F" or "C"
  precision: 1.0 (integer degrees for all current markets)
  rounding_rule: "wmo_half_up"
  finalization_time: "12:00:00Z"

WMO half-up rounding:
  floor(x × (1/precision) + 0.5) / (1/precision)
  Different from Python round() (banker's rounding)
  Different from half-away-from-zero for negative values

assert_settlement_value(): mandatory gate for ALL settlement DB writes
```

---

## 20. TIGGE Source Data Pipeline (`51 source data/`)

The TIGGE pipeline lives **outside** the Zeus repo at `51 source data/`. It is the sole provider of ENS forecast data for calibration and live inference.

### 20.1 Dual Track Architecture

Two ECMWF TIGGE parameters are downloaded and processed independently:

| Track | ECMWF Param | paramId | shortName | stepType | Purpose |
|-------|-------------|---------|-----------|----------|---------|
| **mx2t6_high** | 121.128 | 121 | mx2t6 | max | Daily HIGH temperature |
| **mn2t6_low** | 122.128 | 122 | mn2t6 | min | Daily LOW temperature |

Both represent 6-hour aggregation windows (endStep - startStep = 6). Grid resolution: **0.5° × 0.5°**. 51 ensemble members (0=control, 1-50=perturbed).

### 20.2 Raw Data Flow

```
ECMWF TIGGE Archive
     │
     ├── tigge_mx2t6_download_resumable.py (620 LOC)
     │   → raw/tigge_ecmwf_ens_regions_mx2t6/{region}/{date_range}/*.grib
     │
     └── tigge_mn2t6_download_resumable.py (60 LOC, wraps mx2t6)
         → raw/tigge_ecmwf_ens_regions_mn2t6/{region}/{date_range}/*.grib

Regions: americas, europe_africa, asia, oceania (defined in tigge_regions.py)
3 ECMWF API accounts used in parallel (triple-shard via tmux sessions)
Max target lead: 7 days. Steps: 6, 12, 18, ..., up to 204h (dynamic per timezone)
```

### 20.3 Local Calendar Day Extraction — The Critical Step

**`tigge_local_calendar_day_extract.py`** (474 LOC) + **`tigge_local_calendar_day_common.py`** (238 LOC)

For each city × issue_date × target_local_date × member:

1. Compute local calendar day bounds in UTC (DST-aware via `ZoneInfo`)
2. Find all 6h GRIB windows that overlap the local day
3. Extract nearest-gridpoint value via `codes_grib_find_nearest()`
4. Convert Kelvin → native unit (°F or °C)

**Daily HIGH (mx2t6) — simple:**
```
member_value = max(all overlapping 6h-max values)
```
Boundary windows are safe: if the max occurred outside the target day, inner values dominate.

**Daily LOW (mn2t6) — boundary quarantine:**
```
inner_values = [v for 6h windows fully inside local day]
boundary_values = [v for 6h windows partially overlapping]

if boundary_min ≤ inner_min:
    member.boundary_ambiguous = True
    member.value = None  # EXCLUDED from training
    
training_allowed = all 51 members non-ambiguous AND non-missing
```
Rationale: Tmin can occur at any time. A boundary window's minimum might come from the neighboring day → must quarantine.

**Output:** JSON per city/issue_date/target_date/lead_day with 51 member values + metadata
- data_version: `tigge_mx2t6_local_calendar_day_max_v1` or `tigge_mn2t6_local_calendar_day_min_v1`

### 20.4 GRIB Validation Gates

Before extraction, GRIB files pass hard metadata validation:
- mx2t6: paramId=121, shortName="mx2t6", stepType="max", typeOfStatisticalProcessing=2
- mn2t6: paramId=122, shortName="mn2t6", stepType="min", typeOfStatisticalProcessing=3
- Both: endStep - startStep == 6 (6-hour aggregation window confirmed)

### 20.5 Pipeline Orchestration

| Pipeline | Script | Schedule | Purpose |
|----------|--------|----------|---------|
| Daily | `tigge_daily_pipeline.py` | 03:00 UTC cron | T-3 day settlement-matched backfill → Zeus ETL |
| Full History | `tigge_full_history_pipeline.py` (708 LOC) | hourly cron | Gap-fill day2-day7 coverage |
| Settlement Backfill | `tigge_settlement_backfill.py` (760 LOC) | called by daily | Only downloads dates with settlement values in Zeus DB |

Settlement backfill queries Zeus DB (`zeus-world.db` or `zeus-shared.db`) `settlements` table to find city×date pairs. Enforces 48h delay compliance (skips dates < 3 days old).

### 20.6 Coverage Monitoring

`tigge_local_calendar_day_scan.py` classifies each city×issue_date×lead_day slot:
- `OK`, `MISSING_RAW`, `MISSING_EXTRACT`, `REJECTED_MEMBER_COUNT`
- mn2t6-specific: `N/A_CAUSAL_DAY_ALREADY_STARTED`, `REJECTED_BOUNDARY_AMBIGUOUS`
- Quarantine rate >20% per city → `WARN_HIGH_QUARANTINE_RATE`

### 20.7 Key Constants

| Constant | Value |
|----------|-------|
| Grid resolution | 0.5° × 0.5° |
| Ensemble members | 51 (0=control, 1-50=perturbed) |
| 6h aggregation window | endStep - startStep == 6 |
| Max target lead day | 7 |
| ECMWF queue limit | 20 concurrent requests |
| ECMWF field limit | 600,000 per request |
| Download batch | 3 days per window |
| ECMWF accounts | 3 (triple-shard parallel) |
| Date delay compliance | T-3 days (≥72h old) |
| Boundary quarantine warn | >20% per city |

---

## 21. Additional Pathologies (P8–P16)

Beyond the 6 structural pathologies (§14) and 7 contamination vectors (§15), deep audit reveals 9 more:

### P8. `fill_tracker.py` — Triple Silent Exception Swallow (CRITICAL)

Lines 137, 176, 214: Three `except Exception: pass` blocks silently swallow failures in:
1. `_maybe_update_trade_lifecycle` — DB lifecycle write
2. `_maybe_emit_canonical_entry_fill` — canonical event emission
3. `_maybe_log_execution_fill` — execution telemetry

**Impact:** If DB write fails, in-memory position shows "filled" but DB shows "pending_entry". `chain_reconciliation` may void or duplicate. `position_current` and `position_events` desync from JSON portfolio — the exact split-brain the canonical event system was designed to prevent.

### P9. `monitor_refresh.py` — Hardcoded `model_agreement="AGREE"` (HIGH)

Lines 176, 382: Both refresh paths hardcode `model_agreement="AGREE"` when calling `compute_alpha()`. The entry path in `evaluator.py` performs real GFS crosscheck and may produce `SOFT_DISAGREE` (α-0.10) or `CONFLICT` (α-0.20). During monitoring, the system systematically overweights model trust, inflating α, delaying exits that should fire on CONFLICT.

### P10. `executor.py` — New `PolymarketClient()` per order (MEDIUM)

Lines 281, 365: Creates fresh `PolymarketClient()` on every entry/exit order. Each construction spawns a subprocess to read macOS Keychain (~100ms latency). On hot exit path (backoff retry, day0 settlement rush), this adds latency and subprocess failure risk.

### P11. `harvester.py` — Portfolio Save Before DB Commit (HIGH)

Lines 213-217: `save_portfolio()` BEFORE `conn.commit()`. If crash between portfolio save and DB commit: JSON says positions settled, DB says active. Calibration pairs from this run lost. Commit should happen BEFORE `save_portfolio()`.

### P12. `main.py` — `_cycle_lock` with no timeout/watchdog (MEDIUM)

Lines 30-38: If a cycle hangs (CLOB timeout, infinite retry), ALL subsequent cycles skip silently forever. No watchdog, no max duration, no alert. Heartbeat writer still runs, so daemon appears alive.

### P13. `ensemble_client.py` — Unbounded Module-Level Cache (MEDIUM)

Line 28: `_ENSEMBLE_CACHE: dict = {}` with no size bound or eviction. Over multi-day daemon run: 51 cities × models × past_days → unbounded memory growth. TTL check (15min) prevents stale reads but never evicts.

### P14. `chain_reconciliation.py` — Phantom Position Persistence (CRITICAL)

Lines 339-350: `skip_voiding` guard fires on API outage but ALSO fires when all positions are genuinely redeemed on-chain. Phantom positions persist forever, blocking new entries, consuming risk limits, creating stale monitor cycles.

### P15. `portfolio.py` — Duplicate authority field check (LOW)

Lines 109-110: `missing.append("fresh_prob_is_fresh")` appears twice. Cosmetic but breaks audit dedup.

### P16. `harvester.py` — Settlement value uses `round()` not WMO (MEDIUM)

Line 684: `round(float(settlement_value))` uses Python banker's rounding, not `round_wmo_half_up_value()` which exists in `settlement_semantics.py`. Currently safe (precision=1.0) but will silently corrupt with sub-degree resolution.

---

## 22. Anti-Pattern Inventory

### Silent Exception Swallowing (7 critical instances)

| File | Line | Context | Severity |
|------|------|---------|----------|
| fill_tracker.py | 137 | Trade lifecycle DB write | Critical |
| fill_tracker.py | 176 | Canonical entry-fill write | Critical |
| fill_tracker.py | 214 | Execution telemetry write | High |
| control_plane.py | 130 | Auto-pause DB persist fallback | Medium |
| ensemble_signal.py | 283 | Bias correction config | Low |
| monitor_refresh.py | 163 | Timestamp parse fallback to 48h | Medium |
| monitor_refresh.py | 354 | Day0 timestamp parse | Medium |

### SQL Injection: NONE. All queries use parameterized `?` placeholders.
### Hardcoded Credentials: NONE. All via macOS Keychain.
### Thread Safety: `_ENSEMBLE_CACHE` (P13) is the only significant concern.

---

## 23. Test Coverage Gaps

### Critical Paths with ZERO Tests

| File | LOC | Risk | Test? |
|------|-----|------|-------|
| ledger.py | ~100 | Event sourcing core | NO |
| projection.py | ~100 | UPSERT projection | NO |
| fill_tracker.py | ~350 | Fill verification | NO |
| lifecycle_events.py | ~200 | Canonical event builder | NO |
| lifecycle_manager.py | ~200 | State machine FSM | NO |
| polymarket_client.py | ~250 | CLOB order placement | NO |
| chain_reconciliation.py | 589 | Chain truth reconciliation | NO |
| cycle_runtime.py | 1197 | All runtime helpers | NO |

The **canonical event system** (ledger + projection + lifecycle_events + lifecycle_manager) is the architectural foundation for position truth — and has ZERO test coverage.

---

## 24. Time Bombs

### TB-1. DST Transition in `hours_since_open`
`monitor_refresh.py` L159-163: If `entered_at` stored without timezone (legacy positions), assumes UTC. Wrong assumption shifts α by ±0.15.

### TB-2. Season Flip Mid-Position
Position entered March 19 (DJF) settling March 21 (MAM) uses different calibration buckets for entry vs settlement. `monitor_refresh` recomputes season, may apply DJF Platt model to MAM settlement.

### TB-3. Gamma API Pagination Gap
`harvester.py` `_fetch_settled_events()` uses offset-based pagination. Events inserted/deleted between page fetches → duplicates or gaps. No dedup by event ID.

### TB-4. Year Boundary Lead Days
For UTC+13 cities (Auckland), `lead_days_to_date_start` at year boundary can produce off-by-one. "2027-01-01" target at "2026-12-31 23:00 UTC" → lead≈0, but local date is already Jan 1.

### TB-5. `smoke_test_portfolio_cap_usd` Never Removed
`cycle_runner.py` L230-243: Comment says "Remove after first full lifecycle observed." Permanent entry blocker if cap is reached and nobody remembers it exists.

### TB-6. Harvester Hourly vs Real-Time Settlement
Harvester runs every 1 hour. Position settling at minute 1 won't be processed for up to 59 minutes. During this window, `monitor_refresh` still runs on the settled position, potentially triggering exits on a position that no longer exists on-chain.

### TB-7. APScheduler `max_instances=1` Drops Cycles
`max_instances=1` + `coalesce=True`: if cycle takes longer than interval, next invocation silently dropped. For `day0_capture` at 15-min intervals with 15-min order timeouts, this is a guaranteed drop pattern.
