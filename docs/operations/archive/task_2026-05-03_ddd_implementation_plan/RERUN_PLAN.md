# DDD Phase 1 — Complete Rerun Plan

Created: 2026-05-03
Authority: operator directive 2026-05-03 — "深度计划完整重现，不再introduce结构性错误"
  + tribunal review at `review.md` (this folder) — adversarial verdict SHADOW_ONLY
Status: **PLAN ONLY — no execution until operator approval**

## §0 Scope and posture

**Scope**: This plan defines the rerun of all Phase 1 §2.1 – §2.4 work to
address the structural errors identified by the tribunal review at `review.md`,
plus produce the missing §2.5 / §2.6 work, plus add the data quality
investigation that the original Phase 1 skipped.

**Posture**: Even after this rerun completes, DDD must remain `SHADOW_ONLY`
per tribunal §16. The rerun's purpose is **not** to make DDD live-ready. It
is to make Phase 1 conclusions DEFENSIBLE so that future SHADOW→GATED→LIVE
promotion has a sound foundation.

**Operator pre-commitment requested before execution**:
1. The rerun output may invalidate one or more existing conclusions
   (§2.1 floors, §2.2 k=0, §2.3 σ_window, §2.4 curve). Operator agrees that
   acceptance of a previous conclusion does NOT bind acceptance of the same
   conclusion after rerun.
2. Time/DB compute budget: full rerun with corrected pipeline is estimated
   at 4-8 hours of script execution + multiple operator decision-checkpoints.
3. SHADOW_ONLY posture for v1 is **non-negotiable** unless rerun produces
   evidence that materially exceeds tribunal's promotion criteria (§13.11).

---

## §1 The eight structural errors to address

Distilled from `review.md`. Each is rated by:
- **Verified**: confirmed against actual repo / DB by reconnaissance probes
- **Severity**: how badly it corrupts the existing Phase 1 conclusion
- **Fix cost**: rough hours
- **Risk of introducing NEW errors**: what could go wrong while fixing it

| # | Structural error | Verified? | Severity | Fix cost | New-error risk |
|---|---|---|---|---|---|
| E1 | Zero-obs days invisible to percentile (Denver lost 3 days) | YES | HIGH | 1h | LEFT JOIN denominator wrong by 1 if mistime |
| E2 | HIGH-only generalized to LOW (LOW window crosses midnight) | YES | HIGH | 4h | LOW peak hour derivation has no ground truth |
| E3 | Operator overrides without provenance label | YES | LOW | 30m | low — pure metadata |
| E4 | DST denominator not 23/24/25 | YES (London 2025-03-30) | MEDIUM | 2h | over-correction breaks non-DST cities |
| E5 | Critical-window vs 24h hard kill | YES (architectural) | HIGH | 1h | redefining 0.35 threshold for directional |
| E6 | Source/station segmentation absent | NO (no migrations in DB) | LOW now / HIGH future | 0h+infra | adding segment key now invites premature complexity |
| E7 | Independent N vs row N for §2.2 | YES | HIGH | 2h | wrong group definition cascades |
| E8 | **Platt full-sample leakage** (regen 2026-04-29 covering all of test window) | YES (Tokyo recorded_at 2026-04-29 10:00-10:16, 682k rows) | **CRITICAL** | 8h+ | refit pipeline must isolate train data |

**E8 is the most dangerous**: it invalidates §2.2 entirely. The rerun cannot
produce a defensible §2.2 without first refitting Platt with proper
time-window isolation. This is the highest-impact fix and also the
highest-cost one.

---

## §2 Reconnaissance probes (already executed during plan production)

These probes were run during plan drafting to verify which structural errors
are real vs hypothetical:

```
Probe 1: data_coverage table exists in DB → YES (use it for expected slots)
Probe 2: cities with >1 station_id in wu_icao_history → NONE (E6 not current)
Probe 3: Tokyo HIGH calibration_pairs_v2 recorded_at distribution
         → all 682k rows on 2026-04-29 10:00-10:16 → E8 CONFIRMED LEAKAGE
Probe 4: Lagos days with ANY HIGH-window obs vs ANY obs → 183 vs 184 (1 missing day)
Probe 5: Denver days with ANY wu_icao_history obs in 2025 H2 → 181/184 (3 missing days)
Probe 6: Paris distinct stations → ['LFPB'] only, range 2024-01-01 → 2026-05-02
Probe 7: London 2025-03-30 (spring forward) → only 1 hour observed
         (DST gap mop-up #12 unfinished for that day)
Probe 8: Tokyo target_date != DATE(utc_timestamp) → 7677 rows
         (expected: target_date is local-day, ingestion is correct)
```

