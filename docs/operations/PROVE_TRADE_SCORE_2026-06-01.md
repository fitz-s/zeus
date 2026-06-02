# PROVE: robust_trade_score — 3-Axis Adversarial Proof — 2026-06-01

```
Created: 2026-06-01
Last reused or audited: 2026-06-01
Authority basis: EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §2.1-2.3 (robust executable EV theorem);
  trade_score.py:48-52; event_reactor_adapter.py _robust_trade_score_from_generated_inputs (4301-4323),
  _execution_price_from_snapshot (4078-4096), _p_fill_lcb_for_direction (4218-4271);
  market_analysis.py _bootstrap_bin/_bootstrap_bin_no (728-897); evaluator.py find_edges admission (488-491,613-614).
Scope: READ-ONLY adversarial architect proof. NO code edits / git / DB writes. HEAD 6fcd05a69f.
Evidence: state/zeus-world.db no_trade_regret_events (7,011 buy_no rows; window 2026-05-29T17:03Z→2026-06-01T19:35Z),
  numerical reproduction (§4), source @ HEAD.
```

## VERDICT (1 line)

**TRADE_SCORE structure is DESIGN-FAITHFUL and MATH-CORRECT; it is NOT a triple-pessimism stack.**
The live formula reduces to `p_fill_lcb × (min(q_5pct,q_posterior) − c − λ)` — a single cost, a single λ,
a 2-point infimum over belief — which is exactly the spec's `P_fill_LCB · inf_θ E[Y−C−λ | F]` (§2.1).
**The genuine-edge suppression is REAL but lives UPSTREAM of the multiply**: (1) the `q_5pct` CI is
inflated by the bias-split (already ruled `CI_HONESTY_…`), and (2) `p_fill_lcb` is pinned at the 0.05
floor on 68.6% of real-edge rows because this is the **no-submit / public-book-only** path, whose own
spec theorem (§2.1) sets `robust executable TradeScore_LCB = 0` until real fill evidence exists. The
product is correct; two of its three inputs are artifacts of an unfinished path, not a wrong objective.

---

## AXIS 1 — DESIGN-FAITHFULNESS: the EDLI objective IS the spec objective (not a reactor reinvention)

**Spec definition** (`EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md:106-117`, §2.1):
> Robust executable EV = `Eθ[1_F · (Y − C − λ) | I_t]`, robustified as `inf_{θ∈Θ_t} …`.

**Spec layering** (§2.3, :176-181):
> `ActionableTradeScore: P_fill_LCB * robust conditional edge.`

**Live implementation** (`trade_score.py:48-52`):
```python
robust_edge = min(q_5pct − c_95pct − λ_edge,  q_posterior − c_stress − λ_stress)
score       = p_fill_lcb * robust_edge
```

This is a **structural match** to §2.1/§2.3:
- `p_fill_lcb` = the `P_fill_LCB` / `1_F` expectation factor.
- `min(…, …)` = a **2-point discretization of the `inf_{θ∈Θ_t}`** over the belief uncertainty set: the
  worst of {5th-percentile belief `q_5pct`, point belief `q_posterior`}.
- `(q − c − λ)` = the spec's `(Y − C − λ)` conditional edge.

So the product `P_fill_LCB × inf_θ(edge)` is **the designed object, not a stripped/substituted one**. The
multiply is faithful.

**Contrast with the design-faithful `evaluator.py` (the NON-EDLI replay/discovery path):** that path does
NOT use this product. `find_edges` (`market_analysis.py:488-491` buy_yes, `:613-614` buy_no) admits on
`edge = p_posterior − cost > 0` **AND** `ci_lo > 0` (a single CI-lower-positive robustness gate), and
ranks downstream by the **point** `edge.edge`. There is **no `p_fill_lcb` multiplier and no `min(q_5pct,
q_posterior)`** in evaluator.py (grep-confirmed: `robust_trade_score`/`p_fill_lcb` appear ONLY in
`trade_score.py` + `event_reactor_adapter.py`, zero hits in `evaluator.py`).

**Resolution of the apparent divergence:** these are TWO DIFFERENT designed objectives for TWO DIFFERENT
paths — evaluator = "would this edge exist if we could fill at the modeled price" (discovery/replay);
EDLI reactor = "what is the robust EV *including fill risk* on the public book" (live no-submit). The
EDLI objective is the STRICTER, spec-mandated executable one (§2.1 requires the `1_F`/`P_fill` factor).
**The reactor did NOT strip the design; it implemented the spec's harder theorem.** AXIS-1 verdict:
**FAITHFUL.**

