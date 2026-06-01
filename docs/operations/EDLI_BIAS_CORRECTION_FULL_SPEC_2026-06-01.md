# EDLI A4 Bias-Correction — Full Investigation Spec (q-vector "corruption" + YES/NO reverse audit)

- Created: 2026-06-01
- Last reused/audited: 2026-06-01
- Authority basis: operator directive 2026-06-01 ("deep investigate immediately, 7 opus agents, preserve all findings, no reduction, no conclusion, align after rg recheck, fix plan better than findings together"). Money-path-impacting (probability chain). Pipeline boot profile applies.
- Status: INVESTIGATION COMPLETE (7 agents) + RG-rechecked + provenance traced. NO code/config changed by this spec. Daemon SHADOW, real_order_submit_enabled=false, zero capital.
- Scope: the live EDLI traded probability `q` does not equal the ensemble's own bin distribution. This document preserves the complete evidence, the provenance of the activation, the resolved inter-agent conflicts, the three operator questions, and a proposed structural fix superior to the union of the findings. It deliberately does NOT foreclose the operator's keep/tune/revert decision.

---

## 0. THE SYMPTOM (reproduced, exact)

Live shadow, Singapore high, target 2026-06-03 (and 06-01), causal snapshot 1151951/1152237-class, 51 ENS members.

| stage | P(high=31) | P(high=32) | modal |
|---|---|---|---|
| raw ensemble WMO-rounded count | **0.588** | 0.059 | **31** |
| MC p_raw, NO bias (sensor noise + ASOS rounding) | 0.519 | 0.077 | **31** |
| **+1.584°C bias correction → p_raw** | **0.123** | **0.567** | **32** |
| p_cal (Platt) | 0.123 | 0.567 | 32 (identity passthrough) |
| q posterior (model-only fusion) | **0.124** | **0.565** | **32** |
| TRADED q (no_trade_regret_events) | q_YES(31)=0.124 / q_NO(31)=0.876 ; q_YES(32)=0.565 | | 32 |

The traded q places the mode on **bin 32** while the raw ensemble mode is **bin 31**. Consequence the operator surfaced: the system buys **NO on 31** (the bin the raw ensemble says is 58.8% likely) = wrong-side, and **YES on 32** (raw 5.9%). Singapore 2026-06-01 **settled to high = 31°C exactly** — i.e. the **raw** modal bin was correct and the **corrected** modal bin (32) was wrong for that settled day (n=1).

Universal across the live-summer cities (one mechanism, three magnitudes): Singapore +1.58°C (31→32), Taipei +1.80°C (33→35; raw 33=0.745 → ~0.000), Tokyo +3.45°C (22→25; Tokyo additionally ~4°C cold vs ECMWF deterministic at the RAW stage).

---

## 1. THE SEVEN OPUS AGENTS — findings preserved verbatim (no reduction)

Each agent was read-only, referenced original files + live DBs, derived its own verdict, and wrote a standalone doc. Findings below are the agents' own returned verdicts, preserved.

### 1.1 Angle-1 TRACER — `docs/operations/QCORRUPT_TRACE_2026-06-01.md` (agent a23e5d39)
> **Culprit stage: per-city BIAS CORRECTION on member maxes, BEFORE p_raw.** Not MC rounding, not Platt, not bin-index, not a sign bug. The traded q is the operator-activated A4 correction working exactly as designed — there is no defect in the math.
> Exact transform (`event_reactor_adapter.py:3552`): `corrected = members - eff_native`, `eff_native = effective_bias_c = -1.5836°C` → members shifted **+1.5836°C**. Sign correct, single-site (`members_already_corrected=True` guard verified), magnitude from a VERIFIED `model_bias_ens` row (weight_live=1.0, JJA, edli_per_city_v1).
> Per-stage P(31)/P(32) reproduced on snapshot 1151951: raw 0.588/0.059 → MC p_raw no-bias 0.519/0.077 (**bin 31 still modal; MC innocent**) → +1.584°C bias → p_raw **0.123/0.567** (entire collapse here) → p_cal identity passthrough (Platt bypassed by design because `_edli_bias_corrected=True`) → q posterior 0.123/0.567 = **matches traded q exactly**.
> Universal: Taipei +1.80°C (33→35), Tokyo +3.45°C (22→25).
> **Classification: bias-correction MAGNITUDE, not a bug.** Config turned ON 2026-05-31 with settled-truth backtest support (Singapore bin_bias≤1 32%→63%). Real open question: does the May-fit JJA bias (+1.58..+3.45°C) over-warm June live? — which `bias_decay_kelly_haircut` partially hedges.
> Discriminating probe: after 2026-06-03 settles, compare observed Singapore high vs raw-modal(31) vs corrected-modal(32). Observed=31 ⇒ over-warmed; =32 ⇒ correction recovered truth.
> Evidence: `event_reactor_adapter.py:3487-3567` (`_maybe_apply_edli_bias_correction`, shift at :3552), `:3313-3315` single hoisted correction site, `:3624-3629` identity-Platt when corrected; `ensemble_signal.py:254-258` MC loop (innocent); `settlement_semantics.py:128-129` wmo_half_up.