These probes are **load-bearing for the plan** but should be re-run as part
of P0 reproduction to ensure the DB hasn't drifted between plan and execution.

---

## §3 Phased execution plan

The phases are ordered such that earlier phases don't depend on later phases'
outputs. The reverse is not true: Phase H reuses Phase D's refit Platt model,
Phase E's expected-slots, Phase F's DST denominators, and Phase G's LOW
window definitions.

### Phase A — Reproducibility verification (P0)

**Goal**: Before any changes, verify existing Phase 1 outputs reproduce
exactly. Catches any environment drift, data version drift, or accidental
script edits since original execution.

**Steps**:
1. Compute SHA256 of all files in `phase1_results/` and `phase1/`
2. Re-run each Phase 1 script from a clean Python environment using the
   committed scripts
3. Diff outputs:
   - JSON: semantic-diff (key-value compare, ignore ordering)
   - MD: byte-diff
4. Record:
   - DB git SHA at time of original (if known) vs now
   - `data_version` distinct values in observation_instants_v2 (snapshot)
   - calibration_pairs_v2 max(recorded_at) (snapshot)
   - Python version, pytest version (if any)

**Deliverable**: `phase1_reproduction/REPRODUCTION_REPORT.md`
  - PASS/FAIL with exact diff manifest
  - If FAIL, root-cause and decide: re-baseline plan or fix scripts

**Risk of introducing new errors**: minimal. This is verification only, no
changes.

**Decision checkpoint**: if reproduction FAILS, halt and re-baseline before
proceeding. Phase B onward assume reproducible Phase 1 state.

### Phase B — Schema/field forensic audit (P1)

**Goal**: For every variable used in Phase 1, prove it maps to the intended
real-world object.

**Variables to audit**:
- `target_date`: confirm ingestion sets it to city-local date (already
  evidenced by ingestion code; this phase formalizes the proof)
- `local_hour`: confirm it's local-clock-hour, not UTC-hour
- `source`: enumerate all source values used in observation_instants_v2; flag
  any city using non-WU primary
- `data_version`: confirm `v1.wu-native` is canonical and there's no
  silent-fallback to other versions
- `authority`: confirm `VERIFIED` filter excludes the right things; spot-check
  a few `UNVERIFIED`/`QUARANTINED` rows
- `outcome=1`: in calibration_pairs_v2, confirm semantics (winner indicator,
  exclusive per (city, target_date, metric))
- `temperature_metric`: confirm 'high' / 'low' are the only values
- `is_missing_local_hour`, `is_ambiguous_local_hour`: confirm these flags are
  populated correctly for DST days
- `station_id`: confirm population per city; future segment-key reservation

**Deliverable**: `phase1_rerun_audit/B_schema_audit.md`
  - per-variable mapping table
  - SQL snippets used in audit
  - flagged rows or columns

**Risk of introducing new errors**: medium. If I misread a field's meaning
during the audit, all downstream phases inherit the misreading. **Mitigation**:
quote ingestion code or tests that demonstrate the field's semantics, don't
infer.

**Decision checkpoint**: if any field is found to mean something different
than I assumed in original Phase 1, the corresponding original conclusion is
INVALIDATED and the rerun for that section must use the corrected reading.

### Phase C — Data quality investigation (P-new)

**Goal**: Quantify each data-quality issue identified in §1 (E1, E5
specifically). This gives concrete evidence for downstream phases.

**Steps**:
1. **Zero-obs days** (E1): per (city, year-month), count of days with zero
   `wu_icao_history` rows. Tabulate. Identify which cities are most affected.
