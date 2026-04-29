# Slice K3.G10-nice-to-have-batch — 3 followups in one commit

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: con-nyx G10-helper-extraction APPROVE verdict + 4 NICE-TO-HAVE residuals.
Status: planning evidence; implementation has NOT begun.
Branch: `claude/live-readiness-completion-2026-04-26`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-live-readiness-2026-04-26`

## 0. Scope statement

Discharge 3 of 4 G10 NICE-TO-HAVE residuals in a single batched slice (one structural decision: tighten the antibody surface). 4th NICE-TO-HAVE (G10-source-split) deferred to Wave 2 because it requires API design.

## 1. Files touched

| File | Change | Hunk |
|---|---|---|
| `src/contracts/season.py` (NEW) | Canonical home for `season_from_date` + `season_from_month` + `hemisphere_for_lat` + `_SH_FLIP` | ~50 lines |
| `src/calibration/manager.py` | Replace local definitions with `from src.contracts.season import ...` (re-export) | -42/+14 lines |
| `src/data/daily_obs_append.py:69` | Switch `from src.calibration.manager import season_from_date` → `from src.contracts.season import season_from_date` | 2 lines |
| `tests/test_ingest_isolation.py` | Add `src.calibration` to FORBIDDEN_IMPORT_PREFIXES; broaden `test_each_tick_script_has_main_callable` from `*_tick.py` to all non-_shared/non-__init__; add `test_no_forbidden_dynamic_imports_in_ingest` (Call-node patterns) | +90 lines |

## 2. Sub-deliverables

### 2.1 G10-calibration-fence (NICE-TO-HAVE #4, ~30min)

con-nyx finding: `src.data.daily_obs_append:69` imports `season_from_date` from `src.calibration.manager` — pulls `src.calibration` into ingest lane transitively. season helpers are pure calendar code (no calibration logic), so they belong in `src.contracts.season`.

Pattern matches G10-helper-extraction (`_is_missing_local_hour` → `src.contracts.dst_semantics`):
- Extract pure helpers to allowed module
- Original module re-exports for back-compat
- Ingest-path callers re-pointed
- Add forbidden namespace to FORBIDDEN_IMPORT_PREFIXES

### 2.2 G10-tick-suffix-broadening (NICE-TO-HAVE #3, ~5min)

`test_each_tick_script_has_main_callable` previously filtered on `*_tick.py`. A future `daily_runner.py` or `obs_dispatcher.py` would skip the check. Broaden to "all non-_shared, non-__init__ modules".

### 2.3 G10-AST-walk-dynamic-imports (NICE-TO-HAVE #1, ~15min)

`_collect_imports` only sees `ast.Import` + `ast.ImportFrom` statement-level imports. Dynamic call expressions (`__import__("x")`, `importlib.import_module("x")`) evade. Add:
- `_collect_dynamic_imports` helper that walks `ast.Call` nodes for the two patterns
- `test_no_forbidden_dynamic_imports_in_ingest` antibody applying same forbidden-prefix check

Non-string first args (`__import__(some_var)`) are ignored — un-auditable statically; out-of-scope.

## 3. Out-of-scope

- **G10-source-split** — needs `daily_obs_append.daily_tick(conn, source: str | None = None)` API refactor. Per con-nyx Ask #6: don't pre-build the API; refactor as part of the source-split slice itself. Wave 2.

## 4. Worktree-collision check (re-verified 2026-04-26)

- `src/contracts/season.py`: NEW. SAFE.
- `src/calibration/manager.py`: NOT touched by either active worktree per earlier audit. Re-verify before commit.
- `src/data/daily_obs_append.py`: NOT touched by either active worktree (verified earlier in helper-extraction).
- `tests/test_ingest_isolation.py`: own this slice's territory. SAFE.

## 5. Acceptance

- `tests/test_ingest_isolation.py`: 9/9 GREEN (was 8; +1 dynamic-imports test).
- Subprocess audit: 5/5 ticks clean post-fix (with `src.calibration` newly in forbidden).
- Back-compat: `from src.calibration.manager import season_from_date` and `from src.contracts.season import season_from_date` resolve to the SAME function (object identity).
- Adjacent test panel (calibration_bins_canonical, obs_v2_dst, hk_settlement_floor) all green.
- Regression panel delta=0.

## 6. Provenance

Recon performed live 2026-04-26 in this worktree:
- `src/calibration/manager.py:43-80` — season helpers + `_SH_FLIP`. Pure calendar code, no calibration dependencies.
- `grep -rnE "season_from_date|season_from_month|hemisphere_for_lat|_SH_FLIP" src/ tests/ scripts/` — 7 callers across calibration/manager (4 self), execution/harvester (1), data/observation_client (1), data/daily_obs_append (1, the ingest one), engine/replay (1).
- All callers EXCEPT `data/daily_obs_append` are in calibration-allowed lanes. Only the ingest one needs to switch import location.
