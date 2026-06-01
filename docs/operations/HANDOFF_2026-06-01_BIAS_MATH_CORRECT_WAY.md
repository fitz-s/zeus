# HANDOFF — EDLI bias-correction: full context + "math-correct-way" plan (2026-06-01)

- Created: 2026-06-01
- Authority basis: operator directive "preserve all relevant files cited, full context report, after compaction end these problems in the math correct way once and for all; the post-restart shadow result should be here and clean unless something else is off; we tell by comparing to the correct way."
- Read this + `docs/operations/EDLI_BIAS_CORRECTION_FULL_SPEC_2026-06-01.md` (the detailed root) before acting. SHADOW only; zero capital.

## 0. CURRENT LIVE STATE (authoritative)
- Daemon `com.zeus.live-trading` **PID 81578**, restarted 2026-06-01 **19:00 UTC** (14:00 local). `loaded_sha = 6fcd05a69f` (== HEAD). `real_order_submit_enabled=false`, `reactor_mode=live_no_submit`, `edli_live_scope=forecast_only`. Capital on-chain ~$185, untouched.
- **`edli_bias_correction_enabled=false`** (config/settings.json:86, operational/uncommitted). **Log-confirmed OFF:** the last `[zeus.edli_bias] EDLI bias correction applied` line is 18:59 UTC (pre-reboot); ZERO after the 19:00 UTC boot. `bias_decay_kelly_haircut_enabled=true` (the SIZING haircut, left on; separate from p_raw shift).
- Also now live (activated by the same restart, all independently critic-verified): #98 phase-gate (forecast_only admits only PRE_SETTLEMENT_DAY), #101 unit-identity (3-way snapshot==city==bin), #95 reactor mutex-not-across-HTTP. Committed `57e2114f02` / `7deb2d2608` / `6fcd05a69f`.
- GOAL #36 unchanged: 0 fills (SHADOW). Arm = operator's separate irreversible gate, untouched.

## 1. THE PROBLEM, IN ONE LINE
The traded q did not equal the ensemble's own bin distribution because the **A4 per-city ENS bias-correction (activated 2026-05-31) warms member maxes by a per-city `effective_bias_c`** (Singapore +1.58°C, Tokyo +3.45, SF +8.4°F, …), shifting the modal bin (Singapore 31→32) → wrong-side trades. The correction is NOT a code/polarity bug; it is an over-large, unverified MAGNITUDE applied as configured. It is now OFF until re-derived correctly.

## 2. WHAT WAS PROVEN (7 root agents + 3 asymmetry agents + provenance) — preserved in the spec
Full verbatim findings: `EDLI_BIAS_CORRECTION_FULL_SPEC_2026-06-01.md` §1, §3, §9. Summary:
- **Root locus (tracer a23e5d39):** members +bias → p_raw 0.123/0.567 (bin 32 modal); MC innocent; Platt identity (lockstep). Reproduced to match traded q exactly.
- **Mechanism (bias a ae5e83ad):** empirical-Bayes mean shift, `corrected=raw−effective_bias_c`, sign correct.
- **Bin-map (a776f2c7): REFUTE off-by-one** (its "bias didn't fire" was the docstring default; live config True — resolved).
- **Polarity (4 agents) + Asymmetry (3 agents): ALL CLEAN.** `q_NO=1−q_YES` is the exact per-token region complement (ASYM-2, atol 1e-9 for point/range/shoulder); NO LCB uses the correct tail-flip `1−q_YES_UCB` (ASYM-1, proven 0.7342 vs naive-bug 0.97; B-count 0 system-wide ASYM-3); NO cost/token/settlement independently grounded. **No YES/NO inversion anywhere.** One contained cleanup: NO LCB basis (raw-posterior) vs NO point (remapped-YES) — `trade_score=min(LCB,point)` makes it non-binding. Antibody REL-NO-1..6 spec written.