2. **Critical-window catch rate** (E5): for each catastrophic test day from
   §2.1 (the 15 days at cov<0.35), check daily 24h coverage. If daily=0.85
   but directional=0.143, the §7 absolute kill at 0.35 (24h-based) would
   FAIL while the directional check would FIRE. Quantify how many
   "critical-window-only" outages this misses.
3. **DST handling** (E4): for every DST transition day in 2025 in our
   tracked timezones, count: `local_hour` distinct values, `is_missing_local_hour`
   flags, `is_ambiguous_local_hour` flags. Verify ingestion populates flags
   correctly.

**Deliverable**: `phase1_rerun_audit/C_data_quality_quantification.md`
  - Per-(city, year-month) zero-obs-day count table
  - Critical-window-only outage catalog
  - DST-day audit table

**Risk of introducing new errors**: low. This is descriptive analysis, not
calibration. **Mitigation**: read the table, don't recompute floors yet.

**Decision checkpoint**: based on Phase C output, decide:
- Zero-obs days: how to count them (treat as cov=0 in P10? exclude entirely?
  flag as separate "outage" category?)
- Critical-window kill: should §7 absolute kill be redefined on directional
  cov instead of daily cov? (Recommendation: YES.)

### Phase D — Platt time-window-isolated refit (E8 fix)

**Goal**: Refit Platt models using ONLY data with `target_date < 2026-01-01`,
so that test-window evaluation is genuinely out-of-sample.

**This is the BLOCKER for §2.2 rerun**.

**Steps**:
1. Identify the canonical Platt training script in repo
2. Build a wrapped invocation that:
   - Writes new model rows to `platt_models_v2_train_only` (NEW namespace,
     does NOT pollute production `platt_models_v2`)
   - Writes new pair rows to `calibration_pairs_v2_train_only` (same)
3. Refit per (city, metric, cluster, season) with `target_date < 2026-01-01`
4. Re-derive calibration pairs (p_raw, outcome) using the new train-only
   models on **all** dates, including 2026-01-01+
5. Verify: for any (city, target_date, metric) in test window, p_raw should
   differ between full-sample and train-only fit (sanity check that the
   refit actually used different data)

