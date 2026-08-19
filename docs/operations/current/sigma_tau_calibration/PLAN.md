# sigma_tau_calibration -- Plan

Date: 2026-07-28
Branch: `claude/sigma-tau-calib`
Status: active

## Problem

Live posteriors are under-dispersed lead-dependently on the CURRENT-EVIDENCE
(Day0 `_current_shape is not None`) path of the replacement materializer. An
OOS bakeoff (M0..M4 walk-forward, `/private/tmp/.../calib_curves/bakeoff.py`)
selected M2 -- per-`(unit_family, metric)` `k(tau)` times per-city variance
shrinkage -- as the best form that stays inside current law (no city bias
term, no market-price anchor, no historical floor on the current-evidence
shape). `tau = lead_issue_h` = hours from the posterior's ISSUE clock
(`source_cycle_time`) to the CITY'S LOCAL target-date end (next local
midnight, DST-aware) -- NOT `computed_at` (decision time) and NOT a UTC
cut; see "2026-07-28 design-review corrections" and "FIX 1" below for why
both corrections were required.

The current-evidence site (`replacement_forecast_materializer.py`, comment
"Historical k/w/floors would change a decision-time-only shape") hardcodes
`(1.0, 0.0, 0.0)` -- neutral by construction, never fitted. This replaces that
hardcode with a lookup into a NEW walk-forward-fitted artifact
(`state/sigma_tau_calibration.json`, written only by
`scripts/fit_sigma_tau_calibration.py`), following the exact precedent of the
existing `state/sigma_scale_fit.json` / `_replacement_sigma_scale_lookup` /
`_effective_unit_sigma_scale` machinery (materializer.py:1243-1370), which
already governs the HISTORICAL (non-Day0) path and stays untouched.

## Model (M2, evidence basis -- not re-derived here)

Per `(unit_family in {C,F}, metric in {high,low})` group:
- `tau = lead_issue_h` = hours from the posterior's ISSUE clock
  (`forecast_posteriors.source_cycle_time`, top-level DB column) to the END of
  `target_date` UTC. Buckets `[0,6),[6,12),[12,24),[24,36),[36,48),[48,72),
  [72,inf)`.
- `event_weight = 1/n_e` per `(city, target_date)` settlement event (`n_e` =
  that event's row count within the group being fit) -- multiple posteriors
  of one event are correlated (same outcome) and must not multiply-count.
- `k(tau) = argmax_k` of the event-weighted interval-censored bin likelihood
  `sum(w * log[Phi((U-mu)/(k*sig)) - Phi((L-mu)/(k*sig))])` over the settled
  integer's actual bin `[v-0.5, v+0.5)` (native units, F converted to Celsius
  edges) -- a 1-D bounded MLE (`scipy.optimize.minimize_scalar`), PRIMARY.
  Bucket `n_events<60` => UNFITTED, inherits the group-global pooled `k`
  (train-only). Whole group `n_events<60` => group refused, `k=1.0`
  everywhere (fail-closed, same law as `fit_sigma_scale.py` `MIN_CELLS`, now
  counted in unique events).
- Per-city variance correction, cities with `n_events>=30` (pooled across tau
  within the group): `c_raw` fit by the SAME interval-censored MLE with
  `sigma = k(tau)*c*sig` (k already fixed), shrunk
  `c_shrunk^2 = (n_e*c_raw^2 + n0) / (n_e + n0)`, `n0=100` (shrinks toward 1).
- Served `k_eff(unit, metric, tau, city) = k(tau) * c_shrunk(city)`; `w` and
  `floor_steps` stay exactly `0.0` (k-only artifact for this path; the
  uniform-mixture/absolute-floor terms are a DIFFERENT calibration surface
  and are out of scope here).
- A closed-form event-weighted spread-skill ratio (`std(z,ddof=1)/sqrt(mean(sig^2))`,
  the METHOD's original form before the 2026-07-28 design-review correction
  below) is retained as a REPORTED cross-check column (`k_normal_crosscheck`
  / `c_raw_normal_crosscheck`) but is never served.

`center`/`mu` is never touched (RAW law). No gates, no caps.

### 2026-07-28 design-review corrections (applied same day, before any artifact
was ever placed under `state/` -- these supersede the first cut above, not a
later revision; the bullets above already describe the corrected model)

