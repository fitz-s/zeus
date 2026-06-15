# Session Report — Tribunal Verification & Statistical-Layer Reframe

- Created: 2026-05-29
- Author: session (Opus, max effort), worktree `stat-whole-refactor` @ `08e6600d2f`
- Companion docs: `TRIBUNAL_REFRAME_2026-05-29.md` (the verdict), `tribunal_verification_2026-05-29/{A,B,C}_*.md` (raw evidence)
- Status: verification complete; LOW disaggregation measurement in flight; reframe sign-off-ready

---

## 0. TL;DR

The operator pasted a "Forecast Semantics & Calibration Tribunal" — a foundation-reshaping draft plan whose thesis is: *Zeus never defined the forecast random variable as a full-chain-consistent mathematical object, so it silently mixes products / leads / cycles / windows into one undifferentiated "forecast."* The fix it proposes is a **ForecastObject contract + candidate selection**, not more guards.

I verified the Tribunal against live code before designing on it (it was built on subagent audit docs, which are claims, not ground truth). Result:

- **The spine is correct** — the missing forecast-random-variable contract IS the root design failure, and the confirmed defects all reduce to it.
- **Three load-bearing premises are imprecise** in their literal form (W1 "live not ingested", W2 "no scale model", W3 "06/18 = ECMWF limit") — but the underlying concern survives in two of three. Accepting the Tribunal verbatim would have rebuilt partly on false premises.
- **D1 (the source-window bug) runs in production** — but blocks nothing now. HIGH immaterial (≤0.78°C); LOW exposed but **not traded live** (HIGH-only), with 60% fail-closed drops and only 8% (752 rows) contaminated. Source-fix is a parallel track due before LOW launch, not a redesign blocker.
- **Raw wins ~10/11 buckets at current depth** — but the operator's ruling stands: that is backtest luck, not live-alpha proof; the structural defects get fixed regardless.

Net path: build the full correct machinery (contract → provenance ledger → lead/product/cycle keying → wired selector → analytic p_raw); D1-LOW source-fix is blocking-first for LOW; build base = `pr3-schema-stable (e0092e89bd)`.

---

## 1. Starting state

Zeus = Polymarket weather-probability trading system. Trades temperature-bin probabilities ~2 days out using an ECMWF ensemble forecast → bias-correction → Platt calibration → Monte-Carlo bin probabilities → edge vs market price.

At session start (per `HANDOFF_2026-05-29.md` + `ENS_B_BLOCKERS_AND_M_SERIES_CONTEXT_2026-05-28.md`):
- Live = **SHADOW** (daemons compute candidates + intents, but `SUBMIT_REJECTED`, no fills). FT bias-correction flag OFF.
- Prior plan superseded: the MC-rebuild-then-promote chain (D13-D17) was retargeted to a **T-series OOS gate** (T1-T3 done, T4 "in progress", T5 waiting). **HARD-HOLD** on unshadow.
- Two upstream defects already suspected: **D1** (3h product extracted with a 6h window) and **D5** (residual ledger fit at 0-12h lead, served at ~48h trading lead).
- Worktree `stat-whole-refactor` = renamed continuation of `feat/ft-ship-64`, 62 commits ahead of origin/main, carrying the `data_version → dataset_id` + `_v2` collapse rename.

The worktree name signalled the real scope: a **whole statistics-layer refactor**, broader than the live-unshadow mission.

---

## 2. The task — operator's Tribunal (the draft plan)

The operator's framing (verbatim intent): *this change reshapes the whole system from the bottom; we must get it right before starting or the next deep-dive forces a full redo; system entropy is high and relationships are unclear — it needs to be clean; all omission points must be spotted and solved.*

Tribunal executive verdict: **overturn the entire `sd3 / full_transport` execution path.** Do not keep fixing `sd3`, do not MC-rebuild on current OpenData extraction, do not calibrate live OpenData with a TIGGE prior. Root cause is not a bias row / gate hash / Platt / city — it is the absent forecast-random-variable contract. Proposed order: fix source extraction → re-extract → message-level evidence ledger → product×cycle×lead stratified residuals → separate TIGGE/OpenData/transfer validation → candidate selection → triple-gate → small MC → selective production.

---

## 3. Method — verify before design

Per Fitz methodology (code shows function, not logic; trust code over docs; data/code provenance; legacy-until-audited) and the advisor's pre-work guidance: the Tribunal is persuasive and mathematically literate, but it is built on five subagent audit docs (`audit_{datasets,extraction,calibration_keying,inference_selection,blindspots}.md`). Those are the same class of artifact that has produced confident-but-wrong conclusions before. So every load-bearing claim was re-grounded in live code + the 33 GB `zeus-forecasts.db`, treating the Tribunal AND its audit docs as claims-to-verify.

