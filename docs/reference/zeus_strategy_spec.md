# Zeus Strategy Mathematics & Execution Authority Specification

**Created:** 2026-05-22  
**Suggested repo destination:** `docs/reference/zeus_strategy_authority_spec.md`  
**Purpose:** Provide a permanent strategy-level authority guide for Zeus: every live, shadow, and blocked strategy is expressed as executable mathematics tied to code surfaces, market microstructure, physical weather constraints, and promotion evidence.  
**Status:** Draft authority packet for operator review. It is written to be committed into the repo after review. It does not override executable code until committed and tested.

---

## 0. Executive thesis

Zeus must not classify strategies by names such as `center_buy` or `stale_quote_detector` alone. A strategy is valid only when its profit claim is reducible to one of three mathematically reviewable forms:

1. **Payoff-identity deterministic edge** — the trade payoff is pathwise positive after executable cost and fees:

   \[
   \Pi(\omega)=X(\omega)-C>0\quad\forall\omega.
   \]

2. **Physical / settlement-state deterministic edge** — a weather or settlement state has reduced the possible outcome set so that a token's payoff is already known:

   \[
   I_t\subseteq B_i\Rightarrow Y_i=1,
   \qquad
   I_t\cap B_i=\varnothing\Rightarrow N_i=1.
   \]

3. **Calibrated stochastic edge** — the payoff is not pathwise deterministic, but a calibrated probability lower or upper bound proves positive expected value after executable cost and fee:

   \[
   p_i^- - a_i - \phi(a_i)>0
   \]

   for YES buys, or

   \[
   1-p_i^+ - b_i - \phi(b_i)>0
   \]

   for NO buys.

The current Zeus repo already contains much of the scaffolding for these proofs: `StrategyProfile`, `EvidenceTier`, `EvidenceReport`, `PromotionReadinessValidator`, `ShoulderStrategyVNext`, `MarketAnalysisVNext`, `ExecutableMarketSnapshotV2`, candidate strategy files, `decision_events`, `no_trade_events`, `shadow_experiments`, and `regret_decompositions`. The current gap is that multiple strategy implementations still write placeholder edge values or return scaffold no-trade classifications instead of producing theorem-backed decision records.

The strongest actionable strategy family is **deterministic/vector payoff arbitrage**, especially `neg_risk_basket` and YES/NO parity baskets. The strongest existing live family is **settlement/observation deterministic capture**, especially `settlement_capture` and the proposed production form of `resolution_window_maker`. The strongest forecast family remains **calibrated finite-bin center trading**. The weakest original thesis is **ex-ante `shoulder_sell` as retail-lottery short-tail**; it is refuted in sign by the supplied shoulder proof and must be replaced by a physical impossible-tail capture theorem.

---

## 1. Authority and conflict resolution

### 1.1 Authority hierarchy used by this document

When this document is used by future agents, resolve conflicts in the following order:

1. **Executable code and tests at current `origin/main`.** Code wins over prose when implementation state disagrees.
2. **Architecture and invariant files** under `architecture/**`, especially strategy registry and DB ownership.
3. **`docs/reference/zeus_math_spec.md`** for unit handling, bins, calibration, FDR, Kelly, and training-pair mathematics.
4. **This document** for strategy-level mathematical theses, proof forms, and implementation direction.
5. **External venue/weather documentation** for market mechanics and source availability.

If this document disagrees with current code, treat the disagreement as a required implementation change or governance inconsistency, not as silent authority. Every future strategy PR should state whether it implements, amends, or supersedes a section of this guide.

### 1.2 External reality axioms

The following external facts are not Zeus preferences; they are market/source mechanics that strategy math must respect.

**Polymarket CTF payoff.** Each binary market has YES and NO tokens. YES redeems $1 pUSD if the event occurs, NO redeems $1 pUSD if it does not occur. Every YES/NO pair is backed by exactly $1 pUSD in the CTF contract. CTF supports split, merge, and redeem operations. Source: Polymarket CTF documentation, `https://docs.polymarket.com/trading/ctf/overview`.

**Negative-risk conversion.** In Polymarket negative-risk markets, outcomes in a multi-outcome event are linked through conversion; a NO token can be converted into YES tokens in other outcomes. Source: same CTF documentation and Polymarket negative-risk docs.

**Fee formula.** Polymarket taker fee is computed at match time as

\[
\phi(C,p,r)=C\cdot r\cdot p(1-p),
\]

where \(C\) is shares, \(p\) is share price, and \(r\) is category taker fee rate. Makers are never charged fees. Weather fee rate is 0.05. Source: Polymarket fees docs, `https://docs.polymarket.com/trading/fees`.

**Order lifecycle.** All Polymarket orders are limit orders. FOK means fill entirely or cancel immediately; FAK means fill available and cancel the rest; post-only orders rest on the book and are rejected if they would cross, guaranteeing maker status. Matched trades settle atomically on-chain: either the entire trade succeeds or nothing happens. Source: Polymarket order lifecycle docs, `https://docs.polymarket.com/concepts/order-lifecycle`.

**NWS data surface.** The NWS API provides access to forecasts, alerts, observations, hourly forecasts, grid data, and alert filtering. Observations may be delayed due to upstream QC processing, and alert endpoints are live/last-seven-days surfaces, not a complete long-term archive. Source: NWS API docs, `https://www.weather.gov/documentation/services-web-api`.

### 1.3 Repo code surfaces verified for this packet

This packet uses the following code/document surfaces as implementation ground truth:

- `architecture/strategy_profile_registry.yaml`: current live/shadow/blocked strategy registry.
- `src/strategy/strategy_profile.py`: `StrategyProfile`, `is_runtime_live`, `live_allowed_keys`, and `_classify_via_registry`.
- `src/contracts/evidence_tier.py`: `EvidenceTier` IntEnum.
- `src/analysis/evidence_report.py`: Beta(2,2) CI, decision/regret/no-trade aggregation.
- `src/analysis/live_readiness_tribunal.py`: canonical `promotion_predicate`.
- `src/analysis/promotion_readiness.py`: read-only validator.
- `src/contracts/shoulder_strategy_vnext.py`: shoulder dataclass and current scaffold classifier.
- `src/analysis/market_analysis_vnext.py`: `MicrostructureMetrics` and current missing field state.
- `src/contracts/executable_market_snapshot_v2.py`: executable snapshot fields.
- `src/contracts/execution_intent.py`: execution order types and depth status vocabulary.
- `src/strategy/candidates/*.py`: candidate strategy current behavior.
- `src/backtest/shadow_replay_harness.py`: current replay scaffold status.
- `docs/reference/zeus_math_spec.md`: rounding, bins, calibration, FDR, Kelly, decision groups.
- `docs/operations/task_2026-05-21_mainline_completion_authority/**`: mainline completion and promotion pipeline package.