1. **Tau clock.** The first cut indexed tau on `computed_at` (decision time).
   Live evidence: Hong Kong 2026-07-20 HIGH has 247 distinct `computed_at`
   values collapsing to just 4 distinct `source_cycle_time` values on the SAME
   day -- posteriors recompute far more often than the underlying forecast is
   reissued. A `computed_at`-anchored tau shrinks on every wall-clock
   recompute with no new information, a look-ahead-adjacent defect. Verified
   which candidate field is reliably populated:
   `forecast_posteriors.source_cycle_time` (top-level DB column) is 100%
   populated; `$.bayes_precision_fusion.current_evidence_shape.source_cycle_time`
   is ~96.5% populated and, on inspection, is frequently STALE relative to the
   top-level column (the current-evidence ENS shape is legitimately reused
   across cycles) -- using it would train/serve on a different clock than the
   one actually available and reliable at the site. The materializer's
   serving-side lookup was changed to `request.source_cycle_time`
   (`_lead_target_h`'s `computed_at` parameter renamed to `issue_time`); the
   fitter trains on the same top-level column. The old `computed_at`-anchored
   tau survives ONLY as a `--validate`-only comparison column
   (`lead_decision_h` / `taut_decision`), never used for fitting or serving.
2. **Event weighting.** Multiple posteriors for one `(city, target_date,
   metric)` settlement event share the same eventual outcome and are not
   independent draws. `MIN_BUCKET_N` / `MIN_GROUP_N` / `MIN_CITY_N` (still 60 /
   60 / 30) are now compared against the number of UNIQUE EVENTS touching the
   cell, not raw rows; both counts are reported (`n` / `n_events`).
3. **Likelihood.** Replaced the closed-form spread-skill ratio as PRIMARY with
   interval-censored MLE over the settled integer's actual bin -- this fits
   the traded quantity directly. The spread-skill ratio is retained as a
   reported cross-check, not served.
4. **Data fence.** `current_evidence_shape`'s `within`/`between`/`delta`
   component fields are only 100% populated for `computed_at >=
   2026-07-15T22:32:31Z` (86.6% before). This fitter never reads those
   component fields (only `anchor_value_c`/`predictive_sigma_c`, always
   populated), so no fence is applied to the query; the boundary is recorded
   in `_meta.components_fence_reference_ts` /
   `components_fence_applied=False` for audit, so a future editor who adds a
   component-field read is forced to confront the boundary.

**Two findings surfaced by these corrections, not defects:**
- **RAW-mu forces bias into k.** `z` has a non-zero mean (~+0.36C for C/high,
  i.e. `mu` is systematically a bit low relative to settlement) -- an
  existing, documented, UNTOUCHED fact (`center`/`mu` RAW law). The
  interval-censored likelihood assumes a ZERO-mean Gaussian centered exactly
  at `mu` (it has no bias term to fit, by design), so it can only explain a
  real mean offset by INFLATING k. This is why the primary (censored) k for
  C/high (~1.57 pooled) is substantially higher than the bias-corrected
  `k_normal_crosscheck` (~1.06, which explicitly subtracts the empirical mean
  before estimating spread). Both numbers are individually correct for what
  they measure; the primary number is larger because it is honestly paying
  for a bias it is not allowed to correct directly.
- **City correction is currently inert.** With `MIN_CITY_N` now counted in
  unique events and the live corpus spanning only ~17 days
  (2026-07-11..2026-07-27), NO city can yet reach 30 distinct settlement
  days, so every city's `c_shrunk` in the current production artifact is the
  neutral `1.0` (zero cities are eligible in ANY of the four groups). This is
  a natural, self-correcting consequence of switching from a row-count to an
  event-count threshold -- the per-city layer will start firing once the
  corpus accumulates ~30+ days per city -- and not a bug in this slice.

### 2026-07-28 second design-review pass (operator ran `--validate` independently)

The operator's own `--validate 2026-07-21` run found F/high's fitted `global_k=1.251` makes it
WORSE out-of-sample: censored delta `-0.086`, Normal cross-check also negative `-0.079`, and the
fitted coverage (`0.818`) overshoots nominal 0.683 -- correcting for C/high's real signal was
bleeding into F/high, which does not have one (yet, on this data). Three required changes:

5. **Per-group OOS acceptance gate.** No group may ship a `k != 1.0` that has not proven a
   POSITIVE censored-likelihood OOS delta -- the fail-closed principle applied to the fit itself,
   not just to a missing/malformed artifact. `scripts/fit_sigma_tau_calibration.py:gate_group()`:
   fits on a train split, evaluates the SAME event-weighted interval-censored OOS delta the
   `--validate` report already computes (factored into a shared `_censored_oos_delta()` helper so
   the two never diverge), and REFUSES (`fitted=False`, `global_k=1.0`, `refusal_reason=
   "OOS_GATE_FAILED:censored_delta=...<=0"`) any group whose delta is `<= 0`. When `--validate
   CUTOFF` is supplied together with `--out`, the operator's own external cutoff split governs the
   gate (so the exact split they inspected decides what ships); otherwise the DEFAULT production
   fit (`--out` alone, no `--validate`) automatically splits each group's own events by date --
   the chronologically LAST 25% as an internal holdout, `MIN_HOLDOUT_EVENTS=10` below which the
   gate cannot be evaluated reliably and the group refuses. A group that PASSES is refit on the
   FULL population (train+holdout) before shipping -- the holdout only decides ship/no-ship, it
   does not permanently withhold data from the final numbers. Every group's `oos_gate` audit dict
   (`passed`, `censored_delta`, `normal_delta_crosscheck`, `n_holdout_events`, `method`) is stamped
   in the artifact regardless of verdict; `_meta.oos_acceptance_gate` records which split method
   governed this run.
6. **BUG FIX: `--fcst` rejected an already-formed `file:...?mode=ro` URI.** `load()` unconditionally
   wrapped its argument in `f"file:{fcst_path}?mode=ro"`, so passing an ALREADY-WRAPPED URI produced
   the double-wrapped `file:file:...?mode=ro?mode=ro`, which sqlite3 rejected with `no such access
   mode: ro?mode=ro`. New `_connect_ro()` detects a `file:` prefix and uses the caller's URI as-is;
   a plain filesystem path is wrapped exactly as before. Both forms verified against the live DB and
   covered by a regression test.
7. **Uncommitted worktree diff.** Checked at the time of the report request: `git status --short`
   showed a clean working tree (nothing uncommitted). No action was needed by the time this was
   raised; noted here for the record.

## Deliverables

1. `scripts/fit_sigma_tau_calibration.py` -- walk-forward fitter, READ-ONLY on
   `state/zeus-forecasts.db` (`mode=ro`), writes the artifact to a path given
   on the command line (operator places it; never a default under `state/`).
   `--validate CUTOFF` prints the event-weighted OOS mean-log-lik ladder
   (`k=1` vs fitted, both the primary interval-censored likelihood and the
   Normal-density cross-check) and coverage@68.3 per family/metric, fit
   strictly before cutoff / validated strictly at/after it -- run once on the
   issue clock (PRIMARY) and once on the decision clock (COMPARISON ONLY).
2. `src/data/replacement_forecast_materializer.py` -- new
   `_sigma_tau_calibration_lookup` / `_effective_sigma_tau_scale` cache
   functions (same fail-soft shape as `_replacement_sigma_scale_lookup` /
   `_effective_unit_sigma_scale`), a `_lead_target_h` / `_sigma_tau_bucket_label`
   pair for the tau computation (keyed on `request.source_cycle_time`, the
   forecast ISSUE clock -- NOT `request.computed_at`, decision time), and the
   current-shape site now calls the new lookup keyed by `(unit_family, metric,
   tau_bucket, city)` instead of the hardcoded tuple. FAIL-CLOSED TO TODAY:
   artifact absent / unparseable / family+metric group unfitted / bucket
   unfitted with no valid group global `k` => exactly `(1.0, 0.0, 0.0)`, never
   raises. The historical (`_current_shape is None`) branch is untouched --
   still `_effective_unit_sigma_scale`. Provenance gets
   `sigma_tau_artifact_hash` (identity of the artifact file actually read)
   alongside the existing `sigma_scale_k_applied` stamp (which fires only when
   the applied k != 1.0 -- unchanged trigger, now also covers the tau path's
   k).
3. Tests: loader unit tests (absent/malformed/fitted/unfitted-bucket
   inherits-global/city-shrinkage), a serving-equivalence test proving the
   historical path is byte-identical and the current-shape path with no
   artifact present is byte-identical to today, and a fitter smoke test on a
   synthetic sqlite fixture (never the live DB).
4. Registry: `architecture/test_topology.yaml` entries for the new test
   files, following the `test_trust_policy` + `test_metadata` + `categories`
   pattern from commit `9b038e7e9`.

## Acceptance

- Existing materializer test suite (`tests/test_replacement_forecast_materializer.py`,
  `tests/test_replacement_sigma_scale_f_family.py`, `tests/forecast/test_sigma_authority.py`)
  passes unchanged.
- New loader/serving/fitter tests pass.
- `--validate` against the live DB (cutoff 2026-07-21) produces a real OOS
  log-lik improvement over `k=1` for at least the C/high group (the group
  with the clearest evidence-basis signal).
- `git diff --check`, `py_compile`.

### `--validate 2026-07-21` results (live DB, read-only, 2026-07-28, POST-CORRECTION fitter)

Superseded: the pre-correction numbers previously here were fit on `computed_at`-anchored tau with
row counts and the closed-form spread-skill k; kept below in git history, not reproduced here since
the corrected run is what actually ships. Both clocks are now reported side by side to show the
tau-clock choice's effect; event counts (not row counts) gate fitting.

```
[sigma-tau] validate cutoff=2026-07-21 clock=PRIMARY(issue-clock) n_train_rows=12221 n_val_rows=7700
  C/high: n_events_train=348 n_events_val=176 global_k=1.572637 (normal_crosscheck=1.147199)
    censored  oos_mean_loglik delta:+0.19367  GATE:PASS
    normal_xc oos_mean_loglik delta:+0.52897
    coverage@68.3 (event-weighted) k=1:0.5921 fitted:0.7694
  C/low: SKIP (n_events_train=50, n_events_val=28)
  F/high: n_events_train=92 n_events_val=64 global_k=1.251403 (normal_crosscheck=0.971015)
    censored  oos_mean_loglik delta:-0.08584  GATE:REJECT
    normal_xc oos_mean_loglik delta:-0.07870
    coverage@68.3 (event-weighted) k=1:0.7212 fitted:0.8184
  F/low: SKIP (n_events_train=17, n_events_val=11)
[sigma-tau] validate cutoff=2026-07-21 clock=COMPARISON-ONLY(decision-clock) n_train_rows=12221 n_val_rows=7700
  C/high: n_events_train=348 n_events_val=176 global_k=1.572637 (normal_crosscheck=1.147199)
    censored  oos_mean_loglik delta:+0.19514  GATE:PASS
    normal_xc oos_mean_loglik delta:+0.53089
    coverage@68.3 (event-weighted) k=1:0.5921 fitted:0.7710
  C/low: SKIP (n_events_train=50, n_events_val=28)
  F/high: n_events_train=92 n_events_val=64 global_k=1.251403 (normal_crosscheck=0.971015)
    censored  oos_mean_loglik delta:-0.06981  GATE:REJECT
    normal_xc oos_mean_loglik delta:-0.06317
    coverage@68.3 (event-weighted) k=1:0.7212 fitted:0.8105
  F/low: SKIP (n_events_train=17, n_events_val=11)
```

`--out` run WITH `--validate 2026-07-21` (the operator's cutoff governs the shipped gate,
`_meta.oos_acceptance_gate = "external_validate_cutoff:2026-07-21"`):

| group | fitted | global_k | oos_gate.passed | censored_delta | refusal_reason |
|---|---|---|---|---|---|
| C/high | **True** | 1.572620 | True | +0.193669 | -- |
| C/low  | False | 1.0 | -- (never reached) | -- | `INSUFFICIENT_EVENTS:50<60` |
| F/high | **False** | 1.0 | False | -0.085838 | `OOS_GATE_FAILED:censored_delta=-0.08584<=0` |
| F/low  | False | 1.0 | -- (never reached) | -- | `INSUFFICIENT_EVENTS:17<60` |

Default production `--out` run (no `--validate`; internal 25%-holdout gate,
`_meta.oos_acceptance_gate = "internal_holdout_last_25pct_events_by_date"`, full corpus
`n_final=19921`): C/high **PASSES** (`global_k=1.572620`, `censored_delta=+0.097068`,
`n_holdout_events=131`); F/high **REFUSED** (`censored_delta=-0.033533`, `n_holdout_events=39`);
C/low and F/low both refuse on `INSUFFICIENT_EVENTS` before the gate is even reached (58 and 21
events respectively, `< MIN_GROUP_N=60` once 25% is withheld as holdout).

Notes:
- `n_events_train`/`n_events_val` collapsed dramatically versus the old row counts (e.g. C/high
  train was 9578 ROWS, now 348 unique EVENTS) -- confirming the recompute-multiplicity finding.
  `C/low` and `F/low` no longer clear `MIN_GROUP_N=60` EVENTS on this split (they did on raw rows)
  and are honestly SKIPPED rather than reporting a noisy number.
- **F/high correctly refuses.** The operator's own run of `--validate` found F/high's raw fitted
  `global_k=1.251` makes it WORSE OOS: negative censored delta, negative Normal cross-check, and
  fitted coverage (0.818) overshoots nominal 0.683 -- overcorrecting, not calibrating. The gate
  catches this automatically and ships it as `fitted=False, global_k=1.0` (neutral) instead, with
  the failing delta recorded as the refusal reason. This was previously reported (in an earlier
  version of this plan) as a "genuine OOS coverage improvement" alongside C/high -- that framing
  was wrong; withdrawn here in favor of the gate's verdict.
- **Clock choice's effect is small on THIS split**: the two clock blocks' deltas differ only in the
  4th significant digit for C/high and F/high (e.g. +0.19367 vs +0.19514), and the gate verdict
  (PASS/REJECT) is identical under both clocks here. This is expected -- most individual posteriors
  are not near a bucket boundary, so the two clocks usually agree on which bucket a row falls into;
  the correction's importance is in the WEIGHTING (not letting 247 same-cycle recomputes dominate a
  bucket's fit), which both clock choices now share equally via event weighting. The corrected tau
  clock's real benefit is preventing "look-ahead": it is not expected to produce a dramatically
  different NUMBER on a historical replay where recompute clustering happens to be roughly
  clock-symmetric; it prevents a live drift where recompute cadence itself (not new information)
  would move q.

Full production fit (no cutoff, full corpus `n_final=19921` -- the corrected pipeline no longer
drops any rows on the negative-lead check since `lead_issue_h` is never negative in this window),
POST-GATE shipped numbers: `global_k` (interval-censored PRIMARY / normal crosscheck): C/high
1.573/1.061 (**shipped**, gate PASS); C/low REFUSED (insufficient events, both pre- and post-gate);
F/high REFUSED (gate REJECT -- ships neutral 1.0, not its raw 1.133); F/low REFUSED (insufficient
events). See the two findings above (RAW-mu-forces-bias-into-k; city correction currently inert)
for why C/high's shipped primary number runs
higher than the original evidence-basis summary and why zero cities appear in any group's
`cities` map today.

## Work record

- 2026-07-28: read bakeoff.py and fit_inputs.py per the mission's evidence basis. Reproducing
  bakeoff.py's own `fit_k_by_tau` (per-observation `sqrt(mean((z/sig)^2))`) verbatim against the
  live DB gave `global_k~1.7` for C/high with NO rising-with-tau trend -- it does not match the
  cited evidence ("C,high k~1.07-1.18 rising with tau; F,high k~0.80-0.97 shrink"). Cross-checked
  fit_inputs.py's OTHER estimator (`table3_k_fit`: `std(z,ddof=1)/sqrt(mean(sig^2))`, the classical
  ensemble spread-skill ratio) against the same live rows and it reproduced the cited ranges to 3
  sig figs for all four groups (including the F/high SHRINK direction). Root cause: ~1.5% of
  C/high rows have near-zero `predictive_sigma_c`, which the per-observation ratio squares and
  averages (blowing up the mean), while the aggregate std/rms ratio is robust to that. Shipped
  `_spread_skill_k` (std/rms), not bakeoff's per-observation MLE ratio -- flagged prominently in
  both the fitter docstring and PLAN.md so a future reader doesn't "fix" it back.
