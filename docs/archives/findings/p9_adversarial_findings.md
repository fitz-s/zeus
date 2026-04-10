# P9 Adversarial Findings

Version: 2026-04-05 (updated after Part B)
Agent: p9-adversarial
Status: **Part A + Part B Complete**

---

## 1. Reality Fact-Check Results

### 1.1 Fee Structure — CRITICAL FINDING

**Spec assumption:** Flat 5% taker fee (see `execution_price.py:6`, FeeGuard in spec §P10.6).

**Reality (verified via Polymarket docs):**

The fee is **NOT a flat percentage**. Polymarket uses a **price-dependent formula**:

```
fee = C × feeRate × p × (1 - p)
```

Where:
- `C` = shares traded
- `p` = share price
- `feeRate` = category-dependent rate

**Fee rates by market category:**
| Category | Taker feeRate | Maker fee | Maker rebate |
|---|---|---|---|
| Crypto | 0.072 | 0 | 20% |
| Sports | 0.03 | 0 | 25% |
| Finance/Politics/Mentions/Tech | 0.04 | 0 | 25% |
| **Economics/Culture/Weather/Other** | **0.05** | **0** | **25%** |
| Geopolitics | 0 | 0 | — |

**Weather markets use feeRate = 0.05**, but the actual fee paid is `0.05 × p × (1 - p)`, NOT a flat 5%.

**Impact analysis:**
- At p=0.50: fee = 0.05 × 0.5 × 0.5 = 0.0125 per share (1.25%, NOT 5%)
- At p=0.10: fee = 0.05 × 0.1 × 0.9 = 0.0045 per share (0.45%)
- At p=0.90: fee = 0.05 × 0.9 × 0.1 = 0.0045 per share (0.45%)
- At p=0.25: fee = 0.05 × 0.25 × 0.75 = 0.009375 per share (0.9375%)

**For 100 shares in weather markets:**
- At p=0.50: $1.25 (peak fee)
- At p=0.10 or p=0.90: $0.45

This means:
1. **The spec's `ASSUMED_TAKER_FEE = 0.05` (FeeGuard) is WRONG as a flat deduction.** The 5% is the feeRate coefficient, not the actual fee percentage. Actual fee is 0–1.25% depending on price.
2. **`min_gross_edge_for_trade = 0.05 + 0.02 = 0.07` (7% floor) is MASSIVELY conservative.** Real minimum edge needed is only ~1.25% at worst (p=0.50), and much less at tail prices where Zeus trades most.
3. **Zeus is rejecting profitable trades.** A buy_no edge of 3% at p=0.90 (where fee is only 0.45%) would be rejected by the 7% floor, losing $2.55/share in expected profit.
4. The fee formula is quadratic (max at p=0.50, approaching 0 at extremes). Zeus's primary edge is in tail bins (buy_no at high p_market or buy_yes at low p_market) — **exactly where fees are lowest**.
5. Fees are collected differently: **in shares on buy orders, in USDC on sell orders**.

**Additional fee details:**
- Smallest fee is 0.00001 USDC (fees rounded to 5 decimals, can round to 0)
- Fee-enabled markets have `feesEnabled: true` flag
- REST/manual signing requires `feeRateBps` in signed order payload (example: "1000")
- Fee rate can be queried per token: `GET https://clob.polymarket.com/fee-rate?token_id={token_id}`
- Maker rebate: 25% of taker fees redistributed to makers

**Verdict:** The fee model in the FINAL spec is the single most impactful reality gap. It's not just a wrong number — it's a wrong formula shape. Round-trip cost is NOT 10% flat; it's `2 × feeRate × p × (1-p)`, maxing at 2.5% at p=0.50 and approaching 0% at extremes.

### 1.2 Tick Size

**Spec assumption:** Fixed tick size, to be verified (§P10.4 `TICK_SIZE_STANDARD`).

**Reality:**
- Default tick size: **0.01** (confirmed in Polymarket docs examples)
- Tick size is **per-market configurable** — fetched from the markets API
- DELTA-13 in `zeus_runtime_delta_ledger.md` already flags: "tick_size is hardcoded to 0.01, but Polymarket dynamically changes tick_size for prices >0.96 or <0.04"
- The py-clob-client README shows tick_size as "0.001" in one example, indicating markets CAN have different tick sizes

