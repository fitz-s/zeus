# QLCB_HONESTY — Is the q_lcb a true conservative lower bound? (consolidated)

> Created: 2026-06-07
> Last reused/audited: 2026-06-07
> Authority basis: OBSERVE_BASELINE.md (the "before" number); REALIGN_0_1_AUTHORITY.md (live authority = `replacement_0_1`, flag `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled=true`); settlement truth = `zeus-forecasts.settlement_outcomes WHERE authority='VERIFIED'`.
> Method: READ-ONLY (sqlite `mode=ro`) on LIVE DBs in `/Users/leofitz/zeus/state`; code read at the 7 live commits in `/Users/leofitz/zeus`. No writes. Three independent lenses (FULL_COVERAGE, CONSTRUCTION_ROOTCAUSE, EXISTING_ANTIBODY), each cross-checked against the DBs and the code by this consolidation pass.
> Goal frame: iron rule #6 — q_lcb MUST be a conservative lower bound: realized win-rate ≥ q_lcb in every band. Overconfidence = ruin. The honest calibration test is the FULL settled population, NOT the 24 edge-selected traded markets.

> Verification log (this pass, against live state 2026-06-07):
> - Truth: `settlement_outcomes` 6638 VERIFIED (5540 high / 1098 low), 2024-01-01→2026-06-06; 372 QUARANTINED excluded. Confirmed.
> - Flags: `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled=true` (config/settings.json:296); `edli_v1.q_lcb_settlement_coverage_gate_enabled=false` (settings.json:97); `edli_settlement_sigma_floor_enabled=true` (settings.json:88); `edli_settlement_sigma_floor_required=false` (settings.json:89). Confirmed.
> - Live posteriors: `forecast_posteriors` method `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` = 171 rows, **q_lcb_json NULL on 171/171**. Confirmed.
> - `settlement_sigma_floor.json`: 232 cells, sigma_floor_c min 0.95 / median 3.189 / mean 3.208 / max 7.40C, created 2026-06-05. Confirmed.
> - `probability_trace_fact`: 0 rows in zeus-forecasts, 33203 in zeus_trades. `ensemble_snapshots`: 0 rows in zeus_trades. Confirmed (open questions §5).

---

## TL;DR

q_lcb is **OPTIMISTIC (overconfident)** on the FULL settled population, in **every band of BOTH constructions** — this is systemic, not edge-selection bias. ONE root cause: the predictive lower bound is sized from **ensemble member spread (~0.67C)** instead of out-of-sample **forecast-vs-settlement residual (~2.1–2.2C)** — a **~3.2× underdispersion**. q and q_lcb both inherit it because they come from ONE too-tight distribution. The fix is almost entirely **enable-existing**: the K3 settlement-coverage shrink and the settlement_sigma_floor are built, VERIFIED-derived, and shadow-safe — but the coverage gate is flag-OFF, and on the LIVE replacement path it is also structurally un-called. Enabling requires a shadow-validation pass + a per-band PASS bar before the live license.

---

## (1) COVERAGE VERDICT — is q_lcb optimistic, how badly, where?

**Verdict: q_lcb is OPTIMISTIC in EVERY band of BOTH constructions, on the FULL settled population.** Iron rule #6 (realized ≥ q_lcb) FAILS pooled and in essentially every mid/high band on both the superseded OLD path and the path that is ACTUALLY LIVE NOW. This is not the 24-market edge-selection artifact — it reproduces at large n on the full settled population.

**Anchor reproduction (validates methodology).** Traded-cohort recovery reproduces the OBSERVE baseline: realized 20.0% (5/25) vs mean q_lcb 32.6% (baseline: 20.8% / 31.8%) — 12.6pt overconfident. So the large-n numbers below are produced by a pipeline that lands on the known baseline.

### OLD path (10k-MC bootstrap q_lcb = entry_price + edge_ci_lower; `selection_hypothesis_fact`, all buy_yes, 2026-05-04→05-30, joined to VERIFIED)
FULL tested population n=2489: pooled realized **7.4%** (183/2489) vs mean q_lcb **5.7%**. Per band (realized / mean-q_lcb):

