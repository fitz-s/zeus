# Zeus Pipeline Trace: Complete Trade Lifecycle

**Generated**: 2026-04-06
**Scope**: Signal discovery through settlement, with explicit paper/live divergence markers.

---

## Phase 1: Signal Discovery (Cycle Trigger)

### 1.1 Scheduler fires

**Function**: `src/main.py:main()` (line 233)
**Reads from**: `config/settings.json` (via `settings.mode`, `settings["discovery"]`)
**Writes to**: nothing (scheduler setup only)

APScheduler creates three recurring jobs based on `settings["discovery"]`:

| Job | Trigger | Calls |
|-----|---------|-------|
| `opening_hunt` | interval (configurable minutes) | `_run_mode(DiscoveryMode.OPENING_HUNT)` |
| `update_reaction_{time}` | cron at specific UTC times | `_run_mode(DiscoveryMode.UPDATE_REACTION)` |
| `day0_capture` | interval (configurable minutes) | `_run_mode(DiscoveryMode.DAY0_CAPTURE)` |
| `harvester` | every 1 hour | `_harvester_cycle()` |

**Process lock**: `src/engine/process_lock.py:acquire_process_lock()` prevents double-daemon at startup (line 245).
**Cross-mode lock**: `_cycle_lock` (threading.Lock) prevents concurrent cycles within the process (line 25).

**PAPER/LIVE: SAME** -- scheduler logic is identical.

### 1.2 run_cycle() entry

**Function**: `src/engine/cycle_runner.py:run_cycle()` (line 128)
**Reads from**: `zeus.db` (via `get_connection()`), `state/positions-{mode}.json` (via `load_portfolio()`), `state/strategy_tracker-{mode}.json`
**Writes to**: summary dict (in-memory), CycleArtifact

Initialization sequence:
1. `_utcnow()` → decision_time
2. Clear ensemble cache (`_clear_cache()`)
3. Clear active events cache (`_clear_active_events_cache()`)
4. Process control plane commands (`control_plane.py:process_commands()`)
5. `get_current_level()` → risk level check (GREEN/ORANGE/RED)
6. `get_connection()` → opens `state/zeus.db`
7. `load_portfolio()` → loads `state/positions-{mode}.json` (DB-first with JSON fallback)
8. `PolymarketClient(paper_mode=(settings.mode == "paper"))` → CLOB client
9. `get_tracker()` → loads strategy tracker
10. `RiskLimits()` → sizing constraints

### 1.3 Weather data fetch

**Function**: `src/data/ensemble_client.py:fetch_ensemble()` (line 55)
**Reads from**: Open-Meteo Ensemble API (`https://ensemble-api.open-meteo.com/v1/ensemble`)
**Writes to**: in-memory cache (`_ENSEMBLE_CACHE`), then `ensemble_snapshots` table in zeus.db

ENS fetch happens inside `evaluate_candidate()` (evaluator.py:305):
- ECMWF IFS 51-member ensemble (primary)
- GFS 31-member ensemble (crosscheck, non-day0 only)
- Returns `members_hourly` ndarray + `times` + metadata

For Day0 mode, also fetches observations:
**Function**: `src/data/observation_client.py:get_current_observation()`
**Reads from**: Weather Underground API (current conditions for city)

### 1.4 Market data fetch

**Function**: `src/data/market_scanner.py:find_weather_markets()` (line 29)
**Reads from**: Gamma API (`https://gamma-api.polymarket.com/events`)
**Writes to**: `_ACTIVE_EVENTS_CACHE` (module-level cache)

Returns list of active temperature markets with:
- City match, target date, outcomes (bins with token_ids)
- `hours_since_open`, `hours_to_resolution`

Orderbook data (per-bin) fetched later inside `evaluate_candidate()`:
**Function**: `src/data/polymarket_client.py:PolymarketClient.get_best_bid_ask()` (via `get_orderbook()`)
**Reads from**: Polymarket CLOB API (`https://clob.polymarket.com/book`)

**PAPER/LIVE: SAME** -- both modes fetch real market data from the same APIs. The Gamma API and CLOB orderbook endpoints are public/unauthenticated.

### 1.5 Pre-discovery housekeeping (live-only)

Before discovery, `run_cycle()` runs several live-only reconciliation steps:

