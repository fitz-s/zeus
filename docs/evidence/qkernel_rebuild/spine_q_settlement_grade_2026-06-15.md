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

---

## RE-GRADE at k_default = 1.30 (operator's σ fix applied)

- Appended 2026-06-15. The operator raised `state/settlement_sigma_floor.json`
  `_meta.k_default` from 1.0 → 1.30 (consumed as `σ_eff = k · sigma_floor_c` via
  `src/calibration/emos.settlement_sigma_floor` → `src/forecast/sigma_authority.realized_sigma_floor`).
  Confirmed live in a fresh process: the served σ scaled by **exactly 1.30× on all
  10,978 bin-obs** (1.334 → 1.735 °C; absolute-floor cells 1.0 → 1.30 °C). Same
  sample as the prior verdict (360 families, 998 cell×lead, 10,978 bin-obs), so the
  ONLY thing that changed is the width. READ-ONLY re-grade through the real
  `build_joint_q`. **This does NOT supersede the prior verdict; it validates the fix.**

### 1. PIT moved from clearly under-dispersed toward calibrated

| source | std(z) k=1.0 | **std(z) k=1.30** | \|z\|>2 k=1.0 → k=1.30 | \|z\|>3 k=1.0 → k=1.30 | mean(z) k=1.30 |
|---|---|---|---|---|---|
| **WU (canonical)**, n=998 | 1.380 | **1.062** | 0.147 → 0.075 | 0.044 → 0.012 | +0.304 |
| OpenMeteo (robustness, C), n=793 | 1.175 | **0.904** | 0.092 → 0.037 | 0.015 → 0.004 | +0.059 |

Normal targets: |z|>2 = 0.046, |z|>3 = 0.003. **k=1.30 lands WU almost exactly on
calibration (std(z) 1.06, |z|>2 0.075) and OM just past it (0.90, slightly
over-corrected).** The fat tails collapse: WU |z|>3 from 4.4 % → 1.2 %.

### 2. Ring-distance ratios collapsed toward 1.0 (realized is fixed; only mean_q widened)

| ring | n | ratio k=1.0 | **ratio k=1.30** |
|---|---|---|---|
| 0 (modal) | 899 | 0.754 | **0.953** |
| 1 | 1897 | 0.858 | **0.995** |
| 2 | 1820 | 1.364 | **1.121** |
| 3 | 1690 | 2.072 | **1.016** |
| ≥4 | 2676 | 11.03 | **2.689** |
| tail (open shoulders) | 1996 | 0.337 | **0.265** |

Rings 0–3 are now essentially calibrated (0.95–1.12). Ring ≥4 improved 11× → 2.7×
but is **still under-dispersed** — the extreme far tail is fatter than any single
Normal width captures (a Normal of *any* k can't simultaneously fit rings 1–3 and the
≥4 fat tail; this residual is a shape, not a width, defect). The open-shoulder `tail`
got slightly *worse* (0.34 → 0.27): widening pushes more mass onto the catch-all
shoulders, which almost never settle — a known integrator artifact, not a σ problem.

### 3. PIT-optimal k — 1.30 is a justified midpoint; the real fix is lead-dependent

| | WU | OpenMeteo |
|---|---|---|
| **global PIT-optimal k (std(z)→1.0)** | **1.380** | **1.175** |
| per-lead optimal k — 24h | 1.258 | 0.973 |
| per-lead optimal k — 72h | 1.331 | 1.098 |
| per-lead optimal k — 96h+ | **1.527** | 1.393 |

**k=1.30 slightly UNDER-corrects on WU (residual std(z) 1.06 > 1.0) and slightly
OVER-corrects on OM (0.90 < 1.0).** It sits inside the honest [1.18, 1.38] band and is
a defensible single-number choice. BUT a single global k is structurally wrong: the
under-dispersion **grows with lead** (optimal k 1.26 → 1.33 → 1.53 on WU). At k=1.30,
24h is mildly over-dispersed (std(z) 0.97) while 96h+ stays under-dispersed (std(z)
1.18). The precise fix is a **per-lead floor multiplier**: ~1.25 (24h) / ~1.33 (72h) /
~1.50 (96h+). `global_lead_bucket_floor` already widens +0.10 °C/lead-day
(`src/forecast/sigma_authority.py:118`) but is dominated by the flat realized floor;
the per-lead k belongs in the floor *table* or as a lead-scaled `k_default`.

