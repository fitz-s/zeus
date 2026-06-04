# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Zeus no-edge structural-fix program — synthesis of 9 designs +
#   2 adversarial plan-critiques + global hidden-error hunt, all re-verified against
#   live code at HEAD (820f260915, origin/main) on the live checkout 2026-06-03.
#   GOAL#36 / project_live_goal_2026_06_03.md: stable >51% after-cost SETTLEMENT
#   win-rate. This doc is the read-only design of record; operator sign-off is
#   required before any implementation that changes live q.
#
# FRESHNESS NOTE (PR-1 author, 2026-06-03): Premise drift caught while implementing
#   PR-1 (spine). The plan was written against a PRE-#382 base. At HEAD:
#     - scripts/measure_arm_gate_settlement.py EXISTS (from #382) — it was EXTENDED
#       with the capital-weighted antibody, NOT re-created from scratch.
#     - The harvester grading lives at src/execution/harvester.py (not src/data/),
#       and its label-vs-label path is venue-grounded + already cross-unit-safe;
#       grade_receipt (value-vs-bin) does NOT fit there, so that path was left as-is
#       (matches the plan's own N4 scope correction).
#   These corrections are recorded so the next session does not re-derive them.

---

# ZEUS STRUCTURAL FIX PLAN — settlement-grounded after-cost edge program

**Status:** READ-ONLY design, operator sign-off required before any implementation. All file:line / DB claims re-verified at HEAD on the live checkout 2026-06-03. Falsified premises from the two critiques are corrected and excluded.

## 0. PREMISE CORRECTIONS (load-bearing, verified this session — do not build on the originals)