| Step | Function | Paper | Live |
|------|----------|-------|------|
| Reconcile pending entries | `fill_tracker.py:check_pending_entries()` | **skipped** (paper has no pending) | Checks CLOB order status for `pending_tracked` positions |
| Chain sync | `cycle_runtime.py:run_chain_sync()` | Returns `{"skipped": "paper_mode"}, True` | Fetches on-chain positions from Polymarket API, reconciles with local state |
| Quarantine timeout check | `chain_reconciliation.py:check_quarantine_timeouts()` | Runs but no quarantined positions | Expires stale quarantines |
| Orphan order cleanup | `cycle_runtime.py:cleanup_orphan_open_orders()` | **skipped** | Cancels CLOB orders not tracked in portfolio |
| Entry bankroll | `cycle_runtime.py:entry_bankroll_for_cycle()` | `min(config_cap, effective_bankroll)` | `min(config_cap, wallet_balance + exposure)` via `clob.get_balance()` |

---

## Phase 2: Edge Calculation

### 2.1 Signal pipeline: p_raw -> p_cal -> p_posterior -> edge

**Function**: `src/engine/evaluator.py:evaluate_candidate()` (line 240)

```
                                   +-----------+
config/cities.json                 |  Market   |
     |                             | Scanner   |
     v                             +-----+-----+
  City config                            |
     |                                   v
     |  +----------+           outcomes (bins, token_ids)
     +->| ENS API  |                     |
        | (ECMWF)  |                     v
        +----+-----+         +----------+----------+
             |                | evaluate_candidate  |
             v                +---------------------+
   members_hourly (51×H)               |
             |                         |
             v                         v
   +-------------------+     +-------------------+
   | EnsembleSignal /  |     | PolymarketClient  |
   | Day0Signal        |     | .get_best_bid_ask |
   +--------+----------+     +--------+----------+
            |                          |
            v                          v
         p_raw[]                   p_market[]
            |                    (via VWMP)
            v
   +-------------------+
   | Platt calibration |
   | (calibrate_and_   |
   |  normalize)       |
   +--------+----------+
            |
            v
         p_cal[]
            |
            +---- alpha blending ----> p_posterior = alpha*p_cal + (1-alpha)*p_market
            |
            v
   +-------------------+
   | MarketAnalysis     |
   | .find_edges()      |
   +--------+----------+
            |
            v
       edges[] (BinEdge objects with edge, p_posterior, direction)
            |
            v
   +-------------------+
   | fdr_filter()       |
   +--------+----------+
            |
            v
       filtered_edges[]
```

### 2.2 Detailed function chain

| Step | Function | File:Line | Reads | Writes |
|------|----------|-----------|-------|--------|
| ENS fetch | `fetch_ensemble(city)` | `data/ensemble_client.py:55` | Open-Meteo API | in-memory cache |
| ENS validation | `validate_ensemble(result)` | `data/ensemble_client.py` | in-memory | -- |
| ENS snapshot store | `_store_ens_snapshot()` | `engine/evaluator.py:926` | -- | `zeus.db:ensemble_snapshots` |
| p_raw (ENS mode) | `EnsembleSignal.p_raw_vector(bins)` | `signal/ensemble_signal.py` | members_hourly | -- |
| p_raw (Day0 mode) | `Day0Signal.p_vector(bins)` | `signal/day0_signal.py` | observation + remaining_member_maxes | -- |
| Store p_raw | `_store_snapshot_p_raw()` | `engine/evaluator.py:981` | -- | `zeus.db:ensemble_snapshots.p_raw_json` |
| Calibration | `get_calibrator(conn, city, target_date)` | `calibration/manager.py` | `zeus.db:calibration_pairs` | -- |
| Platt transform | `calibrate_and_normalize(p_raw, cal, lead_days, bin_widths)` | `calibration/platt.py` | -- | -- |
| GFS crosscheck | `fetch_ensemble(city, model="gfs025")` | `data/ensemble_client.py:55` | Open-Meteo API (GFS) | -- |
| Model agreement | `model_agreement(p_raw, gfs_p)` | `signal/model_agreement.py` | -- | -- |
| Alpha computation | `compute_alpha(cal_level, spread, agreement, lead, hours)` | `strategy/market_fusion.py:61` | `config/settings.json` edge params | -- |
| VWMP per bin | `vwmp(bid, ask, bid_sz, ask_sz)` | `strategy/market_fusion.py:42` | CLOB orderbook | -- |
| Microstructure log | `log_microstructure()` | `state/db.py` | -- | `zeus.db:microstructure_snapshots` |
| Edge detection | `MarketAnalysis.find_edges(n_bootstrap)` | `strategy/market_analysis.py` | p_raw, p_cal, p_market, alpha, bins | -- |
| FDR filter | `fdr_filter(edges)` | `strategy/fdr_filter.py` | -- | -- |