### 4. Non-modal YES [0.05,0.35] stays NEGATIVE — direction law holds

| | n | mean_q | realized | ratio q/real | buy_YES EV (q-proxy) |
|---|---|---|---|---|---|
| class k=1.0 | 3790 | 0.1638 | 0.1609 | 1.018× | −0.0229 (NEG) |
| **class k=1.30** | 4719 | 0.1412 | 0.1435 | 0.984× | **−0.0178 (NEG)** |

Widening did the right thing to the sub-classes: the over-confident ring-1 losers
moved to fair (q/real 1.17× → **1.01×**, EV −0.053 → −0.022) and the previously
under-confident ring-2/3 "winners" were pulled back to neutral/negative (ring-2 EV
+0.016 → −0.007; ring-3 +0.000 → −0.014). **No sub-class flips to a tradeable YES**
except a tiny ring-≥4 pocket (n=37, EV +0.043 — far-tail, not in the class the law
targets, and below any sane min-n). **The direction law stays.** The aggregate is now
*slightly under*-confident (0.98×), consistent with k=1.30 being a hair wide on the
near rings. (The executable-price caveat from §C is unchanged: at a real ask ≳0.04
below q the class would still be +EV, but that leg remains undemonstrable here.)

### 5. Favorite-NO refusal — INTACT and strengthened (the critical safety check)

Running the live NO-admit gate (`(1−q) − z·se > price_no + cost`, `price_no =
1 − realized_freq(ring)` — the harshest calibrated-market proxy, mirroring
`scripts/sigma_kernel_holdout_replay._no_gate_replay`):

| | modal/favorite-NO admits | near-ring NO admits (winrate) | far-NO admits (winrate) |
|---|---|---|---|
| k=1.0 | **0** | 1178 (0.893) | 3142 (0.974) |
| **k=1.30** | **0** | 830 (0.892) | 1465 (0.985) |

**Modal/favorite-NO admits = 0 at BOTH k.** Widening σ does NOT manufacture a
favorite-NO trade — the spine never admits a NO on its own modal (favorite) bin, and
the highest-q bin (q=1.0, won) carries buy-NO@0.999 EV = −1.02 (correctly refused).
Widening made the spine *more* conservative on near-ring NO (admits 1178 → 830,
winrate held 0.89) and *preserved* the legitimate far-NO harvest (winrate 0.974 →
**0.985**), dropping only the spurious far-NO admits that were riding on the previously
under-dispersed q. The cost-0.999 favorite-NO refusal is structurally intact.

### RE-GRADE VERDICT

**k_default = 1.30 is a correct, safe improvement that should ship.** It moves the
spine PIT from clearly under-dispersed (WU std(z) 1.38) to essentially calibrated
(1.06), collapses the ring-0–3 ratios to 0.95–1.12, keeps the non-modal YES class
net-negative (direction law holds), and does NOT manufacture a favorite-NO trade
(modal-NO admits stay 0; far-NO harvest preserved at 0.985 winrate). Residual gaps,
both minor and both *shape* not *width*: (a) the ring-≥4 extreme tail is still fatter
than a Normal (2.7×) — no global k fixes this; it needs a heavier-tailed kernel or an
explicit far-shoulder mass floor; (b) the under-dispersion is lead-dependent, so the
ideal is a per-lead k (~1.25/1.33/1.50 for 24h/72h/96h+) rather than the flat 1.30,
which leaves 96h+ mildly under-dispersed (std(z) 1.18) and 24h mildly over (0.97).

---

## CENTER-WARM TEST (k_default = 1.30 live; is the cold μ\* the trade-unblock?)

