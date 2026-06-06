# EDLI Live-vs-Design — MASTER SPEC (integration of ~16 opus investigations, 2026-06-01)

- Created: 2026-06-01
- Authority basis: operator directive — integrate ALL session opus findings + the final deep reasoning + the unknown/to-explore directions into one spec.
- Status: SHADOW (daemon PID 81578, HEAD 6fcd05a69f, `real_order_submit_enabled=false`, bias correction OFF). Zero capital. Arm = operator's separate irreversible gate, untouched.
- Historical scope: this document predates the 2026-06-05 EDLI no-cap directive.
  Any mention below of `$5`, `$185`, canary notional caps, or canary order-count
  caps is historical and is superseded by
  `docs/operations/LIVE_CAP_NO_CAP_REGRESSION_EVIDENCE_2026-06-05.md`.
- This is the canonical 2026-06-01 resume document for the dropped-context seam.
  It is not current authority for EDLI live-cap/no-cap sizing law.

---

## 0. THE COMPOSITE PROBLEM
0 live alpha for hours. "All green / tests pass / no trade opportunity" was a FACADE over K structural defects, each hidden behind a passing gate. Every operator question this session pointed straight at one (bias magnitude → wrong statistic; NO=1−YES → tail proof; one Kelly → portfolio gap; design-vs-live → lost spines). The method now standing (see `[[feedback-distrust-all-green-interrogate-design-math-value]]`): interrogate every component on **design-faithfulness × math-correctness × value-provenance**, proactively.

---

## 1. THE UNIFYING ROOT (deep reasoning — Fitz #1: K=1, not N)
The EDLI **event-driven reactor** re-implemented only the **admission spine** of the designed pipeline and LOST two others. Three spines:
- **Admission spine — HEALTHY.** GREEN-only entry gate blocks YELLOW/ORANGE/RED/DATA_DEGRADED correctly; FDR → score → cert chain intact.
- **Magnitude spine — LOST.** Calibrated CI, dynamic Kelly, portfolio heat, DDD/oracle, regime throttle, per-strategy policy — all dropped.
- **RED-action spine — LOST.** RED never sweeps EDLI positions (a safety hole).

**The K=1 structural decision (DROPPED_CONTEXT_SEAM_LEDGER, agent afd6d4ef):** the reactor hands a **stripped scalar receipt payload** to shared engines where `evaluator.py` threads a **live decision object**; each engine's permissive default (`calibrator=None`, `lead_days=3.0`, `kelly_mult=0.25`, `portfolio_heat=0`, binary GREEN, no oracle/phase/strategy, flat λ/cost) **absorbs the gap silently — returns a number, never an error** — which is persisted and (once armed) drives a live order. **8 seams, one shape. Fix once: assemble a `LiveDecisionContext` per reactor event and thread it through every shared-engine call (the object `evaluator.py:4970-6097` already carries). Antibody: a relationship test — for identical (snapshot, family, portfolio, risk, oracle) input, reactor q / q_lcb / size == broad evaluator's — RED until threaded, then the seam category is unconstructable.** This is NOT 8 patches.

---

## 2. ALL OPUS FINDINGS (integrated, by area; each preserved in its doc)

### 2A. q-corruption root (3 agents) — `QCORRUPT_*`
- TRACER (a23e5d39): the q "corruption" reproduced exactly = the A4 bias-correction warming members +1.58°C BEFORE p_raw (`event_reactor_adapter.py:3552`), shifting Singapore modal 31→32. MC innocent; Platt identity (lockstep). 
- BIAS (ae5e83ad): config flag was True (activated 2026-05-31); mechanism + magnitude confirmed.
- BINMAP (a776f2c7): REFUTE off-by-one (its "bias didn't fire" was the docstring default; live config True — resolved §3 of the bias spec).

### 2B. YES/NO asymmetry (4 polarity + 3 asymmetry agents) — `POLARITY_*`, `ASYM_*` — ALL CLEAN
- `q_NO = 1−q_YES` is the EXACT per-token region complement for point/range/shoulder bins (ASYM-2, atol 1e-9) — `yes_q` is the calibrated per-bin region prob, not a point.
- NO LCB uses the CORRECT tail-flip `1 − q_YES_95pct` via `_bootstrap_bin_no` (ASYM-1, proven 0.7342 vs naive-bug 0.97). System-wide enumeration B-count = **0** (ASYM-3); `1−yes_lcb` exists nowhere. NO cost/token/settlement independently grounded (POLARITY cost/token/settle all CONFIRM clean). Antibody REL-NO-1..6 spec written.
- Contained cleanup: NO LCB basis (raw-posterior) vs NO point (remapped-YES) → `q_lcb>q_live` in saturation; `trade_score=min(LCB,point)` makes it non-binding.

