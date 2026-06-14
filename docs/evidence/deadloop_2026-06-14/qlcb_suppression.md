# q_lcb Suppression Root-Cause — Topology-Covered 06-15 Families

- Created: 2026-06-14
- Authority basis: live reactor log (logs/zeus-live.log, HEAD 54e7b0f34c — NOT ee1604440c
  as the brief stated; provenance note below), settlement_outcomes (state/zeus-forecasts.db),
  src/engine/event_reactor_adapter.py, src/calibration/settlement_backward_coverage.py,
  src/strategy/live_inference/{live_admission,direction_law}.py. Read-only.
- Operator RULE-1 framing: every q_lcb < price rejection is presumed OUR DEFECT until
  settlement proves otherwise. Verdict below: the dominant suppressor IS a defect.

## Provenance discrepancy (flagged, not blocking)
The brief pins HEAD ee1604440c and the cap line numbers 469-471 / 739-743 in
`src/execution/event_reactor_adapter.py`. The live tree has HEAD **54e7b0f34c**, the file
is **`src/engine/event_reactor_adapter.py`** (14561 lines), the market-anchor cap is at
**L7481-7504**, and the settlement-coverage shrink is applied UPSTREAM in q-construction at
**L10043 and L10447** (helper `_maybe_apply_settlement_coverage_to_lcb`, L12185). The brief's
"both caps only lower BUY-NO" claim is WRONG for the coverage gate (proven below). All
numbers here are re-derived from the live log + DB, not the brief.

---

## OBSERVATION (no interpretation)
Latest cycle 2026-06-14 12:33:27Z: `processed=11 proof_accepted=0 rejected=11`, reason
`EVENT_BOUND_ALL_CANDIDATES_REJECTED ... capital_efficiency_lcb_ev=19`. The printed "best"
candidate per family is **buy_yes**, not buy_no. Live log K3 shrink lines show the q_lcb is
moved PRE→POST by the settlement-coverage gate. The decision-engine compares the POST value
to price.

---

## THE SUPPRESSOR — settlement-coverage gate (K3), applied to BOTH buy_yes AND buy_no

`_maybe_apply_settlement_coverage_to_lcb` (event_reactor_adapter.py L12185) loops
`for direction in ("buy_yes", "buy_no")` (L12253) — it is NOT buy-no-only. With
`q_lcb_settlement_coverage_gate_enabled=true` (settings.json L92) it is LIVE on every side.

### The defect: conditional forecast probability shrunk to UNCONDITIONAL climatological base rate

`_settlement_coverage_observations` (L12130) builds the "realized" stream by grading EVERY
settled day of (city, metric) against THIS ONE bin:
> "for every SETTLED outcome of this (city, metric), grade 'had I traded THIS bin in THIS
>  direction, would the settled value have won?'"

Every observation is stamped with the SAME scalar `claimed_q_lcb` (L12180), so
`_isotonic_realized_rate` hits its degenerate branch (`np.unique(xs).size <= 1 → np.mean(ys)`,
settlement_backward_coverage.py L110-115). The "realized win rate" is therefore the
**unconditional fraction of all historical days whose settled high landed in this bin** —
a climatological base rate computed over the whole pooled history (n=79-86 days spanning
winter→summer), with NO conditioning on today's forecast.

The claimed q_lcb is a CONDITIONAL forecast-day probability P(bin | today's forecast centers
here). The gate declares it "UNLICENSED" whenever it exceeds the bin's all-history base rate,
and shrinks it to `base_rate − 0.01` (SETTLEMENT_ISOTONIC). **This structurally destroys all
forecast skill**: any honest forecast that concentrates mass on the bin it predicts ALWAYS
exceeds that bin's unconditional frequency, so it ALWAYS reads UNLICENSED and is shrunk to
climatology. The gate cannot tell a skillful concentrated forecast from an overconfident one —
it punishes concentration itself. A perfectly-calibrated forecast is suppressed identically.

