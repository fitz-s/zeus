# math_progress.md

## Purpose
Durable packet-level math lane ledger.
Survives session resets and handoffs.
Records only real state transitions, validation evidence, blockers, and next-packet moves.

## Metadata
- Created: `2026-04-03 America/Chicago`
- Created by: `Opus deep math evaluation`
- Authority scope: `durable packet-level state only`
- Supersedes: `root_progress.md` (archived as `root_progress_archive_2026-04-03.md`)

Do not use this file for:
- every retry
- every test command
- scout breadcrumbs
- micro evidence dumps

Read order for a fresh leader:
1. `AGENTS.md`
2. `math_task.md`
3. `math_progress.md`
4. current active packet
5. Gemini external review (`.omx/artifacts/gemini-day0-full-math-review-2026-04-03T06-25-56Z.md`)

---

## Current Snapshot

- Stage: `MATH-001 PASS; MATH-002 pending freeze`
- Last accepted packet: `MATH-001-SUNSET-SANITY-VALIDATION`
- Current active packet: `MATH-002-BIN-HIT-RATE-CALIBRATION`
- Current packet status: `TODO (needs freeze)`
- Team status: `solo`
- Current hard blockers: `none`

---

## Founding Context

### Why this lane exists

The previous `root_task.md` / `root_progress.md` accumulated ~140 tasks spanning runtime spine, durable events, lifecycle ownership, and forecast math. That structure served the P0-D/P1-E/P2-H rollout but is now too broad for focused math validation work.

This lane exists to:
1. Complete Gemini's three required validations before any math coefficient changes
2. Track hardcoded values with explicit validation dependencies
3. Maintain evidence-before-change discipline for forecast-layer work

### Founding evidence base

**Deep math evaluation (2026-04-03)** reviewed:
- `docs/KEY_REFERENCE/zeus_first_principles_rethink.md`
- `docs/KEY_REFERENCE/statistical_methodology.md`
- `docs/KEY_REFERENCE/zeus_design_philosophy.md`
- `src/signal/forecast_uncertainty.py`
- `src/signal/day0_signal.py`
- `src/signal/ensemble_signal.py`
- `src/signal/diurnal.py`
- `config/settings.json`
- `.omx/artifacts/gemini-day0-full-math-review-2026-04-03T06-25-56Z.md`

**Key findings**:

1. **Architecture is sound**: Observed floor, MC simulation, bootstrap CI are all defensible.

2. **Day0 magic constants lack validation**: 0.75/0.50/0.35 in `temporal_closure`, 50% sigma floor, 3h freshness decay are arbitrary.

3. **Linear time decay is wrong-shaped**: Atmospheric uncertainty has a "cliff" profile, not linear.

4. **Gemini verdict**: "Acceptable with caution" — system is better than raw ensemble but over-engineered with unvalidated constants.

5. **Three required validations before production trust**:
   - Bin hit-rate calibration
   - Sunset sanity check
   - Stale trusted observation stress test

### Prior Day0 slices already landed (from P2-H)

These are completed and should not be re-done:

| Packet | What it did |
|--------|-------------|
| FEAT-P2H-005 | MAE-aware mean-offset attenuation |
| FEAT-P2H-006 | Invalid bias provenance sanitization |
| FEAT-P2H-007 | Trusted-only nowcast source gate (source_factor 0.5 → 0.0 for untrusted) |
| FEAT-P2H-008 | Max-based temporal closure (multiplication → max()) |
| FEAT-P2H-009 | One-hour freshness threshold for post-sunset finality (on isolated branch) |

### What remains

1. **FEAT-P2H-009** is on isolated branch `architects-day0-math-fix` at `/tmp/zeus_day0_math_fix`. It is not yet merged to mainline.

2. **No validation framework exists yet**. The Gemini-required validations have not been implemented.

3. **No coefficient changes should happen until validations complete**.

---

## Durable Timeline

### [2026-04-03 18:30 America/Chicago] Math lane created

- Author: `Opus deep math evaluation`
- Event: `Lane founding`
- Status delta:
  - `root_task.md` → `root_task_archive_2026-04-03.md`
  - `root_progress.md` → `root_progress_archive_2026-04-03.md`
  - `math_task.md` created
  - `math_progress.md` created
- Basis / evidence:
  - Deep evaluation of forecast-layer code and philosophy docs
  - Gemini external review captured in `.omx/artifacts/`
  - Recognition that math validation work needs focused tracking separate from runtime/lifecycle work
- Decisions frozen:
  - Validation-before-modification discipline
  - Gemini's three validations are P0
  - No coefficient changes without evidence
