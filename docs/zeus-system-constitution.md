# Zeus System Constitution
> The authority document for refactoring Zeus. Defines contracts that must be preserved.
> Source: extracted from live codebase on 2026-04-16, verified against source.
> Companion to: `zeus-architecture-deep-map.md` (code topology), `zeus-pathology-registry.md` (known bugs)

---

## 1. System Charter

### 1.1 What Zeus Is

Zeus is a **live-only, position-managed weather-probability trading runtime** on Polymarket. It converts ECMWF ensemble forecasts and Weather Underground settlement observations into calibrated probabilities, selects statistically defensible edges, sizes positions, executes orders, manages exits/settlement, and exposes typed state to Venus/OpenClaw.

Zeus is a **daemon** — a long-running APScheduler process governed by `src/main.py`. It runs 4 discovery modes on staggered intervals (15-30 min cycles), a 60-second heartbeat, and an hourly harvester, all sharing a single `_cycle_lock` to serialize market evaluation while allowing monitoring and harvester to run concurrently.

### 1.2 Optimization Target

**Primary:** Maximize expected geometric growth rate of bankroll (Kelly criterion).

```
f* = (p_posterior - entry_price) / (1 - entry_price)
size = f* × kelly_mult × bankroll
```

- Base `kelly_multiplier` = **0.25** (quarter-Kelly, `settings.json:sizing.kelly_multiplier`). HARDCODED: replace after 500+ settlements using empirical edge-estimation error.
- Dynamic reductions applied multiplicatively via `dynamic_kelly_mult()` (`src/strategy/kelly.py`):

| Condition | Multiplier | Cumulative Effect |
|-----------|-----------|-------------------|
| `ci_width > 0.10` | × 0.7 | Wide CI → less confident |
| `ci_width > 0.15` | × 0.5 | Very wide CI (cumulative with above: 0.25 × 0.7 × 0.5 = 0.0875) |
| `lead_days ≥ 5` | × 0.6 | Distant forecast |
| `lead_days ≥ 3` | × 0.8 | Moderate lead |
| `rolling_win_rate_20 < 0.40` | × 0.5 | Losing streak |
| `rolling_win_rate_20 < 0.45` | × 0.7 | Below-target performance |
| `portfolio_heat > 0.40` | × max(0.1, 1.0 − heat) | High concentration |
| `drawdown_pct > 0` | × max(0.0, 1.0 − drawdown/max_drawdown) | Proportional drawdown |

**INV-05 / §P9.7 cascade floor:** `dynamic_kelly_mult` raises `ValueError` if the result is ≤ 0.0 or NaN, refusing to fabricate a floor. If all sizing gates trigger, the trade is rejected rather than sized at an artificial minimum.

- **Safety cap:** `live_safety_cap_usd` = **$5.00** — Kelly output is hard-clipped at this ceiling (`kelly_size()` parameter `safety_cap_usd`). This is a Phase 1 maturity rail.
- **Fee-adjusted sizing:** When `EXECUTION_PRICE_SHADOW=true` (default), Kelly receives the fee-adjusted entry price via `ExecutionPrice.with_taker_fee()`. Polymarket fee = `fee_rate × p × (1-p)` (convex — highest at p=0.50). This prevents systematic oversizing (INV-12 / D3 resolution, `src/contracts/execution_price.py`).

**Secondary objectives (implicit, not formally ranked):**
- Fill rate (limit orders with dynamic repricing, `limit_offset_pct` = 0.02)
- Drawdown minimization (5 graduated risk levels, portfolio heat cap at 50%)
- Opportunity capture (3 discovery modes, 15-60 minute cycles)

### 1.3 Non-Goals (Enforced in Code)

| Non-Goal | Enforcement Mechanism | Code Location |
|----------|----------------------|---------------|
| Zeus does NOT trade market orders | `order_type: "limit_only"` in settings; `place_limit_order()` is the only order method | `src/execution/executor.py`, `src/data/polymarket_client.py` |
| Zeus does NOT do market-making | One-sided positions only. `MarketAnalysis.find_edges()` evaluates buy_yes OR buy_no per bin, never both simultaneously. No spread-capture logic exists | `src/strategy/market_analysis.py` |
| Zeus does NOT optimize for Sharpe ratio | No Sharpe calculation anywhere in the codebase. Kelly criterion is the sole sizing authority | `src/strategy/kelly.py` |
| Paper/dry_run mode is dead | `get_mode()` raises `ValueError` if `ZEUS_MODE ∉ {"live"}`. `ACTIVE_MODES = ("live",)`. Settings constructor validates mode consistency | `src/config.py:get_mode()` |
| Zeus does NOT use mid-price | `vwmp()` raises `ValueError("Illiquid market: VWMP total size is 0")` when liquidity is zero. Comment: "never use mid-price for edge calculations (VWMP required)" | `src/strategy/market_fusion.py:vwmp()` |
| Zeus does NOT blend GFS into probability | GFS is crosscheck-only. `model_agreement()` returns `AGREE`/`SOFT_DISAGREE`/`CONFLICT` — no blending path exists | `src/signal/model_agreement.py` |
| Zeus does NOT trade low-temperature markets (currently) | `infer_temperature_metric()` returns "low" only on explicit keywords; the signal and bin topology paths support `temperature_metric="low"` but no live strategy targets it | `src/data/market_scanner.py` |

### 1.4 What Counts as Success

- Positive expected log-growth at the portfolio level
- Brier score < 0.25 (`riskguard.brier_yellow` threshold → GREEN risk level)
- Win rate > 40% (`riskguard.win_rate_yellow = 0.40`)
- Directional accuracy ≥ 50% (PROBABILITY_DECISION_THRESHOLD = 0.5 in `src/riskguard/metrics.py`)
- **Gate_50:** At 50 settled trades (`src/riskguard/metrics.py:evaluate_gate_50()`):
  - accuracy ≥ 55% → `"passed"` (global `_gate_50_state` set irrevocably, never re-evaluated)
  - accuracy < 50% → `"failed"` (permanent halt, daemon logs `"Model has no measurable edge. Rebuild required."`)
  - accuracy 50-55% → `"pending"` (re-evaluated at 100 settled trades)

### 1.5 What Counts as a Regression

Any refactor that:
- Changes `p_raw_vector_from_maxes()` output for the same ENS member_maxes, city, bins, settlement_semantics
- Changes `calibrate_and_normalize()` output for the same calibration state
- Changes `compute_posterior()` output for the same p_cal, p_market, alpha, bins
- Changes `kelly_size()` output for the same p_posterior, entry_price, bankroll, kelly_mult
- Changes `dynamic_kelly_mult()` output for the same inputs
- Changes `fdr_filter()` or `apply_familywise_fdr()` accept/reject for same p-values
- Changes entry/exit gate decisions for the same conditions (incl. `evaluate_exit_triggers()`)
- Changes `round_wmo_half_up_values()` output for any input (settlement precision contract)
- Changes `reconcile()` SYNCED/VOID/QUARANTINE classification for same chain state
- Loses position events, settlement records, or calibration pairs from the DB
- Removes the `_gate_50_state` irreversibility property

---

## 2. Strategy Architecture

### 2.1 Strategy Taxonomy

4 strategies, defined in `cycle_runner.py`: `KNOWN_STRATEGIES = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}`

Strategy classification is performed by `_classify_edge_source()` and `_classify_strategy()` in `src/engine/cycle_runner.py` based on discovery mode + edge direction + bin type:

| Strategy | Discovery Mode | Edge Direction | Bin Type | Alpha Source | Signal Class | Kill Condition |
|----------|---------------|----------------|----------|-------------|-------------|----------------|
| `settlement_capture` | `DAY0_CAPTURE` | any | any | `Day0Signal`: `max(observed_high, remaining_ENS_max)` with observation weight fusion, diurnal peak confidence, backbone high, post-peak sigma shrinkage | `src/signal/day0_signal.py` | All observation sources fail → `ObservationUnavailableError` raised |
| `opening_inertia` | `OPENING_HUNT` | any | any | `EnsembleSignal`: 51-member ECMWF IFS → per-member daily max → MC noise simulation → WMO rounding → bin probability → Platt calibration → Bayesian α-blend with market | `src/signal/ensemble_signal.py` | ENS fetch fails (all 3 retries exhausted → returns None → market skipped) |
| `shoulder_sell` | `UPDATE_REACTION` | `buy_no` | shoulder (`is_shoulder=True`) | `EnsembleSignal` + tail α scaling: `α_tail = α × TAIL_ALPHA_SCALE` (0.5). Validated via D3 sweep 2026-03-31 (Brier improvement −0.042) | `src/strategy/market_fusion.py:alpha_for_bin()` | `model_agreement == "CONFLICT"` → α reduced by 0.20, often kills the edge |
| `center_buy` | `UPDATE_REACTION` | `buy_yes` | center (non-shoulder) | `EnsembleSignal`. **Hard block** if `entry_price ≤ 0.02` (`CENTER_BUY_ULTRA_LOW_PRICE_MAX_ENTRY = 0.02` in `evaluator.py`) | `src/engine/evaluator.py` | Ultra-low-price block; also killed by CONFLICT agreement |

### 2.2 Strategy Truth Table: Full Pipeline Per Strategy

| Pipeline Stage | settlement_capture | opening_inertia | shoulder_sell | center_buy |
|---------------|-------------------|-----------------|---------------|------------|
| **Data source** | WU API (priority 1) → IEM ASOS (priority 2, US only) → Open-Meteo hourly (priority 3) + ENS residual hours | Open-Meteo Ensemble API (ECMWF IFS 51-member) | Same as opening_inertia | Same as opening_inertia |
| **P_raw method** | `Day0Signal.p_vector()`: MC sampling with obs floor, backbone high, daylight-weighted blending | `p_raw_vector_from_maxes()`: MC simulation: member_max + N(0, σ_instrument) → WMO round → bin assignment | Same as opening_inertia | Same as opening_inertia |
| **Calibration** | Platt (same per-city/season model) | Platt via `calibrate_and_normalize()` | Same Platt model | Same Platt model |
| **α range** | Same base_alpha by calibration level, same adjustments | Level 1: 0.65, Level 2: 0.55, Level 3: 0.40, Level 4: 0.25. Adjustments: spread ±, agreement ±, lead ±, freshness ± | Same, but tail bins get `α × 0.5` | Same as opening_inertia |
| **GFS crosscheck** | Skipped (Day0 mode — GFS too stale for same-day) | AGREE/SOFT_DISAGREE/CONFLICT via JSD + mode gap | Same | Same |
| **FDR filter** | Full-family BH at α=0.10 | Full-family BH at α=0.10 across all tested bins | Same | Same |
| **Kelly mult** | 0.25 base + dynamic reductions | 0.25 base + dynamic reductions | 0.25 base + dynamic reductions + strategy `threshold_multiplier` | 0.25 base + dynamic reductions + strategy `threshold_multiplier` |
| **Entry timeout** | 15 minutes | 4 hours | 1 hour | 1 hour |
| **Risk budget** | Shared pool (no per-strategy allocation in current code) | Shared pool | Shared pool | Shared pool |
| **Bin eligibility** | Any bin (including shoulders) | Any bin (including shoulders) | Shoulder bins only (`is_shoulder=True`) | Non-shoulder bins only |

### 2.3 Strategy Interaction Rules

- **Same market, multiple strategies:** Possible in theory (e.g., `opening_inertia` enters a position, later `settlement_capture` evaluates the same market on Day 0). In practice, `is_reentry_blocked()` and `has_same_city_range_open()` in `src/state/portfolio.py` prevent duplicate positions on the same bin.
- **Same-cycle exclusion:** All discovery modes share `_cycle_lock` (non-blocking acquire). Only one mode runs at a time. A `DAY0_CAPTURE` cycle cannot overlap with an `OPENING_HUNT` cycle.
- **Position ownership:** Once a position is entered, it is attributed to one strategy (`strategy_key` field on Position). Exit logic does not vary by strategy — `evaluate_exit_triggers()` uses direction-specific paths (buy_yes vs buy_no), not strategy-specific paths.

### 2.4 Strategy Policy Resolution

Override precedence (highest wins), from `src/riskguard/policy.py`:

```python
OVERRIDE_PRECEDENCE = {
    "hard_safety": 3,   # system-level controls (pause_entries, tighten_risk)
    "manual_override": 2,   # human-issued control_overrides DB rows
    "risk_action": 1,   # automated risk_actions DB rows from RiskGuard
}
```

Each strategy resolved independently via `resolve_strategy_policy()`:

| Policy Field | Type | Source | Effect |
|-------------|------|--------|--------|
| `gated` | bool | `is_entries_paused()` / control_overrides / risk_actions | True → no new entries for this strategy |
| `allocation_multiplier` | float | control_overrides / risk_actions | Scales Kelly size (default 1.0) |
| `threshold_multiplier` | float | `get_edge_threshold_multiplier()` / overrides | Scales minimum edge threshold (≥ 1.0, default 1.0) |
| `exit_only` | bool | control_overrides / risk_actions | True → manage existing positions only |

**Locking semantics:** When a higher-priority source locks a field, lower-priority sources are skipped and logged. E.g., if `hard_safety` sets `gated=True`, a `manual_override` trying to set `gated=False` is ignored.

### 2.5 Shared vs Strategy-Specific Components

