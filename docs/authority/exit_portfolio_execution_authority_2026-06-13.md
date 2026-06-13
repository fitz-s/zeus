# Exit / Portfolio-Kelly / Dynamic-Execution / Constant-Elimination authority — 2026-06-13

Status: AUTHORITY (clean-room consult-3, GPT Pro, no Zeus context). Raw archived at
`consult3_exit_portfolio_execution_2026-06-13_raw.txt`. Confidence: HIGH for Q1
fixed-position exit formulas, Q2 horse-race Kelly, the stop-loss-not-distinct proof;
MEDIUM-HIGH for Q2 mean-variance/robust; MEDIUM for Q3 (microstructure, replay-gated).

Companion: the hardcode sweep (`docs/evidence/hardcode_sweep/2026-06-13_round2.md`)
lists 35 unfitted decision-relevant constants; this doc is the governing math each
maps to. Sizing/selection math composes with the 2026-06-13 calibration addendum
(q is the calibrated posterior; q_lcb width uses N_eff=3.71).

═══════════════════════════════════════════════════════════════════════════════
## Q1 — EXIT / TAKE-PROFIT / STOP-LOSS (system currently has NO exit capability — BLOCKER)
═══════════════════════════════════════════════════════════════════════════════

### E1. Cost basis is SUNK (the foundational result)
Under expected-log utility, entry cost c does NOT enter the exit decision (proof:
state sufficiency — two positions with identical (W, n, q_t, b_t, T, fees, depth)
take the same optimal action regardless of c). **A "stop-loss because down X%"
rule provably does not exist.** c enters only via taxes/accounting (→ after-tax
proceeds) or bankroll constraints (→ W). The exit signature must carry
(position_units n, wealth_ex_position W, executable bid-depth curve, portfolio
scenarios), NOT cost basis. The current entry-only system has the wrong decision
inputs for exits.

### E2. Sell-all dominance (boxed)
Sell entire position at executable bid iff (wealth-normalized, z=n/W, M(T)=e^{g*T}):
`log(1+z·v·M) > q_t·log(1+z·A1) + (1−q_t)·log(1+z·A0)`
For binary (A1=1, A0=0): `v > ((1+z)^{q_t} − 1)/(z·M)`; small z: `v > q_t·e^{−g*T}`.
Reading: log utility accepts a bid BELOW q_t (selling removes binary variance);
future opportunity g* lowers the bar further. Posterior PARAMETER variance does
NOT affect the fixed-action comparison (EU linear in q); only FUTURE-information
variance has option value (E5).

### E3. Take-profit = sell-dominance in the high-bid region. NO separate "% gain target".
`v_net_bid ≥ q·e^{−g*T}` (small z). g* = expected log-growth/day of capital
released to the future opportunity set — FITTED from replay
(`g*(T) = (1/T)·mean_b log(W_T,b^{+}/W_T,b^{0})`, the value of one extra released
dollar), never hand-set. License: use the g* adjustment only when its CI is
narrow enough that the sell/hold sign is invariant over the interval.

### E4. Stop-loss is NOT distinct (boxed proof). A posterior moving against the
position changes q_t; the optimal response is ALREADY E2 with updated q_t. No
separate rule.

### E5. THE MISCALIBRATION PATHOLOGY (= the Denver/4-loss exit-refusal class).
E2-E4 assume q_t is correct. A miscalibrated posterior says "still winning" while
the market correctly disagrees → the system holds losers. Two principled fixes
(NOT a hand-set stop-loss):
- **E5a Market-as-second-forecaster blend**: fit logistic stacking on resolved
  snapshots `logit π = β0 + β_a·logit(q_agent) + β_m·logit(q_market) + β_τ·x + u_{family/city/bin}`;
  use `q_exit = E[σ(...)]` (NOT raw q_t) in the exit rule. Weights ESTIMATED by
  out-of-sample log score, never averaged by hand. Market-implied prob from
  executable mid/depth, fee/spread adjusted.
- **E5b Anytime-valid alarm**: likelihood-ratio e-process `E_n = Π q_i^{Y_i}(1−q_i)^{1−Y_i} / r_i^{Y_i}(1−r_i)^{1−Y_i}`
  (r = fitted market blend). Under the null "agent q correct", E_n is a nonneg
  martingale, unit expectation (Ville). SUSPEND raw-posterior authority when
  E_n ≥ h* — h* DERIVED from false-alarm vs missed-miscalibration cost (Q4d),
  not fixed at 20 or 1/0.05. This is the same e-process family as the calibration
  addendum A3 sell-the-mode alarm — one anytime-valid monitoring substrate.