### 1.2 Angle-2 BIAS — `docs/operations/QCORRUPT_BIAS_ANGLE_2026-06-01.md` (agent ae5e83ad)
> **CONFIRMED.** The warm shift is the A4 empirical-Bayes ENS mean-bias correction, **ENABLED LIVE** (`edli_v1.edli_bias_correction_enabled=true`, flipped ON 2026-05-31).
> Store row `model_bias_ens(Singapore, JJA, high, month=6, ldv=ecmwf_opendata_mx2t3_local_calendar_day_max)`: `effective_bias_c=-1.5836`, `weight_live=1.0`, VERIFIED, family=edli_per_city_v1.
> Sign: `effective_bias_c = mean(forecast - observed)` → `members - (-1.58) = members + 1.58` → warms every Singapore member +1.584°C (Singapore °C, no ×1.8). Numerically reproduced: warming raw histogram (modal 31) by +1.58 collapses bin-31 (0.61→0.14) and makes bin-32 modal (0.46) ≈ traded q_YES(32)=0.565.
> Tokyo paradox reconciled — NOT wrong-sign: all cities warm a cold forecast (Tokyo −3.45→+3.45, Taipei −1.80, Singapore −1.58); Tokyo residual coldness = true bias exceeds stored −3.45 (under-warmed). Direction uniform and correct.
> Predictive-error/scale layer REFUTED: `ens_error_model.py` (N(0,total_residual_sd) MC widening + λ·bias SNR-gated) is NOT wired into the live reactor (zero refs in event_reactor_adapter.py). The shift is the flat mean subtraction only, not variance widening.
> Open question: whether +1.58°C is the correct magnitude vs current truth.

### 1.3 Angle-3 BIN-MAP — `docs/operations/QCORRUPT_BINMAP_ANGLE_2026-06-01.md` (agent a776f2c7)
> **REFUTE the bin/label off-by-one.** Decisive test on live Singapore 06-03 snapshot 1151951: bin labeled "31°C" (low=high=31, sv=[31]) receives peak p_raw=0.519; bin "32" receives 0.077. Mass lands on the correct label. (If off-by-one existed, label "32" would carry ~0.519.)
> Four sub-claims confirmed: (1) `market.py:55-58,118-126` "31°C" is a POINT bin (low==high==31, width 1, sv=[31]), inclusive contains; (2) `market.py:243-247` bin_counts content-addressed by each bin's own (low,high), WMO preimage [t-0.5,t+0.5) matches; (3) `event_reactor_adapter.py:4043-4056` parse reads range_low/range_high, live DB stores "be 31°C" as range_low=31.0,range_high=31.0, no interval inflation; (4) `candidate_binding.py:91-126` bins+candidates+quote derive from one sorted sequence, always aligned.
> **[CONFLICT — see §3]** This agent additionally asserted "the OFF-by-default, fail-closed bias correction didn't fire; p_raw peaks on 31; the warm shift is downstream in `_snapshot_p_cal` (Platt) or the bootstrap posterior." This conflicts with Angle-1/2 and is RESOLVED in §3 (the agent read the docstring default "OFF" and computed the UN-corrected p_raw; the live config overrides to ON and the shift IS in the bias step, with p_cal identity).

### 1.4 Polarity-Q — `docs/operations/POLARITY_Q_2026-06-01.md` (agent a99c5baa)
> **REFUTE YES↔NO polarity inversion on the q side.** The Singapore corruption is a continuous one-bin warm shift from the bias/calibration lane, not a polarity or index swap. Confidence HIGH.
> Production p_raw modal at 31 (0.519) — YES↔bin mapping NOT inverted. Stored q: bin31 buy_no q=0.8762 ⇒ q_YES(31)=0.1238 (matches 0.124); q peaks bin 32 (0.565) — monotonic warm re-weighting (warm-shift fingerprint).
> q_NO correctly grounded: `event_reactor_adapter.py:2878` assigns buy_no `(no_token_id, 1.0 - yes_q, no_lcb)` — `1-yes_q` is the SAME bin's complement, paired with the SAME bin's NO token + NO ask. Every `1−YES` site (adapter:2878/3132; market_analysis.py:574/397) is a same-bin complement against the right bin/side — no naive cross-bin/cross-side complement. `q_by_condition` overwritten by index-preserving `normalize(prior)` (inference_engine.py:20-36); no index reversal. Venue maps clean (11 June-3 conditions labels_swapped=0, token_map_valid=1).
> [NOTE — minor attribution: this agent said "from calibration (p_cal)"; the tracer shows p_cal is identity and the shift is the pre-p_raw bias step. Aligned in §3.]