---

## 2. Core mathematical primitives

### 2.1 Binary token payoff

For event \(E\), define

\[
Y=\mathbf 1_E,
\qquad
N=1-Y.
\]

A YES token pays \(Y\) dollars at resolution. A NO token pays \(N\). Buying YES at executable ask \(a\) for one share has expected value

\[
EV_{YES}=p-a-\phi(a),
\]

where \(p=\Pr(E\mid\mathcal F_t)\) and \(\phi(a)=r a(1-a)\) for one taker share. Buying NO at executable ask \(b\) has expected value

\[
EV_{NO}=1-p-b-\phi(b).
\]

If Zeus uses synthetic complement pricing, e.g. \(b\approx 1-m_{YES}\), the formula becomes an approximation and must be marked as non-executable unless native NO ask/depth is present. For live promotion, executable native side is preferred.

### 2.2 Multi-bin weather market payoff

Let a market family have bins \(B_1,\ldots,B_K\) that are mutually exclusive and exhaustive over all possible integer settlement values. For final settlement value \(T\), define

\[
Y_i(T)=\mathbf 1_{\{T\in B_i\}}.
\]

The core market invariant is

\[
\sum_{i=1}^K Y_i(T)=1\quad\forall T.
\]

This invariant is explicitly aligned with `docs/reference/zeus_math_spec.md` section on bin coverage and exactly-one-winning-bin behavior.

### 2.3 Complete YES basket theorem

If Zeus buys one YES share for every bin in a complete family, payoff is

\[
\sum_i Y_i(T)=1.
\]

Let executable sweep cost for buying \(q\) shares of YES token \(i\) be \(A_i(q)\), and fee be \(F_i(q)\). Then complete YES basket profit is

\[
\Pi_Y(q)=q-\sum_{i=1}^{K}\{A_i(q)+F_i(q)\}.
\]

If

\[
\Pi_Y(q)>0,
\]

then the trade is pathwise profitable for all settlement outcomes.

### 2.4 Complete NO basket theorem

For every bin \(i\), NO payoff is \(1-Y_i(T)\). Thus a complete NO basket payoff is

\[
\sum_{i=1}^{K}\left(1-Y_i(T)\right)=K-1.
\]

Let executable NO sweep cost be \(B_i(q)\), fee \(G_i(q)\). Then

\[
\Pi_N(q)=(K-1)q-\sum_{i=1}^{K}\{B_i(q)+G_i(q)\}.
\]

If

\[
\Pi_N(q)>0,
\]

then the complete NO basket is pathwise profitable.

### 2.5 YES/NO pair parity theorem

For a binary condition, YES + NO pair is backed by exactly $1 pUSD. Therefore buying YES and NO of the same condition at one share each has deterministic payoff 1:

\[
\Pi_{pair}(q)=q-A_{YES}(q)-A_{NO}(q)-F_{YES}(q)-F_{NO}(q).
\]

If \(\Pi_{pair}(q)>0\), Zeus has a pathwise merge/redeem arbitrage.

### 2.6 Executable orderbook sweep cost

Top ask alone is insufficient. For one token side with orderbook ask levels \((p_\ell,s_\ell)\), cost for \(q\) shares is

\[
A(q)=\sum_{\ell}p_\ell\Delta q_\ell,
\]

where

\[
\Delta q_\ell=\min\left(s_\ell,\ q-\sum_{j<\ell}\Delta q_j\right).
\]

Fee is level-wise:

\[
F(q)=\sum_{\ell}r p_\ell(1-p_\ell)\Delta q_\ell.
\]

The optimizer should evaluate \(q\) over book depth breakpoints, not over arbitrary human caps. Capital constraints and risk caps may exist at portfolio level, but they do not prove edge. Edge is proven by \(\Pi(q)>0\).

### 2.7 FOK and no-fill economics

For information-delay or stale-quote strategies, FOK execution makes no-fill payoff exactly 0. If a strategy submits a FOK buy and the order does not fill, no position exists and strategy PnL is 0. Therefore fill probability scales expected opportunity rate but does not change the sign of edge when no-fill has zero loss:

\[
\mathbb E[R]=\Pr(F)\cdot EV_{filled}.
\]

If \(EV_{filled}>0\) and \(\Pr(F)>0\), then expected value is positive.

### 2.8 Physical interval theorem

For a market bin \(B_i\) and physical possible settlement interval \(I_t=[L_t,U_t]\):

- If \(I_t\subseteq B_i\), YES is deterministic winner:

  \[
  Y_i=1.
  \]

- If \(I_t\cap B_i=\varnothing\), NO is deterministic winner:

  \[
  N_i=1.
  \]

For daily high, a physical interval can be represented as

\[
T^*_{high}\in[H_t,\ H_t+\Delta^+_{phys}(t)],
\]

where \(H_t\) is high-so-far. For daily low:

\[
T^*_{low}\in[L_t-\Delta^-_{phys}(t),\ L_t],
\]

where \(L_t\) is low-so-far. \(\Delta^{\pm}_{phys}\) must be derived from physical or empirical transition envelopes, not from arbitrary caps.

### 2.9 Calibrated probability bounds

For stochastic strategies, Zeus must not trade on naked posterior \(\hat p\). It should compute lower/upper bounds:

\[
p^- = \hat p - u_\alpha,
\qquad
p^+ = \hat p + u_\alpha,
\]

or conformal/credible equivalents satisfying coverage:

\[
\Pr(p\ge p^- )\ge 1-\alpha,
\qquad
\Pr(p\le p^+ )\ge 1-\alpha.
\]

Live edge conditions become:

\[
p^- - a - \phi(a)>0
\]

for YES buys, and

\[
1-p^+ - b - \phi(b)>0
\]

for NO buys.

### 2.10 Portfolio statistical arbitrage

For vector strategies, define price-adjusted edge vector \(e\), covariance matrix \(\Sigma\), and position vector \(w\). Mean and variance are

\[
\mu(w)=w^\top e,
\qquad
\sigma^2(w)=w^\top\Sigma w.
\]

A quadratic utility objective is

\[
J(w)=w^\top e-\frac{\lambda}{2}w^\top\Sigma w.
\]

The unconstrained optimizer is

\[
w^*=\lambda^{-1}\Sigma^{-1}e.
\]

The strategy has a statistical edge only if the maximized objective is positive after transaction costs. Correlation alone never proves alpha.

### 2.11 Evidence tiers and promotion math

For stochastic strategies, the current `EvidenceReport` uses Beta(2,2) posterior on win rate. With \(k\) wins and \(n\) settled decisions,

\[
\theta\mid k,n\sim\mathrm{Beta}(2+k,2+n-k).
\]

