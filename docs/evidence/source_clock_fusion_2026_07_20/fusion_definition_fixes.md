# Source-clock fusion-definition fixes + artifact hot-reload/refit pipeline

Date: 2026-07-20. Surface: per-city source-clock weight baskets
(`state/source_clock_weights/ACTIVE.json`), fit by
`scripts/fit_source_clock_city_weights.py`, served by
`src/strategy/live_inference/source_clock_city_weights.py`.

This is the WHY-of-record for commits `98fa92ca7 2fa3c978c fc2fac040 2021b8bea
93426ce95`. `fc2fac040` (observation truth) has already been mechanically
reverted twice (`6c0b9e331` original → `8df37168c` revert → `fc2fac040`
reinstate). If any of these is contested again: do not re-revert mechanically —
state the concrete distortion scenario here or raise it to the operator. The
serving artifact on disk is the product of exactly this code.

## Symptom (operator, 2026-07-20)

> per-source data shows some sources are used by nearly ALL cities — incorrect
> and does not fit the fusion definition.

The fusion definition is: **each city×metric gets its own best basket**, not a
universal global fallback. "Nearly all cities share the same 3 sources" is the
signature of cells collapsing onto the fixed `GLOBAL_CORE` basket
(`icon_global, ecmwf_ifs, ukmo_global_deterministic_10km`) — the tier reserved
for cells with < 30 paired settled dates. Too many cells were landing there.

## Three structural causes (all fixed)

1. **Greedy basket had a min-n wait-gate** (`98fa92ca7`). Basket construction
   filtered candidate models by a fixed minimum settled-n *before* the
   significance test, so a high-resolution nest (gfs_hrrr, icon_d2, icon_eu,
   ncep_nbm_conus, arome, ukmo_uk_2km, gem_hrdps_continental) with thin but
   informative history was held back until some fixed n accrued — a
   "wait N days to validate" gate, which is a forbidden proposal class. Fix:
   the guarded ADD loop is now open to ALL candidates and gated only by its own
   `2*SE` significance test (self-scaling with n); the min-n floor only picks
   the *start* model, never holds a candidate back. A thin *noise* model still
   cannot start a basket (`test_thin_noise_model_still_cannot_start_basket`),
   but a thin *informative* nest is admitted by evidence
   (`test_thin_nest_admitted_by_significance_not_min_n`).

2. **R4a previous-runs immutability key was lead-blind** (`2fa3c978c`). The
   walk-forward download skipped a (model, city, target_date) it had already
   fetched, *ignoring lead_days*. That starved the archive of the lead-0/lead-1
   rows the nests need to accumulate paired history — so nests never crossed the
   significance bar (cause 1 could not fire even once fixed). Key is now
   `(model, city, target_date, metric, lead_days)`; persist layer was already
   `INSERT OR IGNORE` UNIQUE-idempotent, so this only widens what gets fetched.

3. **Low metric had no truth to fit against** (`fc2fac040`). The fitter joined
   forecasts to `settlement_outcomes` only. Many city×date low cells have no
   venue settlement row, so the low track had almost no paired history → low
   collapsed to GLOBAL_CORE. Fix: truth is now a preference UNION —
   venue-VERIFIED `settlement_outcomes` (pref 0) wins where present, else the
   VERIFIED settlement-source `observations` value (pref 1: WU icao / HKO /
   Ogimet METAR, the physical station the venue reads). This is walk-forward and
   leak-safe: `observations` are past settled target_dates, VERIFIED authority,
   `target_date < decision_date`. It touches the training truth ONLY — the live
   decision path still requires venue authority; provisional/Day0 observations
   never enter live q. (Measured settlement-source vs venue agreement at fit
   time: high 98.8%, low 97.9%.)

### Single-family degrade (`fc2fac040`, same commit)

Some low cells have only one provider family in the archive. A basket must carry
`MIN_ENTRY_PROVIDER_FAMILIES=2` distinct families. When the fitted basket has
< 2 families it degrades to GLOBAL_CORE rather than raising — this is why a few
low cells (e.g. Ankara/low) correctly show the 3-global basket.

