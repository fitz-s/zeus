# Kelly Portfolio-Allocation Gap — Design vs Live (EDLI reactor path)

```
# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: AGENTS.md probability chain; docs/reference/zeus_math_spec.md §10, §15.7;
#   docs/reference/zeus_risk_strategy_reference.md §3; config/settings.json::_bankroll_doctrine_2026_05_04
# Mode: READ-ONLY root-cause (no edits). HEAD 6fcd05a69f.
```

Operator claim under test: "Kelly is wrong — there should be MULTIPLE Kelly (portfolio
allocation across concurrent positions), not one final Kelly on the whole bankroll;
this was by design. A $43 single bid should not happen."

**Verdict: CONFIRMED.** The EDLI reactor sizes every candidate against the FULL bankroll
independently, with no division by concurrent-position count, no per-position fraction cap,
and no cumulative portfolio-heat gate. The only bound is a flat per-order USD clamp
(`tiny_live_max_notional_usd`). The cross-family allocation discipline the design specifies
lives in the BROAD cycle path (`evaluator.py` / `cycle_runner.py` / `risk_limits.would_breach`)
and was never wired into the EDLI reactor. The $43 is exactly `f*0.93 × mult0.25 × bankroll$185`.

---

## PART 1 — THE DESIGN (what was specified)

The chain is per-edge Kelly, but **bounded by a portfolio-heat allocator that throttles each
marginal position against cumulative held exposure** — the design's "multiple Kelly across
concurrent positions."

- `AGENTS.md:13` — probability chain ends `… → Fractional Kelly → Position Size`. Per-edge.
- `docs/reference/zeus_math_spec.md:455` — `size = f* · kelly_mult · bankroll`.
- `docs/reference/zeus_math_spec.md:458-461` — `kelly_mult` "Reduces (multiplicatively) based on …
  Calibration maturity … Elevated risk state". The risk-strategy ref makes the portfolio term explicit:
- `docs/reference/zeus_risk_strategy_reference.md:195` — dynamic-multiplier table row:
  **`| Portfolio heat | portfolio_heat > 0.40 | × max(0.1, 1.0 - portfolio_heat) |`**
  → marginal sizing is throttled by aggregate exposure across concurrent positions. This is (c) a
  portfolio-heat-aware allocator gating each marginal position.
- `docs/reference/zeus_risk_strategy_reference.md:179-181` — "Per-trade cap parameters are not part of
  the current Kelly contract. Entry discipline is enforced after Kelly by wallet-bankroll availability,
  RiskGuard, **and max-exposure gates.**" The portfolio cap is a SEPARATE post-Kelly gate, by design.
- `config/settings.json:326` — **`max_portfolio_heat_pct: 0.5`** (aggregate exposure ceiling, fraction of bankroll).
- `config/settings.json:325` — **`max_single_position_pct: 0.1`** (per-position cap = 10% of bankroll) → (b).
- `config/settings.json:328` — `max_city_pct: 0.2` (per-city correlation cap) → (d) correlation-aware.
- `config/settings.json::_bankroll_doctrine_2026_05_04` — "Per-cycle exposure discipline now lives in
  posture / RiskGuard / **max-exposure gates only**." The doctrine deletes per-trade caps and relocates
  exposure control to the cumulative portfolio gate — which the EDLI path must therefore inherit.
- `docs/reference/zeus_math_spec.md:903` + `src/strategy/family_exclusive_dedup.py:909`
  (`optimize_exclusive_outcome_portfolio`) — a Kelly portfolio optimizer maximizing
  `max_f E[log(1 + Σ_i f_i·R_i(Y))]` BUT only INTRA-family (exclusive bins of one city/date/metric).
  Its docstring (`family_exclusive_dedup.py:77-78`) states it "relies on per-leg Kelly + **portfolio_heat**
  to bound exposure" — i.e. even the intra-family optimizer presumes the cross-family heat gate exists downstream.

**Design answer to the four sub-questions:** the spec calls for (b) a per-position cap
(`max_single_position_pct=0.1`), (c) a portfolio-heat allocator gating each marginal position
(`max_portfolio_heat_pct=0.5`, `dynamic_kelly_mult` heat throttle), and (d) correlation-aware capping
(`max_city_pct=0.2`). It does NOT divide bankroll by a fixed N (a) — it uses a CUMULATIVE-heat gate,
which is strictly better than naive `bankroll/N`.

---

## PART 2 — THE LIVE EDLI PATH (what's implemented)