Promotion predicate is currently:

\[
\text{tier}_{current}<\text{tier}_{required}
\quad\land\quad
CI_{lower}\ne\varnothing
\quad\land\quad
CI_{lower}>\text{breakeven}+\text{cost\_of\_capital}.
\]

This is correct for stochastic edge cohorts. For deterministic payoff-identity strategies, this CI gate should be supplemented by a deterministic proof report: proof inputs hash, executable cost, settlement payoff identity, and realized reconciliation.

---

## 3. Strategy taxonomy for Zeus

### 3.1 Deterministic payoff-identity strategies

These exploit CTF/basket/merge/redeem identities. They do not need weather forecast probabilities.

- `neg_risk_basket` complete YES/NO family basket.
- YES/NO pair parity baskets for any binary condition.
- Potential family-wide conversion arbitrage in negative-risk markets.

### 3.2 Physical / settlement-state strategies

These exploit the fact that weather observations or source/venue state has already fixed the winning token.

- `settlement_capture`.
- `resolution_window_maker`.
- Reformed `shoulder_sell` as impossible-tail capture.
- Some forms of `imminent_open_capture` when short-horizon physical interval collapses.

### 3.3 Calibrated forecast strategies

These require probability calibration and statistical evidence.

- `center_buy`.
- `center_sell` model-NO side.
- `opening_inertia` when formulated as price-discovery lag against calibrated posterior.
- `imminent_open_capture` when formulated as posterior-collapse edge.
- `shoulder_buy` when formulated as nonstationary tail probability.
- `weather_event_arbitrage` when formulated as alert Bayes factor.

### 3.4 Microstructure strategies

These exploit execution mechanics, latency, adverse selection, or maker/taker asymmetry.

- `stale_quote_detector` as FOK information-delay arbitrage.
- `liquidity_provision_with_heartbeat` as adverse-selection-bounded maker model.
- `opening_inertia` + stale quote hybrid.

### 3.5 Portfolio/vector strategies

These exploit joint distribution and covariance.

- `cross_market_correlation_hedge` as \(\Sigma^{-1}e\) statistical portfolio, not raw correlation threshold.

---

## 4. Current strategy registry and runtime gate implications

`StrategyProfile.is_runtime_live()` should be understood as the money gate:

\[
\text{runtime live}=\{
\text{live\_status}="live"
\land
\text{effective evidence tier}\ge\text{required tier}
\land
\text{promotion blockers}=\varnothing
\}.
\]

This matters because future agents must not treat `live_status: live` alone as sufficient. A strategy can be live-status live but blocked by tier or blockers. Conversely, a strategy can be mathematically proven but still not runtime-live until registry and operator evidence are updated.

Current strategic state from registry and code:

- Runtime live old strategies: `settlement_capture`, `center_buy`, `opening_inertia`, `imminent_open_capture`.
- Shadow/block status new strategies: `shoulder_sell`, `shoulder_buy`, `center_sell`, and six Phase-4 candidates.
- Current `shoulder_strategy_vnext.py` classifier is a scaffold: it returns a `ShoulderStrategyVNext` object with probabilistic fields `nan`, native quotes `None`, `liquidity_gate=False`, and `no_trade_reason=SHOULDER_NO_TRADE_GATE`.
- Current `_classify_via_registry` only routes open-shoulder `buy_no`, effectively serving `shoulder_sell` topology, not `shoulder_buy`.
- Current candidate strategies mostly write placeholder shadow edges and do not yet compute theorem-grade EV.

This document therefore distinguishes **current code behavior** from **target mathematical implementation** for every strategy.

---

## 5. Strategy: `settlement_capture`

### 5.1 Current code state

Registry marks `settlement_capture` live, with `allowed_market_phases: [settlement_day]`, `allowed_directions: [buy_yes]`, and topology `[point, finite_range]`. It is a pre-existing live strategy. The strategy is described as day-0 settlement capture using observation rather than model forecast.

### 5.2 Correct mathematical identity

This is a physical / settlement-state deterministic strategy. Let \(I_t\) be Zeus's physically possible settlement interval at decision time. For bin \(B_i\):

\[
I_t\subseteq B_i\Rightarrow Y_i=1.
\]

Buying YES at executable ask \(a_i\) is pathwise profitable iff

\[
1-a_i-\phi(a_i)>0.
\]

For any bin excluded by physics,

\[
I_t\cap B_i=\varnothing\Rightarrow N_i=1,
\]

and buying NO is profitable iff

\[
1-b_i-\phi(b_i)>0.
\]

### 5.3 Physical construction

For daily high:

\[
I_t^{high}=[H_t,\ H_t+\Delta^+_{phys}(t)].
\]

For daily low:

\[
I_t^{low}=[L_t-\Delta^-_{phys}(t),\ L_t].
\]

`Delta` should be derived from a station/season/hour empirical envelope or physical forecast residual model. It is not a portfolio cap. It is the quantity that turns observations into a theorem.

### 5.4 Implementation target

`settlement_capture` should write deterministic proof fields:

```text
strategy_key=settlement_capture
proof_type=physical_interval_subset
interval_low=L_t
interval_high=U_t
bin_low=l_i
bin_high=u_i
winning_side=YES/NO
executable_ask=a
fee=phi(a)
deterministic_profit=1-a-fee
```

### 5.5 Tests

- Synthetic high-so-far interval inside bin => YES deterministic win.
- Synthetic high-so-far interval excluding bin => NO deterministic win.
- Fee-adjusted profit formula exact.
- Observation timestamps pass settlement coherence verifier.

### 5.6 Application level

`settlement_capture` is application-grade and should remain live, but future improvement should convert it from an implicit observation strategy into explicit interval theorem records.

---

## 6. Strategy: `resolution_window_maker`

### 6.1 Current code state

`resolution_window_maker.py` is a shadow candidate. It checks `umaResolutionStatus`-like strings and writes a shadow decision with placeholder `_SHADOW_EDGE=0.03`. It does not compute target price, target size, deterministic profit, or typed settlement outcome.

### 6.2 Correct mathematical identity

When source truth has identified winning bin \(i^*\) but venue has not converged, payoff is already known:

\[
Y_{i^*}=1,
\qquad
Y_j=0\quad(j\ne i^*).
\]

The strategy should buy the underpriced winning YES or losing NO:

\[
\Pi_{winYES}=1-a_{i^*}-\phi(a_{i^*}),
\]

\[
\Pi_{loseNO,j}=1-b_j-\phi(b_j).
\]

It enters only if max deterministic profit is positive.

### 6.3 Implementation target

Replace raw status strings with typed `SettlementOutcome`. The production state should be:

```text
SOURCE_PUBLISHED_VENUE_UNRESOLVED
```