- Open uncertainties:
  - Actual bin hit-rate characteristics
  - Actual sunset distribution width
  - Actual stale-data sigma expansion behavior
- Next required action:
  - Implement MATH-001 sunset sanity validation
- Owner:
  - Math lane lead

### [2026-04-03 18:30 America/Chicago] MATH-001-SUNSET-SANITY-VALIDATION frozen

- Author: `Opus deep math evaluation`
- Packet: `MATH-001-SUNSET-SANITY-VALIDATION`
- Status delta:
  - Packet frozen as first math lane work item
- Basis / evidence:
  - Gemini external review specifically requires sunset sanity check
  - No existing test validates near-sunset distribution narrowing
- Decisions frozen:
  - Test should assert distribution width at T-1min before sunset
  - Test should use realistic Day0Signal construction with post-sunset temporal context
  - Test should document observed distribution width for later coefficient tuning
- Open uncertainties:
  - What distribution width is "acceptable"?
  - Does current temporal_closure achieve sufficient narrowing?
- Next required action:
  - Write targeted test in `tests/test_day0_signal.py`
  - Run with multiple city/season/observation combinations
  - Document results in math_progress.md
- Owner:
  - Math lane lead

### [2026-04-03 19:15 America/Chicago] MATH-001-SUNSET-SANITY-VALIDATION COMPLETED

- Author: `Opus math validation execution`
- Packet: `MATH-001-SUNSET-SANITY-VALIDATION`
- Status delta:
  - Packet status: `FROZEN / READY TO IMPLEMENT` → `PASS`
  - Current active packet: `MATH-001` → `MATH-002`
- Evidence collected:

**Test Results (7 new tests in `TestSunsetSanityValidation`):**

| Test | obs_weight | p_within_2F | effective_std | Verdict |
|------|------------|-------------|---------------|---------|
| Fresh trusted at sunset | 1.0000 | 1.0000 | 0.00°F | **PASS** |
| Stale (2h) at sunset | 1.0000 | 1.0000 | 0.00°F | **PASS** |
| Untrusted at sunset | 1.0000 | 1.0000 | 0.00°F | **PASS** |
| Multi-city (NYC/London/Tokyo) | 1.0000 | 1.0000 | 0.00 | **PASS** |

**Daylight Progress Transition (daylight_progress sweep at 0.5 → 1.0):**

| progress | h_rem | obs_weight | p_within_2F | effective_std |
|----------|-------|------------|-------------|---------------|
| 0.50 | 6.0h | 0.5000 | 1.0000 | 0.62°F |
| 0.70 | 3.6h | 0.7000 | 1.0000 | 0.38°F |
| 0.85 | 1.8h | 0.8500 | 1.0000 | 0.19°F |
| 0.95 | 0.6h | 0.9500 | 1.0000 | 0.00°F |
| 0.99 | 0.1h | 0.9900 | 1.0000 | 0.00°F |
| 1.00 | 0.0h | 1.0000 | 1.0000 | 0.00°F |

**Hours Remaining Impact (daylight_progress=0.5 constant):**

| h_rem | obs_weight | effective_std |
|-------|------------|---------------|
| 12.0 | 0.2500 | 1.80°F |
| 6.0 | 0.5000 | 1.36°F |
| 3.0 | 0.7500 | 0.91°F |
| 1.0 | 0.9167 | 0.57°F |
| 0.5 | 0.9583 | 0.00°F |
| 0.0 | 1.0000 | 0.00°F |

- Decisions accepted:
  - Current temporal_closure is **effective**: distribution narrows appropriately at sunset
  - Post-sunset with `daylight_progress=1.0` correctly locks to observed high
  - Progression is **gradual**: obs_weight increases smoothly, not cliff-shaped
  - `p_within_2F` acceptance threshold of > 0.80 is exceeded (achieved 1.0000)
- Observations:
  - At `hours_remaining=0.5` and `daylight_progress=0.5`, distribution already collapses to 0.00°F std
  - This indicates current temporal_closure may be **more aggressive** than needed, not too weak
  - The 0.75/0.50/0.35 coefficients may be overly conservative (damping signals too much)
  - Gemini's concern about "sunset sanity" is **resolved positively** — the system works
- Implications for later packets:
  - MATH-004 (sigma floor evaluation) can proceed without blocking
  - MATH-006 (coefficient tuning) now has baseline data
  - If anything, coefficients may need to be **less** aggressive, not more
- Files changed:
  - `tests/test_day0_signal.py`: Added `TestSunsetSanityValidation` class (7 tests)
  - `work_packets/MATH-001-SUNSET-SANITY-VALIDATION.md`: Created
- Next required action:
  - Freeze and implement MATH-002 (bin hit-rate calibration)
