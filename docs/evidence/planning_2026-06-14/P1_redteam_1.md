# RED-TEAM #1 (hostile) — Strategy-of-Record + the Four P2 Plans

**Date:** 2026-06-14
**Mode:** READ-ONLY hostile adversary. No edits, no deploy, no live touch. DBs opened `?mode=ro`. Every empirical claim re-verified at source or DB **this session** (not inherited from the plans under attack).
**Target:** `P1_strategy_of_record.md` + the six ordered thrusts, decomposed into the 11 nodes of `P2_sequence_and_critical_path.md`, with the file-level mechanics in `P2_W-QLCB.md`, `P2_W-SUBMIT.md`, `P2_W-EDGE-LOCATE.md`.
**Mandate:** for each step, would it actually reach continuous settlement-proven correct-bin alpha, or just "an order submits"? Is it a 1-order hack? Does it ADD a gate/cap/throttle (law 3)? Does it license edge not proven real (re-create the base-rate illusion / law 4)? Does it loosen `capital_efficiency` (forbidden)? What breaks downstream? What does it FORGET? Kill/demote failing steps.

---

## 0. WHAT I VERIFIED FIRST (the plan's foundation — mostly holds, with two corrections)

Before attacking, I re-ran the plan's load-bearing facts. **The foundation is real:**

| Claim | Plan says | I verified | Verdict |
|---|---|---|---|
| Live q_lcb primary path | BUNDLE | `feature_flags.openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled=True` (nested, not top-level) | ✅ CONFIRMED |
| Submit master arm | ON | `edli.real_order_submit_enabled=True`, `edli.reactor_mode=live`, `edli.edli_live_operator_authorized=True` | ✅ CONFIRMED |
| Coverage shrink | ACTIVE live | `edli.q_lcb_settlement_coverage_gate_enabled=True` | ✅ CONFIRMED |
| `anchor_sigma_c` crush | 3.0 universal | `SELECT json_extract(provenance_json,'$.anchor_sigma_c'),COUNT(*)` → **`3.0 | 3504`** | ✅ CONFIRMED |
| `_isotonic_realized_rate` exists, shrink-only wrapper | yes | `settlement_backward_coverage.py:97` exists; `apply_settlement_coverage:204` "Never widen: the shrink only ever LOWERS" | ✅ CONFIRMED — UP arm is genuinely novel |
| VERIFIED settlements | 7009, 2024-01→2026-06 | `7009 | 2024-01-01 | 2026-06-13` | ✅ CONFIRMED (but see KILL-1 — wrong population) |
| `DIRECTION_LAW_SIGMA_K` hardcoded | 1.0 | `direction_law.py:57 DIRECTION_LAW_SIGMA_K = 1.0` | ✅ CONFIRMED |

**Two foundational corrections the plans did NOT make (and that change the verdict):**

1. **The flag keys are nested under `feature_flags`/`edli`, and one config note the plans never quote is decisive:** the `q_lcb_settlement_coverage_gate_enabled` note says the coverage verdict **already feeds the ARM gate UNCONDITIONALLY** (`arm_gate_coverage_blocks`, flag-independent): *"arming requires coverage_ratio not None and |ratio-1|<0.10, and blocks on UNLICENSED."* **A coverage authority over q_lcb already exists and already gates arming.** Thrust 3 is not adding the first settlement-coverage authority — it is mutating one that is live and load-bearing in the ARM path. None of the four plans model this interaction. (See KILL-3.)

2. **The "7009 settlements = sufficient fit data" claim is true but points at the WRONG population.** The isotonic observation stream is NOT the 7009 settlements directly, and NOT the regret-event cohort either. It is something a hostile reading exposes as fatal to the UP arm's premise (KILL-1).

---

## 1. THE KILL SHOT — Thrust 3's UP arm reads a BIN BASE RATE, not correct-bin edge (law 4 + law 8 violation, the illusion re-created)

This is the single most important finding. It demotes the headline causal fix from "the one lever" to "a lever that, as specified, manufactures the exact base-rate illusion the operator contract forbids."

### 1.1 What the UP arm actually reads (traced at source this session)

The UP arm lifts `q_lcb` toward `realized_isotonic = _isotonic_realized_rate(observations, q_lcb_raw)`. I traced what builds `observations`:

