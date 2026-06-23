# Zeus Risk & Sizing Reference

Status: canonical durable reference  
Authority rank: reference. Code, manifests, tests, and authority docs win on disagreement.  
See also: `docs/reference/zeus_prediction_market_quant_reference.md`.

---

## 1. Risk Is Behavioral

Risk/control that cannot change evaluator, sizing, execution, cancellation, reduce-only, exit, or admission behavior is theater. Zeus risk must actuate.

Risk levels:

| Level | Behavior |
|---|---|
| GREEN | normal admission if all other gates pass |
| YELLOW | no new entries; continue monitoring |
| ORANGE | no new entries; exit only under favorable/policy-authorized conditions |
| RED | protective cancel/sweep/exit behavior per code |
| DATA_DEGRADED | no new entries; preserve monitor/exit/reconciliation where safe |

Exact enum definitions live in `src/riskguard/**`; money-path object/state grammar is registered in `architecture/money_path_objects.yaml`.

---

## 2. DATA_DEGRADED

DATA_DEGRADED is not permission to fabricate inputs. It is the safe posture for missing/stale/partial authority.

Examples:

- stale forecast source cycle;
- missing q/q band;
- partial market substrate;
- stale executable book;
- stale heartbeat/user-channel/venue connectivity;
- missing balance/allowance/collateral evidence;
- unknown chain state;
- stale current-fact pointer.

Behavior: block new entries, continue safe held-position monitoring/exit/reconciliation/settlement lanes where code supports them.

---

## 3. Executable-Cost Sizing

Sizing must use executable side-specific cost. Bare display price, midpoint, implied probability, or old static entry price cannot satisfy corrected Kelly/log-utility authority.

`architecture/negative_constraints.yaml` forbids:

- bare static entry price at Kelly seams;
- implied-probability execution price for Kelly;
- final execution intent carrying posterior/edge/recompute fields;
- held-token exit quotes entering corrected posterior priors.

---

## 4. q-Kernel Robust Utility Sizing

The current q-kernel candidate uses family payoff/exposure utility, not independent scalar per-bin Kelly.

Inputs:

- candidate payoff vector over Ω;
- q/q_lcb/q_ucb or conservative payoff bound;
- native-side executable cost/depth/fee/tick;
- portfolio/family exposure;
- risk/capital stake bound;
- route feasibility.

Reference objective:

```text
choose stake s that maximizes conservative expected log utility ΔU(s)
```

Selection then ranks by robust utility density, with total ΔU and edge/cost as secondary order.

---

## 5. Portfolio Heat And Exposure

Family bins are mutually exclusive. Risk must reason about exposure over Ω rather than treating sibling bins as independent scalar bets. Same-family NO/YES/route exposure can net or compound depending on payoff vector; inspect current strategy modules before changing fill-up, shift-bin, or rebalance behavior.

`src/risk_allocator/**` may block, reduce-only, select safer order type, or cap global allocation. It must not bypass the executor, venue command repo, or lifecycle truth.

---

## 6. Risk Change Checklist

Before changing sizing/risk:

1. prove cost is executable/native-side aware;
2. prove q_lcb/payoff lower bound is coherent;
3. prove family exposure is represented over Ω;
4. prove risk state changes behavior;
5. prove DATA_DEGRADED blocks new entries and does not blind safe exit/monitor lanes;
6. prove final execution intent cannot re-decide;
7. update tests/manifests/reference if state or risk vocabulary changes;
8. do not write current bankroll/capital values into durable docs.
