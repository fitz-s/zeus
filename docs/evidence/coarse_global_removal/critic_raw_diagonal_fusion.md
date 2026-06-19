# CRITIC REPORT — RAW diagonal-precision fusion, no-shadow (S1–S7)

- Reviewer role: adversarial CRITIC (writer != reviewer), read-only.
- Date: 2026-06-18
- Branch/HEAD reviewed: `claude/agent-a0b5249170bcdaabe` @ `c25e7e4b87`.
- Mode: started THOROUGH, escalated to ADVERSARIAL after confirming the unit-mismatch HIGH.

## DIFF-RANGE CORRECTION (read first)

The task named merge-base `8f1c277f31..c25e7e4b87`. That range is **WRONG**: `8f1c277f31`
IS the S7 commit, so the entire src implementation (S1–S7) sits **below** the given base and
the range contains ONLY test+doc commits (`git diff 8f1c277f31..c25e7e4b87 -- src/` = empty).
The real implementation is `cbbd4afe57..8f1c277f31` (5 src commits). I reviewed the correct
range **`8f6a5eb9e6..c25e7e4b87`** (base = `cbbd4afe57~1` = the implementer's own pre-work
baseline, report line 176): 10 src files, +777/−61. All findings below are against that range.

## SPEC NOT FOUND (process finding)

The cited authority `docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md`
and `consult_resolution_ledger_2026-06-17.md` **do not exist** anywhere in the repo or git history
(`git ls-files`, `git log --all --diff-filter=D` both empty). Every code comment and the file
header of `qlcb_reliability_guard.py` cite a spec that cannot be opened. The §-references
("§1–§7") therefore cannot be checked against the authoritative text — I reviewed against the
implementer report + the operator laws in the task. The spec must be committed alongside the
code (provenance law: every script header carries a resolvable Authority basis).

---

## VERDICT: GO-WITH-FIXES

The core design is sound and the dangerous surfaces are clean: INV-37 is untouched (zero new DB
writes/connections; the new second-moment query reuses the passed conn, SELECT-only), the
submission pipeline (RiskGuard/freshness/MECE/venue/receipts) is byte-unchanged, the §7
provenance gate cannot halt the live path (spine is unconditionally `_NoOpDebiasAuthority` →
zero shift; whitelist == the three `center_method` Literals exactly), the S4 Path-B simplex
renorm is correct and is the only remaining raw-percentile site, the q_lcb guard ships INERT =
byte-identical, abstain correctly rejects before sizing, and the EB removal is real (z = raw).

Two findings block a clean ACCEPT: a **HIGH unit-mismatch** in the new weight shrink/floor
(degC² constants vs native-unit raw second moment) that mis-weights the live F-city center at
low history depth, and a **MEDIUM overstated-unify** claim (entry and exit are still two
different fusion engines — only the EB shift was unified, not the center). Neither corrupts
settlement, crashes, or breaks an invariant; fix the HIGH before live activation of the weights.

---

## HIGH — src/forecast/center.py:177-204 (walk_forward_model_weights) — degC²/native² unit mismatch in the shrink target and the floor, distorting the live F-city center at low n

PROBLEM. The precision basis `member.walk_forward_raw_m2_native` is threaded in **native units**:
`src/engine/event_reactor_adapter.py:11936` converts the degC² raw second moment to °F² by
`_c2_to_native_var = (9/5)**2 = 3.24` for F-cities. But the floor and the low-n shrink target in
`center.py` are built from `SIGMA_FLOOR = 0.8`, documented as **degC**
(`bayes_precision_fusion.py:53` "degC floor on per-source obs std"):

- `floor_m2 = SIGMA_FLOOR**2 = 0.64` — **degC²**, compared via `max(m2_eff, floor_m2)` against a
  native-unit `m2_eff` (line 204).
- `equal_m2 = (SIGMA_FLOOR*LOWN_INFLATE)**2 = 1.44` — **degC²**, BLENDED with the native-unit raw
  second moment in the low-n shrink `m2_eff = lam*raw_m2_f + (1-lam)*equal_m2` (line 199) — i.e.
  it **adds °F² to degC²**.

For the 11 F-settled cities (NYC, Chicago, Atlanta, Miami, Dallas, Austin, … — the active US
weather book; `config/reality_contracts/data.yaml`) the shrink target is 3.24× too small in the
correct (native) units, so a thin-history member is shrunk toward an artificially HIGH precision
→ it gets MORE weight than the shrink-to-equal rule intends (the intent is INVERTED at low n).