- Neutralization site located by the exact quoted string at
  `src/data/replacement_forecast_materializer.py:4436` (base HEAD had it near line 4304; this
  worktree already carried +~130 lines from prior commits). `_SIGMA_SCALE_FIT_PATH` cache pattern
  read at lines 1243-1382 (untouched); new functions inserted immediately after
  `_replacement_city_candidate_lookup` (was line 1382).
- Fresh worktree was missing `config/settings.json` (gitignored) -- copied from the main checkout
  so tests could import `src.config`; this is a config file, not a `state/` DB.
- `src/state/db_writer_lock.py` allowlist required one addition for the fitter's own
  `sqlite3.connect(...?mode=ro)` call, following the EXACT precedent comment format
  `fit_sigma_scale.py` already uses in that list.
- Registry: `test_sigma_tau_calibration_serving_equivalence.py` (the safety-property antibody)
  registered in `test_trust_policy` / `categories.core_law_antibody` / `test_metadata`, matching
  the `test_replacement_fused_q_shape.py` precedent exactly. The pure-function loader test
  (`test_sigma_tau_calibration_lookup.py`) and the fitter smoke test
  (`test_fit_sigma_tau_calibration_fitter.py`) were left UNREGISTERED, matching the precedent of
  their closest sibling (`test_replacement_sigma_scale_f_family.py`, also unregistered).
  `topology_doctor.py --tests` issue count: baseline (main checkout, same commit) 511, worktree
  513 (+2, both benign `test_topology_missing` warnings for the two unregistered files); zero
  change in any `error:` category.