### 2C. Bias magnitude (2 agents) — `BIAS_MAGNITUDE_ROOT`, `BIAS_EXTRACTION_ARTIFACT`; full spec `EDLI_BIAS_CORRECTION_FULL_SPEC`
- WRONG STATISTIC (BIAS-MAG-1): the fit (`scripts/write_promoted_edli_bias.py:56-57`) uses `mean(ensemble-mean-of-maxes − settled-max)`. Settlement is the daily MAX, which lands near the ensemble TOP not its mean → ~1.2°C inflation for low-bias cities (Singapore stored −1.58 but true **max−obs=−0.38**; Shanghai −0.97 vs **+0.22**). The ENSEMBLE IS CORRECT.
- NOT EXTRACTION (BIAS-MAG-2): GRIB recompute bit-identical (0.00°C); timezone windows correct. Tokyo's −3.45 is **grid representativeness** (open-meteo IFS HRES 9km vs Zeus IFS ENS 0.25°/25km; Tokyo Bay cools the cell) — a real station-vs-grid offset, not a forecast bug. DO NOT edit the extractor.
- The served bias bypassed the fitter's OWN OOS gate (`build_candidate_biases` "raw served by design"); activation contradicted its preserved "do-NOT-wire" validation; supporting evidence ephemeral (`/tmp/settled_val.py`) / missing (`promotion_table.json`). **Turned OFF 2026-06-01.**