| band | realized | mean q_lcb | verdict |
|---|---|---|---|
| 0.0–0.2 | 5.8 | 2.7 | GOOD |
| 0.2–0.4 | 20.3 | 27.6 | OPTIMISTIC |
| 0.4–0.5 | 14.7 | 44.1 | OPTIMISTIC |
| 0.5–0.6 | 9.1 (1/11) | 54.4 | OPTIMISTIC |
| 0.6–0.7 | 30.0 | 64.8 | OPTIMISTIC |
| 0.7–0.8 | 37.5 | 75.8 | OPTIMISTIC |
| 0.8+ | 100 | 92.1 | GOOD |

The SELECTED_post_fdr=1 subset (n=988) — the bins the system actually wanted to trade — is **worse**: every band <0.8 fails; 0.5–0.6 = **0/10 (0.0% realized vs 54.8% claimed)**; pooled 2.6% vs 8.7%.

### NEW path (LIVE; replacement_0_1 EMOS/Wilson q_lcb_5pct; `edli_no_submit_receipts` receipt_json, 2026-05-31→06-06)
The honest unit of observation is **per-market-instance** (one highest-q_lcb bin per distinct (city,date,metric), near-independent; raw per-bin n is inflated ~22× by correlated bins):

| view | n | pooled realized | pooled mean q_lcb |
|---|---|---|---|
| raw per-bin | 60962 | 77.7% | 94.5% (OPT all 8 bands) |
| dedup unique bins | 312 | 65.1% | 82.4% |
| **per-market-instance** | **105** | **73.3%** (77/105) | **95.1%** |

Per-market-instance bands: 0.9–1.0 → realized **74.1%** (20/27) vs claimed 95.9%; **band ==1.0 → realized 79.1% (53/67) vs claimed 100.0%**. ~22pt overconfident, EVERY band OPTIMISTIC. By metric: high n=56410 realized 78.0% vs 94.5%; low n=4552 realized 72.9% vs 94.6% — both OPTIMISTIC.

### Worst bands (the most dangerous failures — highest claimed lower bound that still loses)
- OLD selected 0.5–0.6 = **0/10** (claimed 54.8%).
- OLD full 0.7–0.8 = 37.5% (claimed 75.8%); 0.6–0.7 = 30.0% (claimed 64.8%).
- **NEW q_lcb==1.0 buy_no (dedup 110 win / 26 loss = 80.9% vs claimed 100%)**: losers settled EXACTLY in the bin the model assigned ~0 probability mass — Shanghai 28C→28, Taipei 37C→37, Seoul 27C→27, Wuhan 32C→32, with q_live≈1.000. The posterior was certain the temperature would NOT land in a bin that then settled dead-on. This is the most dangerous class: maximum claimed certainty, realized miss.