- Full regression: every test file importing `replacement_forecast_materializer` (42 files, 376
  cases) passes except 10 pre-existing failures verified identical on the unmodified main checkout
  at the same base commit (5 schema-migration tests expecting a retired trade-authority status
  column that is already present in the base schema, plus 5 unrelated AST/source-scan antibodies).
- `--validate 2026-07-21` against the live DB: see Acceptance section below for numbers.
- 2026-07-28 (later same day): design-review corrections 1-4 applied (tau clock -> issue time;
  event weighting; interval-censored primary likelihood; data-fence documentation). Full fitter
  rewrite (`scripts/fit_sigma_tau_calibration.py`); ONE-LINE serving-site change
  (`request.computed_at` -> `request.source_cycle_time` at the single `_lead_target_h(...)` call
  site) plus a parameter rename (`_lead_target_h(target_date, computed_at)` ->
  `_lead_target_h(target_date, issue_time)`) for clarity -- everything else in the materializer
  (the loader function, the fail-closed contract, the provenance stamp sites) is UNCHANGED, per the
  "architecture unchanged" instruction, since the artifact's served shape (a scalar k per bucket,
  a scalar c per city) did not change, only how those scalars are fitted.
  - Fixed a real bug caught by the rewrite: `validate()` originally used `df.rename(columns=
    {tau_col: "taut"})` to switch between the issue-clock and decision-clock bucket columns, which
    creates a DUPLICATE "taut" column (both source columns exist on every row from `prep()`) and
    crashes pandas with `cannot reindex on an axis with duplicate labels`. Fixed by direct
    assignment (`train["taut"] = train[tau_col]`) instead of rename.
  - Fixed a lint issue (ruff E741, ambiguous single-letter `l`) in three function signatures by
    renaming `l`/`u` bin-edge parameters to `lo`/`hi`.
  - Rewrote `tests/test_fit_sigma_tau_calibration_fitter.py`'s synthetic fixture to add a
    `recomputes_per_event` mechanism (201 same-source_cycle_time, different-computed_at posteriors
    for one event, hourly-spaced so hourly dedup does not collapse them) directly proving the
    tau-clock correction's core claim (all 201 land in ONE bucket) and the event-weighting
    correction's core claim (their combined weight is exactly 1, not 201). Updated two
    serving-equivalence test requests whose hand-picked `source_cycle_time` values needed to
    (a) avoid landing exactly on the `[36,48)`/`[48,72)` bucket boundary (48.0h, since bucketing
    switched from `computed_at` to `source_cycle_time`) and (b) satisfy the OpenMeteo anchor's
    00/06/12/18 UTC cycle-hour validation, which the original arbitrary hour choice did not.
  - Combined new+existing regression suite after the corrections: 29 new tests pass; same 10
    pre-existing (unrelated) failures as before, verified identical on the unmodified main
    checkout.