### 2.3 Alpha blending

**Function**: `src/strategy/market_fusion.py:compute_alpha()` (line 61)
**Reads from**: `config/settings.json` (edge.base_alpha, edge.spread_tight_f, edge.spread_wide_f)

Alpha formula: `p_posterior = alpha * p_cal + (1 - alpha) * p_market`
- `alpha` ranges [0.20, 0.85]
- Base alpha from calibration level (level1-4 in config)
- Adjusted by: ensemble spread, model agreement, lead days, hours since open

**PAPER/LIVE: SAME** -- edge calculation is identical. Both use real ENS and real CLOB data.

---

## Phase 3: Sizing & Decision

### 3.1 Kelly sizing

**Function**: `src/engine/evaluator.py` (line 807)
**Reads from**: `config/settings.json` (sizing.kelly_multiplier, sizing limits), portfolio state

```
kelly_size(p_posterior, exec_price, bankroll, kelly_mult * risk_throttle)
```

Where:
- `dynamic_kelly_mult()` adjusts base Kelly by CI width, lead days, portfolio heat
- `ExecutionPrice` wraps entry price with fee (via `polymarket_fee()`)
- Risk throttle: 50% if cluster exposure > 10%, 50% if global heat > 25%

### 3.2 Risk checks

**Function**: `src/strategy/risk_limits.py:check_position_allowed()` (line in evaluator.py:829)
**Reads from**: portfolio state, `config/settings.json` (sizing limits)

Checks in order:
1. `max_single_position_pct` -- single position vs bankroll
2. `max_city_pct` -- per-city exposure
3. `max_region_pct` -- per-cluster exposure
4. `max_portfolio_heat_pct` -- total portfolio heat
5. `max_correlated_pct` -- correlated exposure

Anti-churn layers (evaluator.py:682-724):
- `is_reentry_blocked()` -- recently exited same bin
- `is_token_on_cooldown()` -- token-level cooldown
- `has_same_city_range_open()` -- cross-date block

Strategy policy: `resolve_strategy_policy(conn, strategy_key, now)` can gate or throttle a strategy.

### 3.3 P10 Reality Contract Gate

**Function**: `src/engine/cycle_runner.py` (line 233-246)
**Reads from**: `config/reality_contracts/` (YAML files), `zeus.db`

```python
rcl_result = RealityContractVerifier(load_contracts()).verify_all_blocking()
if not rcl_result.can_trade:
    entries_blocked_reason = f"reality_contract_stale:{failed_ids}"
```

All blocking reality contracts must be fresh. Stale = skip entire discovery phase.

### 3.4 LIVE_LOCK Check

**Function**: `src/engine/cycle_runner.py` (line 207-211)
**Reads from**: `state/LIVE_LOCK` file existence

```python
_live_lock = Path(...) / "state" / "LIVE_LOCK"
if settings.mode == "live" and _live_lock.exists():
    entries_blocked_reason = "LIVE_LOCK: live trading locked"
```

File currently exists: `state/LIVE_LOCK` is present (confirmed).

### 3.5 Full entry gate chain (cycle_runner.py:204-260)

In order:
1. LIVE_LOCK file check (live mode only)
2. Portfolio quarantine check
3. Chain sync availability (live mode -- chain_api required)
4. Risk level (must be GREEN)
5. Entry bankroll availability (must be positive)
6. Portfolio heat near max
7. Reality contract verification (P10)
8. Entries paused (control plane)
9. Strategy gate per decision