Worst cities (= the baseline's loser cities; underdispersion ratio sig_realized/sig_member): Jeddah 11.5×, Shanghai 4.9×, Istanbul 4.8×, Busan 4.6×, Toronto 4.3×, Tokyo 3.9×, San Francisco 3.5×, Kuala Lumpur 2.9×. Edge selection concentrates bets in the MOST underdispersed cities, so live optimism is *worse* than the pooled 3.24×.

---

## (2) ROOT CAUSE — where sigma is too small (both constructions), with measured ratios

**ONE structural decision executed in two places, not N bugs:** the predictive lower bound is sized from WITHIN-ENSEMBLE dispersion instead of OUT-OF-SAMPLE forecast-vs-settlement error. The 51 members agree with each other to ~0.67C but collectively miss realized settlement by ~2.2C scatter + ~1.0C warm bias.

### Measured underdispersion (member spread vs realized residual, lead=24h, member-mean − VERIFIED settlement)
| cohort | sig_member | sig_realized | ratio | n | notes |
|---|---|---|---|---|---|
| pooled high (2026-04→06) | 0.668C | 2.165C | **3.24×** | 2709 | bias −1.08C, RMSE 2.42C |
| pooled high (full year) | — | — | **3.10×** | 4609 | robustness |
| low | 0.514C | 1.463C | **2.85×** | 179 | |
| Jeddah | 0.433C | 4.965C | **11.48×** | — | bias −2.84C |
| Shanghai | 0.481C | 2.362C | **4.91×** | — | |
| San Francisco | — | — | 3.51× | — | bias −3.79C |

So q and q_lcb are both built on a distribution **~3.2× too tight** → realized (20.8%) sits far below claimed mean q_lcb (31.8%).

### Construction A — OLD 10k-MC bootstrap (canonical / opening_inertia buy_yes)
- `src/strategy/market_analysis.py:444–462` `_bootstrap_p_raw_all` resamples `self._member_maxes` and adds `mc_sigma = hypot(self._sigma_instrument, self._representativeness_sigma)`.
- `representativeness_sigma` (the per-city realized residual_sd_c — the ONLY term that injects realized error) is gated to fire ONLY when an EDLI bias correction was applied (`market_analysis.py:276–289`; `event_reactor_adapter.py:6121` `_edli_representativeness_sigma_native`). On the common uncorrected city it is **0.0**, so the bootstrap draws from the ~0.67C member cloud.
- The honest wideners — `extra_member_sigma` / `full_predictive_residual_sd` (`ens_error_model.py:152–175` → `p_raw_vector_with_error_model:257–283` → `ensemble_signal.py:252 effective_sigma=hypot`) — flow only through the EMOS/error-model path.
- The `settlement_sigma_floor` (the correct fix, median 3.18C) was created **2026-06-05 — AFTER all 24 baseline trades settled**, so those trades had no floor at all.

### Construction B — NEW replacement_0_1 (LIVE authority)
- `src/data/replacement_forecast_materializer.py` writes `q_lcb_json = None` on every posterior (INSERT at ~line 514; "q_lcb_json_role": "absent_no_calibrated_lcb_available" at :475). **Confirmed: q_lcb_json NULL on 171/171 live `forecast_posteriors` rows.**
- So `event_reactor_adapter.py:5414–5427` `_replacement_yes_lcb_for_bin` takes the **Wilson branch**: the `replacement_bundle.q_lcb` map is empty, so it falls to `successes = aifs_probabilities[bin] * member_count(51)`; `q_lcb = _wilson_lower_bound(successes, 51)` (`_wilson_lower_bound` at :5356, z=1.645).
- `aifs_probabilities` are **RAW member-vote frequencies** `count / total_members` (`src/strategy/ecmwf_aifs_sampled_2t_probabilities.py:262–275`) — NO noise / MC / residual smoothing. The Wilson interval widens only for **binomial sampling on 51 trials**; it has **zero knowledge of the 3.24× model underdispersion**. Wilson q_lcb for a 1C bin vs the honest sigma=3.18C ceiling (max point-prob 0.125): 45/51→0.788, 40/51→0.677, 35/51→0.572, 30/51→0.473 — all 4–6× above the honest 0.125 ceiling.
- The `anchor_sigma_c=3.0C` soft-anchor smoothing is applied to the POINT q only (`openmeteo_ecmwf_ifs9_aifs_soft_anchor.py:194–210`), **NOT to q_lcb**.

### The shared single-sigma coupling (why widening the bound alone is insufficient)
`src/calibration/emos_q_builder.py` (header lines 3–8, body 47–113 / 139–172): `build_emos_q` produces BOTH the point q AND the q_lcb by integrating ONE Gaussian `N(mu, sigma)` over the settlement preimage — "the SAME (mu, sigma) feeding the point q AND the lcb sigma" (line 50). When the spread is too tight, q and q_lcb are overconfident TOGETHER; the bound inherits the point estimate's overconfidence. The internal floors (`emos_sigma_model`, `settlement_sigma_floor`, `sigma_native = sigma_c * 1.8` at :98/:169) widen relative to the ensemble's own variance, never relative to realized settlement miss-distance, so internal floors alone cannot close a systemic miscalibration. (Note: the `*1.8` is an F→C native-unit factor, not an ad-hoc widener; its interaction with the floor is an open question — §5.)

---

## (3) THE FIX — prefer enabling existing mechanisms

The root cause is single; the fix is **almost entirely enable-existing**. Two built, VERIFIED-derived, shadow-safe antibodies exist; one path needs them wired in.

### FIX-A (enable-existing, primary) — turn on the K3 settlement-coverage shrink on the canonical path
- **Change:** `config/settings.json:97` `edli_v1.q_lcb_settlement_coverage_gate_enabled` `false → true`.
- **Why it is safe:** `_maybe_apply_settlement_coverage_to_lcb` (`event_reactor_adapter.py:7484`, called at :5699) currently short-circuits at line 7505 (flag false → immediate no-op). With the flag on, for each (cond, direction) it runs `settlement_backward_coverage_check` (`src/calibration/settlement_backward_coverage.py:128`, min_n=30) and `apply_settlement_coverage` (:204), which **only ever LOWERS** the LCB (`return min(q_lcb, q_lcb_out)`, :224) and **fails open** (any error keeps the upstream lcb). Re-grounded entries get `calibration_source = SETTLEMENT_ISOTONIC` (`qlcb_provenance.py:43`). Worst case = no change; never a wider/wrong-side bet.
- **Expected effect:** 0.5–1.0 bands dragged toward realized — e.g. NEW ==1.0 band 100%→~79%, 0.9–1.0 95.9%→~74%; OLD 4 high-conviction failures shrink before the trade decision (Wuhan 26C+ 0.697→0.446, Jeddah 36C 0.631→0.117, Miami 84-85F 0.607→0.097, Miami 86-87F 0.540→0.029). Overconfident high-q_lcb losers stop being selected once q_lcb drops below cost.
- **Reuses:** 100% existing — `settlement_backward_coverage.py`, `_maybe_apply_settlement_coverage_to_lcb`, `apply_settlement_coverage`, `arm_gate_coverage_blocks`, `qlcb_provenance._set_qlcb_provenance`. **Zero new code to turn it on.** (`arm_gate_coverage_blocks` at :228 already blocks ARM on coverage_ratio deviation ≥10%, flag-independently.)
- **Data availability confirmed:** all 13 traded cities have n=46–480 VERIFIED observations, all > min_n=30.

### FIX-B (small new wiring) — extend K3 to the LIVE replacement_0_1 path (structural gap)
- **Finding (lens divergence resolved here):** the K3 helper's ONLY call site is line 5699, inside the canonical/EMOS path. `_replacement_authority_probability_and_fdr_proof` (`event_reactor_adapter.py:5430`) returns at 5446/5505 and **never calls `_maybe_apply_settlement_coverage_to_lcb`**. So FIX-A alone — flipping the flag — does **not** reach the live authority path. Flag-on + the call wired in are BOTH required to protect live trades.
- **Change:** call `_maybe_apply_settlement_coverage_to_lcb(family=…, forecast_conn=…, lcb_by_direction=…)` on the `lcb_by_direction` produced by the replacement path before it returns, under the same flag guard.
- **Expected effect:** the Wilson fallback (the optimistic branch) stops being the last word for live trades once settlement evidence exists; UNLICENSED q_lcb is shrunk to realized − 1pp on the path that is actually live.
- **Reuses:** the same helper (`event_reactor_adapter.py:7484`); needs only the call with the same `forecast_conn`.
- **Note:** currently 0 NEW-path positions have settled, so K3 on this path would return INSUFFICIENT_DATA until June fills resolve — wiring it now is forward-looking, not retroactive.

### FIX-C (enable-existing) — floor the live replacement q_lcb with settlement_sigma_floor
- **Change:** in `_replacement_yes_lcb_for_bin` (`event_reactor_adapter.py:5414–5427`), compute the bound from a Gaussian CDF over the bin using `sigma ≥ settlement_sigma_floor(city, season, metric)` (`src/calibration/emos.py`) as a floor, instead of (or capping) the Wilson member-count bound. The floor table (`settlement_sigma_floor.json`, 232 cells, median 3.18C, created 2026-06-05) already exists, is VERIFIED-derived, and is flag-on (`edli_settlement_sigma_floor_enabled=true`) — it is simply not consulted on this path.
- **Expected effect:** caps any single 1C-bin q_lcb at ~0.12 (sigma 3.18C) instead of 0.47–0.79, killing the 0.5–0.7 dead band that lost ~100% in the baseline. Note `k_default=0.8` → served floor ≈ 0.8 × 3.18 ≈ 2.54C, which slightly UNDERshoots RMSE 2.42C for high-ratio cities (re-check, §5).
- **Reuses:** `settlement_sigma_floor()` / `load_sigma_floor_table` in `src/calibration/emos.py` + the existing JSON — the SAME antibody already protecting the OLD path.

### FIX-D (new math, only if A–C insufficient) — recalibrate the spread at source
After A–C, fix the POINT estimate too so q (not just q_lcb) is honest and EV/Kelly sizing stops over-betting. Either (a) have the materializer write a real `q_lcb_json` via `full_predictive_residual_sd` / `extra_member_sigma` (`ens_error_model.py:152–175`, `p_raw_vector_with_error_model:257–283`) over the AIFS member distribution, or (b) fold the realized residual sigma into the soft-anchor kernel BEFORE deriving q_lcb. Refit `emos_sigma_model` / representativeness-sigma against realized settlement miss-distance for the q_live==1.0-bin loser cities (Shanghai, Taipei, Seoul, Wuhan, Singapore high). Reuses existing predictive-residual machinery — minimal new math, no parallel system.

### FIX-E (antibody, mandatory) — relationship test makes the optimistic category unconstructable
Add a cross-module relationship test: for any (band) with n≥30 on the FULL VERIFIED settled population, assert `realized_win_rate ≥ mean_q_lcb` (Wilson LCB of realized as the acceptance floor); plus a unit test that a bin where 40/51 members cluster cannot emit q_lcb above the settlement_sigma_floor-implied ceiling. File: `tests/engine/test_replacement_0_1_qlcb_dispersion_floor.py` against `settlement_outcomes(authority='VERIFIED')`. A failing test = stage-1 antibody; the K3 SETTLEMENT_ISOTONIC re-grounding deployed-on is the full antibody.

---

## (4) BEFORE/AFTER VALIDATION PROTOCOL + PASS BAR + LICENSE

**BEFORE (this report = the honest large-n baseline, coverage gate OFF):**
- OLD path pooled realized 7.4% vs q_lcb 5.7% (full) / 2.6% vs 8.7% (selected); every mid/high band OPTIMISTIC.
- NEW path per-market-instance n=105 realized 73.3% vs q_lcb 95.1%; every band OPTIMISTIC; ==1.0 band realizes 79.1%.
- Live config: flag true (authority), coverage gate false, replacement path never calls K3, q_lcb_json NULL on all 171 posteriors.

**AFTER protocol (apples-to-apples; same metric, same population, same VERIFIED join on both sides — only the flag/wiring changes):**
1. **Shadow first.** Flip `settings.json:97 → true` in a SHADOW run. The K3 shrink logs `K3 coverage shrink … claimed→new_q` (`event_reactor_adapter.py:7558`) WITHOUT affecting trades — quantify how many bins move and by how much before going live. For FIX-B/C, run the replacement path in shadow and inspect `q_lcb_calibration_source` in the decision_audit payload (expect `SETTLEMENT_ISOTONIC` on UNLICENSED bins).
2. **Re-run the exact coverage query** (per-market-instance, one highest-q_lcb bin per (city,date,metric), joined to VERIFIED) on the post-enable cohort once ≥30 NEW markets settle.
3. **Hold the OLD-path window fixed** as the immovable before-number.
4. **Guard re-introduction** with FIX-E's relationship test in CI.

**PASS bar (the license condition):**
- For every band with n≥30: **realized win-rate ≥ mean_q_lcb** (iron rule #6), using the **Wilson LCB of realized** as the acceptance floor.
- **|coverage_ratio − 1| < 0.10** per (city, metric) band — the same tolerance `arm_gate_coverage_blocks` already enforces.
- Both must hold on the FULL settled population, NOT the edge-selected traded subset (selection bias concentrates bets in the most-underdispersed cities).

**License to enable live:** FIX-A (flag) + FIX-B (wire K3 into replacement path) may be promoted from shadow to live ONLY after (a) the shadow run shows the shrink moves bins in the OBSERVE_BASELINE direction (UNLICENSED verdicts match the realized failures — confirmed in simulation for all 4 high-conviction failures), and (b) ≥30 NEW-path markets have settled and pass the PASS bar. Flag-ON is documented HIGH RISK (it moves live q_lcb); validate on shadow output first. FIX-C (sigma floor on replacement path) is lower-risk (it only widens / lowers q_lcb) but should still shadow-confirm it does not over-shrink buy_no.

---

## (5) OPEN QUESTIONS (resolved as homework where possible)

1. **NEW-path live SETTLED book is zero.** `edli_live_profit_audit` (506 rows) has settlement_outcome NULL and q_lcb_5pct NULL on ALL rows. The NEW-path verdict is computed on NO_SUBMIT-candidate q_lcb (evaluated, not yet filled) — the honest FULL-population test, but not yet confirmed on actually-FILLED replacement_0_1 positions. **Homework:** re-run once June fills settle (markets June 6–9 settle June 7+). *Resolved-direction:* the per-market-instance test on 105 distinct (city,date,metric) is near-independent and reproduces the baseline, so the OPTIMISTIC direction is firm; only the on-filled magnitude is pending.
2. **AIFS member-spread provenance.** Underdispersion was measured on the OpenData ENS `ensemble_snapshots` archive (populated). The NEW path uses AIFS sampled-2t members, which have no dedicated settled archive — the exact AIFS-member-std vs AIFS-settlement-residual ratio is unmeasured (live posteriors target 2026-06-08/09, not yet settled). The 51-member AIFS spread is almost certainly in the same underdispersion class, but the precise AIFS ratio is pending those settlements.
3. **−1.0C warm bias is a SEPARATE location error** layered on the scale underdispersion (RMSE 2.42C vs resid-std 2.17C). Whether the replacement anchor corrects this bias or inherits it is unconfirmed. If uncorrected, even a correctly-widened q_lcb will be biased toward warm bins — so FIX-D must address bias, not only scale.
4. **The `*1.8` factor at `emos_q_builder.py:98/169`** is an F→C native-unit conversion (confirmed: `members_c = (arr − 32.0)/1.8` at :77/:147), NOT an ad-hoc widener. Its interaction with the `settlement_sigma_floor` (does the floor double-count?) is worth a code-provenance audit before refitting sigma (FIX-D).
5. **`probability_trace_fact` is EMPTY in zeus-forecasts (0 rows)** but has 33203 rows in zeus_trades ending 2026-05-21 (pre_vector_unavailable/unknown-dominated). Confirm this is expected post-cutover archival, not a live-write regression on the intended canonical q/q_lcb trace surface (now dark for the live path).
6. **`ensemble_snapshots` is EMPTY (0 rows) in zeus_trades** — recompute-from-ensemble was unavailable as a q_lcb fallback; this consolidation used `selection_hypothesis_fact` (OLD) and `edli_no_submit_receipts` receipt_json (NEW). Confirm whether `ensemble_snapshots_v2` carries live members for an independent re-derivation cross-check.
7. **Coverage gate min_n=30 is per-(city, metric) across all dates**, not per-(city, metric, season, band). For narrow bins (e.g. Miami 84-85F, n_wins≈11) the isotonic signal-to-noise may be low even when the pooled n passes. Audit per-band n before treating individual bin verdicts as high-confidence.
8. **F-unit city audit (data hazard).** F-unit cities (San Francisco 68/70/71 'C' with |dist| 12/33/35) appear in the loser miss-distance histogram — the bin_label vs settlement_value comparison may have a residual F/C handling gap for US cities. The win-test rounds settlement_value and matches numerics (robust for C cities); re-audit for F cities before quoting per-city numbers. Does not change the OPTIMISTIC direction or the C-city evidence.
9. **Should `edli_settlement_sigma_floor_required` be flipped true** once key cells are verified, to prevent silent floor bypass (fail-soft, no floor) on new cities (currently `false`; Jakarta/Manila MAM cells are not biting)?