- 2026-07-28 (third pass, same day): operator ran `--validate` independently and found F/high's
  raw fit makes it OOS-worse; required a per-group acceptance gate (see "second design-review
  pass" section above), the `--fcst` URI double-wrap bug fix, and confirmation of a clean
  worktree.
  - Implemented `gate_group()` / `_censored_oos_delta()` / `_split_holdout_by_event_date()` /
    `_connect_ro()` in the fitter; wired `main()`'s `--out` path through the gate (external cutoff
    when `--validate` is also given, else an internal 25%-holdout split); `validate()` now prints
    a `GATE:PASS`/`GATE:REJECT` line per group using the SAME delta computation as the gate itself
    (shared helper, cannot diverge).
  - Debugging note: the first synthetic gate-acceptance test used the EXISTING deterministic
    (exact alternating +d/-d) multi-series fixture and failed unpredictably -- a perfectly
    deterministic residual pattern has no genuine sampling variability for a holdout split to
    reward, and non-interleaved per-series date blocks put an entire different regime into the
    holdout tail. Fixed by (a) interleaving the two Shanghai bucket series across one shared
    calendar window (`day_offset`/`day_stride` in `_add_series`) so the existing lower-level
    `fit_group()` unit tests keep a representative bucket mix, and (b) adding a SEPARATE, properly
    randomized single-bucket fixture (`z ~ N(0, (true_k*sig)^2)`, fixed seed) specifically for
    testing the gate's accept/reject behavior in isolation
    (`test_oos_gate_accepts_a_genuine_correction`, `test_oos_gate_rejects_a_spurious_correction`).
  - `git status --short` at the time of this pass was already clean (no uncommitted diff to
    `scripts/fit_sigma_tau_calibration.py` or anything else) -- the prior commit had already
    captured everything.
  - Combined new+existing regression suite after the gate: 33 new tests pass (13 fitter + 17
    loader + 3 serving-equivalence); same 5 pre-existing (unrelated) materializer-schema failures
    as before, verified identical on the unmodified main checkout. `ruff check`, `py_compile`,
    `git diff --check` all clean.

## 2026-07-28 (fourth pass, same day) DEEP-REVIEW: eight required fixes

An independent deep review (verifying the two hardest claims locally against the live DB) returned
NO-GO with eight required fixes. All eight are applied; every claim below was verified by re-running
against the live DB read-only.