| Component | Shared? | Note |
|-----------|---------|------|
| ENS fetch + P_raw | Shared | Same for all strategies using same city/date; `_ENSEMBLE_CACHE` with 15-min TTL |
| Platt calibration | Shared | Same model per city/season/bin (bucket key = city × season) |
| Market prices (VWMP) | Shared | Same orderbook; `vwmp()` computes volume-weighted micro-price |
| GFS crosscheck | Shared | Same `model_agreement()` result per candidate |
| FDR family | Shared | Full-family scan across all bins/directions; `apply_familywise_fdr()` at q=0.10 |
| Risk limits | Shared | `RiskLimits` dataclass: same caps for all strategies |
| Kelly multiplier | Strategy-specific | `threshold_multiplier`, `allocation_multiplier` per strategy from policy resolution |
| Discovery mode timing | Strategy-specific | Opening Hunt: 30min, Update Reaction: cron, Day0: 15min |
| Day0Signal | settlement_capture ONLY | `Day0Signal` class with observation floor + residual ENS |
| Center buy price gate | center_buy ONLY | `entry_price ≤ 0.02` → rejected |
| Tail α scaling | shoulder_sell primarily | `alpha_for_bin()`: shoulder bins get `α × 0.5` |

---

## 3. Market & Venue Contract

### 3.1 Venue

Polymarket CLOB on Polygon (chain_id=137). Authentication via `py_clob_client` with lazy initialization (`_ensure_client()` on first I/O).

| Endpoint | URL | Purpose | Auth |
|----------|-----|---------|------|
| CLOB | `https://clob.polymarket.com` | Order placement, orderbook, order status, fee rate | API keys derived from Metamask private key (macOS Keychain) |
| Gamma | `https://gamma-api.polymarket.com` | Market discovery, settlement detection | None (public) |
| Data API | `https://data-api.polymarket.com` | Secondary data | None |

**Wallet initialization:** `PolymarketClient._ensure_client()` calls `_resolve_credentials()` which reads `openclaw-metamask-private-key` and `openclaw-polymarket-funder-address` from macOS Keychain via subprocess call to `bin/keychain_resolver.py`. The CLOB client creates or derives API credentials on first connection. If credential resolution fails → `RuntimeError` → daemon exits (fail-closed, P7 wallet check).

### 3.2 What Is a Market

A set of temperature bins for a specific **city + target_date**. Discovered via Gamma API using tag search for slugs `["temperature", "weather", "daily-temperature"]` (`TAG_SLUGS` in `market_scanner.py`).

**Temperature metric inference:** `infer_temperature_metric()` scans event title for low-temperature keywords (`"lowest temperature"`, `"low temperature"`, `"daily low"`, etc.). Default is `"high"`. This determines whether `member_maxes_for_target_date()` takes per-member daily maxima or minima.

**Market filtering pipeline:**
1. Fetch active events from Gamma API (paginated, 50 per page)
2. Match to known cities via title parsing (`_match_city()` against `cities_by_name` + `cities_by_alias`)
3. Parse target date from title
4. Extract outcomes (bins) with token IDs and prices
5. Filter by `min_hours_to_resolution` (default 6.0 hours, from `settings.json:discovery.min_hours_to_resolution`)
6. Cache results for 5 minutes (`_ACTIVE_EVENTS_TTL = 300.0`)

### 3.3 What Is a Bin

`Bin(low, high, unit, label)` — the atomic tradable instrument. Defined in `src/types/market.py` as a frozen dataclass.

| Unit | Bin Width | Example | Settlement Values Covered | Validation Rule |
|------|-----------|---------|---------------------------|-----------------|
| °F | 2°F range | "60-65°F" | 6 integer values: 60,61,62,63,64,65 | `width != 2` → `ValueError` raised in `__post_init__` |
| °C | 1°C point | "10°C" | 1 integer value: 10 | `width != 1` → `ValueError` raised in `__post_init__` |
| Shoulder (°F) | Open-ended | "≤39°F" | Unbounded | `is_shoulder=True` → width validation skipped |
| Shoulder (°C) | Open-ended | "11°C or higher" | Unbounded | `is_shoulder=True` → width validation skipped |

**Bin topology validation:** `validate_bin_topology()` in `src/types/market.py` enforces the partition invariant: every market's bin set must cover all integer settlement values exactly once — no gaps, no overlaps. Raises `BinTopologyError` on violation.

**Bin classification properties:**
- `is_open_low` → `low is None or low == -inf` (lower shoulder)
- `is_open_high` → `high is None or high == +inf` (upper shoulder)
- `is_shoulder` → `is_open_low or is_open_high`
- `width` → `high - low + 1` for °F (integer range); `1` for °C point bins

**Unit contract:** `Bin.unit` must be `"F"` or `"C"`. Cross-validated against label: `"°F"` in label but `unit="C"` → raises `ValueError`. Bin cannot have both low and high unset. NaN values → `ValueError`.

### 3.4 What Is a Token

Each bin has a YES `token_id` and a NO `token_id` on-chain. Orderbook queries use `token_id`. Position carries both `token_id` and `no_token_id`. Direction determines which token is traded:
- `buy_yes` → order placed on `token_id`
- `buy_no` → order placed on `no_token_id`

Strict token routing enforced in `create_execution_intent()`: unknown direction → `ValueError`.

### 3.5 Market Lifecycle States

```
Discovery (Gamma API active event)
  → Candidate (passes city match + date parse + time filters)
    → Evaluated (edge detection via MarketAnalysis)
      → Traded (position opened) OR Rejected (no edge / risk limit / FDR filter)
        → Monitored (position held, hourly re-evaluation)
          → Exited (sell order filled) OR Settlement (harvester detects Gamma settled event)
            → Calibration pair recorded
              → Position removed from portfolio
```

### 3.6 Order Lifecycle

```
Intent → PolymarketClient.place_limit_order() → pending_tracked
  → fill_tracker verifies via CLOB order status
  → entered (filled) or voided (cancelled/expired)
  
Entry timeouts by mode (src/execution/executor.py:MODE_TIMEOUTS):
  Opening Hunt:    4 hours  (14,400s)
  Update Reaction: 1 hour   (3,600s)
  Day0 Capture:   15 minutes (900s)

Exit: Position.evaluate_exit() → exit intent → PolymarketClient.place_limit_order()
  → exit_pending → sell_filled or backoff_exhausted

Settlement: harvester detects via Gamma API settled events
  → settle position → record calibration pair → remove from portfolio
```

**Exit state machine (Position.exit_state values):**
`""` → `"exit_intent"` → `"sell_placed"` → `"sell_pending"` → `"sell_filled"` | `"retry_pending"` → `"backoff_exhausted"`

### 3.7 Order Execution Rules

- **Limit orders ONLY** (never market orders). `settings.json:execution.order_type = "limit_only"`
- **Limit offset:** `limit_offset_pct = 0.02` (2% offset from native-side VWMP)
- **Share quantization:** BUY rounds UP: `math.ceil(shares * 100 - 1e-9) / 100.0`; SELL rounds DOWN (0.01 increments)
- **Dynamic limit:** if within 5% of best ask, jump to ask for guaranteed fill. If gap > 5% → warning logged, order may not fill
- **Whale toxicity detection:** cancel on adjacent bin sweeps. `toxicity_budget = 0.05` in `ExecutionIntent`
- **Fee-adjusted execution price:** when `EXECUTION_PRICE_SHADOW=true` (default). `ExecutionPrice.with_taker_fee()` applies Polymarket's convex fee: `fee_rate × p × (1-p)`
- **Slicing policy:** `size_usd > 100` → `"iceberg"` (micro-orders); otherwise `"single_shot"`
- **Reprice policy:** `day0_capture` → `"dynamic_peg"`; other modes → `"static"`
- **Collateral check (sells):** `check_sell_collateral()` in `src/execution/collateral.py` verifies wallet balance ≥ `(1 - price) × shares`. Fail-closed: if balance unverifiable → don't sell.

### 3.8 Partial Fill Semantics

Not explicitly handled. `fill_tracker.py` verifies full fill or no fill. Partial fills would leave a position in `pending_tracked` state. Max pending cycles without `order_id`: 2 (then voided).

---

## 4. External Source Matrix

### 4.1 Source Registry

| Source | Fetch File | Endpoint | TTL / Cache | Retry Policy | Failure Behavior | Authority Level | Criticality |
|--------|-----------|----------|-------------|-------------|-------------------|----------------|-------------|
| **Open-Meteo Ensemble** | `src/data/ensemble_client.py` | `ensemble-api.open-meteo.com/v1/ensemble` | 15-min in-memory cache (`_ENSEMBLE_CACHE`, keyed by city/model/past_days) | 3 retries × 10s backoff. HTTP 429 → `note_rate_limited()` with Retry-After header | Returns `None` → market skipped this cycle | PRIMARY for probability generation | **HIGH**: no ENS = no P_raw = no edge detection |
| **Open-Meteo Archive** | `src/data/openmeteo_client.py` | `archive-api.open-meteo.com` | Shared 10k calls/day quota (`openmeteo_quota.py`) | 3 retries. HTTP 429 → 5-min cooldown via `note_rate_limited()` | Degraded calibration | Calibration training data | **MEDIUM**: historical data for Platt model fitting |
| **Polymarket CLOB** | `src/data/polymarket_client.py` | `clob.polymarket.com` | No cache (real-time orderbook) | No retry on `place_limit_order`. Orderbook GET: standard httpx timeout 15s | `raise` on failure. Startup wallet check fail-closed (daemon exits via P7). Order status returns `FETCH_ERROR` dict on exception | CRITICAL: execution venue | **CRITICAL**: no CLOB = no trading |
| **Polymarket Gamma** | `src/data/market_scanner.py` | `gamma-api.polymarket.com` | 5-min events cache (`_ACTIVE_EVENTS_TTL = 300.0`). Tag→event pagination at limit=50 | 3 retries × 0.5s incremental backoff (`_gamma_get()`) | Stale cache returned if fresh fetch fails; empty list if no cache | Market discovery + settlement detection | **HIGH**: no discovery = no new trades |
| **Weather Underground (real-time)** | `src/data/observation_client.py` | `api.weather.com` timeseries endpoint | **No cache.** WU only keeps ~23h of timeseries data (`hours=23` param) | No retry — single attempt per call | Returns `None` → falls through to IEM ASOS → Open-Meteo | Priority 1 for Day0 signal; settlement truth authority | **CRITICAL**: WU is the settlement source for 44/51 cities. Miss a day = data gone forever (36h data window) |
| **WU Daily (batch)** | `src/data/wu_daily_collector.py` | `api.weather.com` timeseries endpoint | Daily collection at 12:00 UTC. De-duplicated via `INSERT OR IGNORE` | Single attempt per city | Returns `None` for city; `errors` count incremented | Settlement value for zeus-world.db | **CRITICAL**: settlement truth for calibration pairs |
| **IEM ASOS** | `src/data/observation_client.py` | `mesonet.agron.iastate.edu/json` | Priority 2 fallback for US cities only (checks `city.wu_station` and `settlement_unit == "F"`) | Standard httpx timeout | Returns `None` → falls through to Open-Meteo | US airport METAR re-distribution | **LOW**: fallback behind WU |
| **Open-Meteo Hourly** | `src/data/observation_client.py` | Open-Meteo current API | Priority 3 fallback for all cities | Via quota tracker | Returns `None` → `ObservationUnavailableError` raised (all sources exhausted) | Free fallback | **LOW**: last-resort fallback |
| **Chain RPC** | `src/state/chain_reconciliation.py` | Polygon via `py_clob_client` | Every cycle before trading (mandatory in live mode) | Inherited from CLOB client | 3 reconciliation rules (see §7.1). Empty chain → skip voiding (P14 pathology) | **Chain is truth.** Portfolio is cache. | **HIGH**: position truth verification |
| **TIGGE** | External ETL pipeline | ECMWF TIGGE archive | Batch processing (not real-time). T−3 day delay compliance (≥72h old data only) | Processed via ETL scripts in `scripts/` | Missing data → holes in calibration training set | Calibration training | **HIGH**: Platt model quality depends on training data volume |

### 4.2 Data Freshness & Staleness Detection

| Data Type | Freshness Requirement | Staleness Detection | Consequence of Staleness |
|-----------|----------------------|---------------------|--------------------------|
| ENS ensemble | Refreshed every 6h (ECMWF IFS runs at 00/06/12/18 UTC) | Cache TTL of 15 min in `_ENSEMBLE_CACHE` | Stale cache used if fresh fetch fails; edge computed on older forecast |
| WU observation | Real-time (23h window) | `day0_nowcast_context()` computes `freshness_factor` from observation age | Stale observation → σ expansion in Day0Signal (`day0_post_peak_sigma()`) |
| Market prices | Real-time via CLOB orderbook | `last_monitor_market_price_is_fresh` flag on Position | Stale price → `ExitContext.missing_authority_fields()` triggers incomplete verdict |
| Risk state | 60-second RiskGuard tick | `riskguard.staleness_hours = 6` in settings. `TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE = 2 hours` | Stale reference → `DATA_DEGRADED` risk level (YELLOW-equivalent safety) |
| Gamma events | 5-min cache | Monotonic timestamp comparison in `_get_active_events()` | Stale cache served if refresh fails |

### 4.3 Ensemble Validation Rules

