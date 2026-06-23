# Zeus Strategy Specification

Status: canonical durable strategy reference  
Authority rank: reference. Executable source, tests, manifests, and `docs/authority/**` win on disagreement.  
See also: `docs/reference/zeus_prediction_market_quant_reference.md`.

---

## 1. Strategy Identity

Zeus strategy is family-level, settlement-contract trading. A strategy claim must reduce to one of:

1. **Payoff-identity deterministic edge**: payoff is pathwise positive after executable cost.
2. **Settlement-state deterministic edge**: observed/settled weather state restricts Ω enough to prove payoff.
3. **Calibrated stochastic edge**: conservative probability/payoff lower bound proves positive edge after executable cost.
4. **Lifecycle/venue structural edge**: exit/rebalance/fill-up action improves or protects exposure under current chain/book facts.

Every claim must name the family, bin, native side, executable cost, payoff vector, evidence, and risk behavior.

---

## 2. Current Live Forecast Strategy Path

**Executable current behavior.** With `qkernel_spine_enabled` true, the live family decision path is:

```text
event_reactor_adapter
  -> qkernel_spine_bridge.decide_family_via_spine
  -> FamilyDecisionEngine.decide
  -> selected _CandidateProof or typed no-trade
  -> unchanged reactor submit pipeline
```

The q-kernel selects family/bin/native-side routes from payoff-vector economics, not old scalar trade-score or market-fusion doctrine.

---

## 3. Candidate Admission

A live candidate must pass all relevant gates:

```text
complete family Ω
+ live-eligible predictive distribution
+ q/q-band certification
+ executable side/route/book evidence
+ direction law
+ market coherence
+ q_lcb reliability guard where applicable
+ edge_lcb > 0
+ optimal_delta_u > 0
+ risk/capital/freshness/pre-submit gates
```

Failure should be typed as no-trade/no-submit, not hidden under a zero score.

---

## 4. Direction Law

**Executable current behavior.** Let `m` be the forecast settlement bin: the bin containing the served predictive center after applying the family rounding/settlement rule.

```text
buy_yes/bin_i legal iff i == m
buy_no/bin_i  legal iff i != m and boundary-zone law allows
```

Rules:

- YES is modal/forecast-bin only.
- Non-modal YES is not admitted from tail q.
- NO is native-side harvest against bins not forecast to settle, with boundary-zone restrictions.
- NO lower bound uses conservative band identity, not `1 - q_lcb_yes`.
- The NO empirical OOF reliability license is NO-only and cannot be used for non-modal YES.

Code anchors: `src/strategy/live_inference/direction_law.py`, `src/decision/family_decision_engine.py`.

---

## 5. Selection Objective

**Executable current behavior.** The q-kernel selection order is:

1. enumerate executable route candidates;
2. reject direction-law violations;
3. reject market-incoherent candidates without license;
4. reject non-positive conservative edge;
5. reject non-positive robust marginal utility;
6. choose max robust utility density.

Reference selection score:

```text
utility_density = optimal_delta_u / optimal_stake_usd
```

Tie/secondary order uses total `optimal_delta_u`, `edge_lcb`, and lower cost. Old opportunity-book and scalar-Kelly surfaces may record/display context but must not be treated as the default selector.

---

## 6. Executable Cost And No-Trade Reasons

No live edge exists until executable cost is known for the native side/route. Candidate rejection examples:

- missing native token;
- missing native quote;
- stale executable snapshot;
- partial family topology/substrate;
- no direct executable route for unchanged single-leg submit path;
- predictive distribution not live eligible;
- q_lcb reliability abstain;
- market incoherent;
- direction law rejected;
- no positive edge;
- no positive ΔU;
- pre-submit witness failed;
- risk/collateral/heartbeat/user-channel denied.

The exact reason vocabulary is code-owned. Docs must not invent new live reason strings.

---

## 7. Strategy Taxonomy

Historical human labels such as center-bin buy, shoulder-bin sell, settlement capture, opening inertia, fill-up, shift-bin, and reversal exit are useful only when mapped to the current executable form:

```text
source of edge
+ family/bin/native side
+ payoff vector
+ executable route/cost
+ evidence class
+ admission gate
+ sizing/risk behavior
+ lifecycle action
```

A label alone does not authorize trading.

---

## 8. Legacy Strategy Surfaces

Do not use old papers or draft packet specs as current strategy law. The following are diagnostic/history unless current code proves active use:

- dated replacement final-form docs;
- old single-q regime notes;
- legacy ENS/Platt/market_fusion as live blend;
- `q_lcb_5pct` as independent strategy authority;
- AIFS hard dependency;
- submit-disabled/shadow-only/packet-freeze claims;
- operator-only closeout residue.

Surviving rules from those sources must be promoted into active authority/reference before use.

---

## 9. Strategy-Change Checklist

Before changing strategy code/docs:

1. Read `docs/authority/zeus_current_architecture.md`.
2. Inspect the exact source path and tests.
3. Prove family/bin/native-side semantics.
4. Prove q/q-band and lower-bound coherence.
5. Prove executable side cost and route.
6. Prove direction law and market coherence.
7. Prove sizing/risk behavior changes.
8. Prove no duplicate submit/lifecycle corruption.
9. Update manifests/reference/docs registry if route/authority changes.
10. Run targeted tests and topology checks or report why unavailable.
