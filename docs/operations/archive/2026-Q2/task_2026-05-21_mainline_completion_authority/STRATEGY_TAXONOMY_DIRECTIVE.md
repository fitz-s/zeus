# Strategy Taxonomy Directive — Operator Source (2026-05-22)

> Verbatim operator framework. This is the **input artifact** every implementation
> subagent must read before working. It is NOT the polished strategy spec (that is
> authored separately by the spec-author track). All theorems below are the
> authority for the reframe. Notation: `phi` = fee, `p-` = calibrated lower bound,
> `p+` = calibrated upper bound, `a` = YES ask, `b` = NO ask.

## §0. Unified judgment criterion — every strategy falls into one of three proof classes

At time `t`, buy executable price vector `a_t` for token combo `w`; settlement payoff
random variable `X`; fee `phi(a_t)`. A strategy is application-grade iff it proves ONE of:

1. **Pathwise arbitrage**: `X(ω) − C(a_t) > 0  ∀ω`
2. **Physics-bounded deterministic edge**: `X(ω) = x*` given a physical/settlement state
3. **Calibrated statistical edge**: `E[X − C | F_t] > 0`

Class 3 MUST use a **calibrated bound**, not a raw posterior:
- buy YES: `p⁻_{i,t} − a_{i,t} − phi(a_{i,t}) > 0`
- buy NO:  `1 − p⁺_{i,t} − b_{i,t} − phi(b_{i,t}) > 0`

`p⁻, p⁺` = probability lower/upper bound after calibration / conformal / posterior credible bound.

**Polymarket reality**: CTF gives YES/NO $1 payoff, fully collateralized; taker fee
`phi = C · feeRate · p(1−p)`, weather feeRate = 0.05, maker fee = 0; all orders are limit;
FOK = fill-entirely-or-cancel; on-chain matched trade is atomic.

---

## §1. settlement_capture → physical state-lock arbitrage (NOT a forecast strategy)

Currently live (LIVE_NORMAL, buy_yes, point/finite_range, settlement-day). Current thesis:
buy bin nearest observed temperature once same-day observation is sufficiently certain.
This is settlement-state recognition, not forecast alpha.

**Interval-state theorem.** Final official temp `T*`, bin `B_i = [l_i, u_i)`. At time `t`,
store a physical possible interval `T* ∈ I_t = [L_t, U_t]` (not a point).

- If `I_t ⊆ B_i`  → `1{T*∈B_i} = 1` → buy YES, pathwise `Π_i = 1 − a_i − phi(a_i)`, positive iff `a_i + phi(a_i) < 1`.
- If `I_t ∩ B_i = ∅` → YES must lose, NO must win → `Π_i^NO = 1 − b_i − phi(b_i)`, positive iff `b_i + phi(b_i) < 1`.

**Physics**: daily high — current high-so-far `H_t` is a floor: `T*_high ≥ H_t`; upper bound
NOT a constant cap but `T*_high ≤ H_t + Δ_phys⁺(t, station, season, synoptic)` from solar
radiation / remaining daylight / boundary-layer mixing / advection bound / historical
same-station-season hourly-transition envelope. Daily low: `T*_low ≤ L_t`,
`T*_low ≥ L_t − Δ_phys⁻`. These are meteorological constraints turned into a settlement interval.
NWS API provides forecasts/alerts/observations; observations may have QC/ingest delay → the
interval MUST carry `source_available_at` + QC state.

**Application form**: enter iff `I_t ⊆ B_i` OR `I_t ∩ B_i = ∅`. Evidence = SettlementCaptureVerifier
timestamp coherence, NOT long-run win-rate.

---

## §2. resolution_window_maker → source-known venue-unresolved discount arbitrage

Current code: shadow candidate; checks umaResolutionStatus string ∈ resolved/asserted, writes
shadow decision; target/size/posterior None, edge placeholder `_SHADOW_EDGE=0.03`. Same class as
settlement_capture: resolution-state arbitrage, not prediction.

Typed outcome determined `i*`: `Y_{i*}=1, Y_j=0 ∀j≠i*`.
- winning YES ask `a_{i*}`: `Π = 1 − a_{i*} − phi(a_{i*})`
- losing NO ask `b_j`: `Π_j^NO = 1 − b_j − phi(b_j)`
Trade if any `1 − a_{i*} − phi > 0` OR `1 − b_j − phi > 0`. Maker fee 0 but fill not guaranteed;
FOK/taker → fill确定收益. FOK no-fill → no position → payoff theorem unchanged.