**Verdict:** Mostly correct. The 0.01 default is right for most prices, but extreme prices may use 0.001. Already known as DELTA-13.

### 1.3 Settlement Sources

**Spec assumption:** Weather Underground (WU) for all cities.

**Reality (from codebase + docs):**
- All 16 cities in `config/cities.json` use WU station URLs as settlement sources
- NYC uses KLGA (LaGuardia), Chicago uses KORD, etc.
- Settlement is based on WU's displayed integer temperature, NOT NWS official
- **DELTA-15 and DELTA-21 flag:** some markets may use NOAA, but Zeus currently handles only WU
- NYC precipitation markets use NOAA, not WU

**Verdict:** Correct for temperature markets. The spec's §P10 contracts correctly identify this as needing verification per market.

### 1.4 Order Types

**Spec assumption:** "limit_only" (per `state/assumptions.json:33`).

**Reality:**
- Polymarket CLOB supports at minimum: **GTC** (Good-Till-Cancelled) and **FOK** (Fill-or-Kill)
- The py-clob-client Python SDK exposes `OrderType.FOK` and `OrderType.GTC`
- The TypeScript client shows `limit_order()` builder
- Zeus's assumption of "limit_only" is essentially correct — all orders are limit orders, with GTC being the default time-in-force
- FOK is available for market-like execution (limit at aggressive price + FOK = pseudo-market order)
- No evidence of native market orders, IOC, or GTD

**Verdict:** Zeus's "limit_only" assumption is accurate. FOK availability is noted but not critical.

### 1.5 Minimum Order Size

**Spec assumption:** `min_order_usd: 1.0` (in config/settings.json:85). DELTA-14 flags: "min_order_size (5 shares) is not enforced in sizing logic."

**Reality:**
- Polymarket docs do not explicitly state a minimum order size
- The reality crisis doc (`TOP_PRIORITY`) references 5 shares as min_order_size_shares
- Zeus's `$1.0` minimum in settings is an internal constraint, not necessarily matching Polymarket's
- The `feeRateBps` in the signed payload suggests orders below certain sizes may have zero fees

**Verdict:** Needs live verification. The $1.0 internal minimum may be too low or too high vs Polymarket's actual floor. DELTA-14 remains open.

### 1.6 UMA Oracle Resolution Timeline

**Spec assumption:** Advisory contract `RESOLUTION_TIMELINE` (§P10.7).

**Reality (from UMA docs):**
- Assertions use a **pre-defined liveness period** (duration not universally fixed)
- If undisputed: settlement is immediate after liveness window expires
- If disputed: submitted to UMA's DVM (Decentralized Verification Mechanism)
- **DVM voting period: 48–96 hours**
- Disputes resolved "within a few days" via tokenholder votes
- DELTA-17 flags: "UMA oracle dispute period (2 hours) not modeled; assumes clean resolution"

**Verdict:** The 2-hour dispute period in DELTA-17 appears to be the liveness window, not the full dispute resolution time. If disputed, resolution takes **48–96 hours** via DVM. For weather markets (objective, low-controversy), disputes are rare but not impossible.

### 1.7 API Rate Limits

**Spec assumption:** `RATE_LIMIT_BEHAVIOR` contract (§P10.7, degraded criticality).

**Reality:**
- Polymarket docs do not explicitly publish rate limits
- DELTA-19 flags: "rate limiting handled only for Open-Meteo; Polymarket CLOB/Gamma/Data APIs have no rate limit handling"
- The codebase handles Open-Meteo rate limits (`RATE_LIMIT_COOLDOWN_SECONDS = 5 * 60`)
- No Polymarket-specific rate limit handling exists

**Verdict:** Unknown. Must be empirically tested. The degraded criticality seems appropriate — rate limiting won't lose money, just slow execution.

---

## 2. Undiscovered Issues (Spec Missed)

### 2.1 CRITICAL: Fee Formula Shape Invalidates Kelly Sizing