Then compute executable payoff options. No probability model is needed.

### 6.4 Tests

- Source known / venue unresolved / winning YES ask 0.97 => deterministic profit after fee is positive if fee-adjusted.
- Source known / venue unresolved / losing NO ask 0.96 => deterministic profit after fee.
- Venue already resolved => no resolution-window edge.
- Disputed/source revision => no deterministic theorem; route to operator review.

### 6.5 Application level

Application-grade after typed settlement inputs are wired. It should be promoted on deterministic proof records, not Beta win-rate alone.

---

## 7. Strategy: `center_buy`

### 7.1 Current code state

`center_buy` is live in the registry, allowed for `buy_yes` finite-range bins. In `evaluator.py`, non-shoulder `buy_yes` is classified as `center_buy` after settlement/opening/imminent dispatch handling.

### 7.2 Mathematical model

For finite bin \(B_i\), calibrated probability is

\[
p_i=\Pr(T\in B_i\mid\mathcal F_t).
\]

Buy YES EV:

\[
EV_i=p_i-a_i-\phi(a_i).
\]

Application-grade condition uses lower bound:

\[
p_i^- - a_i - \phi(a_i)>0.
\]

### 7.3 Calibration requirements

This strategy must inherit `zeus_math_spec.md` requirements:

- WMO half-up settlement rounding.
- Complete bin coverage including open outer bins.
- Monte Carlo histogram per snapshot.
- Extended Platt calibration.
- Decision-group bootstrap, not row bootstrap.
- BH FDR over full hypothesis family.

### 7.4 Implementation target

`center_buy` should produce a proof record:

```text
p_raw
p_cal
p_lower_bound
native_yes_ask
fee
edge_lower_bound=p_lower_bound-native_yes_ask-fee
calibration_bucket
n_eff_decision_groups
fdr_family_id
```

### 7.5 Tests

- Calibration lower bound never exceeds point posterior.
- Trade fires only when fee-adjusted lower-bound EV is positive.
- FDR family includes all tested hypotheses, not only prefiltered winners.
- Native ask/depth is used for live price, not mid.

### 7.6 Application level

Application-grade as a calibrated stochastic strategy. It is live today, but should be upgraded from posterior-vs-market to lower-bound-vs-executable-ask.

---

## 8. Strategy: `center_sell`

### 8.1 Current code state

`center_sell` is currently blocked/IDEA in the registry. The evaluator has no production route for non-shoulder `buy_no` finite-range center sell.

### 8.2 Model-NO theorem

For finite bin \(B_i\):

\[
EV^{NO}_i=1-p_i-b_i-\phi(b_i).
\]

Application-grade condition uses upper bound:

\[
1-p_i^+-b_i-\phi(b_i)>0.
\]

### 8.3 Pair parity sub-strategy

For any binary condition, if executable YES and NO asks satisfy

\[
q-A_Y(q)-A_N(q)-F_Y(q)-F_N(q)>0,
\]

then buying YES+NO and merging/redeeming has pathwise profit. This is deterministic and stronger than model-NO edge.

### 8.4 Implementation target

`center_sell` should be split internally into two proof paths:

1. `center_sell_model_no`: calibrated stochastic NO buy.
2. `center_pair_parity`: deterministic YES/NO pair arbitrage.

Same strategy key can remain `center_sell`, but `proof_type` must distinguish them.

### 8.5 Tests

- `buy_no` finite bin routes to `center_sell`.
- NO edge uses native NO ask, not synthetic complement unless marked synthetic.
- Pair parity proof enumerates YES+NO payoff as 1 for all outcomes.
- If pair parity profit positive, no probability estimate is required.

### 8.6 Application level

Application-grade after routing and native NO book support. Pair parity sub-strategy is deterministic; model-NO sub-strategy is calibrated stochastic.

---

## 9. Strategy: `opening_inertia`

### 9.1 Current code state

`opening_inertia` is live, allowed for opening hunt, buy_yes and buy_no, and multiple bin topologies. It is one of the pre-existing live strategies.

### 9.2 Mathematical model

Opening-market price discovery can be modeled as exponential relaxation:

\[
m(t)=p+(m_0-p)e^{-\lambda t}+\epsilon_t,
\]

where \(p\) is calibrated fair probability and \(m(t)\) is market implied probability. Edge at time \(t\):

\[
EV(t)=p-a(t)-\phi(a(t)).
\]

Application condition:

\[
p^- - a(t)-\phi(a(t))>0.
\]

For NO side:

\[
1-p^+ - b(t)-\phi(b(t))>0.
\]

### 9.3 Physical/market interpretation

The parameter \(\lambda\) is the market price-discovery rate. Half-life is

\[
t_{1/2}=\frac{\ln2}{\lambda}.
\]

A valid `opening_inertia` proof must estimate or bound \(\lambda\) from historical opening ticks. Edge decays with time; therefore opening time and quote freshness are part of the theorem, not a rule bolted on later.

### 9.4 Implementation target

Add opening-relaxation proof fields:

```text
market_open_time
decision_time
time_since_open_seconds
p_lower_or_upper_bound
opening_mid_or_ask
lambda_estimate
edge_lower_bound
```

### 9.5 Tests

- Edge decays under relaxation model as time since open increases.
- Opening trade at same price but later time has lower expected edge.
- Native ask/depth is used.
- Forecast lower/upper bounds are used, not naked posterior.

### 9.6 Application level

Application-grade but should be upgraded from heuristic live strategy to relaxation-model strategy.

---

## 10. Strategy: `imminent_open_capture`

### 10.1 Current code state

`imminent_open_capture` is live, allowed for imminent-open-capture mode and buy_yes/buy_no over multiple topologies. It is pre-existing live.

### 10.2 Mathematical model

This strategy should be defined as short-horizon posterior collapse. Let remaining time to resolution be \(\tau\). Forecast uncertainty decreases as \(\tau\to 0\):

\[
T^*=\mu_t+\eta_t,
\qquad
\mathrm{Var}(\eta_t)=\sigma^2(\tau),
\qquad
\sigma^2(\tau)\downarrow 0.
\]

For bin \(B_i\):

\[
p_i(t)=\Pr(T^*\in B_i\mid\mathcal F_t).
\]

YES application condition:

\[
p_i^-(t)-a_i-\phi(a_i)>0.
\]

NO application condition:

\[
1-p_i^+(t)-b_i-\phi(b_i)>0.
\]

### 10.3 Implementation target

This strategy should use observation and short-term forecast data:

```text
hours_to_resolution
latest_observation_time
observation_available_at
hourly_forecast_distribution
posterior_variance_sigma2_tau
p_lower_or_upper_bound
```

### 10.4 Tests

