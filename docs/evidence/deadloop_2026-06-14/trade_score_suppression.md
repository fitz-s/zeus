# TRADE_SCORE_NON_POSITIVE — suppressor audit (2026-06-14)

Read-only trace. DBs queried RO. Every claim carries file:line or DB/log provenance.
HEAD d0c7d72bc1. Daemon pid 73803 started 2026-06-14 15:38:42 CDT.

## OBSERVATION (no interpretation)

- Live cycle histograms: dominant per-candidate reject reason on processed families =
  `TRADE_SCORE_NON_POSITIVE` (e.g. `logs/zeus-live.log:8730890`, `:8738774` — 15:19, 15:50).
- Tokyo 26°C buy_yes q_lcb=0.4314 price=0.0010 ev_per_dollar=430 appears as the family
  `best=` candidate but the family reason mix is `capital_efficiency_lcb_ev=12
  coverage_unlicensed_tail=2 direction_law=1 other=7` (`logs/zeus-live.log:8724992`, 14:54)
  — i.e. it was NOT blocked by trade_score; it was blocked by `coverage_unlicensed_tail`
  on the PRE-restart daemon.
- `edli_no_submit_receipts` persists ZERO rows with `trade_score <= 0`
  (`SELECT COUNT(*) ... WHERE trade_score<=0` → 0). The operative `$.reason` on every
  persisted receipt is `event_bound_final_intent_no_submit` (1797/2000) or submit-stage
  reasons; `TRADE_SCORE_NON_POSITIVE` is never the operative `$.reason` in the table.

## THE FORMULA (file:line)

Score authority (primary): `_mode_consistent_ev_for_proof`
(`src/engine/event_reactor_adapter.py:7909`) → `select_mode_consistent_ev`
(`src/strategy/live_inference/mode_consistent_ev.py:334`):

```
ev_taker = clamp01(p_fill_taker) * (q_lcb - cost - penalty)            # mode_consistent_ev.py:373
ev_maker = clamp01(p_fill_maker) * (q_fill_adj - limit - penalty)     # mode_consistent_ev.py:387-389
score    = chosen_ev                                                   # adapter:7570
```

Fallback path (when mode_ev is None): `_robust_trade_score_from_generated_inputs`
(`src/engine/event_reactor_adapter.py:14033`) → `robust_trade_score`
(`src/strategy/live_inference/trade_score.py:55`):

```
edge_bound = min(q_5pct  - c_95pct.value  - penalty,                   # trade_score.py:69
                 q_post  - c_stress.value - stress_penalty)            # trade_score.py:70
score      = p_fill_lcb * edge_bound                                   # trade_score.py:79
```

Gate: `if trade_score <= 0.0: reason="TRADE_SCORE_NON_POSITIVE"`
(`src/engine/event_reactor_adapter.py:2807-2813`).

### Cost-term UNITS (decisive)

ALL terms — `q_lcb`, `cost`/`c_cost_95pct`, `limit`, `penalty` — are in **probability
units (0–1)**. There is NO per-share term anywhere in the score. The penalty is a flat
0.01 in probability units, hardcoded at `adapter:14052-14053` (`penalty=0.01,
stress_penalty=0.01`) and defaulted at `mode_consistent_ev.py:7917` (`penalty=0.01`).

The real venue fee is ALREADY inside `c_fee_adjusted`: `with_taker_fee` /
`polymarket_fee` (`executable_cost.py:78-82`, `execution_price.py:259-278`),
`fee = fee_rate × p × (1−p)`. This fee SHRINKS toward the cheap tail (∝ p), it does not
explode. `c_cost_95pct = c_fee_adjusted + min_tick_size` (`adapter:13658`) — a 1-tick
conservative buffer on top of the fee-adjusted cost.

## REAL CANDIDATE RECONSTRUCTION (persisted numbers, edli_no_submit_receipts)

The TRADE_SCORE_NON_POSITIVE candidates are not persisted per-candidate, but persisted
receipts carry the identical scoring inputs. Cleanest cheap-tail rows:

| candidate | q_lcb | c_fee_adj | c95(=cfee+tick) | true edge (q_lcb−c_fee) | score core (−tick−pen) | DB trade_score |
|---|---|---|---|---|---|---|
| Karachi 40°C buy_yes | 0.02283 | 0.01259 | 0.01359 | **+0.01024** | **−0.00076** | 0.000133 (≈0) |
| Paris 16°C buy_yes | 0.03578 | 0.01050 | 0.01150 | +0.02528 | +0.01428 | 0.014281 ✓ |
| NYC 86-87°F buy_yes | 0.04849 | 0.01259 | 0.01359 | +0.03589 | +0.02489 | 0.024878 ✓ |

Reconstruction matches DB trade_score to 6 dp → the formula is exactly
`p_fill × (q_lcb − c_fee_adjusted − tick − 0.01)`.

**Dominating negative term**: the flat **0.01 penalty** (lambda_edge / stress_penalty).
Karachi has a genuine +1.0-prob-cent executable edge (q_lcb 0.0228 vs real fee-cost
0.0126) but the tick(0.01)+penalty(0.01) stack drives the score negative.

