# Rainstorm: Agentic Weather Trading System — Architecture Blueprint

*For Polymarket weather prediction markets. Designed for a solo developer with live capital at risk.*

---

## 1. Agent Topology

**Decision: Hub-and-spoke orchestrator, not mesh.**

A mesh topology (where agents communicate peer-to-peer) adds O(n²) communication paths. For a solo developer, this means O(n²) debugging surface area. Weather markets have a natural sequential data flow — observe → analyze → decide → execute — which maps cleanly to an orchestrator pattern. Mesh buys you parallel negotiation between agents, which you don't need when you have one strategy running on one market class.

### 1.1 The Agents

**Conductor** (long-running daemon)
The orchestrator. A lightweight Python process running on cron ticks (every 15 min for active markets, every 6 hours for scanning). It owns the task queue, decides which agents to invoke, and enforces execution order. No LLM calls — pure Python control flow.

```python
# Conductor pseudocode
class Conductor:
    def tick_15min(self):
        active_markets = self.sentinel.get_active_markets()
        for market in active_markets:
            weather_signal = self.meteorologist.get_signal(market)
            current_position = self.bookkeeper.get_position(market)
            
            if self.risk_guard.is_halted():
                log.warn("Trading halted by RiskGuard")
                return
            
            edge = self.analyst.evaluate_edge(
                signal=weather_signal,
                market=market,
                position=current_position
            )
            
            if edge and edge.confidence > MINIMUM_EDGE:
                order = self.analyst.size_order(edge, current_position)
                if self.risk_guard.approve(order):
                    self.trader.execute(order)
                    self.chronicler.log_trade(order, edge, weather_signal)
    
    def tick_weekly(self):
        self.strategist.review_portfolio(
            trades=self.chronicler.get_week_trades(),
            pnl=self.bookkeeper.get_pnl_summary()
        )
```

**Sentinel** (long-running, low-frequency)
Scans Polymarket for active and upcoming weather markets. Extracts market parameters (location, threshold, resolution date). Runs every 6 hours. Pure API calls + regex parsing for structured markets, Gemini Flash for ambiguous market descriptions.

**Meteorologist** (on-demand, per-market)
The signal generator. Called by Conductor when a market needs fresh probability estimates. Pulls from weather data sources, runs ensemble logic, outputs a calibrated probability distribution. This is where most of the alpha lives. Details in Section 2.

**Bookkeeper** (long-running daemon)
Tracks all open positions, realized PnL, unrealized PnL, and settlement status. Pure Python — no LLM needed. Reads from Polymarket API + local SQLite state. This was the source of your previous PnL calculation bugs, so it deserves to be an isolated, well-tested module.

**Harvester** (on-demand, event-triggered)
Monitors market resolution events and claims settlements. Triggered by Conductor when a market's resolution time passes. Pure API calls. The previous settlement harvester bug came from not checking resolution status before claiming — the fix is a state machine: `OPEN → RESOLVED → CLAIMED → VERIFIED`.

**Analyst** (on-demand, per-market)
The edge calculator. Takes a Meteorologist signal and a market's implied probability, computes the edge, and sizes the position. Mostly pure Python (Kelly math, spread adjustment), but escalates to Sonnet when the edge is marginal (2-5%) and needs qualitative validation.

**Strategist** (on-demand, weekly)
Portfolio-level review. Looks at the week's trades, PnL trajectory, and model calibration metrics. Runs on Sonnet. Outputs: recommended parameter adjustments, markets to exit, new market types to consider.

**Trader** (long-running daemon)
Execution engine. Takes sized orders from Analyst, manages order book interaction, handles slippage, and implements TWAP (time-weighted average price) for larger orders. Pure Python + Polymarket API.

**RiskGuard** (long-running daemon, independent)
The circuit breaker. Runs as a separate process with read access to Bookkeeper state. Can veto any order before execution. Can halt all trading. Cannot be overridden by Conductor. Details in Section 5.

