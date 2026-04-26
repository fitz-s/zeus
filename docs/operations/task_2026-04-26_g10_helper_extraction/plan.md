# Slice K3.G10-helper-extraction — bool/dict + transitive + syspath fixes

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: con-nyx APPROVE_WITH_CONDITIONS on G10-scaffold (`dfd5fb0`) — 2 MAJOR + 4 NICE-TO-HAVE. Both MAJORs MUST land before next G10-family slice (cutover or launchd-plists).
Status: planning evidence; implementation has NOT begun.
Branch: `claude/live-readiness-completion-2026-04-26`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-live-readiness-2026-04-26`

## 0. Scope statement

Discharge G10-scaffold's 2 MAJOR conditions in a single batched slice:

1. **MAJOR #1 — transitive forbidden import**: extract `_is_missing_local_hour` from `src.signal.diurnal` to `src.contracts.dst_semantics` (allowed); update 2 ingest-path callers (`daily_obs_append.py`, `hourly_instants_append.py`); add subprocess-isolated transitive-import audit antibody.

2. **MAJOR #2 — direct script invocation broken**: add 3-line `sys.path.insert(0, ...)` shim to each of 5 tick scripts (matches `live_smoke_test.py` convention); add antibody asserting bootstrap exists.

Plus 1 NICE-TO-HAVE landed inline (con-nyx pattern feedback #12):
3. **Negative-detection test** — programmatically craft a violating tick and assert the AST-walk antibody detects it. Without this, N/N green doesn't prove the antibody actually fires on real violations.

## 1. Files touched

| File | Change | Hunk size |
|---|---|---|
| `src/contracts/dst_semantics.py` (NEW) | Canonical home for `_is_missing_local_hour`. Allowed for both ingest + engine lanes. | ~50 lines |
| `src/signal/diurnal.py` | Replace local definition with `from src.contracts.dst_semantics import _is_missing_local_hour` (re-export for back-compat). | -22/+10 lines |
| `src/data/daily_obs_append.py:73` | Switch import from `src.signal.diurnal` to `src.contracts.dst_semantics`. | 2 lines |
| `src/data/hourly_instants_append.py:50` | Same switch. | 2 lines |
| `scripts/ingest/daily_obs_tick.py` | Add sys.path shim before project imports. | +5 lines |
| `scripts/ingest/hourly_instants_tick.py` | Same. | +5 lines |
| `scripts/ingest/solar_daily_tick.py` | Same. | +5 lines |
| `scripts/ingest/forecasts_daily_tick.py` | Same. | +5 lines |
| `scripts/ingest/hole_scanner_tick.py` | Same. | +5 lines |
| `tests/test_ingest_isolation.py` | Add 3 new tests: `test_no_forbidden_transitive_imports_in_ingest` (subprocess-isolated), `test_each_tick_script_self_bootstraps_syspath` (substring grep), `test_antibody_self_test_catches_synthetic_violation` (negative-detection). | +130 lines |

Other callers of `_is_missing_local_hour` (NOT touched, work via re-export):
- `src.data.ingestion_guard.py:475-476`
- `src.signal.diurnal.py:340, :415` (within-module references)
- `tests/test_obs_v2_dst_missing_hour_flag.py` (uses `from src.signal.diurnal import _is_missing_local_hour` — back-compat re-export)

## 2. Worktree-collision check (re-verified 2026-04-26)

- `src/contracts/dst_semantics.py`: NEW file. SAFE.
- `src/signal/diurnal.py`: NOT touched by either active worktree. SAFE.
- `src/data/daily_obs_append.py`, `src/data/hourly_instants_append.py`: NOT touched by either active worktree. SAFE.
- `scripts/ingest/*`: own this slice's territory. SAFE.
- `tests/test_ingest_isolation.py`: own this slice's territory. SAFE.

## 3. Acceptance

- All 8 tests in `tests/test_ingest_isolation.py` GREEN (5 pre-existing + 3 new).
- All 6 tests in `tests/test_obs_v2_dst_missing_hour_flag.py` GREEN (back-compat verification — uses `from src.signal.diurnal import _is_missing_local_hour`).
- Each of 5 ticks importable via direct invocation (`python scripts/ingest/X_tick.py` resolves project imports cleanly).
- Regression panel delta=0.
- Pin: `from src.signal.diurnal import _is_missing_local_hour` and `from src.contracts.dst_semantics import _is_missing_local_hour` resolve to the SAME function (object identity).

## 4. RED→GREEN sequence

The pre-existing transitive antibody DID NOT EXIST before this slice — so RED phase via "add the test to current code, confirm it fires on the existing transitive imports, then fix" IS meaningful. But we'd ship the test AND the fix in one commit per atomic-slice convention:
1. Land all changes in single GREEN commit.
2. Subprocess audit empirically verified BEFORE commit (showed violations on pre-fix code; confirmed clean post-fix).

## 5. Out-of-scope (handed off to followups)

- **G10-AST-walk-dynamic-imports** (NICE-TO-HAVE #1) — detect `__import__()` + `importlib.import_module()` string-arg violations. ~15 lines, ~15min. Not in this slice; tracked as followup.
- **G10-tick-suffix-broadening** (NICE-TO-HAVE #3) — broaden main()-required check beyond `*_tick.py`. ~2 lines. Followup.
- **G10-calibration-fence** (NICE-TO-HAVE #4) — decision on adding `src.calibration` to forbidden list. Operator-gated. Folds with G10-helper-extraction-2 (e.g., move `season_from_date` to `src/contracts/season.py`).
- **G10-cutover** (Wave 2) — remove `_k2_*_tick` from `src/main.py`. Now UNBLOCKED on the helper-extraction front; still gated on operator + launchd plists + cycle proof.
- **G10-launchd-plists** — sys.path shim is in place, so launchd plists can be straightforward `python /path/to/scripts/ingest/X_tick.py` invocations.

## 6. Provenance

Recon performed live 2026-04-26 in this worktree:
- Subprocess-isolated transitive audit (con-nyx empirical reproduction): pre-fix `daily_obs_tick` pulls `src.signal`+`src.signal.diurnal`; same for `hourly_instants_tick`. solar/forecasts/hole_scanner clean.
- `python scripts/ingest/daily_obs_tick.py` direct invocation pre-fix → `ModuleNotFoundError: No module named 'src'`. Post-fix: imports resolve, fails at runtime with empty test DB (sqlite OperationalError — expected).
- `_is_missing_local_hour` callers: 4 production paths (`hourly_instants_append`, `daily_obs_append`, `ingestion_guard`, self-references in `diurnal`) + 1 test file. Back-compat re-export covers all unchanged callers.