## 3. HOW THE BIAS IS CALCULATED (exact) — and why the magnitude is wrong
Fitter `src/calibration/ens_bias_model.py` (`empirical_bayes_shrinkage_v1`). Settled-residual based, NOT MC.
- Per (city, season, month, product): TIGGE prior `mu_t=robust_mean(forecast−actual)` (product mx2t6 6h, ~2yr), variance `v0=var_of_mean+0.25`; OpenData likelihood `e_bar=robust_mean(forecast−actual)` (product mx2t3 3h, n live settled pairs, used if n≥20).
- Posterior `bias = w·e_bar + (1−w)·mu_t`, `w=v0/(v0+σ²/n)`. Applied `corrected=raw−bias` PRE-MC (`apply_bias_to_extrema:234`). degC; ×1.8 for °F cities.
- **The served value bypasses the file's OWN OOS gate.** `build_candidate_biases:351-391` deprecates serving the shrinkage posterior ("leans toward the harmful TIGGE prior at thin live n"); "adoption requires clearing the OOS gate (LCB + BH-FDR); at ~12–18-sample live depth nothing clears it, so **raw is served by design**." The live −1.58 (weight_live=1.0) was promoted WITHOUT a preserved OOS pass. `assert_bias_state_consistent:248` also requires Platt refit on bias-corrected pairs of the same family (train/serve lockstep).
- **Provenance gap:** first validation `~/.openclaw/workspace-venus/EDLI_BIAS_REPLAY_RESULT_2026_05_31.md` (preserved) said **"Do NOT wire the current rows"** (helped 14, hurt 13, vs-SETTLED 0/0). Activation `450b9be476` relied on a second backtest `/tmp/settled_val.py` (ephemeral) + `promotion_table.json` (MISSING), self-validated, predating June.

### 3.1 Per-city live bias magnitudes (from the daemon log, 2026-06-01 ~18:59 UTC, pre-OFF)
Range **0.4°C → 8.4°F** — non-uniform, which is the tell:
| city | eff_bias_c | native | plausibility |
|---|---|---|---|
| Toronto | −0.412 | −0.412°C | ~0.x — plausibly real |
| Wuhan | +0.408 | +0.408°C | ~0.x, POSITIVE (warm) — plausibly real |
| Shanghai | −0.966 | −0.966°C | ~1°C |
| Singapore | −1.58 | −1.58°C | 2× the ECMWF-grid discrepancy (~0.7) — over-stated |
| Tokyo | −3.45 | −3.45°C | matches our extraction error vs ECMWF (21.9 vs 26.3) — EXTRACTION ARTIFACT suspect |
| Tel Aviv | −4.00 | −4.00°C | large |
| San Francisco | −4.682 | **−8.43°F** | huge — station/grid or extraction suspect |

Operator's thesis (correct): a 51×10k-MC ensemble is accurate; true forecast bias should be **0.x°C**. The large values (Tokyo/SF/TelAviv) are NOT forecast bias — they are extraction/station artifacts the fit mis-attributes.

## 4. OPEN INVESTIGATION (running at handoff — fold results in next session)
- **BIAS-MAG-1 (a01383e8, scientist):** trace the fit; recompute the TRUE per-city bias from CLEAN contributing-snapshots (contributes_to_target_extrema=1) ensemble-daily-max vs settled WU obs; decompose the inflation (non-contributing snapshots / TIGGE prior / station-grid / small-n). → `docs/operations/BIAS_MAGNITUDE_ROOT_2026-06-01.md`.
- **BIAS-MAG-2 (a4f3a64c, debugger):** is the cold bias an ENSEMBLE DAILY-MAX EXTRACTION defect? Tokyo 21.9 vs ECMWF 26.3 (both ECMWF). Audit the local-day-window/timezone daily-max extraction. → `docs/operations/BIAS_EXTRACTION_ARTIFACT_2026-06-01.md`.