**PAPER/LIVE: DIFFERENT** sizing bankroll source:
- **Paper**: `min(config_cap, effective_bankroll)` -- no wallet check
- **Live**: `min(config_cap, wallet_balance + exposure)` -- real wallet balance via CLOB API

---

## Phase 4: Order Execution (CRITICAL DIVERGENCE POINT)

### 4.1 Execution intent creation

**Function**: `src/execution/executor.py:create_execution_intent()` (line 123)
**Reads from**: EdgeContext, BinEdge, `config/settings.json` (execution section)

Creates `ExecutionIntent` with:
- `limit_price` = native limit via `compute_native_limit_price()` + dynamic ask-jump
- `is_sandbox = (settings.mode == "paper")`
- `timeout_seconds` = mode-based (4h/1h/15min)
- `token_id` = YES token or NO token depending on direction

### 4.2 Execution

**Function**: `src/execution/executor.py:execute_intent()` (line 176)

#### PAPER PATH (line 198-199)

```python
if intent.is_sandbox:
    return _paper_fill(trade_id, intent, edge_vwmp, label)
```

**Function**: `_paper_fill()` (line 233)
- **Instant fill** at VWMP (no order placed)
- Returns `OrderResult(status="filled", fill_price=edge_vwmp)`
- No API call, no order ID
- **Writes to**: nothing (pure return value)

#### LIVE PATH (line 200-209)

```python
else:
    return _live_order(trade_id, intent, shares)
```

**Function**: `_live_order()` (line 434)
- Creates `PolymarketClient(paper_mode=False)` (authenticates via macOS Keychain)
- Calls `client.place_limit_order(token_id, price, size, side="BUY")`
  - **Writes to**: Polymarket CLOB (on-chain order)
- Returns `OrderResult(status="pending", order_id=..., timeout_seconds=...)`
- Min-size retry: parses CLOB errors, bumps shares, max 2 retries

### 4.3 place_limit_order() call chain

**Function**: `src/data/polymarket_client.py:PolymarketClient.place_limit_order()`
**Reads from**: macOS Keychain (private key, funder address) via `_resolve_credentials()`
**Writes to**: Polymarket CLOB on-chain order

Uses `py-clob-client` library:
1. Signs order with Ethereum private key
2. Submits to CLOB REST API
3. Returns order confirmation dict with `orderID`

### 4.4 Position materialization

**Function**: `src/engine/cycle_runtime.py:materialize_position()` (line 191)

Creates a `Position` dataclass with all entry context. Key state difference:

| Field | Paper | Live |
|-------|-------|------|
| `state` | `"entered"` | `"pending_tracked"` |
| `order_id` | `""` (no order) | CLOB order ID |
| `order_status` | `"filled"` | `"pending"` |
| `chain_state` | `"unknown"` | `"local_only"` |
| `entered_at` | now ISO | `""` (not entered yet) |
| `order_posted_at` | `""` | now ISO |
| `order_timeout_at` | `""` | now + timeout ISO |
| `env` | `"paper"` | `"live"` |

Initial runtime state computed by `initial_entry_runtime_state_for_order_status()`:
- `"filled"` -> `"entered"`
- `"pending"` -> `"pending_tracked"`

### 4.5 Data writes after materialization

**Writes to** (in order, cycle_runtime.py:961-971):

1. `add_position(portfolio, pos)` -- adds to in-memory `PortfolioState.positions`
2. `log_trade_entry(conn, pos)` -- writes to `zeus.db:trade_decisions` table
3. `_dual_write_canonical_entry_if_available(conn, pos)` -- writes to `zeus.db:position_events` + `position_current`
4. `log_execution_report(conn, pos, result)` -- writes to `zeus.db:execution_reports`
5. `tracker.record_entry(pos)` (paper only, on fill) -- updates strategy tracker in-memory

At end of cycle:
6. `save_portfolio(portfolio)` -- atomic write to `state/positions-{mode}.json`
7. `save_tracker(tracker)` -- writes to `state/strategy_tracker-{mode}.json`
8. `store_artifact(conn, artifact)` -- writes to `zeus.db:decision_chain`
9. `write_status(summary)` -- writes to `state/status_summary-{mode}.json`

### 4.6 Paper vs Live Execution Summary

