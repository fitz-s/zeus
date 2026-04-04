# math_task.md

## Purpose
Active execution control surface for Zeus math/forecast layer work.
Tracks exactly one frozen packet at a time plus the queue of verified next priorities.

## Metadata
- Created: `2026-04-03 America/Chicago`
- Created by: `Opus deep math evaluation`
- Authority scope: `math layer packets only`
- Supersedes: `root_task.md` (archived as `root_task_archive_2026-04-03.md`)

## Current Active Packet

- Packet: `MATH LANE COMPLETE`
- State: `ALL WORK DONE`
- Execution mode: `IDLE`
- Owner: `Math lane lead`

## Summary

All P0/P1/P2 math validation packets have been completed, including code changes:

| Packet | Status | Key Finding |
|--------|--------|-------------|
| MATH-001 | ✅ PASS | Sunset collapse works correctly |
| MATH-002 | ✅ PASS | 97.8% hit rate in high-confidence region |
| MATH-003 | ⚠️ COND | Found freshness disconnect → MATH-005 |
| MATH-004 | ✅ PASS | 50% floor acceptable, enhanced by MATH-010 |
| MATH-005 | ✅ IMPL | Freshness-sigma connection implemented |
| MATH-006 | ✅ PASS | Coefficients acceptable, renamed in MATH-010 |
| MATH-007 | ✅ PASS | Lead multiplier is conservative and safe |
| MATH-008 | DEFER | Cosmetic rename, low priority |
| MATH-009 | ✅ PASS | Bayesian model promising but not urgent |
| MATH-010 | ✅ IMPL | Quantization noise floor + named constants |

**Code changes implemented:**
1. MATH-005: `freshness_factor` parameter added to `day0_post_peak_sigma()`
2. MATH-010: Quantization noise floor (0.35°F/0.20°C) + all magic constants renamed

**All 112 math tests pass.**

---

## Queue (Priority Order)

All packets are validation-first: measure before changing.

| ID | Priority | Title | Status | Depends On | Deliverable | Validation |
|----|----------|-------|--------|------------|-------------|------------|
| MATH-001 | P0 | Sunset sanity validation | **PASS** | - | Test proving Day0 distribution narrows appropriately near sunset | ✅ 7 tests pass |
| MATH-002 | P0 | Bin hit-rate calibration framework | **PASS** | MATH-001 | Historical bin hit-rate vs predicted probability comparison tool | ✅ 6 tests pass, high-conf 97.8% hit rate |
| MATH-003 | P0 | Stale-data stress test | **CONDITIONAL PASS** | MATH-001 | Test proving 2h-stale trusted observation expands sigma appropriately | ⚠️ Found defect, triggered MATH-005 |
| MATH-004 | P1 | Sigma floor evaluation | **PASS** | MATH-001,002,003,005 | Evidence-based decision on 50% floor | ✅ 7 tests, enhanced by MATH-010 |
| MATH-005 | P0 | Freshness-to-sigma connection | **IMPL** | MATH-003 | Connect freshness_factor to distribution width | ✅ Implemented, 1.5x max expansion |
| MATH-006 | P1 | temporal_closure coefficients calibration | **PASS** | MATH-002 | Data-driven 0.75/0.50/0.35 evaluation | ✅ 6 tests, constants renamed in MATH-010 |
| MATH-007 | P2 | lead_sigma_multiplier dynamic calculation | **PASS** | MATH-002 | MAE vs lead_days curve extraction from model_bias | ✅ 5 tests, verdict: current is conservative and safe |
| MATH-008 | P2 | ens_dominance rename + documentation | DEFERRED | - | Rename to obs_exceeds_ens_fraction + docstring clarification | Low priority, cosmetic |
| MATH-009 | P2 | Bayesian sigma synthesis evaluation | **PASS** | MATH-004 | Prototype Bayesian sigma merge vs current linear floor | ✅ 5 tests, verdict: promising but not urgent |
| MATH-010 | P0 | Quantization noise floor + named constants | **IMPL** | MATH-004,third-party | Implement floor and rename magic constants | ✅ Implemented per third-party review |

---

## Hardcoded Values Under Evaluation

These are the specific constants identified in the deep evaluation that require validation before modification:

| Location | Current Value | Issue | Validation Packet |
|----------|---------------|-------|-------------------|
| `forecast_uncertainty.py:250` | `hours_remaining / 12.0` | Linear decay doesn't match atmospheric uncertainty cliff | MATH-006 |
| `forecast_uncertainty.py:258-263` | `0.75, 0.50, 0.35` | Magic constants without empirical basis | MATH-006 |
| `forecast_uncertainty.py:524` | `peak * 0.5` | 50% sigma floor too conservative | MATH-004 |
| `forecast_uncertainty.py:479` | `age_hours / 3.0` | 3h freshness decay too permissive during peak heating | MATH-005 |
| `ensemble_signal.py:197-206` | `discount = 0.7` | Fixed bias discount regardless of sample quality | MATH-007 |
| `forecast_uncertainty.py:203` | `1.0 + 0.2 * (lead / 6.0)` | Fixed lead inflation without per-city/season validation | MATH-007 |

---

## External Review Summary (Gemini 2026-04-03)

**Verdict**: Acceptable with caution

**Defensible**:
- Observed floor constraint (physical invariant)
- Sigma shrinkage direction (standard nowcasting)
- Freshness decay concept (industry standard)

**Problematic**:
- Correlated-factor multiplication in temporal_closure (now fixed to max())
- 50% sigma floor (too conservative post-sunset)
- 3h freshness decay (too permissive during peak heating)
- Linear time decay (should be cliff-shaped)
- ens_dominance directionality (needs documentation)

**Required Validations Before Production Trust**:
1. Bin hit-rate calibration
2. Sunset sanity check
3. Stale trusted observation stress test

---

## Claim Rules

1. Before starting, change packet status to `IN_PROGRESS` and write the owner.
2. After finishing, change status to `REVIEW` or `DONE`.
3. Record actual touched files, validation results, and unresolved edges in `math_progress.md`.
4. If a new validation need is discovered, add it to the queue before proceeding.
5. **No coefficient changes without validation evidence.**

## Queue Policy

- Validation before modification.
- One packet at a time.
- Evidence requirements are blocking, not advisory.
- Gemini's three required validations are P0; all else waits.