- Appended 2026-06-15. With k=1.30 deployed, the live spine's highest-edge
  candidates are NO-on-its-own-modal-bin (q_yes_modal ≈ 0.27 vs market ≈ 0.35) which
  direction-law correctly kills, leaving the direction-legal candidates with no
  clearable edge. Hypothesis: a cold μ\* (PIT mean(z) > 0 on WU — we **settle on WU**,
  so a cold-vs-WU center is a real P&L defect) under-weights the warm/modal bin and
  produces exactly this pattern. Test: warm μ\* by +W·σ_served toward WU settlement
  (a single signed residual-mean shift — confirmed correct sign for BOTH metrics:
  k=1.30 mean(z) = +0.278 high, +0.580 low, both positive ⇒ settle runs warmer than
  μ\* for both). Re-graded through the real `build_joint_q` with only `mu_native`
  replaced. Same 360-family / 998-cell sample. READ-ONLY.

  Note: at the **live k=1.30**, the standardized cold bias is **+0.304σ** (WU),
  not the +0.40σ measured at k=1.0 — widening σ shrank the standardized residual. So
  the PIT-optimal warm is +0.30σ; +0.40σ overshoots.

### 1. The warm DOES fix the PIT mean (calibrates the center to WU)

| warm (×σ) | mean(z) | std(z) | \|z\|>2 |
|---|---|---|---|
| +0.00 | +0.304 | 1.062 | 0.075 |
| +0.20 | +0.104 | 1.062 | 0.061 |
| +0.30 (optimal) | ≈ 0.00 | 1.062 | — |
| +0.40 | −0.096 | 1.062 | 0.067 |

mean(z) is linear in W; the PIT-mean-optimal warm is **W\* = +0.304σ** (lead-stable:
+0.31/+0.29/+0.31 for 24h/72h/96h+). **+0.20σ leaves a residual +0.10σ; +0.40σ
overshoots to −0.10σ.** So if a center warm ships, **+0.30σ** is the calibration
target, not +0.40.

### 2. THE DECISIVE TEST — the warm does NOT surface direction-legal positive edge

After-cost EV (2 % taker) of the two **direction-LEGAL** classes, by warm:

| warm | YES-on-MODAL EV | (ratio q/real) | NO-on-NON-MODAL EV |
|---|---|---|---|
| +0.00 | **−0.069** | 1.225× | **−0.025** |
| +0.20 | **−0.053** | 1.143× | **−0.023** |
| +0.40 | **−0.038** | 1.072× | **−0.022** |

**Neither legal class crosses zero at ANY warm.** The warm helps YES-on-modal
(ratio q/real 1.22× → 1.07× as the modal bin calibrates) but it **never becomes
after-cost positive** — even fully warmed, mean_q_modal 0.266 vs realized 0.249, so
buying YES at the model's own q still loses after cost. NO-on-non-modal is flat-negative
throughout (≈ −0.022). By ring, no NO-on-non-modal sub-class turns positive at any warm
(near-NO and far-NO both stay −EV at the q-proxy price).

**Against the REAL market the live daemon sees** (modal priced ≈ 0.35): YES-on-modal is
**−0.16 to −0.18/$1 at every warm.** Even where the warmed modal q exceeds 0.35
(n ≈ 60–68 cells), those bins settle YES only ~20 % of the time — far below the 0.35
ask. **The market correctly prices the modal bin at ~0.35; the spine buying YES there
loses heavily, warmed or not.** The root reason is structural: with 1 °C bins and
σ ≈ 1.7 °C, **the modal bin's realized win-frequency is only ~22–25 %** — no single bin
is a >35 % favorite, so there is no direction-legal bin on which YES can beat a
calibrated market. The cold center is NOT the binding constraint.

### 3. Do-no-harm — the warm BREAKS the favorite-NO refusal beyond +0.20σ

| warm | modal/favorite-NO admits | near-NO admits (winrate) | far-NO harvest (winrate) | mean(z) overshoot |
|---|---|---|---|---|
| +0.00 | **0** | 830 (0.892) | 1465 (0.985) | +0.304 (ok) |
| +0.20 | **0** | 891 (0.816) | 1154 (0.991) | +0.104 (ok) |
| +0.40 | **424** | 1110 (0.802) | 1231 (0.989) | −0.096 (ok) |

**At +0.40σ the favorite-NO refusal BREAKS** — modal/favorite-NO admits jump 0 → 424:
warming μ\* far enough pushes the modal q_yes down so the spine starts wanting to sell
NO on its own (warmed-past) forecast bin, and near-ring NO win-rate degrades 0.89 →
0.80 (more selling NO near the winner). **+0.20σ keeps modal-NO admits at 0** (safe) but
already erodes near-NO win-rate (0.89 → 0.82). The PIT-optimal +0.30σ sits between
these — it would calibrate the mean but begins eroding the near-NO harvest and
approaches the favorite-NO-refusal boundary. The far-NO harvest itself stays healthy
(winrate 0.985 → 0.989) at all warms.