The fee is NOT a constant to deduct — it's a function of price. This means:
- Kelly's `f* = (p_posterior - entry_price) / (1 - entry_price)` where entry_price includes fee must use `entry_price + feeRate × p × (1 - p)`, not `entry_price + 0.05`
- The fee-adjusted entry price is **non-linear in p**, which changes the Kelly optimization landscape
- The current `FeeGuard.adjust_edge()` subtracts a flat 0.05 from gross edge, but the real fee at tail prices (where Zeus trades) is 0.45–0.9%, not 5%
- **Zeus is leaving 3–4.5% edge on the table at every tail trade**

### 2.2 CRITICAL: feeRateBps in Signed Orders

The docs show that REST/manual signing requires `feeRateBps` in the signed order payload (example: "1000" = 10%). If this is not set correctly in the order, the order may be rejected or executed with wrong fee expectations. This is an execution-layer reality gap not covered in any existing contract.

### 2.3 MEDIUM: Fee Collection Asymmetry (Buy vs Sell)

Fees are "collected in shares on buy orders and USDC on sell orders." This means:
- On buy: you receive fewer shares than expected (fee taken in-kind)
- On sell: you receive fewer USDC than expected (fee taken in cash)
- This asymmetry affects P&L attribution and position tracking differently than a uniform deduction
- Zeus's P&L model assumes symmetric fee treatment

### 2.4 MEDIUM: Fee-Free Market Existence

Some Polymarket markets are fee-free (geopolitics). Weather markets have fees. But the `feesEnabled` flag means fee status is per-market, not per-category. A weather market deployed before the fee activation date would be fee-free. Zeus has no mechanism to query this per-market.

### 2.5 LOW: Maker Rebate Program

25% of weather taker fees are redistributed to makers. If Zeus posts limit orders that don't cross the spread, it is a MAKER and pays ZERO fees while earning rebates. The spec assumes Zeus always pays taker fees, but a well-designed execution strategy could make Zeus a maker on most trades.

---

## 3. INV-12 Violations Found (Full Part B Scan)

### 3.1 `src/strategy/market_analysis.py:141` — CONFIRMED VIOLATION (D3 canonical)

```python
entry_price=float(self.p_market[i]),
```

`BinEdge.entry_price` is set to `p_market[i]` — an implied probability, not VWMP+fee. This is the canonical D3 violation.

**Tracing the data flow:**
1. `evaluator.py:435` computes `p_market[idx] = vwmp(bid, ask, bid_sz, ask_sz)` — this IS a true VWMP from live CLOB data
2. This `p_market` array is passed to `MarketAnalysis.__init__()` at `evaluator.py:607`
3. `market_analysis.py:141` uses `self.p_market[i]` as `entry_price`

**Revised assessment:** The value IS a VWMP (computed at evaluator.py:435), not a raw Gamma probability. However:
- **No fee has been deducted.** The VWMP is the raw bid/ask-weighted price. The actual cost to trade includes the Polymarket fee `feeRate × p × (1 - p)`.
- The `vwmp` field on BinEdge (line 143) uses the same raw value — correct as a market price, but NOT an execution cost.

### 3.2 `src/engine/evaluator.py:789-793` — SEMANTIC LAUNDERING (CRITICAL)

```python
# edge.entry_price is VWMP with fee deducted, in probability space.
exec_price = ExecutionPrice(
    value=edge.entry_price,
    price_type="vwmp",
    fee_deducted=True,
    currency="probability_units",
)
```

**`fee_deducted=True` is FALSE metadata.** The value is raw VWMP with NO fee deduction applied anywhere in the pipeline. Tracing the full chain:
1. CLOB → `get_best_bid_ask()` → raw bid/ask
2. `evaluator.py:435` → `vwmp(bid, ask, bid_sz, ask_sz)` → VWMP (no fee applied)
3. → `MarketAnalysis.__init__` → `p_market` array
4. `market_analysis.py:141` → `entry_price = float(self.p_market[i])` (still no fee)
5. `evaluator.py:790` → `ExecutionPrice(value=edge.entry_price, fee_deducted=True)` — **LIE**

The contract type passes `assert_kelly_safe()` but the fee claim is fabricated. This is worse than no contract — it gives false confidence that fees are handled when they are not.

**Combined with the fee formula finding (§1.1):** Even if fee were deducted, the spec assumes flat 5% deduction. Reality is `feeRate × p × (1-p)` which is 0.45–1.25% depending on price.

### 3.3 `src/engine/evaluator.py:589` — AlphaDecision → bare float extraction

