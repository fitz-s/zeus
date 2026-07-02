All quantification confirmed. Most activated μ-offsets are MAM cells (the season already rolled past); only a handful of JJA cells carry a live warm offset, and Tokyo|JJA|high is NOT among them. The systemic warm tilt the traces measured is real but its dominant live mechanism is the **EMOS slope b>1** (40% of high cells have b>1.05, amplifying any warm xbar) plus the residual EDLI/μ-offset on the emos-miss minority — not a uniform +4.847 maze. I have everything needed. Writing the authoritative diagnosis now.

# Zeus Forecast-Pipeline Diagnosis: μ\* Decoupling and σ Over-Tightness

**Authority:** Lead forecast scientist synthesis of 6 read-only traces, ALL load-bearing file:line claims re-verified against live DBs/logs/config this session (2026-06-14). **Critical correction applied: three traces fingered the wrong live lane** — see §0.

---

## 0. CRITICAL RECONCILIATION — the traces split, and ground truth resolves the split against the majority

Three traces (dims 1, 3, 5) reported **ROOT_CAUSE_FOUND: EDLI per-city bias maze adds +4.847°C at `event_reactor_adapter.py:11602`, giving μ\*=26, σ=0.8, q[26C]=0.469, logged 841×.** This is **NOT the current live primary lane**, and the live numbers are different. Verified facts:

1. **`config/settings.json:86` `edli_emos_sole_calibrator_enabled=true`.** The same file's line-130 note is explicit: *"legacy edli_bias_correction_enabled + bias_decay haircut are INERT on the primary FORECAST lane while edli_emos_sole_calibrator_enabled=true (bias maze bypassed); they fire only on DAY0/emos-miss."* Confirmed in code: `event_reactor_adapter.py:10944-10952` sets `_emos_regime` and routes the non-day0 cell to `build_emos_q`; the EDLI `_maybe_apply_edli_bias_correction` (`:11084`, `:11602`) is only reached on the `elif`/emos-miss path.
2. **`grep -c "EDLI bias correction applied" logs/zeus-forecast-live.log` = 0 today** (whole log, all cities). The 841× figure is from a **stale log epoch predating the flag flip** (flag committed `2026-06-07`). `EMOS_SERVE_FAILED` count today = 0 → EMOS is serving, the maze is dark.
3. **The live EMOS center for Tokyo|JJA|high is ~23.6°C, not 26.** Verified EMOS coefficients `state/emos_calibration.json` `Tokyo|JJA|high.params = [a=-0.28553, b=1.09946, c=1.72454, d=0.72383, e=-0.08999]`, model `μ = a + b·x̄`. With fresh `x̄=21.7` (cycle 2026-06-14T12Z members 20.2/21.9/21.9/21.3/21.9/23.0): **μ\* = -0.28553 + 1.09946·21.7 = 23.57°C**. σ_emos = √exp(c + d·ln(S²) + e·lead) ≈ **1.99°C** (S²=0.70, lead≈1.3d), floored at the settlement floor 1.3343°C (no-op here, model σ already wider).
4. **The EMOS μ-offset is NOT applied to Tokyo|JJA|high.** `state/emos_mu_offset.json` `Tokyo|JJA|high.activated=False` (offset −0.368, OOS do-no-harm **FAILED**: crps_after 1.638 > crps_before 1.567). `emos_q_builder.py:108` `mu_c = mu_c - offset` only fires on `activated` cells. Season keying verified correct (`:10961` month 6 → JJA, no MAM mis-route to the −1.89 offset).

**Net:** on the live lane today, Tokyo|JJA|high serves **μ\*≈23.6, σ≈2.0, q[26C]≈0.083** (scipy-verified) — a **+2.2°C** decoupling from the fresh consensus (21.4), **not +5°C**, and the 26C bin is ~8%, **not 47%**. The operator's "μ\*=26, σ=0.8, q=0.469" is reproducible **only** from the EDLI-maze lane (μ=26, σ=0.8 → q[25.5,26.5]=0.468, scipy-confirmed) — i.e. an **emos-miss/day0 candidate, or a pre-flag-flip observation.** Both lanes are diagnosed below; the document is honest about which is live.

---

## 1. ROOT-CAUSE VERDICT

### 1A. The μ\* warm decoupling — ranked by contribution to a Tokyo-class gap