---

## AXIS 2 — MATH-CORRECTNESS: NOT a triple-worst-case stack (the brief's premise is REFUTED on structure)

The brief's worry: "multiplying three independently-conservative quantiles (`p_fill_lcb` × `q_5pct−c_95`
× `q_posterior−c_stress`) = triple-pessimism that compresses a +20¢ edge to ±0.4¢."

**This mis-reads the formula.** It is a `min`, not a product, of the two edge terms; and the two terms
share identical cost and λ. The live call (`event_reactor_adapter.py:4313-4322`) passes:
- `c_95pct = c_stress = c_cost_95pct` (the SAME ExecutionPrice value, twice — `:4317-4318`),
- `penalty = stress_penalty = 0.01` (`:4320-4321`).

Therefore, algebraically:
```
min(q_5pct − c − λ,  q_posterior − c − λ)  ≡  min(q_5pct, q_posterior) − c − λ
```
The cost is subtracted **ONCE**, λ **ONCE**. There is no double-cost, no double-λ. And since `q_5pct` is a
true LOWER bound (`q_5pct ≤ q_posterior` is the intended invariant — see `ASYM_LCB_TAIL_…` proving the NO
LCB is the correct tail-flip `1 − q_YES_95pct`), the `min` simply selects `q_5pct`. So:
```
robust_edge  =  q_5pct − c − λ                 (one CI-LCB belief, one cost, one λ)
score        =  p_fill_lcb × (q_5pct − c − λ)
```

This is a **DOUBLE** discount over the design point edge `(q_posterior − c)`, not a triple:
1. **belief discount**: `q_posterior → q_5pct` (use the 5th-percentile of the edge bootstrap) — the §2.1
   `inf_θ`, mathematically correct as a robust lower bound.
2. **fill discount**: `× p_fill_lcb` — the §2.1 `1_F`/`P_fill` factor, mathematically a correct EV weight.
   (λ=0.01 is a small fixed cushion, not a quantile.)

Both discounts are individually the *correct* robustification per spec. **The structure does not
structurally suppress real edge** — a tight-CI, deep-book real edge survives with most of its value
(§4 Case 2: +10¢ point edge → +1.66¢ score on a deep book, 16.6% survival; not annihilation). AXIS-2
verdict: **CORRECT** (the "triple stack" framing is refuted; it is a faithful 2-point `inf` × fill-prob).

### Sub-finding (FDR, raised by brief): the bootstrap p-value is a WEAK guard — confirmed defect, but it is NOT over-rejecting the genuine edges; it is mis-trusted as a calibration guard

BH-FDR math itself is textbook-correct (`selection_family.benjamini_hochberg_mask:250-259`: sort
ascending, largest rank `k` with `p_(k) ≤ q·k/n`). The defect is the **null hypothesis**, not the BH
procedure:

`p_value = mean(bootstrap_edges ≤ 0)` (`market_analysis.py:815,892`), where each
`bootstrap_edge = p_post_b[bin] − c_b` resamples ENS members + Platt params **around the SAME point
distribution**. The null it tests is *"the edge of THIS point belief is ≤ 0"* — it measures the point's
OWN sampling spread, NOT whether the point is calibrated/correct. Reproduced (§4):
- A **confidently-wrong** tight forecast → `p_value = 0.0000` → **PASSES** FDR (would admit wrong-side, the
  Paris case from `DESIGN_CRITIC_…`).
- A **genuine** +7¢ edge with honestly-wide CI → `p_value = 0.294` → **REJECTED** at α=0.1.

So FDR is bidirectionally wrong (under-rejects confident-wrong, over-rejects wide-CI-genuine). **However,
this does NOT make TRADE_SCORE over-reject the live genuine edges** — those die on the `q_5pct` CI term and
the `p_fill` floor (§3), not on FDR. FDR's danger is the OPPOSITE: it must NOT be promoted to "the
false-confidence guard" for any §4.2-style admit-rescope (already flagged HARD-NO in `DESIGN_CRITIC_…`
CRITICAL-2). AXIS-2 sub-verdict: **FDR is a valid BH procedure on an INVALID null** — keep it as a weak
noise filter, never as a calibration/false-confidence floor.

---

## AXIS 3 — VALUE-PROVENANCE: two of the three inputs carry ARTIFACT values (the real suppression)