```python
alpha = alpha_decision.value  # Extract bare float for downstream arithmetic
```

The `AlphaDecision` wrapper is stripped immediately after the D1 target check. The bare `alpha` float then flows to `MarketAnalysis.__init__` (line 609) and `compute_posterior()` (via `MarketAnalysis._find_edges()`). At these downstream boundaries, there is no contract protection — only the evaluator entry point is guarded.

Same pattern at:
- `monitor_refresh.py:115`: `alpha = alpha_decision.value  # P9-D1: extract bare float for arithmetic`
- `replay.py:492`: `alpha = alpha_decision.value  # P9-D1: extract bare float for replay arithmetic`

**Severity: LOW.** The alpha is consumed within the same function that checked compatibility. The float extraction is pragmatic — numpy arrays need floats. But it means `MarketAnalysis` itself has no type safety on `alpha`.

### 3.4 `src/execution/exit_triggers.py:235` — bare float p_market at exit boundary

```python
current_market = float(current_edge_context.p_market[0])
```

In `_evaluate_buy_no_exit()`, `p_market[0]` is extracted as a bare float for the EV gate calculation. This crosses the execution→settlement boundary without typed wrapper. No `ExecutionPrice` wrapping here.

### 3.5 `src/engine/cycle_runtime.py:316` — bare float p_market at runtime boundary

```python
p_market = float(edge_ctx.p_market[0])
```

Same pattern — bare float extraction from EdgeContext for downstream use.

### 3.6 Summary of remaining INV-12 violations

| Location | Value | Missing wrapper | Severity |
|---|---|---|---|
| `evaluator.py:793` | entry_price VWMP | `fee_deducted=True` is false | **CRITICAL** |
| `exit_triggers.py:235` | p_market at EV gate | No ExecutionPrice | MEDIUM |
| `cycle_runtime.py:316` | p_market at runtime | No ExecutionPrice | MEDIUM |
| `evaluator.py:589` | alpha float extraction | No AlphaDecision downstream | LOW |
| `monitor_refresh.py:115` | alpha float extraction | No AlphaDecision downstream | LOW |

---

## 4. INV-13 Violations Found (Full Part B Scan)

### 4.1 Kelly cascade: ALL provenance checks bypassed

**In `kelly.py::dynamic_kelly_mult` (lines 67-74):**
All 8 `require_provenance()` calls pass `requires_provenance=False`:

```python
require_provenance("kelly.ci_width_threshold_moderate", requires_provenance=False)
require_provenance("kelly.ci_width_threshold_wide", requires_provenance=False)
require_provenance("kelly.lead_days_threshold_long", requires_provenance=False)
require_provenance("kelly.lead_days_threshold_medium", requires_provenance=False)
require_provenance("kelly.win_rate_threshold_poor", requires_provenance=False)
require_provenance("kelly.win_rate_threshold_weak", requires_provenance=False)
require_provenance("kelly.heat_threshold", requires_provenance=False)
require_provenance("kelly.max_drawdown_default", requires_provenance=False)
```

**This means `require_provenance()` returns `None` immediately without checking the registry at all.** The function signature allows `requires_provenance=False` as a legitimate escape hatch, but using it on EVERY call in the most critical cascade path defeats the entire INV-13 purpose.

### 4.2 Name mismatch: code names vs registry names

Even if `requires_provenance=True`, the lookup would FAIL because names don't match:

| Registry name (YAML) | Code reference (kelly.py) | Match? |
|---|---|---|
| `kelly_ci_width_threshold_narrow` | `kelly.ci_width_threshold_moderate` | NO — "narrow" vs "moderate", underscore vs dot |
| `kelly_ci_width_threshold_wide` | `kelly.ci_width_threshold_wide` | Prefix mismatch (underscore vs dot) |
| `kelly_ci_width_multiplier_narrow` | (not referenced by name) | Code uses bare literal 0.7 |
| `kelly_ci_width_multiplier_wide` | (not referenced by name) | Code uses bare literal 0.5 |
| `kelly_lead_days_threshold_long` | `kelly.lead_days_threshold_long` | Prefix mismatch |
| `kelly_lead_days_multiplier_long` | (not referenced by name) | Code uses bare literal 0.6 |
| `kelly_lead_days_multiplier_medium` | (not referenced by name) | Code uses bare literal 0.8 |
| `kelly_win_rate_threshold_low` | `kelly.win_rate_threshold_poor` | NO — "low" vs "poor" |
| `kelly_win_rate_multiplier_low` | (not referenced by name) | Code uses bare literal 0.5 |
| `kelly_portfolio_heat_threshold` | `kelly.heat_threshold` | NO — different name |