`validate_ensemble()` in `src/data/ensemble_client.py`:
- Member count must equal `ensemble.primary_members` (51 for ECMWF IFS). Below threshold → `"REJECTED"` logged, returns `False`
- NaN fraction > 50% → `"REJECTED"` logged, returns `False`
- Response must contain `members_hourly` numpy array of shape `(n_members, hours)`

### 4.4 Security Issue: Hardcoded WU API Key

`wu_daily_collector.py` L24: `WU_API_KEY = "6532d6454b8aa370768e63d6ba5a832e"` — **committed plaintext**.
`observation_client.py` correctly uses env var: `WU_API_KEY = os.environ.get("WU_API_KEY", "")` with a `raise SystemExit` if empty. Inconsistent: the daily collector bypasses the env var pattern.

---

## 5. Time Model

### 5.1 Timestamp Standard

`datetime.now(timezone.utc)` — timezone-aware UTC. Used consistently across the codebase.

**EpistemicContext** (`src/contracts/epistemic_context.py`) is the canonical time authority:
```python
@dataclass(frozen=True)
class EpistemicContext:
    decision_time_utc: datetime   # When the evaluation cycle ran
    data_cutoff_time: datetime    # Latest data eligible for this decision
    data_version: str             # "live_v1"
    is_fallback: bool             # True if using override time
```
- `__post_init__` raises `ValueError` if `decision_time_utc.tzinfo is None` — naive timestamps are structurally forbidden.
- Created at cycle entry via `EpistemicContext.enter_cycle()` which sets both times to `datetime.now(timezone.utc)`.

**ONE PATHOLOGY:** `db.py` L2419 uses `datetime.utcnow()` (naive) in a trade-close path.

### 5.2 Timestamp Lattice

| Timestamp | Semantics | Where Set | Format | Timezone |
|-----------|-----------|-----------|--------|----------|
| `decision_time_utc` | When the evaluation cycle ran | `EpistemicContext.enter_cycle()` | ISO 8601 | UTC (tz-aware, enforced) |
| `data_cutoff_time` | Latest data eligible for this decision | `EpistemicContext.enter_cycle()` | ISO 8601 | UTC (tz-aware, enforced) |
| `entered_at` | When the position was created | Position creation in `_materialize_position()` | ISO 8601 | UTC |
| `day0_entered_at` | When Day0 captured this position | Set only for settlement_capture | ISO 8601 | UTC |
| `last_monitor_at` | Last monitoring update time | `monitor_refresh` | ISO 8601 | UTC |
| `checked_at` | Last risk evaluation time | `risk_state` table in risk_state.db | ISO 8601 | UTC |
| `heartbeat` | Daemon alive signal | `_write_heartbeat()` every 60s | ISO 8601 | UTC |
| `quarantined_at` | When position entered quarantine | `enter_chain_quarantined_runtime_state()` | ISO 8601 | UTC |
| `chain_verified_at` | Last chain reconciliation check | Chain reconciliation cycle | ISO 8601 | UTC |
| `fetched_at` | When external data was fetched | Various data clients | ISO 8601 | UTC |
| `occurred_at` | When a canonical position event occurred | `position_events` table | ISO 8601 | UTC |

### 5.3 Local Time Handling

`ZoneInfo(city.timezone)` for all city-local operations. Each city carries its timezone in the `City` dataclass (`cities.json`). The codebase covers **35 distinct timezones** across **51 cities** spanning all 6 populated continents.

**City-local operations that require timezone conversion:**
- `select_hours_for_target_date()`: Slices ENS hourly forecast array to local-day hours. Validates ≥20 hours found (raises `ValueError` if incomplete).
- `lead_days_to_date_start()`: Computes fractional days until city-local midnight of target date. Always anchored to `ZoneInfo(city_timezone)`, never `date.today()`.
- `lead_hours_to_settlement_close()`: Hours until end of target date (local 24:00). Used for exit timing.
- `_select_local_day_samples()`: Filters WU observation timestamps to local target date.
- `_fetch_wu_observation()`: Converts WU epoch timestamps to local time via `datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(tz)`.

**DST handling:** All conversions use `ZoneInfo` (PEP 615), which handles DST transitions automatically. The `time_context.py` helper `_coerce_datetime()` rejects naive datetimes with `ValueError("reference_time must be tz-aware")`.

### 5.4 Settlement Window Definitions

| Window | Definition | Code Location |
|--------|-----------|---------------|
| Target date start | City-local 00:00 of target_date | `lead_days_to_date_start()` in `time_context.py` |
| Target date end (settlement close) | City-local 24:00 (00:00 of target_date + 1 day) | `lead_hours_to_settlement_close()` in `time_context.py` |
| WU finalization | `finalization_time = "12:00:00Z"` in `SettlementSemantics` | `src/contracts/settlement_semantics.py` |
| Day0 capture window | `max_hours_to_resolution: 6` (market resolves within 6 hours) | `MODE_PARAMS[DAY0_CAPTURE]` in `cycle_runner.py` |
| Opening hunt window | `max_hours_since_open: 24` (market opened < 24h ago) | `MODE_PARAMS[OPENING_HUNT]` |
| Update reaction window | `min_hours_since_open: 24`, `min_hours_to_resolution: 6` | `MODE_PARAMS[UPDATE_REACTION]` |
| Near-settlement exit | `near_settlement_hours: 4.0` — positions near settlement get special hold logic | `settings.json:exit.near_settlement_hours` |
| Settlement imminent exit | `hours_to_settlement < 1.0` → immediate exit signal | `evaluate_exit_triggers()` |

### 5.5 Lookahead Prevention

- `EpistemicContext` explicitly separates `decision_time` from `data_cutoff` — both set to `now()` at cycle entry, preventing future data from entering the computation chain.
- TIGGE uses T−3 day delay compliance (≥72h old data only for calibration training).
- Calibration pairs: label = settlement value (outcome), feature = forecast available at decision time.
- `p_raw_vector_from_maxes()` is shared between live inference and offline calibration rebuilds — training and inference use the same MC + noise + rounding code path. Naive member counting is forbidden because it produces distribution shapes that diverge from the live P_raw space.

---

## 6. Capital & Risk Contract

### 6.1 Bankroll Definition

| Parameter | Value | Source | Note |
|-----------|-------|--------|------|
| `capital_base_usd` | **$150.00** | `settings.json` | Static reference point |
| Live bankroll | `PortfolioState.bankroll` | `working_state_metadata` table → `load_portfolio()` | Dynamic, updated each cycle |
| `live_safety_cap_usd` | **$5.00** | `kelly_size()` parameter | Phase 1 maturity rail — Kelly output hard-clipped |
| `smoke_test_portfolio_cap_usd` | **$5.00** | `settings.json` (optional key) | Blocks new entries when sum of open `cost_basis_usd` ≥ cap. One-time guard; should be removed after first full lifecycle |
| `daily_baseline_total` | `PortfolioState.daily_baseline_total` | Set from bankroll at daily reset | Reference for daily loss % calculation |
| `weekly_baseline_total` | `PortfolioState.weekly_baseline_total` | Set from bankroll at weekly reset | Reference for weekly loss % calculation |

**Entry bankroll computation:** `_entry_bankroll_for_cycle()` resolves the available bankroll for sizing new positions. Deducts cost of pending/active positions. If CLOB wallet balance is unverifiable, `entry_bankroll` is `None` → new entries blocked.

### 6.2 Risk Levels (RiskGuard)

RiskGuard runs as a **separate process** (`src/riskguard/riskguard.py`) with its own 60-second tick. It reads authoritative settlement records from zeus_trades.db, writes to risk_state.db, and emits durable risk actions into the `risk_actions` table.

5 risk levels defined in `src/riskguard/risk_level.py` as an `Enum`:

| Level | Precedence | Entry | Monitoring | Exit | Code Action String |
|-------|-----------|-------|------------|------|--------------------|
| `GREEN` | 0 | Normal | Normal | Normal | `"Normal operation"` |
| `DATA_DEGRADED` | 1 | YELLOW-equivalent (blocked) | Normal | Normal | `"Data degraded, acting with YELLOW-equivalent safety without declaring loss boundary breach"` |
| `YELLOW` | 2 | Blocked | Continue | Normal | `"No new entries, continue monitoring held positions"` |
| `ORANGE` | 3 | Blocked | Continue | Exit at favorable | `"No new entries, exit positions at favorable prices"` |
| `RED` | 4 | Blocked | Cancel all | Exit immediately | `"Cancel all pending orders, exit all positions immediately"` |

**Level composition:** `overall_level(*levels)` takes the max of all individual metric levels. This means a single RED metric overrides all GREEN metrics.

**Entry gate in cycle_runner:** `_risk_allows_new_entries(risk_level)` returns `True` only for `GREEN`. Even `DATA_DEGRADED` blocks new entries. Additionally, the cycle runner checks 8 separate entry-blocking conditions:
1. `chain_sync_unavailable` — chain reconciliation failed
2. `portfolio_quarantined` — any position in quarantine state
3. `force_exit_review_daily_loss_red` — daily loss RED (B5)
4. `risk_level={YELLOW|ORANGE|RED}` — risk system escalation
5. `entry_bankroll_unavailable` — wallet balance unverifiable
6. `entry_bankroll_non_positive` — no capital available
7. `smoke_test_portfolio_cap_reached` — one-time cap hit
8. `near_max_exposure` — portfolio heat ≥ 95% of `max_portfolio_heat_pct`

### 6.3 Risk Metrics Thresholds (from `settings.json:riskguard`)

| Metric | GREEN | YELLOW | ORANGE | RED | Evaluation Function |
|--------|-------|--------|--------|-----|---------------------|
| Brier score | < 0.25 | ≥ 0.25 | ≥ 0.30 | ≥ 0.35 | `evaluate_brier()` in `metrics.py` |
| Directional accuracy | ≥ 0.50 | — | < 0.45 | — | `directional_accuracy()` vs threshold |
| Win rate | ≥ 0.40 | < 0.40 | < 0.35 | — | Per-strategy aggregation in `_strategy_settlement_summary()` |
| Daily loss % | < 8% | — | — | ≥ 8% | `_trailing_loss_snapshot()` with `max_daily_loss_pct = 0.08` |
| Weekly loss % | < 15% | — | — | ≥ 15% | `_trailing_loss_snapshot()` with `max_weekly_loss_pct = 0.15` |
| Max drawdown % | < 20% | — | — | ≥ 20% | Against `max_drawdown_pct = 0.20` |

**Trailing loss calculation:** `_trailing_loss_snapshot()` reads historical `risk_state` rows to find a reference equity point. Reference selection:
- Looks back by `lookback` timedelta to find the most recent valid reference row
- Validates internal consistency: `abs(initial_bankroll + total_pnl - effective_bankroll) ≤ $0.01`
- Reference staleness tolerance: **2 hours** (`TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE`)
- If no valid reference found → `DATA_DEGRADED` (not a false GREEN, not a false RED)

**Status taxonomy for trailing loss:** `{"ok", "stale_reference", "insufficient_history", "inconsistent_history", "no_reference_row"}`. Only `"ok"` and `"stale_reference"` allow loss computation; all others degrade to `DATA_DEGRADED`.

### 6.4 Position Limits (from `settings.json:sizing`)

| Limit | Value | Enforcement | Code |
|-------|-------|-------------|------|
| Max single position | **10%** of bankroll (`max_single_position_pct = 0.10`) | `check_position_allowed()` rejects if `size_usd / bankroll > 0.10` | `src/strategy/risk_limits.py` |
| Max portfolio heat | **50%** (`max_portfolio_heat_pct = 0.50`) | `portfolio_heat_for_bankroll()` sums all position sizes / bankroll. Entry blocked at 95% of limit (`heat >= 0.50 * 0.95`) | `src/state/portfolio.py`, `cycle_runner.py` |
| Max correlated (cluster) | **25%** (`max_correlated_pct = 0.25`) | `cluster_exposure_for_bankroll()` sums positions in same geographic cluster | `src/state/portfolio.py` |
| Max per-city | **20%** (`max_city_pct = 0.20`) | `city_exposure_for_bankroll()` sums all positions for one city | `src/state/portfolio.py` |
| Min order | **$1.00** (`min_order_usd = 1.00`) | `check_position_allowed()` rejects if `size_usd < 1.00` | `src/strategy/risk_limits.py` |
| Micro-position hold floor | **$1.00** | `evaluate_exit_triggers()` Layer 8: positions with `size_usd < 1.0` are never sold, held to settlement | `src/execution/exit_triggers.py` |

**Risk limits enforcement flow:**
1. Kelly sizes the trade → `kelly_size()` returns raw USD amount (clipped at `safety_cap_usd = $5.00`)
2. `check_position_allowed()` validates against all limits
3. Returns `(allowed: bool, reason: str)` — rejected trades get a logged reason

### 6.5 Gate_50 — Terminal Evaluation

Implemented in `src/riskguard/metrics.py:evaluate_gate_50()`:

```python
_gate_50_state: str = "pending"  # Module-level global. "pending" | "passed" | "failed"
```

| Condition | Settled Count | Accuracy | Result | Reversible? |
|-----------|--------------|----------|--------|-------------|
| Too few trades | < 50 | any | `"pending"` | Yes |
| Passed | ≥ 50 | ≥ 55% | `"passed"` | **No** — `_gate_50_state` set permanently |
| Failed | ≥ 50 | < 50% | `"failed"` | **No** — permanent halt, logs `"Model has no measurable edge. Rebuild required."` |
| Ambiguous | ≥ 50 | 50-55% | `"pending"` | Yes — re-evaluates at 100 |

