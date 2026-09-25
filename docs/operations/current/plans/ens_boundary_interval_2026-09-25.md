# ENS boundary-interval admission — 2026-09-25

Status: DESIGN + IMPLEMENTATION (branch `claude/agent-ad6df475aede285df`, base `origin/live` 14244e742).
Authority: `docs/authority/statistical_calibration_addendum_2026-06-13.md` D2 (CAR
interval-widening preferred; exclusion only when the ambiguity set is unrecoverable
or directional; conservatism from interval WIDTH, no knob).

## Problem (measured read-only 2026-09-25)

ECMWF Open Data ENS mn2t3/mx2t3 are 3-hour aggregates on a 3-hour step grid.
A city's local day rarely aligns with it, so the first and last windows straddle
local midnight. Per member the local-day extreme is then only known to lie in an
interval:

- LOW: `[boundary_min, inner_min]` when `boundary_min < inner_min`, else exact `inner_min`.
- HIGH: `[inner_max, max(inner_max, boundary_max)]`.

Today such rows are excluded: LOW majority-ambiguous rows are
`REJECTED_BOUNDARY_AMBIGUOUS`/contributes=0, and HIGH rows whose certificate fails only
on `boundary_can_exceed_inner` are UNKNOWN/contributes=0. A family with no eligible
cycle never gets a posterior. A family whose later cycles are all excluded loses
readiness 30 h after its last eligible cycle (`replacement_readiness_expires_at`), so
it expires too (Milan/Miami/Chicago LOW 09-25, Singapore HIGH 09-25). The stale-shape
branch does not rescue either case: a stale shape has no live authority
(`_current_evidence_shape_has_probability_authority` requires `shape_lag_hours == 0`).
Census: 27 dark venue families across 09-25..27.

## Design

### D-1. What becomes admissible (ingest, `scripts/ingest_grib_to_snapshots.py`)

A row is *interval-censored* when every one of the 51 members has a finite, ordered
interval bound and the native windows cover the whole local day:

- HIGH: the boundary certificate's only failure reason is `boundary_can_exceed_inner`.
  Every member then has validated native windows, the clipped union covers the day
  (no `native_interval_gap`), and inner/boundary aggregates match their windows.