**Chronicler** (on-demand, post-trade)
Logs every trade with full context: the weather signal, the market state, the edge calculation, the execution details, and the outcome. Writes to append-only SQLite. Used by Strategist for weekly review and by the learning loop.

### 1.2 Communication Protocol

All inter-agent communication goes through Python function calls mediated by Conductor. No message queues, no pub/sub — that's overengineering for a system with < 10 agents and < 100 messages/day. The exception is RiskGuard, which runs in a separate process and communicates via a shared SQLite table (`risk_state`) that the Trader checks before every execution.

```
risk_state table:
| field          | type    | description                    |
|----------------|---------|--------------------------------|
| halted         | bool    | global kill switch             |
| halt_reason    | text    | why trading was halted         |
| max_order_size | float   | current per-order limit (USDC) |
| daily_loss_limit| float  | remaining daily loss budget    |
| updated_at     | datetime| last RiskGuard heartbeat       |
```

If `updated_at` is more than 5 minutes stale, Trader refuses to execute (RiskGuard may have crashed).

### 1.3 Lifecycle Summary

| Agent        | Lifecycle     | Invoke Frequency      | LLM Layer |
|-------------|---------------|----------------------|-----------|
| Conductor   | Long-running  | Every 15 min         | None      |
| Sentinel    | Long-running  | Every 6 hours        | L1 (Flash)|
| Meteorologist| On-demand    | Per market per tick   | L1 (Flash)|
| Bookkeeper  | Long-running  | Continuous            | None      |
| Harvester   | On-demand     | At market resolution  | None      |
| Analyst     | On-demand     | Per market per tick   | L0/L2     |
| Strategist  | On-demand     | Weekly                | L2 (Sonnet)|
| Trader      | Long-running  | On order              | None      |
| RiskGuard   | Long-running  | Every 60 seconds      | None      |
| Chronicler  | On-demand     | Post-trade            | None      |

---

## 2. Information Acquisition & Signal Generation

### 2.1 Data Sources

**Primary: Open-Meteo Ensemble API** (free, no API key required)
Aggregates GFS, ECMWF IFS, ICON, GEM, and other models into a single API. Returns ensemble member forecasts, giving you probability distributions out of the box. This is the highest-value-per-engineering-hour data source for a solo developer.

```python
# Example: Get temperature ensemble for Chicago
# Returns 51 ensemble members from ECMWF
GET https://ensemble-api.open-meteo.com/v1/ensemble
  ?latitude=41.88&longitude=-87.63
  &hourly=temperature_2m
  &models=ecmwf_ifs025
  &forecast_days=7
```

**Secondary: NOAA GFS via NOMADS** (free)
The raw GFS model output. 6-hourly updates, 0.25° resolution. Use for verification and for variables Open-Meteo doesn't expose (e.g., certain precipitation types, severe weather parameters).

**Tertiary: NWS API** (free)
Textual forecasts, watches, warnings, and advisories. Critical for detecting forecast "regime changes" — when NWS forecasters override the models. Parse with Gemini Flash.

**NOT recommended for MVP: ECMWF direct access.** The data quality is the best in the world, but the API costs money and the data format (GRIB2) requires significant engineering to process. Open-Meteo already gives you ECMWF ensemble data for free.

### 2.2 Signal Generation Pipeline

```
Raw Data → Ensemble Extraction → Threshold Probability → Calibration → Tradeable Signal
```

**Step 1: Ensemble Extraction**
For a market like "Will NYC temperature exceed 95°F on July 15?", pull the 51-member ECMWF ensemble for that location and date.

**Step 2: Threshold Probability (Raw)**
Count ensemble members exceeding the threshold.

```python
def raw_probability(ensemble_members: list[float], threshold: float) -> float:
    """Naive ensemble probability."""
    exceedances = sum(1 for m in ensemble_members if m > threshold)
    return exceedances / len(ensemble_members)
```