Trace of a single position size in the reactor (`src/engine/event_reactor_adapter.py`):

1. `src/strategy/kelly.py:30-58` — `kelly_size(p_posterior, entry_price, bankroll, kelly_mult=0.25)`
   → `f_star = (p_posterior - price)/(1 - price)`; **`return f_star * kelly_mult * bankroll`**.
   Sizes against the FULL `bankroll` argument. No N, no held-exposure term.
2. `src/events/money_path_adapters.py:81-100` — `evaluate_kelly(…, bankroll_usd, kelly_multiplier)`
   calls `kelly_size(p_posterior, execution_price, bankroll_usd, kelly_mult=kelly_multiplier)`.
   `kelly_multiplier` is a **flat scalar** — no portfolio_heat parameter exists in this signature.
3. `src/engine/event_reactor_adapter.py:810-828` (live flat-kelly path):
   - `bankroll_usd = _bankroll_usd_from_provider(...)` → full on-chain wallet (line 4326-4334).
   - `kelly_multiplier = _runtime_kelly_multiplier()` → `settings["sizing"]["kelly_multiplier"]` = **0.25 constant**
     (`event_reactor_adapter.py:4360-4366`). Notably this is NOT `dynamic_kelly_mult` — the reactor
     uses the raw 0.25, so even the heat term INSIDE `dynamic_kelly_mult` (`kelly.py:499-500`) is bypassed.
   - `_maybe_bias_decay_kelly_haircut(...)` (line 815-820) — only per-city bias haircut, no portfolio term.
   - `evaluate_kelly(p_posterior=proof.q_posterior, bankroll_usd=bankroll_usd, kelly_multiplier=kelly_multiplier)`.
4. The resulting `kelly.size_usd` is clamped ONLY by the flat per-order cap at
   `src/engine/event_reactor_adapter.py:1615-1616`:
   `requested_notional = max(min(kelly_size_usd, max_notional_usd), min_order_notional)`
   where `max_notional_usd = tiny_live_max_notional_usd` (`config/settings.json:122` = **185.0**).
   This is a per-ORDER clamp, NOT a portfolio constraint.

Searches confirming the absence (HEAD 6fcd05a69f):
- `grep would_breach|risk_limits|RiskLimits|max_single_position` over
  `event_reactor_adapter.py` + `reactor.py` + `money_path_adapters.py` → **ZERO matches.**
- `grep cumulative|total_exposure|held_positions|open_position|sum.*kelly` in reactor cycle → **ZERO matches.**
- `portfolio_heat` / `max_portfolio_heat_pct` appear ONLY in `cycle_runner.py` (lines 833-835),
  `evaluator.py` (5451, 6001), `risk_limits.py` (51-52), `kelly.py` (499-500) — never in the reactor.

**$43 reproduction (exact):**
`f* = 0.93`, `kelly_mult = 0.25`, `bankroll = $185` →
`0.93 × 0.25 × 185 = $43.01`. The per-order cap `min($43.01, $185) = $43.01` is non-binding
(43 < 185), so the full Kelly bid lands. With a ~$185 wallet that single bid is **23% of bankroll**
on ONE bin — already above `max_single_position_pct=0.10`. With N concurrent near-certain families,
total exposure = N × $43, bounded by NOTHING in the reactor.

Status: `live_submit_enabled=False` (`event_reactor_adapter.py:911`), `SUBMIT_DISABLED`
(`1063-1064`) — currently SHADOW/no-submit, so this is a pre-live structural defect, not a
realized loss. But the sizing number persisted in receipts is the wrong one.

---

## PART 3 — THE GAP

The portfolio allocator EXISTS but is unwired in the EDLI path; the broad (non-EDLI) cycle path HAS it.

| Constraint | Broad cycle path | EDLI reactor path |
|---|---|---|
| Per-position cap (`max_single_position_pct=0.1`) | `risk_limits.would_breach` `evaluator.py` | **ABSENT** |
| Cumulative portfolio heat (`max_portfolio_heat_pct=0.5`) | `would_breach` `new_heat=current+position_pct` (`risk_limits.py:51-55`); `dynamic_kelly_mult(portfolio_heat=current_heat)` (`evaluator.py:6001`, `kelly.py:499-500`); exposure-gate at `cycle_runner.py:833-835` | **ABSENT** |
| Per-city correlation cap (`max_city_pct=0.2`) | `would_breach` (`risk_limits.py:58`) | **ABSENT** |
| Dynamic (heat-aware) multiplier | `dynamic_kelly_mult` | **ABSENT** — uses flat 0.25 (`_runtime_kelly_multiplier`) |
| Intra-family Kelly portfolio | `optimize_exclusive_outcome_portfolio` (`family_exclusive_dedup.py:909`) | partial (single-leg Stage A; live max_legs HARD-CAPPED to 1) |
| Only bound present | — | flat per-order `tiny_live_max_notional_usd=$185` |