Three parallel verifier agents (sonnet) + targeted self-checks, each emitting CONFIRMED / STALE / WRONG / UNVERIFIABLE per claim with file:line citations. One advisor consult before committing to an interpretation, one after.

---

## 4. Verification results

### 4.1 Confirmed defects — the real basis for redesign

All CONFIRMED against live code:

1. **Bias/error model is lead/cycle/product-blind (D5).** `model_bias_ens` PK = `(city, season, month, metric, live_data_version)` (`ens_bias_repo.py:38-62`); `load_bucket_residuals` pools `lead_hours <= 48` into one mean bias (`:281`), then serves it at every lead. This is the strongest real defect — not source extraction.
2. **Evidence ledger collapses product lineage.** `source_kind='prior'` is a hardcoded literal (`build_ens_residual_evidence.py:227`) — TIGGE 6h prior and OpenData 3h live rows tagged identically. Ledger also lacks all window-provenance fields (window start/end, startStep/endStep, agg_window_hours, source_run_id, available_at).
3. **Candidate scorer is too-coarse AND a stub.** Buckets only `(city, metric, season)` (`score_error_model_candidates.py:38`) — no product/cycle/lead. Worse: candidate construction + OOS scoring is "wired in a follow-up commit" (`:18-20`) — **NOT wired at HEAD.** T4 is not actually built; the handoff's "T4 in progress, scoring engine exists" overstated it.
4. **`contributes_to_target_extrema` precomputed at ingest, never rederived at read.** Written at `ingest_grib_to_snapshots.py:472`; read verbatim at `executable_forecast_reader.py:33`. Coherent ranking over possibly-wrong metadata.
5. **Single freshest snapshot elected; cross-run/cross-cycle spread discarded.** `executable_forecast_reader.py:1231` (`min(..., key=_bundle_rank)`). Run-to-run uncertainty recorded nowhere. (Amsterdam example: 16 forecasts for one target date spanning 5.14-9.30°C, 15 discarded.)
6. **p_raw via 10k MC where analytic is exact.** `ensemble_signal.py:254`, Gaussian per-member noise → mixture-of-normals CDF is closed-form (integer-rounding shifts bin edges ±0.5°, still analytic). Hours-long rebuilds are avoidable — directly serves the "clean/low-entropy" goal.

These six are all instances of one missing abstraction: nothing pins (city × metric × target-local-date × product × cycle × lead × window) to a single random variable. **That is the K-structural-decision.**

### 4.2 Corrected premises (literal vs underlying)

The Tribunal's literal assertions are often imprecise, but the underlying concern usually survives. Both halves matter.