```
PAPER:                                    LIVE:
  evaluate_candidate()                      evaluate_candidate()
       |                                         |
       v                                         v
  create_execution_intent(sandbox=True)     create_execution_intent(sandbox=False)
       |                                         |
       v                                         v
  _paper_fill() → instant fill             _live_order() → CLOB API call
  status="filled"                          status="pending"
       |                                         |
       v                                         v
  materialize(state="entered")             materialize(state="pending_tracked")
       |                                         |
       v                                         v
  add_position → save_portfolio            add_position → save_portfolio
  (positions-paper.json)                   (positions-live.json)
       |                                         |
       v                                         v
  DONE — position is active                NEXT CYCLE: fill_tracker checks order
                                           → "entered" or voided
```

---

## Phase 5: Position Monitoring

### 5.1 Monitor cycle

**Function**: `src/engine/cycle_runtime.py:execute_monitoring_phase()` (line 367)

On **every** cycle, before discovery, the monitoring phase runs for all existing positions.

For each position (skipping pending_tracked, economically_closed, admin_closed, backoff_exhausted):

1. **Refresh probability**: `src/engine/monitor_refresh.py:refresh_position()` (line in cycle_runtime.py:510)
   - Re-fetches ENS data, recomputes p_raw for the position's bin
   - Re-runs Platt calibration
   - Fetches current CLOB market price
   - Updates position: `last_monitor_prob`, `last_monitor_edge`, `last_monitor_market_price`, `last_monitor_best_bid/ask`

2. **Build exit context**: `_build_exit_context()` (cycle_runtime.py:310)
   - Aggregates fresh prob, market price, best bid/ask, hours to settlement, etc.

3. **Evaluate exit**: `pos.evaluate_exit(exit_context)` (cycle_runtime.py:520)
   - Position owns its own exit logic (see Phase 6 below)

4. **Day0 window transition**: When `hours_to_settlement <= 6.0`, position state transitions:
   `entered/holding` -> `day0_window` (cycle_runtime.py:489-496)

### 5.2 Exit trigger evaluation

**Function**: `src/state/portfolio.py:Position.evaluate_exit()` (line 267)

8-layer exit trigger chain (evaluated in order):

| Layer | Trigger | Urgency | Condition |
|-------|---------|---------|-----------|
| 0 | INCOMPLETE_EXIT_CONTEXT | -- | Missing authority fields → fail closed |
| 1 | SETTLEMENT_IMMINENT | immediate | `hours_to_settlement < 1.0` |
| 2 | WHALE_TOXICITY | immediate | Adjacent bin sweep detected |
| 3 | MODEL_DIVERGENCE_PANIC | immediate | Divergence score > hard threshold |
| 4 | FLASH_CRASH_PANIC | immediate | Market velocity < -0.15/hr |
| 5 | VIG_EXTREME | normal | Market vig outside [0.92, 1.08] |
| 6 | Micro-position hold | -- | `size_usd < $1.00` → never sell |
| 7 | Direction-specific | normal | EDGE_REVERSAL (buy_yes) or BUY_NO_EDGE_EXIT (buy_no) |

Direction-specific exit logic:
- **buy_yes**: 2-consecutive-cycle negative edge + EV gate (sell $ > hold EV)
- **buy_no**: N-consecutive-cycle negative edge + EV gate + near-settlement hold

### 5.3 Live-only monitoring: pending exit checks

**Function**: `src/execution/exit_lifecycle.py:check_pending_exits()` (line 432)

Before monitoring, in live mode only:
- Checks CLOB order status for positions with `exit_state` in `("sell_placed", "sell_pending", "exit_intent")`
- FILL_STATUSES `{"MATCHED", "FILLED"}` -> `compute_economic_close()` -> `sell_filled`
- VOID_STATUSES `{"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}` -> retry
- Empty status (API outage) -> after 3 consecutive unknowns, trigger retry

**PAPER/LIVE: DIFFERENT** monitoring behavior:
- **Paper**: Monitor refresh uses same data sources, exit is instant
- **Live**: Additional pending exit reconciliation, chain state tracking, CLOB fill checks

---

## Phase 6: Exit Execution

### 6.1 Exit decision flow

**Function**: `src/execution/exit_lifecycle.py:execute_exit()` (line 183)

Called from `execute_monitoring_phase()` when `should_exit == True`:

```python
exit_intent = build_exit_intent(pos, exit_context, paper_mode=paper_mode)
outcome = execute_exit(portfolio, position, exit_context, paper_mode, clob, conn, exit_intent)
```

### 6.2 Paper exit

**Function**: `execute_exit()` lines 208-219

```python
if paper_mode:
    _mark_pending_exit(position)
    position.exit_state = "exit_intent"
    closed = compute_economic_close(portfolio, position.trade_id,
                                     exit_intent.current_market_price,
                                     exit_intent.reason)
    closed.exit_state = "sell_filled"
    return f"paper_exit: {exit_intent.reason}"
```

- **Instant close** at current market price
- `compute_economic_close()` sets pnl, exit_price, state="economically_closed"
- No order placed, no pending state

**Writes to**: portfolio in-memory (will be saved at end of cycle to `positions-paper.json`)

### 6.3 Live exit

**Function**: `_execute_live_exit()` (line 233)

State machine: `"" -> exit_intent -> sell_placed -> sell_pending -> sell_filled (economically_closed)`

1. **Collateral check**: `check_sell_collateral()` -- ensures shares are sellable
2. **Cancel stale order**: if retrying, cancel previous `last_exit_order_id`
3. **Place sell order**: `place_sell_order()` -> `execute_exit_order()` -> `client.place_limit_order(token_id, price, shares, side="SELL")`
   - Limit price = `current_price - exit_base_offset` (config), possibly adjusted to `best_bid`
   - Share quantization: SELL rounds DOWN
   - Min-size retry: up to 2 retries, sub-minimum lots return "hold_to_settlement"
4. **Quick fill check**: if order_id available, immediately check status
   - If FILLED: `compute_economic_close()` -> `sell_filled`
   - If not: `exit_state = "sell_pending"` -- checked next cycle

**Writes to**:
- `zeus.db` via `log_exit_attempt_event()`, `log_exit_fill_event()`, `log_exit_retry_event()`, `log_pending_exit_recovery_event()`
- Portfolio in-memory (saved at end of cycle)
- Polymarket CLOB (sell order)

### 6.4 Exit retry state machine

**Function**: `_mark_exit_retry()` (exit_lifecycle.py:662)

- Exponential backoff: 5min, 10min, 20min, ... capped at 60min
- Max 10 retries before `backoff_exhausted` (hold to settlement)
- `next_exit_retry_at` set as ISO timestamp
- `check_pending_retries()` releases cooldown on next eligible cycle

### 6.5 Canonical dual-write for exit: CONFIRMED ABSENT

**There is no `_dual_write_canonical_exit_if_available()` function.** The exit path writes to:
- `zeus.db:exit_attempt_events` (via various `log_exit_*` functions)
- Portfolio JSON (via `save_portfolio()`)
- Position projection (via `compute_economic_close()` which updates in-memory state)

But there is **no parallel write to `position_events` + `position_current`** for exits, unlike entries which have `_dual_write_canonical_entry_if_available()`. The settlement path does have `_dual_write_canonical_settlement_if_available()`.

This is the **canonical dual-write gap for exits** -- exits are recorded in portfolio JSON and exit-specific event tables, but not in the canonical event-sourced position_events table.

---

## Phase 7: Settlement (Harvester)

### 7.1 Harvester trigger

**Function**: `src/execution/harvester.py:run_harvester()` (line 113)
**Trigger**: APScheduler, every 1 hour (`main.py:287`)
**Reads from**: Gamma API (`GET /events?closed=true`), `zeus.db`, `state/positions-{mode}.json`

### 7.2 Settlement detection

**Function**: `_fetch_settled_events()` (harvester.py:217)
**Reads from**: `https://gamma-api.polymarket.com/events?closed=true`
- Paginates through closed events (200 per page)
- Filters to temperature events only (keyword match)

For each settled event:
1. `_match_city()` -- matches event title to city config
2. `_extract_target_date()` -- extracts target date
3. `_find_winning_bin()` -- determines winner via `winningOutcome == "Yes"` or `outcomePrices[0] >= 0.95`

### 7.3 Calibration pair generation

**Function**: `harvest_settlement()` (harvester.py:526)
**Writes to**: `zeus.db:calibration_pairs` (via `add_calibration_pair()`)