**Irrevocability:** Once `_gate_50_state` is set to `"passed"` or `"failed"`, the function returns immediately on subsequent calls without re-evaluating. This is a process-lifetime latch (resets on daemon restart — not persisted to DB).

### 6.6 Portfolio Heat Calculation

`portfolio_heat_for_bankroll(portfolio, bankroll)` in `src/state/portfolio.py`:
- Sums `cost_basis_usd` (or `size_usd` as fallback) for all non-terminal positions
- Divides by bankroll
- Result ∈ [0.0, ∞) — can exceed 1.0 if positions were entered at higher bankroll
- Used in cycle_runner to block entries when heat ≥ 95% of `max_portfolio_heat_pct` (0.475 effective)

### 6.7 Capital Reservation

- **Pending orders** reserve bankroll via `cost_basis_usd` on Position (set at entry intent time, not fill time)
- **Collateral verification for sells:** `check_sell_collateral()` verifies wallet balance ≥ `(1 - entry_price) × shares`. This is because selling YES tokens on Polymarket requires the seller to post collateral for the potential NO outcome.
- **Fail-closed sell guard:** If `clob.get_balance()` raises any exception → `can_sell = False`, sell is not attempted
- **Bankroll consistency:** RiskGuard performs B053 dual-source consistency check: canonical DB position count vs capital metadata position count. Mismatch → warning logged (`"B053 Consistency Mismatch"`)

### 6.8 Risk State FSM

```
GREEN ──[brier ≥ 0.25 OR win_rate < 0.40]──→ YELLOW
GREEN ──[accuracy < 0.45 OR brier ≥ 0.30]──→ ORANGE
GREEN ──[brier ≥ 0.35 OR daily_loss ≥ 8% OR weekly_loss ≥ 15% OR drawdown ≥ 20%]──→ RED
GREEN ──[trailing loss reference stale/missing]──→ DATA_DEGRADED

DATA_DEGRADED ──[reference recovered + metrics GREEN]──→ GREEN
DATA_DEGRADED ──[trailing loss exceeds threshold]──→ RED (preserved through degradation)

YELLOW ──[all metrics recover]──→ GREEN
ORANGE ──[accuracy recovers ≥ 0.45 AND brier < 0.30]──→ GREEN/YELLOW
RED ──[all loss metrics recover AND brier < 0.35]──→ GREEN/YELLOW/ORANGE

Any level ──[overall_level(*all_metric_levels)]──→ max(all levels)
```

**Transition semantics:** There are no sticky states or hysteresis — each RiskGuard tick independently computes all metric levels and takes their max via `overall_level()`. If metrics recover, the level drops immediately. The only stickiness is in durable `risk_actions` rows which are explicitly expired when the recommended strategy gates change.

### 6.9 Drawdown Tiers and Responses

| Drawdown Range | Kelly Multiplier Effect | Entry Status | Exit Behavior |
|---------------|------------------------|-------------|---------------|
| 0% | No reduction | Normal | Normal |
| 0-10% | `m *= (1 - drawdown/0.20)` = 0.5× at 10% | Normal | Normal |
| 10-20% | Linear reduction → 0.0 at 20% | May be blocked by heat | Exit at favorable |
| ≥ 20% (`max_drawdown_pct`) | Kelly multiplier → 0.0 → `ValueError` raised → trade rejected | RED level → entries blocked | Immediate exit of all positions |

The drawdown response is embedded in two independent systems:
1. **Kelly sizing** (`dynamic_kelly_mult`): proportional reduction `max(0.0, 1.0 - drawdown_pct / max_drawdown)`. At `drawdown_pct = max_drawdown` (20%), multiplier → 0.0 → `ValueError` raised → trade is not sized.
2. **RiskGuard** (`max_drawdown_pct = 0.20`): triggers RED level → entries blocked globally, immediate exit of all positions.

### 6.10 RiskGuard Durable Actions

When RiskGuard computes strategy-level gate recommendations, it writes them to the `risk_actions` table in zeus_trades.db via `_sync_riskguard_strategy_gate_actions()`. These are:
- **Source:** `"riskguard"`
- **Action type:** `"gate"` with value `"true"`
- **Precedence:** 50 (below `hard_safety` at 3 in the override system, but above default)
- **Persistence:** Active until RiskGuard explicitly expires them (no timeout-based expiry)
- **Resolution:** `resolve_strategy_policy()` reads these rows and applies them at precedence level 1 (`risk_action`)

---

## 7. Execution & Reconciliation Contract

### 7.1 Order Lifecycle

Orders follow a strict unidirectional state machine. The executor (`src/execution/executor.py`) is the sole order creation authority; the fill tracker (`src/execution/fill_tracker.py`) is the sole verification authority; the exit lifecycle (`src/execution/exit_lifecycle.py`) owns all exit state transitions.

**Entry Order Lifecycle:**

```
Intent created (create_execution_intent)
  → _live_order(): PolymarketClient.place_limit_order(side="BUY")
    → OrderResult(status="pending", order_id=<CLOB ID>)
      → Position created as state="pending_tracked" (immediately, before fill confirmation)
        → fill_tracker.check_pending_entries() polls CLOB order status
          → CLOB status ∈ {"FILLED", "MATCHED"} → _mark_entry_filled():
              - pos.state → "entered" (via enter_filled_entry_runtime_state)
              - pos.entry_fill_verified = True
              - fill_price, shares, cost_basis_usd updated from CLOB payload
              - Canonical event ENTRY_ORDER_FILLED appended to position_events
              - Trade lifecycle DB row updated
              - Execution telemetry logged
          → CLOB status ∈ {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"} → _mark_entry_voided():
              - void_position() removes from portfolio
              - pos.state → voided (via enter_voided_entry_runtime_state)
              - Trade lifecycle DB row updated
          → No order_id after MAX_PENDING_CYCLES_WITHOUT_ORDER_ID (2) cycles → voided
          → Order timed out (order_timeout_at exceeded) → voided
```

**Exit Order Lifecycle:**

```
ExitContext triggers exit (evaluate_exit_triggers)
  → build_exit_intent(): ExitIntent(trade_id, reason, token_id, shares, current_market_price, best_bid)
  → _validate_exit_intent(): cross-checks trade_id, token_id, shares, price
  → check_sell_collateral(): fail-closed — if balance unverifiable, don't sell
  → execute_exit_order(): PolymarketClient.place_limit_order(side="SELL")
    → pos.exit_state transitions:
        "" → "exit_intent" → "sell_placed" → "sell_pending"
          → "sell_filled" (economically_closed — canonical dual-write)
        OR → "retry_pending" (cooldown DEFAULT_COOLDOWN_SECONDS=300s, up to MAX_EXIT_RETRIES=10)
          → back to "" after cooldown for re-evaluation
        OR → "backoff_exhausted" (stop retrying, hold to settlement)
```

**Exit State Machine (Position.exit_state values):**

| State | Meaning | Transition To |
|-------|---------|---------------|
| `""` | No exit in progress | `exit_intent` |
| `exit_intent` | Exit decision made, not yet submitted | `sell_placed` or `retry_pending` |
| `sell_placed` | Sell order submitted to CLOB | `sell_pending` |
| `sell_pending` | Awaiting fill confirmation | `sell_filled` or `retry_pending` |
| `sell_filled` | Confirmed sell fill → economically closed | Terminal (harvester settles later) |
| `retry_pending` | Failed attempt, cooling down | `""` (re-evaluation) |
| `backoff_exhausted` | MAX_EXIT_RETRIES (10) exhausted | Terminal (hold to settlement) |

**GOLDEN RULE** (from `exit_lifecycle.py` docstring): "confirmed sell fill creates economic close, not settlement. Settlement remains a later harvester-owned transition."

### 7.2 Order Execution Rules

| Rule | Value | Code Location |
|------|-------|---------------|
| Order type | Limit ONLY (never market) | `settings.json:execution.order_type = "limit_only"` |
| Limit offset | 2% from native-side VWMP | `settings.json:execution.limit_offset_pct = 0.02` |
| BUY share quantization | `math.ceil(shares * 100 - 1e-9) / 100.0` (round UP) | `executor.py:execute_intent()` |
| SELL share quantization | `math.floor(shares * 100 + 1e-9) / 100.0` (round DOWN) | `executor.py:execute_exit_order()` |
| Dynamic limit (entry) | If within 5% of best ask → jump to ask | `executor.py:create_execution_intent()` |
| Dynamic limit (exit) | `base_price = current_price - 0.01`; if best_bid < base and slippage ≤ 3% → use best_bid | `executor.py:execute_exit_order()` |
| Limit price clamp (exit) | `max(0.01, min(0.99, limit_price))` | `executor.py:execute_exit_order()` |
| Slicing policy | `size_usd > 100` → `"iceberg"`; otherwise `"single_shot"` | `executor.py:create_execution_intent()` |
| Reprice policy | `day0_capture` → `"dynamic_peg"`; other modes → `"static"` | `executor.py:create_execution_intent()` |
| Whale toxicity budget | `toxicity_budget = 0.05` | `ExecutionIntent` default |
| Idempotency key (exit) | `"{trade_id}:exit:{token_id}"` | `executor.py:create_exit_order_intent()` |

**Mode-based fill timeouts** (`MODE_TIMEOUTS` in `executor.py`):

| Mode | Timeout | Seconds |
|------|---------|---------|
| `opening_hunt` | 4 hours | 14,400 |
| `update_reaction` | 1 hour | 3,600 |
| `day0_capture` | 15 minutes | 900 |

Unknown mode → `ValueError("Unknown execution mode ... Explicit runtime mode required.")` — fail-fast, no default.

### 7.3 Fill Tracker Contract

`src/execution/fill_tracker.py` is the **sole fill-verification authority**. Cycle runtime delegates to it; chain reconciliation is the rescue path only.

**Verification statuses recognized:**
- `FILL_STATUSES = {"FILLED", "MATCHED"}` → entry confirmed
- `CANCEL_STATUSES = {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}` → entry voided

**Grace period:** `MAX_PENDING_CYCLES_WITHOUT_ORDER_ID = 2` — positions without any `order_id` after 2 cycles are voided.

**Post-fill recording flow** (`_mark_entry_filled`):
1. Extract fill price from CLOB payload (`avgPrice` / `avg_price` / `price`)
2. Update position: `entry_price`, `shares`, `cost_basis_usd`, `size_usd` from actual fill
3. Compute `fill_quality = (fill_price - submitted_price) / submitted_price`
4. Set `state = "entered"`, `order_status = "filled"`, `chain_state = "local_only"`
5. Call `_maybe_update_trade_lifecycle()` → DB write
6. Call `_maybe_emit_canonical_entry_fill()` → append ENTRY_ORDER_FILLED to `position_events`
7. Call `_maybe_log_execution_fill()` → execution telemetry
8. If any DB write fails → `pos.state = "quarantine_fill_failed"` (not silent)
9. If tracker provided → `tracker.record_entry(pos)`

### 7.4 Chain Reconciliation Rules

Run **every cycle before trading** (mandatory in live mode). Implemented in `src/state/chain_reconciliation.py`.

**Three rules. No reasoning about WHY. Chain is truth.**

