# Zeus Risk & Strategy Reference

Durable reference for risk level computation, strategy governance,
Kelly sizing mechanics, and monitoring architecture.

Authority: executable source, tests, machine manifests, and authority docs win
on disagreement with this document.

---

## 1. Risk Level Architecture

### 1.1 `RiskLevel` enum

```python
class RiskLevel(Enum):
    GREEN         = "GREEN"          # Normal operation
    DATA_DEGRADED = "DATA_DEGRADED"  # Data quality issue, YELLOW-equivalent safety
    YELLOW        = "YELLOW"         # No new entries, continue monitoring
    ORANGE        = "ORANGE"         # No new entries, exit at favorable prices
    RED           = "RED"            # Cancel all pending, exit all immediately
```

`overall_level()` computes the maximum across all input levels using an
ordinal mapping: `GREEN=0, DATA_DEGRADED=1, YELLOW=2, ORANGE=3, RED=4`.

### 1.2 Risk inputs to `tick()`

RiskGuard's `tick()` computes 6 independent risk levels, then takes the max:

| Input level | How computed |
|-------------|-------------|
| `brier_level` | `evaluate_brier(brier_score, thresholds)` on settlement rows |
| `settlement_quality_level` | RED if settlement rows exist but none are metric-ready; YELLOW if any degraded rows |
| `execution_quality_level` | YELLOW if fill rate < 0.3 with ≥10 observed entries |
| `strategy_signal_level` | YELLOW if edge compression alerts or strategy tracker errors |
| `daily_loss_level` | From `_trailing_loss_snapshot()` with 24h lookback |
| `weekly_loss_level` | From `_trailing_loss_snapshot()` with 7-day lookback |

### 1.3 Trailing loss computation

`_trailing_loss_snapshot()` is the most complex risk input:

1. Query `risk_state` table for reference row within `[now - lookback, now]`
2. Validate reference row: `initial_bankroll` and `effective_bankroll` denote
   the same wallet-equity object (within `$0.01` tolerance). `total_pnl` is
   analytics/reporting evidence only and is not folded into bankroll truth.
3. If no valid reference → `DATA_DEGRADED` (not GREEN, not RED)
4. If valid reference found:
   - `loss = max(0, reference_equity - current_equity)`
   - `RED` if `loss > initial_bankroll × threshold_pct`
   - `GREEN` otherwise
5. If reference timestamp is stale (> 2h past the lookback cutoff):
   - RED losses stay RED
   - GREEN degrades to DATA_DEGRADED (unknown loss regime)

Status values: `{"ok", "stale_reference", "insufficient_history",
"inconsistent_history", "no_reference_row"}`.

### 1.4 `get_current_level()` — fail-closed read

When other modules need the current risk level:
1. Read latest row from `risk_state` table
2. If no row → RED (no data = worst case)
3. If row is > 5 minutes old → RED (RiskGuard may have crashed)
4. If DB error → RED
5. Otherwise → return stored level

### 1.5 `DATA_DEGRADED` semantics

DATA_DEGRADED is a 5th level between GREEN and YELLOW in the ordinal.
It means: "we cannot compute a reliable loss boundary, so act with
YELLOW-equivalent safety without declaring an actual loss breach."

Concrete trigger: trailing loss reference row is missing, stale, or
inconsistent. This is distinct from YELLOW (which indicates an actual
measured concern).

In `tick_with_portfolio()` (degraded entry point), if
`portfolio.portfolio_loader_degraded=True`, the brier input level is
set to DATA_DEGRADED instead of GREEN.

### 1.6 Force exit review

When `daily_loss_level == RED`, `force_exit_review = 1` is written to the
`risk_state` row. Cycle runner reads this via `get_force_exit_review()` to
block new entries. Fail-closed: `True` on any error.

**Key files**: `src/riskguard/risk_level.py`, `src/riskguard/riskguard.py`

---

## 2. Strategy Governance

### 2.1 Per-strategy risk actions

RiskGuard emits durable strategy gate actions to the `risk_actions` table:

```sql
INSERT INTO risk_actions (
    action_id,        -- "riskguard:gate:{strategy_key}"
    strategy_key,
    action_type,      -- "gate"
    value,            -- "true"
    issued_at,
    effective_until,  -- NULL while active
    reason,           -- pipe-joined reason codes
    source,           -- "riskguard"
    precedence,       -- 50
    status            -- "active" / "expired"
)
```