| Rank | Mechanism | file:line | Live status | Contribution |
|---|---|---|---|---|
| **R1** | **EMOS slope `b>1` amplifies a warm-biased x̄ around a low pivot.** `μ = a + b·x̄`; Tokyo b=1.099, so each +1°C of x̄ above the fit pivot is amplified, and the intercept a=−0.286 does not re-center. At x̄=21.7 this yields 23.57 (+2.2 vs consensus). 40% of all high cells (79/200) carry b>1.05; max b=1.865 (KL\|DJF). | `emos_q_builder.py:86` (`emos_predictive`), `emos.py:6` (model form) | **LIVE primary** | **+2.2°C** (Tokyo); the dominant live injector |
| **R2** | **EMOS μ-offset warm shift** `μ_corr = μ\* − offset_c` (offset_c<0 ⇒ warms). Tokyo\|JJA\|high is **inactive** (do-no-harm failed), but **10 cells are activated live**, warming up to **+2.47°C** (Miami\|MAM). | `emos_q_builder.py:108`; `state/emos_mu_offset.json` | **LIVE (10 cells, not Tokyo)** | 0°C Tokyo; **+0.4…+2.5°C** elsewhere |
| **R3** | **Stale EDLI per-city bias maze** `corrected = members − eff_native` with eff_native=−4.847 ⇒ **+4.847°C**. The row (`state/zeus-world.db model_bias_ens` Tokyo\|JJA\|high) is **frozen `recorded_at=2026-06-04`** (10 days stale; `max(recorded_at)` across all 67 high EDLI rows = 2026-06-04 → the d7 producer `scripts/write_d7_rolling_edli_bias.py` has not run since), fit on the **05-24…06-02 WARM regime** (settlements 22-31°C) on a **product-mismatched** `ecmwf_opendata_mx2t3` grid-cell-max vs the served forward models and the RJTT settlement station. | `event_reactor_adapter.py:11602` (applied), `:11084` (call site), `:11597` (F×1.8 unit fork); `ens_error_model.py:279` (training path) | **DARK on primary lane today** (fires only on emos-miss/day0) | **+4.847°C when it fires** — the largest single injector, but currently latent |

**Composition.** The decoupling is a **2-of-3 active warm stack**: R1 (EMOS b>1, +2.2°C, every served high cell) is universal and live; R2 (μ-offset, +0.4…+2.5°C) adds on top for 10 activated cells; R3 (EDLI +4.847°C) is a **loaded gun** that fires the moment a cell misses EMOS or routes through DAY0. The honest de-bias these are *supposed* to encode is the settlement gap ≈ **+1.2°C** (`state/bias_scale_fit.json` Tokyo b_shrunk=1.208, matching the realized 06-11/12/13 model→settle gaps of +1.2/+1.3/+0.5). **Every live mechanism over-warms relative to that ~+1.2°C target**: R1 by ~1°C, R2 by up to +1.3°C, R3 by ~+3.6°C.

**Single most dangerous site:** `event_reactor_adapter.py:11602` (R3) — it is unbounded, stale, regime-flipped, product-mismatched, and one emos-miss away from injecting +4.847°C into a money decision. **Single most pervasive site:** `emos_q_builder.py:86`/`emos.py` (R1) — the b>1 slope silently warms 40% of high cells on every fresh run.

### 1B. The σ over-tightness

**σ=0.8 is a single-bin BACKOUT artifact, not a served width.** It is recoverable only by assuming μ=26 and solving q[25.5,26.5]=0.46852 = 2Φ(0.5/σ)−1 → σ=0.799. The live EMOS lane serves **σ≈2.0** for Tokyo (above the 1.0 floor and the 1.3343°C settlement floor). The genuine σ defect is a **path-divergent floor bypass**, two mechanisms:

1. **Soft-anchor fallback bypasses ALL σ-floors** (`replacement_forecast_materializer.py:1555` gate; floors at `:1614`/`:1622` live *inside* the BPF-override block). When a fresh run loses BPF capture it keeps the unfloored AIFS-member-vote shape (`:1511-1512`) → σ≈0.99 (posterior 3475) vs σ=1.74 with BPF (posterior 3404), same cell 4h apart. This is the **shadow** lane; it does not touch the live EMOS serve but corrupts the persisted posterior used for shadow comparison.
2. **`predictive_sigma_c` hard floor = 1.0** (`replacement_forecast_materializer.py:1119`) is **below realized day-ahead error** (`state/settlement_sigma_floor.json`: global residual_std=1.4747, MAD-σ=1.1861, Tokyo\|JJA\|high floor=1.3343, n=161). A 1.0 constant below measured evidence violates the no-unsupported-hardcode law.

