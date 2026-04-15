# ADR: Zeus K4.5 Fix Pack

**Status**: APPROVED  
**Branch**: `worktree-data-rebuild`  
**Frozen spec**: `.omc/plans/k4.5-freeze.md`  
**Ralplan iterations**: 4 (Critic iter-4 APPROVE)  
**Date**: 2026-04-13  

---

## Context

K4-code introduced the authority gate: every data atom must carry `authority='VERIFIED'` before entering the live trading path. Round 1 (code-reviewer) and Round 2 (adversarial critic) found 12 defects in the K4-code implementation. This ADR records the structural decisions made to address them.

### Plan A vs Plan B

**Plan A (chosen)**: Patch K4-code in-place. The 3 structural decisions fix the design failures that generated the 12 defects. K4-code commits stay; this fix pack patches them.

**Plan B (rejected)**: Revert K4-code and re-implement from scratch. Rejected because K4-code's overall architecture is sound; the defects are localized to 3 structural gaps, not to the architecture itself.

---

## The 12 defects (Round 1 + Round 2 consolidated)

### CRITICAL (3)

| ID | Location | Description |
|----|----------|-------------|
| C1 | `scripts/refit_platt.py:103-109` | INSERT into platt_models omits `authority` column. Every refit silently writes UNVERIFIED. Migration's DELETE step then deletes them. Zero VERIFIED Platt models post-rebuild. |
| C2 | `scripts/rebuild_calibration.py:252` | Unicode literal bug `f"{v}u00b0{city.settlement_unit}"` produces literal `"85u00b0F"`. Synthetic-bin fallback path corrupted. |
| C3 | `scripts/rebuild_settlements.py:107-109` | Reads `obs["unit"]` but does NOT cross-check vs `city.settlement_unit`. Wrong-unit obs stamped `authority='VERIFIED'`. VERIFIED label on wrong-unit data actively deceives the authority gate. |

### HIGH (3)

| ID | Location | Description |
|----|----------|-------------|
| H3 | All 4 `compute_alpha` call sites + all test files | `authority_verified=` kwarg omitted. Second-line gate at `market_fusion.py:94-95` permanently bypassed. |
| H4 | `scripts/rebuild_calibration.py:286-308` | `rows_written += 1` unconditional regardless of INSERT OR IGNORE rowcount. Re-run summary lies. |
| H5 | `blocked_oos.py:64-75`, `store.py:139-144`, `effective_sample_size.py:43-58` | Three bare SELECTs from `calibration_pairs` with no authority filter. Future contributors inherit unfiltered access by default. |

### MEDIUM (6)

| ID | Location | Description |
|----|----------|-------------|
| M6 | `scripts/rebuild_calibration.py:287-305` | Bare `except Exception: pass` swallows DB errors silently. Combined with H4, summary lies harder. |
| M7 | `src/calibration/store.py:116` | Pre-migration shim returns ALL rows when authority column absent and `authority_filter='UNVERIFIED'` requested. False-positive blocks every trade on half-migrated DB. |
| M8 | `tests/test_rebuild_pipeline.py:167` | `assert len(pairs) > 0` instead of exact count. Bug writing 1 pair passes. |
| M9 | TIGGE ensemble_snapshots `value_native_unit` | ECMWF GRIB 2t field delivered in Kelvin (~298K for 25°C). No assertion at K4 read time. |
| M10 | `scripts/rebuild_calibration.py _compute_p_raw_for_bins` | No NaN/Inf check on members_json values. Silent bias. |
| M11 | `scripts/rebuild_calibration.py` | No `len(member_maxes) == 51` assertion. Corrupt 10-member snapshot produces silent biased output. |

---

## The 3 structural decisions

### K1_struct — Per-row content validation

**Decision**: VERIFIED label is a CONTRACT not a STAMP. Every rebuild script must call a per-row validator BEFORE constructing the output atom.

**Rationale**: C3 showed that unit-inconsistent data was being stamped VERIFIED. M9/M10/M11 showed that Kelvin values, NaN members, and wrong member counts were silently corrupting outputs. The common failure mode: each script independently implemented ad-hoc checks (or none). A single validator module forces all scripts through the same gate.

**Implementation**:
- New module: `src/data/rebuild_validators.py`
- `validate_ensemble_snapshot_for_calibration()`: checks member count (51), detects Kelvin, converts, hard-rejects impossible values, checks NaN/Inf
- `validate_observation_for_settlement()`: cross-checks unit vs city.settlement_unit, converts F↔C mismatches (does not reject), rejects unknown units, detects Kelvin
- Both log failures to `availability_fact` (best-effort, never blocks)
- Both integrated into `rebuild_calibration.py` and `rebuild_settlements.py`