- `_settlement_coverage_observations` (`event_reactor_adapter.py:12095`) builds the stream for ONE (city, metric, bin, direction) by querying **EVERY** settled outcome of that (city, metric) — `SELECT settlement_value, settlement_unit FROM settlement_outcomes WHERE city=? AND temperature_metric=?` — and for each one calling `grade_receipt(bin, direction, settlement)` to ask *"had I traded THIS bin in THIS direction, would the settled value have won?"*
- It stamps **every** observation with the **same single** `claimed_q_lcb` (`:12146` `CoverageObservation(q_lcb=float(claimed_q_lcb), won=...)`).

Two consequences, both fatal as specified:

**(a) `_isotonic_realized_rate` is NOT isotonic in the live case — it is the pooled mean.** Its own code (`:113-117`): *"a single distinct claimed band → pooled mean (isotonic on a single x is just the mean)."* Since every observation carries the same `claimed_q_lcb`, `np.unique(xs).size <= 1` is always true on the live per-cell stream → it short-circuits to `np.mean(ys)`. So `realized_isotonic` = **the fraction of this (city,metric)'s settled days on which this exact bin+direction would have won.**

**(b) `grade_receipt` is pure bin-geometry, NOT forecast-conditional.** Verified at `graded_receipt.py:90`: it takes only `(bin, direction, settlement_value)` — "did the settled temperature fall in this bin." It does **not** know whether today's posterior put any mass on the bin. So the realized rate the UP arm reads is **the unconditional historical base rate that this (city,metric,bin) settles as a winner.**

### 1.2 Why this re-creates the forbidden illusion

The UP arm therefore lifts `q_lcb` toward **the bin's unconditional base rate** — exactly the quantity operator law 4 names as "BASE RATE already in the price — NOT alpha." For a near-center ring bin the base rate is ~0.10–0.30; the market prices it ~0.09. The UP arm will lift `q_lcb` from ≈0 to the base rate, clearing the price, and `capital_efficiency` will ADMIT — **on a candidate whose only "edge" is that the bin historically wins more often than its price, regardless of whether today's forecast honestly concentrates mass there.**

This is the law-8 trap re-emerging precisely where the strategy claimed immunity. The strategy says (P1 §1.3) *"the point q is never the defect"* and treats the UP arm as restoring an honest belief. But the UP arm does NOT use the point q to decide *how much* to lift — it uses the pooled base rate. The model's actual conviction on *this* day (whether `q_point` is 0.10 because the forecast genuinely peaks on the ring, or 0.10 by coincidence) is discarded. **The fix admits the bin on its average behavior, not on today's correct-bin disagreement.** That is the base-rate illusion with a settlement-coverage costume.

### 1.3 The clamp is the ONLY thing standing between this and disaster — and it is under-stressed

`min(target, q_point)` (W-QLCB §1.2) is load-bearing far beyond how the plan frames it. The plan presents the clamp as a population-C antibody ("can't lift a below-market model"). But its REAL job, exposed here, is to prevent the base-rate UP arm from admitting a bin whose base rate exceeds today's honest conviction. If `q_point=0.10` and the base rate is 0.30, the clamp holds q_lcb at 0.10 — good. **But if `q_point=0.12` and the base rate is 0.12, the UP arm lifts q_lcb all the way to ~0.12 (=q_point) on EVERY day the bin appears, whether or not today's forecast is actually sharp.** The clamp permits admission whenever the point q happens to sit near the base rate — which is most ring days, because a calibrated point q on a ring bin *is* approximately its base rate. **The clamp does not discriminate the alpha day from the average day; it only caps the magnitude.** The plan's claim that "the UP arm only ever lets the model's HONEST belief reach the gate" is FALSE: it lets the model's belief reach the gate **whenever the base rate independently corroborates it**, which is a different and much weaker condition than "the model honestly disagrees with the market today."

### 1.4 Verdict on Thrust 3 (N7)

