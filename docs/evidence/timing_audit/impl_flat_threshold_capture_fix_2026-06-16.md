# Implementation: coverage-aware capture self-healing gate (flat-threshold fix) — 2026-06-16

```
# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: docs/evidence/timing_audit/capture_reactor_stall_rootcause_2026-06-16.md
#   (verified root cause, Fix 1 PRIMARY/CODE); BAYES_PRECISION_FUSION_SPEC §6 F1
#   (q-path consumes the persisted single_runs capture); operator no-caps law
#   (memory: no-caps-no-overengineering-2026-06-12 — fixpoint detector, not a cap).
```

Worktree implementation (RELATIVE paths). No live `src/` tree edited. `git`-free verification.

## Scope

ONE bug from the root-cause report: the capture self-healing re-run gate
`_extras_cycle_incomplete` in `src/data/replacement_forecast_production.py` was
coverage-blind, degrading ~42% (06-16: 56%) of LIVE forecast posteriors to the
known-bad legacy `aifs_member_votes_soft_anchor` q-shape. Fix 2 (throughput) and
Fix 3 (FAILED-flag observability) from the report are OUT of scope here.

## Old vs new gate logic

### OLD (deleted) — flat row-count, coverage-blind
`src/data/replacement_forecast_production.py:340-374`:
```python
_EXTRAS_COMPLETE_THRESHOLD = 200
cycle_iso = cycle.astimezone(utc).isoformat()
count = SELECT COUNT(*) FROM raw_model_forecasts WHERE source_cycle_time = ?
return count < _EXTRAS_COMPLETE_THRESHOLD
```
Failure mode (verified in the root-cause report): the near-day (lead=0) leg alone
is ~382 rows for one cycle, so `count` exceeds 200 and the gate returns
**complete** (skip the fan-out) while the lead+1/lead+2 city scopes are still
un-captured. Those scopes are stranded → the q-path
(`replacement_forecast_materializer.py:966-975` →
`read_current_instrument_values`) finds no current `single_runs` row → returns
`None` → `q_shape` falls back to legacy `aifs_member_votes_soft_anchor`.
`EXTRAS_CURRENT_CYCLE_COMPLETE_SKIPPED` fired 318×; lead+1 was 93% STALE.

### NEW — per-(city, metric, target_date) coverage probe + per-cycle fixpoint latch
`_EXTRAS_COMPLETE_THRESHOLD` and the flat-count logic are DELETED. Three pieces:

1. `_extras_coverage_missing(cfg, cycle) -> (missing_scopes, planned_count) | None`
   - `need` = `{(row.city, row.temperature_metric, row.target_date)}` from
     `build_replacement_forecast_current_target_plan(forecast_db)` — the SAME plan
     the fan-out builds its download targets from
     (`_download_bayes_precision_fusion_extra_raw_inputs_if_needed:284,312`).
   - `have` = `SELECT DISTINCT city, metric, target_date FROM raw_model_forecasts
     WHERE source_cycle_time = ? AND endpoint = 'single_runs'` — the EXACT
     endpoint/natural-key the q-path reads. A `previous_runs` substitute is a q
     FALLBACK, not cycle completeness, so it is deliberately NOT counted.
   - Returns `(need - have, len(need))`; `None` on any probe error; `(∅, 0)` when
     the plan is empty (no open markets).

2. `_extras_cycle_incomplete(cfg, cycle=None) -> bool` (the gate)
   - `None` cycle / probe error → `True` (fail-open, unchanged contract).
   - `not missing` → `False` (complete; **terminates** — all planned scopes captured).
   - `missing` and **fixpoint latched** → `False` (complete-with-gap, logged).
   - `missing` and not latched → `True` (re-run the fan-out, logged with the gap).

3. Per-cycle fixpoint latch (the explicit unservable-case handler), keyed on the
   cycle ISO, stored on the existing `bayes_precision_fusion_capture`
   scheduler-health entry's `business_liveness` (NO new table/daemon/marker-row):
   - `_extras_fixpoint_latched(cycle)` reads `extras_fixpoint_latched` AND
     `extras_fixpoint_cycle == cycle_iso`.
   - `_record_extras_fixpoint(cfg, cycle, written)` LATCHES iff `written == 0` AND
     coverage still incomplete (the residual is unservable for this cycle right
     now); UN-LATCHES on any progress (`written > 0`).
   - Call site (`:694-712`): the probe cycle is resolved ONCE and reused for both
     the gate and the recorder (race-free). The recorder fires ONLY on status
     `BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED` — a fail-soft skip
     (`FAILSOFT_SKIPPED`/`NO_TARGETS`/`UNRESOLVED_SKIP`) is TRANSIENT, carries no
     `written_row_count`, and must NOT latch (else it would wrongly suppress
     self-healing). This is the "unservable → complete-with-gap" vs "transient
     error → keep re-running" distinction the brief required.

## Termination argument (the loop provably halts — no infinite re-run)

Two independent bounds; the fixpoint is the primary, cycle rollover makes
complete-with-gap safe.