- Posterior variance decreases with time-to-resolution under same data quality.
- If physical interval collapses inside a bin, the strategy routes to deterministic theorem rather than stochastic posterior.
- If only stochastic, lower/upper probability bound is used.

### 10.5 Application level

Application-grade as a bridge between calibrated forecast and physical settlement capture.

---

## 11. Strategy: `shoulder_sell`

### 11.1 Current code state

Registry marks `shoulder_sell` shadow with blockers. `_classify_via_registry` currently routes only open-shoulder `buy_no`, and `classify_shoulder_candidate` returns scaffold no-trade.

### 11.2 Original thesis failure

Original thesis: retail demand overprices shoulder YES, making NO buy positive EV. But supplied edge analysis shows the sign is reversed in the observed window:

\[
\mathbb E[p_{mkt}-p_{cal}]<0.
\]

For `buy_no` shoulder:

\[
EV_{NO}=m_{YES}-p-\phi(1-m_{YES}).
\]

If \(m_{YES}-p<0\), fee only worsens it. The original ex-ante short-tail thesis must not be promoted.

### 11.3 Reformed strategy: impossible-tail capture

`shoulder_sell` can be mathematically revived only as physical impossible-tail capture.

For upper shoulder \(B=[u,\infty)\), if physical upper bound satisfies

\[
U_t<u,
\]

then YES is impossible and NO is deterministic winner:

\[
N_B=1.
\]

Profit:

\[
\Pi=1-b_{NO}-\phi(b_{NO}).
\]

For lower shoulder, analogous physical lower bound excludes the tail.

### 11.4 Implementation target

Rename proof type, not necessarily strategy key:

```text
strategy_key=shoulder_sell
proof_type=physical_impossible_tail
shoulder_side=upper/lower
physical_interval=[L_t,U_t]
shoulder_threshold=u
native_no_ask=b
fee=phi(b)
deterministic_profit=1-b-fee
```

### 11.5 Tests

- Upper shoulder excluded by physical upper interval => NO deterministic win.
- If interval overlaps shoulder, no deterministic theorem exists.
- Original retail-bias path remains blocked/refuted.

### 11.6 Application level

Original `shoulder_sell` is not application-grade. Reformed impossible-tail capture is application-grade and should be treated as settlement-bound strategy.

---

## 12. Strategy: `shoulder_buy`

### 12.1 Current code state

`shoulder_buy` is blocked/IDEA in registry. There is no buy_yes shoulder routing in current `_classify_via_registry`.

### 12.2 Correct mathematical model

For upper shoulder threshold \(u\):

\[
p_u=\Pr(T>u\mid X).
\]

Buy YES edge:

\[
EV=p_u-a-\phi(a).
\]

Application condition:

\[
p_u^- - a - \phi(a)>0.
\]

### 12.3 Required tail model

Open shoulders are rare-event objects, not finite-bin center events. They need nonstationary tail modeling. A valid model can be:

\[
p_u(X)=1-F_{\theta(X)}(u),
\]

where \(X\) includes ensemble mean, ensemble spread, seasonal harmonic, source bias, advection proxy, and alert/forecast covariates. The output must be calibrated with a lower bound.

### 12.4 Integration with weather alerts

If alert signal \(A\) is present, use Bayes factor:

\[
\frac{\Pr(T>u\mid X,A)}{1-\Pr(T>u\mid X,A)}
=
\frac{\Pr(T>u\mid X)}{1-\Pr(T>u\mid X)}
\cdot
\frac{\Pr(A\mid T>u,X)}{\Pr(A\mid T\le u,X)}.
\]

This avoids hardcoded “heat dome only” rules; alert/regime becomes a covariate in tail probability.

### 12.5 Implementation target

```text
strategy_key=shoulder_buy
proof_type=calibrated_tail_yes
p_tail_raw
p_tail_calibrated
p_tail_lower_bound
native_yes_ask
fee
edge_lower_bound=p_tail_lower_bound-native_yes_ask-fee
```

### 12.6 Tests

- `buy_yes` open-high shoulder routes to `shoulder_buy`.
- Tail lower bound is calibrated and never exceeds posterior.
- Trade fires only if lower-bound EV positive.
- Realized shoulder outcomes validate calibration by decision group.

### 12.7 Application level

Not currently live-ready. Can become application-grade after nonstationary tail calibration and native YES execution are wired.

---

## 13. Strategy: `stale_quote_detector`

### 13.1 Current code state

Current code treats `spread_observed_window_ms` as an info-event proxy and `raw_orderbook_hash_transition_delta_ms` as book transition evidence. It writes placeholder `_SHADOW_EDGE=0.02`. Current `MarketAnalysisVNext` sets `spread_observed_window_ms=None`, so production signal is not fed.

### 13.2 Correct mathematical model

The strategy is FOK information-delay arbitrage. Suppose an info event updates fair probability from \(p_0\) to \(p_1\). If stale ask remains \(a_0\), filled trade EV is

\[
EV_{filled}=p_1-a_0-\phi(a_0).
\]

With FOK no-fill payoff 0:

\[
\mathbb E[R]=\Pr(F)EV_{filled}.
\]

If \(EV_{filled}>0\) and \(\Pr(F)>0\), strategy has positive expectation. Fill probability affects frequency, not sign.

### 13.3 Required inputs

- Canonical `InfoEvent`: forecast update, observation update, source publication, venue status update.
- Event timestamp \(t_E\).
- Pre/post posterior \(p_0,p_1\).
- Book hash transition time.
- Native executable stale ask and depth.

### 13.4 Implementation target

```text
proof_type=fok_information_delay
info_event_id
info_event_time
p_after_lower_bound
stale_quote_price
fee
edge_lower_bound=p_after_lower_bound-stale_quote_price-fee
order_type=FOK
```

### 13.5 Tests

- If no fill, no strategy PnL row is created.
- If FOK fill and edge lower bound positive, regret equals realized payoff minus cost.
- Book hash stasis alone does not imply edge; posterior jump is required.

### 13.6 Application level

Application-grade after info-event feed and FOK execution binding are wired.

---

## 14. Strategy: `weather_event_arbitrage`

### 14.1 Current code state

Current code checks `alert_source` and `active_weather_alert` and writes placeholder `_SHADOW_EDGE=0.04`. It does not quantify alert impact on market bins.

### 14.2 Correct mathematical model

An alert is a signal \(A\). For bin \(B_i\), define likelihood ratio:

\[
LR_i(A,X)=\frac{\Pr(A\mid B_i,X)}{\Pr(A\mid \neg B_i,X)}.
\]

Bayes update:

\[
O_i'=O_i\cdot LR_i,
\qquad
p_i'=\frac{O_i'}{1+O_i'}.
\]

Application condition:

\[
p_i^{\prime -}-a_i-\phi(a_i)>0.
\]

### 14.3 Required inputs

- NWS alert feed / active alerts.
- Alert type, severity, certainty, urgency, onset, expiry, zone, city mapping.
- Historical alert-to-temperature-bin outcomes for likelihood ratios.
- Market executable asks.

### 14.4 Implementation target

```text
proof_type=alert_bayes_factor
alert_type
alert_source
alert_issued_at
alert_onset
LR_estimate
LR_confidence_bound
p_prior
p_post_lower_bound
edge_lower_bound
```

### 14.5 Tests

- Alert source absent => no theorem.
- Alert present but LR not fit => no stochastic edge.
- Trade fires only if post-alert lower-bound EV positive.

### 14.6 Application level

Can become application-grade stochastic strategy after alert likelihood model is fit. Current placeholder should remain shadow.

---

## 15. Strategy: `liquidity_provision_with_heartbeat`

### 15.1 Current code state

Current code reads `passive_maker_estimate.expected_fill_probability` and enters if it exceeds 0.30 with depth. This is insufficient and may be self-referential because the estimate can come from Zeus’s own prior order history.

### 15.2 Correct mathematical model

Maker strategy EV must include adverse selection:

\[
EV_{maker}=\Pr(F)\cdot\left[p_{fair}-q_{bid}-AS\right].
\]

where

\[
AS=\mathbb E[p_{after}-p_{before}\mid F]
\]

for a buy-maker fill. Since maker fee is 0, fee does not rescue or hurt edge, but adverse selection is load-bearing.

Application condition:

\[
p_{fair}^- - q_{bid} - AS^+>0.
\]

Fill probability scales throughput:

\[
\mathbb E[R]=\Pr(F)\cdot edge,
\]

but does not determine sign.

### 15.3 Required inputs

- Public CLOB fill/orderbook transition data, not only Zeus’s own orders.
- Post-fill price move distribution.
- Quote state features: spread, queue depth, time since last book transition, forecast update proximity.

### 15.4 Implementation target

```text
proof_type=maker_adverse_selection_bound
p_fair_lower_bound
maker_bid
adverse_selection_upper_bound
maker_fee=0
edge_lower_bound=p_fair_lower_bound-maker_bid-AS_upper
fill_probability
```

### 15.5 Tests

- High fill probability alone does not trigger entry.
- Entry requires adverse-selection-adjusted edge positive.
- Estimate source cannot be only Zeus self-history.

### 15.6 Application level

Research-to-application after adverse selection model. Current fill-probability threshold is not proof-grade.

---

## 16. Strategy: `cross_market_correlation_hedge`

### 16.1 Current code state

Current code resolves city, regime, correlation cache, then enters if maximum off-diagonal correlation magnitude exceeds 0.10. It writes placeholder `_SHADOW_EDGE=0.02`. Correlation cache is expected but not historically populated in current promotion design.

### 16.2 Correct mathematical model

Correlation alone is not alpha. Define edge vector \(e\) across markets and shrunk covariance \(\Sigma\). Optimize:

\[
J(w)=w^\top e-\frac{\lambda}{2}w^\top\Sigma w.
\]

Solution:

\[
w^*=\lambda^{-1}\Sigma^{-1}e.
\]

Application condition:

\[
J(w^*)>0.
\]

If \(e=0\), no amount of correlation creates alpha.

### 16.3 Required inputs

- Calibrated edge vector across city markets.
- Regime-conditional shrunk covariance matrix.
- Common weather-system clustering.
- Executable prices for all vector legs.

### 16.4 Implementation target

```text
proof_type=joint_distribution_stat_arb
edge_vector
shrunk_covariance_hash
weights
expected_return
variance_penalty
objective_value
```

### 16.5 Tests

- Correlation with zero edge vector produces no trade.
- Positive edge vector with positive covariance computes \(w^*=\Sigma^{-1}e\).
- Shrinkage matrix must be positive definite or regularized.

### 16.6 Application level

Application-grade only after cache and edge vector are fed. Current correlation-threshold implementation is not sufficient.

---

## 17. Strategy: `neg_risk_basket`

### 17.1 Current code state

Current code already states the right thesis: family completeness of the negRisk YES token book vs theoretical total. It currently uses `_BASKET_ARB_THRESHOLD = Decimal("0.97")`, checks `neg_risk_family_complete`, `neg_risk_token_count`, and `neg_risk_yes_ask_sum`, then writes a shadow decision.

### 17.2 Correct theorem

For a complete multi-outcome family:

\[
\sum_i Y_i(T)=1.
\]

Complete YES basket profit:

\[
\Pi_Y(q)=q-\sum_i(A_i(q)+F_i(q)).
\]

Complete NO basket profit:

\[
\Pi_N(q)=(K-1)q-\sum_i(B_i(q)+G_i(q)).
\]

If either is positive, the strategy is pathwise profitable.

### 17.3 Implementation target

Delete arbitrary 0.97 threshold. Compute exact executable profit using family-level orderbook sweep.

```text
proof_type=complete_family_basket
family_id
K
side=YES_BASKET/NO_BASKET
q_star
vector_cost
vector_fee
deterministic_payoff
profit
```

### 17.4 Tests

- Enumerate every possible winning outcome; complete YES basket payoff always \(q\).
- Complete NO basket payoff always \((K-1)q\).
- Orderbook sweep cost exact.
- Partial vector fill is not counted as complete arbitrage.

### 17.5 Application level

Highest-priority application-grade candidate after family book wiring.

---

## 18. Pair parity strategy for all binary markets

This strategy is not currently a named registry key, but should be included because it uses the same CTF theorem and can be implemented as a sub-proof under `center_sell`, `neg_risk_basket`, or a new `ctf_parity_arbitrage` key.

### 18.1 Theorem

For a single binary condition:

\[
Y+N=1.
\]

If executable YES and NO asks satisfy

\[
\Pi_{pair}(q)=q-A_Y(q)-A_N(q)-F_Y(q)-F_N(q)>0,
\]

then buying both and merging/redeeming is pathwise profitable.

### 18.2 Implementation target

```text
proof_type=yes_no_pair_parity
condition_id
yes_token_id
no_token_id
q_star
cost_yes
cost_no
fees
profit
```

### 18.3 Application level

Application-grade if executable books can be captured for both sides.

---

## 19. Unified decision record model

Current `CandidateDecision` is too single-leg/predictive for deterministic and vector strategies. It should be extended rather than forcing theorem-grade decisions into `p_posterior` fields.

### 19.1 Single-leg stochastic decision

