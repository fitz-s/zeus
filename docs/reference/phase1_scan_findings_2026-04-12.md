# Phase 1 Scan Findings — 2026-04-12

> Prometheus scanner. Full codebase scan (7 categories + second pass).
> Source: `.omx/context/prometheus-scan-report_2026-04-12.md`
> Status: reference — findings are open unless marked fixed.

---

## Critical Findings

| ID | File | Pattern | Severity | Phase 1 scope | Status |
|----|------|---------|----------|---------------|--------|
| C-1 | `src/config.py:46-54` | `resolve_mode()` — root paper discriminator; all `get_mode()` callers branch on its return | CRITICAL | Tier 2.4 (legacy vocab deletion) | Open |
| C-2 | `src/execution/executor.py:118` | `is_sandbox=(get_mode() == 'paper')` in `create_execution_intent()` | CRITICAL | Tier 2.4 | Open |
| C-3 | `src/execution/harvester.py:274` | `paper_mode=(get_mode() == 'paper')` in redeem/settlement path | CRITICAL | Tier 2.4 | Open |
| C-4 | `src/engine/cycle_runner.py:173` | `PolymarketClient(paper_mode=(get_mode() == 'paper'))` — every cycle init | CRITICAL | Tier 2.4 | Open |
| C-5 | `src/state/truth_files.py:48-49,126,177` | `-paper` suffix detection, paper/live path dict, `backfill_truth_metadata_for_modes(modes=('paper','live'))` | CRITICAL | Tier 1.3 | Open |
| C-6 | `src/main.py:291-296` | Startup validates `ZEUS_MODE` and accepts `{'paper', 'live'}` | CRITICAL | Tier 2.4 | Open |
| C-7 | `src/control/control_plane.py:80-89` | `pause_entries()` sets `_control_state['entries_paused']` ONLY in memory — lost on daemon restart. FM-06 violation. P1.4 auto-pause depends on this. | CRITICAL | Tier 1.4 (must fix before P1.4 closes) | Open |
| C-8 | `tests/test_architecture_contracts.py` | `test_harvester_settlement_path_uses_economically_closed_phase_before_when_applicable` — ACTIVELY FAILING. Harvester settles `active` phase positions; contract requires `economically_closed`. Bug #9. | CRITICAL | Tier 2.2 | Open |

---

## Moderate Findings

| ID | File | Pattern | Severity | Phase 1 scope | Status |
|----|------|---------|----------|---------------|--------|
| M-1 | `src/state/db.py:49` | `get_trade_connection()` docstring implies paper/live dispatch | MODERATE | Tier 1.3 | Open |
| M-2 | `src/state/db.py:538,856-860` | `env TEXT NOT NULL DEFAULT 'paper'` schema column | MODERATE | Tier 1.3 | Open |
| M-3 | `src/state/db.py:1106-1107` | `if env != 'paper':` runtime write path (paper branch dead in live-only) | MODERATE | Tier 1.3 | Open |
| M-4 | `src/state/portfolio.py:142` | `PortfolioPosition.env: str = 'paper'` — any new position without explicit env gets paper label | MODERATE | Tier 1.3 | Open |
| M-5 | `src/state/portfolio.py:849` | `candidate_mode in {'paper', 'live', 'test'}` — includes paper | MODERATE | Tier 1.3 | Open |
| M-6 | `src/data/polymarket_client.py:133,153,161,181,201,255,269` | 7 guards: `'Live API X cannot be called in paper mode'` — exist because `paper_mode=True` is reachable | MODERATE | Tier 2.4 (delete after `paper_mode` param removed) | Open |
| M-7 | `src/engine/process_lock.py:3-4,27` | Separate lock files per mode; mode parameter in process lock | MODERATE | Tier 1.3 | Open |
| M-8 | `src/execution/executor.py:129` | `edge_vwmp: float` parameter — only exists to feed `_paper_fill()` simulation | MODERATE | Phase 2 / Tier 2.4 | Deferred |
| M-9 | `src/engine/cycle_runner.py:148,153` | `except Exception: pass` around `_clear_ensemble_cache` and `_clear_active_events_cache` — no log, no alert | MODERATE | New packet candidate | Open |
| M-10 | `src/engine/cycle_runtime.py:843` | `except Exception: pass` around decision-log telemetry write — silent failure | MODERATE | New packet candidate | Open |
| M-11 | `src/control/control_plane.py:38-47` | Quarantine-clear acks read from JSON (`control_plane.json`) for decisions — not DB-backed. INV-03 violation. | MODERATE | New packet candidate | Open |
| M-12 | `src/observability/status_summary.py:77` | Reads prior JSON output as `cycle_summary` when current is None — derived export feeds back into next write. INV-03 feedback loop. | MODERATE | New packet candidate | Open |
| M-13 | `src/strategy/market_analysis_family_scan.py:35` | `scan_full_hypothesis_family` accesses `p_posterior` without `entry_method`/`selected_method` in scope — INV-12. Structural linter FAIL. | MODERATE | Structural linter / Tier 2.X | Open |
| M-14 | `src/execution/executor.py:149-225` | `_paper_fill()` 40-line dead code alongside live path — `if intent.is_sandbox` silently paper-fills on config error | MODERATE | Tier 2.4 / Phase 2 | Open |