### Exact numeric proof (shrink target == unconditional bin frequency)

| Family (06-15) | bin | settled count / pool | base rate | K3 shrink target (rate−0.01) | logged POST q_lcb |
|---|---|---|---|---|---|
| Singapore high | 31°C | 12 / 86 | 0.1395 | 0.1295 | **0.129535** (log) |
| Shanghai high | 27°C | 7 / 86 | 0.0814 | ~0.08 (+MC floor) | ~0.098 (best-line) |
| Wuhan high | 30°C | 9 / 79 | 0.1139 | 0.104 | 0.0980 (best-line) |

Singapore is the cleanest: logged shrink `buy_yes 0.508824 → 0.129535 (UNLICENSED n=86)`;
0.129535 == 12/86 − 0.01 to 4 dp. The pre-K3 q_lcb 0.5088 is a real concentrated forecast
(Day0 obs already climbing through 28°C at 17:30Z toward the 31°C bin the market prices at 0.48).

---

## PER-FAMILY STAGE-BY-STAGE q_lcb PIPELINE

Stages (file:line): raw bin q via emos.bin_probability_settlement → MC conservative q_lcb_5pct
→ **K3 settlement-coverage shrink** (L10043/L10447, the suppressor) → market-anchor cap
(L7481-7504, buy_no only, did NOT fire on these buy_yes bests) → capital_efficiency compare
(live_admission.py L87) → direction_law (L7580) / coverage_unlicensed_tail (L7590).

### Singapore high 31°C — buy_yes — **S-CAP (the gate suppresses real edge)**
| stage | q_lcb | ev/$ vs price=0.48 | verdict |
|---|---|---|---|
| MC q_lcb_5pct (pre-K3) | **0.5088** | **(0.5088−0.48)/0.48 = +0.060** | **POSITIVE — admissible** |
| K3 coverage shrink → | 0.1295 | (0.1295−0.48)/0.48 = −0.730 | UNLICENSED, shrunk to base rate |
| capital_efficiency compare | 0.1295 | −0.7301 | **REJECTED** (matches log) |

**The crossing stage is K3 (L10043/L10447).** Pre-cap q_lcb (0.5088) BEAT price after cost
(+6 ev/$). The one-sided shrink alone flipped +0.060 → −0.730. Classification: **S-CAP** — the
settlement-coverage gate is suppressing a q_lcb that was tradeable before it touched it.

### Shanghai high 27°C — buy_yes — **S-CAP / BOUNDARY**
EMOS q_lcb ~0.157 (log buy_yes 0.156863), price 0.08-0.09. PRE-K3 ev/$ ≈ +0.85 (positive).
POST-K3 ~0.098 → ev/$ ≈ +0.15 still marginally positive but at/below the boundary after the
shrink erodes most of the margin; logged as a boundary rejection. Crossing stage: K3.
Classification: **S-CAP** (the shrink crushes a positive pre-cap edge toward the price).

### Beijing high — buy_yes legs shrunk to climatology (S-CAP), buy_no +13.71 ev → direction_law (HONEST)
Beijing buy_yes legs logged shrinking 0.176→0.015, 0.098→0.000, 0.098→0.028 (UNLICENSED n=79) —
same base-rate confound (S-CAP). The brief's buy_no +13.71 ev @ price=0.0040, q_lcb=0.0588 is
analyzed separately below.

---

## BEIJING direction_law verdict — HONEST block of a PHANTOM +edge

Rule (direction_law.py L185-219, header L18-31): buy_no is banned ONLY on the FORECAST bin —
the single bin where the canonically-rounded fused center `settled(mu*)` lands. This mirrors
grade_receipt exactly (buy_no WINS iff settled_bin != traded_bin), so the only banned bin is
the one buy_no LOSES on if our own forecast settles exactly.

