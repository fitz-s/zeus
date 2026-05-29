# Handoff — Stat-layer redesign (Tribunal) — 2026-05-29

Single resume reference. Read §0 + §7 first.

> **Session-2 update (2026-05-29 cont.):** Three increments BUILT + TDD'd + committed,
> all offline/additive (no live serving wired) — wave-critic reviewed:
> - `b62b025898` **P2 pair_residual loop** (D-J1 wrong-station drop) + canonical `dataset_id`.
> - `67968ec915` **P4 analytic Gaussian-mixture p_raw** — equivalence-proven vs 10k MC; MC stays live (critic ACCEPT).
> - `8d7933624f` **P2 SEV-2 fix** — settlement-authority registry: forecast `noaa`/`cwa_station`
>   reconcile with settlement `ogimet`/`cwa` collectors (operator-confirmed same-truth); `?site=`
>   station parser; UnknownSettlementAuthorityError = loud quarantine. Un-drops Istanbul/Moscow/Tel Aviv.
> - `7f2b489f9c` docs: tautological metric/unit gate dims + canonical-schema precondition.
>
> Remaining highest-leverage: **D-S1** settlement-schema columns (also un-blocks Hong Kong, whose
> climat.htm settlement URL has NO parseable station), P5 run-selection, P3 production-serving
> integration (DORMANT). Contracts have zero active-live consumers yet. 84 redesign/signal tests green.