Gate reasons are accumulated from two sources:
1. **Edge compression**: from `strategy_tracker.edge_compression_check()` →
   `"edge_compression"`
2. **Execution decay**: per-strategy fill rate < 0.3 with ≥10 observed →
   `"execution_decay(fill_rate={rate}, observed={n})"`

When a strategy no longer has active gate reasons, its action is set to
`status="expired"` with `effective_until=now`. This is the un-gate path.

### 2.2 Portfolio truth loading for RiskGuard

RiskGuard uses a dual-source portfolio truth model:

1. **Canonical source** (`position_current` table): authoritative positions
   via `query_portfolio_loader_view()` → `choose_portfolio_truth_source()`
   must return `"canonical_db"` or RiskGuard raises `RuntimeError`
2. **Capital metadata** (JSON portfolio): bankroll, baselines, ignored_tokens

Consistency lock: if canonical position count ≠ metadata position count,
the result carries `consistency_lock="mismatched"`. This is logged at ERROR
level but does not halt RiskGuard (it still computes risk for the canonical
positions).

### 2.3 Strategy health tracking

`refresh_strategy_health()` writes per-strategy rows to `strategy_health`:
- 30-day realized PnL
- Unrealized PnL from open positions
- Fill rates
- Edge compression status

`query_strategy_health_snapshot()` reads these for the risk tick's
total PnL computation: `total_pnl = Σ(realized_pnl_30d) + Σ(unrealized_pnl)`.

Fallback: if `outcome_fact` or `strategy_health` tables are missing or empty,
PnL falls back to `portfolio.recent_exits` sum.

### 2.4 Settlement summary dedup

`_strategy_settlement_summary()` aggregates settlement rows into per-strategy
metrics. It includes an explicit trade-ID dedup layer because historical
settlement rows can contain duplicates from multiple upstream sources
(canonical + legacy position_events, duplicate decision_log batches). Without
dedup, a strategy could show 19 settlements when the truth is 6 unique trades.

**Key file**: `src/riskguard/riskguard.py`

---

## 3. Kelly Sizing

### 3.1 Base formula (`kelly_size()`)

```python
f_star = (p_posterior - price_value) / (1.0 - price_value)
raw_proposal = f_star * kelly_mult * bankroll
```

Pre-conditions enforced before computation:
- `entry_price.assert_kelly_safe()` — typed ExecutionPrice boundary (INV-21)
- `price_value ∈ (0, 1)` — zero or negative price → return 0
- `bankroll > 0`
- `p_posterior ∈ [0, 1]`
- `p_posterior > price_value` — no positive edge → return 0

Per-trade cap parameters are not part of the current Kelly contract. Entry
discipline is enforced after Kelly by wallet-bankroll availability, RiskGuard,
posture, executable-price, and max-exposure gates.

### 3.2 Dynamic multiplier (`dynamic_kelly_mult()`)

Starting from `base=0.25`, five factors reduce multiplicatively:

| Factor | Condition | Multiplier |
|--------|-----------|------------|
| CI width (moderate) | `ci_width > 0.10` | × 0.7 |
| CI width (wide) | `ci_width > 0.15` | × 0.5 (cumulative with above: × 0.35) |
| Lead time (long) | `lead_days ≥ 5` | × 0.6 |
| Lead time (medium) | `lead_days ≥ 3 AND < 5` | × 0.8 |
| Win rate (poor) | `rolling_win_rate_20 < 0.40` | × 0.5 |
| Win rate (weak) | `rolling_win_rate_20 < 0.45 AND ≥ 0.40` | × 0.7 |
| Portfolio heat | `portfolio_heat > 0.40` | × `max(0.1, 1.0 - portfolio_heat)` |
| Drawdown | `drawdown_pct > 0 AND max_drawdown > 0` | × `max(0.0, 1.0 - drawdown_pct / max_drawdown)` |

**Cascade floor rule**: The result must be a finite positive number. Two
fail-closed checks:
- NaN → `ValueError` (NaN ≠ NaN check)
- `≤ 0.0` → `ValueError` ("refusing to fabricate a floor value")

This is a design choice: when all gates trigger simultaneously, the system
raises rather than silently sizing at an artificial minimum. The caller must
handle the exception (typically by skipping the trade).

