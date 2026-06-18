# Implementation report — no-shadow RAW diagonal-precision fusion

- Created: 2026-06-18
- Authority: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md (the
  verified 10-step flow + the exact q_lcb guard) + consult_resolution_ledger_2026-06-17.md.
- Branch: `claude/agent-a0b5249170bcdaabe` (isolated worktree). HEAD `bbfd488537`.
- LAWS honored: NO SHADOW (no flag-default-OFF parallel products); NO fitted forward per-city
  de-bias (RAW center; the q_lcb guard does NOT move μ); settlement is the only truth.

## Commit chunks (logical, in order)

| sha | step | summary |
|---|---|---|
| `cbbd4afe57` | S1+S2 | RAW second-moment precision weights (1/E[r²]) on the spine center |
| `5c084023b5` | S3 | unify exit/monitor belief onto RAW (forecast_posteriors is RAW) |
| `05ba1d3b9f` | S4 | kill Path B modal-collapse (per-row simplex renormalize) |
| `7c36112410` | S6 | q_lcb empirical reliability guard (RAW-honest serving rule) |
| `8f1c277f31` | S7 | provenance fail-closed (reject a live PD with nonzero de-bias shift) |
| `0deb445363` | tests | RED-on-revert suite for S1–S7 |
| `7e9856c412` | tests | update spine-source contract tests for the 3-tuple producer |
| `bbfd488537` | tests | q_lcb guard decision-integration (abstain → no-trade) |

(S5 is wiring-only — no code change needed; see below.)

## Step-by-step: every file:line changed

### S1 — RAW second-moment history provider + threading onto the spine member

- `src/forecast/types.py:54-92` — `RawModelMember` gains `walk_forward_raw_m2_native: float|None = None`
  and `walk_forward_n: int = 0` (the RAW second moment Ê[(x−Y)²] with bias² INCLUDED, + its
  walk-forward count). Defaults None/0 ⇒ equal-weight (back-compatible).
- `src/data/bayes_precision_fusion_history_provider.py:51-95` — NEW `raw_second_moment_by_model(...)`:
  reuses the EXISTING walk-forward residual source (`BayesPrecisionFusionHistoryProvider` →
  `ModelHistory.residual_by_target_date`, the RAW residual x−Y, NOT EB), squares each residual and
  averages: `Ê[(x−Y)²] = mean((forecast − settlement)²)` over target_date < decision date. Returns
  `{model: (raw_m2_degC, n_train)}`. NO parallel residual pipeline. Fail-soft → empty.
- `src/engine/event_reactor_adapter.py:11765-11888` — `_spine_multimodel_members_for_event` now
  returns a 3-tuple `(members_native, source_cycle, precision_by_index)`. After the member query it
  computes the per-model raw second moment via `raw_second_moment_by_model` keyed on
  (city, metric, lead=(target−cycle).days, models), converts degC²→native² for F-cities (×(9/5)²),
  and returns `[(model, raw_m2|None, n), ...]` aligned to the member order. Fail-soft → all-None.
- `src/engine/event_reactor_adapter.py:8309-8330` — the live stash threads
  `payload["_edli_spine_raw_m2_by_index"]` + `["_edli_spine_n_by_index"]` (same index order as the
  member arrays) when the precision list aligns to the members.
- `src/engine/event_reactor_adapter.py:6826` — the OTHER producer call site (cert payload) unpacks
  the new 3rd element (ignored there).

### S2 — `walk_forward_model_weights` → raw second-moment basis

- `src/forecast/center.py:128-220` (function + section comment rewritten) —
  `walk_forward_model_weights` forms `w_m ∝ 1/max(Ê[(x−Y)²], SIGMA_FLOOR²)` reading
  `member.walk_forward_raw_m2_native`. Shrink-to-equal at `n < MIN_TRAIN` via the EB low-n rule
  (`m2_eff = lam·raw_m2 + (1−lam)·(SIGMA_FLOOR·LOWN_INFLATE)²`, `lam = n/(n+KAPPA)`); absent history
  (raw_m2 None / non-finite / n==0) → the equal-precision floor → exact 1/n when NO member carries a
  signal. NO `shrink_cov` / `diag_cov` / `np.var` / `np.std` / demeaning anywhere. Envelope-lock +
  robust-Huber + (no-op) debias in `build_center` left untouched.