| # | Claim | Literal | Underlying concern |
|---|---|---|---|
| W1 | Live OpenData "zero DB rows" / not ingested | **FALSE** — 20,732 live rows (11,418 mx2t3 HIGH + 9,314 mn2t3 LOW). | **VALID, worse than stated.** The 23,918 *disk* files are `mx2t6` batch (unwired). But live `mx2t3` is produced by the *same broken `STEP_HOURS=6` extractor* (§4.3). "Live is a clean separate path" is false. |
| W2 | No scale/σ; under-dispersion needs σ added | **FALSE** — location+scale exists (`residual_sd_c` + `total_residual_sd_c` as `extra_member_sigma`, `ens_error_model.py:132-173`). | **VALID.** σ is fit lead-pooled and mis-applied at trading lead. The LogLoss 11-25 under-dispersion concern survives as a keying defect (4.1#1), not a missing parameter. |
| W3 | 06z/18z absent = ECMWF limit | **MIS-ATTRIBUTED** — Zeus policy `live_authorization:false`. | Minor; data exists, Zeus declines it. |
| W4 | D1 corrupts live ≤8.6°C | **PARTLY TRUE** (§4.3) | HIGH immaterial; LOW exposed. The 8.6°C figure is cross-cycle pollution, but a real LOW defect sits underneath. |

This corrects my own first-pass overstatement (I initially labelled W1/W2 simply "WRONG" — the advisor flagged that as discounting valid concerns, including ones that overlap my own §4.1).

### 4.3 D1 deep-dive — the pivot

**Code chain (confirmed):** the external extractor `51 source data/scripts/extract_open_ens_localday.py` imports `STEP_HOURS=6` from `tigge_local_calendar_day_common.py:20` and applies `window_start = window_end - 6h` (`:399`) to the 3h product, emitting `aggregation_window_hours:6` and a malformed `"-3-3"` label at step=3 (`:408`). The 2026-05-07 cutover renamed the param to `mx2t3` but never overrode the 6h constant.

**The pivot (I was wrong, then corrected):** my first read was "the live daemon reads GRIB `endStep` directly, so D1 is a latent landmine on an unwired path." **False.** `src/data/ecmwf_open_data.py:1176` `collect_open_ens_cycle` (the live cycle collector) shells out via subprocess to that exact broken extractor at `:1488`, guarded only by `if not skip_extract:` (default-runs). **Production ingest runs the broken code.** The advisor caught that my descope rested on the window-*column* algebra, not the served daily-extreme *value* — the only thing that moves money.

**Measured value impact (recompute on live 2026-05-28 12z GRIB, correct-3h vs served-6h):**

| Metric | Δ daily-extreme | Mechanism |
|---|---|---|
| HIGH (mx2t3) | 0.00°C Americas; **0.78°C Singapore (max)**; Tokyo/Busan = NULL rows | Daily max peaks mid-afternoon, far from the dropped early-morning step. Immaterial. UTC+9 lead_0/12z: step=3 is the only in-day step → dropped → `contributes=0` (signal dropout). |
| LOW (mn2t3) | **DROP-dominant** — 9,314 rows: 5,607 (60%) DROP (fail-closed, `training_allowed=0`); **752 (8%) CONTAMINATE** (1-4°C warm min, `training_allowed=1`); rest unaffected | The dropped/mis-windowed step IS the coldest early-morning window where the daily MIN lives. UTC+9 (Tokyo/Busan) lead_1 = dominant dropout (0 served vs 51 correct members). **LOW NOT live** (`settings.json:65 apply_to_metrics:["high"]`; `manager.py:958`→RAW_UNCALIBRATED) → fix due before LOW launch, not a current live blocker. |

**Verdict (final):** D1 blocks **nothing now** — HIGH immaterial, LOW not traded live (HIGH-only), contamination 8% and confined to shadow/LOW-training. Source-fix is a **parallel track** due before (a) LOW launch and (b) any LOW evidence rebuild in the new ledger. Fix = product-derived `STEP_HOURS` + re-extract LOW. This removes the last place the Tribunal's "source-fix first" phasing held — it holds only as "before LOW is real."

### 4.4 Data reality — raw wins, but that's not the point

From `product_stratified_high.csv` + an LCB-at-n sanity check:
- OpenData raw daily-max MAE ≈ **1-2°C** (already near-unbiased).
- TIGGE→OpenData transfer correction **hurts 7/11** buckets, catastrophically: Jeddah MAM raw 2.05 → corrected **9.06**; Busan 1.30 → 3.78; Jakarta 1.24 → 2.77.
- OpenData self-correction (best case, leave-one-out): improvements sub-0.5°C. At n=12-18, **only 1/11 buckets clears a crude LCB>0 — San Francisco MAM, the known station-gap artifact.** Every other bucket cannot prove it beats raw.

Calibration-pair depth context: TIGGE 38.2M HIGH / 7.1M LOW pairs vs OpenData 592,950 HIGH / **7,858 LOW**. History is overwhelmingly TIGGE; the live product is OpenData; transfer is unavoidable for long history but must be validated product-by-product, lead-by-lead.

**Operator ruling on this:** "必须区分 luck 和实际现实中的 win rate … backtest 无法证明 live alpha." The 10/11 raw-win is in-sample luck, not live-alpha proof. The defects in §4.1 are absolute and get fixed regardless of who currently wins. Raw-as-default becomes a *safety property* of the OOS-vs-settlement accept-rule, not a license to ship lean.

---

## 5. Two mid-course corrections (honesty record)

This session corrected itself twice, both surfaced by the advisor:
1. **D1 descope reversed.** I pre-concluded "latent landmine," even biasing a subagent's framing ("bug or landmine?"). The advisor flagged that I measured columns, not values, and over-rotated toward not-a-bug. Re-measured on values → D1 is live for LOW.
2. **W1/W2 "WRONG" softened.** Labelling the Tribunal's premises flatly wrong discounted valid underlying concerns (and an internal W1↔§4 inconsistency). Reframed to "literal imprecise, concern valid."

Both are recorded so the reframe's other conclusions can be trusted: the verification was adversarial in both directions, not a rubber-stamp and not a contrarian over-correction.

---

## 6. Operator decisions integrated

1. **Rename is DONE** (operator PRAGMA + grep proof). The 3 tables (`calibration_pairs`, `ensemble_snapshots`, `source_run`) have `dataset_id` columns; the 871 residual `data_version` refs are legit value-strings / keep-table columns / Python attrs. `ens_bias_repo` binds a value-param *named* `data_version` to `WHERE e.dataset_id = ?` (positional binding — no break). My "half-done landmine" concern was wrong. **Build base = `pr3-schema-stable (e0092e89bd)`**; latest PR merges before implementation. Caveat: `MetricIdentity.data_version` (Python attr) = the lineage VALUE written into `dataset_id`, intentionally not renamed — rename it only as a separate deliberate change.
2. **Ambition = full structural remediation** (§4.4), not a lean raw-server.

---

## 7. The reframed plan

### 7.1 K-structural-decision
One typed **`ForecastObject`** (city, metric, target_local_date, source_id, product_id, dataset_id, issue_time, cycle, available_at, lead_bucket, agg_window_hours, step_window, grid/station, member_vector) + **`SettlementObject`** (city, metric, local_date, unit, station, authority). A residual is valid **only if** `ForecastObject.target == SettlementObject.target` AND product/lead are carried. **Enforced at a single writer/reader chokepoint** — otherwise it is documentation (20% survival), not a type that makes the mixed-RV category unconstructable (Fitz L4 antibody).

### 7.2 Corrected phasing (vs Tribunal §15)
0. D1 value verdict → blocking-for-LOW vs deferred (DONE for HIGH, LOW measuring).
1. ForecastObject/SettlementObject contract @ chokepoint.
2. Evidence-ledger migration: provenance + `source_kind {tigge_prior|opendata_live|paired_delta}` + lead_bucket.
3. Re-key bias/error model + WIRE the T4 scorer on (city×metric×season×product×cycle×lead_bucket).
4. Analytic p_raw (keep MC as cross-check fixture).
5. Same-product/same-lead OOS selection vs settlement → 12-city small MC smoke (cond A-E) → selective production.
- Parallel: D1 external-extractor fix (blocking-for-LOW); deliberate gate-hash re-bump retiring sd3.

The reorder vs the Tribunal: **keying + lineage contract is the spine; source-fix is blocking only for LOW, not a universal first step.**

### 7.3 What stays true from the Tribunal
Abandon sd3 / unconditional full_transport; raw dominates unless a candidate wins same-product/same-lead OOS vs settlement; ForecastObject contract is the right spine; lead/cycle/product must enter evidence + selection; analytic mixture-CDF can replace MC.

---

## 8. Task / domino structure

13 tasks, dependencies wired (TaskList). Critical path: #1 verify (done) → #2 D1 verdict (done, LOW finalizing) → #3 reframe (this) → #4 scope (done) → #5 contract → #6 ledger → #7 re-key + wire T4 → #8 analytic p_raw → #9 OOS selection + smoke → #12 unshadow (HARD-HOLD). #10 D1 LOW fix gated by #2. #11 gate re-bump gated by #7. #13 (#359 merge + rebase) external-gated.

Carry-over open threads preserved: #105 (trace `p_raw_domain` tag), #109 (scanner 1-of-11 submarkets), #121 (portfolio_quarantined RiskGuard), #110 (perf), #63 (roadmap).

---

## 9. Hard rails (unchanged)
Stats-locked (changing re-bumps gate hash): MIN_PRIOR_N=5, MIN_PAIRED_N=5, DEFAULT_MIN_LIVE_N=20, CONSERVATIVE_RESIDUAL_FLOOR_C=3.0, gate `deabf8f64bde27b7`. Producer never writes prod DBs (`_refuse_prod_db`). No partial calibration on new cities. INV-37 cross-DB ATTACH+SAVEPOINT. First live order completes programmatically. The re-key WILL re-bump the gate hash — that is intended (sd3 is being retired) and must be deliberate, with a new gate id.

---

## 10. Open items / immediate next
- **D1-LOW resolved** (agent a9878396e): 60% fail-closed drop / 8% (752) contaminate / LOW not live → source-fix is a parallel track, not a blocker. Verification phase COMPLETE.
- On PR merge: reframe is sign-off-ready; P1 (contract) starts on `pr3-schema-stable (e0092e89bd)`.
- Decide if `MetricIdentity.data_version` attr renames with the contract.
- Sequencing note: D1 source-fix must precede any LOW-track evidence rebuild (P2/P3) so the 752 contaminated + UTC+9-dropout rows don't poison LOW training.

## Evidence index
- `TRIBUNAL_REFRAME_2026-05-29.md` — the verdict.
- `tribunal_verification_2026-05-29/A_source_extraction.md` — D1 code + value measurement.
- `tribunal_verification_2026-05-29/B_calibration_keying.md` — bias/Platt/scale keying.
- `tribunal_verification_2026-05-29/C_ledger_scorer_praw.md` — ledger lineage, T4 stub, MC/analytic.
- `sd3_validation_evidence/product_stratified_high.csv` — transfer-hurts + raw-MAE data.
