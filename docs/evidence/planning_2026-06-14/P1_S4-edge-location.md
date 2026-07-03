# P1_S4 — Edge-Location Strategy: Where Is the Real Correct-Bin Alpha for the $1,162 Book?

**Date:** 2026-06-14
**Lens:** Market-structure / edge-location strategist (fresh — prior solution-framing discarded).
**Mode:** PLAN-MAKING. Read-only over DBs (`?mode=ro`). No production edits, no deploy.
**Mandate:** Empirically locate where the model's correct-bin belief honestly beats the market out-of-sample after cost. Decide where to fish and where NOT to. Restore the mid-band flow only if it is real.

**One-line verdict:** *The cheap-tail "alpha" the system has been trying to unblock for 100 patches does not exist — it is the model's own tail over-confidence, refuted by settlement at 0/72. The buy_no favorite lane is net-negative after cost. The ONE place the model honestly beats the market is the **near-center ring bin (the `exact` bin nearest the forecast), where the model is mildly UNDER-confident (q≈0.093 vs realized 0.108)** — a thin, real, mid-priced edge that the current architecture cannot reach because it is structured to fish the penny tail. The plan is to **stop fishing the tail, calibrate the tail down to honesty, and re-aim the whole pipeline at the ring bin.***

---

## 0. What I did (provenance of every number below)

I joined **stored model belief at decision time** (`edli_no_submit_receipts.receipt_json`, which carries all 22 evaluated candidate bins per family with `q_posterior`, `q_lcb_5pct`, `execution_price`, `q_lcb_calibration_source`, `same_bin_yes_posterior`, `bin_label`, `city`, `metric`) against **realized settlement** (`zeus-forecasts.db.settlement_outcomes`, `authority='VERIFIED'`, `winning_bin` + `settlement_value`), over the receipt window **2026-05-31 → 2026-06-12** intersected with settled dates **2026-05-25 → 2026-06-13**.

- Receipts scanned: **62,874**. Distinct settleable `(city, target_date, metric, bin_kind, lo, hi, direction)` observations after taking the latest eval per instrument: **427** (435 in YES-view). Scripts: `/tmp/edge_map2.py`, `/tmp/edge_map3.py`, `/tmp/edge_map4.py`, `/tmp/supply.py` (logic reproduced in §11 so it can be re-run).
- The `settlements` table in `zeus-world.db` is **empty (0 rows)**; settlement truth lives only in `zeus-forecasts.db.settlement_outcomes` (7,010 VERIFIED rows). Any future backtester must read settlement from there.
- The bin question text (`"Will the highest temperature in Karachi be 40°C on June 12?"`, `"... be 29°C or higher ..."`, `"... be 25°C or below ..."`, `"... be 72-73°F ..."`) parses cleanly into `(city, date, metric, kind∈{exact,range,or_higher,or_below}, lo, hi)`; `bin_wins` uses `settlement_value` with a ±0.5 half-step tolerance matching the settlement integrator's `half_step` convention (`replacement_final_form §1e`, `emos.bin_probability_settlement` half_step=0.5 for precision=1).

**Caveat on n:** the window is short (≈2 weeks) and the settled-and-evaluated intersection is ~430 instruments. These numbers are directionally decisive (the signs are large and consistent with the independent `sigma_scale_fit.json` calibration fit), but the plan's §6 verification rebuilds this as a continuous, expanding out-of-sample monitor rather than a one-shot claim. I treat the current numbers as a **strong lead that converges with two independent sources** (the σ-fit and the diagnosis), not as a final settled fact.

---

## 1. The empirical edge map (the core deliverable)

### 1a. Out-of-sample realized win-rate by direction × price-band

One observation per settleable instrument (latest eval), joined to settlement:

| dir | price-band | n | realized win% | mean q | mean q_lcb | mean price | of which q_lcb>price: n | their win% |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| buy_yes | <0.05 | 72 | **0.0%** | 0.07 | 0.03 | 0.01 | 34 | **0.0%** |
| buy_yes | 0.05–0.2 | 25 | 16.0% | 0.15 | 0.06 | 0.12 | 4 | 25.0% |
| buy_yes | 0.2–0.4 | 17 | 23.5% | 0.13 | 0.04 | 0.33 | 0 | — |
| buy_yes | 0.4–0.6 | 4 | 25.0% | 0.12 | 0.04 | 0.43 | 0 | — |
| buy_no | 0.4–0.6 | 6 | 33.3% | 0.88 | 0.80 | 0.58 | 6 | 33.3% |
| buy_no | 0.6–0.8 | 65 | 64.6% | 0.88 | 0.74 | 0.70 | 55 | 63.6% |
| buy_no | ≥0.8 | 238 | 97.5% | 0.91 | 0.74 | 0.97 | 16 | 93.8% |

**Reading:** The cheap buy_yes longshot tail (`<0.05`, n=72) settled YES **zero times**. The 34 of those that passed the honest `q_lcb>price` test *also* settled zero times. The model said "edge" on the penny tail and was wrong every single time it settled.

### 1b. The cheap buy_yes tail, broken out by the model's own confidence

| model q_point bucket | n | realized YES rate | mean price | mean q |
|---|---:|---:|---:|---:|
| [0.00, 0.02) | 19 | 0.00% | 0.0038 | 0.0064 |
| [0.02, 0.05) | 11 | 0.00% | 0.0057 | 0.0365 |
| [0.05, 0.10) | 22 | 0.00% | 0.0118 | 0.0691 |
| [0.10, 0.30) | 19 | 0.00% | 0.0119 | 0.1459 |
| [0.30, 1.01) | 1 | 0.00% | 0.0033 | 0.4960 |
| **overall** | **72** | **0.00%** | **0.0087** | — |

The harder the model "disagrees" on a cheap bin (q up to 0.15–0.50 vs price ~0.01), the more confidently it is **wrong**. This is the signature of calibration error masquerading as alpha, not suppressed alpha.

### 1c. Model reliability (YES-space, every distinct settled bin, n=435)

| model q_yes bucket | n | mean q | realized | verdict |
|---|---:|---:|---:|---|
| [0.00, 0.02) | 68 | 0.005 | 0.029 | under-conf |
| [0.02, 0.05) | 63 | 0.035 | 0.016 | **OVER-conf (2.2×)** |
| [0.05, 0.10) | 133 | 0.076 | 0.053 | **OVER-conf (1.4×)** |
| [0.10, 0.20) | 141 | 0.130 | 0.163 | **under-conf (the live edge)** |
| [0.20, 0.40) | 29 | 0.275 | 0.138 | OVER-conf (2.0×) |
| [0.40, 0.60) | 1 | 0.418 | 0.000 | (n=1, ignore) |

### 1d. By bin KIND — the decisive cut

| bin kind | n | mean q | realized | verdict |
|---|---:|---:|---:|---|
| `exact` (ring/near-center) | 333 | 0.093 | **0.108** | **UNDER-confident — model honestly beats market here** |
| `or_higher` (hot tail) | 51 | 0.102 | 0.020 | OVER-confident 5× — hallucinated mass |
| `or_below` (cold tail) | 51 | 0.065 | 0.000 | OVER-confident ∞ — hallucinated mass |

This is the whole story in three rows. The model's probability mass is **mis-distributed within the family**: it bleeds mass into the two open-ended tail bins (`or_higher`/`or_below`), where it is 5×+ over-confident and settlement crushes it, and it correspondingly **under-fills the `exact` ring bins**, where it is genuinely a touch better than the market (10.8% realized vs 9.3% asserted vs ~9.1% market base rate). The `sigma_scale_fit.json` `tail` ratio of **0.296** (Family C) / **0.138** (Family F) is the same fact measured independently: tail mass over-assigned ~3.4×–7×. Cf. `b2_capital_efficiency_audit.md:47`.