The +13.71 ev buy_no @ price=0.0040 (market prices NO at 0.4%, i.e. YES ≈ 99.6% — an extreme
favorite) was blocked by direction_law as `forecast_bin`. That means **our own forecast center
also settles into this bin** — we and the market AGREE it is the likely outcome. The
q_lcb_no=0.0588 (a 5.88% residual NO tail) comes from the conservative MC lower bound on the
predictive-sigma floor (1.0°C), NOT from genuine disagreement with the market. ev/$ =
(0.0588−0.004)/0.004 = +13.7 is arithmetically real but **causally phantom**: it is the
favorite-longshot NO trap (buy cheap NO on an extreme favorite) where the "edge" is the
conservative q_lcb tail manufacturing NO mass on a bin we ourselves predict.

**Verdict: the direction_law block is HONEST.** Betting NO on your own forecast bin is betting
against yourself; the +13.71 ev is an artifact of the floor-sigma tail, not settlement-backed
edge. This is precisely the category the law (incident 0b5c305e, Milan 24C) exists to kill. Do
NOT remove it. The +13.7 appeared on a wrong-direction bin because the ranking objective
max(q_lcb−price) peaks where q_lcb's conservative tail most disagrees with a near-certain market
price — exactly where the q_lcb carrier is least trustworthy.

---

## coverage_unlicensed_tail — the SECOND suppressor on cheap +ev longshots (S-CAP, same root)
Wuhan 30°C buy_yes q_lcb=0.098 price=0.019 ev=+4.16 and Tokyo 26°C q_lcb=0.0574 price=0.001
ev=+56.4 are rejected by `coverage_unlicensed_tail` (live_admission.py L141): price<0.05 AND
q_lcb > 2×price AND source not in {EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}. But note the q_lcb
HERE is already the K3-shrunk base rate (0.098 = 9/79 climatology). The candidate is a +ev
longshot whose q_lcb the system itself reduced to climatology, then rejects for "disagreeing"
with the market while unlicensed. Same root confound, second gate.

---

## HYPOTHESIS TABLE
| Rank | Hypothesis | Confidence | Evidence strength | Why it stands |
|---|---|---|---|---|
| 1 | K3 settlement-coverage gate shrinks conditional forecast q_lcb to unconditional bin base rate, flipping +edge → −edge | **High** | **Strong** (exact numeric match: shrink target == settled_count/pool, 3 families) | Singapore 0.1295==12/86−0.01; degenerate isotonic = pooled mean proven in code |
| 2 | Pre-cap q_lcb itself is below price (S-CALIB) | Low | Refuted | Singapore PRE-K3 0.5088 > price 0.48 → +0.060 ev BEFORE the gate |
| 3 | Beijing +13.71 is a real suppressed edge | Low | Refuted | forecast bin == market favorite; ev is floor-sigma phantom; direction_law honest |
| 4 | Market-anchor cap (buy_no) is the suppressor | Low | Refuted | the bests are buy_yes; the anchor cap (L7481) is buy_no-only and did not fire on them |

## EVIDENCE AGAINST my leader (disconfirmation pass)
- Could the base rate legitimately exceed the forecast? Only if the forecast were
  overconfident. But it shrinks a PERFECTLY calibrated forecast identically — the gate has no
  conditioning term, so it cannot be a calibrated licensing test. The "realized rate" is
  P(bin) unconditional, never P(bin | forecast). This is a measurement confound, not a
  calibration verdict.
- Is the Singapore 0.5088 itself phantom flat-sigma (so K3 correctly kills it)? Partial risk:
  q_lcb≈q_point=0.51 is tight. BUT (a) the market independently prices the same bin at 0.48 —
  the forecast is NOT disagreeing wildly with the market, so it is not the runaway-disagreement
  pathology; (b) Day0 obs are climbing toward the 31°C bin. The edge is small (+6%) and
  market-corroborated — the opposite of phantom. Even if one disputes this single forecast, the
  K3 MECHANISM is broken independent of any one family.