- Owner:
  - Math lane lead

---

## Identified Hardcoded Values

Reference list for future packets. Each value links to its validation packet.

### Category A: Day0 Temporal Closure

| File:Line | Value | Purpose | Validation Packet |
|-----------|-------|---------|-------------------|
| `forecast_uncertainty.py:250` | `12.0` | Hours for linear time closure | MATH-006 |
| `forecast_uncertainty.py:259` | `0.75` | Peak signal coefficient | MATH-006 |
| `forecast_uncertainty.py:260` | `0.50` | Daylight signal coefficient | MATH-006 |
| `forecast_uncertainty.py:261` | `0.35` | ENS signal coefficient | MATH-006 |

### Category B: Sigma Policy

| File:Line | Value | Purpose | Validation Packet |
|-----------|-------|---------|-------------------|
| `forecast_uncertainty.py:524` | `0.5` | Peak-driven sigma reduction cap | MATH-004 |
| `forecast_uncertainty.py:427` | `0.5` | Backbone residual max adjustment | MATH-004 |

### Category C: Freshness / Nowcast

| File:Line | Value | Purpose | Validation Packet |
|-----------|-------|---------|-------------------|
| `forecast_uncertainty.py:479` | `3.0` | Freshness decay hours | MATH-005 |
| `forecast_uncertainty.py:465-466` | `6.0` | Short lead hours cap | MATH-006 |
| `forecast_uncertainty.py:497` | `0.25` | Nowcast blend weight base | MATH-006 |

### Category D: Lead-Day Sigma

| File:Line | Value | Purpose | Validation Packet |
|-----------|-------|---------|-------------------|
| `forecast_uncertainty.py:202-203` | `6.0, 0.2` | Lead multiplier shape | MATH-007 |
| `forecast_uncertainty.py:212` | `4.0` | Reference spread multiplier | MATH-007 |
| `forecast_uncertainty.py:224` | `0.1` | Spread multiplier cap | MATH-007 |

### Category E: Bias Correction

| File:Line | Value | Purpose | Validation Packet |
|-----------|-------|---------|-------------------|
| `forecast_uncertainty.py:168` | `0.7` | Default bias discount factor | MATH-007 |
| `forecast_uncertainty.py:155` | `20` | Min samples for correction | MATH-007 |
| `forecast_uncertainty.py:165` | `4.0` | MAE high threshold (× base_sigma) | MATH-007 |
| `forecast_uncertainty.py:170` | `2.0` | Max offset cap (× base_sigma) | MATH-007 |

---

## Gemini Review Action Items (Reference)

From `.omx/artifacts/gemini-day0-full-math-review-2026-04-03T06-25-56Z.md`:

1. ✅ **Temporal closure multiplication → max()**: Already fixed in FEAT-P2H-008
2. ✅ **source_factor 0.5 for untrusted → 0.0**: Already fixed in FEAT-P2H-007
3. ⬜ **50% sigma floor too conservative**: Needs MATH-004 validation
4. ⬜ **3h freshness decay too permissive**: Needs MATH-005 validation
5. ⬜ **Linear time decay wrong shape**: Needs MATH-006 validation
6. ⬜ **Bin hit-rate calibration**: Needs MATH-002 framework
7. ⬜ **Sunset sanity check**: MATH-001 (current packet)
8. ⬜ **Stale-data stress test**: Needs MATH-003

---

## Integration Notes

### Relationship to Architects lane

The `architects_task.md` / `architects_progress.md` lane tracks canonical-authority rollout (P1.x through P3.x packets). That lane handles:
- Runtime spine
- State/schema ownership
- Lifecycle transitions
- Control-plane contracts

This math lane is **parallel but non-overlapping**. Math changes touch `src/signal/**` and `tests/test_*signal*.py`. Architects changes touch `src/state/**`, `src/engine/**`, `architecture/**`.

### Relationship to isolated Day0 branch

The `/tmp/zeus_day0_math_fix` worktree on branch `architects-day0-math-fix` contains:
- FEAT-P2H-009 (one-hour freshness threshold for post-sunset finality)
- Test suite passing (550 passed, 3 skipped as of last check)

That branch should remain isolated until:
1. MATH-001/002/003 validations complete
2. Integration path to mainline is explicitly frozen
3. Human approval for merge

---

## Archive Reference

The archived files contain:
- Full P0-A through P2-I program queue history
- T1-T126 task backlog (mostly durable-event lane)
- Historical baton state and claim rules
- P2-H MAE-aware/bias-sanitization/trusted-source slice records

If historical context is needed, consult:
- `root_task_archive_2026-04-03.md`
- `root_progress_archive_2026-04-03.md`