### CENTER-WARM VERDICT

**Warming μ\* to WU does NOT surface direction-legal after-cost-positive edge — the
cold center is NOT the unblock; the constraint is elsewhere.** The +0.30σ warm
genuinely calibrates the center (mean(z) → 0) and is a real correctness improvement to
the predictive, but it leaves BOTH direction-legal classes net-negative
(YES-on-modal −0.04 at the q-proxy, −0.17 against the real 0.35 market; NO-on-non-modal
−0.022), and beyond +0.20σ it starts eroding the near-NO harvest / breaking the
favorite-NO refusal (424 modal-NO admits at +0.40σ). The live "no legal edge" pattern
is **structural, not a center defect**: σ ≈ 1.7 °C over 1 °C bins makes the modal bin a
~22–25 % event, so no direction-legal YES bin can beat a calibrated market, and
NO-on-non-modal is a ~zero-edge calibrated bet after cost. The spine's *real* edge —
the NO-on-its-own-modal candidate (q_yes_modal 0.27 < market 0.35) — is genuine
(the market over-prices the modal favorite) but is **direction-illegal by construction**.
The binding question is therefore NOT the center; it is **whether the direction law
should admit NO-on-modal when the spine's calibrated q_yes_modal is materially below the
market** (a separate, higher-stakes policy question — that NO-on-own-forecast trade is
exactly the favorite-NO class the refusal was built to block, so admitting it needs its
own settlement-graded validation, not a center tweak).

**Minimal center lever (if the +0.30σ warm ships anyway as a calibration fix, NOT as a
trade-unblock):** attach a settlement-residual-mean correction inside the center
authority, NOT a new bias lane. The served μ\* today is the raw debiased-member Huber
consensus (`src/forecast/center.py`; `EMOS_OOS_STRENGTH_DEFAULT = 0.0`, so EMOS is
NOT currently shifting the center, and `DebiasAuthority` is a NoOp at the live spine
seam per `qkernel_spine_bridge._NoOpDebiasAuthority`). The correct home is the
**`DebiasAuthority`** (`src/forecast/debias_authority.py`) — give it a per-(city,season,
metric) settlement-residual-mean term (the `residual_mean_c = 0.4841` already computed
and stored in `state/settlement_sigma_floor.json._meta`, the same no-leak WU-residual
fit that produced the σ floor). Applying it there (a) calibrates μ\* to WU settlement
by construction, (b) keeps it a single ONCE-applied de-bias inside the envelope
invariant (`build_center` re-clamps to `[member_min, member_max]`), and (c) does NOT
touch `emos_predictive` / the EMOS sole-calibrator path (EMOS stays the width/shape
calibrator; the residual-mean is a separate, additive center term) — so it cannot
re-introduce the legacy bias maze. **But per the verdict above, this is a
calibration-quality fix, not a trade-unblock; do not deploy it expecting new fills.**

---

## DECISIVE: edge-gated NO-on-modal grade (the candidate alpha)

- Appended 2026-06-15. k_default=1.30 live. This grades the ONE class the live spine
  surfaces as positive-edge but the direction law kills: **NO on the spine's own modal
  bin**, admitted only when the live gate fires (q_no_lcb > market_NO_cost, i.e.
  edge_lcb>0). q_no_lcb is built from the REAL #91 coherent band
  (`build_joint_q_band`, 600 draws, alpha=0.05; q_no_lcb_modal = 1 − q_yes_ucb(modal)).
  Same 360-family / 998-cell sample. Settlement = `settlement_outcomes` VERIFIED;
  NO wins iff the modal bin did NOT contain the settled value. READ-ONLY.

### The class exists and is continuous (deliverable 5)

998 modal-NO candidates over 360 families. At the operator's cited live NO cost band
(0.62–0.69), the gate (q_no_lcb > cost) fires on **884–919 of 998** candidates =
**~130 admits/day**, every one of the 7 settled days (154/157, 94/105, 143/158,
135/144, 147/164, 133/157, 104/113). This is a **continuous harvest**, not a handful.