EVIDENCE (numerically run, two thin F-city members, A good/B bad RMSE):
  n=10 (lam 0.56): weight(A) BUGGY 0.782 vs CORRECT 0.749  (Δ +3.3pp)
  n=24 (lam 0.75): weight(A) BUGGY 0.792 vs CORRECT 0.776  (Δ +1.6pp)

REALIST CHECK (why HIGH, not BLOCKER): the distortion is a few pp of relative weight, bounded:
(a) the floor `max(·, 0.64 degC²)` rarely bites for F-cities (native °F² m2 values dominate the
degC² floor), so deep-history (n ≥ MIN_TRAIN=25) F-city members are correctly weighted (the
uniform ×3.24 cancels in the sum-to-1 normalization — only the floor/shrink constants break it);
(b) it only bites thin cells (n<25): newly-added decorrelated providers (gfs_hrrr/gem_hrdps per
the 2026-06-17 model-set change) and sparse (city,metric,lead) cells; (c) the convex-combination
invariant INV-C1 still holds (weights non-negative, sum-1 — `test_center_envelope.py` green), so
this never leaves the member envelope, never corrupts settlement, never crashes. The C-cities (5)
are unaffected (scale = 1.0). It is HIGH because it is a real wrong-weight on the live RAW center
for the majority (F) book at exactly the thin-history cells the shrink exists to protect.

FIX. Carry the unit into the constants, OR scale the raw second moment back to degC² before the
weight math. Minimal correct option in `center.py:177-204`: derive the native-unit scale from the
case unit (the same `(9/5)**2` factor) and apply it to BOTH `floor_m2` and `equal_m2`:
  `u = 1.0 if case.metric-unit == "C" else (9.0/5.0)**2`
  `floor_m2 = (SIGMA_FLOOR**2) * u ;  equal_m2 = ((SIGMA_FLOOR*LOWN_INFLATE)**2) * u`
(`ForecastCase` must expose the settlement unit, or pass it through the member.) Alternatively
convert `raw_m2_native` → degC² at the top of the loop (`raw_m2_f /= u`) so ALL of floor/shrink/
raw_m2 are in degC². ADD a test: an F-city low-n two-member set where the good model's weight
matches the degC²-consistent computation (RED on the current mixed-unit code).

---

## MEDIUM — "ONE RAW belief / entry==exit==monitor identity" is NOT achieved — entry and exit are still two DIFFERENT fusion engines (report S3 overstates the unify)

PROBLEM. The report (impl_raw_diagonal_fusion_report.md S3, lines 64-68) claims entry (spine),
exit (`position_belief`), and monitor "all read ONE RAW belief … the single surgical unify
point." That is **only half true**. The change unifies the *de-bias regime* (both are now RAW,
EB shift removed, `bias_shift_c = None`) but the two centers come from **structurally different
fusion formulas**:
- ENTRY (spine PD): `walk_forward_model_weights` → diagonal `1/E[r²]` convex center +
  `weighted_huber_location` (`center.py`). The live entry decision builds this PD from
  `raw_model_forecasts` (`event_reactor_adapter.py:8303`); it does **not** read
  `forecast_posteriors.q_json` for its center (only for cert provenance, :6780).
- EXIT/MONITOR (forecast_posteriors.q_json via `position_belief.load_replacement_belief`):
  `fuse_bayes_precision_posterior` → anchored full-covariance Bayesian `mu* = V*(τ0⁻²μ0 +
  1'Σ⁻¹z)` with the ecmwf anchor prior + off-diagonal Σ (`bayes_precision_fusion.py:135-157`,
  `replacement_forecast_materializer.py:1291`).

So `μ_entry ≠ μ_exit` for the same family even after this change — the task's explicit question
"Entry==exit==monitor identity?" answers **NO at the center level**. The diagonal-1/E[r²]
upgrade (S1–S2) is applied ONLY to the spine entry; the materializer center the exit/monitor
read is unchanged in fusion method.