### 3.3 Worked example from code

```
Inputs: p_posterior=0.65, entry_price=0.50 (VWMP, fee_adjusted),
        bankroll=$10,000, base=0.25

f* = (0.65 - 0.50) / (1 - 0.50) = 0.30

dynamic_kelly_mult(base=0.25, ci_width=0.12, lead_days=4,
                   rolling_win_rate_20=0.52, portfolio_heat=0.15,
                   drawdown_pct=0.05, max_drawdown=0.20):
  ci_width=0.12 > 0.10        → × 0.7  = 0.175
  ci_width=0.12 < 0.15        → no additional
  lead_days=4 ≥ 3 and < 5     → × 0.8  = 0.140
  win_rate=0.52 ≥ 0.45        → × 1.0  = 0.140
  heat=0.15 ≤ 0.40            → × 1.0  = 0.140
  drawdown=0.05/0.20 = 0.25   → × 0.75 = 0.105

Position = 0.30 × 0.105 × $10,000 = $315

Downstream exposure gates may reject or throttle the entry, but `kelly_size()`
does not apply a per-trade hard cap.
```

### 3.4 Provenance guard

`dynamic_kelly_mult()` calls `require_provenance("kelly_mult")` before
any computation. This checks the provenance registry
(`provenance_registry.yaml`) to verify that `kelly_mult` has a declared
origin and audit trail. If provenance is not registered, the call fails.

**Key file**: `src/strategy/kelly.py`

---

## 4. RiskGuard Process Architecture

### 4.1 Separate process

RiskGuard runs as an independent process with a 60-second tick loop
(`__main__` block). It:
- Has its own DB connections (reads from zeus.db, writes to risk_state.db)
- Is independent of the trading cycle (cycle_runner.py)
- Can crash without killing the trading loop
- The trading loop can read stale risk state (handled by the 5-minute
  staleness check in `get_current_level()`)

### 4.2 Dual-DB architecture

| Database | Contents | Who writes |
|----------|----------|------------|
| `zeus.db` (trade) | position_events, position_current, risk_actions, chronicle, trade_decisions | cycle_runner, harvester, riskguard (actions only) |
| `risk_state.db` | risk_state (level history) | riskguard only |
| `zeus.db` (shared) | ensemble_snapshots, calibration_pairs, platt_models, settlements | harvester, calibration, data ingest |

RiskGuard writes `risk_actions` (strategy gates) to zeus.db but reads risk
history from risk_state.db. Both connections are committed within `tick()`.

### 4.3 Alert emission

After computing risk level, RiskGuard emits Discord alerts:

| Transition | Alert |
|------------|-------|
| Any → RED | `alert_halt(failed_rules)` with per-input detail |
| RED → GREEN | `alert_resume("rules cleared")` |
| Any → YELLOW | Per-input `alert_warning()` for each YELLOW contributor |
| Any → DATA_DEGRADED | `alert_warning()` with "DATA_DEGRADED: Missing trailing loss baseline" |

Alert failures are caught and logged — they do not affect risk level
computation or persistence.

### 4.4 Strategy settlement summary

The risk tick produces a detailed JSON `details_json` blob written to
risk_state. Key fields for downstream consumers:

- `probability_directional_accuracy`: the probability-side hit rate (did
  p > 0.5 match the outcome?). Distinct from per-strategy
  `trade_profitability_rate` (wins/count). These were previously both called
  `accuracy`, causing confusion in LLM reports.
- `strategy_settlement_summary`: per-strategy `{count, pnl, wins, trade_profitability_rate}`
- `entry_execution_summary`: per-strategy `{attempted, filled, rejected, fill_rate}`
- `recommended_strategy_gates` / `recommended_strategy_gate_reasons`:
  strategies that should be gated and why

---

## 5. Cross-References

- Math pipeline: `docs/reference/zeus_math_spec.md`
- Domain model: `docs/reference/zeus_domain_model.md`
- Architecture law: `docs/authority/zeus_current_architecture.md`
- Execution/lifecycle: `docs/reference/zeus_execution_lifecycle_reference.md`
- Source AGENTS:
  - `src/riskguard/AGENTS.md` — risk domain rules
  - `src/strategy/AGENTS.md` — strategy domain rules
  - `src/calibration/AGENTS.md` — calibration domain rules