- Worktree: `.claude/worktrees/stat-whole-refactor`  ·  Branch: `stat-whole-refactor`  ·  HEAD: `67968ec915`
- Base: merged current with `origin/main` (PR #359 canonicalization in). Collection: 0 errors. 68 new contract/stat tests green.
- Python: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python`; run pytest from the worktree root.

---

## 0. TL;DR — current state

The statistical redesign (P1+P2+P3) demanded by the 2026-05-29 Tribunal is **built, TDD'd, committed**. The system **serves raw OpenData correctly now**; the machinery to *earn* a bias correction (once data depth + FT promotion arrive) exists and is proven, and "sd3 renamed" (auto-applying a TIGGE-proven correction to live OpenData) is **structurally unconstructable** end-to-end.

What is NOT done: **production-serving integration** of these antibodies (offline pipeline wiring → persisted selection → `model_bias_ens` lead/cycle/product re-key → lead-aware serving twins). It is **DORMANT** (FT flag off, `error_model_family` NULL everywhere, no OpenData Platt) and gated on FT promotion + the live-DB migration. Plus P4 (analytic p_raw) and P5 (run the OOS selection on real data) remain.

---

## 1. Mission recap

Bring Zeus live onto a bias-corrected forecast — but only if the correction beats raw OOS vs **settlement**, on the **same product/lead** it will serve. Tribunal root cause: Zeus never defined the forecast random variable as a full-chain-consistent object, so it mixed products (3h OpenData vs 6h TIGGE), leads, cycles, units, and settlement identities. The redesign makes each mix unconstructable via typed contracts + a correct accept-gate. Authority docs: `TRIBUNAL_REFRAME_2026-05-29.md`, `TRIBUNAL_DRAFT2_RESPONSE_2026-05-29.md`, `CRITIC_SYNTHESIS_2026-05-29.md`, `P2_LEDGER_SEAM_FINDINGS_2026-05-29.md`.

---

## 2. What's DONE — the antibodies (file map)

**P1 — forecast random-variable contract** (`src/contracts/`):
- `forecast_target.py` — `ForecastTarget` + `assert_same_target` (residual valid only if city/metric/target_local_date/station/unit/authority match) + `normalize_settlement_authority` (reconciles `wu_icao` ↔ `wu_icao_history_v1`).
- `forecast_object.py` — `ForecastObject.from_snapshot_row` (fail-closed RV parse: product token mx2t3/mx2t6, cycle, RAW lead_hours, members, data_version, `.target`). RAISES on missing field; reuses `validate_members_unit`.
- `residual_key.py` — `pair_residual` (target-gated) + `source_kind_for_data_version` (tigge_prior/opendata_live, kills hardcoded 'prior') + `SettlementObject.from_settlement_row` (station from WU URL, authority from provenance_json, unit = forecast claim per D-S1).
- `residual_value.py` — `residual_celsius` (converts EACH side by its own unit; fixes the masked degC/degF bug).

**P2 — ledger wiring** (`scripts/build_ens_residual_evidence.py`):
- `_strict_evidence_row` now: unit-correct residual (`residual_celsius`), derived `source_kind`, window-provenance fields (forecast_window_start/end, source_run_id, available_at), canonical table names (`ensemble_snapshots`/`settlement_outcomes`).

**P3 — statistical redesign** (`src/calibration/` + `scripts/`):
- `ens_bias_model.py::build_candidate_biases` — REPLACES the TIGGE→OpenData shrinkage: emits product-tagged candidates (opendata_bias = OpenData-only; tigge_prior tagged cross-product; raw baseline). `posterior_bias`/`fit_bucket` unchanged (additive).
- `oos_gate.py` — the gate inputs (were absent): `date_blocked_folds` (S4), `effective_sample_size` AR(1) (S3), `moving_block_bootstrap_lcb` (S2/S3), `bh_fdr_accept` (S1).
- `score_error_model_candidates.py::choose_candidate` — accept-gate: same-product mandatory (refuses cross-product), raw-default, LCB>0, catastrophic veto.

Tests (68): test_forecast_target_contract / test_forecast_object_contract / test_residual_key_contract / test_residual_value_contract / test_settlement_pairing_contract / test_strict_evidence_row / test_candidate_product_segregation / test_oos_gate / test_ens_bias_candidates.

Session commits (newest first): `cb57aaa0d3 3d12b7f852 c9cc3a2440 c293e04a73 a95119d600 ffd6c9aabf bccdb1e694 0dee92a198 38e6885da5 10fe7f5a31 e37bd501b5 b327845038 8088759c91 60f123e840 f1788a8c9f`.

---

## 3. Hard-won understanding (verified facts — do not re-derive)

- **Live forecast path runs the broken extractor.** `ecmwf_open_data.py:1488` (`collect_open_ens_cycle`) shells out to `51 source data/scripts/extract_open_ens_localday.py` which applies `STEP_HOURS=6` to the 3h product (D1). HIGH impact ≤0.78°C (immaterial); LOW (mn2t3) exposed: of 9,314 rows, 60% fail-closed-drop, 8% (752) contaminate 1-4°C warm. **LOW is NOT traded live** (`settings.json apply_to_metrics:["high"]`). → D1 fix is pre-LOW-launch, not blocking.
- **Units are MIXED across sources.** OpenData members `members_unit=degF`; provenance contract docstring claims degC; WU settlement °F. Per-row unit handling mandatory. The legacy ledger converted settlement with `members_unit` — correct only by coincidence (masked ~50°C bug where they differ).
- **`settlement_outcomes` has NO unit/station columns** (D-S1). Station is in the `settlement_source` URL; unit is convention; authority in `provenance_json.data_version`. The contract derives station+authority, but unit is the forecast's CLAIM (unverifiable until a schema column is added).
- **Data depth is brutal:** ~801 independent settled HIGH outcomes over ~23 days, 50 cities. No OpenData Platt (`platt_models_v2` = tigge_mars only) → p_cal=p_raw. No FT branch live (`error_model_family` NULL). At n=12-18/bucket, **no correction clears the OOS gate** — raw dominates for months, by design.
- **TIGGE→OpenData transfer HURTS 7/11 buckets** (Jeddah 2.05→9.06). The whole reason for product segregation.
- **VERIFICATION-SURFACE LESSON (cost me 2 errors this session):** for design/naming/schema questions, verify against the **code/DDL at the build base** (`src/state/schema/v2_schema.py`), NOT the running live DB (lags merged renames) and NOT a negative grep on an unconfirmed path. Canonical tables = `ensemble_snapshots`, `settlement_outcomes` (no `_v2`).

---

## 4. What REMAINS

**Production-serving integration of P3 (DORMANT — gated on FT promotion + live-DB migration):**
1. Offline scoring pipeline: wire `build_candidate_biases` → re-MC each candidate's p_raw via the production sampler → proper-score vs settlement on `date_blocked_folds` → `moving_block_bootstrap_lcb` + `bh_fdr_accept` → `choose_candidate` → persist a selection manifest. (`score_error_model_candidates.py` is the home; the scoring half is still a stub per its module docstring.)
2. `model_bias_ens` table lead/cycle/product re-key (schema migration) so the serving key carries them.
3. Serving-twin lead-awareness (Cons-A): `src/engine/evaluator.py:3296` `_resolve_ft_error_model_for_entry` + `src/engine/monitor_refresh.py:343` `_resolve_ft_error_model` (byte-twins, divergence warned at evaluator.py:43-45 but unenforced) — thread `forecast_lead_bucket`, RAISE on miss (fail-closed, not fail-open to raw), de-duplicate via shared helper or CI grep.

**P2 loop — [S2 DONE]** (`b62b025898`): `build_evidence` now routes every candidate through `_pair_or_drop`→`pair_residual` (loose join gated; wrong-station dropped fail-closed) and reads canonical `e.dataset_id AS data_version` (the prior `e.data_version` errored on the canonical schema — column renamed in B5). Dict-by-column access replaced the 23-col positional unpack. `tests/test_build_evidence_{pairing_gate,integration}.py`.
**P2 remaining:** D-S1 — add `settlement_unit`+`settlement_station` columns to `settlement_outcomes` (settlement-side migration; makes unit/station verifiable not heuristic). Also `ens_bias_repo.py:167` still selects stale `e.data_version` (dormant serving path) — fix when that path reactivates.

**P4 analytic p_raw — [S2 DONE as additive draft]** (`67968ec915`): `analytic_p_raw_vector_from_maxes` (`ensemble_signal.py:268`) = closed-form Gaussian-mixture CDF over the rounding preimage, equivalence-proven vs the 10k MC (`tests/test_analytic_p_raw_equivalence.py`, 16 tests; p_raw atol 2e-3, logit atol 1.5e-2 [clamp-bounded], identity p_cal). **ADDITIVE — MC stays the live generator.**
**P4 retirement (remaining, overlaps P5):** before swapping analytic into live serving, re-run the equivalence gate against the **real active Platt** once an OpenData Platt exists (full Cons-D: gate on p_cal + logit(p_raw), not p_raw alone — Platt was trained on MC p_raw). No OpenData Platt today (p_cal=p_raw), so IdentityCalibrator currently reflects production.

**P5 — run the selection** (#9): once depth/FT allow, run the OOS candidate selection on the corrected lead-matched base; expect raw-fallback dominant.

**Validation harness** (#14): before/after fixture is a 5-row smoke (`capture_before_fixture.py` built + allowlisted). Equivalence mode (analytic vs MC) decisive; improvement mode underpowered for months (honest limit).

**D1-LOW source fix** (#10): product-derive `STEP_HOURS` (3 for mx2t3/mn2t3) + re-extract LOW. Pre-LOW-launch.

**Carry-overs:** #11 gate-hash re-bump (retire sd3) when keying changes; #12 M5 unshadow (HARD-HOLD) + gates (#105 p_raw_domain, #121 quarantine, #109 scanner); #13 #359 already merged — rebase done via merge.

**Cleanup (operator's broader sweep):** ~16 non-mine straggler scripts still on `_v2` (list in task #16) — several read the lagging live DB, so gated on live-DB migration. Pre-existing migrate-script writer-lock test failures were DEFERRED by main (`07ceb26f3f`) — not regressions.

---

## 5. Hard rails (do not violate)
- Canonical tables: `ensemble_snapshots`, `settlement_outcomes`. No `_v2` in new code.
- FT is DORMANT and must stay so until P5 passes OOS on a D1+lineage-corrected, lead-matched base.
- First live order completes programmatically; no manual completion.
- Stats-locked (changing re-bumps the gate hash): MIN_PRIOR_N=5, MIN_PAIRED_N=5, DEFAULT_MIN_LIVE_N=20, FLOOR=3.0. Re-keying WILL re-bump — do it deliberately with a new gate id (#11).
- Producer never writes prod DBs; INV-37 cross-DB ATTACH+SAVEPOINT.
- Live DB lags canonical schema — design to canonical, let the operator migrate the live DB.

---

## 6. EXACT resume order (next session, fresh context)
1. Re-read this §0/§3/§4 + `P2_LEDGER_SEAM_FINDINGS` + `CRITIC_SYNTHESIS`.
2. Highest leverage = **P4 analytic p_raw** (bounded, high-value, kills the hours-long MC rebuild) OR **P2 pair_residual loop wiring** (bounded, completes the ledger antibody). Both fully specced.
3. The P3 production-serving integration (§4 items 1-3) is the larger phase — do it WHEN FT promotion is on the table; it's Tier-0 serving + a schema migration, dormant until then.
4. Keep TDD (relationship tests first); commit per increment; verify executor work (esp. SQL tuple-unpack — dict-fixture tests miss it).

## 7. Doc index
- `TRIBUNAL_REFRAME_2026-05-29.md` — corrected premises + reframed phasing.
- `TRIBUNAL_DRAFT2_RESPONSE_2026-05-29.md` — executable spec + before/after harness design.
- `CRITIC_SYNTHESIS_2026-05-29.md` — 3-opus critic verdict (REVISE) + the compounding-failure insight + verification-accuracy record.
- `P2_LEDGER_SEAM_FINDINGS_2026-05-29.md` — 4 ledger defects + exact wiring plan.
- `tribunal_verification_2026-05-29/{A,B,C,D,E,CRITIC_*}.md` — grounded evidence per claim.
- `SESSION_REPORT_TRIBUNAL_2026-05-29.md` — full session arc.
