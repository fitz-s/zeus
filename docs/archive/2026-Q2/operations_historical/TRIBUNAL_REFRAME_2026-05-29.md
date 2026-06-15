# Tribunal Reframe — verified premises, corrected phasing, the one structural decision

- Created: 2026-05-29
- Last audited: 2026-05-29
- Authority basis: read-only verification of the 2026-05-29 Forecast Semantics & Calibration Tribunal
  against live code + zeus-forecasts.db. Verifier evidence: `tribunal_verification_2026-05-29/{A,B,C}_*.md`.
- Status: DRAFT pending D1 value-level verdict (agent a9878396e). All else locked.

> The Tribunal's *direction* is right — the root cause is the absence of a full-chain-consistent
> forecast-random-variable contract, and "more guards" cannot fix it. But three of its load-bearing
> premises are stale/wrong, and its phasing (source-extraction-repair-first) is misordered for the
> live path. Accepting it verbatim would rebuild on a partly-false foundation — the exact redo the
> operator wants to avoid. This doc separates what is TRUE from what is OVERSTATED, then states the
> single structural decision and the corrected order.

---

## 1. Corrected premises (lead with these)

Pattern: the Tribunal's literal assertions are often imprecise, but the underlying concern usually survives. Both halves matter — the imprecision is why it cannot be implemented verbatim; the surviving concern is why the redesign is still needed. Do NOT read these as "the Tribunal was wrong."

| # | Tribunal claim | Literal verdict | Underlying concern |
|---|---|---|---|
| W1 | Live OpenData "zero DB rows" / not ingested | **FALSE** — 20,732 live mx2t3/mn2t3 rows exist. | **VALID, and worse than stated.** The 23,918 *disk* files are the `mx2t6` batch product (unwired). But the live `mx2t3` rows are produced by the **same broken `STEP_HOURS=6` extractor**, run as a subprocess by the daemon (§4). "Live is a clean separate path" is false — live IS affected. Tribunal's instinct (live corrupted) right; its mechanism (zero rows) muddled. |
| W2 | No scale/σ correction; under-dispersion needs σ added | **FALSE** — location+scale exists (`residual_sd_c` + `total_residual_sd_c` as `extra_member_sigma` in MC, ens_error_model.py:132-173). | **VALID.** σ is fit on lead-pooled (≤48h) short-lead residuals, then applied at ~48h+ trading lead — mis-fed, not absent. The LogLoss 11-25 under-dispersion concern survives; it's a keying defect (§2.1), not a missing-parameter defect. |
| W3 | 06z/18z absent due to ECMWF limitation | **MIS-ATTRIBUTED** — Zeus policy `source_release_calendar.yaml` `live_authorization:false`. | Minor; data exists, Zeus declines it. |
| W4 | D1 (STEP_HOURS=6 on 3h product) corrupts live ≤8.6°C | **PARTLY TRUE — see §4** | Production DOES run the broken extractor (`collect_open_ens_cycle` → `extract_open_ens_localday.py` at ecmwf_open_data.py:1488, guarded only by `if not skip_extract`). HIGH immaterial (≤0.78°C); **LOW (mn2t3) materially exposed — measuring (§4)**. The 8.6°C headline is cross-cycle pollution, but a real LOW defect sits underneath. |

The 8.6°C headline (mx2t3 vs mx2t6, n=425) mixes different issue cycles; it is NOT a clean measure of the window bug. Same-issue control is the only valid magnitude test (§4).

---

## 2. Confirmed defects — these justify the redesign (the real K-decision)

All CONFIRMED against live code:

1. **Bias/error model is lead/cycle/product-blind.** `model_bias_ens` PK = `(city, season, month, metric, live_data_version)`; `load_bucket_residuals` pools `lead_hours <= 48` into one mean bias, then serves it at every lead. (ens_bias_repo.py:38-62, 281). **This is the strongest real defect — D5 — not source extraction.**
2. **Evidence ledger collapses product lineage.** `source_kind='prior'` is a hardcoded literal (build_ens_residual_evidence.py:227) — TIGGE 6h prior and OpenData 3h live rows tagged identically. Ledger also lacks all window-provenance fields (window start/end, startStep/endStep, agg_window_hours, source_run_id).
3. **Candidate scorer is both too-coarse AND a stub.** Buckets only `(city, metric, season)` — no product/cycle/lead. And `score_error_model_candidates.py` candidate-construction + OOS scoring is "wired in a follow-up commit" — NOT wired at HEAD. T4 is not actually built.
4. **`contributes_to_target_extrema` precomputed at ingest, never rederived at read.** Reader trusts the stored int (executable_forecast_reader.py:33). Coherent ranking over possibly-wrong metadata.
5. **Single freshest snapshot elected; cross-run/cross-cycle spread discarded.** No record of run-to-run uncertainty (executable_forecast_reader.py:1231).
6. **p_raw via 10k MC where analytic suffices.** Gaussian per-member noise → exact mixture-of-normals CDF (rounding shifts bin edges ±0.5°, still closed-form). Hours-long rebuilds are avoidable.

The Tribunal correctly names the unifying cause: **no single typed object pins (city × metric × target-local-date × product × cycle × lead × window) to one random variable, so the system silently mixes them.** Defects 1-5 are all instances of that one missing contract.

---

## 3. The data reality: raw is the near-term answer

Confirmed from `product_stratified_high.csv` + LCB-at-n sanity:

- OpenData raw daily-max MAE ≈ **1-2°C** for nearly all buckets (already near-unbiased).
- TIGGE→OpenData transfer correction **hurts 7/11** measured buckets, catastrophically (Jeddah MAM raw 2.05 → corrected **9.06**; Busan 1.30 → 3.78).
- OpenData self-correction (best case, leave-one-out): improvements sub-0.5°C; at n=12-18, **only 1/11 buckets clears a crude LCB>0 — San Francisco MAM, the known station-gap artifact.** Every other bucket: correction cannot prove it beats raw.

**Operator correction (2026-05-29):** do NOT read "raw wins 10/11" as license to ship lean and skip the fix. *"必须区分 luck 和实际现实中的 win rate … backtest 无法证明 live alpha."* The 10/11 is an in-sample/backtest artifact, not proof live alpha favors raw. The defects in §2 are absolute structural flaws that must be remediated regardless of who currently wins.

**Revised implication:** build the FULL structurally-correct machinery (contract → provenance ledger → lead/product/cycle-keyed evidence → wired candidate selector → analytic p_raw). Raw-as-default is a **safety property of the accept-rule** (serve raw when no correction is OOS-proven on the same product/lead vs *settlement*), NOT a reason to defer building the selector. The selector must exist and be correct so the system is right when live conditions differ from the backtest — and because raw itself is contaminated for LOW until D1 is fixed (§4), "just serve raw" isn't even clean today.

---

## 4. D1 value-level materiality — RESOLVED

**Production ingest runs the broken extractor.** `collect_open_ens_cycle` (ecmwf_open_data.py:1176) shells out via subprocess to `EXTRACT_SCRIPT = extract_open_ens_localday.py` (line 1488), which imports `STEP_HOURS=6` (tigge_local_calendar_day_common.py:20) and applies `window_start = window_end - 6h` (line 399) to the 3h product. The in-repo daemon is a *wrapper* around the external script, not an independent GRIB reader. (My first-pass read that the daemon reads `endStep` directly was wrong; corrected by the value-test agent + my own grep of the call site.)

Empirical Δ (correct-3h vs served-6h recompute on live 2026-05-28 12z GRIB), now real since production = the 6h path:

| Metric | Δ daily-extreme | Why |
|---|---|---|
| **HIGH (mx2t3)** | 0.00°C Americas, ≤0.78°C (Singapore worst) | Daily max peaks mid-afternoon, far from the mis-windowed early-morning boundary step. **Immaterial.** |
| **LOW (mn2t3)** | **MEASURED: DROP-dominant, minority contamination** | Of 9,314 LOW rows: **5,607 (60%) DROP** (`boundary_ambiguous`→`training_allowed=0`, fail-closed safe); **752 (8%) CONTAMINATE** (`training_allowed=1`, 1-4°C warm min accepted); rest unaffected. UTC+9 (Tokyo/Busan) lead_1 is the dominant dropout (0 valid members served vs 51 correct). **LOW is NOT live** (`settings.json:65 apply_to_metrics:["high"]`; `manager.py:958` LOW→RAW_UNCALIBRATED). |
| Coverage | UTC+9 (Tokyo/Busan/HK) lead_0/12z | step=3 is the only in-day step → served set empty → NULL/excluded rows. Signal dropout. |

