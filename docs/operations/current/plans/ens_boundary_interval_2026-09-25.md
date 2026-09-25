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

`current_evidence_ensemble_eligibility_sql(alias)` = exact point row
(contributes=1, boundary_ambiguous=0, causality OK, FULLY_INSIDE) OR interval row
(INTERVAL_CENSORED status, payload causality OK: `causality_status IN ('OK',
'REJECTED_BOUNDARY_AMBIGUOUS')`, the latter being Law 1's training label on a
causally-OK LOW row). The replacement chain calls it at every site:

1. materializer current-evidence selector (`_current_evidence_snapshot_row`);
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

These are the per-bin UCB floors that keep q_ucb honest. The lower bounds travel as
`members_c` and the upper bounds as `member_upper_c` (None = exact points, which
keeps today's path):

- Member hit counts → plausibility counts: #members whose interval intersects the bin
  preimage. For any consistent x, `hits(x) ≤ plausibility`, and the Clopper-Pearson UCB is
  increasing in k, so the sample floor dominates every assignment.
- Cantelli moment term uses the served σ (the supremum above), and it is increasing in σ.
- ENS-center component term: sup over the feasible ENS center `[m_l, m_u]` (exact: the
  bin mass is unimodal in the center, so candidates {m_l, m_u, clamp(bin midpoint)}
  suffice), at the spread `w` of the assignment farthest from the served center.
  This is a supremum over centers, not over every (center, spread) pair. The
  plausibility term is the rigorous finite-sample floor.
- n, zero-hit floor and n_eff depend only on n = 51 and are unchanged.

## Validation (read-only, before wiring)

See §Validation results below (filled after the run). The data limits are:

- LOW endpoints are hash-only in the DB, and the raw extraction JSON (retention) exists
  only for issue 09-25T00Z, whose targets are not settled yet. LOW therefore cannot
  be validated on settled history now.
- HIGH certificates (issue ≥ 09-21T12Z) persist every member's inner/boundary maxima,
  so HIGH interval σ is reconstructable on settled targets 09-21..09-24:
  20 families have ≥ 1 boundary-only cycle.

## Predeclared live check (for what the data cannot support now)

After deploy, every interval-admitted posterior is identified by
`current_evidence_shape.interval_censored_member_count > 0`. On settled outcomes,
split by metric, graded on the served `N(μ, σ)`: central-90% coverage and mean z². The
check runs once n ≥ 30 families per metric. Pass if the Wilson 95% lower bound of
90%-coverage ≥ 0.80. Fail means the widening is insufficient or the center is biased,
and the operator gets a report, not an automatic knob.

## Files, tests, rollback

Recorded at the end of the implementation. Rollback = revert the branch commits. Rows
already written with the new status stay inert under the old code, because every old
reader requires FULLY_INSIDE/contributes=1.
