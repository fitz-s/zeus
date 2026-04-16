# Zeus System Constitution
> The authority document for refactoring Zeus. Defines contracts that must be preserved.
> Source: extracted from live codebase on 2026-04-16, verified against source.
> Companion to: `zeus-architecture-deep-map.md` (code topology), `zeus-pathology-registry.md` (known bugs)

---

## 1. System Charter

### 1.1 What Zeus Is

Zeus is a **live-only, position-managed weather-probability trading runtime** on Polymarket. It converts ECMWF ensemble forecasts and Weather Underground settlement observations into calibrated probabilities, selects statistically defensible edges, sizes positions, executes orders, manages exits/settlement, and exposes typed state to Venus/OpenClaw.

### 1.2 Optimization Target

**Primary:** Maximize expected geometric growth rate of bankroll (Kelly criterion).

```
f* = (p_posterior - entry_price) / (1 - entry_price)
size = f* × kelly_mult × bankroll
```

- Base kelly_multiplier = **0.25** (quarter-Kelly, pending 500+ settlements)
- Dynamic reductions for CI width, lead days, win rate, heat, drawdown

**Secondary objectives (implicit, not formally ranked):**
- Fill rate (limit orders with dynamic repricing)
- Drawdown minimization (graduated risk levels, portfolio heat caps)
- Opportunity capture (3 discovery modes, 15-60 minute cycles)

### 1.3 Non-Goals

- Zeus does NOT optimize for Sharpe ratio
- Zeus does NOT trade market orders (limit orders ONLY)
- Zeus does NOT do market-making (one-sided positions only)
- Paper mode was decommissioned — live-only (`ZEUS_MODE=live` enforced at startup)

### 1.4 What Counts as Success

- Positive expected log-growth at the portfolio level
- Brier score < 0.25 (GREEN risk level)
- Win rate > 40%
- **Gate_50:** At 50 settled trades, accuracy ≥ 55% → passed. < 50% → permanent halt.

### 1.5 What Counts as a Regression

Any refactor that:
- Changes P_raw for the same ENS input
- Changes P_cal for the same calibration state
- Changes P_posterior for the same market prices
- Changes Kelly size for the same edge/CI
- Changes entry/exit gates for the same conditions
- Loses position events or settlement records

---

## 2. Strategy Architecture

### 2.1 Strategy Taxonomy

4 strategies, defined in `cycle_runner.py`: `KNOWN_STRATEGIES = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}`

| Strategy | Discovery Mode | Edge Direction | Bin Type | Alpha Source |
|----------|---------------|----------------|----------|-------------|
| `settlement_capture` | DAY0_CAPTURE | any | any | Day0Signal: `max(observed_high, remaining_ENS_max)` |
| `opening_inertia` | OPENING_HUNT | any | any | EnsembleSignal: 51-member ENS → P_raw → Platt → Bayesian blend |
| `shoulder_sell` | UPDATE_REACTION | buy_no | shoulder | EnsembleSignal with shoulder-specific tail α scaling |
| `center_buy` | UPDATE_REACTION | buy_yes | center | EnsembleSignal. Blocked if entry_price ≤ 0.02 |

### 2.2 Strategy Policy Resolution

Override precedence (highest wins):
1. `hard_safety` (level 3) — system-level, cannot be overridden
2. `manual_override` (level 2) — operator intervention
3. `risk_action` (level 1) — automated risk response

Each strategy independently: gated, allocation-multiplied, threshold-multiplied, or set exit-only.

### 2.3 Shared vs Strategy-Specific Components

| Component | Shared? | Note |
|-----------|---------|------|
| ENS fetch + P_raw | Shared | Same for all strategies using same city/date |
| Platt calibration | Shared | Same model per city/season/bin |
| Market prices (VWMP) | Shared | Same orderbook |
| GFS crosscheck | Shared | Same model agreement |
| FDR family | Shared | Full-family scan across all bins |
| Kelly multiplier | Strategy-specific | `threshold_multiplier`, `allocation_multiplier` per strategy |
| Discovery mode timing | Strategy-specific | Different intervals, different cycles |
| Day0Signal | settlement_capture ONLY | Not shared |

---

## 3. Market & Venue Contract

### 3.1 Venue

Polymarket CLOB on Polygon (chain_id=137).

| Endpoint | URL | Purpose |
|----------|-----|---------|
| CLOB | `https://clob.polymarket.com` | Order placement, orderbook |
| Gamma | `https://gamma-api.polymarket.com` | Market discovery, settlement |
| Data API | `https://data-api.polymarket.com` | Secondary |

### 3.2 What Is a Market

