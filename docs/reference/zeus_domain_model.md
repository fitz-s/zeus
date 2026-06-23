# Zeus Domain Model

Status: canonical durable reference  
Authority rank: reference. Code, manifests, tests, DB/runtime receipts, and `docs/authority/**` win on disagreement.  
See also: `docs/reference/zeus_prediction_market_quant_reference.md`.

---

## 1. Zeus In One Sentence

Zeus is a live-money engine for Polymarket weather prediction-market settlement contracts. It converts weather-source and forecast evidence into settlement-bin probabilities, native-side candidate economics, executable orders, lifecycle truth, settlement/redeem actions, and learning feedback.

It does not trade continuous weather values. It trades native YES/NO tokens on discrete settlement bins.

---

## 2. Core Objects

| Object | Meaning | Primary anchors |
|---|---|---|
| Family | One city/local-date/metric/market-set Ω with mutually-exclusive bins | `src/forecast/types.py`, `src/probability/outcome_space.py`, `src/decision/family_decision_engine.py` |
| Metric | `high` or `low`; separate physical/calibration/settlement tracks | `architecture/fatal_misreads.yaml`, `src/contracts/**` |
| Bin | Point, finite range, or open shoulder settlement set | `src/probability/outcome_space.py`, `src/contracts/settlement_semantics.py` |
| Native side | YES or NO outcome token at the venue | `src/contracts/native_side_candidate.py`, `src/decision/payoff_vector.py` |
| q | Point settlement probability over Ω | `src/calibration/emos.py`, `src/data/replacement_forecast_materializer.py` |
| q_lcb/q_ucb | Conservative probability/payoff band with coherent random-variable semantics | `src/decision/joint_q.py`, `src/decision/qlcb_reliability_guard.py` |
| Executable cost | Side-specific venue price/depth/fee/tick cost curve | `src/contracts/executable_cost_curve.py`, `src/engine/event_reactor_adapter.py` |
| Lifecycle truth | Event/projection/chain-backed position state | `src/state/lifecycle_manager.py`, `src/state/portfolio.py`, `src/state/chain_reconciliation.py` |

---

## 3. Family And Outcome Space Ω

A family is one complete mutually-exclusive settlement outcome space:

```text
family = city + local target date + metric + unit + settlement rule + venue topology
Ω = {bin_1, bin_2, ..., bin_n}
```

Each bin belongs to exactly one family. A candidate may be a YES or NO side of one bin, but its risk and selection must be evaluated against the whole family Ω because bins are mutually exclusive.

A family must not be reconstructed from partial strings when typed topology, family id, condition id, token id, topology hash, or source metadata are available.

---

## 4. Bin Types

| Type | Resolves on | Hazard |
|---|---|---|
| `point` | One integer settlement value | treating label as continuous interval |
| `finite_range` | A finite set of settlement integers | off-by-one endpoint errors |
| `open_shoulder` | Unbounded tail | treating as symmetric finite range |

Settlement-bin probability is probability of the settlement preimage, not density at a continuous value.

---

## 5. YES/NO Native-Side Semantics

YES_i pays one dollar-equivalent if bin i resolves. NO_i pays if any other bin resolves.

```text
YES_i(ω) = 1[ω = i]
NO_i(ω)  = 1[ω != i]
```

NO is a native Polymarket side with its own token, quote, depth, route, fill, and exposure. It is not a casual complement shortcut.

Allowed q-band complement inside the q seam:

```text
q_lcb_no_i = 1 - q_ucb_yes_i
```

Forbidden:

```text
q_lcb_no_i = 1 - q_lcb_yes_i
```

Execution price for NO must come from NO-side executable evidence or a code-authorized maker reservation bound that lowers a resting limit; it cannot be manufactured from YES price.

---

## 6. Source And Settlement Truth

Contract/source/settlement truth comes before forecast probability.

A weather market decision must know:

- local target date, not just UTC timestamp;
- metric high/low;
- settlement station/source/role;
- rounding or truncation rule;
- unit;
- market condition/token topology;
- bin definitions and shoulders.

`docs/operations/current_source_validity.md` and `docs/operations/current_data_state.md` are current-fact pointers for audited present truth. They expire. This reference does not store current source verdicts.

---

## 7. Probability Path Summary

Current executable path:

```text
raw_model_forecasts
  -> bayes_precision_fusion posterior center/dispersion
  -> settlement-preimage bin integration via emos.bin_probability_settlement
  -> q/q_lcb/q_ucb maps in forecast_posteriors
  -> event reactor candidate proofs
  -> qkernel_spine_bridge
  -> FamilyDecisionEngine.decide()
```

Legacy ENS/Platt/market_fusion material is diagnostic/history unless current source/config/manifests prove it is active for the task.

---

## 8. Lifecycle Vocabulary

Canonical phases:

```text
pending_entry -> active -> day0_window -> pending_exit -> economically_closed -> settled
```

Terminals/recovery: `voided`, `quarantined`, `admin_closed`, `unknown` where code declares it.

Exit intent is not closure. Economic close is not settlement. Chain/CLOB truth outranks local cache.

---

## 9. Default Safety Model

When unsure:

- do not trade from partial family topology;
- do not infer NO from YES;
- do not use stale q or stale book;
- do not rely on archive or packet claims as law;
- do not write current facts into durable reference;
- mark unknowns explicitly and fail closed in the money path.