**Improvement**: stop using raw string status; use Phase-7 typed SettlementOutcome
`SOURCE_PUBLISHED_VENUE_UNRESOLVED`. Then compute `winning_token_profit = 1 − ask − fee`,
`losing_no_profit = 1 − no_ask − fee`, pick max positive. Application-grade; same class as
settlement_capture. Current problem = frozen/unwired input, not invalid edge.

---

## §3. stale_quote_detector → FOK information-delay arbitrage

Current code: spread_observed_window_ms as info-event proxy, book_hash_transition_delta_ms as
stale detector, depth>0 → shadow enter, edge `_SHADOW_EDGE=0.02`. But MarketAnalysisVNext fixes
spread_observed_window_ms = None → deadlocked on real data. "stale" ≠ positive EV; positive EV =
`new_fair_value − old_executable_quote > fee`.

Info event `E` at `t_E` (forecast update / official observation / source publication).
Pre-event fair prob `p_0`, post `p_1`. Market ask still old `a_0`. Single-share YES edge:
`Δ = p_1 − a_0 − phi(a_0)`. FOK execution; `R = 1_F · (Y − a_0 − phi(a_0))`;
`E[R|F_t] = Pr(F)·(p_1 − a_0 − phi(a_0))`. If `p_1 − a_0 − phi > 0` and `Pr(F)>0` → `E[R]>0`.
**Key**: FOK makes no-fill loss = 0; fill probability affects volume not edge sign.

**Improvement**: define InfoEvent (forecast update / source observation / source publication /
market resolution status update); compute post-event posterior `p_1`; check book hash still
unresponsive; write `edge = p_1 − a_0 − phi(a_0)` (not placeholder); FOK; no-fill no loss.
Application-grade given info-event feed + executable-quote capture. Latency arbitrage, not prediction.

---

## §4. opening_inertia → opening price exponential relaxation model

Currently live (buy_yes/buy_no, point/finite_range/open_shoulder, Kelly 0.5). Only live strategy
with preliminary settled PnL. Edge = opening prices not yet arbitraged = price-discovery lag.

Mid/ask relaxes after open: `m(t) = p + (m(0) − p)·e^{−λt} + ε_t`. Buy YES at `a(t)`:
`EV(t) = p − a(t) − phi(a(t))`. `p` has estimation error → use calibrated lower bound
`p⁻ = p̂ − z_α·σ_cal`. Provable lower bound `EV⁻(t) = p⁻ − a(t) − phi(a(t))`; apply iff `EV⁻(t)>0`.
Verifiable params = `λ, σ_cal, m(0)−p` (NOT win rate). Edge half-life `t_{1/2} = ln2 / λ`; enter
as early as possible.

**Improvement**: per new market record pre-open posterior `p`; post-open record `m(t)`; estimate
`λ`; trade only `p⁻ − ask − fee > 0` or buy NO `1 − p⁺ − noAsk − fee > 0`. Already has live
execution chain; prioritize upgrade.

---

## §5. center_buy → calibrated multinomial market maker

Currently live (buy_yes, finite_range). Evaluator routes non-shoulder buy_yes to center_buy.
Bins exhaustive: `Σ p_i = 1`, `Y_i = 1{T∈B_i}`, `EV_i = p_i − a_i − phi(a_i)`. Application form
uses multinomial calibration: `p⁻_i = inf{ p_i : p_i in calibrated confidence/conformal set }`.
Condition: `p⁻_i − a_i − phi(a_i) > 0`. Ensemble raw output has bias/dispersion error → needs
postprocessing (heteroscedastic variance, AR error correction) per probabilistic-forecasting lit.