**Two structural failures:**
1. Naming convention mismatch (underscore `kelly_` in YAML vs dot `kelly.` in code)
2. Semantic label mismatch (narrow/moderate, low/poor, portfolio_heat_threshold/heat_threshold)

### 4.3 Unregistered constants: multiplier values in kelly.py

The **threshold** constants are (incorrectly) referenced by `require_provenance()`. But the **multiplier** values (0.7, 0.5, 0.6, 0.8, 0.5, 0.7) are bare literals in the code that are NOT checked by `require_provenance()` at all:

```python
# kelly.py lines 80-98
if ci_width > 0.10:
    m *= 0.7       # ← no require_provenance() call for THIS value
if ci_width > 0.15:
    m *= 0.5       # ← no require_provenance() call
if lead_days >= 5:
    m *= 0.6       # ← no require_provenance() call
elif lead_days >= 3:
    m *= 0.8       # ← no require_provenance() call
if rolling_win_rate_20 < 0.40:
    m *= 0.5       # ← no require_provenance() call
elif rolling_win_rate_20 < 0.45:
    m *= 0.7       # ← no require_provenance() call
```

The registry HAS entries for these (`kelly_ci_width_multiplier_narrow`, `kelly_ci_width_multiplier_wide`, etc.) but the code never calls `require_provenance()` for the multiplier values — only for the threshold values.

### 4.4 Executor constants: properly routed through settings, not checked by require_provenance()

`executor.py` now loads constants from `settings["execution"]` (lines 99, 113-126):
- `toxicity_budget`, `max_slippage`, `iceberg_threshold_usd`, `dynamic_limit_gap_pct`, `exit_base_offset`, `exit_max_slippage`

These are in the provenance registry YAML but are never checked by `require_provenance()` at runtime. The registry documents them but doesn't enforce them.

### 4.5 market_fusion.py constants: documented but not runtime-checked

`compute_alpha()` uses bare literals (+0.10, -0.15, -0.10, -0.20, +0.05, -0.05, +0.10, +0.05) for alpha adjustments. These are all in the registry YAML but none call `require_provenance()`.

### 4.6 Summary: INV-13 enforcement gap

| Category | In registry? | require_provenance() called? | Enforced at runtime? |
|---|---|---|---|
| Kelly thresholds | Yes (wrong names) | Yes (all False) | **NO** |
| Kelly multipliers | Yes (right names) | **NO** | **NO** |
| Alpha adjustments | Yes | **NO** | **NO** |
| Exit constants | Yes | **NO** | **NO** |
| Executor constants | Yes | **NO** | **NO** |

**The registry is a documentation artifact, not a runtime enforcement mechanism.** Zero constants are actually checked at runtime.

---

## 5. Cascade Bound Analysis

### 5.1 Worst-Case Kelly Cascade Product

From `kelly.py::dynamic_kelly_mult` (lines 76–103):

```
m = base (0.25)
    × 0.7    (ci_width > 0.10)
    × 0.5    (ci_width > 0.15, cumulative)
    × 0.6    (lead_days >= 5)
    × 0.5    (win_rate < 0.40)
    × 0.1    (portfolio_heat > 0.90 → max(0.1, 1-0.9) = 0.1)
    × 0.0    (drawdown at max → 1 - 1.0 = 0.0)
```

**Worst case: 0.25 × 0.7 × 0.5 × 0.6 × 0.5 × 0.1 × 0.0 = 0.0**

The cascade product can reach **exactly zero** via the drawdown pathway. The spec §P9.7 says it should be bounded in `[0.001, 1.0]`. The code's `max(0.0, 1.0 - drawdown_pct / max_drawdown)` allows zero.

**Without drawdown hitting max:**
0.25 × 0.7 × 0.5 × 0.6 × 0.5 × 0.1 = **0.0002625** — below the 0.001 floor claimed in the spec.

### 5.2 Risk Throttle Cascade (evaluator.py)