**Step 3: Calibration (Critical)**
Raw ensemble probabilities are systematically miscalibrated. ECMWF ensembles tend to be underdispersive (too confident). Apply a historical calibration correction.

```python
def calibrated_probability(
    raw_p: float,
    lead_days: int,
    variable: str,
    location_cluster: str
) -> tuple[float, float]:  # (calibrated_p, uncertainty)
    """
    Apply calibration based on historical forecast verification.
    Returns calibrated probability and estimation uncertainty.
    """
    # Load pre-computed calibration curves
    # Built from 2+ years of ensemble forecasts vs observations
    cal_curve = CALIBRATION_CURVES[variable][location_cluster][lead_days]
    
    calibrated = cal_curve.transform(raw_p)
    
    # Uncertainty increases with lead time and decreases with sample count
    uncertainty = cal_curve.confidence_interval(raw_p, alpha=0.1)
    
    return calibrated, uncertainty
```

The calibration curves are the most important intellectual property in the system. Build them by backtesting ensemble forecasts against NOAA observed data (ISD/ASOS stations). This is a one-time computation that should be updated monthly.

**Step 4: Multi-Model Blending**
Don't rely on a single model. Weight GFS and ECMWF based on recent performance for each variable and lead time.

```python
def blended_probability(ecmwf_p, gfs_p, lead_days, variable):
    """Dynamic model weighting based on recent verification."""
    ecmwf_weight = MODEL_WEIGHTS[variable][lead_days]['ecmwf']  # e.g., 0.65
    gfs_weight = MODEL_WEIGHTS[variable][lead_days]['gfs']      # e.g., 0.35
    return ecmwf_p * ecmwf_weight + gfs_p * gfs_weight
```

### 2.3 Calibration Against Market Implied Probability

```python
def compute_edge(
    model_p: float,
    model_uncertainty: float,
    market_yes_price: float,
    market_spread: float
) -> Edge:
    """
    Compare model probability to market-implied probability.
    Account for spread as transaction cost.
    """
    market_implied_p = market_yes_price  # On Polymarket, price ≈ probability
    
    # Effective cost: you pay the ask, sell at the bid
    effective_buy_price = market_yes_price + market_spread / 2
    effective_sell_price = market_yes_price - market_spread / 2
    
    # Edge on YES side
    yes_edge = model_p - effective_buy_price
    # Edge on NO side  
    no_edge = (1 - model_p) - (1 - effective_sell_price)
    
    # Only trade if edge exceeds uncertainty + minimum threshold
    min_edge = max(0.05, model_uncertainty * 1.5)
    
    if yes_edge > min_edge:
        return Edge(side='YES', magnitude=yes_edge, confidence=yes_edge / model_uncertainty)
    elif no_edge > min_edge:
        return Edge(side='NO', magnitude=no_edge, confidence=no_edge / model_uncertainty)
    else:
        return None
```

---

## 3. Edge Discovery & Strategy Logic

### 3.1 Alpha Sources (Ranked by Reliability)

**Source 1: Temporal edge (most reliable)**
Weather model accuracy degrades predictably with lead time. At 1-2 day lead, ECMWF is ~85% reliable for temperature thresholds. At 7+ days, it drops to ~60%. Many Polymarket participants don't adjust for this — they anchor on the current forecast regardless of lead time. When the market prices a 7-day forecast as if it were a 2-day forecast, there's a systematic edge.

**Source 2: Calibration edge**
As described above, raw ensemble probabilities are miscalibrated. If you have better calibration curves than the market, you have an edge.

**Source 3: Update latency edge**
NWS issues forecast updates throughout the day. Open-Meteo updates 4x daily. If you process these updates faster than the market adjusts, you can trade on stale prices. This edge is small (~1-2%) but consistent.

**Source 4: Tail event mispricing**
Markets systematically underprice extreme weather events because humans underweight low-probability outcomes (prospect theory). When your ensemble shows a 8% chance of a record-breaking event and the market prices it at 3%, that's a significant edge — even accounting for the model's own tail unreliability.