### 2D. Design-vs-live (Kelly + probability + sizing/risk) — `KELLY_PORTFOLIO_GAP`, `DESIGN_VS_LIVE_*`, `DROPPED_CONTEXT_SEAM_LEDGER`
- KELLY (#107): single bet × full bankroll → $43 = 23% on one bin; the cumulative heat allocator (`would_breach`, `dynamic_kelly_mult(portfolio_heat=)`) EXISTS but is ZERO-wired in the reactor. Design-faithful max single bid = **$18.50** (10% cap), book capped $92.50.
- PROBABILITY (AUDIT-1): market fusion (`MODEL_ONLY`) + lead_days Platt term are NOT lost (by design). The one always-on divergence: `MarketAnalysis` built WITHOUT `calibrator`/`lead_days` → bootstrap CI on RAW p_raw, no σ_parameter → wrong `q_5pct`/FDR/prefilter. HIGH.
- SIZING/RISK (AUDIT-2): **D1 CRITICAL — RED never sweeps EDLI positions** (INV-05 hole). D2 flat Kelly (9 inputs dropped). D4 strategy-family flattening (`strategy_key="entry_forecast"` hardcoded). D3 ORANGE favorable-exit absent. D5 DDD/oracle absent. D6 no regime/heat throttle.
- SEAM LEDGER: 8 seams (3 HIGH/3 MED/2 LOW) → one `LiveDecisionContext` fix (§1).

### 2E. Scoring + economics (2 agents) — `PROVE_TRADE_SCORE`, `PROVE_COST_FILL`
- trade_score OBJECTIVE is DESIGN-FAITHFUL (a0718e7b): `p_fill_lcb × inf_θ(edge−λ)` = spec §2.1 executable-EV theorem. NOT a triple stack (c_95==c_stress, λ==0.01 collapse it). The suppression is in INPUTS.
- COST DOUBLE-PENALTY (aa5e192961, DEFECT): `c_95==c_stress==cost+1tick` + `penalty==stress_penalty==0.01` → ≈ real_cost + **2.0¢** flat stress per leg; `min()` a no-op. Live NYC buy_no +1.40¢ → −0.6¢ rejected. **86% of the thin-edge book recovers positive edge by dropping the hardcoded 0.01 penalty alone.** Not design-faithful (evaluator uses σ_market + $0.05 floor, warns against double-haircut). `c_95` is a misnomer (cost+1 tick, not a percentile).
- q_5pct CI artifact (a0718e7b DEFECT-1): bootstrap on uncorrected/raw surface inflates CI ~15-19¢, binds the min in 80%, kills 50.8% — same root as AUDIT-1's calibrator-not-threaded seam (+ the now-OFF bias). Fix = CI_HONESTY §4.1 (committed 69bee9b752 — VERIFY present on the live daemon HEAD).
- p_fill 0.05 floor is **DESIGN-INTENT** (a0718e7b): spec §2.1 sets P_fill|public-book-only→0; the NO-SUBMIT path awaiting FillFeasibilityEvidence. Floors 68.6% of real-edge rows. **This is a major reason fills=0 even if armed — by design, until fill-evidence is supplied.** p_fill + fee are otherwise CORRECT (the feared full-$43 fill defect does NOT exist).
- FDR: BH correct but null resamples the same point distribution → confident-wrong passes, wide-CI rejects. Keep as a weak noise filter; NEVER a calibration/false-confidence guard.

### 2F. Committed pre-arm correctness fixes (verified-live this session)
- #98 phase-gate (forecast_only admits only PRE_SETTLEMENT_DAY) — kills same-day wrong-side; live + working (153+90 phase-closed/h). 
- #101 unit-identity (fail-closed snapshot==city==bin). 
- #95 reactor mutex never across network submit. All independently critic-verified.

---

## 3. THE COMPLETE FIX PLAN (structural, sequenced; not N patches)
1. **Thread `LiveDecisionContext`** through the reactor → shared engines (the K=1 fix): carries fitted calibrator + lead_days (fixes CI surface), portfolio_heat + held positions (fixes Kelly $43 → $18.50), graded RiskLevel + regime throttle, oracle/DDD status, per-strategy policy, phase evidence. + the relationship antibody (reactor q/size == evaluator).
2. **RED-action spine:** wire `risk_level==RED → sweep EDLI positions` into the EDLI cycle (INV-05). HARD arm-blocker.
3. **Cost/score inputs:** drop the hardcoded `penalty/stress_penalty=0.01` double-stack (recovers 86% of thin-edge); replace `cost+1tick` with the design σ_market/percentile cost. Confirm CI_HONESTY §4.1 live.
4. **Bias the math-correct way (correction stays OFF until):** re-derive on the CORRECT statistic (distributional calibration of the member-max distribution vs settled obs — PIT/quantile, NOT mean-of-maxes); separate the grid-representativeness station-transfer term for cells that don't represent the station (Tokyo/SF/TelAviv); OOS-gate (LCB+BH-FDR vs SETTLED) with PRESERVED, independently-verified evidence; Platt lockstep. Provenance gate (P3) + runtime settled tripwire (P4).
5. **Kelly multiplier:** replace flat 0.25 with `dynamic_kelly_mult(ci_width, lead, win_rate, portfolio_heat, drawdown, strategy, city)` via the context.
6. **Selector (#102):** book-wide EV-per-dollar `(q−cost)/cost`, after q-correctness; portfolio-heat guard before lifting caps.
7. **p_fill / FillFeasibilityEvidence:** supply the fill-feasibility evidence so p_fill clears the 0.05 floor — REQUIRED for any fill (it's the NO-SUBMIT-by-design floor).
8. **Belief-cache:** invalidate on config/restart so stale bias-corrected beliefs flush (the residual "something off" observed post-bias-OFF).

---

## 4. UNKNOWN / TO-EXPLORE DIRECTIONS (explicit open questions)
- The exact math-correct bias estimator form: PIT/reliability calibration vs matched-quantile vs distributional shift — which is correct + minimal for the member-max → settled-max relationship? (settlement = a high quantile of the member-max distribution; how to de-bias the DISTRIBUTION, not shift the mean.)
- The grid-representativeness term: how to measure (station obs − same ENS grid value) robustly, per-city, OOS-gated; does it interact with the q distribution's spread (not just mean)?
- `LiveDecisionContext` refactor SCOPE: one object threaded, or does the reactor's per-event/no-submit-proof architecture make a full evaluator-context infeasible (then which subset)? (afd6d4ef says one refactor; needs an implementation spike.)
- FillFeasibilityEvidence: what evidence lets p_fill exceed 0.05 in forecast_only (the design's NO-SUBMIT floor) — is any fill possible pre-day0 without it?
- Is `MODEL_ONLY_POSTERIOR_MODE` optimal for forecast_only, or should a market-aware posterior fuse once liquidity/edge-vs-market matters? (design says model-only by default — revisit for live alpha.)
- Strategy-family policy needs for EDLI (4 families) — does forecast_only map to one family or several with distinct Kelly/risk?
- DDD/oracle integration in the reactor — what halts/down-sizes on a coverage outage?
- #24 settled-truth rolling gate per city — the only valid bias/calibration adjudication (NOT online forecast, which shares the cold bias).
- Post-canary exposure governance belongs to max-exposure/posture/cluster layers;
  it is not a replacement for a hidden EDLI per-order notional cap.
- Belief-cache invalidation mechanism on config change (verify it self-heals at 12Z or needs explicit flush).
- Whether the q_5pct CI artifact fully resolves with bias-OFF + calibrator-threaded, or a residual raw-vs-calibrated split remains.

---

## 4.5 BIAS OPERATIONAL DETAIL (merged from HANDOFF_2026-06-01_BIAS_MATH_CORRECT_WAY)

### Exact calculation (the value, for resume)
Fitter `src/calibration/ens_bias_model.py` (`empirical_bayes_shrinkage_v1`), settled-residual based (NOT MC). Per (city, season, month, product): TIGGE prior `mu_t=robust_mean(forecast−actual)` (mx2t6 6h), `v0=var_of_mean+0.25`; OpenData likelihood `e_bar=robust_mean(forecast−actual)` (mx2t3 3h, n live pairs, used if n≥20); posterior `bias = w·e_bar+(1−w)·mu_t`, `w=v0/(v0+σ²/n)`; applied `corrected=raw−bias` PRE-MC (`event_reactor_adapter.py:3552`), degC, ×1.8 for °F. The residual statistic is WRONG (mean-of-maxes vs realized-max → ~1.2°C inflation); served value bypassed its own OOS gate (`build_candidate_biases:351` "raw served by design"). OFF since 2026-06-01.

### Per-city live magnitudes (daemon log pre-OFF)
Toronto −0.41 (max−obs ≈ +; ~0.x real) · Wuhan +0.41 (warm) · Shanghai −0.97 (true +0.22) · Singapore −1.58 (true −0.38) · Taipei −1.80 · Tokyo −3.45 (grid-rep, Tokyo Bay) · Tel Aviv −4.00 · San Francisco −4.68°C=−8.4°F. Small = ~real; large = grid-representativeness, not forecast bias.

### Shadow-clean verification method (compare to "the correct way")
With bias OFF, post-restart q must MATCH the raw ensemble WMO distribution per city (modal == raw modal). RAW references (computed 2026-06-01, for next-session check): Singapore 06-03 `{29:.039,30:.314,31:.588,32:.059}` modal 31; Tokyo 06-02 `{21:.255,22:.667,23:.078}` modal 22; Taipei 06-02 `{32:.059,33:.745,34:.196}` modal 33; Sao Paulo 06-02 `{18:.039,19:.196,20:.569,21:.196}` modal 20. CLEAN iff traded modal == raw modal. Residual observed: the continuous-redecision belief cache still serves pre-restart bias-corrected beliefs until a fresh forecast cycle — VERIFY first next session (Singapore 06-03 q_YES(31) should be ~0.5, not 0.124); if it doesn't clean up, the cache invalidation on config change is the "something else off."

### First actions next session (resume checklist)
1. Confirm post-restart shadow q is CLEAN (raw modal) per the references above; flush belief cache if stale.
2. Implement the K=1 `LiveDecisionContext` thread (§1) + the reactor-q==evaluator-q antibody.
3. Wire RED→sweep for EDLI positions (INV-05, hard arm-blocker).
4. Drop the hardcoded `penalty/stress_penalty=0.01` cost double-stack (`event_reactor_adapter.py:4320-4321`, recovers 86% thin-edge); confirm CI_HONESTY §4.1 live.
5. Re-derive bias the math-correct way (distributional calibration, not mean-of-maxes; separate grid-representativeness term; OOS-gate vs SETTLED, preserved+verified) — correction STAYS OFF until it passes.
6. Supply FillFeasibilityEvidence so p_fill clears the by-design 0.05 floor (else no fills even if armed).
7. Selector #102, Kelly heat-allocator #107 ($18.50 cap), via the context.

## 5. FILE MANIFEST + STATUS
Master/root: `EDLI_LIVE_VS_DESIGN_MASTER_SPEC_2026-06-01.md` (this), `EDLI_BIAS_CORRECTION_FULL_SPEC_2026-06-01.md`, `HANDOFF_2026-06-01_BIAS_MATH_CORRECT_WAY.md`, `DROPPED_CONTEXT_SEAM_LEDGER_2026-06-01.md`.
Agent docs (docs/operations/): QCORRUPT_TRACE / _BIAS_ANGLE / _BINMAP_ANGLE; POLARITY_Q/_COST/_TOKEN/_SETTLE; ASYM_LCB_TAIL / _POINT_SEMANTICS / _SYSTEM_ENUMERATION; BIAS_MAGNITUDE_ROOT / BIAS_EXTRACTION_ARTIFACT; DESIGN_VS_LIVE_PROBABILITY / _SIZING_RISK; KELLY_PORTFOLIO_GAP; PROVE_TRADE_SCORE / PROVE_COST_FILL; VERIFY_98/101/58; CRITIC_SEV21_MUTEX; SEV21_MUTEX_HTTP_FIX; DAY0_PHASE_GATE_IMPL; DESIGN_CRITIC; SETTLEMENT_CORRECTNESS_AUDIT.
Tasks: #105 q-corruption, #106 asymmetry, #107 Kelly, #108 design-vs-live (+ the new RED-sweep CRITICAL, cost double-penalty, p_fill-floor-by-design).
State: daemon SHADOW PID 81578 HEAD 6fcd05a69f; bias OFF; #98/#101/#95 live; zero capital; arm untouched. PR opening (agent a67786) to ship the committed code + these docs to main. Worktree cleanup (git-master) = final step → single main worktree.