On top of `dynamic_kelly_mult`, `evaluator.py:753-759` applies:
```python
risk_throttle = 1.0
if regime: risk_throttle *= 0.5
if global_heat: risk_throttle *= 0.5
```

Combined with Kelly: `kelly_size(edge, price, bankroll, km * risk_throttle)` where `km = dynamic_kelly_mult(...)`.

**True worst case: 0.0002625 × 0.25 = 0.00006563** (if risk_throttle = 0.25, which requires both regime + heat throttles).

The spec's claimed bound of `[0.001, 1.0]` is **violated** by at least 15×.

---

## 6. Entry-Exit Symmetry Analysis (Part B — post p9-seams review)

### 6.1 DecisionEvidence IS constructed but NOT enforced

After the p9-seams update, `exit_triggers.py` now constructs `DecisionEvidence` objects:

**`_evaluate_buy_yes_exit()` (line 173):**
```python
_exit_evidence = DecisionEvidence(
    evidence_type="exit",
    statistical_method="consecutive_observation",
    sample_size=consecutive_confirmations(),  # = 2
    confidence_level=0.50,  # No formal CI
    fdr_corrected=False,
    consecutive_confirmations=consecutive_confirmations(),
)
```

**`_evaluate_buy_no_exit()` (line 247):** Same pattern.

**BUT:** The `_exit_evidence` is assigned to a local variable with a leading underscore (`_exit_evidence`), which is the Python convention for "unused." It is NEVER:
- Returned from the function
- Passed to `assert_symmetric_with()`
- Attached to the `ExitSignal` dataclass
- Logged or persisted

The `ExitSignal` dataclass (line 37-42) has no field for evidence. So even though the contract is constructed, it goes nowhere — it's dead code that documents the asymmetry without preventing it.

### 6.2 The TODO is explicit but deferred

Both paths contain:
```python
# P9-D4: Document exit evidence — makes entry-exit asymmetry visible.
# TODO: P9-D4 — upgrade exit evidence to bootstrap CI
```

This is honestly documented. The contract _makes the asymmetry visible in code comments_ but does not change runtime behavior.

### 6.3 Entry vs Exit evidence comparison (unchanged)

| Dimension | Entry | Exit | Asymmetry |
|---|---|---|---|
| Statistical method | bootstrap CI | consecutive observation | Qualitative gap |
| Sample size | n=200+ | n=2 | **100×** |
| FDR correction | BH α=0.10 | None | Binary gap |
| Confidence level | CI_lower > 0 (95%) | 50% heuristic | **~2× formal** |
| Consecutive confirmations | 1 (CI-based) | 2 (observation-based) | Different semantics |

### 6.4 Practical impact

The 2-cycle exit with no FDR means:
- A true edge with noise amplitude > threshold will produce 2 consecutive false negatives ~25% of the time (assuming independent 50% noise per cycle)
- Entry's bootstrap + FDR has a false discovery rate target of 10%
- **The system is ~2.5× more likely to falsely exit than to falsely enter**

This matches Rainstorm's 7/8 buy_no false-EDGE_REVERSAL pattern exactly. D4 is documented but not resolved.

### 6.5 Verdict

The p9-seams work honestly handled D4 as "document now, resolve later." The `DecisionEvidence` construction with `_exit_evidence` is a good marker for the future upgrade. The TODO is explicit. This is the right engineering decision IF there's a follow-up packet. But per the FINAL spec, there is no follow-up — this is the last architecture work.

---

## 7. Contract Semantic Gaps (Part B — full assessment)

### 7.1 ExecutionPrice — Semantic Laundering (D3) — CRITICAL

The contract type is structurally correct. `assert_kelly_safe()` checks all three dimensions (price_type, fee_deducted, currency). BUT:

- `evaluator.py:793` sets `fee_deducted=True` when no fee is deducted → **false positive safety check**
- The contract prevents _syntactic_ violations (bare float) but not _semantic_ violations (typed float with wrong provenance claims)
- **This is the most dangerous failure mode of typed contracts:** the system appears safe while systematically oversizing every position by the fee amount

**Does the contract actually prevent D3?** Partially. It prevents `implied_probability` from being used at Kelly (good). But it does NOT prevent fee-less VWMP from being labeled as fee-deducted.

