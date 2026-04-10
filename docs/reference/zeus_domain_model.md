# Zeus Domain Model — "Zeus in 5 Minutes"

Zeus converts weather ensemble forecasts into calibrated trading probabilities on Polymarket.

## 1. The probability chain

```
51 ENS members → per-member daily max → Monte Carlo (sensor noise + rounding) → P_raw
P_raw → Extended Platt (A·logit + B·lead_days + C) → P_cal
P_cal + P_market → α-weighted fusion → P_posterior
P_posterior - P_market → Edge (with double-bootstrap CI)
Edges → BH FDR filter (220 hypotheses) → Selected edges
Selected → Fractional Kelly (dynamic mult) → Position size
```

**Worked example**: Chicago, 3 days out. 51 ensemble members predict daily max temperatures. For each member, add ASOS sensor noise (σ ≈ 0.2–0.5°F), round to integer (WU display), repeat 10,000× → P_raw per bin. Platt calibrates: `P_cal = sigmoid(A·logit(P_raw) + B·3 + C)`. Fuse with market price via α-weighted blend → `P_posterior`. Edge = `P_posterior - P_market`. Bootstrap CI on that edge (3 uncertainty sources). If BH-significant across all 220 hypotheses → Kelly sizes it.

## 2. Why settlement is integer

Polymarket weather markets settle on Weather Underground's reported daily high. WU reports **whole degrees** (°F or °C). A real temperature of 74.45°F → sensor reads 74.2°F → METAR rounds → WU displays 74°F.

This means probability mass concentrates at bin boundaries in ways mean-based models miss entirely. Zeus's Monte Carlo explicitly simulates the full chain: `atmosphere → NWP member → ASOS sensor (σ ≈ 0.2–0.5°F) → METAR rounding → WU integer`.

**Enforcement**: `SettlementSemantics.assert_settlement_value()` gates every DB write. If you bypass it, you corrupt the truth surface.

**Key file**: `src/contracts/settlement_semantics.py`

## 3. Calibration with temporal decay

Raw ensemble probabilities are systematically biased — overconfident at long lead times, underconfident near settlement.

Extended Platt: `P_cal = sigmoid(A·logit(P_raw) + B·lead_days + C)`

`lead_days` is an **input feature**, not a bucket dimension. This triples positive samples per training bucket (45→135) vs bucketing by lead time. Without the `B·lead_days` term, Zeus overtrades stale forecasts.

**Maturity gates**: n < 15 → use P_raw directly. 15–50 → strong regularization (C=0.1). 50+ → standard fit.

**Key file**: `src/calibration/platt.py`

## 4. Model-market fusion (α-weighted posterior)

Zeus uses α-weighted linear fusion, not classical Bayesian conjugate updating:

```
P_posterior = α × P_cal + (1 - α) × P_market
```

**α** (how much to trust the model vs the market) is dynamically computed per decision:

| Factor | Effect on α | Why |
|--------|-------------|-----|
| Calibration maturity (level 1–4) | Base α (0.30–0.70) | More training data → trust model more |
| ENS spread tight (< threshold) | α += 0.10 | Ensemble agreement → model signal stronger |
| ENS spread wide (> threshold) | α -= 0.15 | Ensemble disagreement → model signal weaker |
| Lead days < 2 | α += 0.05 | Short lead → forecast skill high |
| Lead days ≥ 5 | α -= 0.05 | Long lead → forecast skill decays |

α is clamped to [0.20, 0.85] — never fully trust either source.

**Market price** uses VWMP (Volume-Weighted Micro-Price), not raw mid-price. VWMP: `(bid × ask_size + ask × bid_size) / total_size`. This accounts for order book imbalance.

**Key file**: `src/strategy/market_fusion.py`

## 5. Double-bootstrap confidence intervals

Edge uncertainty comes from **three independent sources**, not one:

1. **Ensemble sampling uncertainty** — which 51 members the NWP produced (bootstrap over members)
2. **Instrument noise** — ASOS sensor measurement error (Monte Carlo with σ ≈ 0.2–0.5°F)
3. **Calibration parameter uncertainty** — Platt model coefficients are estimated, not known

The double-bootstrap procedure:
1. Resample ensemble members with replacement → new P_raw
2. Resample Monte Carlo noise realizations → new rounded settlement counts
3. Propagate through calibration → new P_cal → new P_posterior → new edge
4. Repeat 1000× → edge distribution → CI width

P-values come from bootstrap empirical distribution: `p = mean(bootstrap_edges ≤ 0)`. **Never** from normal approximation or analytic formulas — the distributions are non-Gaussian near bin boundaries.

**Key file**: `src/strategy/market_analysis.py`

## 6. FDR filtering