## 4.1 MAGNITUDE ROOT — CONFIRMED (BIAS-MAG-1 + BIAS-MAG-2, 2026-06-01)
Both agents reproduced from raw originals. The stored bias has TWO real, separable components; the ensemble itself is CORRECT (operator's premise holds):
- **(a) WRONG RESIDUAL STATISTIC (BIAS-MAG-1, `BIAS_MAGNITUDE_ROOT_2026-06-01.md`):** the fit (`scripts/write_promoted_edli_bias.py:56-57`) computes `effective_bias_c = mean(err)`, `err = (ensemble MEAN of the 51 member daily-maxes) − settled MAX`. Settlement resolves on the daily MAX, and the realized max lands near the ensemble's TOP, not its mean → the ensemble mean sits −0.6…−1.8°C below it with ZERO real bias. For low-bias cities this gap IS the stored value: Singapore mean−obs=−1.58 but **max−obs=−0.38**; Shanghai −1.01 vs **+0.22**; Taipei −1.88 vs −0.44; Shenzhen −0.58 vs +0.40. Reproduced to ≤0.1°C; no calc bug, no TIGGE leak (weight_live=1.0, n_prior=0), no post-peak contamination.
- **(b) GRID-REPRESENTATIVENESS (BIAS-MAG-2, `BIAS_EXTRACTION_ARTIFACT_2026-06-01.md`):** NOT extraction — re-scanned raw GRIB, recomputed local-day max = BIT-IDENTICAL to stored (error 0.00°C; timezone windows correct; DO NOT edit `extract_open_ens_localday.py`). The Tokyo 4.4°C gap is open-meteo IFS **HRES (9km deterministic)** vs Zeus IFS **0.25° ENS (25km, 51-member)**: Tokyo's 25km cell is contaminated by Tokyo Bay → genuinely cooler; even the warmest ENS member is 3°C below HRES. Singapore's cell agrees (Δ0.7). So Tokyo/SF/TelAviv have a real ENS-cell-vs-station offset (−2…−4°C), a representativeness term — not a forecast bug, not extraction.
- Cosmetic (BIAS-MAG-2 #10): Singapore stored `forecast_window_start_utc` 18:00Z under-states local-day start (16:00Z) by 2h — provenance-field hygiene, did NOT affect any daily-max value.

## 5. THE "MATH-CORRECT-WAY" FIX PLAN (do after compaction — UPDATED with §4.1)
Extraction is proven correct (do NOT touch the extractor). The correct estimator must separate the two confirmed components:
1. **Fix the residual STATISTIC.** Stop using `mean(member_maxes) − obs`. The settled value is the realized MAX = a HIGH quantile of the member-max distribution, not the mean. Options: derive any correction from the DISTRIBUTION's calibration vs settled obs (PIT/reliability of the 51-member-max + MC q against observed daily-max), or compare matched quantiles — NOT a flat mean-shift. For the low-bias cities this collapses the bias to ~0.x (or +), matching the operator's claim.
2. **Treat GRID-REPRESENTATIVENESS as a separate, real station-transfer term** for cities whose ENS cell doesn't represent the WU station (Tokyo/SF/TelAviv): measure (station settled obs − the SAME ENS grid value) over history; it is a stable offset, applied as a representativeness correction, OOS-gated per city, NOT conflated with forecast bias. Cities whose cell already agrees (Singapore Δ0.7) need ~no correction.
3. Per-city, contributing-snapshots-only, current-regime OpenData (no TIGGE-prior leak — use `opendata_bias`, not the shrinkage posterior), **OOS-gated (LCB + BH-FDR vs SETTLED)** with PRESERVED in-repo + independently-verified evidence, Platt refit on the SAME corrected domain (lockstep). Fail-closed to raw per city. Plus the §6 P3 provenance gate + P4 runtime settled tripwire.
The correct bias estimator must separate three confounded quantities the current `mean(forecast−actual)` lumps together:
1. **Extraction error** — our per-member daily-max vs a correctly-extracted daily-max (cross-check: ensemble median vs ECMWF-deterministic at the same point). If non-zero (Tokyo +4.4), FIX THE EXTRACTION (timezone/window/step-set), do NOT absorb it into a bias. This likely removes most of the large magnitudes.
2. **Station-vs-grid offset** — settled WU station daily-max vs the model grid-point. A real, stable, separable offset (urban siting); belongs in a station-transfer term, not the forecast bias.
3. **Residual forecast bias** — what remains after 1+2: expected ~0.x°C for a good ensemble.
Then: per-city, contributing-snapshots-only, current-regime (OpenData mx2t3, no TIGGE-prior leak for the served value — use the `opendata_bias` candidate, NOT the shrinkage posterior), **OOS-gated (LCB + BH-FDR vs SETTLED)** with the evidence PRESERVED in-repo + independently critic-verified, Platt refit on the SAME corrected domain (lockstep). Re-enable per-city ONLY for cities that clear the gate; fail-closed to raw otherwise. Add the §6 P3 provenance gate (a correction with no preserved+verified+current settled-OOS receipt cannot shift live q) + P4 runtime settled-truth tripwire.

## 6. POST-RESTART SHADOW VERIFICATION (the operator's "compare to the correct way")
- Bias OFF confirmed by log. Clean-q confirmation pending a fresh forecast cycle (the continuous-redecision belief cache may serve pre-restart bias-corrected beliefs until the next FORECAST_SNAPSHOT_READY recomputes — VERIFY this first next session: query a clearly-post-cache Singapore 06-03 receipt and confirm q_YES(31)≈0.5–0.6 (raw modal 31), NOT 0.124).
- Method to "tell by comparing to the correct way": for each live city/target_date, compare (a) post-restart raw q_YES(bin) ⟷ (b) the raw ensemble WMO histogram (must MATCH = clean) ⟷ (c) the future correct-way de-biased q. RAW reference (computed 2026-06-01): Singapore 06-03 {29:.039,30:.314,31:.588,32:.059}; Tokyo 06-02 {21:.255,22:.667,23:.078}; Taipei 06-02 {32:.059,33:.745,34:.196}; Sao Paulo 06-02 {18:.039,19:.196,20:.569,21:.196}. If post-restart traded modal == raw modal for all → CLEAN. If not → belief-cache staleness or another active shift ("something else is off") — investigate.

## 7. FILE MANIFEST (all cited — preserve)
Spec + handoff:
- `docs/operations/EDLI_BIAS_CORRECTION_FULL_SPEC_2026-06-01.md` (detailed root, all agents verbatim)
- `docs/operations/HANDOFF_2026-06-01_BIAS_MATH_CORRECT_WAY.md` (this file)
Agent deliverables (docs/operations/):
- q-corruption: `QCORRUPT_TRACE_2026-06-01.md`, `QCORRUPT_BIAS_ANGLE_2026-06-01.md`, `QCORRUPT_BINMAP_ANGLE_2026-06-01.md`
- polarity: `POLARITY_Q_2026-06-01.md`, `POLARITY_COST_2026-06-01.md`, `POLARITY_TOKEN_2026-06-01.md`, `POLARITY_SETTLE_2026-06-01.md`
- asymmetry: `ASYM_LCB_TAIL_2026-06-01.md`, `ASYM_POINT_SEMANTICS_2026-06-01.md`, `ASYM_SYSTEM_ENUMERATION_2026-06-01.md`
- magnitude (pending): `BIAS_MAGNITUDE_ROOT_2026-06-01.md`, `BIAS_EXTRACTION_ARTIFACT_2026-06-01.md`
Code (file:line):
- `src/calibration/ens_bias_model.py` (fitter; posterior_bias, fit_bucket, build_candidate_biases:351, assert_bias_state_consistent:248, apply_bias_to_extrema:234)
- `src/calibration/ens_bias_repo.py` (read_bias_model reader), `src/calibration/ens_error_model.py` (variance layer, NOT wired live)
- `src/engine/event_reactor_adapter.py:3487-3567` (_maybe_apply_edli_bias_correction; shift :3552; flag read :3511; correction site :3313-3315; identity-Platt :3624-3629), `:2878` (NO point complement), `:3124` (NO bootstrap LCB)
- `src/strategy/market_analysis.py:823-894` (_bootstrap_bin_no, correct tail-flip), `:391-399` (1−p_market diagnostic only)
- `src/signal/ensemble_signal.py:173-265` (p_raw_vector_from_maxes, bin_counts, MC :254-258), forecast extrema authority (extraction — locate next session)
- `src/contracts/settlement_semantics.py:128-129` (wmo_half_up), `src/types/market.py` (Bin)
Evidence + config:
- `~/.openclaw/workspace-venus/EDLI_BIAS_REPLAY_RESULT_2026_05_31.md` (preserved "do-not-wire"); `/tmp/settled_val.py` (EPHEMERAL — recover/preserve); `promotion_table.json` (MISSING)
- `config/settings.json:86` (edli_bias_correction_enabled=false now), `:87-90` (bias_decay_*), `:92` (the _note)
- `logs/zeus-live.log` (daemon log; bias-applied messages, per-city magnitudes; 691MB — tail-grep only)
- DBs: `state/zeus-forecasts.db` (ensemble_snapshots members_json, model_bias_ens via attach), `state/zeus-world.db` (edli_no_submit_receipts, no_trade_regret_events), `state/zeus_trades.db` (orderbooks)
Commits: activation `450b9be476`; wire `41e576b83e`; default-off `8756e1a27a`; #98 `57e2114f02`; #101 `7deb2d2608`; #95 `6fcd05a69f`.

## 8. FIRST ACTIONS NEXT SESSION
1. Confirm post-restart shadow q is CLEAN (raw modal) per §6 — resolve belief-cache staleness if not.
2. Fold BIAS-MAG-1/2 results → quantify extraction error vs station offset vs residual bias per city.
3. Implement the §5 correct-way estimator (extraction fix FIRST if BIAS-MAG-2 confirms; then opendata_bias + OOS gate + provenance gate). Relationship-test-first.
4. Keep bias OFF until per-city settled-OOS clears; re-enable only gate-passing cities.