## Result — live 20260720 artifact (108 city×metric cells)

| metric | CITY_SPECIFIC | REGION_POOLED | GLOBAL_CORE |
|--------|---------------|---------------|-------------|
| high   | 51            | 0             | 3           |
| low    | 49            | 1             | 4           |

- Low `CITY_SPECIFIC` 7 → **49** (the fix's headline effect).
- Cells carrying ≥1 regional/hi-res nest: **high 21, low 15** (was ~0).
- Only **8 / 108 (7%)** cells remain on the shared global basket — the fusion
  definition is now satisfied: sources are per-city, not universal.

### Accepted tradeoff (obs-truth)

Fitting low on the observation-truth fallback moved mean cell MAE by roughly
+0.025 °C at fit time (a minority of cells worse, a minority better). Accepted:
the alternative for ~42 newly-CITY_SPECIFIC low cells was the coarse 3-global
basket with zero per-city specificity. Per-city fusion with a small mean-MAE
cost dominates a universal global fallback. Flag for later: high ~1.2% / low
~2.1% venue-vs-observation disagreement samples may hide settlement
dispute/revision cases — worth a targeted audit, not a blocker.

## Artifact pipeline — hot-reload + weekly refit (`2021b8bea`, `93426ce95`)

Two latent gaps surfaced while landing the above, and had to be fixed together:

- **Loaders never hot-reloaded** (`2021b8bea`). All four fitted serving
  artifacts (source_clock_weights, staleness_variance, shape_age_sigma,
  ens_member_dependence) cached `_load_active_artifact` keyed on directory path
  only — a refit that rewrites ACTIVE.json was invisible to long-lived daemons
  until restart. Cache key now includes the pointer's `st_mtime_ns`: rewrite →
  reload, unchanged → pure cache hit (one `stat` per call). A never-reloading
  walk-forward artifact is the exact frozen-CSV accident class it replaces.

- **Refit was never scheduled** (`93426ce95`). The "weekly cron candidate" lived
  only in a docstring; 20260717/19/20 artifacts were all produced by hand, and
  staleness/shape-age/ens-dependence were frozen at 20260717. New ingest job
  `ingest_artifact_refit` (cron Mon 06:00 UTC, after the weekend settlement
  grade) runs the four fitters as fail-soft subprocesses. Registered in
  `src/data/source_job_registry.py` (governance-checked). With hot-reload this
  is fully autonomous: Monday refit → pointer mtime changes → next serving call
  uses the new basket, no restart.

## Verified live (post-restart)

Daemons restarted 2026-07-20 07:18 (coworker deploy), after all five commits
(last 07:12). Confirmed on the running code:

- boot registry check: 34 jobs, `ingest_artifact_refit` scheduled = True (run on
  the current working tree, i.e. the version the daemon loaded; coworker's
  concurrent HKO-poller edit to `ingest_main.py` is additive and does not touch
  the refit job).
- `forecast-live` serves the 20260720 obs-truth baskets
  (e.g. Ankara/high = `ecmwf_ifs + icon_eu`).
- ACTIVE.json → `city_weights_20260720.json`, sha b88bb61f…; disk and code agree.

First *autonomous* refit proof is next Monday 06:00 UTC (today's daemon booted
after 06:00 UTC, so today's artifact remains the manual one — the wiring, not a
catch-up run, is what today proves).

## Not done here (main-line, pending operator direction)

- Release-timing probability dynamics: how each source's publish time drives
  system-wide swings and how the system *should* damp them. Fail-open on a
  missing/stale source is confirmed (a stalled source delays only its dependent
  scopes per GOAL.md failure isolation); the deeper damping design is not built.
- Isolation criteria: when to isolate a city/position on stale/missing/low-
  confidence rather than trade on an expired probability — criteria not yet
  formalized.
- market_fusion Tier-2 (retire the dormant LEGACY posterior-blend branch):
  separate governance-touching pass.
