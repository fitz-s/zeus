# SPINE q Settlement Grade — Is the Live q-Kernel's Predictive Over- or Under-Dispersed?

- Created: 2026-06-15
- Last reused or audited: 2026-06-15
- Authority basis: Grades the **LIVE q-kernel spine** form
  `src/forecast/predictive_distribution_builder.build_predictive_distribution(...)`
  → `src/probability/joint_q.build_joint_q(pd, omega)` (NOT the legacy
  `emos.bin_probability_settlement` form). The spine belief is reconstructed exactly
  as the live reactor assembles it
  (`src/engine/event_reactor_adapter.py:7608-7660` producer +
  `src/engine/qkernel_spine_bridge.py` bridge): the **causal ECMWF-ENS member
  envelope** (de-bias OFF live ⇒ raw == debiased members), the envelope-locked
  `build_center` μ\*, and the realized-floor `build_sigma` σ. Settlement truth =
  `zeus-forecasts.db.settlement_outcomes` WHERE `authority='VERIFIED'`
  (`settlement_value` range-tested against bin bounds, mirroring
  `src/contracts/graded_receipt.py`). READ-ONLY: all DBs opened `immutable=1`; the
  only artifact written is this markdown.
- Scratch harness: `/tmp/qkernel/spine_grade.py` (belief reconstruction + real
  `build_joint_q`), `/tmp/qkernel/spine_analyze.py` (calibration tables),
  `/tmp/qkernel/spine_obs.json` (10,978 graded spine bin-observations).

---

## VERDICT (deliverable D)

**The LIVE SPINE's predictive is UNDER-DISPERSED on the tradeable tail — the OPPOSITE
defect from the legacy EMOS form, and the OPPOSITE of what the prior #121 study
concluded for the posterior-store q.** The realized-floor σ the spine serves
(≈ **1.334 °C** for almost every cell — the metric-pooled `settlement_sigma_floor`
value) is **~38 % too narrow**. The settlement-replay PIT statistic is
**std(z) = 1.380** (z = (settle − μ\*)/σ; calibrated ⇒ 1.0), with fat tails
(|z|>2 = **14.7 %** vs Normal 4.6 %; |z|>3 = **4.4 %** vs Normal 0.27 %). The
under-dispersion is settlement-source-robust (std(z) = 1.18 even on the OpenMeteo
realized extreme). Consequently the spine assigns near-zero mass to far bins
(ring ≥4 mean q = 0.0023) that actually settle there **2.5 %** of the time —
a realized/expected ratio of **11×**.

The single most specific fix and its exact lever: **the σ-floor is too small because
it was fit on a 2-day residual window and deliberately left un-widened
(`k_default = 1.0`).** Two equivalent levers:
1. **Re-fit / widen the floor** at `state/settlement_sigma_floor.json` —
   raise `_meta.k_default` from `1.0` toward **~1.4** (= the measured std(z)), or
   re-run `scripts/fit_settlement_sigma_floor.py` over a ≥ 14-day residual window
   (it currently used `window: residual-2026-06-08..2026-06-09`, n=185 pairs, mostly
   pooled to the 1.3343 °C metric tier). The consumer math is unchanged
   (`σ_eff = max(model_σ, k·sigma_floor_c)`).
2. The floor is consumed verbatim at **`src/forecast/sigma_authority.py:572-573`**
   (`realized_floor_native = max(floor.rmse_native, floor.mad_sigma_native);
   sigma = realized_floor_native`). That line is *correct* (the floor dominates by
   design); the defect is the *value* of the floor, not the max logic.

**On the direction law specifically:** the prior #121 recommendation to relax the
direction law is **NOT supported by the spine numbers, and is mildly
counter-indicated.** The non-modal YES `[0.05,0.35]` class the law kills is, in
aggregate, only borderline calibrated (mean q 0.164 vs realized 0.161, ratio
1.02×) and is **NEGATIVE after cost at the q-proxy price (−0.023/$1)** — and it
splits into a *losing* over-confident modal-adjacent ring-1 sub-class (q/real
1.17×) and a *winning* under-confident ring-2/3 sub-class (q/real 0.74×). Relaxing
the law would admit the ring-1 losers as readily as the ring-2 winners. **Fix the σ
floor first; re-grade the YES class on the corrected (wider) q before touching the
direction law.** (See deliverable C for the one caveat: at the *real* market discount
the motivating example cites — ask ≈ 50 % of q — buy-YES on the class flips to
+0.059/$1, but that executable leg is not demonstrable on this data; §E.)