**Deliverable**:
- New tables `platt_models_v2_train_only`, `calibration_pairs_v2_train_only`
  (operator-named so they don't pollute production)
- `phase1_rerun_audit/D_platt_refit_audit.md` — verification that train_only
  models differ from full-sample, plus per-(city,metric) sample size counts

**Risk of introducing new errors**: VERY HIGH. Platt fit pipeline has multiple
hidden dependencies (regularization params, weight schemes, season clusters,
v2 vs legacy fields). Re-running incorrectly could produce models that look
plausible but use wrong inputs. **Mitigations**:
1. Use the existing canonical Platt training entry-point with a `--train-end-date`
   parameter or equivalent — do NOT reimplement Platt fit
2. Spot-check: for one large-N city/metric (e.g., Tokyo HIGH), compare
   train-only fit's parameters to documented Platt expectations
3. For each (city, metric), report N_train, N_test, refit converged or not
4. Do NOT delete the original `platt_models_v2` / `calibration_pairs_v2` —
   they remain the canonical production state. The rerun uses a parallel
   namespace.

**Decision checkpoint**: if Platt refit fails or produces unstable results
on any (city, metric), §2.2 rerun for that bucket must be SHADOW_ONLY and
the multiplier-validation hypothesis remains "untestable for that city".

### Phase E — Expected-slot denominator (E1 fix)

**Goal**: Replace `count(distinct local_hour) / fixed_window_size` with
`observed_slots / expected_slots(city, target_date)` using the
`data_coverage` table.

**Steps**:
1. Inspect `data_coverage` table schema. What fields does it have? Is it
   per (city, target_date) or per (city, target_date, hour)?
2. If `data_coverage` provides expected-slot count per day: use it directly.
3. If not: build expected-slot generator per (city, target_date) using:
   - city timezone
   - DST flags (use is_missing_local_hour / is_ambiguous_local_hour)
   - source cadence (assume hourly for WU primary; verify)
4. Verify: zero-obs days (from Phase C) should now appear with cov=0 in the
   recomputed series, not be silently absent.

**Deliverable**: 
- New computation function `directional_coverage_with_expected_slots(city, target_date, track)`
- Per-(city, year-month) summary showing how many days had cov<old_value
  due to zero-obs-day inclusion
- `phase1_rerun_audit/E_expected_slot_denominator.md`

**Risk of introducing new errors**: medium-high.
- If expected-slots over-count (e.g., always 24 even on DST days), coverage
  ratios get artificially deflated on those days
- If expected-slots under-count (e.g., 23 on a non-DST day), coverage ratios
  exceed 1.0
- If `data_coverage` table itself has errors or stale data, propagated bug
- **Mitigation**: spot-check 5 cities × 5 dates each, manually verify
  expected vs observed makes sense. Use multiple methods to cross-validate.

**Decision checkpoint**: if data_coverage table is unreliable, fall back to a
generated expected-slot calendar; document choice.

### Phase F — DST denominator handling (E4 fix)

**Goal**: For DST transition days, expected-slot count is 23 (spring forward)
or 25 (fall back), not 24.

**Steps**:
1. From Phase C audit: enumerate DST transition days for each tracked
   timezone in 2024-01-01 → 2026-04-30
2. For each DST day, expected-slot calendar must reflect:
   - Spring forward: 23 local hours
   - Fall back: 25 local hours (the repeated hour gets disambiguated by
     `is_ambiguous_local_hour`)
3. For directional windows: confirm DST gap usually FALLS OUTSIDE peak
   window for HIGH (peak ≈ afternoon, gap ≈ 02:00 night). For LOW window
   (dawn), the spring-forward gap CAN fall inside.
4. Add per-day expected-slot field to coverage computation.

**Deliverable**: `phase1_rerun_audit/F_dst_denominator.md`
  - DST day catalog
  - Sanity check: London 2025-03-30 directional HIGH window 11-17 → 7
    expected slots (DST gap is 01:00-02:00, outside window) → expected
    cov computation unchanged
  - LOW window for London on same day: dawn 02:00-08:00 → ONE expected hour
    is missing (the DST gap) → 6 expected slots that day

**Risk of introducing new errors**: medium.
- Mis-classifying DST days for non-US timezones
- Wrong direction of correction (subtracting 1 instead of treating as gap)
- **Mitigation**: cross-check with `is_missing_local_hour` flag — every DST
  spring-forward day should have rows flagged.

**Decision checkpoint**: confirm with operator the policy for fall-back
ambiguous hour: count as 1 slot or 2 slots?

### Phase G — LOW track definition (E2 fix)

**Goal**: §2.1 / §2.3 / §2.4 must be re-run for LOW track separately. This
requires:
1. Per-city `historical_low_hour` (currently absent in cities.json)
2. LOW-specific window radius (may differ from HIGH's ±3)
3. Cross-midnight handling for LOW windows that span the local date boundary

**Steps**:
1. Empirically derive `historical_low_hour` per city from
   `observation_instants_v2.running_min` (the hour where daily min was
   observed). Same method as my §2.2 reconnaissance for HIGH peak.
2. Per-city LOW peak distribution: histogram of low_hour over 2025 H2.
   Identify common peak hours.
3. For each city, choose `historical_low_hour` as median or mode of low-day
   distribution.
4. Verify cross-midnight behavior:
   - Tokyo (UTC+9): if LOW occurs at local 04:00, that's UTC 19:00 of the
     PREVIOUS calendar day. Does our `target_date` correctly represent the
     local-day this belongs to?
5. Define LOW window: `historical_low_hour ± radius_low` (radius pending
   §2.6 validation; provisional value 3 hours)
6. For LOW window crossing midnight: hours straddle two local dates. Must
   confirm settlement convention — does PM include all 24 hours of local
   day_X or include some hours from day_X-1?

**Deliverable**:
- `phase1_rerun_audit/G_low_track_definition.md`
- Per-city `historical_low_hour` table
- Cross-midnight test cases (Tokyo, Seoul, NYC, etc.)

**Risk of introducing new errors**: HIGH. This is the most semantically
risky correction.
- If LOW peak hour is wrong, the LOW directional coverage measures the wrong
  hours, every downstream LOW conclusion is built on noise
- Cross-midnight: easy to off-by-one in date assignment
- **Mitigations**:
  1. Cross-validate empirically-derived low_hour against external climate
     references (e.g., NOAA's climate normals for "average daily minimum
     temperature time" if available)
  2. Spot-check 3 cities manually: does the low-hour distribution make
     physical sense? (Tokyo dawn ≈ 04:00-05:00 local in summer)
  3. For cross-midnight, write a unit test with a synthetic case before
     applying to real data

**Decision checkpoint**: operator approval on LOW peak hour values (per-city
table) before they enter floor calibration.

### Phase H — Re-run §2.1, §2.3, §2.4 with all corrections

**Goal**: With Phases B-G complete, re-execute the original Phase 1 §2.1,
§2.3, §2.4 analyses using:
- Phase E's expected-slot denominator
- Phase F's DST handling
- Phase G's LOW track definition (separate calculation)
- Phase B's verified field semantics
- Phase C's zero-obs-day visibility (cov=0 days now in the percentile data)

**Steps**:
1. **§2.1 HIGH redo**: per-city floor recommendations using corrected
   pipeline. Compare to original. Identify cities whose floor changed.
2. **§2.1 LOW redo** (NEW): per-city LOW floors. Likely different from HIGH.
3. **§2.3 σ_window redo**: ACF on corrected daily coverage series. May find
   different autocorrelation structure with zero-obs days included.
4. **§2.4 curve redo**: shortfall buckets recomputed with corrected
   coverage; mismatch proxy unchanged.

**Deliverable**: `phase1_rerun_results/` directory with:
- `H_p2_1_HIGH_corrected.md` + JSON
- `H_p2_1_LOW_corrected.md` + JSON
- `H_p2_3_sigma_corrected.md` + JSON
- `H_p2_4_curve_corrected.md` + JSON
- `H_DELTA_REPORT.md` — what changed vs original?

**Risk of introducing new errors**: medium. By this point, most error sources
have been addressed in earlier phases. Remaining risk is implementation bugs
in the corrected scripts. **Mitigation**: each corrected script imports the
shared expected-slot computation function (single source of truth), no
inline duplication.

**Decision checkpoint**: per-city floor changes ≥ 0.10 must be operator-reviewed.

### Phase I — §2.2 rerun with Platt train-only refit (E8 fix)

**Goal**: Re-execute §2.2 multiplier validation using Phase D's train-only
Platt models. This is the genuine out-of-sample test.

**Steps**:
1. Use `calibration_pairs_v2_train_only` as the data source
2. Compute per-(city, metric):
   - N_independent = COUNT(DISTINCT (target_date, metric)) where train_only
     model exists, target_date < 2026-01-01 (the actual independent decision
     points)
   - Brier_test = mean error on test rows (target_date >= 2026-01-01) using
     train_only p_raw
3. Run the same regression as my original §2.2: Brier ~ a + b/sqrt(N)
4. Compare: does the train_only k_estimate equal the full-sample k_estimate?

**Possible outcomes**:
- A. Refit confirms k=0 → original §2.2 conclusion VINDICATED despite
     contaminated foundation
- B. Refit shows k > 0 (genuine small-sample effect previously masked by
     leakage) → multiplier should be re-enabled at empirical k value
- C. Refit shows wildly different k → the foundation was so contaminated
     that no inference is possible without more data

**Deliverable**: `phase1_rerun_results/I_p2_2_train_only.md` + JSON
  - All three outcome scenarios pre-specified with operator decision tree
  - Final k recommendation

**Risk of introducing new errors**: depends on Phase D's correctness. If
Phase D's refit is wrong, Phase I conclusion is wrong.

**Decision checkpoint**: operator must approve the new k value (if any)
before it enters Phase J implementation.

### Phase J — §2.5 + §2.6 (missing pieces)

**Goal**: Address the missing PLAN.md sections.

**§2.5 small_sample_floor**: empirical justification for `if N<100, DDD ≥ 0.05`
- Sample size threshold below which Brier variance explodes
- Use Phase I's train-only data so this isn't leakage-contaminated

**§2.6 peak_window radius**: empirical justification for ±3 hours
- Per-city distribution of actual peak local_hour
- Sensitivity test: at what radius does 95% of historical extremes fall in?
- Cross-validate HIGH and LOW radii independently

**Deliverable**: 
- `phase1_rerun_results/J_p2_5_small_sample_floor.md`
- `phase1_rerun_results/J_p2_6_peak_window_radius.md`

**Risk of introducing new errors**: low. These are descriptive analyses with
simple acceptance criteria.

### Phase K — Tail-loss and executable EV (NEW from review §13.9 + §13.10)

**Goal**: The reviewer correctly noted that Brier/ECE don't predict trading
P&L. This phase adds:
- Tail-loss metric: P(|settlement_error| ≥ 1 bin) per shortfall bucket
- Executable EV proxy: simulate Kelly trades over test window with and
  without DDD discount; compare hypothetical P&L

**This phase is OPTIONAL for v1**. It directly supports promotion from
SHADOW to LIVE but doesn't gate the SHADOW_ONLY rollout.

**Deliverable**: `phase1_rerun_results/K_tail_and_ev.md`

**Risk of introducing new errors**: medium. Simulation has many degrees of
freedom (entry/exit logic, fee model, slippage). **Mitigation**: use the
simplest possible simulator that imitates production — no parameter tuning.

### Phase L — Final delta report and promotion decision

**Goal**: One document summarizing every change, every empirical result, and
the resulting policy.

**Sections**:
1. Original Phase 1 conclusions (frozen reference)
2. Per-section corrected conclusions
3. Cities/metrics/sections where conclusion CHANGED
4. New parameter values (with provenance: data-driven vs operator-policy)
5. Implementation gates: which DDD components can go to SHADOW vs blocked
6. Open questions remaining

**Deliverable**: `phase1_rerun_results/FINAL_DELTA.md`

**Decision checkpoint**: Operator final review. Approves or sends back for
specific re-investigation.

---

## §4 New-error catalog — what to NOT do during rerun

The operator's "don't introduce new structural errors" warning is the central
constraint. The following are anti-patterns I commit not to repeat:

### Anti-pattern 1: Reuse of previous bad conclusions

If §2.1 floors were wrong, ALL DOWNSTREAM Phase 1 work that used them is
suspect. The corrected §2.1 floors are reused in §2.4 (shortfall buckets)
and §7 hard-kill (relative rail uses city_floor).

**Discipline**: re-run downstream sections using corrected floors, not
shortcut-update-only-the-changed-cells.

### Anti-pattern 2: Operator overrides leaking into "data-driven"

In original §2.1, Denver/Paris jumped from 0.60 (data) to 0.85 (operator
override). The override was correctly labeled as Ruling A in narrative, but
the JSON manifest didn't carry a `floor_source: "policy"` field. Future
agents reading the JSON could mistake 0.85 as the data result.

**Discipline**: every per-city override gets `floor_source` and `decision_id`
fields with operator ruling reference.

### Anti-pattern 3: Multi-comparison fishing

The reviewer flagged that I tried multiple Brier metrics (all rows / winning
rows / ECE) in §2.2. Each test was post-hoc. If I run all three again with
train-only Platt, picking whichever passes is multi-comparison cheating.

**Discipline**: pre-register the metric for §2.2 rerun BEFORE looking at the
data. Use the same canonical metric across train and test. Report the
others as supplementary diagnostics, not decision criteria.

### Anti-pattern 4: Acceptance threshold drift

Operator's original criterion was R² > 0.10 for §2.2. I should not relax this
to "weak directional support" if the rerun gives R² = 0.05.

**Discipline**: same acceptance criteria as original PLAN.md §2 unless
operator explicitly approves a change.

### Anti-pattern 5: Implicit field default

When SQL omits a filter (e.g., no WHERE clause on `data_version` or
`authority`), default values get pulled in. In v2 schema, `authority`
defaults to 'UNVERIFIED' on legacy rows. Implicit inclusion can pollute.

**Discipline**: every SQL query in the rerun explicitly filters
`authority='VERIFIED'`, `data_version='v1.wu-native'`, `temperature_metric IN
('high','low')` as appropriate. Audit Phase H scripts for these filters
before running.

### Anti-pattern 6: Cross-window inference

Defining a baseline as "P10 of train window" smuggles forward-looking
information if the train window is selected after seeing test outcomes. My
original §2.1 used 2025-07-01 → 2025-12-31 train, which was operator-
specified before viewing test data — OK. But subsequent decisions (e.g.,
the choice of 90-day σ_window in §2.3) may have been informed by test-
window observations.

**Discipline**: the rerun's train/test split is FROZEN at 2026-01-01
boundary. No parameters tuned by looking at post-2026-01-01 data.

### Anti-pattern 7: One-script multi-purpose pollution

Original §2.1's `p2_1b_floor_sensitivity.py` reused queries from `p2_1_*.py`
but with different filters. If the shared SQL function had a subtle
difference between callers, results would diverge silently.

**Discipline**: rerun scripts use a single shared function for coverage
computation (Phase E deliverable). Per-section scripts call this function
with explicit parameters; no SQL inlined per script.

### Anti-pattern 8: "Fixing" via post-hoc rule

If the rerun shows Lagos's floor changed to 0.30, I might be tempted to
"fix" by saying "but operator wants Lagos to stay 0.45 per Ruling B".
That's data-overriding: claiming a result while applying policy on top.

**Discipline**: report the data-driven floor AS data-driven; if operator
applies override, label it explicitly per Anti-pattern 2.

---

## §5 Dependency graph and execution order

Phases that MUST complete before Phase H (the main rerun):

```
A (Reproduction) → B (Schema audit) → C (Data quality) → 
  ┌─→ D (Platt refit) ──────────┐
  ├─→ E (Expected slots) ───────┤
  ├─→ F (DST denominators) ─────┼─→ H (§2.1, §2.3, §2.4 redo)
  └─→ G (LOW track) ────────────┘   │
                                     ├─→ I (§2.2 train-only)
                                     ├─→ J (§2.5, §2.6)
                                     └─→ K (Tail/EV, optional)
                                                 │
                                                 └─→ L (Final delta)
```

D, E, F, G can run in parallel (independent prep work).
H depends on all of D, E, F, G.
I depends on D specifically.
J depends on H + I outputs (uses corrected floors and train-only Platt).
K is optional and depends on H + I.
L is the synthesis at the end.

**Parallel-safe phase pairs**: (D, E), (D, F), (D, G), (E, F), (E, G), (F, G)

**Total estimated wall clock if executed serially**: ~16-24 hours
**Parallel-execute optimized**: ~10-14 hours

---

## §6 Decision checkpoints requiring operator input

To minimize wasted execution if operator disagrees with a direction, the
following checkpoints PAUSE execution and request operator approval:

| Checkpoint | After phase | Decision required |
|---|---|---|
| C-1 | C | Zero-obs-day treatment: include as cov=0, exclude, or flag separately? |
| C-2 | C | §7 absolute kill: redefine on directional cov instead of daily cov? |
| F-1 | F | Fall-back DST ambiguous hour: count as 1 slot or 2? |
| G-1 | G | Per-city `historical_low_hour` table approval before flooring |
| H-1 | H | Cities whose corrected floor differs from original by > 0.10 — accept new value or apply policy override? |
| I-1 | I | If train-only Platt gives k > 0, revive multiplier in spec? |
| L-1 | L | SHADOW vs LIVE-GATE promotion of any DDD component |

---

## §7 Acceptance criteria for the rerun overall

The rerun is COMPLETE when:

1. Phase A reproduction passes OR re-baseline is documented
2. Every variable in Phase 1 is mapped (Phase B)
3. All 8 structural errors (§1) are addressed in code, not just narrative
4. Phase H produces corrected §2.1/§2.3/§2.4 outputs with deltas
5. Phase I either confirms k=0 with proper foundation OR provides empirical
   k value with Ruling 1-compliant time-window stability
6. Phases J, K (if executed) provide §2.5/§2.6 + tail-loss data
7. Phase L synthesis is operator-approved

The rerun is SUCCESSFUL (not just complete) when:

8. SHADOW_ONLY implementation can proceed with high confidence in inputs
9. Each Phase 2 implementation parameter has a rerun-derived value, NOT a
   pre-rerun value
10. The reference doc `zeus_oracle_density_discount_reference.md` is updated
    to reflect the new evidence

The rerun is INCONCLUSIVE if:

11. Phase D Platt refit cannot complete (model fitting failure for some
    cities) — those cities cannot have §2.2 rerun; multiplier remains k=0
    by default
12. Phase G LOW peak hour cannot be derived for some cities (insufficient
    sample) — LOW DDD inactive for those cities until samples accumulate

---

## §8 What this plan does NOT cover

Per scope discipline:

1. **Live promotion criteria** — tribunal §13.11 is operator-territory; I
   defer until rerun completes
2. **Phase 2 implementation** of `data_density_discount.py` — depends on
   rerun outcome; deferred
3. **Critical-window definition refinement** beyond current `peak ± radius`
   approach — tribunal hints at "actual extreme hour" detection; this is
   Phase 2 territory
4. **Source/station segmentation infrastructure** — not currently needed
   (no migrations in DB), reserve for future
5. **Cross-vendor canary** for T2 silent change detection — separate
   monitoring concern, see vendor change registry
6. **DDD-Platt double-counting attribution** — tribunal §11.2 raises this;
   solved by Phase D + Phase I rerun's clean comparison

---

## §9 Operator pre-approval checklist

Before I start executing, please confirm:

- [ ] Plan structure and phase ordering acceptable
- [ ] Rerun resource budget (16-24 hours serial, ~10-14 parallel) acceptable
- [ ] SHADOW_ONLY posture for v1 confirmed (no live trading effect even
  after rerun until separate live-promotion gate)
- [ ] Decision checkpoints (§6) — operator commits to be available for
  these per-checkpoint approvals
- [ ] New tables `platt_models_v2_train_only` / `calibration_pairs_v2_train_only`
  are acceptable side effects (parallel namespace, not modifying production)
- [ ] Cities.json edits will be deferred until Phase L final synthesis
  (no per-phase config edits)
- [ ] Anti-patterns §4 list — operator endorses these as binding constraints
- [ ] No more sub-agent dispatch for this work — operator's previous
  experience (haiku hallucination, sonnet partial completion) makes
  delegate-and-trust unsafe; main session executes
- [ ] Plan delivered alone, NO execution begins until operator says proceed

## §10 Rough work breakdown for execution authorization

| Phase | Wall-clock | What I write | What runs |
|---|---|---|---|
| A | 30m | small reproduction script + report MD | re-runs each phase1 script |
| B | 1.5h | audit MD, no new scripts | repo grep + DB schema dumps |
| C | 1h | data quality probe script + report MD | DB queries |
| D | **6-8h** | Platt refit wrapper or invocation | Platt training pipeline (heavy) |
| E | 2h | expected-slot computation function + tests | DB queries + verification |
| F | 1.5h | DST handling addition + tests | targeted DB probes |
| G | 3h | LOW peak derivation + cross-midnight tests | DB queries + verification |
| H | 2h | redo §2.1/§2.3/§2.4 scripts using corrected pipeline | computation |
| I | 1.5h | redo §2.2 with train-only Platt | computation |
| J | 1h | §2.5/§2.6 scripts | computation |
| K (opt) | 3h | tail-loss + EV simulator | simulation |
| L | 1h | synthesis MD | writeup only |
| **total** | ~24h serial | **~13 deliverable docs + 8-10 scripts** | |

If operator wants to scope down for v1: Phase A through Phase H is the
**minimum viable rerun** to address tribunal's §16.4 "REJECT FOR LIVE"
points. Phases I-L can be deferred to a v1.1 cycle.

---

## §11 Cross-references

- Adversarial review: `review.md` (this folder) — must be consulted at
  every phase to verify the relevant tribunal concerns are addressed
- Original PLAN.md: this folder — represents the unmodified plan that the
  rerun is correcting
- Phase 1 results: `phase1_results/` — frozen reference for the rerun
  baseline
- Reference docs (read-only inputs to the rerun):
  - `docs/reference/zeus_oracle_density_discount_reference.md`
  - `docs/reference/zeus_calibration_weighting_authority.md`
  - `docs/reference/zeus_vendor_change_response_registry.md`
  - `docs/reference/zeus_market_settlement_reference.md`
- Database canonical state at plan time: `state/zeus-world.db` (data_version
  `v1.wu-native`, calibration_pairs_v2 max(recorded_at) = 2026-04-29 10:16)