A set of temperature bins for a specific **city + target_date**. Discovered via Gamma API tag search for `["temperature", "weather", "daily-temperature"]`.

### 3.3 What Is a Bin

`Bin(low, high, unit, label)` — the atomic tradable instrument.

| Unit | Bin Width | Example | Settlement Values Covered |
|------|-----------|---------|---------------------------|
| °F | 2°F range | "60-65°F" | 6 integer values |
| °C | 1°C point | "10°C" | 1 integer value |
| Shoulder | Open-ended | "≤39°F" | Unbounded |

**Topology invariant:** Every market's bin set must cover all integer settlement values exactly once — no gaps, no overlaps.

### 3.4 What Is a Token

Each bin has a YES token_id and a NO token_id on-chain. Orderbook queries use token_id.

### 3.5 Order Lifecycle

```
Intent → PolymarketClient.place_limit_order() → pending_tracked
  → fill_tracker verifies via CLOB order status
  → entered (filled) or voided (cancelled/expired)
  
Entry timeouts by mode:
  Opening Hunt: 4 hours
  Update Reaction: 1 hour
  Day0 Capture: 15 minutes

Exit: Position.evaluate_exit() → exit intent → PolymarketClient.place_limit_order()
  → exit_pending → sell_filled or backoff_exhausted

Settlement: harvester detects via Gamma API settled events
  → settle position → record calibration pair → remove from portfolio
```

### 3.6 Order Execution Rules

- **Limit orders ONLY** (never market orders)
- Share quantization: BUY rounds UP, SELL rounds DOWN (0.01 increments)
- Dynamic limit: if within 5% of best ask, jump to ask for guaranteed fill
- Whale toxicity detection: cancel on adjacent bin sweeps
- Fee-adjusted execution price when `EXECUTION_PRICE_SHADOW=true`

### 3.7 Partial Fill Semantics

Not explicitly handled. `fill_tracker.py` verifies full fill or no fill. Partial fills would leave a position in `pending_tracked` until timeout.

---

## 4. External Source Matrix

| Source | Fetch File | Endpoint | TTL | Failure Behavior | Criticality |
|--------|-----------|----------|-----|-------------------|-------------|
| Open-Meteo Ensemble | `ensemble_client.py` | `ensemble-api.open-meteo.com` | 15min cache | Retry 3× / 10s backoff → None | HIGH: no ENS = no P_raw |
| Open-Meteo Archive | `openmeteo_client.py` | `archive-api.open-meteo.com` | Shared 10k/day quota | Retry 3×, 429→5min cooldown | MEDIUM: calibration data |
| Polymarket CLOB | `polymarket_client.py` | `clob.polymarket.com` | No cache | Raise on failure. Startup wallet check fail-closed (daemon exits) | CRITICAL: no CLOB = no trading |
| Polymarket Gamma | `market_scanner.py` | `gamma-api.polymarket.com` | 5min events cache | Retry 3× / 0.5s backoff | HIGH: no discovery |
| Weather Underground | `observation_client.py` | `api.weather.com` | **36h data window** | Returns None on non-200 | CRITICAL: miss a day = data gone forever |
| WU Daily | `wu_daily_collector.py` | `api.weather.com` | Daily at 12:00 UTC | ⚠️ HARDCODED API KEY in source | CRITICAL: settlement truth |
| IEM ASOS | `observation_client.py` | `mesonet.agron.iastate.edu` | Priority 2 fallback | Used behind WU for US cities | LOW: fallback only |
| Chain RPC | `chain_reconciliation.py` | Polygon via py_clob_client | Every cycle | 3 rules: match→SYNCED, local-not-chain→VOID, chain-not-local→QUARANTINE. Empty→skip_voiding | HIGH: position truth |
| TIGGE | External pipeline | ECMWF TIGGE archive | Batch (not real-time) | Processed via ETL scripts | HIGH: calibration training |

### 4.1 Security Issue: Hardcoded WU API Key

`wu_daily_collector.py` L24: `WU_API_KEY = "6532d6454b8aa370768e63d6ba5a832e"` — **committed plaintext**.
`observation_client.py` correctly uses env var. Inconsistent.

---

## 5. Time Model

### 5.1 Timestamp Standard

`datetime.now(timezone.utc)` — timezone-aware UTC. Used consistently across the codebase.

**ONE PATHOLOGY:** `db.py` L2419 uses `datetime.utcnow()` (naive) in a trade-close path.

### 5.2 Timestamp Lattice

