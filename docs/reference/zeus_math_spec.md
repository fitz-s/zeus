# Zeus Math Specification

Status: canonical durable math reference  
Authority rank: reference. Executable code, tests, manifests, and `docs/authority/**` win on disagreement.  
See also: `docs/reference/zeus_prediction_market_quant_reference.md`.

---

## 0. Scope Labels

Each formula below is one of:

- **Executable current behavior** when named code implements it;
- **Reference explanation** when it explains the model class;
- **Ideal target / not asserted implemented** when marked that way.

Never cite this file alone to prove runtime behavior. Inspect the code anchor.

---

## 1. Settlement Probability Over Ω

**Executable current behavior via `src/calibration/emos.py::bin_probability_settlement`.**

A family has outcome space Ω with bins `B_i`. Given predictive random variable `Y` and settlement transform `S(·)`:

```text
q_i = P(S(Y) ∈ B_i)
```

`S` includes unit, rounding/truncation, metric, city/source rule, and settlement support. A continuous forecast mean is not a settlement probability.

---

## 2. Bayesian Precision Fusion

**Executable current behavior via `src/forecast/bayes_precision_fusion.py`.**

The live replacement forecast path consumes persisted model members and produces a fused posterior center/dispersion. Reference form:

```text
x = vector of model forecasts for a city/date/metric/source cycle
Σ = residual covariance / shrinkage covariance estimate
w ∝ Σ⁻¹ 1
mu* = wᵀ x
V*  = fused predictive variance after residual/covariance treatment
```

Exact empirical-Bayes bias, residual windows, shrinkage, fallbacks, and product identity guards live in code and DB ownership manifests. Do not hard-code fitted sigma-scale constants in docs; runtime artifacts such as `state/sigma_scale_fit.json` must be freshly inspected before quoting numeric values.

---

## 3. q Band And Lower-Bound Coherence

**Durable architecture law; executable seams in `src/decision/joint_q.py`, `src/decision/qlcb_reliability_guard.py`, materializer q maps.**

For the same random variable `X`:

```text
LCB(X) <= E[X] <= UCB(X)
```

A live q band must share:

- same family Ω;
- same bin topology hash;
- same settlement transform;
- same source/forecast cycle/dependency hash;
- same side/payoff random variable after transformation.

A lower bound greater than its point estimate is invalid unless the code proves it is a lower bound for a distinct transformed random variable. Do not repair this by clamping in prose; treat it as a certification failure.

YES/NO conservative identity:

```text
q_lcb_yes_i = LCB(P(ω = i))
q_ucb_yes_i = UCB(P(ω = i))
q_no_i      = 1 - q_yes_i
q_lcb_no_i  = 1 - q_ucb_yes_i
q_ucb_no_i  = 1 - q_lcb_yes_i
```

Forbidden live claim:

```text
q_lcb_no_i = 1 - q_lcb_yes_i
```

---

## 4. Payoff Vectors

**Executable current behavior via `src/decision/payoff_vector.py` and `src/strategy/utility_ranker.py`.**

For bin i:

```text
YES_i(ω_j) = 1 if j = i else 0
NO_i(ω_j)  = 0 if j = i else 1
```

A route payoff vector may differ from this primitive when route/collateral/negative-risk mechanics are represented. Inspect the route object and payoff-vector code; do not infer route economics from side label alone.

---

## 5. Edge At Executable Cost

**Executable current behavior.**

Reference candidate economics:

```text
cost      = executable side-specific cost including fee/tick/depth terms as code defines them
point_ev  = q_payoff_point - cost
edge_lcb  = q_payoff_lcb - cost
```

No edge exists at display price, midpoint, stale quote, implied probability, or fabricated complement price. The cost must be executable for the candidate's native side/route.

---

## 6. Robust Utility And Size

**Executable current behavior in q-kernel selection; exact implementation in utility/payoff modules.**

Reference log-utility form:

```text
ΔU(s) = E_q_conservative[log(W + payoff(s) - cost(s) + exposure_state)] - log(W + exposure_state)
```

The q-kernel computes an optimal stake subject to candidate route, depth/cost, current exposure, and risk/capital constraints. Default live selection uses robust utility density:

```text
score = optimal_delta_u / max(optimal_stake_usd, ε)
```

The old scalar `trade_score` is telemetry/legacy surface, not the default q-kernel selector.

---

## 7. Direction Law Math

**Executable current behavior via `src/strategy/live_inference/direction_law.py` and q-kernel selection.**

Let `m` be the settlement bin containing the served predictive center under the family rounding rule.

```text
YES_i admissible iff i = m
NO_i  admissible iff i ≠ m and boundary-zone law allows
```

Empirical OOF q-lcb reliability may license specific NO harvest cases. It does not license non-modal YES.

---

## 8. FDR / Multiple Testing

**Current implementation must be inspected by task.** Zeus historically uses Benjamini-Hochberg/FDR concepts for admission across tested family/bin candidates. Under the q-kernel path, do not assume an old scalar FDR or p-value gate is the terminal selector; inspect `event_reactor_adapter`, q-kernel inputs, and candidate proof fields for the task.

Reference rule: multiple-testing control can suppress candidates, but it cannot replace side-aware q, executable cost, direction law, market coherence, robust ΔU, or risk.

---

## 9. Legacy Math Surfaces

The following are non-default unless the current code path explicitly uses them for the task:

- 51-member ENS Monte Carlo member-counting;
- Extended Platt as live probability authority;
- α-weighted market_fusion as live blend;
- old double-bootstrap `q_lcb_5pct` as independent live gate;
- market-anchor caps or arbitrary haircuts.

They may be retained as diagnostics, baseline references, or archive evidence. They are not current strategy law by placement in old docs.

---

## 10. Validation Checklist For Math Changes

A math change must prove:

1. contract/source/settlement identity is unchanged or deliberately migrated;
2. high/low tracks are not mixed;
3. q and q_lcb/q_ucb refer to the same Ω and random-variable semantics;
4. NO lower bounds are conservative (`1 - q_ucb_yes`, not `1 - q_lcb_yes`);
5. executable cost is native-side/depth/fee/tick aware;
6. lower-bound inversion cannot pass live admission;
7. replay/validation grades against settlement truth and no-hindsight decision-time information;
8. stale legacy documents were not used as present-tense law.