### 7.2 AlphaDecision — PROPERLY ENFORCED (D1) — GOOD

`evaluator.py:588` calls `alpha_decision.assert_target_compatible("ev")` immediately after construction. This WORKS because:
- `compute_alpha()` returns `optimization_target="risk_cap"` (not "brier_score")
- `risk_cap` → `ev` is a compatible pairing (only brier→ev raises)
- The design decision to label alpha as "risk_cap" is the creative resolution: it sidesteps the D1 brier→ev conflict by declaring a third category

**However:** Is labeling the alpha as `risk_cap` honest? The alpha adjustments (spread, agreement, lead time) were all validated against Brier score (see registry entries). The overall blending is defensive (hence "risk_cap"), but individual adjustment weights are Brier-optimized. This is a gray area — technically correct but hiding the Brier dependency at the component level.

### 7.3 TailTreatment — PROPERLY INTEGRATED (D2) — GOOD

`compute_posterior()` accepts optional `TailTreatment` at line 203. When provided:
- `tail_treatment.scale_factor` replaces the hardcoded 0.5
- The `serves` field forces declaration of calibration_accuracy vs profit
- `warn_if_profit_unvalidated()` catches Brier-validated claims of profit optimization

**But:** Falls back to bare `0.5` when `tail_treatment is None` (line 236). Since callers can still omit the argument, the contract is optional, not mandatory.

### 7.4 VigTreatment — PROPERLY INTEGRATED (D5) — BEST

Most correctly implemented contract:
- `market_fusion.py:226` uses `vig_treatment.clean_prices` for blend input
- Factory `from_raw_prices()` computes vig factor and cleans at construction time
- `__post_init__` validation ensures vig removal happened before blend
- `applied_before_blend=True` requirement is checked structurally

**This is the gold standard** for how the other contracts should work.

### 7.5 DecisionEvidence — CONSTRUCTED BUT UNENFORCED (D4) — DOCUMENTED GAP

As detailed in §6: constructed as `_exit_evidence` (unused local), `assert_symmetric_with()` never called, `ExitSignal` has no evidence field. The asymmetry is documented via TODO comments but not resolved.

### 7.6 HoldValue — EXISTS BUT UNUSED (D6) — GAP

`hold_value.py` exists with proper fee+time cost declarations and the `with_costs()` factory method. Searching the codebase for actual usage:
- `exit_triggers.py` does NOT import or use `HoldValue`
- The EV gate calculations at `exit_triggers.py:161-164` and `exit_triggers.py:234-238` use bare arithmetic: `net_hold = shares * position.p_posterior` — exactly the D6 pattern of ignoring fee/time costs
- **D6 is unresolved.** The contract type exists but is never applied to the EV gate that needs it.

### 7.7 ProvenanceRegistry — EXCELLENT DOCUMENTATION, ZERO ENFORCEMENT

51 entries, all well-documented with data_basis, validated_at, replacement_criteria, and cascade_bound. The YAML file is a high-quality reference document.

**But as a runtime enforcement mechanism, it does nothing:**
- All `require_provenance()` calls in kelly.py use `requires_provenance=False`
- Alpha adjustments, exit constants, and executor constants have NO `require_provenance()` calls
- Name mismatches mean even enabling the checks would cause UnregisteredConstantError

### 7.8 Contract Scorecard

| Contract | D gap | Type correct? | Constructed? | Enforced at runtime? | Actually prevents failure? |
|---|---|---|---|---|---|
| AlphaDecision | D1 | Yes | Yes | **Yes** (assert_target_compatible) | **Yes** — but "risk_cap" label is debatable |
| TailTreatment | D2 | Yes | Optional | When provided | **Partial** — falls back to bare 0.5 |
| ExecutionPrice | D3 | Yes | Yes | **Yes** (assert_kelly_safe) | **NO** — fee_deducted=True is false |
| DecisionEvidence | D4 | Yes | Yes (_unused) | **NO** | **NO** — asymmetry documented, not fixed |
| VigTreatment | D5 | Yes | Optional | When provided | **Yes** — gold standard |
| HoldValue | D6 | Yes | **NO** | **NO** | **NO** — EV gate uses bare arithmetic |
| ProvenanceRegistry | INV-13 | Yes | Bypassed | **NO** | **NO** — zero runtime checks |

