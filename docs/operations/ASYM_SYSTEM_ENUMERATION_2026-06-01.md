# ASYM SYSTEM ENUMERATION — NO/short complement-of-YES audit

- Created: 2026-06-01
- Last reused or audited: 2026-06-01
- Authority basis: OPERATOR LAW 2026-06-01 ("NOTHING inherits NO = reverse-of-YES"); HEAD `6fcd05a69f`; read-only enumeration
- Scope: system-wide `src/` — every site that derives a NO/short/complement quantity from a YES quantity, classified A/B/C

## OPERATOR LAW (restated)

No NO-side decision quantity may be obtained by **reversing a YES decision quantity**. Concretely, for any `buy_no` (or short / held-NO) candidate, each of:

`q_NO`, `q_NO_lcb`, `p_fill_NO`, `cost_NO`, `edge_NO`, `Kelly_NO`, `P(held)_NO`

must be grounded in **NO's own distribution / NO's own book / NO's own bootstrap**. The single tolerated exception is the **per-token binary point complement** `q_NO = 1 − q_YES` for the *same bin's* two tokens (Class A) — and even then, any **tail** quantity (LCB/UCB/percentile) must be computed by complementing **per bootstrap sample** (`q_NO = 1 − q_YES` inside the loop, so `q_NO_lcb` = 5th pct of `1 − q_YES` = `1 − q_YES_UCB`), **never** by reversing an already-collapsed YES LCB (`1 − yes_lcb` is RED).

## Classification key