---

## Low Findings (doc/comment only — no behavior change in Phase 1)

| ID | File | Pattern | Phase scope |
|----|------|---------|-------------|
| L-1 | `docs/reference/repo_overview.md:20` | Table still lists `zeus-paper.db` and `positions-paper.json` as active infrastructure | Phase 2 |
| L-2 | `tests/` (12 files) | `positions-paper.json` in test fixtures (exercise Bug #7 code path) | Phase 2 cleanup |
| L-3 | `docs/archives/control/root_progress.md:581` | Old `PLAN.md` reference — archive doc, historical record | No action |
| L-4 | `src/execution/executor.py:3,132` | Docstrings mentioning paper | Phase 2 |
| L-5 | `src/data/market_scanner.py:53` | Docstring mentioning paper mode | Phase 2 |
| L-6 | `src/riskguard/riskguard.py:386`, `src/main.py:241`, `src/state/strategy_tracker.py:8` | Comments referencing paper | Phase 2 |

---

## Test Suite State (2026-04-12)

27 failures + 6 errors. Three structural root causes:

| Cluster | Root cause | Scope |
|---------|-----------|-------|
| A — CRITICAL | Missing schema tables (`trade_decisions`, `decision_log`) — Tier 1.3 dropped 16 tables. Test fixtures still query old tables. | Tier 2.1 (update fixtures to `position_events`+`position_current`) |
| B — MODERATE | `test_truth_layer.py` expects `strategy_tracker-paper.json` to be written — live-only no longer writes it. | Phase 2 cleanup (`@pytest.mark.skip` until then) |
| C — MODERATE | `test_load_tracker_rejects_deprecated_state_file` DID NOT RAISE — deprecation guard in `truth_files.py` silenced. | Investigate `truth_files.py` guard |
| D — LOW | 6 calibration quality test errors — zero-state DB post-nuke, not structural. | Live-data dependent |

---

## Packet Mapping

| Packet | Findings |
|--------|---------|
| Tier 1.3 (shared→world rename) | C-5, M-1, M-2, M-3, M-4, M-5, M-7 |
| Tier 1.4 (auto-pause hook) | C-7 (must fix before or with P1.4) |
| Tier 2.1 (JSON fallback deletion) | Cluster A+B test failures |
| Tier 2.2 (settlement iterator) | C-8 (contract test must be GREEN to close Tier 2.2) |
| Tier 2.4 (legacy vocab deletion) | C-1, C-2, C-3, C-4, C-6, M-6, M-14 |
| Phase 2 | L-1 to L-6, M-8, Cluster B tests |
| New packet candidates | C-7, M-9, M-10, M-11, M-12 |