### E6. Partial exit (boxed). Sell fraction x maximizing
`U(x) = q·log(W + M·S(x) + n(1−x)·A1) + (1−q)·log(W + M·S(x) + n(1−x)·A0)`,
S(x) = integrated proceeds through the bid-DEPTH curve (concave; never assume top
bid for full size). Closed form for constant bid v, r=vM, z=n/W, binary payout:
r≥1 → x*=1; else interior `x0 = ((1−q)r(1+z) − q(1−r)) / (z·r(1−r))`,
`x* = min(1, max(0, x0))`. Robust version: replace q by `q_exit − z_α·σ` with α
from Q4b. **All-or-nothing exits are wrong** — the FOC gives a fraction.

═══════════════════════════════════════════════════════════════════════════════
## Q2 — PORTFOLIO KELLY (system currently sizes per-candidate — overbets correlated/exclusive)
═══════════════════════════════════════════════════════════════════════════════

### P1. Horse-race Kelly inside a K-bin event (boxed, CLOSED FORM).
Allocate f_k ≥ 0 + cash s, s+Σf_k=1, max Σ_j q_j·log(s + f_j/p_j). Solution:
- not overround (Σp_k ≤ 1): **f_k* = q_k, s*=0**.
- overround: active set `A(s)={k: q_k/p_k > s}`, `s* = (1−Σ_{A}q_k)/(1−Σ_{A}p_k)`,
  `f_k* = (q_k − p_k·s*)_+`.
- no-bet region: `max_k q_k/p_k ≤ 1 → all f_k*=0, s*=1`.
**This REPLACES per-bin "edge > threshold" sizing** — bins COMPETE, the threshold
s* is endogenous. The current capital_efficiency_lcb_ev per-candidate gate +
fixed kelly_multiplier is the wrong structure (each bin sized in isolation).
CORRECTION (build #63, verified 400k random families): the superiority is NOT a
notional inequality `Σ horse-race f ≤ Σ per-candidate f` — that is FALSE (per-bin
Kelly (q−p)/(1−p) UNDER-bets exclusive bins; horse-race can commit several × more
in the overround case). The provable invariant is GROWTH-DOMINANCE:
expected-log-growth(horse-race) ≥ expected-log-growth(any per-candidate sizing),
zero violations over 400k random families. Horse-race fixes BOTH over- and
under-betting because it maximizes the exact joint log-growth objective.

### P2. Cross-family correlation (same-day weather regimes). Second-order Kelly =
QP: `max_f μ^T f − ½ f^T M f`, μ=E[X], M=E[XX^T]. Binary moments:
`Cov(Y_i,Y_j) = R_ij·√(q_i(1−q_i)q_j(1−q_j))`, Fréchet-clipped; within-family
exclusive bins Cov=−q_i q_j (use the exact horse-race, not the QP). LICENSE the QP
only when (i) posterior UB on ρ=|f^T X|<1, (ii) Taylor-remainder UB < objective
margin to the nearest materially different portfolio, (iii) active set invariant
over posterior draws. Else exact Monte-Carlo scenario Kelly.

### P3. Robust (boxed). `max_f min_{π∈Π} Σ_s π_s log(1+X_s^T f)` over a credible
scenario set Π (Sun & Boyd). This IS the posterior-uncertainty correction to full
Kelly — same family as the addendum A1 DR-Kelly, now joint.

### P4. Sequential bankroll reservation (boxed — replaces any hand-set cash reserve).
Reserve = shadow price of liquidity, NOT a fixed fraction. HJB:
`∂_t V + λ(t)·E_m[max_a{G(a;m) + V(t,w−a)} − V] = 0`, terminal V(T,w)=log w. A
current bet must beat `Ψ_t(a;w)=V(t,w)−V(t,w−a)`. Fit opportunity arrival λ(t,city,
type) (nonhomogeneous Poisson/Hawkes from opportunity timestamps incl. skipped)
and the mark distribution m=(edge,depth,ttl,corr-cluster,fees) from logged
candidates. License only when replay CI of V_reserve − V_spend-now excludes 0.

### P5. YES(a)+NO(b) on adjacent bins CAN be growth-optimal (boxed). NO(b)=bet on
union-except-b; YES(a≠b)=tilt inside that union; not redundant unless a payoff is
dominated by cheaper replication. **Run a dominance/arbitrage LP FIRST** (is any
held payoff vector reproducible more cheaply by a basket?) before Kelly.

═══════════════════════════════════════════════════════════════════════════════
## Q3 — DYNAMIC RE-QUOTING (after exit+sizing; replay-gated; extends solved one-shot)
═══════════════════════════════════════════════════════════════════════════════

### X1. Re-decision is EVENT-DRIVEN (book change / posterior jump / time decay),
not fixed-interval. Q4c gives the cadence when polling is unavoidable.
### X2. Cancel-replace: queue position has value (Erlang progress already accrued,
Moallemi-Yuan). Chasing the touch throws away queue priority — hold vs chase has
a formal crossover; do not naively chase.
### X3. Laddering across price levels is NOT generically optimal — valuable only
when capacity / fill covariance / adverse-selection shape `A(δ_d)` supports it.
Replay-licensed feature flag only.
### X4. Dynamic value ≥ one-shot (option value of re-quoting); the gap bounds when
the simple one-shot maker/taker rule (addendum A8) is empirically adequate.

