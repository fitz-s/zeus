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

### Discrete settlement support (mandatory architecture concept)

Discrete settlement support is a **semantic atom**, not an implementation detail. Any work touching uncertainty, calibration, hit-rate analysis, edge math, pricing, or settlement interpretation must treat settlement support as authority before reasoning from continuous physical intuition.

**Three required concepts:**

| Concept | Definition |
|---------|------------|
| `bin_contract_kind` | `point` (single integer), `finite_range` (fixed integer set), or `open_shoulder` (unbounded) |
| `bin_settlement_cardinality` | Number of discrete settled values that resolve the bin to YES |
| `settlement_support_geometry` | The exact discrete support implied by the venue contract |

**Current Zeus market law:**

1. **Fahrenheit non-shoulder bins** are `finite_range` with cardinality `2` — e.g., `50-51°F` resolves on `{50, 51}`
2. **Celsius non-shoulder bins** are `point` with cardinality `1` — e.g., `10°C` resolves on `{10}`
3. **Shoulder bins** are `open_shoulder` — they are NOT ordinary finite bins and must not be reasoned about as symmetric bounded ranges

**Forbidden shortcuts** — no work may infer bin semantics from:
1. Label punctuation alone
2. Informal intuition about "1 degree" vs "2 degree" width
3. Continuous interval width without checking discrete settlement support
4. Continuous-model variance collapse without justifying discrete contract support

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

## 11. Four independent strategies

Zeus's edges fall into four categories with fundamentally different risk/alpha profiles. They should be tracked independently because their alpha decay rates differ — portfolio-level P&L masks which strategies are working and which are being competed away.

### Strategy A: Settlement Capture (observation-based, most durable)

- **Edge source**: Observed fact — temperature has already crossed a bin threshold post-peak
- **Risk**: Near-zero (post-peak + already crossed ≈ near-certain)
- **Requires**: Observation speed (frequent WU/ASOS polling, ideally every 3–5 min in Day0 window)
- **Does NOT require**: ENS forecast, Platt calibration, bootstrap CI
- **Alpha decay**: Very slow — this is not a predictive edge, it's an observation speed advantage
- **Key insight**: Only strategy where increasing operational frequency directly increases edge

### Strategy B: Shoulder Bin Sell (structural, durable)

- **Edge source**: Retail cognitive bias (prospect theory, lottery effect → shoulder bins overpriced)
- **Risk**: Tail risk (extreme weather events cause large losses)
- **Requires**: Basic climatological probability estimates
- **Does NOT require**: Precise ENS signals — a rough "this bin wins ~5% historically" suffices
- **Alpha decay**: Moderate — as more bots enter, shoulder overpricing narrows

### Strategy C: Center Bin Buy (predictive, moderate durability)

- **Edge source**: Model is more accurate than market at estimating the most likely temperature bin
- **Risk**: Medium (model error → loss = entry price)
- **Requires**: Full signal chain (ENS → Platt → bootstrap → FDR)
- **Alpha decay**: Fastest — most easily competed away by other quantitative participants

### Strategy D: Opening Inertia (temporal, unverified)

- **Edge source**: New market mispricing (first liquidity provider's anchoring effect)
- **Risk**: Medium-high (opening price may be coincidentally correct)
- **Requires**: Market scanning + model signal
- **Alpha decay**: Fastest — as bots scan new market opens, the window shrinks

### Edge decay monitoring

Each strategy's average edge magnitude should be tracked over 30/60/90-day windows. When a strategy's edge trend is negative and sustained for 30+ days, the correct response is to reduce capital allocation to that strategy — not to refine the model. If all four strategies show compressing edges, reduce total position sizing until the trend reverses.

Per-strategy tracking enables: independent win rate, cumulative P&L, edge trend, fill rate, and holding period. RiskGuard monitoring (Brier score, drawdown) should eventually be per-strategy so that Strategy C's deterioration doesn't halt Strategy A.

## 12. Translation loss law

Natural language → code translation has systematic, irreducible information loss. This is not a solvable problem — it is a physical property of attention allocation across context boundaries.

**Survival rates across sessions:**
- Functions, types, tests: **100%** — they are executable and self-enforcing
- Design philosophy, architecture rationale: **~20%** — they require understanding, which decays

**Consequence**: Every session should encode insights as code structure (types, tests, contracts), not documentation. `Bin.unit`, `SettlementSemantics.for_city()`, and `test_celsius_cities_get_celsius_semantics()` are executable forms of design intent — they enforce correctness without being understood. Documentation that explains *why* is valuable but fragile; code that *prevents* errors is durable.

**Relationship tests before implementation**: Before writing a new module, write tests for its relationships with existing modules — not "does this function return the right value" but "when this function's output flows into the next function, what properties must hold?" If you cannot express a cross-module relationship as a pytest assertion, you do not yet understand that relationship.

## 13. Structural decisions methodology

When facing N surface-level problems, do not write N patches. Find K structural decisions where K << N.

**Examples from Zeus:**
- 22 chain-safety mechanisms = 5 structural decisions
- 10 paper/live isolation mechanisms = 3 structural decisions
- The `state_path()` function = 1 structural decision that covers all per-process file isolation

The test for a structural decision: does it eliminate a *class* of problems, or just one instance? If one instance, it is a patch. If a class, it is a structural decision.

## 14. Data provenance model

Zeus classifies all persistent data into three layers with distinct isolation semantics:

| Layer | What | Isolation rule | Examples |
|-------|------|----------------|----------|
| **World data** | External facts independent of Zeus's trading decisions | Shared across all modes, no mode tag | ENS forecasts, calibration pairs, settlement observations |
| **Decision data** | Records of Zeus's choices and their outcomes | Shared + `env` column discriminator | `trade_decisions`, `chronicle`, `position_events` |
| **Process state** | Mutable runtime state of a running instance | Physically isolated via `state_path()` | `positions-{mode}.json`, `strategy_tracker-{mode}.json`, `risk_state` |

**Why this matters**: World data can be safely shared between paper and live modes because it is objective. Decision data must be tagged so paper decisions never contaminate live analytics. Process state must be physically separate because two concurrent instances writing the same file corrupt both.

## 15. Code-data mismatch: the DST case study

Code correctness does not guarantee system correctness. This is not philosophy — it is a concrete bug class that has occurred in Zeus.

**The setup**: Zeus's `diurnal_curves` table aggregates hourly temperature data by `obs_hour` for Day0 peak prediction. The runtime `get_current_local_hour()` correctly uses `ZoneInfo` for DST-aware local time. The ETL pipeline that populated the table also used timezone-aware code.

**The bug**: The inherited historical data stored `local_hour` values derived from UTC, not true local time. Evidence: London 2025-03-30 (spring clock change, hour 1 does not exist in BST) had all 24 hours present in the table, including hour 1. If the data were truly DST-aware, hour 1 would be missing.

**The consequence**: During the entire BST summer period, the runtime queried `diurnal_curves` with hour 14 (correct 2:00 PM BST) but retrieved data aggregated from UTC hour 14 (actually 3:00 PM BST). Systematic 1-hour offset for all DST cities (London, Paris, New York, Chicago). Non-DST cities (Tokyo, Seoul, Shanghai) were unaffected.

**The lesson**: Code review cannot catch this — both the ETL code and the runtime code were individually correct. The failure was at the *semantic boundary* between inherited data and new code. This is why Venus exists: not to check code correctness, but to verify that code assumptions match data reality.