REALIST CHECK (why MEDIUM, not HIGH): this center-method divergence is **pre-existing**
architecture (the spine entry never read forecast_posteriors.q_json for its center; `position_
belief.py:4-20` documents the intent that exit read "the SAME table the entry decision used" —
but the live entry does not in fact source its center there). The change does not introduce the
divergence; it correctly REMOVES one component of it (the EB shift). No money-path regression.
But the disclosure matters: the operator's stated goal (memory: "Entry brain and exit brain read
different data sources") is only partially met, and shipping under a "one belief, entry==exit"
banner risks a false sense that the two-center split (#135) is fully closed. FIX (disclosure +
optional follow-up): correct the report/spec to say "the EB-shift divergence is closed; the
fusion-method divergence (diagonal entry vs anchored-Bayesian exit) remains and is a separate
item." If true center identity is required, either (a) have the entry also serve from
forecast_posteriors.q_json, or (b) apply the diagonal `1/E[r²]` weighting in the materializer's
fused μ* path too — a deliberate decision, not a silent gap.

---

## MEDIUM — qlcb guard ACTIVATION contract: the (not-yet-built) OOF table must be keyed with the EXACT same {modal,nonmodal} + lead_bucket scheme or every ACTIVE cell silently misses → INERT

The guard is INERT and safe today. But when the operator places `state/qlcb_oof_reliability.json`
the cell key is `metric|lead_bucket(L1/L2_3/L4P)|bin_position|qbN` where the engine supplies
`bin_position ∈ {"modal","nonmodal"}` ONLY (`family_decision_engine.py:941`) and `lead_days =
case.lead_hours/24` bucketed by `qlcb_reliability_guard.lead_bucket` (≤1→L1, ≤3→L2_3, else L4P).
The module docstring/`cell_key` says "modal/shoulder/tail or whatever the OOF table was built
with" — a RICHER vocabulary than the engine actually emits. If the offline fitter builds the
table with any other `bin_position` label set, or computes lead differently, EVERY live lookup
returns `None` → INERT pass-through → the guard NEVER deflates even when "ACTIVE". That is a
silent no-op, not a loud failure. This is not a code defect (it is the artifact contract), but it
MUST be written into the table-builder spec: bin_position ∈ {modal,nonmodal}, lead via the SAME
lead_bucket thresholds, metric lower-cased, q_lcb bucket via QLCB_BUCKET_EDGES. Otherwise live
activation produces zero behavior change and the operator will believe the guard is protecting
trades when it is not.

---

## LOW — provenance/header cites a non-existent spec file (see "SPEC NOT FOUND")

`src/decision/qlcb_reliability_guard.py:3` (Authority basis) and every "§N" comment point at
`FINAL_no_shadow_execution_flow_2026-06-18.md`, which is not in the repo. Commit the spec, or
re-point the headers to a resolvable authority. The provenance law requires a resolvable basis.

---

## What's verified SAFE (hunted hard, no defect)