### 1.5 Polarity-Cost — `docs/operations/POLARITY_COST_2026-06-01.md` (agent a03033456)
> **NO POLARITY BUG — cost/edge/direction are per-side independent (CONFIRM).** NO cost reads the independent NO book: `executable_cost._levels_for_direction` (executable_cost.py:158-167) routes buy_no→no_asks; explicit `assert_not_no_complement_cost` guard (executable_cost.py:98-105); NO cost never `1−yes_cost`.
> `p_market_no` independent (event_reactor_adapter.py:3340-3345). Direction selection never crosses q and cost (`:2876-2903`): buy_no pairs q_NO(=1−yes_q win-prob) with cost_NO; buy_yes pairs q_YES with cost_YES. `q_NO=1−yes_q` is the win-probability MECE identity (allowed), NOT a cost complement; NO LCB independently grounded by the NO-direction bootstrap (`:3124`). trade_score+Kelly per-direction (trade_score.py:48-51,68-71; kelly.py:62). Contract test 4/4 PASS.
> DECISIVE live evidence (Singapore 06-01, zeus_trades.db): 20 conditions show YES_ask=0.999 AND NO_ask=0.999 simultaneously (sum 1.998); naive complement would give NO=0.001 — off by +0.998. **REFUTE naive-complement bug.**
> Note: `executable_market_snapshots` is EMPTY (0 rows) in zeus-world.db this checkout; live captures live in `zeus_trades.db`; depth JSON single-token-per-row (YES/NO separate snapshot rows).

### 1.6 Polarity-Token — `docs/operations/POLARITY_TOKEN_2026-06-01.md` (agent a15f797a)
> **CONFIRM — no token/outcome/venue-side polarity inversion in buy_NO/buy_YES path.** Direction→token co-constructed (`event_reactor_adapter.py:2876-2878` buy_no→no_token_id); quote side same (`:4248-4253`). `outcome_label` grounded in token map, never assumed from direction (`:928` `"NO" if selected_token_id==candidate.no_token_id else "YES"`); snapshot contract enforces token↔label agreement (executable_market_snapshot.py:236-239). Cert carries token_id verbatim (execution.py:114); both buy_yes/buy_no → side="BUY" (Polymarket NO = BUY of no_token, never SELL of YES). Executor category-killer: `_final_intent_snapshot_metadata` (executor.py:1730-1745) independently re-derives expected token from direction vs the elected snapshot's own yes/no columns, fail-closes on mismatch; venue POST `token_id=intent.token_id, side="BUY"` (:3046/3567).
> Live: 61/61 buy_no receipts → token_id==no_token_id, outcome_label=="NO", ZERO inversions.
> **Soft spot (LOW, not an inversion):** `edli_position_bridge.py:249` falls back to direction-assumed label if cert label missing — recommend fail-closed.

### 1.7 Polarity-Settle — `docs/operations/POLARITY_SETTLE_2026-06-01.md` (agent af90432f)
> **CONFIRM — NO inversion / YES/NO mirror in settlement/fill/PnL/exit.** Outcome→side: `_extract_resolved_market_outcomes` (harvester.py:1208) maps YES to the one bin with outcomePrices==[1,0]; others NO; MECE exactly-one-winner (harvester.py:915). Payout: `won = (pos.bin_label==winning_bin)` is the YES-truth of the position's OWN bin (harvester.py:2387); exit_price buy_yes→1.0 if won, buy_no→1.0 if NOT won (:2403-2406) — independent NO leg. PnL `shares×exit_price−cost_basis` direction-agnostic (portfolio.py:2135). NO redeems own token no_token_id, CTF indexSet ["1"] (NO=index0); YES ["2"] (harvester.py:2420-2440). Exit native per direction (`_evaluate_buy_no_exit` separate; the one flip `_held_probability` buy_no→1−p_obs_yes is the CORRECT native mapping, F-1 guarded).
> DECISIVE: Singapore high=31°C, shadow buy_no on 34/35 → NO-34 pays 1, NO-35 pays 1, NO-31 (hit bin) pays 0, YES-31 pays 1 — exactly correct, verified by executing the harvester functions.
> Residual (non-polarity): V1 redeem indexSet binary-only; ranged markets → winning_index_set=None (scope limit, not inversion).

---

## 2. PROVENANCE — who turned it on, when, on what basis

Commit chain (config/settings.json is currently `M` uncommitted/operational; flag value = True):
- `41e576b83e` feat(edli-bias): **wire** per-city ensemble bias correction into live `_snapshot_p_raw` (flag-gated, fail-closed, identity-Platt lockstep).
- `8756e1a27a` fix(edli-bias): **set edli_bias_correction_enabled=false** (default-OFF per contract).
- `e532270f76` / `2919d48e79` feat(edli-kelly): pre-submit **bias-decay Kelly haircut** (interim, data-insufficient phase).
- `450b9be476` fix(edli-bias): unit-correct per-city correction (degC→degF for F cities) **+ activate A** ← **THE ACTIVATION**, 2026-05-31.
- `69bee9b752` fix(edli-score): unify bias-corrected member surface (Wall A §4.1).