### 3.2 Position Sizing: Fractional Kelly

Full Kelly is optimal in theory but catastrophic in practice — it assumes your edge estimate is perfect, which it never is. Use **quarter-Kelly** as the default, with dynamic adjustment.

```python
def kelly_fraction(
    edge: float,
    win_probability: float,
    kelly_multiplier: float = 0.25  # Quarter-Kelly default
) -> float:
    """
    Fractional Kelly criterion for position sizing.
    
    Full Kelly: f* = (p * b - q) / b
    where p = win prob, q = 1-p, b = odds (payout ratio)
    
    Quarter-Kelly reduces variance by ~75% while capturing ~50% of growth.
    """
    if edge <= 0:
        return 0.0
    
    # For binary markets, b = (1/price) - 1
    b = (1 / (win_probability - edge)) - 1  # payout odds
    q = 1 - win_probability
    
    full_kelly = (win_probability * b - q) / b
    
    # Apply fractional multiplier and cap
    position_fraction = full_kelly * kelly_multiplier
    return min(position_fraction, 0.15)  # Never more than 15% of bankroll on one market


def dynamic_kelly_adjustment(
    base_fraction: float,
    model_uncertainty: float,
    lead_days: int,
    recent_win_rate: float,
    portfolio_heat: float  # fraction of bankroll currently at risk
) -> float:
    """
    Adjust Kelly fraction based on regime indicators.
    """
    adjustment = 1.0
    
    # Reduce sizing when model uncertainty is high
    if model_uncertainty > 0.15:
        adjustment *= 0.5
    
    # Reduce sizing at long lead times (more uncertainty)
    if lead_days > 5:
        adjustment *= 0.6
    
    # Reduce sizing when recent performance is poor
    if recent_win_rate < 0.45:  # Below break-even
        adjustment *= 0.5
    
    # Reduce sizing when portfolio heat is high
    if portfolio_heat > 0.40:
        adjustment *= (1.0 - portfolio_heat)
    
    return base_fraction * adjustment
```

### 3.3 Execution Strategy

Polymarket weather markets are **thin**. Typical order book depth is $500-2000 at the best bid/ask. Strategy:

**For orders < $50:** Market order. The slippage is negligible and speed matters more than price improvement.

**For orders $50-500:** Limit order at the current best price, with a 5-minute timeout. If not filled, cancel and re-evaluate (the signal may have changed).

**For orders > $500:** TWAP over 2-4 hours. Split into $100-200 chunks, placed at randomized intervals. This prevents signaling and reduces market impact.

```python
class TWAPExecutor:
    def __init__(self, total_size: float, duration_hours: float, chunk_size: float = 150.0):
        self.chunks = int(total_size / chunk_size)
        self.interval = duration_hours * 3600 / self.chunks
        self.jitter = self.interval * 0.3  # ±30% randomization
    
    async def execute(self, market_id: str, side: str):
        for i in range(self.chunks):
            # Re-check edge before each chunk
            current_edge = self.analyst.get_current_edge(market_id)
            if current_edge is None or current_edge.magnitude < 0.03:
                log.info(f"Edge disappeared after {i}/{self.chunks} chunks")
                break
            
            await self.trader.place_limit_order(
                market_id, side, self.chunk_size, 
                price=current_edge.entry_price
            )
            
            sleep_time = self.interval + random.uniform(-self.jitter, self.jitter)
            await asyncio.sleep(sleep_time)
```

---

## 4. Model Cost Layering Architecture

### 4.1 Layer Definitions

The guiding principle: **every token must earn its keep.** If a task can be solved with a regex, don't call an LLM. If it can be solved with Flash, don't call Sonnet.