- The spine attaches the threaded precision in
  `src/engine/qkernel_spine_bridge.py:_served_predictive_inputs` (lifts `raw_m2_by_index`/`n_by_index`
  with the new `_coerce_optional_float_list` / `_coerce_int_list` helpers) and
  `build_fresh_model_set` (attaches `walk_forward_raw_m2_native` / `walk_forward_n` to each
  `RawModelMember` when the precision arrays align 1:1 with the members; else equal-weight).

### S3 — Unify the EXIT belief onto RAW (the active BLOCKER)

Chosen approach: **make `forecast_posteriors` RAW** by removing EB from the consumed center, so
entry (spine, already RAW via `_NoOpDebiasAuthority`), exit (`position_belief`), and monitor
(`monitor_refresh`) all read ONE RAW belief. This is the single surgical unify point because both
exit and monitor read `forecast_posteriors.q_json`.

- `src/data/bayes_precision_fusion_capture.py:306-325` — `_eb_corrected` → `_raw_instrument`:
  returns `z = raw_value` (NOT `raw − b̂`), keeps `n_train`; the walk-forward history is retained
  for width/provenance only. `parent_bias` deliberately unused for the center.
- `src/data/bayes_precision_fusion_capture.py:44-49` — `eb_bias` import REMOVED from the
  consumed-center path (structural antibody).
- `src/data/bayes_precision_fusion_capture.py:447-484` — both instrument loops call `_raw_instrument`.
- `src/data/replacement_forecast_materializer.py:1754` — `bias_shift_c` forced `None` (the fitted
  forward per-city de-bias `get_city_debias_c` is forbidden under the RAW law), so
  `anchor_value_corrected_c == raw_anchor_value_c` (zero shift) and the fused μ* written to
  `forecast_posteriors` is the RAW diagonal center.

**Every `forecast_posteriors` reader traced** (`grep -rln forecast_posteriors src/`): the only
readers that consume a *center/q* value are `position_belief.load_replacement_belief` (exit) and
`monitor_refresh.monitor_probability_refresh` (monitor) — both read `q_json`, now RAW. The
`event_reactor_adapter` posterior reader (`forecast_posteriors`, ~6780) reads only cert provenance
(`posterior_identity_hash`, `source_run_id`, member hashes) — NOT a center/q — and the spine ENTRY
builds its own RAW PD from `raw_model_forecasts`, never from `forecast_posteriors.q_json`. No reader
consumes an EB center after this change.

### S4 — Delete/wrap q_lcb Path B

- `src/data/replacement_forecast_materializer.py:1702-1730` (`_build_fused_q_bounds`) — each draw's
  row is renormalized to the probability simplex BEFORE the marginal percentile (the IDENTICAL
  renormalize-then-quantile transform `build_joint_q_band` performs). The modal-collapse defect (raw
  per-bin percentile over un-normalized rows) is now unconstructable; Path B is no longer an
  INDEPENDENT q_lcb method. `build_joint_q_band` (4000 draws, alpha 0.05, OOF realized-floor σ,
  per-draw simplex) remains the q_lcb AUTHORITY for the spine ENTRY decision.

### S5 — Single q from the RAW PD (wiring verification only)

No code change. `src/probability/joint_q.build_joint_q` already reads `pd.mu_native` (the RAW
envelope-locked precision-weighted center, upgraded by S1+S2) and `pd.sigma_native` (train-RMSE /
realized-floor width) directly (joint_q.py:236,244) and integrates the per-city settlement-preimage
bins. The single q from the RAW PD therefore flows by construction.

### S6 — q_lcb empirical reliability guard

