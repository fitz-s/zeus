# Zeus Market & Settlement Reference

Status: canonical durable reference  
Authority rank: reference. Code, manifests, tests, and authority docs win on disagreement.  
See also: `docs/reference/zeus_prediction_market_quant_reference.md`.

---

## 1. Market Hierarchy

A Polymarket weather event exposes a family of binary YES/NO markets over mutually-exclusive weather settlement bins.

```text
Event / condition family
  -> market for bin_1: YES token, NO token
  -> market for bin_2: YES token, NO token
  -> ...
  -> market for bin_n: YES token, NO token
```

Zeus treats the family as one Ω. It may trade one native side, but probability, payoff, selection, exposure, and risk are family-aware.

---

## 2. Settlement Identity

A valid Zeus weather contract must pin:

- city and canonical aliases;
- local settlement date;
- metric (`high` or `low`);
- unit (`C` or `F`);
- settlement source/station/product role;
- rounding/truncation rule;
- bin topology and endpoints;
- venue condition/market/token identity;
- source validity evidence when current facts are required.

Contract/source/settlement truth comes before forecast probability. A forecast value without settlement identity is not a tradable q.

---

## 3. Bin Topology

| Bin type | Settlement set | Hazard |
|---|---|---|
| point | one integer/settlement value | continuous-value intuition |
| finite_range | finite set of integer/settlement values | endpoint off-by-one |
| open_shoulder | unbounded lower or upper tail | treating as finite/symmetric |

All q is probability of settlement preimage:

```text
q_i = P(settlement_transform(Y) ∈ B_i)
```

`src/probability/outcome_space.py` and `src/calibration/emos.py` are the relevant implementation anchors.

---

## 4. Native Side And Negative Risk

YES_i pays on bin i. NO_i pays on all other bins.

NO is native side/basket payoff, not a casual complement execution shortcut. It has its own:

- token id;
- quote and depth;
- route/collateral behavior;
- fill/position identity;
- payoff vector;
- q lower-bound semantics.

Allowed probability-space NO lower bound:

```text
q_lcb_no_i = 1 - q_ucb_yes_i
```

Forbidden:

```text
q_lcb_no_i = 1 - q_lcb_yes_i
```

`architecture/negative_constraints.yaml` defines the narrow maker quote reservation carve-out: a complement may lower a resting maker limit against complete-set mint matching. It may not price q, edge, fill, or size.

---

## 5. Settlement Source Roles

Settlement, Day0 monitoring, historical hourly ingest, and forecast skill are separate source roles. Do not collapse them because names or station codes look similar.

Current per-city/source truth belongs in:

- `docs/operations/current_source_validity.md`;
- `docs/operations/current_data_state.md`;
- source manifests such as `architecture/source_rationale.yaml` and city/source contracts.

Those files must carry freshness/expiry. This reference does not store current source verdicts.

---

## 6. High/Low Track Law

HIGH and LOW are separate metric families. A LOW market is not a high market with sign reversed, and a low settlement history gap is not automatically a data bug. Use metric-specific source, calibration, market topology, settlement evidence, and replay identity.

---

## 7. Settlement Writes

Settlement writes must pass settlement semantics, source provenance, bin topology, and DB ownership. Harvester/settlement paths own settlement records; execution/exit intent does not settle positions.

Canonical settlement/fill/learning boundaries:

```text
venue/chain facts -> command/fill facts -> position events/projection -> settlement outcomes -> redeem/learning
```

---

## 8. Market-Settlement Review Checklist

Before editing source/settlement/market code or docs, prove:

1. local date and metric are correct;
2. bin topology is complete;
3. open shoulders are handled as tails;
4. source role is correct and fresh if current;
5. rounding/truncation rule matches contract/source;
6. high/low data are not mixed;
7. YES/NO token mapping is native-side correct;
8. NO complement shortcut is not used;
9. settlement write path and DB ownership are correct;
10. replay/backtest uses settlement truth and no-hindsight information.