### 1e. buy_no economics NET OF COST (the base-rate verdict)

Stake 1 share at NO cost = price; payout 1 iff NO wins (the bin loses). Gross P&L/share = `win − price`; subtract ~1¢ fee:

| NO price band | n | NO win-rate | mean cost | gross P&L/sh | after ~1¢ fee |
|---|---:|---:|---:|---:|---:|
| 0.5–0.6 | 6 | 33.3% | 0.580 | −0.247 | −0.257 |
| 0.6–0.7 | 29 | 58.6% | 0.653 | −0.067 | −0.077 |
| 0.7–0.8 | 36 | 69.4% | 0.746 | −0.051 | −0.061 |
| **0.8–0.9** | **32** | **93.8%** | **0.860** | **+0.077** | **+0.067** |
| 0.9–1.0 | 206 | 98.1% | 0.984 | −0.004 | −0.014 |
| **q_lcb>price subset (passes capital_efficiency)** | **77** | **67.5%** | — | **−0.059** | **−0.069** |

**The buy_no favorite lane is a NET LOSER after cost across almost the entire range.** The 77 bins that pass the honest `capital_efficiency` gate (`q_lcb>price`) realize **−0.069/share after fee**. The high win-rate (98% at ≥0.9) is fully priced in and the 1¢ fee tips it negative. The only positive pocket is NO_price 0.8–0.9 (+0.067/share, n=32) — a single narrow band, too thin to be a strategy on its own and the SAME ring-vs-tail signal from the YES side viewed from the complement.

This empirically **vindicates and extends operator law #4**: buy_no favorite-buying is not merely "base rate" — it is *net-negative after cost*. Unblocking submission to re-enable it loses money.

### 1f. Market supply by price band (is there a mid-band to restore?)

Distinct evaluated buy_yes `condition_id`s since 06-08 (n=407), by YES execution price:

| band | count | share |
|---|---:|---:|
| <0.05 | 233 | 57.2% |
| 0.05–0.2 | 91 | 22.4% |
| 0.2–0.4 | 66 | 16.2% |
| 0.4–0.6 | 17 | 4.2% |

The YES weather market **is structurally a longshot market**: 79.6% of YES bins price below 0.2; only 4.2% reach 0.4–0.6. The "mid-band collapse to n=1" the synthesis lamented is **not a generation bug — it is the true market supply.** A temperature family of ~11 bins has at most one or two bins near the forecast center carrying middling probability; the market prices that ring bin efficiently. **There is no large hidden mid-band to "restore."** The edge that exists lives in the 0.05–0.20 near-ring band (1c row [0.10,0.20): n=141, model under-confident), not in a mythical 0.4–0.6 flow.

---

## 2. Objective (precise, settlement-graded)

Produce **continuous, settlement-proven, after-cost-positive fills** by entering the **single tradeable edge the data supports**: the **near-center `exact` ring bin where the calibrated model is honestly more confident than the market**, at YES prices roughly **0.06–0.25**, buying YES (or equivalently selling the over-priced tail), sized by horse-race Kelly on the *calibrated* posterior.

Non-goals (data-proven dead ends, do NOT pursue):
1. Cheap buy_yes longshot tail (<0.05): 0/72 realized — a trap, not alpha.
2. buy_no favorite-buying: net-negative after cost (−0.069/share on the admitted subset).
3. "Restoring" a 0.2–0.6 mid-band flow: the market does not supply it.

---

## 3. The mechanism: why q_lcb collapses to ≈0 on cheap bins, and what it means

The diagnosis (`diagnosis_confirmation.md`) established that ~88% of rejections are `capital_efficiency_lcb_ev` firing on `q_lcb≈0`, and asked the decisive question: **is q_lcb≈0 a CORRECT belief (honest no-edge) or a BROKEN floor crushing real mass?** My settlement join answers it, and the answer is *both, in different places*:

- **On the open tail bins (`or_higher`/`or_below`) and the deep cheap tail (price<0.02):** q_lcb≈0 is **partly too HIGH, not too low.** The model's *point* q is already over-confident there (1c, 1d). A q_lcb of 0 is the 5th percentile of a bootstrap that itself over-assigns tail mass; settlement says even 0 was generous (realized 0/51 on `or_below`, 1/51 on `or_higher`). Here `capital_efficiency` rejecting is **correct** — there is no edge, and arguably the *point* q should be lower too.
- **On the near-ring `exact` bins (price 0.06–0.25):** the model is under-confident (q 0.093 vs realized 0.108). Here q_lcb is dragged to ≈0 by the **composition of three conservative operators stacked on a thin-N bootstrap**, and *this* is where real mass is being crushed off a correct bin:
  1. The 5th-percentile lower quantile of a 51-member ensemble bootstrap (`probability_uncertainty.py:307`) — for a bin whose point q is 0.10 but where only ~5 of 51 members vote, the 5th percentile is structurally 0.
  2. The N_eff=3.71 width correction (#61, `probability_uncertainty.py:328-341`) widens the interval further (lower q_lcb) — justified for correlated members but compounding the floor.
  3. The James-Stein shrink-toward-market (#61, Step-0) pulls the point toward the market, which on an *under-priced* ring bin shrinks *away* from the truth.
  4. The σ-shape `floor_steps≈1.80` (`sigma_scale_fit.json`) sets a wide minimum σ, spreading the Normal and lowering every individual bin's mass including the ring.

So the binding constraint has a **two-population structure**: on the tail it is honest no-edge (correctly rejected, and the model is if anything *over*-stating it); on the near-ring it is an honest edge being **zeroed by a conservative LCB stack that was tuned to protect against the tail over-confidence.** The system is paying for the tail's sins on the ring bin's account.

**This reframes the entire problem.** The fix is NOT to loosen `capital_efficiency` (operator-forbidden, and correct on the tail). The fix is **upstream: calibrate the point q so the tail mass that is provably wrong is moved back onto the ring bin where it provably belongs**, so the ring bin's *point* q rises to ~0.108+ and its *honest* q_lcb clears the ~0.09 market price. The σ-fit artifact already exists and already does exactly this (it raises the mode-bin calibration ratio 0.514→0.961 per `replacement_final_form §1e-bis`) — but it is `candidate=true`, candidate, and **not wired into the live primary path** (`b2_capital_efficiency_audit.md:76`). The lever is built and sitting on the shelf.

---

## 4. Three weighed alternatives + opinionated pick

### Alternative A — "Wire the existing σ-fit + uniform-mixture into the live primary q path; re-aim at the ring bin." (PICK)

**What:** Promote `sigma_scale_fit.json` from `candidate`/operator-gated/replacement-only to the **live primary q construction**, after a one-week forward-fill validation (its own `promotion` clause demands this). The fit's `(1-w)·N(σ·k) + w·(1/n_bins)` form, with the per-family `floor_steps`/`m`/`w` CIs, pulls over-assigned tail mass back toward the center and onto the ring bin. Then let the *unchanged* `capital_efficiency` gate admit the ring bin once its calibrated q_lcb honestly clears price.

**Why it wins:**
- It is the **only** option that attacks the proven root cause (tail over-confidence / ring under-confidence) at the foundation (law #8: fix the bin-belief, not the gates).
- It **removes** rather than adds: the cheap-tail candidates self-eliminate (their calibrated q drops, `capital_efficiency` rejects them honestly, `coverage_unlicensed_tail` becomes vestigial because almost nothing is both cheap and high-q anymore). This collapses gates rather than accreting them (law #3).
- The artifact, its sole writer (`scripts/fit_sigma_scale.py`), weekly refit cadence, and provenance are already built and operator-ratified in form (`replacement_final_form §1e-bis.1`). The work is *wiring + validation*, not invention.
- It KEEPS the honest `capital_efficiency` gate untouched (keep-invariant #2).

**Cost / risk:** The σ-fit `_meta.promotion` warns "magnitude is non-stationary on 5 days" and "holdout shows" instability. Promotion MUST be gated on a forward-fill validation, not the in-sample fit. The consumer must be wired for the `floor_steps + m` fields (the fit notes the live consumer currently is not). This is the real engineering work and the main failure surface (§5).

### Alternative B — "Leave q alone; add a settlement-isotonic recalibration layer keyed by (family, dist-from-center ring, season) that maps raw q → realized-frequency q, licensed at N≥30."

**What:** Build an isotonic / Platt map from raw model q to realized settlement frequency, bucketed by ring distance (dist=0,1,2,3,≥4,tail per `sigma_scale_fit.json`'s own ring structure), and apply it as the q used for admission. This is `SETTLEMENT_ISOTONIC` generalized from a shrink-only fail-closed operator (D3) into a full bidirectional recalibration.

**Why considered:** It is the most *direct* expression of "make q match settlement" and it is data-keyed, not a hardcode. It would lift the ring bin and lower the tail in one operator.

**Why NOT picked (but salvage one idea):** (1) `SETTLEMENT_ISOTONIC` today only *shrinks* (fail-closed, `min_n=30`); making it *raise* q on the ring bin requires it to license an *upward* move, which is a genuinely new and riskier authority (a recalibration that increases stake on the model's say-so). (2) It is a **second** calibration authority parallel to the σ-fit — exactly the "two licensing vocabularies" the operator wants collapsed. (3) The ring buckets it needs are *already* what the σ-fit fits. So B is a more-complex re-derivation of A's effect. **Salvage:** B's ring-distance bucketing (dist 0..≥4, tail) is the right *diagnostic granularity*; use it as the validation/monitoring axis for A (§6), not as a second live operator.

### Alternative C — "Declare honest-no-edge: the only durable edge is the +0.067/share NO_price 0.8–0.9 pocket; trade ONLY that, accept tiny volume, stop everything else."

**What:** Accept the data's most conservative reading — that the cheap tail is dead, buy_no is net-negative except the 0.8–0.9 sliver, and the ring-bin under-confidence (10.8% vs 9.3%, ~1.5pp edge) is too thin to clear cost reliably. Trade only the narrow profitable NO pocket; otherwise stand down.

**Why considered:** It is the maximally honest, maximally simple reading. If the ring edge does not survive forward validation (§6 kill-criterion), this is the correct fallback, and it respects law #1's *converse*: do not manufacture edge the settlement record does not back.

**Why NOT the primary pick:** (1) The 0.8–0.9 NO pocket is n=32 over 2 weeks — too thin and likely itself a ring-bin artifact (NO on a 0.85-priced bin = the complement of a YES ring bin the market mildly under-prices). It is the *same* edge as A's ring bin, seen from the NO side; trading it directly is strictly worse than trading the calibrated ring bin (you pay 0.86 to make 0.14 instead of paying 0.09 to make 0.91). (2) It abandons the 141-observation [0.10,0.20) under-confidence signal, which is the largest coherent edge in the data. (3) "Stand down" is not the operator's goal (DONE = continuous fills). C is the **fallback**, not the plan.

**Pick: A, with B's ring-distance bucketing as the validation axis and C as the explicit kill-criterion fallback.**

---

## 5. The causal chain to a settlement-proven fill (implementable)

1. **Forward-validate the σ-fit (gate, ~1 week).** Run `scripts/fit_sigma_scale.py` daily; each day, score *yesterday's* fit against *today's* settlements (true out-of-sample). Promotion criterion: the mode-bin (dist=0 ring) calibration ratio must sit in [0.85, 1.15] on the forward window AND the `tail` ratio must move from ~0.30 toward ~1.0 (i.e., tail over-confidence demonstrably reduced) on settlements the fit did not see. Artifact: `docs/evidence/sigma_scale/2026-06-1X_forward_fill_validation.md`. **Kill:** if the forward mode-bin ratio is unstable (>±0.20 swing day-to-day) or the tail does not improve, the fit is non-stationary at this data volume → fall to Alternative C and report honest-no-edge for the ring lane.

2. **Wire the fit into the LIVE primary q path.** Today the σ-scale + uniform-mixture is applied on the replacement path and gated `candidate=true`. The consumer (`src/data/replacement_forecast_materializer.py:1040-1062`, the `q[bin]=bin_probability_settlement(...)` site) must read `floor_steps`, `k`, `m`, `w` per family from `sigma_scale_fit.json` and apply `q_adj=(1-w)·N(σ_core)+w·N(σ_core·m)` with `σ_core=max(σ_impl·k, floor_steps·step)` — the model_form the fit already declares (`sigma_scale_fit.json:161`). Stamp provenance `sigma_scale_k_applied`, `uniform_mixture_w_applied`, `floor_steps_applied`, `m_applied` on every posterior (the first two stamps already exist per `replacement_final_form §1e-bis.1`; add the last two). No new flag — the artifact's presence is the switch (keep-invariant: no shadow/gate-mass).

3. **Let the calibrated ring bin clear the UNCHANGED gates.** With tail mass moved center-ward, the ring `exact` bin's point q rises toward its realized ~0.108–0.13; its honest q_lcb (5th pct − penalties, N_eff-corrected) rises with it. When `q_lcb > price` (price ~0.09), `capital_efficiency` admits — *honestly*, no loosening. The cheap-tail candidates' calibrated q *drops*, so `capital_efficiency` rejects them honestly and `coverage_unlicensed_tail` stops mattering (almost nothing is simultaneously price<0.05 and q_lcb>2×price after calibration).

4. **Size by horse-race Kelly on the calibrated posterior** (#63, already built) over the family's K bins. The ring bin is now the dominant +growth leg. Envelope $5–15 (#18). This is downstream relay (law #8) — it re-computes correctly *because* the bin-belief is now correct.

5. **Submit.** The B1/M5 latch is OPEN (self-cleared 06-14T01:06, `diagnosis_confirmation.md:97`); the absorber self-heals (keep-invariant #1). Once admission produces `proof_accepted=1` on a calibrated ring bin, the existing submit lane carries it. The submit-stage blockers (`real_order_submit_disabled`, mode-flip) are the *secondary* constraint (`diagnosis_confirmation.md:130`) — address them only after admission produces a candidate (do not pre-optimize an empty lane).

6. **Grade at settlement.** Persist entry q_calibrated, entry price, realized settlement, after-cost P&L per fill. The win condition is the §2 objective measured continuously, not a single fill.

---

## 6. Verification that catches the failure modes

| Verification | Catches | Where |
|---|---|---|
| **Forward-fill σ-fit scorer** (score yesterday's fit on today's settlements; mode-bin + tail ratio bands) | The fit is in-sample-inflated / non-stationary (the `_meta.promotion` warning) | new `scripts/validate_sigma_scale_forward.py`; evidence doc |
| **Ring-distance reliability monitor** (B's bucketing: realized vs calibrated q for dist 0..≥4,tail, rolling) | Calibration regresses; tail over-confidence returns; ring edge evaporates | extend the `/tmp/edge_map3.py` join into `scripts/edge_reliability_monitor.py`, daily |
| **Per-fill settlement P&L ledger** (entry q_cal, price, realized, after-cost) | The ring "edge" does not survive real cost/slippage; the +1.5pp is eaten by the 1¢ fee + spread | `settlement_attribution` table (already exists, `zeus-world.db`) |
| **Tail-trap tripwire** (alert if any admitted fill has price<0.05) | Regression to fishing the dead tail | admission-time assertion / monitor query |
| **buy_no net-cost guard** (alert if admitted buy_no realizes <0 after cost over rolling N) | Re-drift into net-negative favorite-buying | rolling query over fills |

**The single decisive forward check (the targeted-fix-vs-stand-down fork):** after one week live with the calibrated q, is the realized after-cost P&L on admitted ring-bin fills **> 0** with the lower CI bound clearing the 1¢ fee? If yes → the ring edge is real, scale within the envelope. If no → the ~1.5pp ring under-confidence does not survive friction → fall to Alternative C (trade only the 0.8–0.9 NO pocket, or stand down) and report honest-no-edge. **Do not force admission of an edge cost eats** (law #1's converse).

---

## 7. Invariants (preserve)

- **INV: `capital_efficiency` is the honest final arbiter — never loosened.** `(q_lcb−price)/price≤0 → reject` (`live_admission.py:113-118`). The fix is upstream q, never this gate. (keep-invariant #2; task #66.)
- **INV: settlement is the only truth.** Every promotion gated on out-of-sample settlement, never in-sample fit (`replacement_final_form §4` iron rule #3).
- **INV: native-NO is the per-sample YES complement, never an independent forecast** (`probability_uncertainty.py:105`, §4 Hidden #3). The ring edge is traded YES-side; if expressed as NO it must derive from the same calibrated YES posterior.
- **INV-37 cross-DB write discipline** (ATTACH+SAVEPOINT) intact for any new monitor that writes (keep-invariant #4).
- **INV: no new flag / no shadow / no gate-cap-throttle.** The artifact's presence is the switch; the change collapses gates (tail candidates self-eliminate), never adds (operator laws #3, memory no-shadow).
- **INV: σ must never be tightened below MC; k_cov must never shrink sigma (CI-honesty)** (keep-invariant #6). The σ-fit *can raise* the ring mass via the mixture `w` and `m`, but the core σ floor stays ≥ MC.
- **INV: direction law inviolable** (operator law #6). Re-aiming at the ring bin does not relax bin-forecast adjacency.

---

## 8. What to KEEP / DELETE

**KEEP:**
- `capital_efficiency` gate (the honest arbiter).
- The probability-uncertainty contract (`probability_uncertainty.py`) — its YES/NO complement and penalty-lowers-only-LCB structure are correct law.
- The reconcile/absorber + B1 self-clear (keep-invariant #1).
- `coverage_unlicensed_tail`'s *intent* as the fail-closed dual (Milan-24C antibody) — but it becomes vestigial post-calibration (see DELETE-candidate).
- Horse-race Kelly (#63), single-Kelly sizing (#18), envelope $5–15.
- `sigma_scale_fit.json` + `scripts/fit_sigma_scale.py` + weekly refit — the lever; promote it, don't rebuild it.

**DELETE / RETIRE (collapse, per law #3):**
- The **operator-gate / `candidate=true` shelving of the σ-fit** on the live primary path — promote after forward validation (it is the fix, not a candidate).
- The **cheap-tail fishing posture** — once calibrated q drops the tail, the tail candidates produce honest `capital_efficiency` rejections; stop treating their non-fill as a bug to unblock. This retires the entire "unblock the cheap tail" program (the refuted de-licensing story, the EMOS-license-file remedy, the `coverage_unlicensed_tail` re-route as headline fix — all are now moot, not just wrong).
- **DELETE-candidate (verify first):** if post-calibration `coverage_unlicensed_tail` fires on 0 admitted candidates for a sustained window (it already fires on ~0.6% of rejections and 0 receipts, `diagnosis_confirmation.md:21`), retire it as dead gate-mass. Keep its Milan-24C *test* as a regression antibody; remove the live gate only after the calibration makes its firing condition unreachable.
- The **EMOS live-licensing override** (`edli_emos_ci_live_enabled` default-off, license file never built) — confirmed never-built, never-fired, not the constraint (`diagnosis_confirmation.md:57,109`). Do not build it; mark the dead override for removal in the gate-collapse sweep (#51).

---

## 9. Failure modes anticipated

1. **The ring edge (1.5pp) is eaten by cost.** Most likely failure. 10.8% realized vs ~9.1% market vs 1¢ fee + spread. Caught by §6's per-fill P&L ledger; fallback is Alternative C. **This is the honest risk: the edge may be real but sub-friction.** The plan surfaces it rather than forcing a fill.
2. **σ-fit non-stationary at 2-week data.** Its own `_meta` warns of this. Caught by §6 forward-fill scorer; if unstable, do not promote — widen the fit window or fall to C.
3. **Calibration over-corrects and crushes the ring bin too** (moving mass center-ward past the ring into dist=0 when the truth was the dist=1 ring). Caught by the ring-distance reliability monitor (per-dist, not aggregate). The σ-fit's per-ring `calibration_at_fit` table (dist 0,1,2,3,≥4,tail) is exactly this guard.
4. **Re-drift to buy_no favorite-buying** because it "wins 98%". Caught by the buy_no net-cost guard. The win-rate is a siren; the after-cost P&L is the truth (1e).
5. **Survivorship in the 2-week join.** The 430-instrument sample is the intersection of *evaluated* and *settled*; if evaluation systematically skipped hard families, the realized rates are biased. Caught by rebuilding the monitor continuously and widening the window (the signs are large enough that moderate bias does not flip them, but the magnitude needs the larger sample).

---

## 10. Where to fish / where NOT to (the conclusion)

**FISH:** the **near-center `exact` ring bin** at YES price ~0.06–0.25, where the calibrated model is honestly more confident than the market (realized 0.108–0.163 vs market ~0.09–0.13). This is the *only* settlement-backed, mid-priced, correct-bin edge in the book. It is thin (~1.5–3pp) and must clear the 1¢ fee — trade it YES-side, sized by Kelly on the calibrated posterior, and *prove it at settlement before scaling.*

**DO NOT FISH:**
- The cheap longshot tail (price<0.05): **0/72 realized.** It is the model's tail over-confidence, not alpha. Calibrate it *down*; never license it.
- buy_no favorites: **net −0.069/share after cost** on the admitted subset. The 98% win-rate is fully priced; the fee makes it a loser.
- A "mid-band 0.2–0.6 flow": the market does not supply it (4.2% of bins). There is nothing to restore.

The system spent 100 patches trying to *unblock* the tail. The data says the tail was *correctly* blocked, and the real edge was being *zeroed on the ring bin* by a conservative LCB stack tuned to defend against that very tail. **Stop unblocking the tail. Calibrate the tail to honesty so its mass returns to the ring, and let the honest gate admit the ring bin it was starving.**

---

## 11. Reproduction (queries, for the next session)

Settlement truth: `zeus-forecasts.db.settlement_outcomes WHERE authority='VERIFIED'` (the `zeus-world.db.settlements` table is EMPTY — do not use it).
Belief at decision: `zeus-world.db.edli_no_submit_receipts.receipt_json → opportunity_book.candidates[*]` carries per-bin `bin_label, direction, q_posterior, q_lcb_5pct, execution_price, q_lcb_calibration_source, same_bin_yes_posterior`.
Join key: parse `bin_label` → `(city, target_date, metric, kind, lo, hi)`; match to `settlement_value` with ±0.5 half-step. Dedup to latest eval per `(instrument, direction)`. Full logic in `/tmp/edge_map2.py … edge_map4.py`, `/tmp/supply.py` (transient — re-create from this section). Recommended permanent home: `scripts/edge_reliability_monitor.py` (header-provenanced, daily, writes `docs/evidence/sigma_scale/`).

*End P1_S4. Fully written, untrimmed.*
