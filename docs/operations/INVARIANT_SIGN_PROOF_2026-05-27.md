# Invariant audit: sign / window / transport (FT-ship hierarchical bias)

**Date:** 2026-05-27
**Author:** Executor (a687f88888000c77e)
**Authority:** Operator critique 2026-05-27 — "current rebuild rows do not pass mathematical invariant audit; SAFE/HOLD tier cannot be production gate; must prove sign + window + transport invariants by fixture test before any further work on bias correction."
**Brief:** `/Users/leofitz/.claude/jobs/866db2ea/EXECUTOR_BRIEF_INVARIANT_AUDIT.md`
**Tests:** `tests/test_invariant_sign_convention.py`
**Branch:** `feat/ft-ship-invariant-audit`

This document records the fixture inputs, run outputs, and verdict for three
end-to-end invariant proofs over the production producer + writer + reader +
applicator chain. NO mocks of any function under test; in-memory sqlite per test.

Code surfaces audited:

| Role | File | Line |
|---|---|---|
| Producer (script call site) | `scripts/fit_full_transport_error_models.py` | 237 |
| Producer (capstone fn) | `src/calibration/ens_error_model.py::fit_city_predictive_error` | 181 |
| Loader (residuals) | `src/calibration/ens_bias_repo.py::load_bucket_residuals` | 134 |
| Loader (cycle selection) | `src/calibration/ens_bias_repo.py` | 202–236 |
| Writer | `src/calibration/ens_bias_repo.py::write_bias_model` | 252 |
| Reader | `src/calibration/ens_bias_repo.py::read_bias_model` | 362 |
| Applicator (offline) | `src/calibration/ens_bias_model.py::apply_bias_to_extrema` | 234 |
| Applicator (live wiring) | `src/calibration/ens_error_model.py::p_raw_vector_with_error_model` | 119 |
| Transport gate | `src/calibration/ens_error_model.py` (`MIN_PAIRED_N = 5`) | 42, 218–223 |

Note on brief: the brief cited `src/calibration/fit_predictive_error.py` and
`ens_error_model.py:141` as the applicator. Codegraph + read confirms the real
applicator at `ens_bias_model.py:234`; ens_error_model.py:141 is the LIVE-wiring
applicator that subtracts `λ·bias` instead of raw `bias`. Both are exercised.

---

## §1 — Sign convention proof (PASS-A)

**Fixture (per-day, 30 days in MAM):**

| Quantity | Value |
|---|---|
| member array | `[19.8, 19.9, 20.0, 20.1, 20.2]` (mean = 20.0°C) |
| settlement (actual) | 25.0°C |
| issue_hour | 0 (HIGH-contributing cycle) |
| OPD data_version | `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` |
| TIGGE data_version | `tigge_mx2t6_local_calendar_day_max_v1` |
| n_days | 30 |

**Run output (from `tests/test_invariant_sign_convention.py::test_step1_sign_convention_end_to_end`):**

| Quantity | Observed |
|---|---|
| `bias_c` (stored) | **-5.0000** |
| `bias_sd_c` | 0.0002 |
| `correction_strength` (λ) | 1.0000 |
| `effective_bias_c` (λ·bias_c) | **-5.0000** |
| `apply_bias_to_extrema(20.0, ...)` | **25.0000** |
| `p_raw_vector_with_error_model` pre-MC | **25.0000** |
| DB round-trip (write → read) | bias_c preserved sign + magnitude |

**Verdict: PASS-A** — convention is `bias = mean(forecast - actual)`; applicator
subtracts (`corrected = raw - bias`). All three live surfaces (offline applicator,
live-wiring applicator, DB round-trip) agree on the warm direction with the
expected magnitude (≈ -5°C bias → ≈ +5°C correction).

**Implication:** the East-Asia "wrong-direction" pattern reported in the
SAFE/HOLD tier classification IS NOT a sign-flip in producer or applicator.
The defect lives elsewhere (candidates: residual lineage, settlement-vs-forecast
unit/timezone mismatch upstream of producer, or per-city data contamination).