**DEMOTE from "the one causal fix" to "conditionally admissible only with a redesigned UP-arm trigger."** As specified, the UP arm is a base-rate admitter wearing settlement-coverage clothing. It does NOT locate correct-bin edge (the operator's actual DONE); it locates bins that historically win more than their price. The §4.1 sub-population backtest (D2) the plan relies on to "decide whether the UP arm ships" **cannot detect this defect**, because that backtest measures R/E (realized-vs-claimed coverage) on the same base-rate population — it will show "under-coverage persists" precisely because the bins DO win at their base rate, and conclude "ship the UP arm," validating the illusion. **The gate-before-the-gate is itself blind to the disease.**

**What the plan FORGOT:** the UP arm needs a *forecast-conditional* realized rate — the bin's win rate **restricted to days when the model put comparable mass there** — to be correct-bin rather than base-rate. That requires joining the coverage observations to the per-day posterior (the bin's `q_point` that day), which `_settlement_coverage_observations` explicitly does NOT do. Building that join is a materially larger change than "reuse `_isotonic_realized_rate`," and the plan's claim of "SIMPLIFY, reuse the existing map" is the source of the defect: the existing map was built for the *shrink* direction, where reading a base rate to LOWER an over-claim is safe (a bin claiming 0.30 that base-rates 0.10 IS over-claiming). Reusing it for the UP direction inverts its validity — base-rate corroboration is sound evidence to shrink a wild claim, but NOT sound evidence to inflate a crushed one into an admission.

---

## 2. KILL-1 — The cohort-growth premise is mis-stated, and the real number is worse for licensing AND better for fitting (both cut against the plan)

The plans repeatedly assert two things that I verified are in tension:

- "7009 VERIFIED settlements → the isotonic/E2 have sufficient fit data" (sequence §0; W-QLCB §2.1).
- "The distinct ring cohort is n≈5; the rate-limiter is settlement accrual toward n≥30" (sequence §5.3; W-EDGE-LOCATE §1.2).

**Both cannot be the binding number for the same gate.** I checked which is which (this session, `state/zeus-world.db` + `state/zeus-forecasts.db`):

- **For the isotonic (Thrust 3):** the observation stream uses ALL settled (city,metric) outcomes counterfactually re-graded — so a per-cell stream DOES reach n≥30 easily off the 7009. **This is WORSE for the plan, not better** — because (KILL §1) that large stream is exactly the base-rate stream. The plan cites 7009 as reassurance; in fact 7009 is the size of the base-rate pool that powers the illusion.
- **For E2's LICENSE gate (Thrust 6):** the verdict requires n≥30 distinct (city,date,bin) **events from the regret substrate** (the days a real candidate was actually generated). I measured this: the **entire** universe of distinct settled `buy_yes` events at **any** price is **50 events, 3 winners**; the ring band (cost 0.05–0.15) is **7 events, 3 winners**; the regret substrate spans only **7 distinct target dates** (2026-05-31 → 2026-06-11).