The EDLI reactor reimplemented sizing as a standalone `evaluate_kelly(flat_multiplier, full_bankroll)`
and never re-attached the post-Kelly exposure discipline that `_bankroll_doctrine_2026_05_04`
explicitly says is "where per-cycle exposure discipline now lives." The broad path passes
`current_heat` (computed from held positions) INTO `dynamic_kelly_mult` AND re-checks cumulative
exposure in `would_breach`; the reactor does neither. The flat `tiny_live_max_notional_usd` clamp was
inserted as the canary's sole guard and is being mistaken for portfolio discipline — it is not: it
bounds one order, not the book.

Root cause (high-dimensional): not "the $43 number is wrong" (symptom). The design failure is at the
**Module A → Module B boundary** — the EDLI reactor (`event_reactor_adapter`) consumes `kelly_size`'s
output but drops the portfolio-exposure context (`current_heat`, held positions) that the broad
evaluator carries across that same boundary. One structural decision — "route all sizing through the
portfolio-heat allocator" — was executed in `evaluator.py` and NOT in the reactor. N future bugs
(over-concentration, city-crowding, single-bin 23% bids) are symptoms of that one unexecuted decision.

---

## PART 4 — THE CORRECT FIX (design-faithful, not invented)

The design-faithful sizing is **cumulative portfolio-heat-gated marginal Kelly** — NOT `bankroll/N`.
Each marginal position is sized by Kelly, then throttled/rejected against AGGREGATE held exposure:

```
current_heat = portfolio_heat_for_bankroll(portfolio, bankroll)          # src/state/portfolio.py:2418
kelly_mult   = dynamic_kelly_mult(base=0.25, …, portfolio_heat=current_heat)   # kelly.py heat throttle
size         = f* · kelly_mult · bankroll                                # kelly.py:58
# then HARD gate before accept:
allowed, reason = would_breach(                                          # risk_limits.py:24-60
    size_usd=size, bankroll=bankroll,
    current_portfolio_heat=current_heat, current_city_exposure=city_exposure,
    limits=RiskLimits(max_single_position_pct=0.10, max_portfolio_heat_pct=0.50, max_city_pct=0.20))
# reject if not allowed; new_heat = current + size/bankroll must stay ≤ 0.50
```

**Where it must wire into the EDLI path** (`src/engine/event_reactor_adapter.py`):
1. The reactor cycle must load the live `PortfolioState` and compute `current_heat` once per cycle,
   then thread `current_heat` + per-city held exposure into the per-candidate sizing block (810-828).
2. Replace `_runtime_kelly_multiplier()` (flat 0.25) at line 814 with `dynamic_kelly_mult(base=0.25,
   …, portfolio_heat=current_heat)` so the heat throttle (`kelly.py:499-500`) actually applies.
3. After `evaluate_kelly` (line 821-827), call `would_breach(...)` and emit a
   `PORTFOLIO_HEAT_BREACH` / `SINGLE_POSITION_CAP` / `CITY_CAP` rejection receipt BEFORE building the
   live-cap certificate — mirroring the broad path's gate. (Reactor currently has only KELLY_REJECTED
   on `size_usd<=0`.)

**Corrected max single bid** (per design, bankroll $185):
- Per-position cap binds first: `max_single_position_pct = 0.10 × $185 = $18.50` (vs current $43).
- Aggregate book ceiling: `max_portfolio_heat_pct = 0.50 × $185 = $92.50` total across ALL concurrent
  positions (vs current unbounded N × $43).
- Per-city ceiling: `max_city_pct = 0.20 × $185 = $37.00` per city.

So the corrected max single bid is **$18.50** (10% cap), down from the current $43.01, and the whole
book is capped at $92.50 instead of growing linearly with the number of concurrent near-certain families.

Note: `bankroll/N` (option a) is the WRONG fix — it is not what the design specifies and degrades as N
grows or shrinks. The design's cumulative-heat gate is the correct, already-built mechanism; the fix is
to WIRE it into the reactor, not invent a divisor.