```python
StochasticEdgeDecision:
    strategy_key: str
    proof_type: str
    side: "buy_yes" | "buy_no"
    token_id: str
    executable_price: Decimal
    fee: Decimal
    p_lower: Optional[Decimal]
    p_upper: Optional[Decimal]
    edge_lower_bound: Decimal
    target_size_usd: Decimal
```

### 19.2 Deterministic single-leg decision

```python
DeterministicEdgeDecision:
    strategy_key: str
    proof_type: str
    side: "buy_yes" | "buy_no"
    token_id: str
    executable_price: Decimal
    fee: Decimal
    deterministic_payoff: Decimal
    deterministic_profit: Decimal
    proof_inputs_hash: str
```

### 19.3 Vector decision

```python
VectorEdgeDecision:
    strategy_key: str
    proof_type: str
    basket_execution_id: str
    legs: tuple[LegIntent, ...]
    q_star: Decimal
    vector_cost: Decimal
    vector_fee: Decimal
    vector_payoff: Decimal
    vector_profit: Decimal
    proof_inputs_hash: str
```

### 19.4 Why this matters

Without these types, deterministic arbitrage strategies are forced to fake `p_posterior` or `_SHADOW_EDGE`. That makes evidence reports meaningless. Strategy proof type should be first-class data.

---

## 20. Promotion evidence by proof class

### 20.1 Stochastic strategies

Use existing `EvidenceReport` / `PromotionReadinessValidator`:

- `decision_events`: denominator.
- `regret_decompositions`: settled win/loss and mean regret.
- Beta(2,2) credible interval.
- `promotion_predicate`: lower CI exceeds breakeven + cost.

Applicable strategies:

- `center_buy`
- `center_sell_model_no`
- `opening_inertia`
- `imminent_open_capture`
- `shoulder_buy`
- `weather_event_arbitrage`
- `liquidity_provision_with_heartbeat`
- `cross_market_correlation_hedge`

### 20.2 Deterministic strategies

Use deterministic proof reports:

- `proof_type`
- `proof_inputs_hash`
- executable cost snapshot
- fee calculation
- payoff identity
- realized reconciliation
- vector fill completeness if applicable

Applicable strategies:

- `neg_risk_basket`
- YES/NO pair parity
- `settlement_capture`
- `resolution_window_maker`
- `shoulder_impossible_tail_capture`
- `stale_quote_detector` FOK latency version when filled edge is deterministic after source-known event

### 20.3 Hybrid strategy evidence

Some strategies can produce both types. Example: `imminent_open_capture` may be stochastic early and deterministic once physical interval collapses. The decision record must store proof type per decision, not just per strategy.

---

## 21. Implementation roadmap

### 21.1 First build: deterministic vector/payoff engine

Implement shared cost/profit engine:

- `src/strategy/payoff_identity.py`
- `src/strategy/orderbook_sweep.py`
- `src/contracts/vector_edge_decision.py`
- `src/analysis/deterministic_edge_report.py`

This unlocks:

- `neg_risk_basket`
- YES/NO pair parity
- deterministic parts of `settlement_capture`
- deterministic parts of `resolution_window_maker`

### 21.2 Second build: physical interval engine

Implement:

- `src/weather/physical_interval.py`
- high/low possible interval construction
- station/season/hour empirical transition envelopes
- source availability timestamps

This upgrades:

- `settlement_capture`
- `imminent_open_capture`
- `shoulder_sell` as impossible-tail capture

### 21.3 Third build: calibrated stochastic lower-bound engine

Implement:

- `p_lower`, `p_upper` from calibration/bootstrap/conformal machinery.
- Proof records for lower-bound EV.

This upgrades:

- `center_buy`
- `center_sell`
- `opening_inertia`
- `shoulder_buy`
- `weather_event_arbitrage`

### 21.4 Fourth build: microstructure latency and maker models

Implement:

- canonical `InfoEvent`
- book hash transition window observer
- FOK stale quote strategy
- adverse selection estimator

This upgrades:

- `stale_quote_detector`
- `liquidity_provision_with_heartbeat`
- opening/stale hybrid

### 21.5 Fifth build: joint distribution portfolio engine

Implement:

- fed `regime_correlation_cache`
- shrunk covariance matrix
- \(\Sigma^{-1}e\) portfolio optimizer

This upgrades:

- `cross_market_correlation_hedge`

---

## 22. Strategy verdict matrix

| Strategy | Current status | Correct proof class | Application verdict | Required transformation |
|---|---:|---|---|---|
| `settlement_capture` | live | physical deterministic | application-grade | explicit interval theorem records |
| `center_buy` | live | calibrated stochastic | application-grade | use lower-bound EV vs native ask |
| `opening_inertia` | live | calibrated stochastic + microstructure relaxation | application-grade | estimate relaxation half-life and lower-bound edge |
| `imminent_open_capture` | live | short-horizon posterior collapse / physical interval | application-grade | add time-to-resolution posterior variance and interval collapse |
| `shoulder_sell` | shadow | original thesis refuted; physical impossible-tail capture only | original not usable; reformed usable | block retail-bias path; implement physical tail exclusion |
| `shoulder_buy` | blocked | nonstationary tail stochastic | research-to-application | EVT/conformal tail lower bound and native YES ask |
| `center_sell` | blocked | calibrated stochastic + pair parity deterministic | application-grade after routing | route buy_no finite bins; add native NO and parity proof |
| `stale_quote_detector` | shadow | FOK information-delay microstructure | application-grade after info feed | canonical InfoEvent and post-event posterior |
| `weather_event_arbitrage` | shadow | alert Bayes factor stochastic | research-to-application | NWS alert likelihood ratios |
| `resolution_window_maker` | shadow | source-known deterministic | application-grade after typed settlement | typed SettlementOutcome and payoff computation |
| `liquidity_provision_with_heartbeat` | shadow | maker adverse-selection model | research-to-application | external adverse selection estimator |
| `cross_market_correlation_hedge` | shadow | joint-distribution portfolio | research-to-application | edge vector + shrunk covariance optimizer |
| `neg_risk_basket` | shadow | complete family deterministic basket | highest-priority application-grade | family orderbook sweep and vector execution |

### 22.1 Directive-vs-code contradictions (audit findings)

These are places where the directive's *description of the current code* disagrees
with the actual code on `feat-strategy-spec-20260522` (verified by grep + read,
2026-05-22). They are distinct from the reframe-proposals throughout §5–§17, which
are intended *changes* the directive requests (and therefore not contradictions).
Only two genuine factual mismatches were found; the directive's current-state
descriptions are otherwise accurate (`_SHADOW_EDGE` placeholder values, registry
statuses, and candidate gate logic all match).