**Verdict (final):** D1 blocks **neither** current work. HIGH is immaterial; LOW is not traded live (HIGH-only), so the 8% contamination currently touches only shadow/LOW-training data, and 60% fail-closed-drops. The source-extraction fix is therefore a **parallel track**, not a redesign blocker — but it MUST precede (a) any LOW market launch, and (b) any rebuild of LOW evidence in the new ledger (else the 752 contaminated rows + the UTC+9 dropouts poison LOW training). Fix = product-derived `STEP_HOURS` (3 for mx2t3/mn2t3) + re-extract LOW. This **removes the one place the Tribunal's "source-fix first" phasing seemed right** — it is right only in the narrow sense "before LOW is real." Evidence: `tribunal_verification_2026-05-29/A_source_extraction.md` §D1.

---

## 5. Corrected phasing (vs Tribunal §15)

Tribunal order = source-repair → re-extract → ledger → tests → selection. Corrected order:

0. **D1 value verdict** (§4) — decides whether step "source-fix" is blocking-first or deferred.
1. **Operator scope decision** (rename vs stat-redesign vs both — §6).
2. **ForecastObject + SettlementObject contract**, enforced at a single writer/reader chokepoint (the seam where every residual/serving row MUST instantiate it — else it's doc, not antibody).
3. **Evidence ledger migration** — full provenance + `source_kind {tigge_prior|opendata_live|paired_delta}` + lead_bucket. Makes lineage-collapse unconstructable.
4. **Re-key bias/error model + WIRE the T4 scorer** on (city×metric×season×product×cycle×lead_bucket).
5. **Analytic p_raw** (replace 10k MC; keep MC as cross-check fixture).
6. **Same-product/same-lead OOS selection → 12-city small MC smoke → selective production.** Expect raw-fallback dominant (§3).
- Parallel/non-blocking: external extractor STEP_HOURS fix (gated by §4); deliberate gate-hash re-bump retiring sd3.

---

## 6. Scope decision — RESOLVED by operator

My "half-done landmine" concern was WRONG. Operator verified (PRAGMA + grep): the `dataset_id` column rename on the 3 tables (calibration_pairs, ensemble_snapshots, source_run) is **complete and consistent** in src. The 871/49 ratio is per-table discipline, not half-done — the 871 are legit value-strings, keep-table columns, and Python attrs. `ens_bias_repo` is NOT a landmine: it binds a value-param *named* `data_version` to `WHERE e.dataset_id = ?` under positional binding — value flows correctly, no runtime break.

**Build base:** branch off `pr3-schema-stable (e0092e89bd)` — canonical, consistent re-key surface. Latest PR merges before implementation starts.

**Caveat for the redesign:** `MetricIdentity.data_version` (Python attribute) is the lineage VALUE written into `ensemble_snapshots.dataset_id`, intentionally NOT renamed (it's a provenance identifier, not the column). If the contract wants the attr renamed to `.dataset_id`, that's a separate deliberate change to scope explicitly.

Ambition (resolved, §3): **full structural remediation**, not lean raw-server.

---

## 7. What stays true from the Tribunal

- Abandon sd3 / unconditional full_transport correction. ✔ (HARD-HOLD correct.)
- Raw dominates unless a candidate wins same-product/same-lead OOS vs settlement. ✔
- ForecastObject contract is the right spine. ✔ (but justified by keying/lineage collapse, not live source-corruption.)
- Lead/cycle/product must enter evidence + selection. ✔ (the strongest confirmed defect.)
- Analytic mixture-CDF can replace MC. ✔