- **FIX 1 (BLOCKER, fit+serve) local-date endpoint.** Tau's target-end was `target_date+1day
  00:00 UTC`; markets settle on the CITY'S LOCAL date. Corrected to
  `datetime.combine(target_date+1day, 00:00, city_tz).astimezone(UTC)`, DST-aware, via
  `config/cities.json`'s canonical IANA `timezone` field (loaded through `src.config.load_cities`,
  never re-derived). The fitter's `_local_target_end_utc` and the materializer's `_lead_target_h`
  (now taking a `city_timezone` parameter, threaded from `request.city_timezone`) compute the
  identical local-date endpoint. A dedicated antibody
  (`test_local_date_endpoint_uses_city_timezone_not_utc`) constructs a Shanghai case where the
  UTC-anchored and local-anchored cuts land in DIFFERENT buckets ([24,36) vs [12,24)) and asserts
  the LOCAL one wins.
- **FIX 2 (BLOCKER, fitter) settlement quantizer.** The universal `[v-0.5, v+0.5)` preimage
  violates `src/contracts/settlement_semantics.py`: Hong Kong's `oracle_truncate` rule has preimage
  `[v, v+1)`. The fitter now looks up each row's rounding rule via
  `SettlementSemantics.for_city(city).rounding_rule` and constructs the preimage via
  `settlement_preimage_offsets(rounding_rule, half_step=0.5)` -- the repo's own declarative source,
  never re-derived inline. `test_hong_kong_uses_asymmetric_oracle_truncate_preimage` and
  `test_non_hk_city_uses_symmetric_wmo_half_up_preimage` lock both branches.
- **FIX 3 (BLOCKER, fitter) numerical stability + fail-closed gate.** Replaced
  `norm.cdf(hi)-norm.cdf(lo)` (silently underflows to exactly 0.0, hence `log(0)=-inf`, for any
  interval ~9+ sigma from center) with a log-domain computation (`_log_interval_prob`) that picks
  the numerically stable `logcdf`/`logsf` branch per row. The `1e-12` clip is REMOVED: a
  non-finite log-probability now makes the optimizer's objective explicitly `+inf` for that scale,
  rather than masquerading as a small finite penalty. `fit_interval_censored_scale` now checks
  `res.success`, finite `res.x`/`res.fun`, and BOUNDARY PINNING (a result within `1e-4` of a search
  bound raises `FitFailure` -- the true optimum is outside the physically sane range, not that the
  bound IS the answer); every caller (`fit_k_by_tau`, `fit_city_shrinkage`, the OOS gate) converts a
  `FitFailure` into an explicit refusal (whole-group refusal for a failed GLOBAL fit; inherits
  global for a failed BUCKET fit; the city is simply omitted for a failed CITY fit) -- never a
  silently wrong k. The gate itself now REJECTS any non-finite `censored_delta` explicitly (`NaN <=
  threshold` is always `False` in Python, so the old `<= 0` check was fail-OPEN for NaN) and
  requires a PREDECLARED margin (`OOS_MARGIN_NATS = 0.01`), not merely `> 0`.
  `test_log_interval_prob_stays_finite_where_naive_underflows`,
  `test_extreme_residual_does_not_crash_or_silently_clip`,
  `test_bound_pinned_optimum_raises_fit_failure`, and
  `test_oos_gate_requires_margin_not_merely_positive` lock this.
- **FIX 4 (BLOCKER, fitter) ship train coefficients; date-blocked holdout; global-k rung.** A group
  that PASSES the gate now ships the TRAIN split's fitted coefficients UNCHANGED --
  `gate_group` no longer refits on the full population (a refit could activate a bucket/city that
  was never actually OOS-scored). The holdout split changed from per-EVENT to per-DATE
  (`_split_holdout_by_target_date`: the chronologically last 25% of WHOLE target dates) so
  same-day cross-city correlation cannot leak across the train/holdout boundary. A
  global-k-only OOS delta (`_censored_oos_delta_flat_k`: one flat k, no bucket/city indexing, vs
  k=1) is now reported in every `oos_gate` dict (`global_k_only_censored_delta`), REPORTED ONLY,
  never gated on -- shows what lead-bucket indexing buys over a single flat correction.
  `test_gate_ships_train_coefficients_unchanged_no_refit`,
  `test_split_holdout_by_target_date_never_splits_one_date_across_train_and_holdout`, and
  `test_oos_gate_reports_global_k_only_comparison_rung` lock this.
- **FIX 5 (BLOCKER, fitter) training population fence.** The query now requires
  `$.bayes_precision_fusion.current_evidence_shape` to be present (a proxy for "this posterior
  actually used the CURRENT-EVIDENCE branch"), `computed_at` strictly before the FIX-1 local
  target end (excludes retroactive/late recomputes the current-evidence path would never
  materialize), and joins `settlement_outcomes` (NOT the raw `settlements` table) with
  `authority='VERIFIED'` -- the EXACT precedent shape `scripts/fit_sigma_scale.py:134` already uses
  for calibration fitting (verified: `settlement_outcomes` and `settlements` are two DIFFERENT
  tables on the live DB with 367 value/authority mismatches between them; `settlement_outcomes` is
  what the established sibling fitter trusts). Both SELECTs now run inside one `BEGIN` read
  transaction. Every fence predicate is recorded in `_meta.population_fence`.
  `test_missing_current_evidence_shape_is_excluded`, `test_unverified_settlement_is_excluded`,
  `test_computed_at_after_local_target_end_is_fenced_out`, and
  `test_one_begin_read_transaction_covers_both_selects` lock this.
- **FIX 6 (BLOCKER, materializer) strict loader authorization.** The materializer now validates a
  TYPED schema before trusting the artifact AT ALL: `_meta.authority` must equal the exact expected
  string, `_meta.schema_version` must equal `1` (an `int`, explicitly not a `bool`),
  `_meta.tau_clock` must equal the exact serving-side clock constant (`_SIGMA_TAU_CLOCK_ID`), and
  per group `fitted is True` / `oos_gate.passed is True` must be EXACT bools (not merely truthy --
  `"fitted": "true"` as a STRING is rejected). Every k value (`global_k`, each bucket's `k`, each
  city's `c_shrunk`) must be a real, finite number (`isinstance(x, (int,float)) and not
  isinstance(x, bool)`) within `[0.25, 4.0]`; the bucket key set must match the expected 7 labels
  EXACTLY. ANY top-level deviation invalidates the WHOLE artifact; an invalid single bucket/city
  entry narrows to that scope only (inherits global_k / omitted, same fail-soft posture as before).
  The artifact is now read+validated ONCE per file generation (`_load_validated_sigma_tau_artifact`,
  an mtime-keyed module-level cache) rather than re-parsed on every lookup -- a mid-batch artifact
  swap cannot produce generation skew within one process's already-served lookups. The ENTIRE
  resolution (tau computation, local target-end, strict validation) is now wrapped in ONE
  neutralizing try/except (`_resolve_sigma_tau_calibration`), replacing the former 3-statement
  inline block at the call site, so a malformed request field can never escape as an exception.
  `tests/test_sigma_tau_calibration_lookup.py` carries 33 antibodies for this contract, including
  the exact rejection cases requested: `"fitted":"false"` string, `fitted=True` with `oos_gate`
  MISSING, a bool used as k, `k=1e100`, a wrong bucket key set, and a wrong `authority` string.
- **FIX 7 (BLOCKER, tests+materializer) equivalence contract.** `sigma_tau_artifact_hash` is now
  OMITTED from provenance entirely (via conditional dict unpacking) rather than present with a
  `null` value, when the calibration is inert -- so the "byte-identical to the code before this PR"
  claim is a literal dict-key-set equality with the pre-artifact provenance, not merely "same
  values, one extra null key". `tests/test_sigma_tau_calibration_serving_equivalence.py` was
  strengthened to compare the FULL provenance dict (every key, via `==` on the whole parsed dict,
  not selected fields) and the full `q_json`/`q_lcb_json`/`q_ucb_json` vectors; added rejection
  antibodies for an artifact missing `oos_gate` entirely and one with a mismatched `tau_clock`
  declaration.
- **FIX 8 (HIGH, fitter) URI + read-only hardening.** `_connect_ro` now parses ANY `file:` URI via
  `urllib.parse`, REJECTS an explicit non-`ro` mode (`mode=rw`/`mode=rwc`) rather than silently
  overriding it, and unconditionally sets `PRAGMA query_only=ON` on the resulting connection as
  defense-in-depth (verified: an `INSERT` against a `_connect_ro`-opened connection now raises
  `sqlite3.OperationalError` even though the underlying file itself is writable).
  `test_connect_ro_rejects_explicit_non_ro_mode` and `test_connect_ro_query_only_blocks_writes`
  lock this (the double-wrap bug itself was already fixed in the third pass above).

### Re-run `--validate 2026-07-21` after all eight fixes (live DB, read-only, 2026-07-28)

```
[sigma-tau] {'n_posteriors_read': 50188, 'n_settlements_read': 9592, 'n_joined_dedup': 19069,
             'n_dropped_negative_lead': 0, 'n_final': 19069, 'n_joined_raw': 37852,
             'n_dropped_unknown_city': 0, 'unknown_cities': [],
             'n_dropped_computed_at_after_local_target_end': 975}
[sigma-tau] validate cutoff=2026-07-21 clock=PRIMARY(issue-clock) n_train_rows=10810 n_val_rows=8259
  C/high: n_events_train=341 n_events_val=194 global_k=1.657183 (normal_crosscheck=1.254361)
    censored  oos_mean_loglik delta:+0.30448  GATE:PASS (margin required:0.01)
    normal_xc oos_mean_loglik delta:+0.48883
    global_k_only (no bucket/city indexing) oos_mean_loglik delta:+0.30618
    coverage@68.3 (event-weighted) k=1:0.5955 fitted:0.7976
  C/low: SKIP (n_events_train=42, n_events_val=31)
  F/high: n_events_train=61 n_events_val=64 global_k=1.389635 (normal_crosscheck=0.992638)
    censored  oos_mean_loglik delta:-0.11116  GATE:REJECT (margin required:0.01)
    normal_xc oos_mean_loglik delta:-0.10406
    global_k_only (no bucket/city indexing) oos_mean_loglik delta:-0.11116
    coverage@68.3 (event-weighted) k=1:0.7212 fitted:0.8296
  F/low: SKIP (n_events_train=10, n_events_val=11)