## CONVERGENCE / SEPARATION
K3 coverage shrink and coverage_unlicensed_tail are the SAME root cause (unconditional base
rate treated as the licensing truth) at two gates. direction_law is GENUINELY DISTINCT and
CORRECT — keep it. Market-anchor cap is a non-factor on these families.

---

## CURRENT BEST EXPLANATION
The live no-edge state on topology-covered 06-15 families is a **DEFECT (S-CAP), not market
efficiency**. The settlement-backward-coverage gate (K3, flag `q_lcb_settlement_coverage_gate_
enabled=true`) computes its "realized win rate" as the UNCONDITIONAL climatological frequency
of each bin over the whole pooled settled history, then shrinks any forecast that concentrates
more probability than that base rate. Because every skillful forecast concentrates above the
base rate, the gate shrinks EVERY q_lcb to climatology and flips real positive edge negative.
Singapore high 31°C is the proof: pre-gate +0.060 ev/$, post-gate −0.730 ev/$, with the shrink
target (0.1295) equal to the bin's unconditional frequency (12/86 − 0.01) to 4 decimals.

## CRITICAL UNKNOWN
Whether the K3 observation stream was EVER intended to condition on the forecast (a true
backward-coverage check would compare, per settled day, the q_lcb the model CLAIMED that day
against whether that day won — a per-decision calibration curve), versus the current
implementation that stamps one constant claimed_q_lcb and grades a fixed bin against all days
(yielding climatology). The code + comments describe the former intent; the implementation
delivers the latter.

## DISCRIMINATING PROBE / HIGHEST-VALUE DE-SUPPRESSION
**Single highest-value de-suppression: disable the K3 settlement-coverage shrink on the live
q_lcb** — set `q_lcb_settlement_coverage_gate_enabled=false` (settings.json L92), which makes
`_maybe_apply_settlement_coverage_to_lcb` an immediate no-op (the ARM-gate coverage VERDICT is
read separately and unconditionally, so this does not disarm safety; it only stops the
climatology shrink of the tradable q_lcb). file:line of the suppressing stage:
`src/engine/event_reactor_adapter.py:10043` and `:10447`.

**RED-on-revert / proof it unmasks REAL edge, not phantom flat-sigma:**
1. With the flag off, re-run the decision on Singapore high 31°C buy_yes: q_lcb returns to the
   EMOS_ANALYTIC 0.5088 (calibration_source EMOS_ANALYTIC, NOT the shrunk SETTLEMENT_ISOTONIC).
   ev/$ = +0.060. The market independently prices the same bin at 0.48 → the unmasked edge is
   market-corroborated, not a runaway-disagreement phantom.
2. Guard against phantom: the EMOS_ANALYTIC q_lcb already carries the predictive-sigma floor
   (1.0°C) and the MC 5% conservative lower bound — the SAME machinery direction_law trusts. The
   flat-sigma phantom case is the one direction_law + coverage_unlicensed_tail still catch
   (Beijing buy_no, Wuhan tail). Removing K3 does NOT remove those — the phantom defenses stay.
3. Settlement check (the only true RULE-1 arbiter): once 06-15 settles, grade the Singapore
   31°C, Shanghai 27°C bins through grade_receipt. If they win at a rate consistent with the
   pre-K3 q (~0.51 / ~0.16 on FORECAST days), the suppression removed real edge. The proper FIX
   (vs the flag-off interim) is to rebuild the coverage observation to condition on the
   forecast — grade each settled day against the q_lcb the model CLAIMED THAT DAY — so the gate
   measures calibration, not climatology. Until that rebuild lands, the flag must be OFF: it is
   currently a climatology floor masquerading as a settlement license.

**Do NOT remove**: direction_law (honest, kills the favorite-longshot phantom) or the
market-anchor cap (buy_no, non-firing here). The single defect to remove is K3's live shrink.