Each cycle evaluates ~220 simultaneous hypotheses (10 cities × 11 bins × 2 directions). At α=0.10 without FDR control, random chance produces ~22 spurious "edges."

Benjamini-Hochberg controls the **false discovery rate** across all hypotheses: sort by p-value ascending, find largest k where `p_value[k] ≤ α × k / m`. Only edges 1..k survive.

P-values come from bootstrap: `p = mean(bootstrap_edges ≤ 0)`. Never from approximation formulas.

**Key file**: `src/strategy/fdr_filter.py`

## 7. Kelly sizing with dynamic cascade

### Base formula
```
f* = (P_posterior - entry_price) / (1 - entry_price)
Position size = f* × kelly_mult × bankroll
```

### Dynamic multiplier cascade
The default `kelly_mult = 0.25` (quarter-Kelly) is reduced multiplicatively by five risk factors:

**Worked example** — P_posterior = 0.65, entry_price = 0.50, bankroll = $10,000:
```
f* = (0.65 - 0.50) / (1 - 0.50) = 0.30

Dynamic mult cascade (base = 0.25):
  CI width = 0.12 (> 0.10)    → × 0.70  = 0.175
  Lead days = 4 (≥ 3)         → × 0.80  = 0.140
  Win rate = 0.52 (OK)        → × 1.00  = 0.140
  Portfolio heat = 0.15 (OK)  → × 1.00  = 0.140
  Drawdown = 5% / 20% max     → × 0.75  = 0.105

Final: f* × mult × bankroll = 0.30 × 0.105 × $10,000 = $315
```

**Cascade floor**: multiplier is bounded to [0.001, 1.0]. NaN → 0.001. This ensures positions are never zero-sized through floating-point collapse (INV-05: risk levels must change behavior, including at the sizing layer).

**Key file**: `src/strategy/kelly.py`

## 8. Truth hierarchy and reconciliation

```
Chain (Polymarket CLOB) > Chronicler (event log) > Portfolio (local cache)
```

Three reconciliation rules (run every cycle before trading):
1. **Local + chain match** → SYNCED (no action)
2. **Local exists, NOT on chain** → VOID immediately (local state is a hallucination)
3. **Chain exists, NOT local** → QUARANTINE 48h (unknown asset, forced exit eval)

Paper mode skips reconciliation. Live mode: mandatory.

**Key file**: `src/state/chain_reconciliation.py`

## 9. Lifecycle state machine

9 states in `LifecyclePhase` enum. Legal transitions enforced by `LEGAL_LIFECYCLE_FOLDS`.

```
pending_entry → active → day0_window → pending_exit → economically_closed → settled
                                    ↗ (can also go directly from active)
Terminal states: voided, quarantined, admin_closed
```

Critical distinctions:
- **Exit ≠ close**: `EXIT_INTENT` is a lifecycle event; economic closure is separate
- **Settlement ≠ exit**: Market settlement and position exit are separate lifecycle events
- Only the lifecycle manager may transition state (INV-01)
- Only `LifecyclePhase` enum values may be used (INV-08)

### Entry/exit lifecycle worked example

```
1. Evaluator finds BH-significant edge on Chicago 75°F+ bin
   → POSITION_OPEN_INTENT (phase: pending_entry)

2. Executor posts BUY order to Polymarket CLOB
   → ENTRY_ORDER_POSTED (phase: pending_entry)

3. Order fills at $0.52/share
   → ENTRY_ORDER_FILLED (phase: active)

4. [3 days pass, monitor runs each cycle]
   → MONITOR_REFRESHED events (phase: active)

5. Settlement day arrives
   → phase transitions to day0_window (special monitoring rules)

6. Monitor detects edge has reversed, signals exit
   → EXIT_INTENT (phase: still day0_window until exit order)

7. Executor posts SELL order
   → EXIT_ORDER_POSTED (phase: pending_exit)

8. Sell order fills at $0.71/share
   → EXIT_ORDER_FILLED (phase: economically_closed)

9. Market settles: WU reports 76°F (bin was 75°F+, outcome = YES)
   → SETTLED (phase: settled, P&L = $0.19/share final)
```

**Key file**: `src/state/lifecycle_manager.py`

## 10. Risk levels change behavior (INV-05)

| Level | Behavior |
|-------|----------|
| GREEN | Normal operation |
| YELLOW | No new entries, continue monitoring |
| ORANGE | No new entries, exit at favorable prices |
| RED | Cancel all pending, exit all immediately |

Advisory-only risk is explicitly forbidden. If a risk level doesn't change behavior, it violates INV-05.

Overall level = max of all individual levels. Fail-closed: any computation error → RED.

**Key file**: `src/riskguard/risk_level.py`