**Layer 0: Pure Python** (~85% of all operations)
- All API calls (Polymarket, Open-Meteo, NOAA)
- JSON/CSV parsing of structured data
- All mathematical operations (Kelly, PnL, position sizing)
- Cron scheduling and orchestration
- State management (SQLite reads/writes)
- Settlement harvesting
- Cost: $0

**Layer 1: Gemini 2.5 Flash** (~12% of operations)
- NOAA text forecast parsing (extracting structured data from NWS discussion text)
- Ambiguous market description classification ("Does this market mean surface temp or heat index?")
- Routine Sentinel scanning of new markets
- Simple data quality checks on weather data
- Cost: ~$0.001/call, ~$1.20/month at projected volume

**Layer 2: Claude Sonnet 4.6** (~2.5% of operations)
- Weekly strategy review (Strategist agent)
- Emergency evaluation (when RiskGuard detects anomalies)
- Marginal edge validation (when Analyst isn't confident)
- Monthly calibration review
- Cost: ~$0.02/call, ~$10-15/month

**Layer 3: Claude Opus 4.6 / GPT-5.4** (~0.5% of operations)
- Novel market type analysis (new weather product you haven't seen before)
- System architecture decisions (prompted by you, not autonomous)
- Quarterly deep post-mortem analysis
- Cost: ~$0.15/call, ~$5-8/month

### 4.2 Preventing Token Cost Explosion

Agentic loops are the #1 cost risk. An agent that calls another agent that calls another agent can burn through $50 in tokens before you notice. Defenses:

**Hard budget per invocation chain:**
```python
class TokenBudget:
    def __init__(self, max_tokens_per_chain: int = 8000):
        self.remaining = max_tokens_per_chain
    
    def consume(self, tokens: int) -> bool:
        self.remaining -= tokens
        if self.remaining <= 0:
            raise TokenBudgetExhausted(
                f"Chain exceeded {self.max_tokens_per_chain} token budget"
            )
        return True
```

**No recursive agent calls.** The Conductor calls agents; agents never call each other. This eliminates the possibility of infinite loops.

**Context compression.** Before passing data to any LLM call, strip it to the minimum needed. Don't pass the full 51-member ensemble to Flash — pass the summary statistics (mean, std, percentiles).

**Caching.** Cache LLM responses for identical inputs. Weather data changes every 6 hours, so cache TTL = 6 hours for L1 calls, 24 hours for L2 calls.

### 4.3 Monthly Token Budget

```
Layer 0 (Python):     $0.00
Layer 1 (Flash):      $1.20   (1200 calls × $0.001)
Layer 2 (Sonnet):     $12.00  (600 calls × $0.02)
Layer 3 (Opus/5.4):   $6.00   (40 calls × $0.15)
─────────────────────────────
Target monthly:       $19.20
Hard ceiling:         $75.00
Kill switch:          $100.00  (auto-halt all LLM calls)
```

The kill switch is a simple counter in SQLite that every LLM wrapper checks before making a call.

---

## 5. Risk Control & Self-Supervision

### 5.1 Decision Degradation Detection

The system must detect when its own predictions are getting worse. Three mechanisms:

**Brier Score Rolling Window**
Track the Brier score (mean squared probability error) over a rolling 30-trade window. If the score exceeds 0.35 (equivalent to being no better than a coin flip on binary outcomes), halt trading and trigger a Sonnet review.

```python
def brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """Lower is better. 0.25 = coin flip. 0.0 = perfect."""
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / len(predictions)

class DegradationDetector:
    THRESHOLD = 0.30       # Was 0.35, tightened per your bug fix
    WINDOW = 30
    
    def check(self, recent_trades: list[Trade]) -> bool:
        if len(recent_trades) < self.WINDOW:
            return False  # Not enough data
        
        predictions = [t.model_probability for t in recent_trades[-self.WINDOW:]]
        outcomes = [t.actual_outcome for t in recent_trades[-self.WINDOW:]]
        
        score = brier_score(predictions, outcomes)
        if score > self.THRESHOLD:
            self.trigger_review(score, recent_trades)
            return True
        return False
```

**Calibration Drift**
Monthly, compare predicted probabilities to actual outcomes in buckets (0-10%, 10-20%, etc.). If any bucket is off by more than 15 percentage points, flag for recalibration.

**Win Rate Decay**
If the rolling 20-trade win rate drops below 40% (below the break-even threshold after fees), reduce position sizes by 50% automatically.

### 5.2 Circuit Breakers

```python
class RiskGuard:
    """Independent process. Cannot be overridden by Conductor."""
    
    RULES = {
        'max_single_position': 0.15,    # 15% of bankroll
        'max_portfolio_heat': 0.50,      # 50% of bankroll at risk
        'max_daily_loss': 0.08,          # 8% of bankroll
        'max_weekly_loss': 0.15,         # 15% of bankroll
        'max_correlated_exposure': 0.25, # 25% on correlated markets
        'max_single_market_loss': 0.05,  # 5% of bankroll per market
    }
    
    def approve(self, order: Order) -> bool:
        """Pre-trade risk check. All rules must pass."""
        checks = [
            self._check_position_limit(order),
            self._check_portfolio_heat(order),
            self._check_daily_loss(order),
            self._check_weekly_loss(order),
            self._check_correlation(order),
        ]
        
        if not all(checks):
            self._log_rejection(order, checks)
            return False
        return True
    
    def _check_correlation(self, order: Order) -> bool:
        """
        Weather markets are often correlated.
        'NYC temp > 95°F' and 'NYC heat wave' are ~80% correlated.
        Cap total exposure to correlated markets.
        """
        correlated_markets = self.get_correlated_markets(order.market_id)
        total_exposure = sum(
            self.bookkeeper.get_position(m).notional 
            for m in correlated_markets
        )
        return (total_exposure + order.notional) / self.bankroll < self.RULES['max_correlated_exposure']
```

### 5.3 Black Swan Protection

Weather black swans (derechos, bomb cyclones, unprecedented heat waves) are the existential risk. Defenses:

**Position diversity.** Never have more than 3 positions on the same geography. A freak event in one city shouldn't wipe you out.

**Maximum loss per market.** The `max_single_market_loss` rule means no single market can cost you more than 5% of your bankroll. Combined with quarter-Kelly sizing, actual losses are typically 1-3%.

**Model distrust at extremes.** When the ensemble shows > 20% probability of a record-breaking event (95th percentile of historical observations), automatically increase model uncertainty by 2x. This shrinks position sizes on exactly the trades where the model is least reliable.

**Immediate exit triggers.** If NWS issues an "Extreme" alert for a location where you hold a position, exit the position at market price regardless of PnL. The information asymmetry flips — the market will adjust faster than your model.

---

## 6. Continuous Learning & Self-Evolution

### 6.1 Learning From Trades

**What:** After each market resolves, the Chronicler logs a structured record:
```python
@dataclass
class TradeRecord:
    market_id: str
    market_type: str         # temp_threshold, precip_threshold, etc.
    location: str
    lead_days_at_entry: int
    model_probability: float
    market_price_at_entry: float
    edge_at_entry: float
    position_size: float
    outcome: int             # 1 = YES resolved, 0 = NO
    pnl: float
    weather_data_snapshot: dict  # The forecast at time of trade
    actual_observation: float    # The observed weather value
```

**Monthly analysis (Sonnet-powered):**
The Strategist agent receives the last month's TradeRecords and answers:
1. Which market types had the highest/lowest edge realization?
2. Are there systematic biases by lead time, geography, or variable?
3. Which trades would you have sized differently in hindsight?
4. Are there new market types appearing that we should model?

### 6.2 Model Recalibration Triggers

Recalibration is expensive (requires reprocessing historical data), so it should be triggered, not scheduled.

**Trigger 1: Calibration drift** detected by the 15pp bucket test (Section 5.1).

**Trigger 2: Seasonal transition.** Weather model performance changes systematically between seasons (e.g., convective precipitation is harder to forecast in summer). Force recalibration at the equinoxes and solstices (4x/year).

**Trigger 3: Model change.** When ECMWF or GFS deploys a major model update (happens 1-2x per year), all calibration curves are potentially invalid. Force full recalibration.

**Trigger 4: 50-trade milestone.** After every 50 resolved trades, run a lightweight calibration check. If within tolerance, continue. If drifted, recalibrate.

### 6.3 Overfitting Prevention

The #1 risk in backtesting weather strategies is overfitting to historical weather patterns. A model that learned "July in NYC is always hot" will fail in the year it isn't.

**Defense 1: Walk-forward validation only.** Never backtest on a fixed historical window. Use expanding-window walk-forward: train on months 1-12, test on month 13. Train on months 1-13, test on month 14. Etc. This mimics how the system actually operates.

**Defense 2: Limit model complexity.** The calibration curves should be isotonic regression (monotonic, nonparametric) — not neural networks, not gradient-boosted trees. Simpler models overfit less, and calibration curves should be smooth and monotonic by definition.

**Defense 3: Structural priors over statistical patterns.** The system should "know" that forecast skill degrades with lead time, not "learn" it from data. Hard-code this relationship and only fit the parameters (how fast does skill degrade?), not the shape.

**Defense 4: Ensemble diversity.** By blending multiple weather models, you reduce exposure to any single model's systematic bias. The weights themselves are fit on recent data (last 90 days), preventing them from locking in to stale patterns.

---

## 7. Deployment Roadmap

### Phase 1: Instrumented Manual Trading (Weeks 1-4)

**Goal:** Validate the signal pipeline with real market data, no automated execution.

**Build:**
- Meteorologist agent (signal generation only)
- Bookkeeper (manual position tracking)
- Chronicler (trade logging)
- Calibration curves for top 3 market types (temperature, precipitation, snowfall)

**Workflow:** Meteorologist generates signals → you review → you trade manually → Chronicler logs → you learn.

**Success Metrics:**
- Signal Brier score < 0.22 over 30+ evaluated markets
- Edge identification rate: ≥ 60% of trades you take should have had a positive expected value (verified post-resolution)
- Calibration error: < 10pp in each probability bucket
- Latency: signal generation < 30 seconds from data update

**Exit criteria:** 40+ markets evaluated, Brier score consistently < 0.22, at least 15 actual trades logged with positive EV.

### Phase 2: Semi-Automated with Human Veto (Weeks 5-10)

**Goal:** Automate signal → sizing → order, with human approval for execution.

**Build:**
- Conductor (orchestration)
- Sentinel (market scanning)
- Analyst (edge detection + sizing)
- Trader (order placement, but requires your confirmation)
- RiskGuard (automated pre-trade checks)
- Harvester (settlement claiming)

**Workflow:** Conductor runs on cron → generates sized trade recommendations → sends you a notification (Telegram/Discord) → you approve/reject → Trader executes.

**Success Metrics:**
- PnL positive over any rolling 2-week period
- Win rate ≥ 52% (accounting for fees)
- Average edge at entry ≥ 4%
- Zero risk rule violations
- System uptime > 95% (measured by successful cron completions)
- Monthly LLM cost < $30

**Exit criteria:** 6 consecutive weeks of positive PnL, no risk incidents, system running unattended for 48+ hours between interventions.

### Phase 3: Full Automation with Oversight (Weeks 11-20)

**Goal:** Remove human approval loop. You monitor dashboards, not individual trades.

**Build:**
- Strategist (weekly portfolio review)
- Degradation detection
- Full circuit breaker suite
- Monitoring dashboard (simple HTML page reading from SQLite)
- Alerting (Telegram notifications for RiskGuard events, degradation, and daily PnL summary)

**Workflow:** Fully autonomous. You check the dashboard 1-2x daily. Alerts notify you of anomalies.

**Success Metrics:**
- Monthly ROI ≥ 3% on deployed capital
- Maximum drawdown < 12% over any 30-day period
- Sharpe ratio > 1.5 (annualized, measured monthly)
- Model calibration error < 8pp per bucket
- Monthly LLM cost < $25 (optimized caching)
- Zero manual interventions required over any 7-day period

**Exit criteria:** 3 consecutive months of positive returns with Sharpe > 1.5.

### Phase 4: Scaling & Diversification (Months 6+)

- Expand to new weather market types (wind, humidity, extreme events)
- Add geographic diversification (European, Asian weather markets if available)
- Increase bankroll allocation as track record grows
- Consider adding non-weather prediction markets that share structural similarities (sports over/under, economic thresholds)

---

## 8. Top 3 Failure Modes & Defenses

### Failure 1: Calibration Rot (Most Likely)

**What happens:** Your calibration curves, built on historical data, slowly diverge from reality. New weather patterns, model updates, or shifting market microstructure make them stale. You keep trading on edges that no longer exist.

**Why it's dangerous:** It's silent. Your Brier score degrades by 0.01 per month — imperceptible in the noise until you've lost 20 trades in a row.

**Defense:**
- The 30-trade rolling Brier score check (Section 5.1) catches this within ~6 weeks
- Forced seasonal recalibration (4x/year) prevents long-term drift
- The Strategist's monthly review explicitly asks "are our calibration assumptions still holding?"
- Never let calibration curves go more than 90 days without at least a lightweight validation check

### Failure 2: Liquidity Trap (Most Expensive)

**What happens:** You build a position in a thin market, and when you need to exit (signal changes, risk limit hit), there's no liquidity on the other side. You're stuck holding a position you no longer want, or you exit at a massive loss.

**Why it's dangerous:** Polymarket weather markets are thin. A $500 position that takes 2 hours to build can only be exited at a 10-15% loss if there's no natural counterparty.

**Defense:**
- Never hold more than 20% of a market's daily volume
- Track order book depth before entering, and refuse to trade if your target position exceeds 30% of visible liquidity
- Use a hard time-to-resolution limit: no new positions in markets resolving in < 12 hours (you can't exit if wrong)
- Accept that some positions must be held to resolution — size them accordingly

### Failure 3: Correlated Blowup (Most Catastrophic)

**What happens:** You hold YES on "NYC > 95°F", YES on "NYC heat wave", and YES on "Northeast power demand spike". A surprise cold front arrives, and all three markets go against you simultaneously. Your diversification was illusory.

**Why it's dangerous:** Weather events are inherently correlated across geography and market type. "Independent positions" in weather markets are often just the same bet repackaged.

**Defense:**
- The correlation check in RiskGuard (Section 5.2) caps total correlated exposure at 25%
- Maintain a correlation matrix (precomputed) for all active market pairs. Update monthly.
- Simple correlation heuristic: same city = 0.8 corr, same region = 0.5 corr, same variable different region = 0.3 corr. Override with empirical data when available.
- Portfolio heat limit of 50% means even a total correlated wipeout only costs half your bankroll

---

## Appendix: Technology Stack

```
Language:       Python 3.12+
Database:       SQLite (single-file, no server, easy backup)
Scheduling:     APScheduler (in-process cron)
HTTP:           httpx (async, connection pooling)
LLM:            litellm (unified interface to all providers)
Deployment:     Single VPS (Hetzner, ~$10/mo) or your local machine
Monitoring:     Simple Flask dashboard + Telegram bot for alerts
Version Control: Git, with .env for API keys
```

**Why not Postgres/Redis/Kafka/K8s?** You're one person trading < $10K. Every additional infrastructure component is a liability — another thing that can crash at 3 AM when a market is resolving. SQLite handles millions of rows, needs zero configuration, and backs up with `cp`. When you're managing $100K+, then add infrastructure.