| Local State | Chain State | Action | Code Path |
|-------------|------------|--------|-----------|
| Position exists, token matches | Token found on chain | **SYNCED** — update chain_state, sync size/price if divergent | `reconcile()` main loop |
| Position exists | Token NOT found on chain | **VOID** immediately (don't ask why) | `void_position()` |
| No local position | Token found on chain | **QUARANTINE** — create Position with state via `enter_chain_quarantined_runtime_state()`, 48h forced exit eval | `reconcile()` quarantine path |
| N local positions | 0 chain tokens (API returns empty list) | **SKIP voiding** — API likely returned incomplete data (P14 pathology) | Safety guard in `reconcile()` |

**Chain position snapshot:** `ChainPositionView` (frozen dataclass) is built once per cycle from the chain API. All downstream code reads from this snapshot, never from live API calls mid-cycle. This prevents inconsistent reads during reconciliation.

**Precedence:** Chain > Chronicler > Portfolio. Always. (`chain_reconciliation.py` docstring: "Three sources of truth WILL disagree.")

### 7.5 Collateral Verification

`check_sell_collateral()` in `src/execution/collateral.py`:
- Required collateral for selling YES shares: `(1 - entry_price) × shares`
- Calls `clob.get_balance()` to verify wallet holds enough
- **Fail-closed:** If `get_balance()` raises any exception → `can_sell = False`, reason = `"balance_fetch_failed"`
- Negative required collateral (entry_price > 1.0 edge case) → clamped to 0.0

### 7.6 Harvester (Settlement)

`src/execution/harvester.py` — runs every 1 hour. Owns the settlement lifecycle transition.

**Harvester cycle:**
1. Open separate connections: `trade_conn` (zeus_trades.db) + `shared_conn` (zeus-world.db)
2. Poll Gamma API for settled weather events (`_fetch_settled_events()`)
3. For each settled event:
   a. Match to city via `_match_city()`
   b. Parse target date
   c. Find winning bin label
   d. Retrieve decision-time snapshot contexts for calibration pair generation
   e. Generate calibration pairs (1 outcome=1 for winning bin, outcome=0 for all other bins)
   f. Call `maybe_refit_bucket()` — Platt model updated if sufficient new pairs
   g. Settle held positions: compute P&L, record settlement, dual-write canonical settlement event
4. Store `SettlementRecord`s in `decision_chain`
5. Save portfolio and strategy tracker if positions were settled
6. Commit both connections

**Canonical settlement dual-write:** `_dual_write_canonical_settlement_if_available()`:
- Guards against duplicate settlement via `_current_phase_in_db()` — if `position_current.phase` is already terminal (`settled`, `voided`, `admin_closed`, `quarantined`), the write is skipped
- Backfills canonical entry events if `position_events` has no history for this trade
- Appends SETTLEMENT_RESOLVED event with winning_bin, won, outcome metadata

**P1 pathology note:** `save_portfolio(portfolio)` is called before DB commit — if the process crashes between save and commit, the portfolio JSON reflects a state the DB doesn't know about.

---

## 8. Operational Job Graph

### 8.1 Complete Job Table

All jobs are scheduled via `APScheduler.BlockingScheduler` in `src/main.py`.

| Job | Schedule | Lock | max_instances | Failure Behavior | Criticality |
|-----|----------|------|---------------|------------------|-------------|
| `opening_hunt` | 30-min interval | `_cycle_lock` (non-blocking) | 1 | Logged + error status written via `write_status()` | HIGH: primary new-market discovery |
| `update_reaction` | Cron 07/09/19/21 UTC | `_cycle_lock` (non-blocking) | 1 | Logged + error status written | HIGH: monitors existing market evolution |
| `day0_capture` | 15-min interval | `_cycle_lock` (non-blocking) | 1 | Logged + error status written | HIGH: same-day settlement capture |
| `harvester` | 1-hour interval | **NONE** ⚠️ | **not set** ⚠️ | `try/except` with `logger.error`, no escalation | **CRITICAL**: miss = delayed settlement = stale positions |
| `heartbeat` | 60-second interval | None | 1 | `logger.warning` on failure (non-fatal) | LOW: operator monitoring only |
| `ecmwf_open_data` | Cron 01:30/13:30 UTC | None | not set | `try/except` with `logger.error` | MEDIUM: supplements ENS data |
| `wu_daily` | Cron 12:00 UTC | None | 1 | `try/except` with `logger.error` | **CRITICAL**: WU keeps ~36h of data. Miss = data lost forever |
| `etl_recalibrate` | Cron 06:00 UTC | None | not set | Per-script error capture; partial success possible | HIGH: calibration freshness |
| `automation_analysis` | Cron 09:00 UTC | None | 1 | `try/except`, non-fatal | LOW: diagnostic only |

### 8.2 ETL Recalibration Pipeline

`_etl_recalibrate()` runs sequential subprocess scripts via venv Python:

| Step | Script | Purpose | Timeout |
|------|--------|---------|---------|
| 1 | `etl_diurnal_curves.py` | City-specific diurnal temperature curves | 300s |
| 2 | `etl_temp_persistence.py` | Temperature persistence statistics | 300s |
| 3 | `etl_hourly_observations.py` | Hourly observation rollups | 300s |
| 4 | `etl_tigge_direct_calibration.py` | TIGGE ENS→settlement calibration pairs | 300s |
| 5 | `refit_platt.py` | Platt model refit (D5: Brier 0.31→0.02, −92%) | 300s |
| 8 | `run_replay.py --mode audit` | Replay audit snapshot for performance trend | 600s |

Each step logs `"OK"` or `"FAIL: {stderr}"` independently. Partial success is tolerated — a failed ETL step does not abort subsequent steps.

### 8.3 Startup Sequence

`src/main.py:main()` enforces a strict 6-step boot:

1. **Mode validation**: `ZEUS_MODE` env var must exist and equal `"live"`. Paper mode explicitly rejected with `sys.exit()`. Any other value → `sys.exit()`.
2. **World DB schema init**: `init_schema()` on `get_world_connection()` — creates all world tables (`ensemble_snapshots`, `calibration_pairs`, `observations`, etc.)
3. **Trade DB schema init**: `init_schema()` on `get_trade_connection()` — creates trade tables (`position_events`, `position_current`, `execution_log`, etc.)
4. **Startup data health check** (`_startup_data_health_check(conn)`):
   - Bias correction readiness reminder (warns if `model_bias` entries ready but `bias_correction_enabled=false`)
   - Data coverage gap warning (forecast_skill, model_bias vs configured city count)
   - ETL table freshness check (empty `asos_wu_offsets`, `observation_instants`, `diurnal_curves`, etc. → logged)
   - Assumption manifest validation via `scripts/validate_assumptions.py`
5. **P7 wallet check** (`_startup_wallet_check()`): `PolymarketClient().get_balance()` must succeed. Failure → `sys.exit("FATAL: Cannot start — wallet unreachable.")` — **fail-closed, non-recoverable**.
6. **APScheduler start**: `BlockingScheduler` starts all jobs defined above.

### 8.4 Mutual Exclusion Contract

- All 3 discovery modes (`opening_hunt`, `update_reaction`, `day0_capture`) share `_cycle_lock = threading.Lock()` with **non-blocking acquire** (`acquire(blocking=False)`). If the lock is held, the mode is skipped with a warning: `"<mode> skipped: another cycle is still running"`.
- **Harvester runs independently** — no lock. This means concurrent harvester and discovery cycles are possible (TB-7 finding). This is a known design choice: settlement detection should not be blocked by edge evaluation.
- **Heartbeat** has no lock — it writes a JSON file atomically (tmp + `Path.replace()`).

### 8.5 Job Failure Handling

Discovery mode failures (`_run_mode()`):
```python
try:
    summary = run_cycle(mode)
except Exception as e:
    logger.error(...)
    write_status({"mode": mode, "failed": True, "failure_reason": str(e)})
```
Failure is non-fatal — the scheduler continues. The error is logged and written to `status_summary.json` for operator visibility. No automatic retry; the next scheduled interval retries.

---

## 9. Data Ownership Model

### 9.1 World Data — zeus-world.db (Shared, Immutable Facts)

World data represents **observed physical reality** and **derived statistical models**. It is shared across all modes and is never modified by trading logic.

| Table | Authority | Write Path | Mutability |
|-------|-----------|------------|------------|
| `ensemble_snapshots` | ECMWF IFS 51-member ensemble | `ensemble_client.py` fetch + `evaluator.py` storage | Append-only (keyed by city/date/issue_time) |
| `observations` | WU/IEM/Open-Meteo settlement truth | `wu_daily_collector.py`, `observation_client.py` | Append-only (`INSERT OR IGNORE`) |
| `observation_instants` | Derived from observations | ETL: `etl_observation_instants.py` | Rebuilt periodically |
| `calibration_pairs` | Harvester + rebuild scripts | `calibration/store.py:add_calibration_pair()` | Append-only. Authority field: `VERIFIED`/`UNVERIFIED` |
| `platt_models` | Platt refit pipeline | `calibration/store.py:save_platt_model()` | Overwrite per bucket key (cluster×season) |
| `settlements` | Gamma API via harvester | `db.py:log_settlement_event()` | Append-only |
| `diurnal_curves` | Derived from observations | ETL: `etl_diurnal_curves.py` | Rebuilt periodically |
| `temp_persistence` | Derived from observations | ETL: `etl_temp_persistence.py` | Rebuilt periodically |
| `solar_daily` | Derived from coordinates | ETL: `etl_solar.py` | Rebuilt periodically |
| `model_bias` | ECMWF forecast vs observed | ETL: bias correction scripts | Rebuilt periodically |
| `forecast_skill` | Derived from ensemble accuracy | ETL scripts | Rebuilt periodically |
| `control_overrides` | Human operator | Manual DB insert or control plane | Mutable (active/expired) |

### 9.2 Decision Data — zeus_trades.db (Per-Trade, Append-Only)

Decision data records **what the system decided and why**. The canonical event ledger (K0) is the ultimate truth for position lifecycle.

| Table | Authority | Write Path | Mutability |
|-------|-----------|------------|------------|
| `position_events` | **K0 Ledger** — canonical event log | `lifecycle_events.py` builders → `ledger.py:append_many_and_project()` | **Append-only. Never update. Never delete.** |
| `position_current` | Derived projection of `position_events` | `projection.py` — updated atomically with event append | Overwritten per trade_id (derived, rebuildable from events) |
| `edge_decisions` | Evaluator | `db.py:log_edge_decision()` | Append-only |
| `execution_log` | Executor | `db.py:log_execution_report()` | Append-only |
| `selection_family_facts` | FDR filter | `db.py:log_selection_family_fact()` | Append-only |
| `selection_hypothesis_facts` | FDR filter | `db.py:log_selection_hypothesis_fact()` | Append-only |
| `risk_actions` | RiskGuard | `riskguard.py:_sync_riskguard_strategy_gate_actions()` | Active until explicitly expired |
| `alert_cooldown` | Discord alerting | `discord_alerts.py` | Mutable (cooldown tracking) |

### 9.3 Process State (Mutable, Reconstructible)

Process state is the **current working snapshot** of the system. It is derived from decision data and can be rebuilt.

| Artifact | Storage | Authoritative Source | Rebuild Method |
|----------|---------|---------------------|----------------|
| `portfolio.json` | File (state/) | `position_events` → `position_current` | Replay `position_events` |
| `working_state_metadata` | zeus_trades.db table | Bankroll from initial capital + cumulative P&L | Replay all settlements |
| `risk_state.db` | Separate SQLite | RiskGuard 60s tick output | Recompute from settlement data |
| `daemon-heartbeat.json` | File (state/) | 60s heartbeat write | Restart daemon |
| `status_summary.json` | File (state/) | `write_status()` per cycle | Next cycle regenerates |
| `strategy_tracker.json` | File (state/) | Per-strategy trade counts and win rates | Replay from settlements |

### 9.4 Data Taxonomy Summary

```
World Data (physical reality, shared, append-only)
├── Raw observations (WU, IEM, Open-Meteo)
├── Ensemble forecasts (ECMWF IFS)
├── Derived models (Platt, diurnal, persistence, bias)
└── Calibration pairs (forecast→outcome linkage)

Decision Data (trade logic, per-trade, append-only)
├── Canonical event ledger (position_events — K0)
├── Edge decisions (what was evaluated)
├── Execution reports (what was submitted)
└── Risk actions (RiskGuard policy output)

Process State (working snapshots, mutable, derived)
├── Portfolio (current position set)
├── Bankroll/capital metadata
├── Risk level state
└── Status/health summaries
```

---

## 10. Security Contract

### 10.1 Credential Surfaces

| Credential | Storage | Resolution Method | Failure Mode |
|------------|---------|-------------------|-------------|
| Metamask private key | macOS Keychain | `openclaw-metamask-private-key` via `keychain_resolver.py` subprocess (stdin/stdout protocol) | `RuntimeError("Cannot resolve Polymarket credentials")` → daemon exit |
| Funder address | macOS Keychain | `openclaw-polymarket-funder-address` via same resolver | Same as above — bundled resolution |
| Discord webhook | macOS Keychain | `zeus_discord_webhook` via resolver; `ZEUS_DISCORD_WEBHOOK` env var fallback | Silent skip — alerts disabled, no crash |
| WU API key (observation_client) | Environment variable | `WU_API_KEY = os.environ.get("WU_API_KEY", "")` → `raise SystemExit` if empty | Daemon refuses to start |
| WU API key (wu_daily_collector) | **HARDCODED** ⚠️ | `wu_daily_collector.py` L24: `WU_API_KEY = "6532d6454b8aa370768e63d6ba5a832e"` — committed plaintext | Works but violates security contract |

### 10.2 Security Issues

| Issue | Severity | Location | Impact | Remediation |
|-------|----------|----------|--------|-------------|
| Hardcoded WU API key | **HIGH** | `wu_daily_collector.py` L24 | Key exposed in source control; inconsistent with `observation_client.py` which uses env var | Move to env var or Keychain like other credentials |
| Private key in subprocess args | MEDIUM | `polymarket_client.py:_resolve_credentials()` | Key passed through subprocess stdout; visible to process listing briefly | Already mitigated by local-only execution |
| No credential rotation | LOW | All Keychain credentials | Long-lived secrets | Manual rotation via macOS Keychain |

### 10.3 Environment Variables

| Variable | Required | Purpose | Validation |
|----------|----------|---------|------------|
| `ZEUS_MODE` | **Yes** | Mode enforcement | Must equal `"live"`. Paper mode → `sys.exit()`. Missing → `sys.exit()`. |
| `WU_API_KEY` | Yes (for observation_client) | Weather Underground API | Empty string → `SystemExit` |
| `OPENCLAW_HOME` | No (default `~/.openclaw`) | Root path for keychain resolver | Used in subprocess credential resolution |
| `ZEUS_DISCORD_WEBHOOK` | No | Override Keychain webhook URL | If set, used instead of Keychain lookup |
| `ZEUS_DISABLE_DISCORD_ALERTS` | No | Disable all Discord alerting | `"1"`, `"true"`, `"yes"` → alerts silently skipped |
| `EXECUTION_PRICE_SHADOW` | No (default `true`) | Fee-adjusted Kelly sizing | When true, Kelly receives fee-adjusted entry price |

### 10.4 Network Security

- All CLOB communication over HTTPS (`https://clob.polymarket.com`)
- Chain operations via Polygon RPC (chain_id=137) through `py_clob_client`
- API keys derived from Metamask private key on first connection (not stored separately)
- Gamma API is public (no auth required for market discovery)
- httpx timeout: 15s default for API calls
- No TLS certificate pinning (relies on system trust store)

---

## 11. Refactor Preservation Guarantees

### 11.1 Mathematical Invariants (MUST NOT CHANGE)

These computations must produce **bit-identical** results for the same inputs before and after any refactor:

| # | Function | Module | Inputs | Contract |
|---|----------|--------|--------|----------|
| INV-01 | `p_raw_vector_from_maxes()` | `src/signal/ensemble_signal.py` | member_maxes, bins, σ_instrument, precision | MC simulation → WMO rounding → bin assignment. Same RNG seed → same output. |
| INV-02 | `round_wmo_half_up_values()` | `src/contracts/settlement_semantics.py` | float array, precision | `floor(x + 0.5)` — asymmetric half-up. Must match Polymarket settlement rounding. |
| INV-03 | `calibrate_and_normalize()` | `src/calibration/platt.py` | p_raw, lead_days, A/B/C params | `sigmoid(A × logit(p_raw) + B × lead_days + C)` with P_CLAMP = [0.01, 0.99]. |
| INV-04 | `compute_posterior()` | `src/strategy/market_fusion.py` | p_cal, p_market, alpha, bins | `α_per_bin × p_cal + (1−α) × p_market`, normalized to sum=1.0. Vig treatment applied to complete markets. |
| INV-05 | `kelly_size()` | `src/strategy/kelly.py` | p_posterior, entry_price, bankroll, kelly_mult, safety_cap | `f* = (p - entry) / (1 - entry) × kelly_mult × bankroll`, clipped at `safety_cap_usd`. |
| INV-06 | `dynamic_kelly_mult()` | `src/strategy/kelly.py` | ci_width, lead_days, win_rate, heat, drawdown | Cascade of multiplicative reductions. Result ≤ 0.0 or NaN → `ValueError` (refuses to fabricate floor). |
| INV-07 | `fdr_filter()` / `benjamini_hochberg_mask()` | `src/strategy/fdr_filter.py`, `selection_family.py` | edges with p_values, fdr_alpha=0.10 | BH procedure: sort by p-value ascending, find largest k where `p[k] ≤ α × k/m`. |
| INV-08 | `bin_probability_from_values()` | `src/types/market.py` | measured values, bin bounds | Fraction of values falling within bin range (inclusive). |

### 11.2 Behavioral Invariants (MUST NOT CHANGE)

| # | Behavior | Module | Verification Test |
|---|----------|--------|-------------------|
| BEH-01 | Entry gates: same conditions → same accept/reject for all 8 blocking conditions | `cycle_runner.py` | `test_auto_pause_entries.py`, `test_runtime_guards.py` |
| BEH-02 | Exit triggers: same position state → same exit/hold decision across all 8 layers | `exit_triggers.py` | `test_churn_defense.py`, `test_exit_authority.py` |
| BEH-03 | Risk levels: same metrics → same GREEN/YELLOW/ORANGE/RED classification | `riskguard/metrics.py`, `risk_level.py` | `test_riskguard.py` |
| BEH-04 | Chain reconciliation: same chain state → same SYNCED/VOID/QUARANTINE action | `chain_reconciliation.py` | `test_lifecycle.py` |
| BEH-05 | Settlement: same Gamma events → same settlement records and calibration pairs | `harvester.py` | `test_pnl_flow_and_audit.py` |
| BEH-06 | Gate_50 irrevocability: once `_gate_50_state = "passed"/"failed"`, never re-evaluated | `riskguard/metrics.py` | `test_riskguard.py` |
| BEH-07 | Mode rejection: `ZEUS_MODE != "live"` → `sys.exit()` | `config.py:get_mode()` | `test_config.py` |
| BEH-08 | Wallet fail-closed: `get_balance()` exception → daemon exits at startup | `main.py:_startup_wallet_check()` | `test_wallet_source.py` |
| BEH-09 | Limit-only enforcement: no code path exists that sends a market order | `executor.py`, `polymarket_client.py` | `test_executor.py`, `test_live_execution.py` |
| BEH-10 | `authority_verified=False` → `AuthorityViolation` raised, edge computation blocked | `market_fusion.py:compute_alpha()` | `test_authority_gate.py` |

### 11.3 Structural Invariants (MUST NOT CHANGE)

| # | Invariant | Reason |
|---|-----------|--------|
| STR-01 | `position_events` table is append-only (no UPDATE, no DELETE) | K0 ledger — audit trail integrity |
| STR-02 | `position_current` is a derived projection of `position_events` | Rebuildable from events; never independently authoritative |
| STR-03 | `EpistemicContext.decision_time_utc` must be tz-aware | `__post_init__` raises `ValueError` on naive datetime |
| STR-04 | `Bin.__post_init__` validates unit, width, and label consistency | Structural type safety for all downstream computations |
| STR-05 | `validate_bin_topology()` ensures partition invariant (no gaps, no overlaps) | Market completeness guarantee |
| STR-06 | `_cycle_lock` serializes discovery modes | Prevents concurrent portfolio reads/writes |
| STR-07 | Chain reconciliation runs before trading in every cycle | Portfolio-chain consistency |

### 11.4 What MAY Change

- Internal module boundaries and file structure
- DI mechanism (replace `deps=sys.modules[__name__]` with proper injection)
- Error handling (replace silent `except: pass` swallows with proper handling)
- Logging format and verbosity
- Test coverage (add, never remove existing passing tests)
- Performance (caching, connection pooling, batch queries)
- Code organization within K-zones (module splits, renames)
- SQLite pragmas and connection management
- Discord alert formatting and cooldown periods
- Status summary structure and fields
- ETL script ordering (as long as dependencies are preserved)

---

## 12. K-Zone Architecture

### 12.1 Zone Definitions

| Zone | Scope | Modules | Change Packet | Rule |
|------|-------|---------|---------------|------|
| **K0** | Frozen Kernel: contracts, types, ledger, projection, lifecycle | `src/contracts/`, `src/types/`, `src/state/ledger.py`, `src/state/projection.py`, `src/state/lifecycle_manager.py` | `schema_packet` | Touch LAST, test FIRST. These define the language Zeus speaks. |
| **K1** | Governance: risk guard, control plane | `src/riskguard/`, `src/control/` | `feature_packet` | Policy changes only. No new math. |
| **K2** | Runtime: engine, execution, state, data | `src/engine/`, `src/execution/`, `src/state/`, `src/data/` | `refactor_packet` | Main refactor target. Most structural changes happen here. |
| **K3** | Extension: signal, strategy, calibration | `src/signal/`, `src/strategy/`, `src/calibration/` | `feature_packet` | Math changes require INV-01 through INV-08 preservation proofs. |
| **K4** | Experimental: notebooks, scripts, analysis | `notebooks/`, `scripts/`, `src/analysis/` | `feature_packet` | Disposable. Never imported by K0-K3. |

### 12.2 Forbidden Import Directions

These constraints prevent circular dependencies and ensure the kernel is not contaminated by runtime concerns:

| Importing Module | Cannot Import | Reason |
|-----------------|--------------|--------|
| `src.contracts` (K0) | Any K1/K2/K3/K4 module | Contracts must be self-contained |
| `src.types` (K0) | Any K1/K2/K3/K4 module | Types must be self-contained |
| `src.observability` (K2) | `src.execution.executor`, `src.data.polymarket_client` | Observability must not create execution dependencies |
| `src.control` (K1) | `src.signal`, `src.strategy`, `src.calibration` (K3) | Governance must not depend on extension math |
| `src.riskguard` (K1) | `src.engine.cycle_runner` (K2) | RiskGuard is an independent process; no coupling to runtime orchestration |

### 12.3 K-Zone Change Protocol

**K0 change:** Requires relationship tests proving all K1-K3 consumers still produce identical output. Write tests BEFORE the change. Schema migrations must be backward-compatible (add columns with defaults, never remove).

**K1 change:** Policy changes only. Demonstrate that GREEN/YELLOW/ORANGE/RED classifications and strategy gate decisions are preserved for existing metric inputs.

**K2 change:** Main refactor target. Must preserve all BEH-* behavioral invariants. May restructure modules, rename files, refactor DI patterns. Must not change computation outputs.

**K3 change:** Math changes require before/after comparison on the full calibration pair corpus. Any change to INV-01 through INV-08 functions must include a numerical equivalence proof (same inputs → same outputs to floating-point precision).

---

## 13. Decision Policy & Selection Contract

### 13.1 Market Selection Pipeline

Zeus discovers, evaluates, and filters markets through a multi-stage pipeline. Each stage can reject a candidate; no stage can override a rejection from a prior stage.

```
Gamma API (active events, paginated at 50/page)
  → City match (_match_city against cities_by_name + aliases)
    → Date parse (target_date from event title)
      → Time filter (MODE_PARAMS: hours_since_open, hours_to_resolution)
        → Temperature metric inference (high vs low)
          → 5-minute event cache (_ACTIVE_EVENTS_TTL = 300s)
            → MarketCandidate constructed
              → evaluate_candidate() — evaluator.py
```

### 13.2 Candidate Evaluation (evaluate_candidate)

The evaluator (`src/engine/evaluator.py`) is a **pure function**: `candidate → EdgeDecision`. It has no knowledge of scheduling, portfolio mutations, or execution.

**Evaluation stages (in order):**

| Stage | Check | Rejection Reason | Code |
|-------|-------|------------------|------|
| 1 | Bin topology validation | `BinTopologyError` — gaps or overlaps | `validate_bin_topology()` |
| 2 | Reentry block | Same city+date+bin already held | `is_reentry_blocked()`, `has_same_city_range_open()` |
| 3 | Token cooldown | Recently voided token | `is_token_on_cooldown()` |
| 4 | Strategy enablement | Strategy disabled via control plane | `is_strategy_enabled()` |
| 5 | Strategy policy | Strategy gated by risk/manual override | `resolve_strategy_policy().gated` |
| 6 | ENS fetch | Ensemble unavailable after 3 retries | Returns `None` → market skipped |
| 7 | Ensemble validation | < 51 members or > 50% NaN | `validate_ensemble()` returns `False` |
| 8 | VWMP computation | Total orderbook size ≤ 0 | `ValueError("Illiquid market: VWMP total size is 0")` |
| 9 | Fee rate resolution | Token fee rate unavailable | `FeeRateUnavailableError` |
| 10 | Calibrator lookup | Maturity level 4 + no fallback | Returns `(None, 4)` → use P_raw |
| 11 | Authority gate | Calibration data is `UNVERIFIED` | `AuthorityViolation` raised at `compute_alpha()` |
| 12 | Full-family edge scan | No positive edges with CI_lower > 0 | `MarketAnalysis.find_edges()` returns empty |
| 13 | FDR filter | p-value fails BH threshold at q=0.10 | `apply_familywise_fdr()` rejects |
| 14 | Center buy price gate | `entry_price ≤ 0.02` for center_buy | `CENTER_BUY_ULTRA_LOW_PRICE_MAX_ENTRY = 0.02` |
| 15 | Kelly sizing | Size < $1.00 or size violates risk limits | `check_position_allowed()` returns `(False, reason)` |
| 16 | Portfolio heat | Heat ≥ 95% of `max_portfolio_heat_pct` | Blocked at entry bankroll computation |

### 13.3 Full-Family Hypothesis Scan

Edge detection uses a **full-family scan** (`src/strategy/market_analysis_family_scan.py:scan_full_hypothesis_family()`): for each candidate, ALL bins × ALL directions are tested simultaneously. This ensures the FDR denominator includes every tested hypothesis, not just the ones that passed a pre-filter.

**Family ID construction** (`selection_family.py:make_family_id()`):
```python
family_id = "|".join([cycle_mode, city, target_date, strategy_key, discovery_mode])
```

**FDR application** (`apply_familywise_fdr()`):
- Benjamini-Hochberg applied independently per `family_id`
- Target FDR: `q = 0.10` (from `settings.json:edge.fdr_alpha`)
- p-values computed via `np.mean(bootstrap_edges <= 0)` — exact permutation, not approximation
- Returns `selected_post_fdr` flag and `q_value` per hypothesis

### 13.4 Edge Detection (MarketAnalysis)

`src/strategy/market_analysis.py:MarketAnalysis` computes edges via **double bootstrap CI**:

**Three σ layers:**
1. **σ_ensemble**: Resample 51 ENS members with replacement
2. **σ_instrument**: Add `N(0, σ)` noise (ASOS sensor noise ±0.5°F for °F cities, tighter for institutional stations like HKO)
3. **σ_parameter**: Sample Platt `(A, B, C)` bootstrap params (200 sets, default)

**Edge computation:**
- `edge_yes = p_posterior[i] - p_market[i]` for buy_yes
- `edge_no = (1 - p_posterior[i]) - (1 - p_market[i])` for buy_no (binary markets only, `len(bins) <= 2`)
- CI: `(np.percentile(bootstrap_edges, 5), np.percentile(bootstrap_edges, 95))`
- p-value: `np.mean(bootstrap_edges <= 0)` — exact, never approximated
- **Only edges with CI_lower > 0 are emitted** — this is a structural gate, not a threshold

### 13.5 Strategy Classification

`_classify_edge_source()` + `_classify_strategy()` in `cycle_runner.py` map discovery mode + edge properties to one of 4 strategies:

| Discovery Mode | Edge Direction | Bin Type | → Strategy |
|---------------|----------------|----------|------------|
| `DAY0_CAPTURE` | any | any | `settlement_capture` |
| `OPENING_HUNT` | any | any | `opening_inertia` |
| `UPDATE_REACTION` | `buy_no` | shoulder | `shoulder_sell` |
| `UPDATE_REACTION` | `buy_yes` | non-shoulder | `center_buy` |
| `UPDATE_REACTION` | other combinations | any | `opening_inertia` (fallback) |

### 13.6 Risk Limits Enforcement

After Kelly sizes a trade, `check_position_allowed()` in `src/strategy/risk_limits.py` validates:

| Check | Limit | Enforcement |
|-------|-------|-------------|
| Minimum order | `min_order_usd = $1.00` | `size_usd < min` → rejected |
| Bankroll positive | `bankroll > 0` | Zero/negative → rejected |
| Single position | `max_single_position_pct = 10%` | `size_usd / bankroll > 0.10` → rejected |
| Portfolio heat | `max_portfolio_heat_pct = 50%` | `current_heat + position_pct > 0.50` → rejected |
| City concentration | `max_city_pct = 20%` | `city_exposure + position_pct > 0.20` → rejected |

Returns `(allowed: bool, reason: str)` — rejected trades get a logged reason string.

---

## 14. Data Lineage & Learning Loop

### 14.1 Calibration Pair Generation

When the harvester settles a market, it generates calibration pairs linking **decision-time forecasts** to **observed outcomes**:

```
Settlement detected (Gamma API)
  → _snapshot_contexts_for_market(): retrieve decision-time p_raw vectors from ensemble_snapshots
    → For each learning-ready snapshot context:
      → harvest_settlement(): generate 1 pair per bin:
        - Winning bin: outcome=1, p_raw from snapshot
        - All other bins: outcome=0, p_raw from snapshot
        - lead_days, season, cluster, forecast_available_at recorded
        - decision_group_id computed for effective sample size tracking
        - authority field: 'VERIFIED' or 'UNVERIFIED'
      → add_calibration_pair(): INSERT into calibration_pairs (zeus-world.db)
    → maybe_refit_bucket(): if sufficient new pairs, Platt model is refit
```

### 14.2 Platt Calibration Pipeline

`src/calibration/platt.py:ExtendedPlattCalibrator`:

**Model:** `P_cal = sigmoid(A × logit(P_raw_normalized) + B × lead_days + C)`

- 3-parameter logistic regression (sklearn `LogisticRegression`)
- `P_raw` normalized by bin width for finite bins (`width_normalized_density` input space)
- `lead_days` is an **input feature**, not a bucket dimension — this triples effective samples per bucket
- Bootstrap: 200 parameter sets `(A_i, B_i, C_i)` for σ_parameter in double-bootstrap CI
- Logit clamped at `[P_CLAMP_LOW=0.01, P_CLAMP_HIGH=0.99]`

**Maturity gate** (`src/calibration/manager.py:maturity_level()`):

| Level | Sample Count (n_eff) | Regularization C | Edge Threshold Multiplier | α Base |
|-------|---------------------|-------------------|--------------------------|--------|
| 1 | n ≥ 150 | 1.0 (standard) | 1.0× | 0.65 |
| 2 | 50 ≤ n < 150 | 1.0 (standard) | 1.5× | 0.55 |
| 3 | 15 ≤ n < 50 | 0.1 (strong regularization) | 2.0× | 0.40 |
| 4 | n < 15 | N/A (no Platt — use P_raw) | 3.0× | 0.25 |

**Hierarchical fallback** (`get_calibrator()`):
1. Primary bucket: `cluster × season` (e.g., `"Chicago_DJF"`)
2. Fallback: any other cluster for same season
3. Level 4: no calibrator — use P_raw directly

### 14.3 Effective Sample Size

`src/calibration/effective_sample_size.py` — the `CalibrationDecisionGroup` tracks independent calibration samples:

- Each `decision_group_id` represents one forecast event (city × date × forecast_available_at)
- A single event emits N calibration pair rows (one per bin), but counts as 1 effective sample
- Maturity gate uses `n_eff` (distinct decision groups), not raw pair count
- `SHADOW_ONLY = True` — currently advisory; must never enter evaluator/control gate

### 14.4 Calibration Drift Detection

`src/calibration/drift.py` — two detection mechanisms:

**Hosmer-Lemeshow χ² test:**
- Groups: 4 bins (`HL_GROUPS = 4`), df = 3
- Threshold: `χ² > 7.81` → drifted (p < 0.05)
- Requires `n_groups × 2` minimum samples

**Directional failure emergency:**
- Window: last 20 predictions (`DIRECTIONAL_WINDOW = 20`)
- Threshold: ≥ 8 misses → emergency flag (`DIRECTIONAL_FAIL_THRESHOLD = 8`)
- Decision threshold: `DIRECTIONAL_DECISION_THRESHOLD = 0.5`

**Seasonal recalibration trigger dates:** `["03-20", "06-21", "09-22", "12-21"]` — meteorological season boundaries.

### 14.5 Brier Score Tracking

`src/riskguard/metrics.py:brier_score()`:
- `BS = mean((p_forecast - outcome)²)` — lower is better
- Tracked per RiskGuard tick using authoritative settlement records from zeus_trades.db
- Thresholds: GREEN < 0.25, YELLOW ≥ 0.25, ORANGE ≥ 0.30, RED ≥ 0.35
- Used in Gate_50 evaluation and overall risk level computation

### 14.6 Learning Loop Integrity

| Invariant | Enforcement |
|-----------|-------------|
| Training and inference use same P_raw code path | `p_raw_vector_from_maxes()` shared between `EnsembleSignal` (live) and calibration pair rebuild scripts |
| No future data in calibration | `EpistemicContext` separates `decision_time` from `data_cutoff`; TIGGE uses T−3 day delay |
| Authority provenance on pairs | `authority` field: `VERIFIED` (provenance-checked) vs `UNVERIFIED`. K4 hard gate: `get_pairs_for_bucket(authority_filter='VERIFIED')` default |
| Settlement rounding matches P_raw rounding | Both use `round_wmo_half_up_values()` with same precision |
| Platt input space consistency | `input_space` field on model: `"width_normalized_density"` or `"raw_probability"`. Stale models in old space trigger refit. |

---

## 15. External Assumptions Register

Zeus depends on external systems and conditions that are assumed but not verified at runtime. This register documents each assumption so refactors can audit whether they still hold.

### 15.1 Venue Assumptions

| # | Assumption | Evidence | Failure Mode | Mitigation |
|---|-----------|----------|-------------|------------|
| EA-01 | Polymarket CLOB is available 24/7 | Empirical uptime > 99% | Orders rejected, positions strand in `pending_tracked` | Fill tracker voids after 2 cycles; chain reconciliation rescues |
| EA-02 | Polymarket fee formula is `fee_rate × p × (1-p)` | Documentation + `ExecutionPrice.with_taker_fee()` | Systematic over/under-sizing | Fee rate fetched per-token; `FeeRateUnavailableError` raised if missing |
| EA-03 | Polygon chain (chain_id=137) settles all CLOB trades | py_clob_client design assumption | Chain reconciliation would find mismatches | QUARANTINE path for unknown chain positions |
| EA-04 | Gamma API accurately reports settlement outcomes | Settlement truth source for harvester | Wrong P&L, wrong calibration pairs | No independent settlement verification exists |
| EA-05 | Polymarket temperature markets use consistent bin naming conventions | `_parse_temp_range()` regex patterns | City/bin mismatch → market skipped or wrong bin assignment | Title parsing with extensive regex; unknown formats logged and skipped |
| EA-06 | Orderbook depth is sufficient for limit order fills at stated prices | VWMP computation assumes non-zero liquidity | `ValueError("Illiquid market")` raised | Orders sized to $5 max; iceberg slicing for >$100 |

### 15.2 Data Source Assumptions

| # | Assumption | Evidence | Failure Mode | Mitigation |
|---|-----------|----------|-------------|------------|
| EA-07 | ECMWF IFS runs at 00/06/12/18 UTC with 51 members | ECMWF operational schedule | Stale 15-min cache served; edge computed on older forecast | `validate_ensemble()` rejects < 51 members |
| EA-08 | WU keeps ~36 hours of timeseries data | Empirical observation (`hours=23` param) | Miss a day = data lost forever | `wu_daily` cron at 12:00 UTC; `_wu_daily_collection()` labeled CRITICAL |
| EA-09 | WU is the authoritative settlement source for ~44/51 cities | Polymarket resolution criteria reference WU | Wrong settlement values → wrong P&L | `settlement_source_type` per city in `cities.json` |
| EA-10 | Open-Meteo Ensemble API returns ECMWF IFS data (not a different model) | API documentation; `model="ecmwf_ifs04"` parameter | Model confusion → wrong probability distribution | Model name hardcoded in fetch call |
| EA-11 | IEM ASOS redistributes METAR data accurately for US airports | IEM is a research service (Iowa State) | US fallback observation data is wrong | Priority 2 fallback only; WU is priority 1 |
| EA-12 | TIGGE archive data arrives with ≥72h delay | ECMWF access policy | Lookahead contamination if delay is shorter | T−3 day compliance enforced in ETL scripts |

### 15.3 Market Structure Assumptions

| # | Assumption | Evidence | Failure Mode | Mitigation |
|---|-----------|----------|-------------|------------|
| EA-13 | Temperature markets have bins that partition the integer range | `validate_bin_topology()` structural check | `BinTopologyError` raised → market rejected | Active enforcement at evaluation time |
| EA-14 | °F bins are 2°F wide; °C bins are 1°C point bins | `Bin.__post_init__` width validation | `ValueError` raised for non-conforming bins | Shoulder bins exempt from width check |
| EA-15 | Settlement uses WMO half-up rounding (floor(x + 0.5)) | Polymarket resolution criteria | Wrong rounding → wrong win/lose classification | `round_wmo_half_up_values()` used everywhere |
| EA-16 | Markets resolve on the target_date in the city's local timezone | City timezone from `cities.json` | DST transitions could shift resolution window | `ZoneInfo` handles DST automatically |
| EA-17 | Only one settlement value per city per target_date | Unique constraint in observations table | Multiple contradictory values | `INSERT OR IGNORE` deduplication |

### 15.4 Operational Assumptions

| # | Assumption | Evidence | Failure Mode | Mitigation |
|---|-----------|----------|-------------|------------|
| EA-18 | macOS Keychain is accessible at runtime | launchd daemon runs as user | Credential resolution fails → daemon exits | P7 fail-closed startup gate |
| EA-19 | Daemon runs continuously (launchd restarts on crash) | launchd plist configuration | Missed cycles → stale positions | Heartbeat file + operator monitoring |
| EA-20 | Single daemon instance (no concurrent Zeus processes) | launchd `KeepAlive` config | Portfolio corruption from concurrent writes | `_cycle_lock` serializes within process; no cross-process lock |
| EA-21 | System clock is accurate (NTP synced) | macOS default NTP | Timestamps drift → time-based logic fails | All timestamps are UTC; no clock validation in code |

---

## 16. Observability & Incident Contract

### 16.1 Logging Architecture

Zeus uses Python's `logging` module with a flat logger hierarchy rooted at `"zeus"`:

```
zeus (root)
├── zeus.engine.cycle_runner
├── zeus.engine.evaluator
├── zeus.execution.executor
├── zeus.execution.fill_tracker
├── zeus.execution.exit_lifecycle
├── zeus.execution.harvester
├── zeus.riskguard.riskguard
├── zeus.riskguard.discord
├── zeus.data.polymarket_client
├── zeus.data.ensemble_client
├── zeus.data.observation_client
├── zeus.calibration.manager
├── zeus.strategy.market_fusion
└── zeus.observability.status_summary
```

Level: `logging.INFO` (set at startup in `main()`).
Format: `"%(asctime)s [%(name)s] %(levelname)s: %(message)s"`.

### 16.2 Status Summary (Health Snapshot)

`src/observability/status_summary.py:write_status()` writes a 5-section JSON health snapshot to `state/status_summary.json` every cycle:

| Section | Contents | Source |
|---------|----------|--------|
| Generated metadata | Timestamp, mode, risk level | `datetime.now(UTC)`, `get_mode()`, `get_current_level()` |
| Cycle summary | Last cycle results, entries paused, blocking reasons | Passed from cycle runner |
| Risk details | Per-metric levels, recommended strategy gates, trailing loss status | `risk_state.db` latest row |
| Control plane | Entries paused (reason, source), edge threshold multiplier, per-strategy gates | `control_plane.py` queries |
| Learning surface | Decision group counts, settlement summary, no-trade cases | `decision_chain.py` queries |

**Truth annotation:** `annotate_truth_payload()` adds provenance metadata to the status payload.

### 16.3 Discord Alerting

`src/riskguard/discord_alerts.py` sends notifications via Discord webhook:

| Alert Type | Color | Cooldown | Trigger |
|-----------|-------|----------|---------|
| `alert_halt` | Red (0xFF0000) | 30 min | Risk level escalation to YELLOW+ |
| `alert_resume` | Green (0x00FF00) | 30 min | Risk level recovery to GREEN |
| `alert_warning` | Orange (0xFFA500) | 10 min | DATA_DEGRADED or metric degradation |
| `alert_trade` | Blue (0x3498DB) | None | Every order placed (BUY or SELL) |
| `alert_redeem` | Green (0x00FF00) | None | Settlement redemption completed |
| `alert_daily_report` | Blue (0x3498DB) | 22 hours | Daily summary |

**Cooldown tracking:** Stored in `risk_state.db` (`alert_cooldown` table) to survive process restarts.

**Webhook resolution chain:**
1. `ZEUS_DISCORD_WEBHOOK` env var (if set)
2. macOS Keychain: `zeus_discord_webhook` via `keychain_resolver.py`
3. If neither → alerts silently disabled (no crash)

**Kill switch:** `ZEUS_DISABLE_DISCORD_ALERTS=1` → all alerts skipped.

### 16.4 Heartbeat

`_write_heartbeat()` writes to `state/daemon-heartbeat.json` every 60 seconds:

```json
{
  "alive": true,
  "timestamp": "2026-04-16T12:00:00+00:00",
  "mode": "live"
}
```

Atomic write: tmp file + `Path.replace()`. Operators can poll this file to detect silent daemon crashes.

### 16.5 What Gets Logged (Key Events)

| Event | Log Level | Module | Content |
|-------|-----------|--------|---------|
| Cycle start/end | INFO | `cycle_runner` | Mode, summary stats |
| Mode skipped (lock held) | WARNING | `main.py:_run_mode()` | `"<mode> skipped: another cycle is still running"` |
| Order placed | INFO | `executor` | Direction, token, price, shares, timeout |
| Order rejected | INFO | `executor` | Reason (no token_id, CLOB returned None, etc.) |
| Fill confirmed | INFO | `fill_tracker` | Trade ID, fill price, shares |
| Entry voided | INFO | `fill_tracker` | Trade ID, reason |
| Exit triggered | INFO | `exit_lifecycle` | Trade ID, trigger name, reason, urgency |
| Settlement detected | INFO | `harvester` | City, date, winning bin |
| Risk level change | WARNING/ERROR | `riskguard` | Old level → new level, metric details |
| Gate_50 result | INFO/ERROR | `metrics` | Settled count, accuracy, pass/fail |
| Chain reconciliation anomaly | WARNING | `chain_reconciliation` | VOID or QUARANTINE with trade details |
| Wallet check | INFO/CRITICAL | `main` | Balance amount or "FAIL-CLOSED" |
| Data health gap | WARNING | `main` | Missing tables, bias correction readiness |
| ENS rate limited | WARNING | `ensemble_client` | `note_rate_limited()` with Retry-After |

### 16.6 What Does NOT Get Logged (Gaps)

| Gap | Impact | Location |
|-----|--------|----------|
| No structured metrics export (Prometheus, StatsD) | No time-series dashboarding | System-wide |
| No per-trade P&L log at fill time (only at settlement) | Cannot track unrealized P&L | `fill_tracker.py` |
| Harvester has no lock → concurrent runs possible | Potential duplicate settlement processing | `main.py` (TB-7) |
| No alerting on ETL recalibration failures | Silent calibration staleness | `_etl_recalibrate()` |

---

## 17. Deployment & Migration Contract

### 17.1 Deployment Model

Zeus runs as a **macOS launchd daemon** under the user's login session:

| Component | Process | Lifecycle |
|-----------|---------|-----------|
| Zeus daemon | `ZEUS_MODE=live python -m src.main` | launchd `KeepAlive` (auto-restart on crash) |
| RiskGuard | `python -m src.riskguard.riskguard` | Separate launchd service, 60s tick |
| ETL scripts | Subprocess children of daemon | Spawned by `_etl_recalibrate()`, timeout 300-600s |
| WU daily | Subprocess child of daemon | Spawned by `_wu_daily_collection()` |

**Python environment:** `.venv/` at project root. `_etl_subprocess_python()` resolves `{project}/.venv/bin/python`, falling back to `sys.executable`.

**State directory:** `state/` at project root. Contains all SQLite databases, JSON snapshots, and heartbeat files.

### 17.2 Database Architecture

| Database | Path | Purpose | Size Class |
|----------|------|---------|------------|
| `zeus_trades.db` | `state/zeus_trades.db` | Trade truth: positions, events, execution | Medium (grows with trades) |
| `zeus-world.db` | `state/zeus-world.db` | World truth: observations, calibration, ensemble | Large (grows with data collection) |
| `zeus_backtest.db` | `state/zeus_backtest.db` | Derived audit output (never runtime authority) | Medium |
| `risk_state.db` | `state/risk_state.db` | RiskGuard state and alert cooldowns | Small |
| `zeus.db` | `state/zeus.db` | **LEGACY** — remove after Phase 4 | Deprecated |

**Connection management:**
- WAL mode: `PRAGMA journal_mode=WAL` (concurrent reads during writes)
- Foreign keys enabled: `PRAGMA foreign_keys=ON`
- Timeout: 120 seconds (`sqlite3.connect(..., timeout=120)`)
- Row factory: `sqlite3.Row` (dict-like access)
- Cross-DB joins: `get_trade_connection_with_world()` ATTACHes `zeus-world.db` as schema `"world"`

### 17.3 Schema Migration Approach

**Current approach: additive-only.**
- New columns added with `DEFAULT` values
- `_has_authority_column()` pattern: check `PRAGMA table_info()` before using new columns
- Pre-migration DBs continue to work with reduced functionality
- Explicit migration scripts in `scripts/` (e.g., `migrate_add_authority_column.py`)

**Init schema:** `init_schema()` in `src/state/db.py` creates all tables with `CREATE TABLE IF NOT EXISTS`. Safe to run on existing databases — no destructive operations.

**No ORM:** All SQL is hand-written with parameterized queries. Schema is defined in `init_schema()` and `apply_architecture_kernel_schema()`.

### 17.4 Rollback Procedures

| Scenario | Procedure | Risk |
|----------|-----------|------|
| Bad code deploy | `git checkout` to previous commit + restart daemon | None if schema unchanged |
| Schema migration failure | Restore from `state/*.db` backup (no automated backup) | Data loss if no backup exists |
| Calibration corruption | Re-run `scripts/refit_platt.py` (rebuilds from pairs) | Pairs themselves are append-only |
| Portfolio corruption | Rebuild `position_current` from `position_events` (K0 ledger) | Process state reconstructible |
| World data corruption | Re-run ETL pipeline (`_etl_recalibrate()`) | Upstream sources must still be available |

**No automated rollback exists.** Schema changes must be backward-compatible because the rollback path is "use older code with newer schema."

### 17.5 Backup Strategy

| Asset | Backup Method | Frequency | Note |
|-------|-------------|-----------|------|
| SQLite databases | **None automated** ⚠️ | Manual | `state/*.db` should be backed up before any schema change |
| `portfolio.json` | Written every cycle | Per-cycle | Acts as portfolio snapshot backup |
| `settings.json` | Git-tracked | Per-commit | Configuration changes are versioned |
| `cities.json` | Git-tracked | Per-commit | City configuration is versioned |
| Keychain credentials | macOS Keychain backup | OS-level | Not managed by Zeus |

---

## 18. Acceptance Test Matrix

### 18.1 Test Categories

Zeus has **80+ test files** covering mathematical invariants, behavioral contracts, cross-module relationships, and structural linting. Tests are organized by concern, not by module.

### 18.2 Critical Invariant Tests (Must Pass Before Any Refactor)

| Test File | Guards | K-Zone | Invariants Covered |
|-----------|--------|--------|--------------------|
| `test_cross_module_invariants.py` | P_raw → Platt → Posterior → Kelly pipeline integrity | K0/K3 | INV-01 through INV-06 |
| `test_cross_module_relationships.py` | Module boundary contracts, data flow relationships | K0-K3 | STR-01 through STR-07 |
| `test_architecture_contracts.py` | Import direction enforcement, K-zone boundaries | All | Forbidden imports, zone isolation |
| `test_structural_linter.py` | Codebase structural rules (no bare floats, etc.) | All | Code quality gates |
| `test_semantic_linter.py` | Semantic correctness (provenance, naming) | All | Naming conventions, provenance |

### 18.3 Mathematical Correctness Tests

| Test File | Guards | Invariant |
|-----------|--------|-----------|
| `test_market_analysis.py` | Double bootstrap CI, edge detection, bin probability | INV-01, INV-08 |
| `test_platt.py` | Platt calibration fitting, prediction, bootstrap | INV-03 |
| `test_kelly.py` | Kelly sizing formula, safety cap, cascade bounds | INV-05, INV-06 |
| `test_kelly_cascade_bounds.py` | `dynamic_kelly_mult()` cascade produces no floor | INV-06 |
| `test_kelly_live_safety_cap.py` | `live_safety_cap_usd = $5.00` enforcement | INV-05 |
| `test_fdr.py` | BH procedure correctness | INV-07 |
| `test_bootstrap_symmetry.py` | Bootstrap produces symmetric CIs | INV-01 |
| `test_calibration_manager.py` | Maturity levels, hierarchical fallback, bucket routing | INV-03 |
| `test_calibration_quality.py` | Calibration pair provenance and quality | INV-03 |
| `test_calibration_bins_canonical.py` | Canonical grid bin definitions | INV-08 |
| `test_drift.py` | Hosmer-Lemeshow, directional failure detection | Drift detection |
| `test_temperature.py` | °F/°C conversion, `TemperatureDelta` arithmetic | Unit safety |
| `test_forecast_uncertainty.py` | σ_instrument, σ_ensemble noise modeling | INV-01 |

### 18.4 Behavioral Contract Tests

| Test File | Guards | Behavior |
|-----------|--------|----------|
| `test_churn_defense.py` | 8-layer exit trigger evaluation | BEH-02 |
| `test_exit_authority.py` | Exit decision authority boundaries | BEH-02 |
| `test_entry_exit_symmetry.py` | Entry/exit direction symmetry | BEH-01, BEH-02 |
| `test_auto_pause_entries.py` | Entry blocking conditions (all 8) | BEH-01 |
| `test_runtime_guards.py` | Runtime safety checks | BEH-01, BEH-08 |
| `test_riskguard.py` | Risk level computation, Gate_50 | BEH-03, BEH-06 |
| `test_lifecycle.py` | Position lifecycle state machine | BEH-04 |
| `test_executor.py` | Order creation, limit-only enforcement | BEH-09 |
| `test_live_execution.py` | Live execution path | BEH-09 |
| `test_live_safety_invariants.py` | Live mode safety guarantees | BEH-07, BEH-08 |
| `test_authority_gate.py` | UNVERIFIED data rejection | BEH-10 |
| `test_pnl_flow_and_audit.py` | Settlement → P&L → calibration pair flow | BEH-05 |
| `test_wallet_source.py` | Wallet check fail-closed | BEH-08 |
| `test_force_exit_review.py` | Force exit review mechanism | BEH-02 |

### 18.5 Data Integrity Tests

| Test File | Guards |
|-----------|--------|
| `test_cities_config_authoritative.py` | All 51 cities have required fields, valid timezones, valid coordinates |
| `test_config.py` | Settings schema, mode validation, strict key access |
| `test_observation_contract.py` | Observation data format, timestamp handling |
| `test_observation_atom.py` | Atomic observation primitives |
| `test_ensemble_client.py` | ENS fetch, validation (51 members, NaN threshold) |
| `test_ensemble_signal.py` | Signal generation from ensemble data |
| `test_day0_signal.py` | Day0 observation + ENS fusion |
| `test_model_agreement.py` | GFS crosscheck AGREE/SOFT_DISAGREE/CONFLICT |
| `test_correlation.py` | City correlation matrix, haversine fallback |
| `test_assumptions_validation.py` | External assumption manifest |
| `test_reality_contracts.py` | Reality assumptions hold |

### 18.6 Refactor-Specific Tests

| Test File | Guards |
|-----------|--------|
| `test_no_bare_float_seams.py` | No bare floats at module boundaries (TemperatureDelta required) |
| `test_provenance_enforcement.py` | Data provenance fields present and non-empty |
| `test_shadow_boundary.py` | Shadow-only modules don't enter live decision path |
| `test_instrument_invariants.py` | Instrument noise parameters consistent |
| `test_execution_price.py` | Fee-adjusted pricing correctness |
| `test_data_rebuild_relationships.py` | Rebuild pipeline preserves relationships |
| `test_rebuild_pipeline.py` | Full rebuild produces consistent output |
| `test_rebuild_validators.py` | Rebuild validation checks |
| `test_tracker_integrity.py` | Strategy tracker consistency |
| `test_truth_layer.py` | Truth file consistency |
| `test_truth_surface_health.py` | Truth surface health checks |
| `test_supervisor_contracts.py` | Supervisor boundary contracts |

### 18.7 Acceptance Gate

**Before any refactor is considered safe, ALL of the following must pass:**

```bash
cd workspace-venus/zeus
source .venv/bin/activate
python -m pytest tests/ -x --tb=short
```

**Minimum acceptance criteria:**
1. Zero test failures across all 80+ test files
2. `test_cross_module_invariants.py` passes (pipeline integrity)
3. `test_cross_module_relationships.py` passes (boundary contracts)
4. `test_architecture_contracts.py` passes (K-zone isolation)
5. `test_live_safety_invariants.py` passes (live mode safety)
6. No new `WARNING` or `ERROR` log messages in a single test cycle

**Test execution note:** Tests use in-memory SQLite and mocked external APIs. No network access required. No Polymarket credentials needed. Tests should complete in < 60 seconds.