**Improvement**: trade `i* = argmax_i [ p⁻_i − a_i − phi(a_i) ]` (not "max edge" or "posterior >
market"); add multinomial proper-scoring backtest (LogScore = −log p_winner; Brier = Σ(p_i − y_i)²).
If calibration fails, `p⁻` does not exist → no trade (not a conservative rule). Most natural
statistical live edge; upgrade from heuristic posterior to calibrated lower-bound.

---

## §6. center_sell → NO-side calibrated complement + YES/NO parity (two layers)

Registry blocked/IDEA; no evaluator routing. NO payoff `N_i = 1 − Y_i`; buy NO at `b_i`:
`EV_i^NO = 1 − p_i − b_i − phi(b_i)`; use upper bound `EV_i^{NO,−} = 1 − p⁺_i − b_i − phi(b_i)`;
apply iff `> 0`. Same calibration system as center_buy, upper bound instead of lower.

**Stronger — YES/NO parity check**: binary CTF YES+NO pair fully collateralized to $1. If
`a_YES + a_NO < 1` → buy both + merge/redeem pathwise profit `Π = 1 − a_YES − a_NO − fees`. If
`bid_YES + bid_NO > 1` → reverse unwind. CTF supports split/merge/redeem. So center_sell scans
YES/NO parity arbitrage (deterministic) in addition to model-overpriced NO (statistical).
**Two layers: parity arbitrage (deterministic) + calibrated NO buy (statistical).**

---

## §7. shoulder_sell → REFUTED ex ante; reframe as physical impossible-tail capture

Shadow, buy_no, open_shoulder, has blockers. Classifier returns SHOULDER_NO_TRADE_GATE. Original
thesis (retail overprices open shoulder → buy NO) is sign-reversed: `E[p_mkt − p_cal] < 0`, so
`EV_NO = m − p − fee` has no positive edge.

**Reframe — don't predict tail, physically exclude it.** Upper shoulder `B = [u, ∞)`, buy NO
certain iff `T* < u`. If physical upper bound `T* ≤ U_t < u` → `NO(B) = 1`, `Π = 1 − b_NO − phi`.
For daily high `U_t = H_t + Δ_phys⁺(t)`; if `H_t + Δ_phys⁺(t) < u` → upper shoulder YES physically
impossible. Lower shoulder symmetric. `Δ_phys⁺` from station/season/hour empirical physical
envelope or radiative/advection model `Q_{1−ε}(H_T − H_t | station,season,hour,cloud,wind,front)`;
ε=0 → deterministic empirical bound, ε>0 → statistical bound.

**Application**: original shoulder_sell unusable; renamed `shoulder_impossible_tail_capture` is
application-grade. Merge into settlement/Day0-bound series, not retail-bias strategy.

---

## §8. shoulder_buy → nonstationary EVT tail-underpricing (NOT heat-dome hard case)

Blocked/IDEA. Open shoulder = tail event `p_u = Pr(T>u | X)`. Raw ensemble tail prob has
dispersion error in rare tail → nonstationary extreme-value model `Pr(T>u|X) = 1 − F_θ(u|X)`,
where `X` = continuous physical covariates (ensemble mean, ensemble spread, 850mb temp anomaly,
soil moisture / boundary-layer mixing proxy, geostrophic wind / advection, station bias,
season/day-of-year harmonic) — NOT hardcoded regime. Calibrated lower bound `p⁻_u = inf Pr(T>u|X)`;
apply iff `p⁻_u − a_YES − phi(a_YES) > 0`. Conformal-calibrate with realized settlements:
`Pr(Y=1 | p⁻_u ≥ q) ≥ q`.

**Improvement**: drop HEAT_DOME discrete special case; use nonstationary tail model for `p⁻_u`;
conformal calibration; buy YES only when lower-bound EV positive. Application-grade once tail
model ships; not now-live but NOT a failure — correct direction is EVT/conformal long-tail.

---

## §9. imminent_open_capture → short-horizon observation/forecast posterior collapse

Live (buy_yes/buy_no, point/finite_range/open_shoulder, Kelly 0.5). 0–24h new/reopened markets,
price not fully reflecting current forecast/observation. `T* = μ_t + η_t`, `Var(η_t) = σ²(τ)`,
`σ²(τ) ↓ 0 as τ ↓ 0`. Conditions same as center_buy/sell: YES `p⁻_i(t) − a_i − phi > 0`,
NO `1 − p⁺_i(t) − b_i − phi > 0`. Closer to resolution → smaller `σ²(τ)` → tighter bound. NWS API
provides hourly forecast / raw grid / observations / alerts.

**Improvement**: from "imminent open heuristic" to posterior-collapse arbitrage. Inputs: latest
observation, hourly forecast, time-to-resolution, station bias, official source lag. Trade only
bins whose probability interval已收缩. Application-grade. Edge = short-horizon physical uncertainty
collapse faster than market updates.

---

## §10. weather_event_arbitrage → Bayes-factor alert arbitrage

Code: checks alert_source credibility + active_weather_alert presence; edge `_SHADOW_EDGE=0.04`;
external alert feed unwired. Alert as public signal `A`. Bin `B_i` pre-alert odds
`O_i = Pr(B_i)/(1−Pr(B_i))`; likelihood ratio `LR_i = Pr(A|¬B_i)... ` actually
`LR_i = Pr(A|B_i)/Pr(A|¬B_i)`; Bayes `O'_i = O_i·LR_i`, `p'_i = O'_i/(1+O'_i)`. Apply iff
`p'⁻_i − a_i − phi > 0`. `LR_i` estimated historically, NOT guessed by alert type. NWS alerts
endpoint + forecasts + observations → standardized archive + backtest.

**Physics**: extreme-heat warning ↑ odds upper-shoulder/high bins; freeze/extreme-cold ↑ low bins;
thunderstorm/high-wind weak direct edge but may affect late-day high via cloud/outflow.
**Improvement**: wire NWS alert feed; build alert_event_fact; learn
`LR_i(alertType, city, season, leadTime)`; trade by posterior lower bound. Application-grade
statistical strategy but needs alert→bin Bayes-factor table. Placeholder cannot go live.

---

## §11. liquidity_provision_with_heartbeat → adverse-selection model (NOT own fill history)

Code: relies on passive_maker_estimate.expected_fill_probability, threshold 0.30; missing field →
no_trade. Problem: fill probability may derive from Zeus's own past orders (self-reference bias).
High fill probability often = high adverse selection. Correct:
`EV_maker = Pr(F)·[ s_earned − E(Δp|F) ] + rebate`. Maker fee 0; post-only guarantees maker
(post-only would-match → reject). `s_earned = p_fair − q_bid`; adverse selection
`AS = E[p_after − p_before | F]`; buy maker `EV = Pr(F)·(p_fair − q_bid − AS)`; condition
`p⁻_fair − q_bid − AS⁺ > 0`. `Pr(F)` decides volume not sign; sign decided by adverse-selection bound.

**Improvement**: stop estimating fill prob from Zeus venue_commands; estimate
`AS(q,τ) = E[p_{t+τ} − p_t | fill, quoteState]` from full-market CLOB public trade/book data;
post-only orders; quote only when `p⁻ − bid − AS⁺ > 0`. Application-grade but needs full rewrite
to adverse-selection model. Current fill-probability threshold cannot prove positive EV.

---

## §12. cross_market_correlation_hedge → joint-distribution statistical arbitrage (Σ⁻¹e)

Code reads regime_tag_for() / regime_correlation_cache / RegimeCorrelationStore.get(); missing
cache or unknown regime → no_trade; enter edge placeholder `_SHADOW_EDGE=0.02`. regime_correlation_cache
empty, history not replayable. Correlation ≠ alpha; correlation only reduces variance. Alpha = market
price vector `m` inconsistent with joint forecast distribution `P`.

`n` markets; `e = p − m − fee`; `Σ = Cov(Y)`; weights `w`: `μ(w) = wᵀe`, `σ²(w) = wᵀΣw`.
Mean-variance/log-utility first order → `w* ∝ Σ⁻¹e`. Application condition NOT "corr > 0.10" but
`eᵀΣ⁻¹e > transaction cost penalty`. Phase-5 doc: sample corr unstable for n<p → Ledoit-Wolf
shrinkage `Σ_shrunk = (1−δ)S + δD`, `δ* = π/(γn)` clipped to [0,1].

**Improvement**: rewrite as `w* = argmax_w [ wᵀe − (λ/2) wᵀΣ_shrunk w ]`, closed form
`w* = λ⁻¹ Σ_shrunk⁻¹ e`; check `w*ᵀe − (λ/2) w*ᵀΣ_shrunk w* > 0`. Portfolio-theory positive
expectation, not a cap. Application-grade statistical portfolio strategy; current "max corr
threshold" proves correlation not alpha.

---

## §13. Combo: shoulder_buy × weather_event → stronger tail strategy

`Pr(T>u | X, A)` with `X` = ensemble/forecast physics covariates, `A` = NWS alert/extreme signal,
`u` = open shoulder threshold. Bayes factor
`Pr(T>u|X,A)/(1−·) = Pr(T>u|X)/(1−·) · Pr(A|T>u,X)/Pr(A|T≤u,X)`. Apply iff
`p⁻_tail(X,A) − a_YES − phi(a) > 0`. Alert = continuous/discrete covariate, NOT hardcoded
heat-dome. Stronger than standalone shoulder_buy.

## §14. Combo: opening_inertia × stale_quote → opening stale-quote FOK alpha

At open `m_0` unrelaxed, forecast posterior `p` exists; if orderbook hash unchanged and ask `a_0`
still below posterior lower bound: `EV = Pr(F)·(p⁻ − a_0 − phi(a_0))`. FOK → no-fill no position.
Combines opening_inertia prediction edge + stale_quote latency edge into a stronger theorem.

---

## §15. Priority — which strategies reach application grade

| Pri | Strategy | Reframed theory | Status |
|---|---|---|---|
| 1 | settlement_capture | physical/settlement interval theorem | applicable, strengthen |
| 2 | resolution_window_maker | source-known venue-unresolved deterministic payoff | applicable, needs typed settlement wiring |
| 3 | stale_quote_detector | FOK information-delay arbitrage | applicable, needs info-event/posterior wiring |
| 4 | opening_inertia | opening price exponential relaxation | applicable, estimate half-life + lower-bound posterior |
| 5 | center_buy | calibrated multinomial YES EV | applicable, needs conformal/proper-scoring calibration |
| 6 | center_sell | calibrated NO EV + YES/NO parity arb | applicable, needs routing + native NO book |
| 7 | imminent_open_capture | short-horizon posterior collapse | applicable, needs observation/forecast interval |
| 8 | shoulder_buy | nonstationary EVT/conformal tail model | research→applicable, not bare live |
| 9 | weather_event_arbitrage | alert Bayes factor | research→applicable, needs alert evidence table |
| 10 | liquidity_provision_with_heartbeat | adverse-selection maker model | research→applicable, needs exogenous order-flow data |
| 11 | cross_market_correlation_hedge | w*=Σ⁻¹e joint-distribution stat arb | research→applicable, needs fed shrinkage cache |
| 12 | shoulder_sell | ex ante refuted; keep only physical impossible-tail capture | original unusable; rename/merge into settlement-bound series |

(neg_risk_basket = fee-adjusted complete-bin basket arbitrage, deterministic — see math spec §11.4-11.9.)

---

## §16. Two evidence pipelines — do NOT push everything through one promotion gate

Current PromotionReadinessValidator uses Beta win-rate CI `CI_lower > breakeven + cost`. Good for
stochastic edges; wrong for deterministic payoff. Split into two theorem-class pipelines:

**A. Deterministic / physics-bound promotion** — settlement_capture, resolution_window_maker,
stale_quote FOK-latency sub-type, center_sell YES/NO-parity sub-type, neg_risk_basket. Evidence:
`computed_deterministic_profit == realized_deterministic_profit` AND `Σ payoff − Σ cost − Σ fees > 0`.
NOT win-rate CI.

**B. Calibrated stochastic promotion** — opening_inertia, center_buy, center_sell model-NO sub-type,
shoulder_buy, weather_event_arbitrage, liquidity_provision, cross_market_correlation_hedge,
imminent_open_capture. Evidence: `p⁻ − a − fee > 0` (or portfolio `wᵀe − (λ/2)wᵀΣw > 0`), then CI
verification with settled outcomes.

---

## §17. Code-level decision types — add two, stop stuffing everything into p_posterior

Current CandidateDecision is single-leg: side/target_price/target_size_usd/edge/p_posterior. Many
applicable strategies are vector or deterministic-state. Add:

```
DeterministicEdgeDecision:
    outcome: "enter"
    payoff_identity: str
    deterministic_payoff_usd: Decimal
    deterministic_cost_usd: Decimal
    deterministic_profit_usd: Decimal
    proof_inputs_hash: str

VectorEdgeDecision:
    outcome: "enter"
    legs: tuple[LegIntent, ...]
    vector_cost_usd: Decimal
    vector_payoff_usd: Decimal
    vector_profit_usd: Decimal
```

So settlement_capture, resolution_window_maker, neg_risk_basket, YES/NO parity, stale-quote FOK all
write deterministic proof into one execution/evidence path. Real goal: shift Zeus strategy taxonomy
from "all strategies are posterior-vs-price" to two math objects: payoff-identity deterministic
trades and calibrated stochastic trades — so applicable deterministic strategies promote directly,
not dragged through Beta win-rate CI.