**The contradiction the plans paper over:** E2 grades on the ~50-event regret cohort (correctly — those are real candidate-days), but Thrust 3's UP arm fits on the 7009-settlement base-rate cohort. **The instrument (E2) that is supposed to gate the fix (N7) is measuring a different, 100× smaller population than the fix is fitting on.** E2 will sit at INSUFFICIENT_DATA for months (7 dates of substrate; n≥30 distinct ring events is far away), while N7's UP arm is *already* fully fittable on the base-rate pool and *will* admit. **The gate cannot restrain the fix, because they read different populations.** If N7 is promoted to live the moment its own backtest (D2, base-rate-blind) passes — which the sequence permits at "Wave 2/3" before E2 ever licenses anything — the base-rate admitter goes live UNGATED by the only instrument designed to catch base-rate (E2's Brier antibody). **This is a sequencing hole the DAG does not close.**

**Verdict:** the plan's "fit on the full 7009-row history to compress the rate-limiter" (sequence §5.3) is the single most dangerous sentence in the bundle — it compresses the rate-limiter by fitting the UP arm on the base-rate pool while the base-rate antibody (E2) is still starved. KILL that acceleration lever as written.

---

## 3. KILL-2 — Thrust 4 (σ-shape point-q promotion, N6) moves the crown jewel on a holdout the strategy elsewhere calls untrustworthy

N6 is the FIRST live-behavior node and it changes the **point q** — which the entire strategy designates "the crown jewel — do not touch" (P1 §2 KEEP). The plan's escape hatch is "promote only after `_meta.promotion` forward-fill validation." Hostile reading:

- The same strategy (P1 §2, last KEEP bullet) cites the replacement-form §4 walk-forward showing **"in-sample EV inflated, holdout collapses to +1.2¢…−2.7¢."** The σ-fit (`sigma_scale_fit.json`) carries its own holdout warning in `_meta.promotion`. So the strategy simultaneously (a) keeps the point q sacrosanct, (b) promotes a point-q-moving fit, and (c) cites evidence that this fit class collapses on holdout. **N6 is the strategy violating its own KEEP-list under a validation gate it elsewhere shows is unreliable for exactly this artifact family.**
- N6 raises the ring point q. The clamp ceiling in Thrust 3 is `min(target, q_point)`. So **N6 directly raises the ceiling the base-rate UP arm can exploit** — the two compose to *amplify* the KILL §1 illusion: a higher point q lets the base-rate UP arm admit more. The plan calls this composition a feature ("T4 raises the ceiling so the UP arm lifts further"); hostile reading says it is the two halves of the illusion reinforcing.

**Verdict: DEMOTE N6 to shadow-only / OFF the critical path until KILL §1 is resolved.** Moving the crown jewel to widen the ceiling of a base-rate admitter is the worst possible ordering. If the UP arm is redesigned to be forecast-conditional (the §1.4 fix), N6 becomes a legitimate independent improvement — but it must not ship as "the ceiling-raiser for N7."

---

## 4. KILL-3 — Thrust 3 mutates a coverage authority that already gates ARM, and the plans never model it

From the config note (§0): `arm_gate_coverage_blocks` reads the coverage verdict UNCONDITIONALLY and **blocks arming on UNLICENSED or |ratio-1|≥0.10**, independent of the shrink flag. So today, before any fix:

- The coverage verdict already has a live, load-bearing job: it can **block arming**.
- Thrust 3 replaces `apply_settlement_coverage` (the shrink wrapper) with a bidirectional `calibrated_qlcb`. But `arm_gate_coverage_blocks` reads `CoverageVerdict`, whose `coverage_ratio`/`realized_win_rate`/`status` fields are produced by the SAME `settlement_backward_coverage_check`. **If Thrust 3's bidirectional rewrite changes how the verdict is computed (it must — the UP arm needs a verdict that says "too low" not just "too high"), it changes the ARM-gate's input.** A verdict that currently says UNLICENSED→block-arming on an over-claim could, under a bidirectional construction, say "licensed-up"→permit-arming on a base-rate-corroborated under-claim. **The fix can silently unblock arming on the base-rate illusion through a path neither W-QLCB nor the sequence plan mentions.** The ARM gate is supposed to be the operator's "verified-correct-before-live" backstop; this fix routes under it.

**Verdict:** Thrust 3 has an **un-modeled blast radius into the ARM gate.** This is not optional to resolve — it is the operator's own safety interlock. The plan's risk table (W-QLCB §5) lists five risks; the ARM-gate interaction is not among them. FORGOTTEN. Any implementation MUST freeze `arm_gate_coverage_blocks`'s semantics (the over-claim block) and prove the UP arm cannot relax it. As specified, it can.

---

## 5. KILL-4 — Thrust 5 / W-S2 (N9) is correctly sequenced but the plan UNDER-COUNTS its standalone danger, and "re-decision under capital_efficiency" has a hidden direction leak

W-SUBMIT's own sequencing law (ship N9 only after N5+N6+N7-shadow) is sound and I endorse it. Two hostile additions:

- **The recent MODE_FLIPPED population is symmetric maker↔taker on cheap books** (W-SUBMIT §1.1: "11× MAKER→TAKER at 0.011/0.012 and 11× TAKER→MAKER at 0.14/0.15"). These are the *settlement-dead cheap-tail* (population A). N9's re-admission, even gated, re-prices these on the fresh book — and if the fresh book momentarily shows the flipped mode clearing `q_lcb>price`, N9 submits. **On a cheap tail bin, after the KILL §1 base-rate UP arm has lifted q_lcb, `q_lcb>price` can transiently hold.** N9 + the base-rate UP arm together can push a settlement-dead cheap-tail trade to the venue — the exact law-2/law-4 failure W-SUBMIT's sequencing law was written to prevent, routed back in through the q_lcb fix N9 depends on. **The dependency is not a one-way safety; it is a coupling that can also amplify the upstream defect.**
- "Re-decision under the SAME `capital_efficiency`" is presented as airtight. But `capital_efficiency` on the *fresh* mode uses that mode's cost. A MAKER→TAKER flip re-prices with the taker fee; the K-spine inequality `q_lcb - taker_cost > 0` is *stricter*, good. But a TAKER→MAKER flip re-prices with **zero taker fee** — `q_lcb - maker_cost > 0` is *looser*, and on a base-rate-lifted q_lcb it will clear more often. **The TAKER→MAKER direction of N9 is the permissive one, and it is exactly the direction that pairs with the cheap-book illusion.** The plan treats both flip directions as symmetric re-decisions; they are not.

**Verdict: N9 stays sequenced last (correct), but its RED-on-revert tests (W-SUBMIT §5.1) must add a case: a cheap-tail bin whose q_lcb was base-rate-lifted must STILL be rejected at submit by an independent settlement-deadness check, not merely re-cleared on the looser maker cost.** As written, N9 trusts that the admission fix already excluded dead bins — but KILL §1 shows the admission fix can ADMIT them. The two plans each assume the other is the guard; neither is.

---

## 6. WHAT SURVIVES (the steps that are genuinely correct — credit where due)

A hostile review that kills everything is useless. These steps are sound and should proceed:

- **N1 (T1 observability), N8 (W-S1 lane stamp), N3 (E1 query):** pure read-only / log-text / telemetry. Zero live risk, genuinely unblock observation. **KEEP, ship first, unconditionally.** N1 is correctly a hard prerequisite.
- **N2 (T2 dead-gate deletion):** I confirmed the deleted paths are dead live (`edli_emos_ci_live_enabled` absent; the source-allow-list never stamps `EMOS_ANALYTIC`; `authority_on=False` at `:2811`). **KEEP** — with the D6 guard (do not delete the `coverage_unlicensed_tail` *effect* until a correct admission fix reproduces it). This is real SIMPLIFY.
- **N5 (T3a σ_center producer fix), in shadow:** replacing the `center_sigma_c=3.0` hardcode with a fitted center-uncertainty σ is correct AND it is the part of Thrust 3 that does NOT have the base-rate defect — it makes the *raw* bound mechanically honest without inflating toward a base rate. **PROMOTE N5 to the primary lever** (it is currently framed as the junior half of Alternative 3). The producer fix alone, with NO UP arm, makes the ring bin's raw 5th-percentile honest (q_lcb rises from ≈0 to its *forecast-conditional* ~0.005–0.04 because the center jitter shrinks), and `capital_efficiency` then admits or rejects on a bound that reflects *today's* posterior, not a base rate. **This is the correct-bin fix the UP arm only pretended to be.**
- **N4 (E2 harness) with the Brier antibody:** the vs-market `model_brier < market_brier` requirement is the ONE antibody in the whole bundle that could catch the KILL §1 base-rate illusion — **if it is wired to gate N7's promotion and N7 cannot promote without it.** KILL-1 showed the sequence lets N7 promote on its own base-rate-blind backtest before E2 licenses. **FIX: make E2's Brier-passing LICENSE a hard structural precondition for N7→live (not merely a "consult"), and forbid the §5.3 "fit on full 7009 history to compress" acceleration.** With that change, E2 is the antibody that demotes the UP arm honestly.

---

## 7. THE REORDERED VERDICT (what the plan should become)

The strategy's *diagnosis* is right (the 3.0°C center jitter crushes the ring's lower tail; the point q is honest; `capital_efficiency` is the honest gate to keep). Its **primary remedy is mis-aimed**: it reached for a settlement-coverage UP arm that, traced to source, reads a bin base rate and re-creates the law-4 illusion, gated by a backtest blind to that illusion and an instrument starved of the population that could catch it.