**Key design choice**: Convert F↔C mismatches, do not reject. Reject only unknown units and impossible values. This prevents data loss from well-formed but mis-labeled data while still blocking genuinely corrupt data.

### K2_struct — Perimeter authority

**Decision**: Authority enforcement at SELECT layer, not at consumption layer.

**Rationale**: H5 showed that `blocked_oos.py`, `store.py.get_pairs_count()`, and `effective_sample_size.py` all queried `calibration_pairs` without filtering by authority. The authority gate in the evaluator is only as strong as its weakest input source. Every SQL SELECT from `calibration_pairs` must default to `authority='VERIFIED'`.

**Implementation**:
- `src/calibration/blocked_oos.py._fetch_rows`: `authority_filter='VERIFIED'` default
- `src/calibration/store.py.get_pairs_count`: `authority_filter='VERIFIED'` default
- `src/calibration/effective_sample_size.py.build_decision_groups`: `authority_filter='VERIFIED'` default
- `src/strategy/market_fusion.py.compute_alpha`: signature changed to keyword-only `*, authority_verified: bool` (no default) — forces every caller to be explicit
- `src/calibration/store.py` M7 fix: pre-migration DB returns empty list for all filter values (not all rows)
- Lint rule in `scripts/semantic_linter.py`: forbid `FROM calibration_pairs` outside allowlist (`store.py`, `blocked_oos.py`, `effective_sample_size.py`, `migrations/`). `scripts/` carved out (named gap: operator-run, reviewed at PR time).

**Universal compute_alpha rule**: Post-commit grep `compute_alpha(' src/ tests/` returns zero lines without `authority_verified=` (excluding `test_bootstrap_symmetry.py:120` mock.patch decorator).

### K3_struct — Exact-semantic test coverage

**Decision**: Tests assert exact counts, exact label format (byte-equal), exact schema. Branch coverage: every rebuild script test exercises both happy-path and fallback-path.

**Rationale**: M8 showed `assert len(pairs) > 0` would pass even if the rebuild wrote 1 pair instead of 15. C2's unicode bug was never caught because no test exercised the synthetic-bin path. The pattern: tests that only check "something exists" cannot catch wrong-quantity or wrong-format bugs.

**Implementation**:
- `test_rebuild_pipeline_exact_counts`: asserts `len(pairs) == 12` (3 snapshots × 4 qualifying bins)
- `test_rebuild_calibration_synthetic_bins_use_real_degree_symbol`: forces synthetic-bin fallback, asserts `°` present and `u00b0` absent (C2 regression guard)
- `test_rows_written_reflects_actual_inserts`: second run reports `rows_written=0` (idempotency, H4 guard)
- `test_refit_writes_authority_verified`: all platt_models rows have `authority='VERIFIED'` after refit (C1 guard)
- `test_rebuild_settlements_rejects_unknown_unit`: unknown unit rejected, zero settlements written
- `test_rebuild_calibration_db_error_is_not_swallowed`: injected IntegrityError triggers `logging.warning` (M6 guard)

---

## 7-commit chain

| Commit | Hash | Scope |
|--------|------|-------|
| 1 | `1876f8c` | `rebuild_validators.py` + unit tests (K1_struct) |
| 2 | `0540832` | K1_struct integration into rebuild scripts + C2 unicode fix |
| 3 | `af82618` | K3_struct direct fixes (C1 refit authority, H4 rowcount, M6 logging, M8 exact counts) |
| 4 | `5837e64` | K2_struct perimeter authority + compute_alpha universal update |
| 5 | `489916f` | K2_struct lint rule + CI wiring |
| 6 | `37add36` | E2E tests with exact semantics + branch coverage |
| 7 | *(this commit)* | ADR document |

---

## Acceptance criteria met

1. `pytest tests/test_rebuild_validators.py tests/test_authority_gate.py tests/test_rebuild_pipeline.py tests/test_semantic_linter.py` passes green
2. Full regression: `pytest tests/` passes except 3 pre-existing `test_runtime_guards.py` failures
3. `grep -rn 'compute_alpha(' src/ tests/` returns zero lines without `authority_verified=` (excluding mock patch site)
4. `grep -n 'u00b0' scripts/rebuild_calibration.py` returns zero matches outside comments
5. `grep -n 'authority' scripts/refit_platt.py` shows VERIFIED in INSERT column list
6. `grep -rn 'FROM calibration_pairs' src/` — every match in allowlisted files only
7. Semantic linter reports 0 violations on `src/`

---

## Post-K4.5 next steps

After K4.5 lands: re-run Round 1 (code-reviewer) + Round 2 (adversarial critic) on the new state. If clean, proceed to door 3 (Gemini external). If new defects found, ralplan iter 5 (last allowed).

K4-exec (actual wipe + backfill) remains gated by the 9-round approval loop. K4.5 is a code-only fix pack — no production DB changes.