- LOW (Open Data): the snapshot is majority-ambiguous (today's rejection), every member's
  interval evidence is EXACT or INTERVAL (no INVALID), `_low_native_windows_cover_day`
  holds, and payload causality is OK.

Such a row is written with `forecast_window_attribution_status =
'INTERVAL_CENSORED_TARGET_LOCAL_DAY'`, window = the local day, `contributes_to_target_extrema = 0`,
block reason `boundary_interval_censored_members`, and the 51 bounds in
`provenance_json.member_interval_bounds = {revision, unit, bounds: [[lo, hi] by member id]}`
(native unit). `members_json`, `boundary_ambiguous`, `causality_status` and
`training_allowed` are written exactly as today.

No schema change: the attribution column has no CHECK constraint and the bounds live in
the existing `provenance_json` (FORECAST_CLASS table `ensemble_snapshots`; K1 split and
`db_table_ownership.yaml` untouched).

Deliberately unchanged:

- (a) `native_interval_gap` rows (issue after local-day start) stay ineligible. The
  elapsed part of the day is invisible to that run, so the HIGH upper bound is +inf
  (unrecoverable, D2 fallback). The observed elapsed extreme belongs to the Day0 lane. A
  family then serves the newest cycle issued before local-day start. That cycle is now
  admissible, and its readiness (cycle + 30 h) covers the whole local day for every
  offset because a pre-start cycle is at most 6 h before the start.
- LOW minority-ambiguous rows (1..25 interval members) keep today's FULLY_INSIDE
  drop-member treatment, so every currently served family is byte-identical. Unifying
  them onto the interval law is a follow-up. It changes served q on live families and
  needs its own settled check. That exclusion is D2's biased fallback, so the gap is
  recorded here rather than hidden.
- Old rows written before this change carry no `member_interval_bounds` and keep their
  old status, so they fail closed exactly as today. A LOW row's endpoints cannot be
  recovered from the DB (hash only); new ingests (every 6 h) produce admissible rows.

### D-2. Leakage law

`contributes_to_target_extrema` stays 0 on interval rows. Every point-extrema reader
requires contributes=1 and therefore never sees them: executable reader, OpenData
ingest selector, event-reactor canonical path, evaluator DT7, fact revocation,
calibration and bias repos. No boundary value, endpoint or midpoint is ever
persisted or read as a point daily extreme. `member_interval_bounds` is read by exactly
one consumer, the current-evidence shape reader.

### D-3. One shared admission predicate (`src/data/forecast_extrema_authority.py`)

`current_evidence_ensemble_eligibility_sql(alias)` = `exact_ensemble_eligibility_sql`
(contributes=1, boundary_ambiguous=0, causality OK, FULLY_INSIDE) OR
`interval_ensemble_eligibility_sql` (INTERVAL_CENSORED status, payload causality OK:
`causality_status IN ('OK', 'REJECTED_BOUNDARY_AMBIGUOUS')`, the latter being Law 1's
training label on a causally-OK LOW row). The status is the admission key because
its single writer sets it and persists the bounds from one condition. The shape
reader re-validates the bounds (revision, 51 finite ordered pairs) and returns no
shape, never a point fallback, on anything else. A bounds-revision bump must
re-classify old rows. The replacement chain calls the predicate at every site:

1. materializer current-evidence selector (`_current_evidence_snapshot_row`): one
   seek per class, each on its own partial frontier index (exact: the existing
   `..._exact_frontier`/`..._casefold_frontier`; interval: new
   `..._interval_frontier`), then the newer row by the same ORDER BY key. Interval
   rows are exact-city only, like the HWM. The new index's one-time build scans the
   table: 13 s measured read-only on the live DB, 0 matching rows today.
2. HWM `_latest_eligible_ensemble_input_mark` (seed discovery, cycle trigger, lag reason);
3. cycle-advance `_superseded_baseline_seed_file`;
4. forecast-live ENS-commit wake `_enqueue_committed_opendata_cycle_advance_reseeds`.

Coverage (`src/data/ecmwf_open_data.py`) marks an interval row COMPLETE/LIVE_ELIGIBLE
under the same class, counting members with valid bounds as observed. An antibody test
fails if any of these sites stops calling the shared predicate. The attribution status
is a new value, not a relaxation of FULLY_INSIDE for point rows.

### D-4. Conservative second-moment shape (derivation)

Notation: members `x_i ∈ [l_i, u_i]`, `i = 1..n`, provider center `μ`, provider
between-spread `b`, effective provider count `n_eff`. Today's definitions for a point
assignment `x` are:

    w(x)² = (1/n) Σ (x_i − x̄)²          (within)
    d(x)  = x̄ − μ                        (ENS-center delta)
    σ(x)² = w² + b² + d²                  (predictive)
    c(x)² = w²/n + b²/n_eff + d²          (center sigma)

**Predictive sigma: exact supremum.** Bias-variance gives
`w² + d² = (1/n) Σ (x_i − μ)² =: S(x)`, which is separable in the members, so

    sup_x σ(x)² = b² + (1/n) Σ_i max((l_i − μ)², (u_i − μ)²),

attained at the consistent assignment `x*_i` = the endpoint farther from `μ`.
The served shape is the shape of `x*`, with `w = w(x*)`, `d = d(x*)` and
`σ = hypot(w, b, d) = sup σ`. It never understates σ under any consistent assignment,
and equality is attained. There is no midpoint and no tuning knob. The widening is
exactly the interval width's effect.

**Center sigma: upper bound.** `w²/n + d² = S/n + (1 − 1/n) d²`. `d` is linear in x
with range `[m_l − μ, m_u − μ]` (m = mean of the lower/upper bounds), so

    c² ≤ S_max/n + (1 − 1/n)·max((m_l − μ)², (m_u − μ)²) + b²/n_eff,

which is ≥ c(x)² for every consistent x (a sum of maxima bounds the max of a sum).

Point members (every `l_i = u_i`) never enter this branch. The exact path is the
unchanged code, byte-identical by construction and pinned by a test. The semantics
revision (`ensemble_center_scenarios_v4`) is unchanged, because the probability law
`N(μ, σ)` with `σ = hypot(w, b, d)` over a consistent member assignment is unchanged.
Interval shapes add `interval_censored_member_count` and hash the bounds in
`member_values_hash`, so their identity differs from any point shape.

### D-5. Finite-evidence consumers (materializer, ~5066-5230, ~6340-6800)

These are the per-bin UCB floors that keep q_ucb honest. The shape carries
`member_bounds_c` beside the witness `members_c`. The override and posterior compute
thread both, and None keeps today's exact path:

- Member hit counts become plausibility counts: #members whose interval meets the bin
  preimage. For any consistent x, `hits(x) ≤ plausibility`, and the Clopper-Pearson
  UCB is increasing in k, so the sample floor dominates every assignment. Persisted
  `finite_evidence_member_hits_by_bin` reports these plausibility counts.
- The Cantelli moment term uses the served σ, the supremum above, and it is
  increasing in σ.
- The ENS-center component term is evaluated at the witness mean, which is a
  consistent scenario, with the witness within-spread. This is NOT a supremum over
  all feasible ENS centers. A range version was tried and dropped: on the cases
  constructed here, the sample and moment floors dominated it, so a mutation test
  could not distinguish it. It was not proven redundant in general, so the machinery
  was cut rather than kept on hope. The plausibility term is the rigorous
  finite-sample floor.
- n, the zero-hit floor and n_eff depend only on n = 51, so they are unchanged.

## Validation (read-only, before wiring)

The data has these limits:

- LOW endpoints are hash-only in the DB. The raw extraction JSON that survived
  retention covers only issue 09-25T00Z, whose targets are not settled yet. LOW
  therefore cannot be validated on settled history now.
- HIGH certificates (issue ≥ 09-21T12Z) persist every member's inner and boundary
  maxima, so HIGH interval σ is reconstructable on settled targets 09-21..09-24.

HIGH results, settled VERIFIED outcomes 09-21..09-24, graded on the served
`N(μ + center_debias, σ)`. Wilson 95% intervals are in brackets. Rows are cycles, so
rows within a family are correlated.

- Pipeline check: σ reconstructed from certificate members equals the served σ on 505
  served EXACT snapshots, max |diff| = 8.9e-16.
- Served exact rows (baseline), n=505: 90%-coverage 486/505 [0.94, 0.98];
  50%-coverage 339/505 [0.63, 0.71]; mean z² 0.80. One row per family (n=115):
  cov90 113/115 [0.94, 1.00]; cov50 68/115 [0.50, 0.68]; mean z² 0.64.
- Interval-class rows (reason `boundary_can_exceed_inner` only): 59 rows, 20 families,
  scored with center and between taken from the nearest served cycle of the same family.
  - σ_sup (this design): cov90 57/59 [0.88, 0.99]; cov50 37/59 [0.50, 0.74]; mean z² 0.64.
  - σ from inner maxima only (the leaky alternative): identical to 2 decimals.
  - Why they match: the median interval-member count per row is 3/51, the median mean
    width is 0.018 °C, and the median σ_sup/σ_inner is 1.000 (max 1.208).
  - One row per family (n=20): cov90 19/20 [0.76, 0.99].
  - By UTC offset: E(>+3) n=41, cov90 41/41 [0.91, 1.00], mean z² 0.23; W(<−3) n=14,
    cov90 12/14 [0.60, 0.96], mean z² 1.66; C n=4, uninformative.
- Verdict: on settled HIGH the interval rows are indistinguishable from exact rows, and
  the design neither understates nor visibly overstates. The numbers are too few to
  resolve W-band under-coverage from chance (n=14; the interval spans 0.80).

Sizing on currently dark rows (not graded). The center proxy is the mean of interval
midpoints, so these ratios measure sizing only, not calibration.

- HIGH targets 09-25..27 (DB certificates, 234 rows): spread ratio σ_sup/σ_min median
  1.025, p90 1.119. Jinan/Qingdao/Zhengzhou 09-25 have 5–35 interval members with max
  widths of 0.4–1.2 °C.
- LOW admissible rows from the 09-25T00Z raw snapshot (13 rows, e.g. Seattle, SF,
  Chicago, Miami 09-25, Beijing 09-27, Warsaw 09-26): median width 0.1–1.5 °C. The
  ensemble spread about the midpoint center rises from 0.3–0.6 °C (inner-only std) to
  0.5–1.3 °C. This widening is material: LOW interval families get visibly wider q,
  which is the honest price of the unseen boundary hours.
- Excluded as intended: SF/Seattle 09-28 (issue after local-day start would need
  steps beyond the horizon; `_low_native_windows_cover_day` false, width up to 7 °C).
  Every 09-25T00Z lead-0 HIGH row has `native_interval_gap`.

Dry run of the new classifier over all 478 snapshotted raw payloads (no DB): HIGH has
117 exact, 7 interval and 115 unknown rows; LOW has 111 exact, 13 interval, 17
ambiguous and 98 unknown. Every interval row has contributes=0 and 51 persisted
bounds, and no exact row changed class.

## Predeclared live check (for what the data cannot support now)

After deploy, every interval-admitted posterior is identified by
`current_evidence_shape.interval_censored_member_count > 0`. On settled outcomes,
split by metric, graded on the served `N(μ, σ)`: central-90% coverage and mean z². The
check runs once n ≥ 30 families per metric. Pass if the Wilson 95% lower bound of
90%-coverage ≥ 0.80. Fail means the widening is insufficient or the center is biased,
and the operator gets a report, not an automatic knob.

## Files, tests, rollback

- `src/data/forecast_extrema_authority.py`: status and revision constants,
  `exact_ensemble_eligibility_sql`, `current_evidence_ensemble_eligibility_sql`,
  `member_interval_bounds_from_row`.
- `scripts/ingest_grib_to_snapshots.py`: `_member_interval_bounds`, interval
  classification in `_contract_evidence_fields`, and `member_interval_bounds` in
  `_provenance_json`.
- `src/data/ecmwf_open_data.py`: coverage readiness and the run-level member count for
  interval rows.
- `src/data/replacement_forecast_materializer.py`: selector (exact city: shared
  predicate; casefold fallback and partial index: exact-only), bounds on
  `CurrentEvidenceSnapshotIdentity`, `_interval_censored_evidence_shape`, and bounds
  threaded to the hit counts, tail floors and bootstrap stress.
- `src/data/replacement_input_hwm.py`, `src/data/replacement_cycle_advance_trigger.py`,
  `src/ingest/forecast_live_daemon.py`: call the shared predicate.
- `tests/test_ens_boundary_interval_admission.py` (registered in
  `architecture/test_topology.yaml`).

Out of the replacement chain and deliberately unchanged: the executable reader,
OpenData ingest selector, event-reactor canonical path, evaluator DT7, fact
revocation, calibration and bias repos. They are point-extrema readers, and interval
rows are invisible to them through contributes=0. The live FSR lane is
posterior-backed, so ENS coverage only gates admission.

Rollback: revert the branch commits. Rows already written with the new status stay
inert under the old code, because every old reader requires FULLY_INSIDE/contributes=1.
Existing old rows are never rewritten, so there is no migration to undo.

Next actions:
1. Deploy (operator).
2. Watch the first interval posteriors: `interval_censored_member_count > 0` in
   `current_evidence_shape`.
3. Run the predeclared check once n ≥ 30 per metric.
4. Decide separately whether LOW minority rows move to the interval law.