- `src/decision/qlcb_reliability_guard.py` (NEW) — the serving rule
  `q_safe = min(band.q_lcb, L_g)` where `L_g` = one-sided Wilson 95% lower bound of the OOF realized
  hit-rate in cell `g = (metric, lead_bucket, bin_position, q_lcb_bucket)` (NOT per-city). Trade only
  if `N_g ≥ N_MIN` AND `L_g ≥ bucket_floor − EPS`; else abstain (`q_safe = 0`). Named constants:
  `N_MIN=30`, `EDGE_FLOOR=0.0`, `EPS=0.02`, `QLCB_BUCKET_EDGES`. Artifact-gated
  (`state/qlcb_oof_reliability.json`, gitignored, same posture as the σ-floor): INERT pass-through
  when absent (byte-identical to today), ACTIVE when the operator places the table.
- `src/decision/family_decision_engine.py` — import `apply_guard`; `decide()` calls
  `self._apply_qlcb_reliability_guard(...)` (new method) between scoring and selection. It computes
  each route's served q_lcb (`edge_lcb + cost`), resolves `bin_position` ("modal" = forecast bin,
  else "nonmodal"), and on abstain re-stamps `edge_lcb` to non-positive (and `optimal_delta_u` ≤ 0)
  so the existing `edge_lcb > 0` filter rejects it. `replace` added to the dataclass import;
  `NO_TRADE_QLCB_RELIABILITY_ABSTAIN` reason added.

### S7 — Provenance fail-closed

- `src/forecast/predictive_distribution_builder.py:384-410` — a live-eligible PD whose
  `debias.aggregate_shift_native != 0` is REJECTED (`live_eligible=False` +
  `RAW_LAW_VIOLATION_DEBIAS_SHIFT_NONZERO`); an unrecognized `center_method` → ineligible
  (`RAW_LAW_VIOLATION_CENTER_METHOD`). day0 is exempt (its observed-extreme shift lives in
  `day0.center_after_native`, not debias). `center_method` / `debias.activation_status` /
  `debias.aggregate_shift_native` / `weights_by_model` / `model_set_hash` already flow into the
  `identity_hash` + receipt; this gate refuses to SERVE a violated one.

## New fields / constants

- `RawModelMember.walk_forward_raw_m2_native: float|None`, `RawModelMember.walk_forward_n: int`.
- `qlcb_reliability_guard`: `N_MIN=30`, `EDGE_FLOOR=0.0`, `EPS=0.02`,
  `QLCB_BUCKET_EDGES=(0.0,0.5,0.6,0.7,0.8,0.9,1.0)`, `_WILSON_Z_95`,
  `_QLCB_OOF_RELIABILITY_PATH="state/qlcb_oof_reliability.json"`; `GuardVerdict` dataclass.
- `family_decision_engine.NO_TRADE_QLCB_RELIABILITY_ABSTAIN`.
- Payload keys `_edli_spine_raw_m2_by_index`, `_edli_spine_n_by_index`.

## Tests — what goes RED on revert

- `tests/forecast/test_raw_diagonal_weights.py` (5) — divergent E[r²] ⇒ weights diverge from 1/n
  upweighting the lowest E[r²] (exact 1/E[r²] normalization checked); absent history ⇒ 1/n; thin
  history shrinks toward equal; sub-floor raw_m2 capped at 1/floor². RED if the basis reverts to
  se/var/equal.
- `tests/forecast/test_raw_center_and_provenance.py` (2) — NoOp authority ⇒ zero-shift RAW center
  (debiased consensus == raw consensus, member envelope unmoved); a live PD with a +2.0 forward
  de-bias shift is REJECTED (`RAW_LAW_VIOLATION_DEBIAS_SHIFT_NONZERO`). RED if the §7 gate removed.
- `tests/decision/test_qlcb_reliability_guard.py` (6) — Wilson lower bound conservative on thin
  samples; well-calibrated cell serves min(band, L_g); miscalibrated cell abstains (q_safe=0); thin
  cell (n<N_MIN) abstains; unknown cell INERT pass-through; absent artifact INERT.