---

## DATA & METHOD

Spine belief reconstructed offline, byte-faithful to the live assembly:

- **Members** = `ensemble_snapshots.members_json` (ECMWF-ENS, °C), the freshest
  **causal, VERIFIED, non-day0** snapshot **per lead bucket** per cell — exactly the
  `_forecast_snapshot_row_for_event(allow_latest=False)` envelope the Stage-0 producer
  stashes as `_edli_spine_debiased_members_native` (de-bias is OFF live, so raw ==
  debiased). day0 snapshots are excluded (the live spine routes day0 → legacy lane,
  `qkernel_spine_bridge.py`).
- **μ\*, σ** = the REAL `build_center` (envelope-lock) and `build_sigma`
  (realized-floor) authorities, run via `PredictiveDistributionBuilder(_NoOpDebiasAuthority())`
  — the same objects the bridge constructs. No `fused_center_sd_native` /
  `sigma_resid_native` are threaded by the live bridge, so the served σ is the
  realized floor (or the component RSS when no floor cell exists).
- **q** = the REAL `build_joint_q(pd, omega)` over the family's
  `provenance_json.bin_topology`, with the per-city `rounding_rule` threaded (HK
  `oracle_truncate`, else `wmo_half_up`). Σq = 1 by construction.
- **Settlement** = `settlement_outcomes` VERIFIED `settlement_value`, range-tested
  against bin bounds.

**Sample:** target_date 2026-06-08 .. 2026-06-14 (06-15 has no settlements yet —
markets settle next day). 369 VERIFIED settled families; **360 graded** through the
spine (8 dropped: 5 no ENS snapshot, 1 no bins, 3 no non-day0 snapshot); **998
(family × lead-bucket) spine decisions**; **10,978 spine bin-observations**. Lead
split (bin-obs): 24h n=3,300 · 72h n=3,861 · 96h_plus n=3,817. **No day0 bucket**
(excluded by the live spine, not by data absence). σ served: **1.334 °C** on 7,942
of 8,723 C-city bin-obs, 1.000 °C on 781 (the absolute-floor cells) — effectively a
two-valued σ regime.

---

## A. SPINE ring-distance calibration (the decisive table)

Realized win-frequency vs mean spine_q, bucketed by bin-index distance from the modal
bin (target ratio 1.0). Side-by-side with the legacy EMOS-form holdout
(`scripts/sigma_kernel_holdout_replay.py` on `emos.bin_probability_settlement`).

| ring | n | mean spine_q | realized | **ratio (real/q)** | 95% CI realized | EMOS-form ratio (legacy) |
|---|---|---|---|---|---|---|
| 0 (modal) | 924 | 0.3000 | 0.2262 | **0.754** | [0.200,0.254] | 2.21 |
| 1 | 1906 | 0.2245 | 0.1925 | **0.858** | [0.176,0.211] | 1.95 |
| 2 | 1820 | 0.0979 | 0.1335 | **1.364** | [0.119,0.150] | 1.06 |
| 3 | 1690 | 0.0257 | 0.0533 | **2.072** | [0.044,0.065] | — |
| ≥4 | 2642 | 0.0023 | 0.0254 | **11.03** | [0.020,0.032] | 0.37 |
| tail (open shoulders) | 1996 | 0.0327 | 0.0110 | **0.337** | [0.007,0.017] | 0.09 |
| **TOTAL** | 10978 | 0.0909 | 0.0909 | 1.000 | [0.086,0.096] | — |