| Original claim | Verdict | Evidence |
|---|---|---|
| K1-design: "every LOW trade uses max-temp ensemble mx2t3" | **FALSIFIED** | `event_reactor_adapter.py:3845-3847` switches to `ecmwf_opendata_mn2t3_local_calendar_day_min` for `metric=='low'`. LOW defect is real (44% win-rate) but its cause is LOW-Platt unfit (#54) / LOW representativeness absent — NOT a variable swap. **Do not touch LOW on the swap premise.** |
| B5-design: "market_slug NULL 100% → attribution never matches" | **OVERSTATED** | `settlement_outcomes` market_slug NULL = 1653/6778 = **24.4%**, not 100%. |
| N2-hunter: "attribution dead because driver table empty" | **CONFIRMED — supersedes B5's slug story** | `decision_events`=**0 rows**, `regret_decompositions`=**0 rows**; live path writes `edli_no_submit_receipts`=60623. The slug join is a red herring; the LEFT side is empty. Fixing slug yields zero. |
| K2-design: "all 71 model_bias_ens rows VERIFIED, JJA-fresh" | **INCOMPLETE** | Table holds **150 rows: 71 VERIFIED + 79 NULL-authority**. The reader gates on `weight_live>0 and eff not None`, NOT on `authority='VERIFIED'`. Unverified rows are live-readable → data-provenance-law violation (#122). |
| N1-hunter: "double bias penalty on same row" | **CONFIRMED** | Both `edli_bias_correction_enabled` AND `bias_decay_kelly_haircut_enabled` = `true`. **20/71 VERIFIED rows have |effective_bias_c|>2.0** → same bias number both shifts p_raw (claims corrected) AND halves Kelly (claims untrustworthy). Contradictory. |
| F1: submit branch enforces mainstream / notional cap | **CONFIRMED GAP** | Submit branch `event_reactor_adapter.py:372` has NO `mainstream_agreement_pass` check (sites 742/1088/1185 are receipt annotation only). Notional clamp only at `:1814 if notional_cap_enabled:` — flag is `false` → with `real_order_submit_enabled=true`, full Kelly ships with no ceiling. |

---

## 1. EXECUTIVE — the K structural decisions (K=7, this whole plan reduces to these)

The investigation's ~25 findings are symptoms of **seven** broken module relationships. Every fix below is one of these decisions executed structurally (as a type/contract/test), not a patch.

1. **D1 — Measurement before belief.** There is no settlement-grounded, unit-correct, capital-weighted truth function. Build `grade_receipt()` (B5+B6) and the capital-weighted ARM (F3) FIRST; nothing that changes live q may merge until this spine re-measures a cohort. *Keystone.*
2. **D2 — Arming is an artifact, not a flag.** Live submit must fail closed unless a machine-checked ARM artifact exists (commit SHA + capital-weighted EV>0 + per-city n + LICENSED coverage). Collapses F1/F3/N3 into one boot gate.
3. **D3 — A bias number is corrected XOR distrusted, never both.** One `BiasTreatment` decision per (city,bucket) kills the N1 double-penalty; the same gate fail-closes on NULL-authority/stale rows (K2 + #122).
4. **D4 — Uncertainty travels with every point estimate.** Bias-correction (K2) and q_lcb (K3) must carry their SE/provenance as a TYPE; the consumer cannot receive the estimate without the uncertainty.
5. **D5 — The edge axis is price-disagreement, not forecast-agreement.** Persist `alpha_gap = q_live − market_price` as a first-class column (B2); replace mainstream-agreement as the selection/ARM axis; add the sharpness contract (K1) so flat forecasts cannot emit edges.
6. **D6 — Coverage = skill tail, not insertion-order.** One coverage-fairness contract drives emit (B4); it consumes K1's sharpness verdict, never re-derives it.
7. **D7 — One canonical mechanism per invariant.** Collapse toward: one q-builder, one truth store, one measurement, one calibrator (EMOS as k_cov sink, K3/#110). Persistence-health has ONE checked helper (F2).

---

## 2. SEQUENCED ROADMAP (strict DAG — violations from the critique fixed)

```
PHASE 0  MEASUREMENT SPINE (no live-q risk; everything gates on this)
  P0.1  B5+B6  grade_receipt() + GradedReceipt + BinKind + unit TypeError      [keystone]
  P0.2  F3     capital-weighted ARM script (re-created) consuming grade_receipt
  P0.3  D2     ARM-artifact boot binding (F1 Option C, MANDATORY)
  P0.4  N2     repoint attribution driver to edli_no_submit_receipts (+ CI guard)

PHASE 1  SAFETY / SEMANTIC (independent of q; land early, parallel)
  P1.1  F2     find_weather_markets_or_raise + AST boot guard
  P1.2  F1     rename gate→reference + enforce_on_submit + rename scaleout flag
  P1.3  N3     hard notional ceiling independent of cap-enabled flags
  P1.4  B2a    persist alpha_gap COLUMN (read-only; NO gate yet)

PHASE 2  LIVE-Q FIXES (live_q_risk; SHADOW-only flags; GATED on Phase 0 re-measure)
  P2.1  D3/K2  BiasTreatment 4-tuple: corrected XOR haircut; VERIFIED+fresh fail-closed; SE in quadrature
  P2.2  K1     ForecastSharpnessEvidence required ctor param + settlement-MAE gate
  P2.3  K3     QlcbProvenance type → settlement-backward-coverage as EMOS k_cov (collapse, not 7th flag)
  P2.4  B2b    alpha_gap GATE (fail-closed on NULL fee) — ONLY after K2+K3+B5 validate
  P2.5  B4     coverage-fairness contract drives emit (consumes K1 verdict)

PHASE 3  VALIDATION → ARM
  P3.1  Re-measure post-fix cohort through grade_receipt + capital-weighted ARM
  P3.2  Emit ARM artifact iff exit criteria met; else honest "no edge" verdict
```

**Three sequencing rules that are non-negotiable (each was a critic-flagged violation):**
- **R1:** Nothing in Phase 2 merges to any live-readable flag until P0.1–P0.3 are green AND the spine has re-measured ≥1 settlement cycle. (K3 is `live_q_risk:high`; it must not size live shadow off an unvalidated coverage table built by the same join it's fixing.)
- **R2:** B2 splits — column ships Phase 1 (safe), gate ships Phase 2 after K2/K3 de-inflate q. Gating on K2-inflated alpha_gap selects the *most* inflated candidates.
- **R3:** K1's sharpness gate lives at ONE site (`MarketAnalysis` construction). B4 reads the verdict; it does not re-implement a sharpness check in the emit query (parallel-mechanism creep, E1).

---

## 3. PER-FIX SPECIFICATION (antibody / files / RED tests / callers / risk / validation)

### P0.1 — B5+B6 grade_receipt() [KEYSTONE]  *(IMPLEMENTED in PR-1)*
- **Antibody (3 composed, error category unconstructable):**
  - *Unit:* `grade_receipt(bin, direction, settlement)` raises `UnitMismatchError` when `bin.unit != settlement.settlement_unit` at entry — degF-receipt-vs-degC-settlement is a TypeError at the call boundary, not a comment-out-able assert.
  - *Ceiling/floor:* `BinKind = Literal['exact','ceiling','floor']` as a `@cached_property` on `Bin` (`low&¬high→ceiling`, `¬low&high→floor`, else exact). `grade_receipt` switches on it; ceiling-graded-as-exact is unconstructable.
  - *Membership predicate (critic F4 fix):* use `bin.low <= round_per_city(value) <= bin.high`, NOT a hardcoded `{low, low+1}` set — a 64.5°F settlement must grade into "64-65°F". Rounding via `SettlementSemantics`.
- **Files:** `src/contracts/graded_receipt.py` (NEW), `src/types/market.py` (add `bin_kind`), `tests/test_graded_receipt.py` (NEW). Consumes existing-correct `src/contracts/settlement_resolution.py` (value-derived winning bin).
- **Downstream callers traced:** the value-derived measurement/ARM grading is replaced by `grade_receipt`. The **live harvester path is SAFE** because it grades against the venue-declared winning label (`src/execution/harvester.py:_find_winning_bin` + `_parsed_temperature_bins_equivalent`, label-vs-label, already cross-unit fail-closed) — left as-is. `src/cron/settlement_attribution.py` `compute_realized_pnl` used a `startswith('no_'/'below')` heuristic that is structurally wrong; the live attribution path now routes through `grade_receipt`.
- **Live-q risk:** none.

### P0.2 — F3 capital-weighted ARM  *(IMPLEMENTED in PR-1 — extended #382 tool)*
- **Antibody:** `CapitalWeightedArmVerdict(equal_row_win_rate, equal_row_ev_sigma, capital_weighted_roi, capital_weighted_ev_sigma, per_city_cw_roi)` — all fields required (no Optional). `_capital_weighted_arm_decision` returns DENIED if `capital_weighted_roi<=0` OR any per-city cw cluster negative beyond tolerance OR per-city n<5. `_compute_capital_weighted_verdict` raises `ValueError('MISSING_SIZE')` if any settled row has `kelly_size_usd IS NULL or <=0` → missing size fails CLOSED, never silently equal-weights. **Size source = `kelly_size_usd` column** (verified present), NOT `live_cap_reserved_notional_usd` (NULL on no-submit).
- **Critical DB fix:** the ARM script queries **`zeus-world.db`** (60k+ receipts) — `zeus-forecasts.db.edli_no_submit_receipts` has 0 q-populated rows.
- **Live-q risk:** none.

### P0.3 — D2 ARM-artifact boot binding (F1 Option C — promoted to MANDATORY) *(PR-2)*
- **Antibody:** live boot must additionally require `state/edli_arm_gate_artifact.json` with `{commit_sha, measurement_cmd_hash, capital_weighted_ev>0, gate_pass_n, per_city_n, ev_sigma, date_coverage, coverage_licensed:true}`. Missing/SHA-mismatch/ev≤0 → `RuntimeError` at boot.

### P0.4 — N2 attribution driver repoint *(IMPLEMENTED in PR-1)*
- **Antibody:** `settlement_attribution.py` reads the table the live path writes (`edli_no_submit_receipts`), joined on `(city, target_date, metric, direction)` via `grade_receipt`. Guard: attribution input row-count >0 or the run is FAILED (`AttributionInputEmptyError`) — silent zero = failure.

### P1.1 — F2 persistence-bypass *(PR-3)*
- **Antibody:** single `find_weather_markets_or_raise(...)` in `market_scanner.py`; ALL daemon callers route through it. Boot guard `assert_no_raw_find_weather_markets_in_daemon_callers` AST-scans `src/main.py` + `src/ingest_main.py` (modeled on `assert_writer_jobs_registered`), FATAL on bare call.

### P1.2 — F1 naming + enforce *(PR-2)*
- **Antibody:** rename `mainstream_agreement_gate_enabled → mainstream_agreement_reference_enabled`; rename `edli_live_scaleout_enabled → edli_live_operator_authorized`; add `mainstream_agreement_enforce_on_submit` (default false, fail-closed on missing verdict when true) checked at submit branch before `executor_submit`.

### P1.3 — N3 hard notional ceiling *(PR-2)*
- **Antibody:** a notional ceiling enforced **independent of `tiny_live_notional_cap_enabled`** — the cap-enabled flag may tune the value but cannot remove the ceiling. `#380` removed BOTH notional + daily-order caps in one commit; a hard floor-independent-of-flag prevents single-commit dual-rail removal.

### P1.4 / P2.4 — B2 alpha_gap (split per R2) *(PR-4 / PR-7)*
- **P1.4 column (safe now):** persist `alpha_gap = q_live − c_fee_adjusted` REAL on `edli_no_submit_receipts`. q_live is already direction-adjusted, so the formula is direction-agnostic.
- **P2.4 gate (after K2/K3):** `AlphaGapRequirement(min_gap)` at find_edges + receipt-write; **fail-closed when `c_fee_adjusted is None`**.

### P2.1 — D3/K2 BiasTreatment (folds N1 + #122 + critic-stale-row) *(PR-5)*
- **Antibody:** `_maybe_apply_edli_bias_correction` returns typed `BiasTreatment(shift_native, shift_se_native, n_live, correction_strength, authority, training_cutoff)`. Gates: N1 XOR (corrected XOR haircut), #122 provenance (refuse `authority IS NULL`), stale (training_cutoff within season window), D4 (SE in quadrature when n_live<20). Writer fix: `bias_sd_c = sqrt(V_post)`; `correction_strength = effective/raw`.
- **Live-q risk:** low-medium. **Ships SHADOW-only; gated on Phase 0 re-measure (R1).**

### P2.2 — K1 ForecastSharpnessEvidence *(PR-6)*
- **Antibody:** required positional ctor param on `MarketAnalysis.__init__` → omission is TypeError at startup. Emit zero edges when `mae >= N_SIGMA*bin_width`. Source: `forecast_skill` table; missing row fails closed. Day0 paths `day0_exempt=True`.

### P2.3 — K3 q_lcb → settlement-backward-coverage (collapse into EMOS) *(PR-7)*
- **Antibody:** `QlcbProvenance(calibration_source, n_settlement_observations, coverage_ratio)`; `lcb_by_direction` type `dict[tuple,float] → dict[tuple,QlcbProvenance]`. Make settlement-backward-coverage the EMOS `k_cov` input (#110), not a 7th flag.
- **Live-q risk:** HIGH. **Ships behind `q_lcb_settlement_coverage_gate_enabled=false`.**

### P2.5 — B4 coverage-fairness contract *(PR-6)*
- **Antibody:** emit selection keyed by a `CoverageFairnessRequest` contract object consuming K1's `ForecastSharpnessEvidence`, dedups to ≤1 row/city/cycle — NOT a bare `ORDER BY snapshot_id` re-order.

---

## 4. ADDITIONAL HIDDEN ERRORS (folded above, none dropped)

- **N1 double bias penalty** → P2.1 (XOR gate). HIGH, live today on 20 buckets.
- **N2 dead learning loop** (`decision_events`=0) → P0.4. MEDIUM-HIGH. *(fixed in PR-1)*
- **N3 dual-cap single-commit removal** → P1.3 + D2. HIGH at arm.
- **N4 ceiling-grading defect scoped** → measurement path only (live venue-grounded path safe); P0.1. *(fixed in PR-1)*
- **N5 identity-Platt forced by correction flag** → K2/K3 coupling in P2.1/P2.3.
- **#122 NULL-authority rows live-readable** → P2.1 provenance gate.
- **Critic F2 stale training_cutoff** → P2.1 freshness assert.
- **Critic F4 °F fractional membership mis-grade** → P0.1 rounding predicate. *(fixed in PR-1)*
- **Critic F5 96% buy_no skew at cause** → RED test in P2.2/P2.4: post-fix SELECTED-cohort buy_yes/buy_no ratio must NOT be >90/10.
- **B2 hidden error 1 wrong-DB** (forecasts vs world) → P0.2. *(handled in PR-1: queries zeus-world.db)*
- **K2 G1/G2** (bias_sd_c=residual, correction_strength=1.0) → P2.1 writer fix.
- **K3 receipt has no calibration_source provenance** → P2.3 QlcbProvenance written to receipt_json.
- **Verified NON-issues (cleared):** redeem stub → OPERATOR_REQUIRED (safe); `kelly.py:334` city-mult fail-open bounded [0,2.0] (safe); `_settle_positions won_result is None` skip (conservative).

---

## 5. PR / BRANCH DECOMPOSITION (coherent units)

| PR | Bundle | Why coherent | LOC est |
|---|---|---|---|
| **PR-1 (spine)** | P0.1+P0.2+P0.4 — grade_receipt + ARM script + attribution repoint | One truth function + its two consumers | ~600 |
| **PR-2 (arm-binding)** | P0.3 + F1 rename/enforce (P1.2) + N3 ceiling (P1.3) | All touch the arming boundary | ~450 |
| **PR-3 (persistence)** | P1.1 F2 helper + AST guard | Fully independent, lowest risk | ~300 |
| **PR-4 (alpha-gap column)** | P1.4 column + backfill (NO gate) | Read-only plumbing | ~250 |
| **PR-5 (bias-treatment)** | P2.1 D3/K2 — XOR + provenance + SE + writer fix | One bias decision; SHADOW flags | ~550 |
| **PR-6 (sharpness)** | P2.2 K1 + B4 (P2.5) | B4 consumes K1 verdict | ~500 |
| **PR-7 (coverage-calib)** | P2.3 K3 + P2.4 alpha_gap gate | Both settlement-grounded q gating | ~600 |

PR-1..PR-4 can land in parallel (no q risk). PR-5..PR-7 are serialized behind PR-1 validation (R1).

---

## 6. EXIT CRITERIA (the proof that justifies an eventual ARM)

The program produced a real, validated, settlement-grounded edge **iff ALL hold**, measured by `grade_receipt` + the capital-weighted ARM on post-Phase-2 receipts:

1. **n ≥ 30 settled per active city**, exact-bin (BinKind=exact) cohort, graded value-derived through `grade_receipt` (zero `UnitMismatchError`, zero ceiling-as-exact).
2. **Equal-row after-cost win-rate > 51%** AND **≥ 2σ** (not the current 0.13σ).
3. **Capital-weighted ROI > 0** AND **no per-city capital-weighted cluster negative beyond tolerance** (F3 — catches K2 sizing-up-on-losers).
4. **Coverage LICENSED** for every active city (realized win-rate within ±5pp of served q_lcb; K3).
5. **Selected-cohort buy_yes/buy_no ratio not >90/10** (proves the edge axis changed, not just thinned — critic F5).
6. **ARM artifact** `state/edli_arm_gate_artifact.json` exists with matching commit SHA + measurement-cmd hash; live boot passes the D2 gate.

If any fails after Phase 2, the honest outcome is **"still no edge"** — equally valuable, and the only two acceptable terminal states. No flag flip to `real_order_submit_enabled=true` until criteria 1-6 are green and re-measured across ≥1 fresh settlement cycle. **Overconfidence = ruin.**