- `tests/decision/test_qlcb_guard_decision_integration.py` (2) — end-to-end in `decide()`: INERT
  guard keeps the YES_25 trade; a miscalibrated cell deflates q_safe → the winning candidate's
  edge_lcb goes non-positive → `selected is None` (no trade). RED if the guard injection reverted.
- `tests/test_path_b_qlcb_modal_collapse_regression.py` (2) — finite-bin modal spike: the OLD
  un-renormalized per-bin percentile collapses the modal q_lcb to ~0; the live (renormalized)
  `_build_fused_q_bounds` keeps it >0.25 (≈10×). Plus a grep antibody that the per-row
  renormalization line is present. RED if the renormalize reverted.
- `tests/test_raw_unify_forecast_posteriors.py` (2) — a +3°C hot-biased deep history ⇒ every
  instrument z == raw value (NOT raw − bias), history retained for width; `eb_bias` no longer
  imported and `_eb_corrected` gone. RED if the EB shift reinstated on the consumed center.
- `tests/integration/test_qkernel_spine_sources_multimodel.py` — updated the 2 producer-contract
  tests to the new 3-tuple `(members, source_cycle, precision_by_index)` shape + assert the
  precision list aligns 1:1 with the members.

New tests: **19 pass** (`pytest tests/forecast/test_raw_diagonal_weights.py
tests/forecast/test_raw_center_and_provenance.py tests/decision/test_qlcb_reliability_guard.py
tests/decision/test_qlcb_guard_decision_integration.py
tests/test_path_b_qlcb_modal_collapse_regression.py tests/test_raw_unify_forecast_posteriors.py` →
`19 passed`).

## Full money-path smoke (task-specified)

`pytest tests/calibration/ tests/decision/test_live_receipt_contract.py tests/integration/ -q`
→ **161 passed, 9 failed**. All 9 are PRE-EXISTING (verified by re-running each at the pre-work tree
`cbbd4afe57~1`, where they fail identically):

- 5× `tests/calibration/test_emos_serve.py` — named in the task's known-fails list (NOT mine).
- 3× `tests/integration/test_qkernel_spine_blockers_pr409.py`
  (`test_live_bridge_forecast_case_matches_arm_replay`,
  `test_overlay_preserves_probability_fields_and_updates_score`,
  `test_overlay_does_not_create_milan_buy_yes_probability_contradiction`) — pre-existing overlay/ARM
  fixture failures (e.g. `q_posterior == 0.80` expecting a hard value), unrelated to the RAW change.
- 1× `tests/integration/test_qkernel_spine_routing.py::test_selected_proof_shape_is_submission_pipeline_ready`
  — pre-existing.

Broader sweep over the touched modules (`bayes_precision_fusion`, `replacement_q_mode`,
`replacement_qlcb`, `center`, `sigma_authority`, `joint_q`, `position_belief`, `monitor`, …):
the additional failures (`test_replacement_q_mode_authority`, `test_bayes_precision_fusion_download`,
`test_bayes_precision_fusion_extras_scheduler_health_surfacing`,
`test_replacement_live_qlcb_missing_floor_blocks`, `test_replacement_0_1_qlcb_k3_and_shadowlog`,
`test_soft_anchor_open_ended_bins`, `test_backfill_*`, `test_k2_live_ingestion_relationships`) were
ALL baselined at `cbbd4afe57~1` and fail identically there — pre-existing (driven by the 2026-06-17
model-set changes: gfs_hrrr/gem_hrdps decorrelated-provider count 2/5 → FULL→PARTIAL, and
environmental DB/state fixtures). Targeted suites that DO exercise my changes are green:
`tests/forecast/ tests/decision/ tests/probability/` → **94 passed**;
`tests/integration/test_qkernel_spine_sources_multimodel.py` → **4 passed**;
belief/unify suites (`position_belief`/`monitor`/`single_belief`) → **30 passed, 1 skipped**.

## Steps I could NOT fully complete (flagged, not guessed)