1. **INV-37 / DB writes**: CLEAN. The diff adds ZERO new DB writes/connections (grep of all
   added lines for connect/INSERT/UPDATE/DELETE/ATTACH/SAVEPOINT/BEGIN/COMMIT = none). The new
   `raw_second_moment_by_model` REUSES the passed `conn` (SELECT-only, strict `target_date <`
   no-leak, fail-soft→empty). The qlcb guard touches no DB (reads one state/*.json).
2. **Submission pipeline contract**: UNCHANGED. No file under src/execution, src/venue,
   src/riskguard, src/control, src/supervisor_api, or any migration is in the diff.
3. **§7 provenance gate cannot halt live**: VERIFIED. The live spine builder is constructed with
   `_spine_debias_authority(case)` which is UNCONDITIONALLY `_NoOpDebiasAuthority`
   (qkernel_spine_bridge.py:162-175) → `aggregate_shift_native = 0.0` (line 638) → the
   `abs(shift) > 1e-9` gate never fires. The only other src builder site (pdb.py:486 helper) has
   NO src callers (test seam only). The center_method whitelist {WEIGHTED_HUBER_CONSENSUS,
   SHRUNK_EMOS, RAW_FALLBACK} == the three CenterEstimate.center_method Literals exactly (all
   three assignment sites covered) → no spurious live ineligibility. `DebiasAuthority(())` and all
   refusal paths return zero shift, so the existing test seams stay green (verified: 11 passed).
4. **EB removal (z = raw)**: CORRECT. `_eb_corrected → _raw_instrument` returns `float(raw_value)`,
   `eb_bias` import removed from the consumed-center path, `bias_shift_c` forced None in the
   materializer (`get_city_debias_c` no longer reached). The walk-forward history is retained for
   width/provenance only. No consumer feeds an EB center after this (traced below).
5. **forecast_posteriors consumer unify (de-bias regime)**: VERIFIED. The only live center/q
   consumers are `position_belief.load_replacement_belief` (q_json, exit) and `monitor_refresh`
   (which reads belief THROUGH `position_belief.load_replacement_belief`, :619) — both now RAW.
   `continuous_redecision` reads only `source_cycle_time` (freshness, not a center);
   `current_target_plan` reads COUNT(*) (readiness); `settlement_skill_attribution` reads q_json
   for GRADING only (not money path); the `event_reactor_adapter` posterior reader (:6780) reads
   cert provenance (identity hash / source_run_id), not a center. No EB center consumed anywhere.
6. **S4 Path-B simplex renorm**: CORRECT and COMPLETE. `_build_fused_q_bounds` now renormalizes
   each draw row to the simplex before the marginal 5th percentile — the IDENTICAL transform the
   authority `build_joint_q_band` performs (renorm inside `build_joint_q`'s integrate_all_bins).
   The joint_q_band.py:14,43 "NO per-row renorm" comment describes the OLD defect being replaced,
   not the authority's behavior. These are the ONLY two raw-per-bin-percentile sites in src; no
   other live caller. Path-A semantics preserved; degenerate all-zero rows left as-is (no
   div-by-zero). `test_path_b_qlcb_modal_collapse_regression` green.
7. **q_lcb_route reconstruction `= edge_lcb + cost`**: CORRECT. `edge_lower_bound =
   quantile(samples @ payoff − cost) = quantile(samples @ payoff) − cost`, and the payoff vector
   is a 0/1 indicator (YES=e_i, NO=1−e_i; instruments.py:161), so `samples @ payoff ∈ [0,1]` is a
   genuine win-probability → bucketing into [0,1] edges and comparing to a Wilson hit-rate is
   sound. (Assumption to preserve: all live routes use 0/1 payoff vectors; a scaled payoff would
   break the [0,1] bucketing — note for any future non-binary instrument.)
8. **INERT shipping safe**: VERIFIED. Artifact absent → `_load_reliability_table` returns {} →
   every `table.get(key)` None → `apply_guard` returns basis="INERT", abstained=False → engine
   line 948-950 returns the candidate unchanged. `scored` is byte-identical. Abstain (when ACTIVE)
   re-stamps edge_lcb=−cost and optimal_delta_u≤0, and `_select` (line 68) filters edge_lcb>0
   BEFORE selection/sizing, so an abstained candidate cannot trade or size. Read-only on μ.
9. **3-tuple producer change**: SAFE. Both call sites unpack 3 (event_reactor_adapter:6826 cert,
   :8310 live-stash); exactly 2 callers + the def, no stale 2-tuple unpack. Contract tests updated
   and green.
10. **Weights basis 1/E[r²], nonneg+sum-1, low-n→equal, no demeaning**: CORRECT (modulo the HIGH
    unit bug). Uses raw second moment (bias² included), never np.var/np.std/shrink_cov; all-absent
    → exact 1/n; have_any_signal guard correct. History provider uses strictly-prior date-aligned
    RAW residuals (no look-ahead, no EB).

## Test posture
New + contract suites green (23 passed): test_raw_diagonal_weights, test_raw_center_and_provenance,
test_qlcb_reliability_guard, test_qlcb_guard_decision_integration, test_path_b_qlcb_modal_collapse,
test_raw_unify_forecast_posteriors, test_qkernel_spine_sources_multimodel. Regression sweep green:
test_family_decision_engine (11), test_center_envelope (5), test_single_predictive_distribution
_authority, port-fidelity + tests/probability (24p/1s). Pre-existing fails per the report were not
re-audited line-by-line (out of the changed surface).

## OOF-table-before-live note
The OOF reliability table is NOT required before landing (guard ships INERT = byte-identical). It
IS required before the guard can DEFLATE anything; when placed it MUST use the {modal,nonmodal} +
L1/L2_3/L4P + QLCB_BUCKET_EDGES cell scheme above or it silently no-ops. The S1–S2 diagonal
WEIGHTS, by contrast, go live immediately on land (no artifact gate) — so the HIGH unit-mismatch
fix should precede landing, not the OOF table.

## VERDICT: GO-WITH-FIXES
Fix the HIGH unit-mismatch (center.py:177-204) before landing — it changes the live F-city center
weights today. Correct the MEDIUM unify overstatement in the report (entry≠exit center). Commit
the missing spec. Then GO. OOF table not required before land; required (with the matching cell
scheme) before guard activation.