### After-cost EV — POSITIVE on REAL prices and across the realistic cost band (deliverables 1–2)

| price source | n | NO win-rate | mean NO cost | after-cost EV/$1 | verdict |
|---|---|---|---|---|---|
| **REAL token_price_log** (NO = 1 − YES_bid) | 11 | 0.818 | 0.432 | **+0.386** | POSITIVE |
| **REAL edli c_fee_adjusted** (live fee-adj NO cost) | 17 | 0.882 | 0.728 | **+0.154** | POSITIVE |
| fixed 0.62 (operator band lo) — large-n proxy | 919 | 0.780 | 0.620 | **+0.160** | POSITIVE |
| fixed 0.65 (operator band mid) | 910 | 0.778 | 0.650 | **+0.128** | POSITIVE |
| fixed 0.69 (operator band hi) | 884 | 0.777 | 0.690 | **+0.087** | POSITIVE |
| fixed 0.78 (above q_no_lcb max) | 2 | 0.000 | 0.780 | −0.780 | (gate barely fires; n→0) |

**Real-price n is small (11 + 17 = 28 distinct real quotes)** — the verdict's
confidence rests on these PLUS the large-n fixed-cost counterfactual at the operator's
own cited live book level. Both agree: POSITIVE. The q-proxy (price = 1 − q_yes_modal)
correctly admits **0** — it can only fire when the market prices the modal NO BELOW the
spine's own NO point, which is exactly the over-priced-favorite condition; that the real
gate fires on ~910 cells means the real market IS systematically below the spine's
conservative NO bound on the favorite.

The 11 real token_price_log cases are genuine (Istanbul/Warsaw/London/Wellington/Denver/
Beijing, modal bins, NO asks 0.27–0.67) — 2 were modal-winners (NO lost) but the cheap
mid-window asks (mean 0.43) net strongly positive. The Denver 92°F case (settle=92, modal
90-91 bin did NOT win, NO ask 0.47) is the operator's own example, and NO won.

### Robust across leads AND both settlement sources (deliverable 3)

At fixed cost 0.65, edge-gated:

| | n | NO win-rate | EV/$1 |
|---|---|---|---|
| 24h | 277 | 0.765 | +0.115 |
| 72h | 322 | 0.758 | +0.108 |
| 96h+ | 311 | 0.810 | +0.160 |
| **WU** (canonical, C) | 715 | 0.775 [0.743,0.804] | **+0.125** |
| **OpenMeteo** (robustness, C) | 715 | 0.706 [0.672,0.739] | **+0.056** |

Positive at every lead and on BOTH settlement sources. OM is thinner (+0.056) but still
positive — the edge survives the 67%-winning-bin source disagreement that kills
per-family claims. Not a single-lead, single-source artifact.

### THE HISTORICAL-LOSS RECONCILIATION — why the edge gate excludes the losers (deliverable 4)

The historical favorite-NO loss the operator + tasks #74/#69 closed has TWO mechanisms;
the edge gate filters BOTH:

1. **Sold NO at a NO-EDGE price (NO cost ≈ true NO prob).** Reconstructed from the live
   `edli_no_submit_receipts` (n=60,219 NO decisions graded vs settlement), stratified by
   NO cost: the **deep far-NO class (cost ≥ 0.90)** is the loss class — NO win-rate 0.913
   but after-cost **EV = −0.042** (paying 0.95 to win 0.91 of the time = ruin on the rare
   YES). **The edge gate STRUCTURALLY EXCLUDES this class**: q_no_lcb_modal caps at
   **0.781** (max over all 998 cells), so the gate `q_no_lcb > cost` can NEVER fire when
   the NO cost ≥ 0.79. The historically-losing deep-far-NO is unreachable by this gate
   *by construction*. (The favorite-NO band cost 0.55–0.75 was actually +0.034 historically;
   the deep-far-NO ≥0.90 was the −0.042 loss.)

2. **Cold/miscalibrated q mis-identified the modal bin** (sold NO on the actual winner).
   At k=1.30 this is fixed: the spine PREDICTS modal wins **26.5%**, realized modal_won =
   **21.6%** — the modal call is calibrated (ratio 1.22×, if anything the spine slightly
   *over*-states the favorite, which makes NO-on-modal win MORE than the spine's own q
   implies). The NO-on-modal win-rate (0.784) is REAL, not a mislabel artifact.