Driver: the activation implements the operator's **#55 A4-reactivate directive** ("Fix=RE-ACTIVATE model_bias_ens + regime refit + lockstep cal"). So the *intent* to activate was operator-sanctioned; the *execution + the validation sufficiency* are what §3/§4 examine.

### 2.1 Validation timeline (the load-bearing governance fact)
1. **2026-05-31 09:38 — `EDLI_BIAS_REPLAY_RESULT_2026_05_31.md` (operator-plan Phase 1+2, read-only).** Verdict, verbatim: *"CONFIRMED: the current model_bias_ens rows are NOT usable as-is … Wiring them as-is would HURT ~13 cities = anti-alpha. **Do NOT wire the current rows.**"* Rows then were `effective_bias_c=NULL`, `weight_live=0.0`, `LEGACY_POOLED`, cutoff 2026-05-25, prior-dominant. vs OpenMeteo: raw 15/32 ≤1 → corrected 21/32 ≤1 (+6, marginal). vs SETTLED: **0/0 — none of the 32 receipt dates settled → no ground truth.** Per-receipt MIXED: ~14 improved / ~13 worse / ~5 same. HELPS Tokyo/Wuhan/TelAviv/Singapore/SaoPaulo; HURTS (over-corrects) Wellington −3.08, Seattle −3.44, SF −3.24, Toronto −1.89, Taipei −1.2, Shanghai +1.25. Hard caveat: *"OpenMeteo is sanity, not truth. The only rigorous adjudication is vs SETTLED observed daily-max … the proper next probe must measure correction effect on PAST SETTLED dates using the same canonical contributing-snapshot selection … that is the real OOS gate before any wiring."* Recommended order: refit current-regime → re-run settled-truth replay → require corrected bin_bias≤1 AND no catastrophic per-city regression → only THEN wire.
2. **[refit happened]** — `model_bias_ens` was repopulated between 09:38 and activation: rows now `effective_bias_c=-1.58` (Singapore), `weight_live=1.0`, VERIFIED, edli_per_city_v1 (per Angle-2/Tracer live reads). This satisfies recommendation step 1.
3. **2026-05-31 13:57 — `/tmp/settled_val.py`** — claims a settled-truth backtest (170–232 past targets/city) showing the correction moves p_raw toward truth: SF bin_bias≤1 8%→65%, TelAviv 5%→52%, Singapore 32%→63% (per the config note `_edli_bias_correction_enabled_note`). This is the asserted satisfaction of recommendation step 2.
4. **`450b9be476` "activate A"** — flag set True (step 3).

### 2.2 What is NOT preserved / NOT independent (Q3 = "have anyone check if this is valid")
- The **second** validation evidence is **ephemeral or missing**: `/tmp/settled_val.py` lives in `/tmp` (not in repo); `promotion_table.json` referenced by the config note **does not exist** at the cited path. The only repo-preserved evidence is the **09:38 "do NOT wire" doc** — i.e. the preserved evidence argues AGAINST activation; the evidence supporting activation is not preserved.
- **No independent critic/verifier** record reviewed the refit + the 13:57 settled-val before activation. It is self-validation by the activating session.
- The 13:57 settled-val covers **PAST (≤ May) settled dates**; it cannot cover **June live**. The first June settled point (Singapore 06-01 = 31°C = raw modal, corrected wrong) was unavailable to it.

---

## 3. CONFLICTS — aligned after RG recheck (operator: "there are some conflicting conclusion")