- **The OOF reliability table itself (S6 data artifact)** is NOT built here — it is an OFFLINE fit
  over settled OOF predictions (the spec's "Owed" item). The guard is therefore INERT in the current
  live state (byte-identical to pre-guard), and goes ACTIVE the moment the operator places
  `state/qlcb_oof_reliability.json`. This is the law-compliant, no-shadow posture (artifact-gated
  serving rule, exactly like the σ-floor), but the guard cannot DEFLATE anything until the table
  exists. The guard logic, constants, Wilson bound, and decision-engine injection are fully wired and
  tested with injected tables.
- **The `EDGE_FLOOR` / after-cost edge check** (`q_safe − price − cost > EDGE_FLOOR`) is applied
  implicitly: the guard re-stamps `edge_lcb = q_safe − cost`, and the existing `edge_lcb > 0` filter
  is the after-cost gate (with `EDGE_FLOOR=0.0` this is exact). A positive `EDGE_FLOOR` would need the
  filter raised to `edge_lcb > EDGE_FLOOR`; left at 0.0 to preserve the conservative existing bar.
- The 4 pre-existing spine-integration failures (`blockers_pr409`, `routing`) are NOT addressed —
  they predate this work and are out of scope.

## Method unify (2026-06-18)

**Problem closed**: entry (spine PD) used `walk_forward_model_weights` (RAW diagonal), but
`forecast_posteriors` center came from `fuse_bayes_precision_posterior` mu* (T2 full-Σ Bayesian
BLUE). Both were RAW inputs post-§3, but the fusion METHOD differed — the #135 two-center split.

**Changes** (4 files, 1 new test file):

| File | Change |
|---|---|
| `src/forecast/center.py` | Added `raw_second_moment_weights(raw_m2_and_n, *, unit)` — shared helper, single source of truth for entry and exit weights. Identical logic to `walk_forward_model_weights` but keyed by model name, so both callers call ONE function. |
| `src/data/bayes_precision_fusion_capture.py` | Extended `BayesPrecisionFusionCaptureResult` with `anchor_raw_m2_native: float | None` and `anchor_raw_n_train: int`. Populated in `capture_bayes_precision_instruments` from `anchor_hist.residuals` (mean(r²)). |
| `src/data/replacement_forecast_materializer.py` | In `_replacement_bayes_precision_fusion_override`: after `fuse_bayes_precision_posterior` (kept for sd/width), compute `_mu_diagonal = Σ_m w_m·z_m` via `raw_second_moment_weights` over `capture.likelihood` train_residuals + anchor. Return `anchor_value_c=_mu_diagonal` (not `fused.mu`). F-city unit scaling via `_city_settlement_unit_from_bins`. Equal-weight fallback (no T2 leak) on missing precision signal. |
| `tests/forecast/test_method_unify_center_coherence.py` | 8 RED-on-revert tests: (a) `raw_second_moment_weights` == `walk_forward_model_weights` for same inputs (full history, low-n shrink, no history, F-city unit scaling, sum-to-1); (b) materializer path: `BayesPrecisionFusionCaptureResult` carries anchor raw_m2, diagonal center differs from equal mean when precision varies, zero-history anchor uses equal-weight fallback. |

**Invariants preserved**:
- Width unchanged: `fused.sd` still used for `anchor_sigma_c`.
- Anchor is a MEMBER (not a separate Bayesian prior) with its own `raw_m2`.
- FULL/PARTIAL completeness contract, q-mode, calibration-credential paths intact.
- No T2 BLUE path reachable: all three branches (precision signal / no signal / no instruments) produce a RAW center.
- `debias_applied=false` already set by prior §3 (RAW instruments). `center_method` provenance inherits from `fused.method` which reflects EQUAL_WEIGHT or T2 — a follow-on provenance string update may relabel to `RAW_DIAGONAL` but is NOT a correctness gate.

**Test run**: 91 passed (all `tests/forecast/ tests/decision/` + modal-collapse + unify suites). 1 pre-existing `test_emos_serve` failure unrelated to this work.
