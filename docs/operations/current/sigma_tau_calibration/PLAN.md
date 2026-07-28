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
shape). `tau = lead_target_h` = hours from `computed_at` to the END of
`target_date` UTC (`target_date + 1 day 00:00 UTC`).

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
  at the same base commit (5 schema-migration tests expecting a `trade_authority_status` column
  that is already present in the base schema, plus 5 unrelated AST/source-scan antibodies).
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