### Conflict A — Angle-3 "bias OFF / didn't fire, shift in Platt" vs Angle-1/2 "bias ON / fired, shift in bias step"
**Resolution (RG-confirmed): the bias FIRES; Angle-3 was wrong on this sub-claim.**
- `event_reactor_adapter.py:3503` docstring says *"default OFF: prepared, not active"* — that is the **code default** of the `.get(..., False)`. `:3511` reads the **live config**: `settings["edli_v1"].get("edli_bias_correction_enabled", False)`. Live config = **True** (verified: `config edli_v1.edli_bias_correction_enabled = True`). So the function does NOT early-return; it proceeds to read the VERIFIED row and apply the shift.
- Angle-3's reproduced "p_raw peaks on 31 (0.519)" is the **uncorrected** p_raw (it called the p_raw generator on raw members, not the live `_market_analysis_from_event_snapshot` path which warms members FIRST). The Tracer (Angle-1) ran the FULL live path and got corrected p_raw 0.123/0.567. Both numbers are correct for their respective inputs; the LIVE path is the corrected one.
- Net: bin-mapping is genuinely clean (Angle-3's primary verdict stands); its "bias didn't fire" secondary claim is superseded by the live config + Tracer.

### Conflict B — Polarity-Q "warm shift from calibration (p_cal)" vs Tracer "p_cal is identity; shift in the pre-p_raw bias step"
**Resolution: the shift is the pre-p_raw bias member-warming; p_cal is identity-passthrough** (Platt bypassed because `_edli_bias_corrected=True`, train/serve lockstep, `:3624-3629`). Polarity-Q correctly routed AWAY from polarity toward "the bias/calibration lane" but imprecisely named p_cal; the Tracer's per-stage numbers are the precise locus. No contradiction on the conclusion (not polarity), only on the stage name.

### Conflict C — orchestrator's earlier "system more correct with bias OFF (online forecast)" vs config note "validate vs SETTLED, not online"
**Resolution: RETRACTED.** The online forecast (open-meteo/ECMWF) **shares the ensemble cold bias**, so comparing to it *wrongly rewards raw-cold* (config note `_edli_bias_correction_enabled_note`; 09:38 doc "OpenMeteo is sanity, not truth"). The orchestrator's open-meteo check (Singapore raw −0.9 vs online) cannot adjudicate the correction. The authoritative gate is **vs SETTLED observed daily-max**. The single available June settled point (Singapore 06-01=31) favors raw, but n=1.

### Non-conflict (consistent across all): YES/NO polarity is CLEAN
All four polarity angles (q, cost, token, settle) independently REFUTE any YES↔NO inversion. `q_NO=1−q_YES` is the same-bin win-probability MECE identity; NO cost is the independent NO book; NO token/label/settlement legs are independently grounded. The "buys NO on the modal bin" behavior is NOT a polarity bug — it is the **warm-shifted q** making bin 32 (not 31) look modal, so NO-on-31 scores as edge.

---

## 4. THE THREE OPERATOR QUESTIONS — answered, no conclusion foreclosed

1. **Who turned on the bias correction?** Commit `450b9be476` ("unit-correct + activate A", 2026-05-31), wiring from `41e576b83e`, under the operator's #55 A4-reactivate directive. Config flag currently uncommitted-modified (operational), = True.
2. **Is it based on MC?** **No.** The correction VALUE is `effective_bias_c = mean(forecast − observed)` — a **settled-observation residual mean** stored in `model_bias_ens` (fitter: `src/calibration/ens_bias_model.py`, reader: `ens_bias_repo.read_bias_model`). It is a **flat additive shift applied to member maxes BEFORE the Monte-Carlo**. The MC (sensor-noise + ASOS-rounding, `ensemble_signal.py:254-258`) is a separate downstream layer that the Tracer proved INNOCENT (it does not create or move the bias; modal stays 31 without the correction). The variance/predictive-error MC layer (`ens_error_model.py`) is NOT wired live.
3. **Has anyone checked if this is valid?** Partially, and **not durably / not independently**: (a) the FIRST validation (09:38, preserved) said **DO NOT wire** the then-current rows; (b) a refit + a SECOND settled backtest (13:57, `/tmp/settled_val.py`) **claims** success (SF 8→65%, Singapore 32→63%) and justified activation, but its evidence is **ephemeral (/tmp) / missing (promotion_table.json)**, was **self-validated** (no independent critic/verifier), and **predates June live**; (c) the first June settled point (Singapore 06-01=31 = raw modal, corrected wrong) is a **counter-signal** the May validation could not include. So: a claim of validity exists, but it is unverified-independently, unpreserved, and uncovered for the live regime.

---

## 5. OPEN QUESTIONS (genuinely unresolved — left open per "no conclusion")

- Is the refit `model_bias_ens` (the VERIFIED weight_live=1.0 rows now live) the SAME object the 13:57 settled-val scored, or a later/different fit? (promotion_table.json missing → cannot confirm the served rows == the validated rows. This is a train/serve provenance gap.)
- Does the May-fit JJA per-city magnitude hold in June? Singapore 06-01 (n=1) says over-warm; the aggregate May settled backtest says help. Needs rolling June settled adjudication per city.
- The 09:38 doc found ~13 cities HURT by the (then-stale) rows; did the refit fix the per-city regressions (Wellington/Seattle/SF/Toronto/Taipei/Shanghai over-warm), or do some persist? (Not re-measured post-refit vs settled in a preserved artifact.)
- `bias_decay_kelly_haircut` halves size only when |bias|>2.0°C (C-cities). Singapore (1.58) and other sub-2°C cities trade FULL size on a possibly-over-warming correction. Is 2.0 the right threshold?

---

## 6. PROPOSED FIX PLAN (designed to exceed the union of the seven findings)

The seven agents converge on "bias-correction magnitude, not a code bug" and individually suggest *disable*, *refit*, or *gate-vs-settled*. The union is still only a **point fix on the current rows**. The structural failure is larger and is what makes this recur: **a live probability-shifting correction was activated on self-validated, unpreserved, regime-mismatched evidence, against its own preserved "do-not-wire" verdict, with no runtime self-check that the correction is helping the bin it's about to trade.** The plan below makes that category impossible, not just this instance.

**P0 — STOP THE BLEED (operator decision, reversible, shadow-safe).** Choose ONE now; all are config-only, no code:
- (a) `edli_bias_correction_enabled=false` — revert to raw (the only preserved validation said don't-wire; raw was correct for Singapore 06-01). Cost: re-exposes the genuinely-cold cities (Tokyo −4°C) to wrong-bin from the OTHER side.
- (b) keep ON but lower `bias_decay_threshold_c` 2.0→~1.3 so sub-2°C cities (Singapore 1.58) get the 0.5× size-haircut — keeps the correction, caps the damage while June settles.
- (c) keep ON unchanged — only defensible if §P2 evidence is produced and independently verified first.
Recommendation weight: (b) as the interim (preserves the operator's A4 intent, bounds risk), pending §P2.

**P1 — RELATIONSHIP TEST FIRST (antibody, before any further wiring).** A cross-module test asserting the invariant *"a live-applied per-city bias correction must not move the corrected modal bin away from the held-out SETTLED modal bin on that city's validation set."* Encode as a fixture over the past-settled contributing snapshots per active city; RED on any city where corrected bin_bias-vs-settled > raw bin_bias-vs-settled. This is the gate the 09:38 doc demanded and the activation skipped.

**P2 — PRESERVED, INDEPENDENT, PER-CITY SETTLED OOS EVIDENCE (the real validity gate).** Re-run the settled-truth replay (the 13:57 logic) but: (i) on PAST settled dates with canonical contributing-snapshot selection; (ii) **written to the repo** (`docs/operations/` + a committed `model_bias_ens` promotion receipt), not `/tmp`; (iii) per-city pass requires corrected bin_bias-vs-SETTLED ≤1 AND no per-city regression vs raw; (iv) **independently critic-verified** (separate agent re-runs the replay and confirms the numbers) before the served rows are trusted. Cities that fail → their bias row `weight_live=0` (fail-closed to raw for that city only).

**P3 — STRUCTURAL: make un-validated activation unconstructable (the category-killer).** A boot/decision-time **correction-provenance gate**: `_maybe_apply_edli_bias_correction` applies a row ONLY if that row carries a pointer to a PRESERVED, in-repo settled-OOS receipt whose per-city bin_bias≤1 evidence is current-regime and independently signed. Absent/stale/ephemeral evidence → fail-closed to raw for that city (not silent apply). This converts "activated on /tmp evidence against the preserved don't-wire verdict" from *possible* into *impossible*: a correction with no preserved, verified, current settled-OOS receipt cannot shift live q. (Mirrors the data-provenance law: data with `authority=UNVERIFIED` does not enter the computation chain.)

**P4 — RUNTIME SELF-CHECK (defense in depth, complements the offline gate).** At decision time, when a correction is about to move the modal bin, log a `bias_modal_shift` event with {raw_modal, corrected_modal, city, applied_bias_c}. A rolling monitor compares these against arriving SETTLED observations per city and auto-trips `weight_live=0` for any city whose corrected modal loses to raw modal over a trailing window. This catches June regime drift the May fit cannot (the Singapore class) without a human in the loop.

**P5 — DECOUPLE the haircut from the threshold cliff.** Replace the binary 2.0°C `bias_decay_kelly_haircut` cliff with a continuous size-discount proportional to (|applied_bias_c| and the city's settled-OOS uncertainty), so a 1.58°C correction is sized down smoothly rather than at full size — removes the Singapore-class full-size exposure structurally.

**Why this is better than the findings together:** the agents' union fixes the *number* (refit/tune the magnitude). P3+P4 fix the *system that let an unvalidated number reach live q* — a preserved-and-verified-evidence gate + a runtime settled-truth tripwire — so the next stale/regime-mismatched/self-validated correction cannot silently move live probabilities. P1 supplies the missing antibody; P2 supplies the durable evidence the activation lacked; P0/P5 bound the live exposure today.

---

## 7. REFERENCES
- Agent docs (all in `docs/operations/`): QCORRUPT_TRACE, QCORRUPT_BIAS_ANGLE, QCORRUPT_BINMAP_ANGLE, POLARITY_Q, POLARITY_COST, POLARITY_TOKEN, POLARITY_SETTLE (all dated 2026-06-01).
- Validation evidence: `~/.openclaw/workspace-venus/EDLI_BIAS_REPLAY_RESULT_2026_05_31.md` (preserved, "do NOT wire"); `/tmp/settled_val.py` (ephemeral, claims success); `promotion_table.json` (MISSING).
- Code: `src/engine/event_reactor_adapter.py` `_maybe_apply_edli_bias_correction:3487-3567` (shift :3552), correction site :3313-3315, identity-Platt :3624-3629; `src/calibration/ens_bias_model.py` (fitter), `ens_bias_repo.py` (reader), `ens_error_model.py` (variance layer, NOT wired live); `src/signal/ensemble_signal.py:254-258` (MC, innocent); `src/contracts/settlement_semantics.py:128-129` (wmo_half_up); `src/types/market.py` (Bin).
- Commits: activation `450b9be476`; wire `41e576b83e`; default-off `8756e1a27a`; haircut `e532270f76`/`2919d48e79`; member-surface unify `69bee9b752`.
- Config: `config/settings.json` (uncommitted M) `edli_v1.edli_bias_correction_enabled=true`, `bias_decay_kelly_haircut_enabled=true`, `bias_decay_threshold_c=2.0`.
- Live: Singapore 06-03 snapshot 1151951; Singapore 06-01 SETTLED high=31°C.

---

## 8. HOW THE BIAS IS CALCULATED (operator question, exact)

Fitter: `src/calibration/ens_bias_model.py` (`ESTIMATOR_NAME="empirical_bayes_shrinkage_v1"`). **Settled-residual based, NOT MC-based.**

Per (city, season[, month, cluster], product) bucket:
1. **TIGGE structural prior** (≈2 yr history, product `mx2t6` 6h): `mu_t = robust_mean(forecast − actual)` (10% trimmed); prior variance `v0 = var_of_mean(tigge) + v_transfer` (`V_TRANSFER_DEFAULT=0.25 = (0.5°C)²` — irreducible TIGGE→OpenData transfer uncertainty, since TIGGE is a DIFFERENT product/random variable).
2. **OpenData live likelihood** (≈1 mo, product `mx2t3` 3h): `e_bar = robust_mean(forecast − actual)`, `n` live settled pairs, `sigma2 = variance`, `V_O = sigma2/n`. Used ONLY if `n ≥ min_live_n` (20).
3. **Posterior shrinkage (the served bias):** `w = v0/(v0+V_O)`; **`bias = w·e_bar + (1−w)·(mu_t+delta_g)`**; `V_post = (1/v0 + 1/V_O)⁻¹`. This is the minimum-MSE posterior mean of a Normal prior updated by a Normal likelihood.
4. **Sign + application:** `bias = mean(forecast − actual)` (negative = cold); applied `corrected = raw − bias` PRE-MC (`apply_bias_to_extrema:234`), so cold/negative warms. Singapore eff=−1.58 → members +1.58. Unit: degC; ×1.8 for F-settled cities (SF/Seattle).
5. **MC is NOT the basis and NOT the locus:** the Monte-Carlo (sensor noise + ASOS rounding, `ensemble_signal.py:254-258`) runs AFTER the bias subtraction and was proven innocent by the tracer (modal stays 31 without the correction). The variance/predictive-error MC layer (`ens_error_model.py`) is NOT wired live.

### 8.1 The served value bypasses the fitter's OWN OOS gate (decisive)
The SAME file (`build_candidate_biases:351-391`) **explicitly deprecates serving the shrinkage posterior**: it REPLACES *"the TIGGE→OpenData shrinkage posterior IS the OpenData estimate (which, at thin live n, leans toward the harmful TIGGE prior)"* with a product-segregated candidate set — `raw` (0 bias), `opendata_bias` (OpenData-only mean, target-tagged), `tigge_prior` (cross-product, gate-refused for OpenData). Its docstring states: *"adoption still requires clearing the OOS gate (oos_gate LCB + BH-FDR); at the current ~12–18-sample live depth nothing clears it, so **raw is served — by design.**"* `assert_bias_state_consistent:248` additionally requires Platt to have been refit on bias-corrected pairs of the SAME error-model family (train/serve lockstep). The live served −1.58 (weight_live=1.0) was promoted WITHOUT a preserved OOS-gate pass → it does not "stand" by the fitter's own criteria.

---

## 9. ASYMMETRY (YES/NO "reverse") INVESTIGATION — operator challenge to the polarity rubber-stamp

The first polarity pass ASSERTED "q_NO=1−q_YES is the allowed MECE identity." A second, sharper pass was demanded to PROVE the derived-quantity asymmetry (tail direction, region semantics, system-wide). Results:

- **ASYM-1 LCB tail-direction (`ASYM_LCB_TAIL_2026-06-01.md`, agent a07955f8): CONFIRM correct.** Every executable NO LCB is built by `_bootstrap_bin_no` (`market_analysis.py:823-894`), which transforms each shared posterior sample to `(1 − p_post_yes) − c` BEFORE the 5th percentile — the mathematically correct tail-flip. Numeric proof at edge_n_bootstrap=500: `q_NO_lcb = 0.7342 == 1 − q_YES_95pct` to 1e-9, and ≠ the naive `1 − q_YES_5pct = 0.9718`. No `1−yes_lcb`/`1−yes_ucb` flip site exists. The only `1−yes_q` (`event_reactor_adapter.py:2878`) is the NO POINT (allowed); the LCB is independently keyed from the NO bootstrap.
  - **Contained cleanup (NOT an inversion):** 2371/6777 live buy_no rows show `q_lcb > q_live`, 99.6% in the q→1 saturation regime — a *basis mismatch* (NO point = inference-engine-REMAPPED YES; NO LCB = RAW-posterior bootstrap). `trade_score = min(LCB-branch, point-branch)` makes the inverted-high LCB non-binding, so no executable floor is inflated. Align-LCB-basis-to-remapped-point is a follow-up cleanup.
- **ASYM-2 point + bin-semantics (`ASYM_POINT_SEMANTICS_2026-06-01.md`, agent a433dd03): CONFIRM no defect.** `1−yes_q` is exact for point, finite_range, AND open_shoulder bins because `yes_q` is the calibrated per-bin REGION probability `P(round∈bin)`, not a point (proven to atol 1e-9 across bin types). Shoulders stay regions through Platt (`platt.py:80-86`); NO execution price uses native NO-token VWMP (INV-38), not `1−p_market`. Caveat: YES-side corruption (the bias shift) IS inherited by NO via the complement — a YES-axis concern (the bias-correction), not a NO-side defect.
- **ASYM-3 system-wide enumeration (`ASYM_SYSTEM_ENUMERATION_2026-06-01.md`, agent a8a6b3ef): B-count (real defects) = 0.** The pattern `q_NO_lcb = 1 − yes_lcb` exists nowhere (`rg '1\.?0?\s*-\s*\w*(lcb|ucb|_5pct|_95pct)'` → zero hits). Every NO tail (`event_reactor_adapter.py:3124`, exit `monitor_refresh.py:1833`) is `_bootstrap_bin_no` (correct `1−q_YES_UCB`); `p_fill_NO`/`cost_NO` are NO-book grounded (Class C). All reverse hits are Class-A per-token point complements (defensible). Caveats (not defects): day0 degenerate-CI `evaluator.py:2598` (symmetric with YES); inert fallback `event_reactor_adapter.py:3132`. Antibody REL-NO-1..6 written (asymmetric-bootstrap fixture makes `1−yes_lcb` RED). **The LAW is satisfied today; risk is regression, not current state.**

## 9.1 BIAS MAGNITUDE CHALLENGE (operator 2026-06-01): "ensemble is 51×10k MC, already correct; a 1.58–1.83°C bias is destructive; should be 0.x"
Aligned with the cross-checks. The true ensemble-vs-reality discrepancy appears to be 0.x°C (Singapore ensemble 30.7 vs ECMWF 31.4 ≈ 0.7), but the STORED bias is ~2× that (−1.58). Tokyo's apparent −4.4°C vs ECMWF is an EXTRACTION defect (our daily-max from the ensemble runs cold — timezone/window), not a forecast bias, so the −3.45 "correction" mis-attributes an extraction artifact. Hypothesis: the residual `mean(forecast − observed)` conflates (a) daily-max EXTRACTION error, (b) WU-station-vs-grid offset, (c) cross-product TIGGE prior (mx2t6 6h ≠ live mx2t3 3h), and possibly (d) wrong snapshots (post-peak/contrib=0, per the 09:38 doc) — inflating a true ~0.x bias to 1.58–3.45. Investigation dispatched (BIAS-MAG-1 recompute+trace, BIAS-MAG-2 extraction-artifact). This is WHY the correction must stay OFF until the magnitude is re-derived on clean, correctly-extracted contributing snapshots vs settled obs.

**Net asymmetry verdict (ASYM-1 + ASYM-2 + the 4 first-pass polarity agents): the YES/NO derivation is structurally SOUND — correct tail-flip, region-based YES, independent NO cost/token/settlement. The operator's demand to PROVE rather than assert was correct and is now satisfied; the one real finding is the contained LCB-basis cleanup.** The "buys NO on the modal bin" behavior is NOT a polarity bug — it is the bias-warped q (bin 32 looks modal), which §0–§8 root to the bias correction.

---

## 10. DECISION EXECUTED (2026-06-01, operator: "stay off as every notes says so")

- `config/settings.json`: **`edli_bias_correction_enabled` set true → FALSE** (operational/uncommitted). `bias_decay_kelly_haircut_enabled` left TRUE (the SIZING safety, separate from p_raw shift).
- Daemon **restarted in SHADOW**: PID 14287 → **81578**, `loaded_sha = 6fcd05a69f` (HEAD), `real_order_submit_enabled=false`, `reactor_mode=live_no_submit`. This makes bias-off live AND activates the independently-verified #98 (phase-gate), #101 (unit-identity), #95 (mutex-HTTP).
- Effect: q reverts to the RAW ensemble distribution (Singapore modal back to 31 = the settled truth); `_maybe_apply_edli_bias_correction` early-returns `(members, False)` at `:3511`.
- **The correction stays OFF until it clears a PRESERVED, independently-verified, current-regime settled-OOS gate** (§6 P2/P3) — exactly what the fitter's own `build_candidate_biases` design and the 2026-05-31 09:38 replay doc both require. No re-activation without that evidence.
- Capital: zero throughout (SHADOW). Arm remains the operator's separate irreversible gate, untouched.