For each bin in the settled event:
- Winning bin: outcome=1
- Losing bins: outcome=0
- Looks up decision-time p_raw from `ensemble_snapshots.p_raw_json`
- Triggers `maybe_refit_bucket()` to update Platt model if enough new data

### 7.4 Position settlement

**Function**: `_settle_positions()` (harvester.py:566)
**Reads from**: portfolio.positions (matching city + target_date)
**Writes to**: `zeus.db` (multiple tables), portfolio state

For each matching position:
1. Skip non-terminal states (pending_tracked, quarantined, active exit, etc.)
2. Compute P&L: `shares * exit_price - cost_basis`
   - buy_yes: `exit_price = 1.0` if won, `0.0` if lost
   - buy_no: `exit_price = 1.0` if lost (for the YES outcome), `0.0` if won
3. `compute_settlement_close(portfolio, trade_id, settlement_price, "SETTLEMENT")` -- updates position state
4. Create `SettlementRecord`
5. **Redemption** (live only, harvester.py:643-652):
   ```python
   if exit_price > 0 and not paper_mode and pos.condition_id:
       clob.redeem(pos.condition_id)
   ```
   **Writes to**: Polymarket smart contract (USDC claim)
6. Add token to `ignored_tokens`
7. `log_event(conn, "SETTLEMENT", ...)` -- writes to `zeus.db:events`
8. `log_settlement_event(conn, pos, ...)` -- writes to `zeus.db:settlement_events`
9. `_dual_write_canonical_settlement_if_available()` -- writes to `zeus.db:position_events` + `position_current`

After all settlements:
- `store_settlement_records(conn, records)` -- writes to `zeus.db:decision_log` (settlement section)
- `save_portfolio(portfolio)` -- atomic write to `state/positions-{mode}.json`
- `save_tracker(tracker)` -- writes to `state/strategy_tracker-{mode}.json`

### 7.5 `settlements` table: CONFIRMED LEGACY

**Table**: `zeus.db:settlements` (defined in `state/db.py:88`)
**Status**: The table exists in the schema (`CREATE TABLE IF NOT EXISTS settlements`) but **`_settle_positions()` does not write to it**. Settlement data is written to `settlement_events`, `position_events`, `decision_log`, and `events` tables. The `settlements` table is inherited from Rainstorm and is **legacy/unused** for new writes.

---

## Phase 8: Paper/Live Isolation

### 8.1 state_path() / mode_state_path()

**Function**: `src/config.py:mode_state_path()` (line 22)

```python
def mode_state_path(filename: str, mode: Optional[str] = None) -> Path:
    mode = mode or os.environ.get("ZEUS_MODE", settings.mode)
    stem, ext = filename[:dot], filename[dot:]
    return STATE_DIR / f"{stem}-{mode}{ext}"
```

This is the **single control point** for per-process state isolation.

Example: `mode_state_path("positions.json")` with `mode="paper"` -> `state/positions-paper.json`

### 8.2 Per-mode files (isolated)

| File Pattern | Paper | Live |
|--------------|-------|------|
| `positions-{mode}.json` | `positions-paper.json` | `positions-live.json` |
| `strategy_tracker-{mode}.json` | `strategy_tracker-paper.json` | `strategy_tracker-live.json` |
| `status_summary-{mode}.json` | `status_summary-paper.json` | `status_summary-live.json` |
| `control_plane-{mode}.json` | `control_plane-paper.json` | `control_plane-live.json` |
| `risk_state-{mode}.db` | `risk_state-paper.db` | `risk_state-live.db` (implied) |

Confirmed in `state/` directory:
- `positions-paper.json` -- exists
- `control_plane-paper.json` -- exists
- `control_plane-live.json` -- exists
- `status_summary-paper.json` -- exists
- `strategy_tracker-paper.json` -- exists
- `risk_state-paper.db` -- exists

### 8.3 Shared files (NOT isolated)

| File | Content | Risk |
|------|---------|------|
| `zeus.db` | ENS snapshots, calibration_pairs, microstructure, trade_decisions, decision_chain, settlement_events, position_events, position_current, execution_reports | **Shared** -- both modes write to same DB |
| `state/LIVE_LOCK` | Lock file | Read by both, blocks live only |
| `config/settings.json` | All config | Read by both (mode field determines behavior) |
| `config/cities.json` | City metadata | Read by both |