**Read:** the spine's tail ratio is **>1 and rising with distance** (ring 2 = 1.36,
ring 3 = 2.07, ring ≥4 = 11×) — realized exceeds predicted on the far bins. That is
**UNDER-dispersion**: σ too narrow, too little mass on the tail. This is the exact
INVERSE of the EMOS form (whose ratios *fall* below 1 on the tail: dist≥4 = 0.37,
tail = 0.09 — over-dispersed). So **the realized-floor σ did not "fix" the EMOS tail
over-dispersion; it over-corrected into the opposite defect.** The peak is mildly
*over*-confident (ring 0 = 0.75, ring 1 = 0.86): the spine concentrates too much mass
on/near the mode. The `tail` row (open-shoulder "X or below" / "X or higher" bins) is
*over*-confident at 0.34 — the integrator puts spurious mass on the catch-all
shoulders.

**Cross-check — settlement-replay PIT (z = (settle − μ\*)/σ, n=998 cell×lead):**
mean(z) = +0.396, **std(z) = 1.380**, |z|>1 = 48.5 % (Normal 31.7 %), |z|>2 = 14.7 %
(Normal 4.6 %), |z|>3 = 4.4 % (Normal 0.27 %), max|z| = 4.71. std(z) > 1 ⇒
under-dispersed; the implied calibrated total σ ≈ 1.334 × 1.380 ≈ **1.84 °C**.
Worsens with lead: std(z) = 1.26 (24h) → 1.33 (72h) → **1.53 (96h+)**.

---

## B. After-cost EV by direction × ring (q-proxy price, 2 % taker cost)

Price proxy = mean spine_q per ring (no joinable executable ask for the tail bins;
§E). EV_buy_yes = (realized − price) − cost; EV_buy_no = (price − realized) − cost.
By construction EV_yes + EV_no = −2·cost, so this isolates the *calibration gap*, not
a market edge.

| ring | n | price=mean_q | realized | EV buy_yes | EV buy_no | settlement-POSITIVE class |
|---|---|---|---|---|---|---|
| 0 | 924 | 0.3000 | 0.2262 | −0.0938 | **+0.0538** | **buy_no** |
| 1 | 1906 | 0.2245 | 0.1925 | −0.0519 | **+0.0119** | **buy_no** |
| 2 | 1820 | 0.0979 | 0.1335 | **+0.0156** | −0.0556 | **buy_yes** |
| 3 | 1690 | 0.0257 | 0.0533 | **+0.0075** | −0.0475 | **buy_yes** |
| ≥4 | 2642 | 0.0023 | 0.0254 | **+0.0031** | −0.0431 | **buy_yes** |
| tail | 1996 | 0.0327 | 0.0110 | −0.0417 | **+0.0017** | buy_no (marginal) |

**Read:** the *signs* track the under-dispersion exactly. On the **peak/near-modal
rings (0–1)**, where the spine is over-confident, **buy_NO is the positive class**
(the spine over-prices YES on its own mode). On the **far rings (2, 3, ≥4)**, where
the spine is under-confident, **buy_YES is the positive class** (the spine
under-prices YES on the tail). The open-shoulder `tail` is the lone far class where
buy_NO is (marginally) positive — those shoulder bins carry inflated spurious q.

---

## C. THE DECISION INPUT — the non-modal YES `[0.05,0.35]` class (the direction-law kill)

The class the direction law suppresses: YES on a NON-modal bin with spine_q in
`[0.05,0.35]`. **n=3,790 bin-obs.**

| metric | value |
|---|---|
| mean spine_q | 0.1638 |
| realized win-freq | 0.1609 (95% CI [0.1496,0.1730]) |
| gap (real − q) | −0.0029 |
| calibration ratio (q/real) | **1.018×** (borderline; aggregate ≈ calibrated) |
| buy_YES after-cost EV/$1 @ price = mean_q | **−0.0229 → NEGATIVE** |

**But the aggregate hides a sign flip across rings** — the `[0.05,0.35]` band mixes
two opposite sub-classes:

| ring | n | mean_q | realized | ratio q/real | buy_YES EV (q-proxy) |
|---|---|---|---|---|---|
| 1 (modal-adjacent) | 1893 | 0.2252 | 0.1923 | **1.171× (over-conf)** | −0.053 (LOSER) |
| 2 | 1688 | 0.1030 | 0.1392 | **0.740× (under-conf)** | +0.016 (winner) |
| 3 | 96 | 0.0629 | 0.0833 | 0.755× | +0.000 |
| open-shoulder | 113 | 0.1305 | 0.0265 | 4.92× (badly over-conf) | −0.124 (LOSER) |

So at the **q-proxy price the class is NEGATIVE**, and relaxing the direction law
would admit the ring-1 losers (and shoulder losers) alongside the ring-2/3 winners.
**Limitation that flips the sign — the executable price.** The motivating complaint
is that the *market underprices* these bins (q ≈ 0.18 at ask ≈ 0.09 = ask at ~50 % of
q). At that discount the class is strongly positive:

| ask (× mean_q) | buy_YES EV/$1 = realized − ask − cost |
|---|---|
| 100 % (0.164) | −0.023 (neg) |
| 75 % (0.123) | +0.018 (pos) |
| **50 % (0.082)** | **+0.059 (POSITIVE)** |
| 40 % (0.066) | +0.075 (pos) |

The class is +EV **iff** the real ask sits ≳ 0.04 below mean_q. Whether it does is
**not demonstrable on this data**: only 39/3,790 killed-YES bins have any recoverable
pre-target ask in `token_price_log`, and those (mean ask 0.66 on q≈0.16) are
label-join artifacts, not real same-bin YES asks. **The executable-profit leg is
unproven** — same limitation #121 hit.

---

## D — see VERDICT above.

---

## E. Limitations (consolidated)

1. **Window size.** 7 settled target dates (06-08..06-14); 360 families; 998
   cell×lead decisions. 06-15 unsettled. No day0 bucket (the spine excludes day0 by
   design, not data absence).
2. **Executable price is a q-proxy, not a real ask.** The decisive YES-class EV (§C)
   rests on the calibration + a price model; only 39/3,790 killed bins had any
   recoverable ask and those are mislabeled joins. `token_price_log` does not overlap
   the non-modal tail. The directional EV (§B) is a pure calibration-gap proxy
   (EV_yes+EV_no = −2·cost). A real forward test needs live fills.
3. **Settlement-source label noise.** Re-grading the PIT against the OpenMeteo
   realized extreme (n=793 C cells) gives std(z) = **1.175** and mean(z) = **+0.077**
   — vs WU's std(z) 1.380, mean(z) +0.396. **The under-dispersion is source-robust
   (std(z) > 1 on both).** The +0.4σ *cold bias* (μ\* running below settlement) is
   **mostly a WU-vs-OM source difference**, not a robust forecast miss — consistent
   with the prior study's 67 % winning-bin source flip
   (`docs/evidence/qkernel_rebuild/nonmodal_bin_calibration_2026-06-15.md`). So the
   honest under-dispersion multiplier is in **[1.18, 1.38]** ⇒ σ should be ~1.55–1.85 °C
   vs the served 1.33 °C; the center-bias component is settlement-source-fragile and
   should not by itself drive a μ\* change.
4. **Disagreement with #121, explained by the σ regime.** #121 graded
   `forecast_posteriors.q_json` (the AIFS-sampled soft-anchor posterior, σ from the
   anchor_sigma path) and found non-modal q "calibrated 1.05×". This study grades the
   **live spine joint_q**, whose σ is the **realized floor (1.33 °C)** — a different,
   tighter σ regime. The spine's tighter σ is what produces the tail under-dispersion
   #121 did not see. Where they agree: the *peak/modal* bin is over-confident in both
   (#121 modal 1.28–1.82×; spine modal ratio q/real 1.45×, realized 0.223 vs predicted
   0.324).
5. **Two-valued σ.** The floor cells are almost all pooled to the metric tier
   (1.3343 °C, n=161) or the absolute floor (1.0 °C); there is essentially no
   per-city/per-season σ differentiation in the live floor table, so the spine cannot
   widen for genuinely high-variance cells. Re-fitting the floor (lever §D-1) should
   also restore city×metric cohorts where n permits.