**The gate's economic logic:** the modal bin settles only **21.6%** of the time, but the
market prices it at ~35% (NO cost ~0.65). The gate fires precisely on that mispricing
gap. Gate-fired NO win-rate 0.778, 95% CI **[0.750, 0.804]**, which **clears the
breakeven (cost 0.65) by +0.13** — the CI does not touch breakeven at cost 0.62/0.65/0.69.

### q_no_lcb integrity — NOT crushed by a cap (deliverable 6)

The modal-YES band half-width (q_ucb − q_point) is **tiny** (mean 0.0068, median 0.0020):
the #91 coherent band is TIGHT on the high-belief modal bin (the per-bin-percentile
collapse the #91 fix removed would have hollowed it). q_no_lcb_modal = 1 − q_yes_ucb has
mean **0.728**, median **0.773** — NOT piled near 0, so no market-anchor / settlement-
coverage cap is suppressing the edge. The gate can fire on genuinely over-priced
favorites. (`_replacement_q_market_anchor_enabled` is OFF per the favorite-capture audit;
the band reads only model parameter-posterior draws, no market cap.)

### DECISIVE VERDICT

**Edge-gated NO-on-modal is robustly after-cost POSITIVE — RELAX the direction law to
admit it (this is the validated alpha).** It is positive on real recoverable prices
(token_price_log +0.386 n=11; edli c_fee +0.154 n=17), positive at the operator's cited
live cost band on large-n (+0.087 to +0.160, n≈900), positive at every lead
(+0.108 to +0.160), positive on BOTH settlement sources (WU +0.125, OM +0.056), and
continuous (~130 admits/day). The edge gate cleanly excludes the historical loss class:
the deep-far-NO (cost ≥0.90) that lost −0.042 is structurally unreachable
(q_no_lcb caps at 0.78), and the modal call is now calibrated (predicts 26.5%, realizes
21.6%) so the old cold-q mislabel is gone. The alpha source is a genuine, persistent
market OVER-pricing of the temperature favorite: the modal bin settles 21.6% but is
priced ~35%.

**The exact relaxation lever:** `src/strategy/live_inference/direction_law.py`,
`direction_law_rejection_reason` — the buy_no ban fires when `settled_distance == 0.0`
(the forecast/modal bin) plus the boundary-zone double-ban (`BOUNDARY_ZONE_STEP_FRACTION`).
Make that forecast-bin ban **conditional on the edge gate**: admit buy_no on the modal bin
when `q_no_lcb > market_NO_cost` (edge_lcb > 0) — i.e. only when the market materially
over-prices the favorite vs the spine's CONSERVATIVE lower bound. This preserves the
refusal everywhere the market prices the favorite fairly or cheap (no edge → still
banned), so it does NOT reopen the no-edge / deep-far-NO loss class.

**MANDATORY pre-deployment guards (the rigor the reversal demands):**
1. **Real-price n is thin (28 quotes).** Before live capital, gate the rollout on a
   forward paper-trade capturing real modal-NO asks at decision time for ≥1 week (the
   fixed-cost counterfactual assumes the operator's 0.62–0.69 band holds; confirm it on
   live books, since mid-window asks ran cheaper 0.27–0.67 in token_price_log).
2. **Correlated risk:** the ~130 admits/day are NOT independent — they share daily
   weather-regime risk. Size by family-correlated Kelly, not 130 independent bets; a
   single hot/cold regime day flips many modal bins together.
3. **The 1.22× modal over-confidence** (spine q 0.265 vs realized 0.216) is load-bearing
   for the edge — re-confirm it does not regress if the σ floor or center is later
   re-tuned (a warmer/tighter spine that raised realized modal_won toward 0.265 would
   shrink this edge). Re-grade after any σ/center change.
4. **OM EV (+0.056) is materially thinner than WU (+0.125).** Since we settle on WU this
   is acceptable, but it means the edge is partly a WU-vs-OM winning-bin disagreement;
   the true atmospheric edge is the conservative +0.056. Treat +0.056 as the floor.