- **(A)** CORRECT per-token binary point complement (`q_NO = 1 − q_YES` for the SAME bin's two tokens). Defensible. Flagged because the reverse-pattern is present in source.
- **(B)** NAIVE/WRONG — a derived uncertainty / fill / edge / cost reversed same-direction, or a family/shoulder mishandle, or a YES tail bound reversed to a NO tail bound.
- **(C)** INDEPENDENT — NO grounded from its own source (book/bootstrap/native quote). The desired state.

---

## Full classified table

### Surface: `src/strategy/market_analysis.py` (live edge construction + bootstrap)

| file:line | expression | class | note |
|---|---|---|---|
| `market_analysis.py:399` | `return 1.0 - float(self.p_market[bin_idx])` | A | `buy_no_complement_diagnostic_price()` — explicitly named DIAGNOSTIC, raises if used as executable authority. Binary point complement, non-executable. |
| `market_analysis.py:572` | `p_model_no = 1.0 - float(self.p_cal[i])` | A | NO model point = 1 − YES calibrated point (per-token binary complement). |
| `market_analysis.py:574` | `p_post_no = 1.0 - float(self.p_posterior[i])` | A | NO posterior POINT. Tail is NOT taken here — taken in bootstrap (below). |
| `market_analysis.py:573,610` | `p_market_no = self.buy_no_market_price(i)`; `edge_no = p_post_no − entry_cost_mean_no` | C | NO cost = **native NO VWMP** (executable NO ask), not YES complement. Edge = NO point − NO cost. |
| `market_analysis.py:890` | `bootstrap_edges[i] = (1.0 - p_post_yes) - c_b` | **C (tail-correct)** | NO edge bootstrap complements `p_post_yes` **per sample inside the loop** (`_bootstrap_bin_no`), then takes `percentile(...,5)`. So `q_NO_lcb` ← 5th pct of `1 − q_YES` ≡ `1 − q_YES_UCB`. This is the LAW-mandated tail form. `c_b` is NO-native cost. |
| `market_analysis.py:698` | `p_posterior=1.0 - float(self.p_posterior[i])` | A | NO point inside a non-executable trace record (no quote probed). Non-actionable. |

### Surface: `src/engine/event_reactor_adapter.py` (EDLI live money-path)

| file:line | expression | class | note |
|---|---|---|---|
| `event_reactor_adapter.py:2878` | `(no_token_id, "buy_no", 1.0 - yes_q, no_lcb)` | A (point) / C (lcb) | NO q POINT = `1 − yes_q` (binary complement, A). NO q_lcb = `no_lcb` from `q_lcb_by_direction[(cid,"buy_no")]`, a **separately keyed** value (NOT `1 − yes_lcb`). |
| `event_reactor_adapter.py:3124` | `lcb_by_direction[(cid, dir)] = float(hyp.ci_lower) + cost_by_direction[dir]` | C | For `dir="buy_no"`, `hyp.ci_lower` is the NO-bootstrap edge LCB (`_bootstrap_bin_no`, tail-correct) and the cost is `p_market_no_vec` (native NO). So `q_NO_lcb = edge_lcb_NO + cost_NO`, fully NO-grounded. |
| `event_reactor_adapter.py:3132` | `q_point = yes_posterior if buy_yes else (1.0 - yes_posterior)` | A | FALLBACK when a direction has no executable hyp. Emits `p_value=1.0`, `prefilter=False`, `lcb=q_point` → non-actionable; rejected downstream by `EXECUTABLE_NATIVE_ASK_MISSING`. Reverse-pattern present but inert. |
| `event_reactor_adapter.py:3345` | `p_market_no.append(float(no_price) ...)` | C | NO cost vector built from **native NO ask** (`native_costs[(cid,"buy_no")]`), default sentinel only when absent. |
| `event_reactor_adapter.py:3864` | `masked_lcb[(cid,"buy_no")] = min(no_lcb, 1.0 - q_value)` | A (cap) | Day0 path. `no_lcb` is NO-grounded; capped by NO POINT `1 − q_yes` (an LCB cannot exceed its point). Cap LOWERS, never reverses the tail — tail-safe. Mirror of YES `min(yes_lcb, q_value)` at :3863. |
| `event_reactor_adapter.py:4250` | `"buy_no": book.no_asks` (`_p_fill_lcb_for_direction`) | **C** | `p_fill_NO` Wilson-LCB computed from **NO book's own visible depth** (`book.no_asks`). Fully independent of YES fill. Gold-standard. |
| `event_reactor_adapter.py:4094` | `c_cost_95pct` from `_execution_price_from_snapshot(row, token=no_token, dir="buy_no")` | C | NO cost-95pct walks the **NO book**, not YES complement. |

### Surface: `src/engine/monitor_refresh.py` (exit-side P(held) + CI)

| file:line | expression | class | note |
|---|---|---|---|
| `monitor_refresh.py:783` / `:1183` | `p_cal_native = 1.0 - p_cal_yes` (if `buy_no`) | A | Held-NO calibrated POINT = 1 − YES (binary complement). Feeds `_model_only_native_posterior` + bootstrap context. |
| `monitor_refresh.py:1804` | `p_market_yes = current_p_market if buy_yes else 1.0 - current_p_market` | A | BINARY-only (`len(bins)<=2`): reconstructs YES cost from NO cost by complement, to feed `_bootstrap_bin_no`. Multi-bin uses native NO quote (:1810). Binary per-token complement. |
| `monitor_refresh.py:1833` | `ci_lower, ci_upper, _ = analysis._bootstrap_bin_no(held_idx, ...)` | **C (tail-correct)** | Exit-side NO CI uses the **per-sample NO bootstrap**, NOT `1 − yes_lcb`. |
| `monitor_refresh.py:112` | `np.array([p, 1.0 - p])` | A | Binary 2-vector `[YES, NO]` for a binary market. Point complement. |

### Surface: `src/execution/*` (exit triggers, collateral, executor)

| file:line | expression | class | note |
|---|---|---|---|
| `exit_triggers.py:232-234` | `forward_edge`/`ci_width` from `current_edge_context` (NO-native) → `conservative_forward_edge` | C | buy_no exit edge + LCB threshold built from the **NO-native edge context** (sourced via `_bootstrap_bin_no`). No reversal. AGENTS.md:34/39 codify "never flip". |
| `collateral.py:45` | `required = (1.0 - entry_price) * shares` | C | NO/long collateral = max-loss accounting per share; own definition, not a complement of a YES quantity. |
| `executor.py:1597,1735,1795` | `direction == "buy_no"` branch routing | C | Direction routing to NO token / NO sizing; no quantity reversal. |

### Surface: `src/state/db.py` (derived JSON probability for storage)

| file:line | expression | class | note |
|---|---|---|---|
| `db.py:6513` | `if direction == "buy_no": probability = 1.0 - probability` | A | Derived/displayed NO probability POINT for a stored edge vector. INV-17: DB canonical, JSON derived — never inverts authority. Point complement. |

### Surface: `src/engine/evaluator.py` (Day0 degenerate bootstrap override)

| file:line | expression | class | note |
|---|---|---|---|
| `evaluator.py:2598` | `edge = float((1.0 - probabilities[bin_idx]) - p_market_no)` | A (point) / C (cost) | Day0 `_edli_bootstrap_bin_no` override: NO point = `1 − q_yes` (A), cost = native `buy_no_market_price` (C). **Degenerate CI**: returns `(edge, edge, ...)` → `ci_lower=ci_upper=edge`, NO tail distribution on day0. Not a reversal, but no NO-tail honesty either (flagged, see Caveat). |

### Surface: `src/strategy/kelly.py`, `trade_score.py`, `market_fusion.py`

| file:line | expression | class | note |
|---|---|---|---|
| `kelly.py:62` | `f_star = (p_posterior - price_value) / (1.0 - price_value)` | C | Direction-agnostic. Caller passes NO-native `p_posterior` + NO-native `price_value` for buy_no. `1 − price_value` is the Kelly odds denominator (win-multiple), not a YES→NO reversal. |
| `trade_score.py:48-52,68-79` | `score = p_fill_lcb * min(q_5pct − c_95pct − λ, q_posterior − c_stress − λ)` | C | Direction-agnostic consumer. All of `q_5pct`, `c_95pct`, `p_fill_lcb` are passed NO-native for buy_no. No internal reversal. |
| `market_fusion.py:324-326` | `raw = alpha * p_cal + (1.0 - alpha) * market` | C | `1 − alpha` is a fusion weight, not a YES/NO complement. (Comment :55 notes fusion "not validated against buy_no P&L" — separate calibration concern, not a reversal defect.) |

### Surface: `src/backtest/*` (offline harness — NOT live path)

| file:line | expression | class | note |
|---|---|---|---|
| `shadow_replay_harness.py:445` | `p_model_no = 1.0 - p_raw_val` | A | Offline replay. Point complement; edge uses fixed 0.5 cost placeholder (backtest scaffold, not live cost). |
| `fill_simulator.py:7,143` | `fee_per_share = fee_rate * p * (1 - p)` | C | Polymarket fee curve, not a complement. |

---

## B-COUNT (real defects): **0**

No site reverses a YES uncertainty/fill/edge/cost/tail into a NO quantity. The single forbidden pattern the LAW targets — `q_NO_lcb = 1 − yes_lcb` (collapsed-LCB reversal) — **does not exist anywhere in `src/`** (verified: `rg '1\.0?\s*-\s*\w*(lcb|ucb|ci_lower|ci_upper|_5pct|_95pct|yes_lcb)' src/` → zero hits).

Every tail quantity for NO (q_NO_lcb on the live path at `event_reactor_adapter.py:3124`, on the exit path at `monitor_refresh.py:1833`) is produced by `_bootstrap_bin_no`, which complements `p_post_yes` **per bootstrap sample** and only then takes the 5th percentile — i.e. it already uses the correct `1 − q_YES_UCB` tail. `p_fill_NO` and `cost_NO` are both grounded in the NO book directly.

### Caveats (NOT B, but worth an antibody)

1. **Day0 degenerate NO CI** (`evaluator.py:2598`): the day0 override collapses `ci_lower=ci_upper=edge`, so the day0 buy_no path carries **no tail uncertainty at all** (same as the day0 YES override at :2594). This is a day0-wide design choice, not a NO-vs-YES asymmetry, but it means `q_NO_lcb == q_NO_point` on day0. Flag for the day0 monitoring owner — not a reverse-pattern defect.
2. **Fallback point-complement** (`event_reactor_adapter.py:3132`): inert (non-actionable, rejected downstream) but is the one place a naive future edit could promote `1 − yes_posterior` into an actionable lcb. The antibody below pins it.

---

## ANTIBODY — relationship-test spec

Encode the LAW as cross-module relationship tests so a future naive `1 − yes_lcb` is RED. Place in `tests/strategy/test_no_side_independence_invariant.py` (new) + extend `tests/engine/` reactor tests.

### REL-NO-1 — q_NO_lcb is the per-sample complement tail, NOT the reversed YES LCB
Construct a `MarketAnalysis` for a binary bin with an **asymmetric** YES posterior bootstrap (skewed so `yes_lcb` and `1 − yes_ucb` differ materially). Assert:
```
ci_lo_no, ci_hi_no, _ = analysis._bootstrap_bin_no(idx, N)
ci_lo_yes, ci_hi_yes, _ = analysis._bootstrap_bin(idx, N)
# NO edge LCB must track (1 - YES_UCB) - cost, i.e. the UPPER YES tail —
# it must NOT equal (1 - yes_lcb)-derived value.
q_no_lcb = ci_lo_no + cost_no                 # restore prob space
q_no_from_correct_tail ≈ 1 - (ci_hi_yes + cost_yes_point→q_yes_ucb)
assert abs(q_no_lcb - q_no_from_correct_tail) < tol
# RED tripwire: the naive reversal must be detectably different
q_no_lcb_naive = 1 - (ci_lo_yes + cost_yes→q_yes_lcb)
assert abs(q_no_lcb - q_no_lcb_naive) > tol   # asymmetry makes these diverge
```
A future edit that sets `q_NO_lcb = 1 - yes_lcb` makes line 2 fail and line 4's "diverge" assertion fail → RED.

### REL-NO-2 — p_fill_NO is grounded in the NO book
Build a snapshot where `no_asks` depth ≠ `yes_asks` depth (e.g. NO book thin, YES book deep). Assert:
```
p_fill_no = _p_fill_lcb_for_direction(book, direction="buy_no", shares=s)
p_fill_yes = _p_fill_lcb_for_direction(book, direction="buy_yes", shares=s)
assert p_fill_no != p_fill_yes              # must reflect NO depth, not YES
assert p_fill_no == _wilson_from(book.no_asks, s)   # exact NO-book source
# tripwire: p_fill_no must NOT equal 1 - p_fill_yes
assert abs(p_fill_no - (1 - p_fill_yes)) > tol
```

### REL-NO-3 — cost_NO is the native NO quote
With native NO ask present and ≠ `1 − yes_ask`:
```
assert proof_no.execution_price.value == native_no_ask          # not 1 - yes_ask
assert abs(proof_no.execution_price.value - (1 - yes_ask)) > tol # tripwire
```

### REL-NO-4 — edge_NO and Kelly_NO consume NO-native inputs end-to-end
For a buy_no candidate, assert the trade_score receipt's `q_5pct`, `c_95pct`, `p_fill_lcb` equal the NO-grounded values (REL-NO-1/2/3 outputs), and that `kelly` was called with NO-native `p_posterior`/`price_value`:
```
assert receipt.q_5pct == q_no_lcb_from_bootstrap_no
assert receipt.c_95pct.value == native_no_cost_95pct
assert receipt.p_fill_lcb == p_fill_no_from_book
# Kelly: f_star uses p_post_no and native no cost
assert kelly_call.p_posterior == p_post_no and kelly_call.price_value == native_no_cost
```

### REL-NO-5 — P(held)_NO exit CI uses _bootstrap_bin_no (not yes-reversal)
In `monitor_refresh.refresh_position` for a buy_no position, assert the CI path invoked `analysis._bootstrap_bin_no(...)` (spy/patch) and NOT `1 - analysis._bootstrap_bin(...)`:
```
assert spy.called_with == "_bootstrap_bin_no"
# and resulting ci_lower differs from 1 - yes_ci under asymmetry
```

### REL-NO-6 (guard) — static tripwire on forbidden source pattern
AST/grep CI check (extend the money-path lint) asserting **zero** occurrences in `src/` of `1[.0] - <ident ending in lcb|ucb|ci_lower|ci_upper|_5pct|_95pct>`. Any reintroduction of `1 - yes_lcb`-shaped reversal fails CI before runtime.

All six are **relationship tests** (cross-module invariants across the YES↔NO boundary), authored BEFORE any refactor that touches the complement seams. The asymmetric-bootstrap fixture is the load-bearing element: it makes `1 − yes_lcb` numerically distinguishable from the correct `1 − yes_ucb`, so the naive reversal cannot pass green.