[sigma-tau] validate cutoff=2026-07-21 clock=COMPARISON-ONLY(decision-clock) ...
  C/high: ... delta:+0.30296  GATE:PASS   F/high: ... delta:-0.11831  GATE:REJECT
```

The FIX-5 population fence dropped 975 additional rows (`computed_at` after the LOCAL target end)
relative to the pre-deep-review run, and switched the settlement source from the raw `settlements`
table to `settlement_outcomes.authority='VERIFIED'` -- both tighten the training population toward
the actual current-evidence serving population. C/high still clears the gate with a LARGER margin
than before (+0.304 vs the pre-deep-review +0.194); F/high still correctly rejects. Production
`--out` run (default internal date-blocked holdout gate, full corpus `n_final=19069`): C/high SHIPS
`global_k=1.731717` (TRAIN-only coefficients, `censored_delta=+0.116`, comfortably above the 0.01
margin); F/high, C/low, F/low all refuse (`fitted=False`, neutral `k=1.0`).

### Test/verification summary after the eight fixes

- `tests/test_sigma_tau_calibration_lookup.py`: 33 passed.
- `tests/test_sigma_tau_calibration_serving_equivalence.py`: 6 passed.
- `tests/test_fit_sigma_tau_calibration_fitter.py`: 32 passed.
- Full regression (42 files importing `replacement_forecast_materializer`, 420 cases): 410 passed,
  10 pre-existing failures identical to the unmodified main checkout at base `6ab72d274`
  (5 schema-migration tests expecting a column already present in the base schema; 5 unrelated
  AST/source-scan antibodies) -- zero regressions attributable to this work.
- `ruff check`, `py_compile`, `git diff --check`: all clean.

## 2026-07-28 (fifth pass) Re-review: CONDITIONAL GO to merge dormant, NO-GO to activate -- Batch A + Batch B

Re-review verdict on the eight-fix PR: the artifact is dormant (absent from `state/`, no
auto-placement path exists) so the WIRING is safe to merge, but SIX further fixes are required
before any fitted artifact may actually be placed and take effect. Two batches, both landed in this
pass.

### Batch A -- unblock merge (bot-review threads, BUG-class doc/metadata fixes)

Five `@copilot-pull-request-reviewer` threads, all classified BUG (missing freshness-metadata
headers on the new test files and the fitter script per `architecture/naming_conventions.yaml`'s
`freshness_metadata` spec, and a stale Problem-section sentence still describing tau as
computed_at-anchored after FIX 1 switched it to the issue clock). Fixed by commit, no text replies;
threads resolved via the GraphQL `resolveReviewThread` mutation per
`architecture/agent_pr_discipline_2026_05_09.md` Principle 2.

### Batch B -- activation fixes (six items, required before any artifact may be placed)

- **B1 (BLOCKER) fit/serve parity.** Extracted `served_settlement_log_probability` in
  `src/data/replacement_forecast_materializer.py` -- the ONE function that reproduces, verbatim, the
  two transforms `_compute_posterior_payload` applies before any bin integrates: the T0-1
  remaining-window Day0 center correction (`mu -= delta` for HIGH, `mu += delta` for LOW) and the
  day0/normal dispatch (`_day0_conditioned_bin_probability` vs `bin_probability_settlement`, the
  SAME settlement integrator). The fitter now joins the causal Day0 state per row directly from the
  posterior's own provenance (`day0_conditioning.active`/`observed_extreme_c`,
  `day0_remaining_center_delta_c` -- both already stamped, no schema change needed) and scores
  Day0-active rows through this shared function (`scripts/fit_sigma_tau_calibration.py`'s new
  `_censored_log_prob`, which falls back to the existing fast vectorized `_log_interval_prob` for
  the (majority) non-Day0 rows -- Day0-active rows are a narrow lead-time slice, so the scalar path
  costs nothing at corpus scale). Locked by
  `test_censored_log_prob_day0_row_matches_served_settlement_log_probability` (bit-for-bit parity
  against the materializer's own function) and `test_censored_log_prob_day0_row_differs_from_plain_normal`
  (the day0 transform must actually change the answer, not silently degrade to plain Normal).
- **B2 (BLOCKER) model selection law made authoritative.** `gate_group` now runs a three-way
  selection: NEUTRAL if the flat global-k-only rung doesn't beat k=1 by the OOS margin; GLOBAL_K_V1
  if the full bucket+city model doesn't beat the flat model by the margin (Occam's razor by
  evidence); BUCKET_CITY_K_V1 otherwise. `_meta.model_type` is now a per-group field
  (`neutral`/`global_k_v1`/`bucket_city_k_v1`); the materializer's loader dispatches STRICTLY on it
  and rejects a group whose actual bucket/city shape doesn't match what the declaration promises (a
  `global_k_v1` group with varying buckets or non-empty cities is a shape mismatch, not data).
- **B3 (BLOCKER) composed bound.** `k_eff = bucket_k * c_shrunk` is now enforced at BOTH fitter
  emission (a city whose composed product with any bucket k would escape `[0.25, 4.0]` is rejected)
  and loader lookup (neutral, never clamped, if the composed product is out of range) -- each factor
  individually passing its own range does not guarantee the product does.
- **B4 (HIGH) generation pinning.** Investigated the live materialization batch caller
  (`replacement_forecast_live_materialization_queue.py::_run_materialization_batch`): each item runs
  as a SEPARATE SUBPROCESS, so true cycle-pinning is already achieved by process isolation in the
  live path. Hardened the mtime-based cache to also key on file size (`st_size` alongside
  `st_mtime_ns`), the practical improvement available within this module's scope; a true
  cycle-id-keyed snapshot would require threading state through the queue/batch caller, a follow-up
  if an in-process batch caller is ever introduced.
- **B5 (HIGH) inert-hash.** `sigma_tau_artifact_hash` now reaches provenance ONLY when a
  schema-valid, gate-passed group actually applies a non-neutral k (including the `k_eff == 1.0` by
  coincidence case, which still omits the hash). A rejected artifact, a missing group, or an
  unfitted group logs the rejection (`_log_sigma_tau_rejection`, best-effort, never raises) instead
  of stamping a hash.
- **B6 (HIGH) malformed-scope invalidation.** `_validate_sigma_tau_group` now invalidates the WHOLE
  group on any malformed bucket or city subtree -- only a schema-valid `fitted: false` bucket may
  legitimately inherit `global_k`. `_city_settlement_unit_from_bins` now returns `None` (never
  defaults to `"C"`) when bins are absent or malformed; verified all 6 call sites degrade safely to
  neutral/inert on `None`.
- **MEDIUM cleanups.** Artifact write now uses a unique tmp filename (`{out}.{pid}.{uuid8}.tmp`,
  never collides across concurrent runs), `allow_nan=False` (a NaN/Infinity that slipped through the
  gates now fails the write loudly instead of shipping invalid JSON), and an `fsync` before the
  atomic rename. Dedup is now keyed on INFORMATION IDENTITY -- `(source_cycle_time,
  posterior_config_hash)`, falling back to the exact `computed_at` when the hash is absent -- not a
  wall-clock hour floor: the old key never even included `source_cycle_time`, so two rows from
  DIFFERENT source cycles whose `computed_at` happened to land in the same wall hour could be wrongly
  collapsed. Both new test files registered in `architecture/test_topology.yaml`'s trusted registry
  (`test_sigma_tau_calibration_lookup.py` at the full law-antibody tier alongside its serving-
  equivalence sibling; `test_fit_sigma_tau_calibration_fitter.py` at the lighter `trusted_tests`
  tier, matching the precedent set by `test_fit_sigma_scale.py` / `test_fit_source_clock_city_weights.py`).

### Re-run `--validate 2026-07-21` after Batch B (live DB, read-only, 2026-07-28)

```
[sigma-tau] {'n_posteriors_read': 52539, 'n_settlements_read': 9610, 'n_joined_dedup': 25296,
             'n_dropped_negative_lead': 0, 'n_final': 25296, 'n_joined_raw': 39194,
             'n_dropped_unknown_city': 0, 'unknown_cities': [],
             'n_dropped_computed_at_after_local_target_end': 995}