**A. Per-cycle fixpoint (handles the genuinely-unservable scope).**
The downloader is per-row idempotent
(`bayes_precision_fusion_download.py:918-957`), so for a FIXED cycle C the covered
set is monotone non-decreasing and bounded above by the finite (planned-scope ×
servable-model) product. `_record_extras_fixpoint` watches the pass's own
`written_row_count`: a pass that lands **zero** new rows while still incomplete
means the residual is unservable for C right now (Open-Meteo beyond its publish
horizon, a city/model it will not serve this cycle, or a statically-excluded model
the downloader never even requests) → it LATCHES, and the gate then returns
`False`. So for a fixed C the fan-out runs at most until the covered count stops
increasing — a strictly monotone bounded sequence → **finite re-runs**. Progress
(`written > 0`) un-latches, so a slowly-arriving servable scope keeps healing.

**B. Cross-cycle rollover (makes complete-with-gap safe).**
The probe is keyed to `_probe_resolved_available_cycle()` — the newest
PAIR-COMPLETE cycle on the fixed 00/06/12/18Z grid
(`replacement_cycle_availability.py:47`), monotone in publish order. Within ~6h the
next cycle publishes, the probe advances to C′, the latch (keyed on C's ISO) goes
stale, and C′ is healed from scratch. A permanently-unservable scope thus halts
looping for C but never poisons C+1.

**INVARIANT.** For any cycle C the fan-out runs on finitely many ticks — bounded by
`min(ticks-until-covered-count-stops-rising, C's ~6h active-probe window)` — and
the unservable residual is surfaced (logged), never silently looped on. (Design
cross-checked by the architect agent, 2026-06-16; option (d) no-progress fixpoint
adopted over an attempt-budget cap, which the operator no-caps law forbids.)

## Files changed

- `src/data/replacement_forecast_production.py`
  - DELETED `_EXTRAS_COMPLETE_THRESHOLD` + the flat `COUNT(*) < 200` body.
  - ADDED `_extras_coverage_missing`, `_extras_fixpoint_latched`,
    `_record_extras_fixpoint`, `_EXTRAS_FIXPOINT_HEALTH_JOB`.
  - REWROTE `_extras_cycle_incomplete(cfg, cycle=None)` (coverage-aware + latch).
  - UPDATED the extras call site in `_replacement_cycle_availability_poll_if_needed`
    (`:694-712`): resolve cycle once, thread to gate + recorder, record fixpoint
    only on the DOWNLOADED status.
- `tests/test_replacement_forecast_extras_coverage_gate.py` (NEW, 10 tests).

## Verification (fresh output)

- `python3 -m py_compile src/data/replacement_forecast_production.py` → OK.
- `grep _EXTRAS_COMPLETE_THRESHOLD src/ tests/` → GONE (0 occurrences).
- New test file: **10 passed** (`pytest tests/test_replacement_forecast_extras_coverage_gate.py`):
  - (a) `test_full_near_day_missing_lead1_is_incomplete` — near-day leg seeded with
    **240 rows (> the old 200 floor)** yet lead+1 absent → gate INCOMPLETE
    (regression guard: the deleted flat gate would have wrongly skipped here).
  - (b) `test_all_planned_scopes_captured_is_complete` + `test_no_planned_scopes_is_complete`
    → COMPLETE (terminates).
  - (c) `test_unservable_residual_terminates_via_fixpoint` — 0-progress pass latches
    → next tick SKIPS (loop terminates); plus `test_progress_unlatches_so_servable_data_keeps_healing`,
    `test_latch_auto_clears_when_cycle_advances`, `test_probe_error_fails_open`,
    `test_failsoft_skip_does_not_latch`.
  - END-TO-END through the real poll: `test_callsite_downloaded_zero_progress_latches_then_skips`
    (DOWNLOADED+0 → latch → skip) and `test_callsite_failsoft_does_not_latch`
    (transient → no latch → re-run).
- Full causation-relevant suite (the 13 files that import the edited module + the
  symptom-path/plan/download tests): **141 passed, 2 failed**.
  - The 2 failures are `tests/data/test_replacement_cycle_availability.py::TestPollFetchDecision::{test_fetches_each_published_leg_it_lacks, test_unknown_holdings_fail_open_to_fetch}`.
    **PRE-EXISTING and unrelated** — proven by restoring the function-body-only fix
    (NO call-site change) and observing the identical 2 failures; the leg-fetch
    decision they assert runs entirely BEFORE the edited extras block. They stem
    from a probe-name/signature drift in `replacement_cycle_availability` test
    stubs, not this fix.
- Broader `-k "replacement_forecast or bayes_precision_fusion or extras or
  current_target_plan"` sweep: 53 pre-existing failures, all in files that do NOT
  import `replacement_forecast_production` and do NOT reference any changed symbol
  (e.g. `ReplacementForecastPosteriorBundle.__init__()` signature drift in
  `test_replacement_forecast_veto.py`, live-authority evidence machinery in
  `test_replacement_forecast_runtime_policy.py`). Causally isolated from this fix.

## Provenance verdict

`_extras_cycle_incomplete` (created 2026-06-13, R4b): **STALE_REWRITE** — the flat
row-count gate was correct only under the false assumption that total cycle rows
imply per-scope coverage. Rewritten to the coverage-aware + fixpoint form above;
header `Last reused/audited` unchanged by the linter but the function is now
authored under the 2026-06-16 root-cause regime.