### 8.4 trade_decisions.env column filtering

The `trade_decisions` table includes an `env` column (set to `settings.mode`):
- `log_trade_entry()` writes `pos.env` (which is set to `settings.mode` at materialization)
- Position.env is set during `materialize_position()` (cycle_runtime.py:242): `env=deps.settings.mode`
- Portfolio loader enforces mode: if `pos.env != current_mode`, raises `PortfolioModeError` (portfolio.py:709)

### 8.5 Potential contamination points

1. **zeus.db is shared**: Both paper and live write to the same `zeus.db`. Tables like `ensemble_snapshots`, `calibration_pairs`, `microstructure_snapshots` receive writes from both modes. This is **by design** -- these are "shared world data" (as commented in config.py:29). However, `trade_decisions` and `position_events` are env-tagged.

2. **Portfolio mode guard**: `_load_portfolio_from_json_data()` checks `pos.env != current_mode` and raises `PortfolioModeError`. This prevents loading a paper position into a live daemon.

3. **PolymarketClient paper_mode**: The client constructor takes `paper_mode=(settings.mode == "paper")`. In paper mode:
   - `_init_live_client()` is not called (no Keychain auth)
   - Chain sync returns `{"skipped": "paper_mode"}`
   - No CLOB orders placed

4. **No cross-mode position leakage**: The `mode_state_path()` function ensures each daemon reads/writes its own positions file. The `_cycle_lock` is per-process (not cross-process), so paper and live daemons running simultaneously would use separate lock instances.

5. **Risk**: If `ZEUS_MODE` env var is not set and `settings.mode` is changed in config between daemon restarts, a daemon could load the wrong positions file. The PortfolioModeError guard catches this for positions with `env` set, but legacy positions without `env` would default to the current mode.

---

## Appendix: Complete Data Write Map

### Per trade lifecycle

| Phase | Function | Destination | Paper | Live |
|-------|----------|-------------|-------|------|
| Entry | `add_position()` | in-memory portfolio | YES | YES |
| Entry | `log_trade_entry()` | `zeus.db:trade_decisions` | YES | YES |
| Entry | `_dual_write_canonical_entry_if_available()` | `zeus.db:position_events` + `position_current` | YES | YES |
| Entry | `log_execution_report()` | `zeus.db:execution_reports` | YES | YES |
| Entry | `save_portfolio()` | `state/positions-{mode}.json` | YES | YES |
| Entry | `client.place_limit_order()` | Polymarket CLOB | NO | YES |
| Monitor | `refresh_position()` | position fields (in-memory) | YES | YES |
| Monitor | `save_portfolio()` | `state/positions-{mode}.json` | YES | YES |
| Exit | `compute_economic_close()` | in-memory portfolio | YES (instant) | YES (on fill) |
| Exit | `place_sell_order()` | Polymarket CLOB | NO | YES |
| Exit | `log_exit_attempt_event()` | `zeus.db:exit_attempt_events` | NO | YES |
| Exit | `log_exit_fill_event()` | `zeus.db:exit_fill_events` | NO | YES |
| Exit | `save_portfolio()` | `state/positions-{mode}.json` | YES | YES |
| Settlement | `compute_settlement_close()` | in-memory portfolio | YES | YES |
| Settlement | `log_event("SETTLEMENT")` | `zeus.db:events` | YES | YES |
| Settlement | `log_settlement_event()` | `zeus.db:settlement_events` | YES | YES |
| Settlement | `_dual_write_canonical_settlement_if_available()` | `zeus.db:position_events` + `position_current` | YES | YES |
| Settlement | `store_settlement_records()` | `zeus.db:decision_log` | YES | YES |
| Settlement | `add_calibration_pair()` | `zeus.db:calibration_pairs` | YES | YES |
| Settlement | `clob.redeem()` | Polymarket smart contract | NO | YES |
| Settlement | `save_portfolio()` | `state/positions-{mode}.json` | YES | YES |
| Every cycle | `store_artifact()` | `zeus.db:decision_chain` | YES | YES |
| Every cycle | `write_status()` | `state/status_summary-{mode}.json` | YES | YES |