| Timestamp | Semantics | Where Set | Format |
|-----------|-----------|-----------|--------|
| `decision_time_utc` | When the evaluation cycle ran | `EpistemicContext` at cycle entry | ISO 8601 with TZ |
| `data_cutoff_time` | Latest data eligible for this decision | `EpistemicContext` | ISO 8601 with TZ |
| `entered_at` | When the position was created | Position creation | ISO 8601 with TZ |
| `last_monitor_prob` | Last monitoring update time | `monitor_refresh` | ISO 8601 with TZ |
| `checked_at` | Last risk evaluation time | `risk_state` | ISO 8601 with TZ |
| `heartbeat` | Daemon alive signal | `_write_heartbeat()` every 60s | ISO 8601 with TZ |

### 5.3 Local Time Handling

`ZoneInfo(city.timezone)` for all city-local operations. Each city carries timezone in config. 35 distinct timezones across 51 cities.

### 5.4 Lookahead Prevention

- `EpistemicContext` explicitly separates decision_time from data_cutoff
- TIGGE uses T-3 day delay compliance (≥72h old data only)
- Calibration pairs: label = settlement value, feature = forecast available at decision time

---

## 6. Capital & Risk Contract

### 6.1 Bankroll Definition

- `capital_base_usd` = **$150** (settings.json)
- Live bankroll = `PortfolioState.bankroll` from working state
- `live_safety_cap_usd` = **$5.00** (Kelly output hard-clipped, Phase 1 maturity rail)
- `smoke_test_portfolio_cap_usd` = **$5.00** (one-time guard, should be removed)

### 6.2 Risk Levels (RiskGuard)

| Level | Entry | Monitoring | Exit |
|-------|-------|------------|------|
| GREEN | Normal | Normal | Normal |
| DATA_DEGRADED | YELLOW-equivalent | Normal | Normal |
| YELLOW | Blocked | Continue | Normal |
| ORANGE | Blocked | Continue | Exit at favorable |
| RED | Blocked | Cancel all | Exit immediately |

### 6.3 Risk Metrics Thresholds

| Metric | Yellow | Orange | Red |
|--------|--------|--------|-----|
| Brier score | — | — | > 0.35 |
| Accuracy | — | < 0.45 | — |
| Win rate | < 0.40 | < 0.35 | — |
| Daily loss | — | — | > 8% |
| Weekly loss | — | — | > 15% |
| Max drawdown | — | — | > 20% |

### 6.4 Position Limits

| Limit | Value |
|-------|-------|
| Max single position | 10% of bankroll |
| Max portfolio heat | 50% |
| Max correlated (cluster) | 25% |
| Max per-city | 20% |
| Min order | $1.00 |

### 6.5 Gate_50 — Terminal Evaluation

At 50 settled trades:
- Accuracy ≥ 55% → **passed** (irreversible)
- Accuracy < 50% → **permanent halt** (irreversible)
- Accuracy 50-55% → re-evaluate at 100

### 6.6 Capital Reservation

- Pending orders reserve bankroll (fill_tracker tracks pending_tracked positions)
- Collateral verification: selling YES requires `(1-price) × shares` collateral
- If balance unverifiable → don't sell (fail-closed)

---

## 7. Execution & Reconciliation Contract

### 7.1 Chain Reconciliation Rules

Run every cycle before trading (mandatory in live mode):

| Local State | Chain State | Action |
|-------------|------------|--------|
| Position exists | Token found | SYNCED |
| Position exists | Token NOT found | VOID |
| No position | Token found | QUARANTINE |
| N positions | 0 tokens (API empty) | **SKIP voiding** (P14 pathology) |

### 7.2 Fill Tracker

- Entries create positions as `pending_tracked` immediately
- Fill tracker verifies via CLOB order status
- Max pending cycles without order_id: 2
- Transitions: `pending_tracked` → `entered` (filled) or `voided` (cancelled/expired)

### 7.3 Harvester (Settlement)

- Runs every 1 hour
- Detects settled markets via Gamma API (`_fetch_settled_events`)
- Settles positions, records calibration pairs
- **P1 pathology:** saves portfolio JSON before DB commit

---

## 8. Operational Job Graph

| Job | Schedule | Lock | max_instances | Notes |
|-----|----------|------|---------------|-------|
| `opening_hunt` | 30min interval | `_cycle_lock` | 1 | Discovery |
| `update_reaction` | Cron 07/09/19/21 UTC | `_cycle_lock` | 1 | Discovery |
| `day0_capture` | 15min interval | `_cycle_lock` | 1 | Day0 |
| `harvester` | 1h interval | **NONE** | **not set** | Settlement ⚠️ |
| `heartbeat` | 60s interval | None | 1 | Health |
| `ecmwf_open_data` | Cron 01:30/13:30 UTC | None | not set | Data ingest |
| `wu_daily` | Cron 12:00 UTC | None | 1 | **CRITICAL: miss = data gone** |
| `etl_recalibrate` | Cron 06:00 UTC | None | not set | 6 subprocess scripts |
| `automation_analysis` | Cron 09:00 UTC | None | 1 | Diagnostic |

