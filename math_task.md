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

- Packet: `MATH-005-FRESHNESS-SIGMA-CONNECTION`
- State: `REQUIRED FIX (blocking)`
- Execution mode: `SOLO_EXECUTE`
- Owner: `Math lane lead`
- Reason: `MATH-003 proved freshness_factor is disconnected from distribution width`

## Objective

Fix the defect discovered in MATH-003: `freshness_factor` in `day0_nowcast_context()` is computed but **not propagated** to distribution width calculations.

During mid-day peak heating:
- `freshness_factor` correctly decays from 1.0 to 0.0 over 3 hours
- But `observation_weight` and `effective_std` **do not change**
- Sigma expansion ratio = 1.00x (no expansion for stale data)

This violates the expected behavior that stale observations should increase uncertainty.

## Allowed Files

- `math_task.md`
- `math_progress.md`
- `src/signal/forecast_uncertainty.py` (NOW ALLOWED — fix implementation)
- `tests/test_day0_signal.py`
- `tests/test_forecast_uncertainty.py`
- `work_packets/MATH-005-FRESHNESS-SIGMA-CONNECTION.md`

## Forbidden Files

- `src/signal/day0_signal.py` (until this fix is complete)
- `architecture/**`
- `docs/governance/**`
- `AGENTS.md`

## Non-goals

- No coefficient changes beyond the freshness fix
- No sigma floor changes (MATH-004)
- No Bayesian/Brownian model implementation yet (later packet)

---

## Queue (Priority Order)

All packets are validation-first: measure before changing.

| ID | Priority | Title | Status | Depends On | Deliverable | Validation |
|----|----------|-------|--------|------------|-------------|------------|
| MATH-001 | P0 | Sunset sanity validation | **PASS** | - | Test proving Day0 distribution narrows appropriately near sunset | ✅ 7 tests pass |
| MATH-002 | P0 | Bin hit-rate calibration framework | **PASS** | MATH-001 | Historical bin hit-rate vs predicted probability comparison tool | ✅ 6 tests pass, high-conf 97.8% hit rate |
| MATH-003 | P0 | Stale-data stress test | **CONDITIONAL PASS** | MATH-001 | Test proving 2h-stale trusted observation expands sigma appropriately | ⚠️ freshness_factor not connected to sigma — DEFECT FOUND |
| MATH-004 | P1 | Sigma floor evaluation | TODO | MATH-001,002,003 | Evidence-based decision on 50% floor | Validation results + recommended change |
| MATH-005 | **P0** | Freshness-to-sigma connection | **REQUIRED FIX** | MATH-003 | Connect freshness_factor to distribution width | Currently broken — staleness has no effect |
| MATH-006 | P1 | temporal_closure coefficients calibration | TODO | MATH-002 | Data-driven 0.75/0.50/0.35 replacement | Historical hit-rate per coefficient regime |
| MATH-007 | P2 | lead_sigma_multiplier dynamic calculation | TODO | MATH-002 | MAE vs lead_days curve extraction from model_bias | Per-city/season multiplier table |
| MATH-008 | P2 | ens_dominance rename + documentation | TODO | - | Rename to obs_exceeds_ens_fraction + docstring clarification | Code review + test update |
| MATH-009 | P2 | Bayesian sigma synthesis evaluation | TODO | MATH-004 | Prototype Bayesian sigma merge vs current linear floor | Side-by-side calibration comparison |

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