---

## 8. Recommendations (Prioritized — Part A + Part B combined)

### P0 — IMMEDIATE (blocks live correctness)

1. **Fix fee formula.** Replace `ASSUMED_TAKER_FEE = 0.05` with `fee(p) = feeRate × p × (1 - p)` where `feeRate = 0.05` for weather. This is a 5-10× error at tail prices. The `FeeGuard.min_gross_edge_for_trade()` floor of 7% is rejecting trades with 3-4% real edge at tail prices. **This single fix could change the endgame outcome.**

2. **Fix ExecutionPrice `fee_deducted=True` lie.** Two options:
   - **(A) Actually deduct the fee:** In `evaluator.py`, compute `fee = feeRate × p × (1-p)` and pass `value=edge.entry_price + fee` to `ExecutionPrice(fee_deducted=True)`. This makes Kelly see the true cost.
   - **(B) Set `fee_deducted=False`:** Pass `fee_deducted=False` and add a separate `assert_fee_handled()` check downstream that verifies fee was accounted for in the sizing pipeline. Less clean but honest.
   - **Recommended: Option A.** It fixes both the semantic lie and the actual sizing error.

3. **Enable INV-13 enforcement.** Three steps:
   - Fix name mismatches: use consistent naming (pick underscore or dot, pick one semantic label)
   - Change `requires_provenance=False` to `True` in all 8 kelly.py calls
   - Add `require_provenance()` calls for the 6 multiplier values that are currently unchecked bare literals
   - Add `require_provenance()` calls for alpha adjustments in market_fusion.py

### P1 — HIGH (blocks spec closure per §P9.8)

4. **Wire DecisionEvidence into exit path.** The P9.8 exit condition requires "all 6 contract types exist." They exist, but D4 and D6 are not enforced:
   - Add `evidence` field to `ExitSignal` dataclass
   - Pass `_exit_evidence` to `ExitSignal` instead of discarding it
   - Call `assert_symmetric_with()` with stored entry evidence (requires persisting entry evidence on Position)
   - This is the only way to honestly close D4

5. **Wire HoldValue into EV gate.** `exit_triggers.py:163` uses `net_hold = shares * position.p_posterior` — exactly the D6 bare arithmetic. Replace with `HoldValue.with_costs(gross_value, fee_cost, time_cost)` using the price-dependent fee formula.

6. **Fix cascade bound.** Add `m = max(0.001, m)` floor in `dynamic_kelly_mult()` line 103, before the return. The drawdown pathway currently allows m=0.0, and the non-drawdown worst case is 0.0002625 — both below the spec's claimed 0.001 floor.

7. **Make TailTreatment mandatory.** `compute_posterior()` falls back to bare `0.5` when `tail_treatment is None`. Either require the parameter or make the fallback construct a TailTreatment internally.

### P2 — MEDIUM (improves accuracy, not blocking)

8. **Query per-market fee status.** Use `GET /fee-rate?token_id={id}` to verify `feesEnabled` and actual feeRate before trading. Add this to the P10 `RealityContract` economic family.

9. **Implement maker strategy.** Zeus posts limit orders — if posted below best ask, it is a MAKER and pays ZERO fees while earning 25% rebate. The current 7% floor assumes taker on every trade. A maker-first strategy could make fees irrelevant.

10. **Model fee collection asymmetry** (shares on buy, USDC on sell) in P&L attribution.

11. **Add `feeRateBps` to order signing.** Verify the CLOB client handles this correctly for live orders.

### P3 — LOW (improvements)

12. **Verify min_order_size** against live Polymarket endpoint.
13. **Add UMA dispute contingency** for delayed settlement (48-96h DVM voting period).
14. **Implement Polymarket rate limit detection** (currently only Open-Meteo has rate limit handling).

---

## Source Attribution

- Fee structure: https://docs.polymarket.com/trading/fees (fetched 2026-04-05)
- Order types: Polymarket py-clob-client and clob-client GitHub READMEs
- UMA oracle: https://docs.uma.xyz/protocol-overview/how-does-umas-oracle-work
- Tick size: Polymarket docs examples (0.01 default, configurable per market)
- Codebase: Zeus repo at `/Users/leofitz/.openclaw/workspace-venus/zeus/`