═══════════════════════════════════════════════════════════════════════════════
## Q4 — CONSTANT ELIMINATION (the meta-program governing the 35 F-class hardcodes)
═══════════════════════════════════════════════════════════════════════════════

### K1. Staleness windows ← information-decay process. State-space obs noise grows
with age; the window is where expected decision regret from stale data exceeds the
cost of waiting. Fit the decay (Kalman/OU) per source; derive the window, don't
set hours.
### K2. LCB percentile α (why 5th? — NOT derived). **LCB-Kelly ≡ fractional-Kelly
ONLY for one isolated binary bet, and only with edge-dependent λ(α)=1 − z_α·σ_q/(q̂−p)**
(boxed). NOT equivalent for portfolios / correlation / finite depth / nonlinear
credible sets. Choose `α* = argmax_α Σ_validation log(1 + f_i(q_LCB,i(α))·X_i)`
(prequential rolling / posterior-predictive replay). License α* only if growth-diff
CI vs neighboring α is positive; else use robust-Kelly posterior samples directly.
→ This condemns the fixed `DEFAULT_ALPHA=0.05` (hardcode sweep rank 15) AND the
stepwise ci_width haircut (rank 3) as a non-portfolio double-count.
### K3. Re-evaluation cadence ← jump intensity. `L(Δ)=½·λ_j·r·Δ + C_p/Δ`,
optimal `Δ* = √(2·C_p/(λ_j·r))`. Fit λ_j (edge/book jump intensity) from logs.
### K4. Unified licensing functional (boxed — replaces EVERY n≥X and "2σ" rule).
License a decision/policy change iff `c_+·E[Δ_+] > c_−·E[(−Δ)_+] + c_impl`;
normal approx `μ_Δ/σ_Δ > k*`, **k* SOLVED from the cost equation**
(`c_+(φ(k)+k·Φ(k)) = c_−(φ(k)−k·Φ(−k)) + c_impl/σ`), never habit "2 sigma".
Estimate Δ by paired replay / A-B shadow; σ_Δ by block-bootstrap by day/city/
regime (preserve correlation); c_± from realized false-pos/false-neg loss; c_impl
in log-growth units. → governs calibration maturity tiers (150/50/15), refit
cadences, riskguard alert thresholds, min-fills licenses.

═══════════════════════════════════════════════════════════════════════════════
## IMPLEMENTATION PRIORITY (consult-3 ranking, mapped to Zeus tasks)
═══════════════════════════════════════════════════════════════════════════════
1. **BLOCKER — Q1 exit capability** (Zeus has none). Build `exit_fraction_binary`
   with depth-aware proceeds + position/wealth awareness + blended q_exit (E5a) +
   CI-licensed sell/hold margin + the e-process suspension (E5b). Maps to task #52
   (take-profit lane) + the exit-threshold hardcodes (sweep rank 31). The Denver
   4-loss class is E5 (posterior refused exit while market disagreed).
2. **HIGH — Q2 horse-race Kelly** (closed form) replaces per-candidate sizing →
   directly fixes overbetting across exclusive bins; then same-day correlation QP.
   Maps to kelly_multiplier / capital_efficiency gate (sweep ranks 1-3).
3. **HIGH — constant elimination** (K1/K2/K3/K4): staleness, LCB α, cadences as
   fitted boundaries. Maps to the F kill list.
4. **MEDIUM-HIGH — Q3 dynamic re-quoting** (after 1-2, replay-gated).
5. **MEDIUM — laddering** (flag, replay-licensed).

All new capability lands flag-gated, default = current behavior, shadow-computed;
flag flips are operator-only (standing law). Every fitted constant keeps the K4
licensing functional, never a bare count or habitual percentile.