### 8.1 Startup Sequence

1. Validate `ZEUS_MODE=live`
2. Init world DB schema
3. Init trade DB schema
4. Startup data health check
5. **P7 wallet check** (fail-closed — exits if unreachable)
6. Start APScheduler

### 8.2 Mutual Exclusion

All discovery modes share `_cycle_lock` (non-blocking). Only one can run at a time. Harvester runs independently — **no lock** (TB-7 finding: concurrent harvester cycles possible).

---

## 9. Data Ownership Model

### 9.1 World Data (shared, immutable facts)

- `ensemble_snapshots` — ENS forecast data
- `settlements` — observed settlement values
- `calibration_pairs` — forecast + outcome pairs for Platt training
- `observations`, `observation_instants` — weather observations
- `diurnal_curves`, `temp_persistence` — derived climate features

### 9.2 Decision Data (per-trade, append-only)

- `position_events` — canonical event log (K0 ledger)
- `edge_decisions` — what the system decided and why
- `execution_log` — order placement records

### 9.3 Process State (mutable, reconstructible)

- `position_current` — projection of position_events (rebuildable)
- `working_state_metadata` — bankroll, risk state (reconstructible)
- `portfolio.json` — in-memory portfolio snapshot (should be derivable from events)

---

## 10. Security Contract

### 10.1 Credential Surfaces

| Credential | Storage | Resolution |
|------------|---------|------------|
| Metamask private key | macOS Keychain | `openclaw-metamask-private-key` via `keychain_resolver.py` subprocess |
| Funder address | macOS Keychain | `openclaw-polymarket-funder-address` |
| Discord webhook | macOS Keychain | `zeus_discord_webhook` (env var fallback) |
| WU API key | **HARDCODED** ⚠️ | `wu_daily_collector.py` L24 — committed plaintext |

### 10.2 Env Vars

- `ZEUS_MODE` — mode enforcement (must be "live")
- `WU_API_KEY` — Weather Underground (used by observation_client.py)
- `OPENCLAW_HOME` — root path
- `ZEUS_DISCORD_WEBHOOK` — optional keychain override
- `ZEUS_DISABLE_DISCORD_ALERTS` — disable alerting

---

## 11. Refactor Preservation Guarantees

### 11.1 Mathematical Invariants (MUST NOT CHANGE)

These computations must produce bit-identical results before and after refactor:

1. `p_raw_vector_from_maxes()` — MC simulation output
2. `settlement_semantics.round_values()` — WMO half-up rounding
3. `calibrate_and_normalize()` — Platt calibration output
4. `compute_posterior()` — Bayesian fusion output
5. `kelly_size()` — Kelly sizing output
6. FDR procedure — BH filter output

### 11.2 Behavioral Invariants (MUST NOT CHANGE)

1. Entry gates: same conditions → same accept/reject
2. Exit triggers: same position state → same exit/hold
3. Risk levels: same metrics → same GREEN/YELLOW/ORANGE/RED
4. Chain reconciliation: same chain state → same SYNCED/VOID/QUARANTINE
5. Settlement: same Gamma events → same settlement records

### 11.3 What MAY Change

- Internal module boundaries and file structure
- DI mechanism (replace `deps=sys.modules[__name__]`)
- Error handling (replace silent swallows with proper handling)
- Logging and observability
- Test coverage (add, never remove)
- Performance (caching, connection pooling)
- Code organization within K-zones

---

## 12. K-Zone Architecture

| Zone | Scope | Change Packet | Rule |
|------|-------|---------------|------|
| K0 | Frozen Kernel: contracts, types, ledger, projection, lifecycle | schema_packet | Touch LAST, test FIRST |
| K1 | Governance: riskguard, control | feature_packet | Policy changes only |
| K2 | Runtime: engine, execution, state, data | refactor_packet | Main refactor target |
| K3 | Extension: signal, strategy, calibration | feature_packet | Math changes |
| K4 | Experimental: notebooks, scripts | feature_packet | Disposable |

### 12.1 Forbidden Import Directions

- `src.observability` → cannot import `src.execution.executor` or `src.data.polymarket_client`
- `src.control` → cannot import `src.signal`, `src.strategy`, `src.calibration`