[sigma-tau] validate cutoff=2026-07-21 clock=PRIMARY(issue-clock) n_train_rows=13491 n_val_rows=11805
  C/high: n_events_train=341 n_events_val=208 global_k=1.498959 (normal_crosscheck=1.255544)
    censored  oos_mean_loglik delta:+1.54145  GATE:PASS (margin required:0.01)
    normal_xc oos_mean_loglik delta:+0.48876
    global_k_only (no bucket/city indexing) oos_mean_loglik delta:+1.54169
    coverage@68.3 (event-weighted) k=1:0.5891 fitted:0.7597
  C/low: SKIP (n_events_train=42, n_events_val=33)
  F/high: n_events_train=61 n_events_val=65 global_k=1.338368 (normal_crosscheck=0.985888)
    censored  oos_mean_loglik delta:-0.07022  GATE:REJECT (margin required:0.01)
    normal_xc oos_mean_loglik delta:-0.09000
    global_k_only (no bucket/city indexing) oos_mean_loglik delta:-0.07022
    coverage@68.3 (event-weighted) k=1:0.7313 fitted:0.8240
  F/low: SKIP (n_events_train=10, n_events_val=12)
```

The numbers moved AGAIN, substantially, relative to the eight-fix pass (+0.304 -> +1.54145 for
C/high's censored OOS delta) -- expected, since B1 now correctly scores Day0-active rows through
the max/min absorbing transform instead of silently mis-scoring them as plain Normal against the
WRONG (pre-Day0-correction) mu; the live dataset in this window evidently carries enough Day0-active
rows for this to matter a lot, not a little. C/high's `full_delta - global_delta =
1.54145 - 1.54169 = -0.00024` does not clear the `0.01` OOS_MARGIN_NATS, so the bucket+city
structure does NOT earn its complexity -- ships as `model_type: global_k_v1`,
`global_k=1.498959`, `cities: {}`, exactly the expected shippable per B2's model-selection law.
F/high still correctly REJECTs (censored delta -0.07022, below margin); C/low and F/low still
refuse on insufficient events (42 and 10 respectively, below `MIN_GROUP_N=60`).

Regenerated the artifact (`--out`, same cutoff-governed gate) to the scratchpad -- NEVER
`state/sigma_tau_calibration.json` (activation remains a separate, operator-gated decision from
this re-fit). Verified the regenerated artifact round-trips cleanly through the materializer's OWN
strict loader (`_validate_sigma_tau_artifact` / `_validate_sigma_tau_group`): C/high validates to
`{global_k: 1.498959, buckets: {<all 7 labels>: 1.498959}, cities: {}}`, i.e. every bucket carries
the identical global k (as `global_k_v1` requires) and the composed `k_eff = 1.498959 * 1.0`
(no city correction) sits safely inside `[0.25, 4.0]`.

### Test/verification summary after Batch A + Batch B

- `tests/test_sigma_tau_calibration_lookup.py`: 35 passed (2 new: schema-valid unfitted-bucket
  inherit, absent-city-still-safe -- both B6).
- `tests/test_sigma_tau_calibration_serving_equivalence.py`: 6 passed (fixture updated for B2's
  `model_type` field).
- `tests/test_fit_sigma_tau_calibration_fitter.py`: 37 passed (4 new: pinned-bound test updated for
  the `fit_interval_censored_scale` signature change; day0-provenance-join, day0-vs-non-day0-inert,
  and day0-row-matches-served-kernel -- all B1).
- `tests/test_replacement_forecast_materializer.py`: 44 passed, 5 failed -- IDENTICAL to the
  unmodified checkout (verified via `git stash`; all 5 are schema-migration tests asserting a column
  absent from the base schema, pre-existing, unrelated to this work).
- `tests/test_topology_doctor.py`: 32 failed both WITH and WITHOUT this pass's
  `architecture/test_topology.yaml` edit (verified via an isolated single-file `git stash`) -- root
  cause is `FileNotFoundError: .agents/skills/zeus-ai-handoff/SKILL.md` missing from this worktree's
  provisioning, a pre-existing environment gap, not a topology-registry regression. Direct
  `scripts/topology_doctor.py --tests --json` invocation (the actual governance gate, not the pytest
  wrapper) shows 38 errors before AND after this pass's registry edit (zero new), warnings 475 -> 474
  (one fewer -- `test_sigma_tau_calibration_lookup.py`'s prior "no topology classification" gap is
  now closed).
- `ruff check`, `py_compile`: all clean on every touched file.