| Input | Provenance @ HEAD | Domain-valid? | Finding |
|---|---|---|---|
| `q_posterior` (point) | `1 − yes_q`; `yes_q` = normalized **bias-CORRECTED** posterior | YES (MECE, Σ=1 3×) | clean |
| `q_5pct` (CI LCB) | `_bootstrap_bin_no` 5th pct on **UNCORRECTED/cold** members (forecast path `sampler=None`) | **NO** | **INFLATED** (train/serve mean-split, `CI_HONESTY_…` §2) |
| `c_95pct = c_stress` | `execution_price.value + 1 tick` (`_execution_price_from_snapshot:4095`) | YES | **mis-named** — it is point-cost + 1 tick, NOT a 95th-pct cost distribution. Harmless (≈ +1¢). |
| `p_fill_lcb` | Wilson depth-coverage LCB on **visible public book**, floor 0.05, z=1.645 (`:4218-4271`) | YES (honest) but | **floored** — 68.6% of real-edge rows sit at exactly 0.05 |
| `λ_edge = λ_stress` | fixed 0.01 (`:4320-4321`) | YES | appropriate cushion, not co-defective |

Two provenance defects drive the suppression, and **neither is in the multiply structure**:

1. **`q_5pct` is cold-biased** (the dominant kill). The point posterior trades on bias-CORRECTED (warm)
   members; the bootstrap that produces `q_5pct` resamples UNCORRECTED (cold) members → a ~`|eff_bias_c|`°
   mean-split inflates the CI by ~15-19¢ and drags `q_5pct − c − λ` below 0. **Already ruled INFLATED and
   given a fix in `CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md` §4.1 (committed 69bee9b752; verify on
   THIS checkout 6fcd05a69f — see Open Item).** This is a genuine artifact, not a wrong objective.

2. **`p_fill_lcb` is structurally floored to 0.05** because this is the **public-book-only no-submit
   path**. The spec is explicit (§2.1, :119-127): `inf_θ P(F | public visible book only) = 0` ⇒
   `robust executable TradeScore_LCB = 0`. The Wilson floor 0.05 is the codebase's softened version of that
   theorem. So a genuine +0.32 robust edge (Singapore live row: `q_5pct=0.9216, c=0.5945` → robust=+0.317)
   × 0.05 = +0.0159 — survives but at 5% weight. **This is the spec REFUSING to call a public-book quote an
   executable fill** — by design, until FillFeasibilityEvidence (user-channel / FOK-FAK / empirical cohort)
   exists. It is design-intended pessimism, NOT a math error.

AXIS-3 verdict: **q_5pct = artifact (fixable, §4.1); c_95 = benign misnomer; p_fill_lcb = design-intended
floor of an unfinished path.** The objective consuming them is correct; the inputs are the leak.

---

## 4. DECISIVE LIVE REPRODUCTION

Live identity collapses the formula to `score = p_fill_lcb × (q_5pct − c − 0.01)` (c==c_stress, λ==λ).

```
CASE 1 — Tokyo HIGH 23°C buy_no (live):  q_live=0.9498  q_5pct=0.7647  c=0.7597  p_fill=0.05
  POINT edge (q_live − c_fee 0.7497) = +0.2001   (TWENTY cents)
  q_5pct BINDS the min (0.7647 < 0.9498) — the CI-inflated belief
  robust (pre-fill) = 0.7647 − 0.7597 − 0.01 = −0.00500   → already ≤ 0 on the CI term
  × p_fill 0.05     = −0.00025   → TRADE_SCORE_NON_POSITIVE, killed
  +20¢ point edge → −0.0003 score  (−0.12% survives) — but the kill is the CI term, not the multiply.

CASE 2 — genuine +10¢, deep book, HONEST CI:  q_post=0.62 q_5pct=0.55 c=0.52 p_fill=0.83
  robust = min(0.55,0.62) − 0.52 − 0.01 = +0.0200   ;  × 0.83 = +0.01660  → SURVIVES (16.6% of point edge)
  → proves the structure does NOT annihilate a real edge when its inputs are honest.

CASE 3 — genuine +10¢ but THIN book (p_fill@floor):  q_post=0.62 q_5pct=0.58 c=0.52 p_fill=0.05
  robust = +0.0500 ;  × 0.05 = +0.00250  → 2.5% survives — purely the p_fill FLOOR, by spec design.

CASE 4 — Singapore live buy_no:  q_live=0.9947 q_5pct=0.9216 c=0.5945 p_fill=0.050  pt_edge=+0.40
  robust (pre-fill) = 0.9216 − 0.5945 − 0.01 = +0.3171  (HUGE) ;  × 0.05 = +0.0159  → survives, but the
  0.05 public-book floor compresses a +40¢ edge to +1.6¢. The compressor here is p_fill, not the min/CI.

FDR null-hypothesis demo (n=500 bootstrap):
  Confident-WRONG tight point  → p_value = 0.0000 → PASSES FDR(α=0.1)  (admits wrong-side)
  Genuine +7¢ wide-honest-CI   → p_value = 0.2940 → REJECTED            (drops real edge)
  → FDR resamples the SAME point dist; cannot detect a confidently-wrong point. Confirmed.
```