**Promote / keep (correct-bin, no illusion):**
1. N1, N3, N8 — observability/query, ship now.
2. N2 — dead-gate deletion, ship now (D6 guard).
3. **N5 (σ_center producer fix) becomes the PRIMARY causal lever** — it makes the raw q_lcb forecast-conditionally honest. This alone may close the whole gap, and it carries NO base-rate risk.
4. N4/E2 with the Brier antibody as a **hard structural gate**, plus the fix forbidding the "fit on 7009 to compress" lever.

**Demote / redesign (base-rate illusion as specified):**
5. **N7 (bidirectional UP arm) — DEMOTE.** Do not ship the UP arm as "reuse `_isotonic_realized_rate`." If an UP arm is wanted, it must read a **forecast-conditional** realized rate (the bin's win rate restricted to days the posterior put comparable mass there), which is a larger change than the plan admits and must be proven to beat the market on Brier (E2) *before* its own backtest is trusted. Until then, N7 is a base-rate admitter — KILL.
6. **N6 (σ-shape point-q) — DEMOTE off the critical path.** It moves the crown jewel to raise the ceiling of the base-rate admitter. Legitimate only as an independent improvement AFTER N7 is redesigned; never as "N7's ceiling-raiser."

**Resolve before any live promotion (forgotten blast radius):**
7. **KILL-3 — the ARM-gate interaction.** Thrust 3 mutates the verdict that `arm_gate_coverage_blocks` reads. Freeze the over-claim block; prove the UP arm cannot relax arming. Not in any plan's risk table.

**Sequencing fix (the coupling that amplifies):**
8. **N9 (W-S2)** stays last, but add the cheap-tail settlement-deadness RED test (the TAKER→MAKER looser-cost direction pairs with the base-rate illusion).

**Net effect on the operator's DONE:** with N5 as the primary lever and N7 demoted, the path still reaches a *forecast-conditional* honest ring q_lcb — and if that honest bound clears the price on real correct-bin disagreement, the fill is genuine alpha; if it does not, the dated "market is efficient on the ring" verdict is reached **without** having first manufactured the base-rate illusion to get there. **The redesign makes the law-1 honest-no-edge outcome reachable without passing through a law-4 violation.** The original plan's path reaches a fill faster — by admitting base-rate bins — which is precisely the failure the operator contract names.

---

## 8. ONE-LINE PER STEP (the scorecard)

| Node | Plan's role | Red-team verdict |
|---|---|---|
| N1 (cycle-summary attribution) | prerequisite | ✅ KEEP — ship first |
| N2 (dead-gate deletion) | SIMPLIFY | ✅ KEEP — D6 guard |
| N3 (E1 edge query) | instrument | ✅ KEEP — read-only |
| N4 (E2 harness + Brier) | promotion gate | ✅ KEEP — but make it a HARD gate on N7; forbid the "fit on 7009" lever |
| N5 (σ_center producer fix) | junior half of Alt-3 | ⬆️ **PROMOTE to PRIMARY** — the real correct-bin fix, no base-rate risk |
| N6 (σ-shape point-q) | first live node, ceiling-raiser | ⬇️ DEMOTE off critical path — moves crown jewel to widen the illusion |
| N7 (bidirectional UP arm) | THE causal fix | 🔪 **KILL as specified** — reads a bin base rate (law 4/8); redesign forecast-conditional or drop |
| N8 (W-S1 lane stamp) | telemetry | ✅ KEEP |
| N9 (W-S2 mode re-decision) | latent submit fix | ⚠️ KEEP last + add cheap-tail RED test (TAKER→MAKER leak) |
| N10 (W-S3 tick de-dup) | LOW cleanup | ✅ KEEP — fold into #64 |
| N11 (E3 candidate-focus) | widener | ⏸️ KEEP deferred — but it widens `T` to admit MORE base-rate bins if E2 mis-licenses; gate hard on the Brier antibody |

---

## 9. THE THREE THINGS THE PLAN FORGOT (summary for the operator)

1. **`_isotonic_realized_rate` on the live per-cell stream is the pooled BIN BASE RATE** (single claimed band → mean; `grade_receipt` is forecast-unconditional bin-geometry). The UP arm admits bins on their historical win frequency, not on today's correct-bin disagreement. The base-rate illusion (law 4) re-enters through the headline fix. **The D2 backtest cannot catch it — it measures the same base-rate population.**
2. **The fix's fitting population (7009 settlements, base-rate) ≠ the gate's grading population (~50 regret events).** The instrument meant to restrain the fix is starved of the population the fix exploits; the sequence permits N7→live before E2 can license, leaving the base-rate admitter ungated. The "fit on 7009 to compress the rate-limiter" lever actively weaponizes this.
3. **Thrust 3 mutates the coverage verdict that already gates ARM** (`arm_gate_coverage_blocks`, unconditional, |ratio-1|<0.10). The operator's verified-correct-before-live interlock reads the very object the fix rewrites. No plan models it.

**The clean part of the diagnosis survives and points the way:** the producer fix (N5) is the genuine correct-bin lever — shrink the 3.0°C center jitter to a fitted center-uncertainty σ, and the ring's raw lower bound becomes honest *conditional on today's forecast*, with zero base-rate contamination. Make N5 primary, kill/redesign N7, demote N6, and the strategy reaches the operator's DONE — or the honest law-1 verdict — without ever passing through the illusion it was built to avoid.

*End RED-TEAM #1. Read-only; no production code or daemon changed. Every empirical claim re-verified at source or DB this session.*
