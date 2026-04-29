# Slice K3.G10-scaffold — scripts/ingest/* + isolation antibody

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: parent packet `docs/operations/task_2026-04-26_live_readiness_completion/plan.md` Wave 1 + workbook G10 (archived) + con-nyx pattern feedback #5 (DB round-trip integration tests for any helper that reads from cache).
Status: planning evidence; implementation has NOT begun.
Branch: `claude/live-readiness-completion-2026-04-26`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-live-readiness-2026-04-26`

## 0. Scope statement

**SCAFFOLD** — Build the `scripts/ingest/` directory with standalone tick entry points + the AST-walk isolation antibody. No `src/main.py` changes (cutover deferred to Wave 2 G10-cutover). No launchd plists (operator deploy concern).

5 tick scripts in 1:1 mapping with main.py existing scheduler entries; finer source-splits (per workbook 8-script enumeration) deferred to followup G10-source-split since they would require refactoring `daily_tick` to accept a source filter.

## 1. Files to create (all NEW; zero collision risk)

| File | Purpose | Lines |
|---|---|---|
| `scripts/ingest/__init__.py` | Package marker (also enforces "this is a leaf module — no engine imports allowed via package-level enforcement") | ~20 |
| `scripts/ingest/_shared.py` | Common helpers: logging setup, connection management, error-exit pattern | ~50 |
| `scripts/ingest/daily_obs_tick.py` | Standalone daily-obs tick (WU + HKO + Ogimet bundled per current `daily_tick`) | ~50 |
| `scripts/ingest/hourly_instants_tick.py` | Standalone hourly Open-Meteo tick | ~50 |
| `scripts/ingest/solar_daily_tick.py` | Standalone solar daily tick | ~50 |
| `scripts/ingest/forecasts_daily_tick.py` | Standalone NWP forecasts tick | ~50 |
| `scripts/ingest/hole_scanner_tick.py` | Standalone hole-scan tick | ~50 |
| `tests/test_ingest_isolation.py` | AST-walk antibody — fails if any module under `scripts/ingest/` imports from forbidden namespaces | ~120 |
| `architecture/test_topology.yaml` (companion) | Register new test | 1 line |
| `architecture/script_manifest.yaml` (companion) | Register 5 new tick scripts as ingest-lane | ~10 lines |

Total: ~450 lines new code.

## 2. Forbidden import set (from workbook G10 acceptance)

`scripts/ingest/**/*.py` may NOT import any of:
- `src.engine`
- `src.execution`
- `src.strategy`
- `src.signal`
- `src.supervisor_api`
- `src.control`
- `src.observability`
- `src.main`

ALLOWED imports include:
- `src.data.*` — the actual ETL logic
- `src.state.db.*` — connection management
- `src.config.*` — env / state paths
- `src.contracts.*` — typed atoms
- Standard library + 3rd-party

This list is the antibody's enforcement contract.

## 3. Antibody design (AST-walk)

`tests/test_ingest_isolation.py`:

| # | Test | Asserts |
|---|---|---|
| 1 | `test_scripts_ingest_directory_exists` | `scripts/ingest/` is a directory |
| 2 | `test_scripts_ingest_has_init_module` | `__init__.py` present (package marker) |
| 3 | `test_no_forbidden_imports_in_ingest` | AST-walk every `*.py` under `scripts/ingest/`; for each `Import` / `ImportFrom`, fail if the module name matches a forbidden prefix |
| 4 | `test_each_tick_script_has_main_callable` | AST-walk: each `*_tick.py` has a `main()` callable + `if __name__ == "__main__":` block |
| 5 | `test_each_tick_script_carries_lifecycle_header` | grep header for `# Lifecycle:` + `# Purpose:` + `# Reuse:` |

The big antibody is #3 — a regex-first false-positive scan would miss `from src.signal.diurnal import x` if I just grep for "signal". AST-walk is precise.

## 4. Worktree-collision check (re-verified 2026-04-26)

- `scripts/ingest/**`: NEW dir. No conflict.
- `tests/test_ingest_isolation.py`: NEW. No conflict.
- `architecture/test_topology.yaml`: touched by `zeus-fix-plan-20260426` — companion file; soft-warn acceptable.
- `architecture/script_manifest.yaml`: re-verifying — neither active worktree touches it per earlier audit.

## 5. RED→GREEN sequence

1. Build all 9 new files (5 ticks + __init__ + _shared + antibody + scope/plan/work_log/receipt).
2. Run `pytest tests/test_ingest_isolation.py` — should be GREEN out-of-gate IF I write the ticks correctly (no forbidden imports). RED only if I accidentally import something I shouldn't.
3. Smoke test each tick script: `python scripts/ingest/<name>.py --dry-run` (or equivalent). Confirm import chain works.
4. Single GREEN commit covering all files.
5. Run regression panel.
6. Register in test_topology + script_manifest.
7. Receipt.

This slice is essentially a refactoring + antibody slice. RED phase isn't useful since the test should pass on correct code. If I write a tick with a forbidden import (mistake), the antibody catches it pre-commit.

## 6. Acceptance criteria

- All 9 new files present with lifecycle headers.
- `tests/test_ingest_isolation.py` — 5/5 green.
- Each tick script runs (sample): `python -c "from scripts.ingest.daily_obs_tick import main"` (import-only smoke; full execution requires DB — not in this slice's scope).
- Regression panel shows delta=0 NEW failures.
- `architecture/test_topology.yaml` lists the test.
- `architecture/script_manifest.yaml` lists the 5 tick scripts.
- `scope.yaml` + `receipt.json` filed.

## 7. Out-of-scope (explicit handoffs)

- **G10-cutover** — removing `_k2_*_tick` from `src/main.py` scheduler. Requires operator decision + launchd cycle proof. Wave 2.
- **G10-source-split** — workbook's 8-script split (separate WU/HKO/Ogimet ticks). Requires refactoring `src.data.daily_obs_append.daily_tick` to accept source filter. Followup.
- **G10-launchd-plists** — 8 `.plist` files for launchd. Operator deploy concern; not in source tree.
- **ecmwf_open_data_cycle** standalone version — separate cadence (cycle-based, not tick-based). Followup.

## 8. Provenance

Recon performed live 2026-04-26 in this worktree:
- `src/main.py:111-211` — 5 tick functions (`_k2_daily_obs_tick`, `_k2_hourly_instants_tick`, `_k2_solar_daily_tick`, `_k2_forecasts_daily_tick`, `_k2_hole_scanner_tick`) + `_ecmwf_open_data_cycle` at L237.
- Existing ETL functions: `daily_obs_append.daily_tick`, `hourly_instants_append.hourly_tick`, `solar_append.daily_tick`, `forecasts_append.daily_tick`, `hole_scanner.HoleScanner.scan_all`. None of these import from `src.engine|execution|strategy|signal|supervisor_api|control|observability|main` (verified by grep).
- Existing `scripts/` directory uses standalone-script pattern (e.g., `scripts/fill_obs_v2_dst_gaps.py`).