### Live quantification — how many real-edge candidates die at TRADE_SCORE (state/zeus-world.db, buy_no)

Window 2026-05-29T17:03Z → 2026-06-01T19:35Z (~74.5h). buy_no rows = 7,011.

| Metric | Count | Note |
|---|---|---|
| Rejection mix #1: `TRADE_SCORE_NON_POSITIVE` | **6,691** | the dominant single reason |
| buy_no with genuine POINT edge `(q_live − c_fee) > 5¢` | 846 | real edge present |
| …of those killed at `TRADE_SCORE` | **430 (50.8%)** | ≈ **5.8/hr** real-edge kills |
| buy_no with POINT edge > 10¢ | 695 | |
| …killed at `TRADE_SCORE` | **311** | |
| real-edge rows where `q_5pct < q_live` (CI term binds the min) | **681 / 846 (80%)** | CI is the binding pessimist |
| real-edge rows where robust(pre-fill) ≤ 0 (CI/cost/λ kill) | **430 (50.8%)** | the CI term alone kills half |
| real-edge rows where robust(pre-fill) > 0 (point+CI clears) | 416 (49.2%) | survive the CI, then meet p_fill |
| `p_fill_lcb` pinned at floor (≤ 0.06) among real-edge | **68.6%** | public-book floor compresses the survivors |

**Decomposition of the kill cause among the 846 real-edge buy_no:** 50.8% die on the
**`q_5pct` CI / cost / λ** term (the inflated-CI artifact, §3.1); the remaining 49.2% clear that term and
are then compressed by the **`p_fill` floor** (the no-submit design pessimism, §3.2). **Zero of the kills
are attributable to a "triple-multiply" of independent cost/λ worst-cases — that structure does not exist
(c==c_stress, λ==λ).**

---

## 5. DESIGN-vs-LIVE OBJECTIVE COMPARISON

| | Design-faithful path (`evaluator.py`) | EDLI live reactor (`trade_score.py`) | Same objective? |
|---|---|---|---|
| Admission | `edge = p_post − cost > 0` AND `ci_lo > 0` | `p_fill_lcb × (min(q_5pct,q_post) − c − λ) > 0` | **Different, BOTH spec-valid** |
| Robustness | single CI-lower > 0 gate + BH-FDR | `q_5pct` as the `inf_θ` belief + BH-FDR | EDLI is the STRICTER `inf` form |
| Fill risk | NOT priced (assumes modeled-price fill) | `× p_fill_lcb` (spec §2.1 `1_F` factor) | EDLI adds the spec-mandated factor |
| Ranking | point `edge.edge` | `trade_score` (mis-used as a binary gate; not a ranker — see `DESIGN_CRITIC` Design-2) | n/a |
| Spec basis | discovery/replay edge existence | `EDLI_…SPEC §2.1` executable robust EV `P_fill·inf_θ E[Y−C−λ\|F]` | **EDLI == spec; evaluator is the looser sibling** |

The EDLI objective is **NOT a reactor-local reinvention**; it is the spec's §2.1 executable EV theorem,
which is deliberately stricter than evaluator's "edge exists" check because it prices fill risk on the
public book. The divergence is intended path-specialization, not lost design.

---

## 6. CORRECT / DEFECT verdict + the design-faithful objective

**VERDICT: the trade_score OBJECTIVE is CORRECT and DESIGN-FAITHFUL. The green is NOT hiding a wrong-math
suppressor in the score formula. The "no trade opportunity" is driven by TWO INPUT-PROVENANCE issues
upstream of the (correct) multiply:**