**C-1 — `settlement_capture` is two strategies in code, not one.** Directive §1
treats `settlement_capture` as a single strategy with one interval-state theorem.
The evaluator splits HIGH settlement-day `buy_yes` edges into two distinct strategy
keys via `_day0_high_truth_classification_for_edge`:
`settlement_capture` when the bin is observation-locked, and `day0_nowcast_entry`
when it is NOT locked (`src/engine/evaluator.py:2216-2219` in `_edge_source_for`;
`2236-2240` in `_strategy_key_for`; classifier at `evaluator.py:2284`).
`day0_nowcast_entry` is a registered strategy
(`architecture/strategy_profile_registry.yaml:143`, `live_status: shadow`,
`evidence_tier: REPLAY_PASS`) that the directive never names. The interval theorem
(`I_t ⊆ B_i` vs `I_t ∩ B_i = ∅`) maps onto the *observation-locked* half only; the
*unlocked* half is forecast-upside (`day0_nowcast_entry`), which is a calibrated
stochastic edge (§3.3 class), NOT a physical deterministic one (§3.2 class). A
future agent reading §5 must know the live `settlement_capture` path already
excludes the unlocked forecast edges that §1's theorem appears to cover.

**C-2 — `imminent_open_capture` strategy key resolves to `opening_inertia`.**
Directive §9 (and §10 of this doc) treats `imminent_open_capture` as a distinct
posterior-collapse strategy, and the registry carries a distinct profile for it
(`strategy_profile_registry.yaml:280`, `live_status: live`). But in the evaluator
the *edge-source label* and the *strategy key* diverge for the
`IMMINENT_OPEN_CAPTURE` discovery mode: `_edge_source_for` returns
`"imminent_open_capture"` (`src/engine/evaluator.py:2222-2223`), while both
`_strategy_key_for` (`evaluator.py:2243-2244`) and `_strategy_key_for_hypothesis`
(`evaluator.py:2267-2268`) return `"opening_inertia"` for that same mode. So trades
fired under imminent-open discovery are attributed to `opening_inertia` for
strategy-key purposes (Kelly multiplier, evidence aggregation, promotion), even
though their edge source is logged as `imminent_open_capture`. This is a genuine
code-internal divergence the directive's clean §9 framing does not surface; any
evidence/promotion analysis keyed on `strategy_key` will under-count
`imminent_open_capture` decisions and contaminate `opening_inertia`'s cohort.

---

## 23. Required tests and antibodies

### 23.1 Payoff identity tests

- Complete YES family payoff is always \(q\).
- Complete NO family payoff is always \((K-1)q\).
- YES/NO pair payoff is always \(q\).
- Fee-adjusted profit uses official taker formula.
- Maker paths have zero Polymarket fee.

### 23.2 Physical interval tests

- Interval subset implies deterministic YES.
- Interval disjoint implies deterministic NO.
- Ambiguous interval does not produce deterministic proof.
- Observation/source timestamps are coherent.

### 23.3 Stochastic calibration tests

- Lower bound never exceeds point posterior.
- Upper bound never below point posterior.
- Trade fires only if lower-bound or upper-bound EV is positive after fee.
- Bootstrap resamples decision groups, not rows.
- FDR family includes all tested hypotheses.

### 23.4 Microstructure tests

- FOK no-fill creates no strategy loss.
- Filled stale quote uses post-event posterior, not stale pre-event posterior.
- Post-only order cannot cross spread.
- Maker strategy requires adverse-selection-adjusted edge, not fill probability alone.

### 23.5 Promotion tests

- Deterministic strategy can produce deterministic proof report without Beta CI.
- Stochastic strategy still uses Beta CI and tribunal predicate.
- `is_runtime_live()` remains false if blockers exist.
- Operator reference is required for live-tier crossings.

---

## 24. Guidance for future agents

1. Do not introduce a strategy-specific cap as proof of edge. Caps limit loss; they do not create positive EV.
2. Do not treat placeholder `_SHADOW_EDGE` as evidence.
3. Do not treat correlation as alpha. Correlation only defines covariance; alpha requires edge vector.
4. Do not use synthetic complement prices as executable truth unless explicitly marked and reconciled.
5. Do not let deterministic strategies be evaluated only by stochastic win-rate CI.
6. Do not allow `no_trade_reason=None` to mean “good” unless the proof fields are populated.
7. Do not infer a live promotion from `live_status` alone. Runtime liveness requires status, tier, and no blockers.
8. Do not merge new strategy code without a proof type and theorem statement.
9. Every strategy decision should be explainable by one of: payoff identity, physical interval, calibrated stochastic lower-bound, or vector portfolio objective.

---

## 25. Minimal final architecture

The future Zeus strategy engine should look like this:

```text
Market / source inputs
  ├── CTF token identities and orderbooks
  ├── weather observations and physical intervals
  ├── forecast ensemble and calibration bounds
  ├── alerts / info events
  └── cross-market residual covariance

Proof engines
  ├── payoff_identity_engine
  ├── physical_interval_engine
  ├── calibrated_probability_engine
  ├── microstructure_latency_engine
  └── covariance_portfolio_engine

Strategy adapters
  ├── settlement_capture
  ├── center_buy / center_sell
  ├── opening_inertia
  ├── imminent_open_capture
  ├── shoulder_buy / shoulder_impossible_tail_capture
  ├── stale_quote_detector
  ├── weather_event_arbitrage
  ├── resolution_window_maker
  ├── liquidity_provision_with_heartbeat
  ├── cross_market_correlation_hedge
  └── neg_risk_basket

Decision records
  ├── deterministic_edge_decision
  ├── stochastic_edge_decision
  └── vector_edge_decision

Evidence
  ├── deterministic_edge_report
  ├── evidence_report / tribunal / validator
  └── settlement_capture_verifier
```

This architecture preserves Zeus's complexity while making it reviewable. Future agents should not need to guess what a strategy “means”; they should identify its proof class, verify its theorem inputs, and then inspect the corresponding proof record.

---

## 26. Closing statement

The central mistake to avoid is treating all strategies as forecast edges. Zeus now contains three qualitatively different alpha sources:

1. **Market-structure theorems** from CTF payoff identities.
2. **Physical/settlement theorems** from weather observations and source/venue resolution states.
3. **Statistical forecast theorems** from calibrated probability bounds.

Each strategy must be promoted only through the evidence path appropriate to its theorem. `neg_risk_basket`, pair parity, `settlement_capture`, and `resolution_window_maker` are not “high win-rate models”; they are deterministic payoff systems. `center_buy`, `opening_inertia`, `imminent_open_capture`, `shoulder_buy`, `weather_event_arbitrage`, maker heartbeat, and correlation hedge are statistical or microstructure systems and must carry calibrated lower-bound proofs.

This document is the strategy-level mathematical authority guide that future Zeus agents should use before modifying strategy code, interpreting promotion reports, or proposing live deployment.