The σ=0.8 spike-into-an-impossible-bin the operator saw is therefore the **EDLI-maze lane's σ** (`state/sigma_scale_fit.json` k=1.58, w=0.28 — and note this artifact is `candidate:true`, candidate, holdout "non-stationary on 5 days"), compounding the R3 +4.847°C center error. On the live EMOS lane this specific spike cannot occur (σ≈2.0).

---

## 2. BLAST RADIUS — systemic, one-directional warm

**Not Tokyo-only.** Quantified from `no_trade_regret_events` (live best-buy_yes center) vs `raw_model_forecasts` latest-cycle median, high metric, target ≥2026-06-12:

- **n=114 families; |gap|>2°C = 79 (69%); WARM 72 / COLD 7 = 10.3:1 tilt; mean +2.82°C, median +2.92°C.**
- Largest warm gaps: Qingdao +8.8, Busan +8.1, Beijing +7.8, London +7.7, Milan +7.7, KL +7.55, Tokyo +4.1.
- Warm bins carry **dominant mass** (not cheap-tail): Seoul 06-13 q=0.932@27C, Busan 06-14 q=0.870@29C (model 21.9), Seoul 06-14 q=0.844@26C (model 20.75).

**Structural confirmation of the warm machinery:**
- **40% of high EMOS cells (79/200) have slope b>1.05** (R1 universal warm amplifier); max b=1.865.
- **10 cells carry an activated live μ-offset** warming +0.40…+2.47°C (R2).
- **67 VERIFIED EDLI rows, all `recorded_at≤2026-06-04`** (R3 stale across the board; same loaded gun in every city's emos-miss/day0 path).

The gaps are largest on the **active day (06-14)** and smaller on 06-15 — consistent with the warm correction being applied to whichever cycle the lane holds, then partially washing out as fresher cold cycles land. **Direction is overwhelmingly warm because all three correction layers were fit/frozen in the late-May/early-June warm regime and none track the cool-down.**

---

## 3. THE CORRECT FORECAST CENTER + WIDTH (target design)

### Center invariants
- **INV-C1 (consensus envelope).** `μ\*` MUST lie within `[min, max]` of the fresh **de-biased** model members for the lead/cycle being served, **unless** an OBSERVED day0 extreme exceeds them (then `μ\* ≥ observed_high_so_far`, and only the observation may push μ\* outside the model envelope). Tokyo: members 20.2…23.0 → μ\* ∈ [20.2, 23.0]; 23.6 is at the edge (tolerable), 26 is a hard violation.
- **INV-C2 (single de-bias, magnitude-bounded).** Exactly ONE de-bias is applied, equal to the **realized walk-forward settlement residual** for the cell (Tokyo +1.2°C). `|applied_debias|` MUST NOT exceed the trailing realized settlement-residual mean by >2σ (Tokyo realized −0.33 ± ~1.4 → any |correction|>~3.1°C refused at activation). R1+R2+R3 currently stack 3 de-biases; collapse to one.
- **INV-C3 (freshness ceiling).** Any bias/offset row with `training_cutoff` older than a freshness ceiling (≤2-3d) is STALE → fall back to raw de-biased members, never apply. (R3's row is 10d stale and would be refused.)
- **INV-C4 (slope sanity).** EMOS `b` must be shrunk toward 1.0 (ridge/James-Stein) so a warm x̄ is not amplified; `|b−1|` beyond a fitted band flags the cell for raw fallback. 40% of cells exceed b>1.05 today with no guard.

### Width invariants
- **INV-W1 (realized-error floor, universal).** `σ_pred ≥ realized walk-forward RMSE for the lead bucket` = the per-cell settlement-residual σ (Tokyo\|JJA\|high 1.3343°C; global 1.4747°C). Applied on **every** path before bin integration — lifted OUT of the BPF gate (`replacement_forecast_materializer.py:1555`) and replacing the 1.0 hardcode at `:1119`.
- **INV-W2 (no sub-floor serve).** Refuse to serve any posterior whose effective σ < the cell's realized floor (fail-closed widen, never tighten).
- **INV-W3 (one-σ contract).** Point-q and q_lcb bootstrap draw from the SAME `N(μ,σ)` (already honored by EMOS sole-calibrator; preserve it).

### Data structures
- `emos_calibration.json` cells gain `b_shrunk` + `b_band`; loader refuses out-of-band b.
- `emos_mu_offset.json` + `model_bias_ens` rows gain enforced `training_cutoff` freshness gate + `|correction| vs realized-residual` activation guard (extends the existing one-signed `offset_sign_ok` contract).
- One `realized_sigma_floor` table (already `settlement_sigma_floor.json`) is the single σ-floor authority on all lanes.

With these, a 47%-single-degree spike on an impossible bin is **unconstructable**: INV-C1 caps μ\* at the cold consensus, INV-W1 floors σ at ~1.33°C → q over any single 1°C bin ≤ ~0.30, and the 26C bin (4σ out at μ=21.4) ≈ 0.

---

## 4. STAGED FIX PLAN (shadow → ARM → live; no honest gate weakened)

**S1 — Refit/quarantine the stale EDLI row (R3, highest single magnitude).** Re-run `scripts/write_d7_rolling_edli_bias.py --commit` and restore its daily cron; add a reader freshness gate at `ens_error_model.py:279` / `event_reactor_adapter.py:11084` that treats `training_cutoff > ceiling` as STALE → raw members.
 - *RED-on-revert:* `test_edli_bias_refuses_stale_row` — assert a row with `recorded_at` 10d old yields `applied=False`; reverting the gate flips it to applied=True (+4.847 injected).
 - *Live signal:* emos-miss/day0 candidates stop showing `eff_bias_c=-4.847`; refit lands ~−1.2°C.

**S2 — Bound every correction by realized residual (INV-C2).** Add an activation guard (shared by `emos_mu_offset` loader and `model_bias_ens` reader): refuse `|correction| > realized_settlement_residual_mean + 2σ`.
 - *RED-on-revert:* `test_correction_magnitude_bounded` — a −4.847 (or −2.47 Miami) correction against realized −0.33±1.4 is refused; revert → applied.
 - *Live signal:* activated μ-offset count drops to cells within band; no city warms >~3°C off consensus.

**S3 — Shrink EMOS slope b toward 1.0 (R1, the live primary injector).** Ridge/JS shrinkage in `scripts/fit_emos_calibration.py`; loader band-check at `emos_q_builder.py:86`.
 - *RED-on-revert:* `test_emos_slope_shrunk_within_band` — Tokyo b=1.099 → ~1.0x; revert restores 1.099 and μ\* 23.6→re-amplified.
 - *Live signal:* systemic mean gap (§2) collapses from +2.82°C toward the ~+1.2°C honest de-bias; warm:cold tilt → ~1:1.

**S4 — Consensus-envelope guard (INV-C1) at the q seam.** After μ\* is built (both EMOS and emos-miss branches), clamp/veto if μ\* exits `[min,max]` of fresh de-biased members unless an observed day0 extreme licenses it. This is the **antibody day0 was supposed to be** (dim 5: day0 obs are structurally segregated into the `settlement_capture` lane, `event_reactor_adapter.py:88-95`, and never reconcile the forecast center).
 - *RED-on-revert:* `test_mu_star_within_member_envelope` — μ\*=26 against members [20,23] is vetoed/clamped; revert → 26 passes.
 - *Live signal:* no Tokyo-class 26C selection while every fresh model + day0 obs say 21.

**S5 — Lift σ-floor out of the BPF gate (INV-W1/W2) + replace the 1.0 hardcode.** Move floors from inside `replacement_forecast_materializer.py:1555` to run on every path; set `:1119` floor = per-cell realized σ.
 - *RED-on-revert:* `test_soft_anchor_path_floored` — soft-anchor σ=0.99 floored to ≥1.3343; revert → 0.99 served.
 - *Live signal:* shadow posteriors stop the 1.74↔0.99 swing on BPF-capture loss.

**Sequencing:** all land **shadow-first** (the EMOS sole-calibrator promotion gate — per-city settled win-rate ≥ the maze — already exists; reuse it). **ARM gate:** mainstream-consistency admission (Task #19/ARM clause 3) must pass on the corrected centers before any live flip. Do **not** weaken q_lcb, the settlement-coverage gate, or the one-signed offset contract — these are honest gates; the fix is to make the **center** honest, not to loosen the **eligibility** gates downstream of it.

---

## 5. BIGGEST RISK + WHAT THE TRACES MISSED

**Biggest risk — fixing the dark lane (R3) and declaring victory while R1 silently warms live.** Three of six traces converged hard on the +4.847 EDLI maze, but that maze **fired 0× today** — it is bypassed by `edli_emos_sole_calibrator_enabled=true`. If the team refits/quarantines R3 and stops there, the live primary lane (EMOS b>1, +2.2°C, every high cell) is untouched and the systemic +2.82°C warm tilt persists. **The traces' 841× log evidence was a stale-epoch artifact; trusting it would have aimed the entire fix at a latent lane.** (Memory law: a read-only query/log-count is a LEAD, not a verdict — confirmed here by re-grepping the live log = 0.)

**What the traces missed or got wrong:**
1. **Lane identity.** Dims 1/3/5 did not check `edli_emos_sole_calibrator_enabled` before attributing the live decision to the EDLI maze. Dim 2 (σ) and dim 4 (freshness) correctly flagged "no persisted posterior centers at 26 → the live lane is a different computation," and dim 6 located it at `proof.q_posterior` — but none traced it into `build_emos_q` to find the b>1 amplification.
2. **The real live magnitude.** The decoupling on the live lane is **+2.2°C (μ\*≈23.6), not +5°C**, and **σ≈2.0, not 0.8**. The 26/0.8/0.469 triple is the maze lane or pre-flip.
3. **R1 (EMOS slope) was never named** — all six traces treated EMOS μ\* as a passthrough of x̄. It is `a + b·x̄` with b=1.099 and 40% of cells b>1.05; this is a structural warm amplifier independent of any bias table.
4. **Three stacked de-biases.** No trace counted that R1+R2+R3 can compose: the `DoubleTemperatureCorrectionError` guard (`event_reactor_adapter.py:11489`) covers EDLI-bias × grid-representativeness, but **not** EMOS-slope × EMOS-μ-offset × EDLI when a cell routes emos-miss → maze. Decompose representativeness-offset vs transient-regime-bias before activating `anchor_representativeness_debias.py` (created 06-14, currently dormant) or the warm shift doubles.

**Unverified / needs one more probe:** the exact branch the operator's observed 21:15 candidate took (emos vs emos-miss vs day0) — `forecast_posteriors` has no decision_time→candidate linkage and `_edli_q_source` is not in the live log payload dump; the receipt/live DB join was not located. If it was an emos-miss candidate, R3 is live for that cell and S1 is P0; if it was a pre-06-07 observation, R1 is the whole live story and S3 is P0. **Recommend: add `_edli_q_source` + `μ\*`/`σ` to the decision receipt so the lane is auditable per-candidate** (closes the single biggest evidence gap for the ChatGPT consult).

---

**Verified-this-session file:line index:** `config/settings.json:86,130` · `event_reactor_adapter.py:10944-10995` (EMOS gate/serve), `:11078-11091` (day0/else branch), `:11526-11608` (EDLI maze, applied `:11602`, unit fork `:11597`), `:88-95` (day0→settlement_capture), `:11489` (DoubleTemperatureCorrectionError) · `emos_q_builder.py:41` (build_emos_q), `:86` (emos_predictive), `:108` (μ-offset apply) · `emos.py:6` (μ=a+b·x̄ model) · `ens_error_model.py:279` (EDLI training subtraction) · `replacement_forecast_materializer.py:1119` (σ 1.0 hardcode), `:1511-1512` (soft-anchor), `:1555` (BPF gate), `:1614/:1622` (floors) · `state/emos_calibration.json` (Tokyo\|JJA\|high b=1.099) · `state/emos_mu_offset.json` (Tokyo\|JJA\|high activated=False; 10 activated cells) · `state/zeus-world.db model_bias_ens` (Tokyo eff=−4.847, recorded_at 2026-06-04, 67 rows all ≤06-04) · `state/settlement_sigma_floor.json` (Tokyo 1.3343, n=161) · `state/sigma_scale_fit.json` (candidate:true, candidate). **Live-log reconciliation:** EDLI "bias correction applied" = **0× today** (traces claimed 841×; stale epoch).