- **DEFECT-1 (artifact, fixable):** `q_5pct` is bootstrapped on UNCORRECTED cold members while the point
  uses CORRECTED members → inflated CI binds the `min` in 80% of real-edge rows and kills 50.8% of them.
  Fix = `CI_HONESTY_…` §4.1 (unify the corrected member surface). **Open Item: §4.1 was committed at
  69bee9b752; this audit's HEAD is 6fcd05a69f — VERIFY the fix is present on this checkout before relying
  on it** (the live DB still shows the inflated split in this window, so either the window predates the
  commit on the live runtime or the runtime checkout lags — operator must confirm the live daemon's HEAD).
- **DESIGN-INTENT (not a defect):** `p_fill_lcb` floored at 0.05 on the public book compresses surviving
  edges to ~5% weight. This is the spec's §2.1 theorem (`P_fill | public-book-only → 0`) operating as
  designed. The first executable fill requires **FillFeasibilityEvidence** (user-channel / FOK-FAK /
  empirical cohort), exactly as §2.3 mandates — NOT a relaxation of the score math.

**Design-faithful objective (already what the code computes, restated):**
```
ActionableTradeScore = P_fill_LCB · inf_{θ∈Θ_t} E_θ[ Y − C − λ | F, I_t ]
  with the live 2-point inf = min(q_5pct, q_posterior) − c − λ   (c, λ shared; reduces to q_5pct − c − λ)
```
Keep this objective. Do NOT un-multiply `p_fill_lcb` (that violates §2.1) and do NOT remove the `q_5pct`
inf (that re-admits noise). Instead: (a) fix `q_5pct` provenance (§4.1), and (b) supply real
FillFeasibilityEvidence so `p_fill_lcb` can honestly exceed the floor — the spec-correct path to the first
fill. The certificate/quote plumbing (`EDLI_LIVE_CERTIFICATE_BUILD_FAILED` ≈ 3,255 rejects) is the other
large non-score blocker (`TRADE_SCORE_NORMALIZATION_…` §7) and should be probed in parallel.

---

## 10-LINE VERDICT

1. trade_score = `p_fill_lcb × min(q_5pct−c95−λ, q_posterior−c_stress−λ)` is **DESIGN-FAITHFUL** — it is the spec's §2.1 `P_fill · inf_θ E[Y−C−λ|F]` executable-EV theorem, not a reactor reinvention.
2. **NOT a triple stack:** live passes `c95==c_stress` and `λ_edge==λ_stress`, so the formula reduces to `p_fill_lcb × (min(q_5pct,q_post) − c − λ)` = ONE cost, ONE λ, a 2-point `inf` over belief. Math AXIS = CORRECT.
3. The `min` is a faithful discretization of the spec's `inf_{θ∈Θ}`; `q_5pct` (correct NO tail-flip per `ASYM_LCB_TAIL_…`) binds it normally — a valid robust lower bound, not a fabricated pessimist.
4. The genuine-edge suppression is REAL but lives in the INPUTS, not the structure.
5. DEFECT-1: `q_5pct` bootstrap resamples UNCORRECTED cold members while the point uses CORRECTED members → CI inflated ~15-19¢, binds the min in 80% of real-edge rows, kills 50.8% of them (artifact; fix = `CI_HONESTY_…` §4.1).
6. DESIGN-INTENT (not a defect): `p_fill_lcb` floors at 0.05 on the public book (68.6% of real-edge rows) because the spec §2.1 sets `P_fill|public-book-only → 0`; this path is NO-SUBMIT by design.
7. `c_95` is a misnomer — it is `executable_cost + 1 tick`, not a 95th-pct cost distribution; benign (~+1¢).
8. FDR: BH procedure is textbook-correct, but its null (`mean(bootstrap_edge≤0)` resamples the SAME point) cannot detect a confidently-wrong point — passes wrong-side (p=0.000), rejects wide-CI genuine (p=0.294). Keep as weak noise filter; NEVER as a calibration/false-confidence guard.
9. Live: 6,691 `TRADE_SCORE_NON_POSITIVE`; of 846 buy_no with >5¢ point edge, 430 (≈5.8/hr) die at TRADE_SCORE — 50.8% on the inflated-CI term, the rest compressed by the p_fill floor. Singapore +40¢ edge → +1.6¢ score purely via the 0.05 floor.
10. VERDICT: **CORRECT objective, ARTIFACT inputs.** Keep the `P_fill · inf_θ(edge−λ)` formula; fix `q_5pct` provenance (§4.1, verify on HEAD 6fcd05a69f) and supply real FillFeasibilityEvidence so p_fill exceeds the floor — the spec-mandated route to the first fill. Do NOT un-multiply p_fill or drop the q_5pct inf.
```