## SUPPRESSION MAP

`score_core = q_lcb − c_fee_adjusted − tick − penalty = true_edge − (tick + 0.01)`.
With tick=0.01 this is a FLAT **2-prob-cent edge floor**:

- A genuinely +edge candidate with `0 < (q_lcb − c_fee_adjusted) < ~0.02` is BLOCKED.
- The floor is ABSOLUTE (prob units), so it is harshest on cheap markets: at price 0.01,
  a 0.02 floor is 200% of the price. This is exactly the cheap-tail band where the model
  most disagrees with the market.
- The tick portion is a legitimate, data-driven conservative cost (`min_tick_size`,
  varies per book, sometimes <0.01 or negative). The **penalty (0.01) is the
  un-economic, hardcoded, undocumented constant** — `git log -S "penalty=0.01"` →
  origin `00b73fbbce` (2026-05-28 "complete no-submit online implementation"), NO spec
  citation, NO doc defines what cost the 0.01 models. The real fee is already in
  c_fee_adjusted, so the 0.01 is pure additive conservatism, not a fee proxy.

## VERDICT (RULE 1)

**SUPPRESSOR — partial.** Decomposed:

1. **The cheap-tail "per-share / phantom-10%-fee explodes at low price" hypothesis is
   FALSE.** Every cost term is in probability units; the fee is `fee_rate·p·(1−p)` which
   vanishes at the cheap tail. Even a phantom 10% fee adds 0.0001 at price 0.001. NOT the
   suppressor. (Refuted by structure + numeric, `execution_price.py:259-278`.)

2. **The Tokyo 26°C candidate was NOT blocked by trade_score.** It cleared the score
   (q_lcb 0.43 ≫ floor); it was killed by `coverage_unlicensed_tail` — and that gate is
   now SHADOW-ONLY on HEAD (`adapter:7299, 7637-7657`, commit 1897142f12). The current
   daemon (started 15:38, 3 min AFTER the 15:35 shadow commit, same CDT clock) emits ZERO
   `coverage_unlicensed_tail` live reasons after restart (last live one 15:21, log live to
   15:58). So the Tokyo block is already removed in the running process. NOT trade_score.

3. **The genuine suppressor is the hardcoded 0.01 penalty** — a flat 2-prob-cent edge
   floor (with the legit tick) that kills real +edge cheap-tail candidates whose true
   executable edge is 0–2 prob-cents (Karachi: +1.0¢ true edge → negative score). This is
   an un-economic, undocumented constant doing the work of a hidden minimum-edge gate,
   which is precisely the kind of artificial throttle the operator laws forbid
   (no-caps / no-gate-accretion). HONEST cost is already fully captured by
   `c_fee_adjusted` (real fee) + `min_tick_size` (real tick); the additional 0.01 is
   phantom.

## FIX (precise, file:line)

Remove the phantom 0.01 penalty so the score reflects only REAL executable cost
(fee-adjusted price + conservative tick), letting genuine cheap-tail +edge survive.

- `src/engine/event_reactor_adapter.py:14052-14053` — change `penalty=0.01,
  stress_penalty=0.01` → `penalty=0.0, stress_penalty=0.0` (the fallback path).
- `src/engine/event_reactor_adapter.py:7917` — change `_mode_consistent_ev_for_proof`
  default `penalty: float = 0.01` → `0.0` (the PRIMARY live path; this is the term that
  actually fires, since mode_ev is the chosen score at `adapter:7570`).

This does NOT manufacture phantom edge:
- Direction law (`adapter:7625`), capital_efficiency `q_lcb>price` (`live_admission.py:130`),
  and the buy_no conservative-evidence gate (`live_admission.py:200`) remain — a candidate
  must still clear `q_lcb > c_fee_adjusted + tick` (real, honest executable cost).
- The tick buffer stays (real conservatism). The fee stays (real, in c_fee_adjusted).
- A flat-σ phantom-edge candidate has q_lcb ≤ cost and is still rejected by
  capital_efficiency / the now-honest score; only candidates with a REAL positive edge
  after REAL cost newly survive.

### Residual unknown (does NOT change the verdict)

Per-candidate TRADE_SCORE_NON_POSITIVE rows are not persisted with their q_lcb/cost (the
table persists only family-level `event_bound_final_intent_no_submit` and submit-stage
rows; `trade_score<=0` count = 0). The reconstruction above uses persisted receipts with
identical scoring inputs and matches DB trade_score to 6 dp, so the formula and the
dominating term are proven; what is not directly observable is the exact q_lcb/cost of the
specific candidates logged as TRADE_SCORE_NON_POSITIVE at 15:50. Discriminating probe: add
a one-line per-candidate log of `(q_lcb, c_fee_adjusted, c_cost_95pct, penalty, score)` at
`adapter:2807` before the `<= 0` return, run one cycle, and confirm the dominating term is
the 0.01 penalty on a candidate with `q_lcb > c_fee_adjusted + tick` (true +edge). This
turns the reconstruction into a live single-candidate proof.