---

## §2 — Window selection proof (PASS)

**Fixture (single target_date, two snapshots per metric):**

| Metric | 0Z snapshot (contrib) | 12Z snapshot (contrib) | Settlement | Expected |
|---|---|---|---|---|
| HIGH | mean=30°C (issue_hour=0) | mean=22°C (issue_hour=12) | 32°C | loader picks 0Z → residual ≈ -2 |
| LOW | mean=25°C (issue_hour=0) | mean=18°C (issue_hour=12) | 17°C | loader picks 12Z → residual ≈ +1 |

Both 0Z and 12Z snapshots have `contributes_to_target_extrema=1` and valid
`available_at`. The 12Z snapshot is always FRESHER (later available_at).

**Run output:**

| Test | residual |
|---|---|
| `test_step2_window_selection_high_picks_contributing_cycle` | -2.000 (0Z picked) |
| `test_step2_window_selection_low_picks_contributing_cycle` | +1.000 (12Z picked) |

**Verdict: PASS** — `load_bucket_residuals` correctly applies metric-aware cycle
preference (HIGH→0Z, LOW→12Z) even when the non-contributing cycle is fresher.

---

## §3 — Transport sufficiency proof (PASS)

**Fixture:**

| Quantity | Value |
|---|---|
| n_paired (OPD ∩ TIGGE same-date) | 1 |
| paired Δ (single date) | +5°C (F25=25 vs F50=20) |
| OPD-only days | 29 days with residual ≈ 0 |
| TIGGE-only days | 29 days with residual ≈ -2 |
| `MIN_PAIRED_N` | 5 (in `ens_error_model.py:42`) |

**Run output:**

| Quantity | Observed | Threshold |
|---|---|---|
| `bias_c` | < 3.0°C in magnitude | PASS |
| `effective_bias_c` (λ·bias_c) | < 3.0°C in magnitude | PASS |

**Verdict: PASS** — with `n_paired < MIN_PAIRED_N`, the transport step is gated
to `delta_gated = []`. The 5°C single-pair delta does NOT propagate into a
confident +5°C transport shift. Posterior remains close to the prior with no
spurious large correction.

---

## Final verdict table

| Step | PASS/FAIL | Implication | Next action |
|---|---|---|---|
| 1 — Sign | **PASS-A** | Producer + applicator + DB round-trip all sound. Convention `bias = forecast - actual`, applicator subtracts. | The East-Asia "wrong-dir" defect is NOT a sign flip — investigate elsewhere (residual lineage, upstream unit/TZ, per-city data). |
| 2 — Window | **PASS** | HIGH→0Z and LOW→12Z preference is enforced by `load_bucket_residuals`, not bypassed. | No action; window-selection invariant is locked. |
| 3 — Transport | **PASS** | `MIN_PAIRED_N=5` gate correctly suppresses single-pair transport delta from injecting a confident correction. | No action; transport-sufficiency invariant is locked. |

---

## Findings noted but NOT fixed (per brief)

1. **Pre-existing test breakage in `tests/test_ens_predictive_pipeline.py`** — its
   fixture CREATE TABLE for `ensemble_snapshots` is missing the `issue_time`
   column. `load_bucket_residuals` (ens_bias_repo.py:191) selects `e.issue_time`,
   so both tests in that file currently fail with `sqlite3.OperationalError: no
   such column: e.issue_time`. The new file `test_invariant_sign_convention.py`
   uses a corrected fixture. Fix is out of scope for this audit.

2. **Worktree contamination during this audit** — `src/state/db.py` and
   `tests/state/_schema_pinned_hash.txt` were modified mid-run by a concurrent
   process. This blocked one pytest invocation with a SCHEMA DRIFT exit and
   resolved on its own ~5s later. NOT a defect of this audit; flagged for the
   broader Fitz "agent isolation worktree leaks" feedback.

---

## How to re-run

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical
/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python \
  -m pytest tests/test_invariant_sign_convention.py -v
```

Expected: 4 tests PASS in ~1s.
