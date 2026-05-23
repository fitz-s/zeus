# Phase 2 Closure — Strategy vnext mainline

Closed: 2026-05-21
Authority: Phase 1 closure tag (`phase1_landed`), per-track SCAFFOLD docs in this directory, W6 verifier evidence (session a516a719d2f5abc5e).

## Tracks landed

| Track | Subject | PR | Merge sha | Tag | Schema impact |
|---|---|---|---|---|---|
| T1 | book_hash_transitions transition table | #214/descendants | `62177ecfa4` | `phase2_track1_landed` | world `SCHEMA_VERSION` 13→14 |
| T2 | NoTradeReason StrEnum + no_trade_events natural-key table | #222 | `ce4862a4d2` | `phase2_track2_landed` | world `SCHEMA_VERSION` 14→15 |
| T3 | FreshnessRegistry centralized gate (10-callsite migration) | #234 | `dfe0a20b15` | `phase2_track3_landed` | no schema change |
| T4 | MarketAnalysisVNext + MicrostructureMetrics + anchor-source wire | #238 | `6e17d90195` | `phase2_track4_landed` | `SCHEMA_FORECASTS_VERSION` 4→5 |
| T5 | Position.market_slug JSON + day0 nowcast wiring | #236 | `7d3b24ffac` | `phase2_track5_landed` | no schema change (JSON-only field) |
| P0-1 | Family-exclusive entry gate (Stage A) | #225 | `fc54e6930f` | (no track tag — bundled merge, see follow-up) | no schema change |
| Cleanup | T4_MERGE_DATE + db_table_ownership SCAFFOLD leak | #239 | `5c471cd51f` | (folded into closure) | no schema change |

## Cross-track invariants confirmed (W6 verifier)

- `decision_events` natural key (5-tuple: `market_slug, temperature_metric, target_date, observation_time, decision_seq`) is canonical entry-truth for both decision_events + no_trade_events; `allocate_decision_seq()` runs UNION under `db_writer_lock` (T2).
- `FreshnessLevel(IntEnum)` (FRESH=0..EXPIRED=3) is the single source of freshness verdicts; 11 callsites migrated (T3).
- `T4_MERGE_DATE` constant carries real merge-commit ISO (set by #239 cleanup); anchor-source antibody at `tests/test_inv_anchor_source_real_value.py` no longer silently matches zero rows.
- `market_microstructure_snapshots` DDL coherent with `architecture/db_table_ownership.yaml`; harvester anchor-source lookup uses `get_world_connection()` per INV-37 (T4).
- `Position.market_slug` JSON-only field is backward-compat with v1-vintage `positions.json`; nowcast wiring gates on `market_slug` non-null + `hours_remaining ≤ 6` (T5).
- `dedup_mutually_exclusive_families()` hooks `cycle_runtime.evaluate_cycle` before no_trade persistence + execution; env flag `ZEUS_LIVE_MAX_ONE_ENTRY_PER_WEATHER_FAMILY` default ON (P0-1 Stage A).

## CI evidence

5/5 most-recent main runs green at closure time. Required gates: `gitleaks`, `replay-correctness-gate (Gate 4 Phase 4.D)`. Advisory: `full-pytest-sweep`, `pr-loc-budget`.

## Deferred to Phase 3+

- **#59 P0-1 cross-module integration test** — existing test at `tests/test_inv_family_exclusive_sizing.py:151` calls `dedup_mutually_exclusive_families()` directly (unit-flavor). Cross-module path through `cycle_runtime.evaluate_cycle` needs its own relationship test. Dispatched as a Phase 2 follow-up PR (executor a1e39704da8630f5a).
- **P0-1 Stage B** — full `WeatherFamilyDecision` / `ExclusiveOutcomePortfolio` / `vector_kelly` optimizer per operator's authoritative spec (`docs/operations/task_2026-05-20_family_level_sizing_fix/FAMILY_SIZING_FIX_SPEC.md`). Stage A env-flag gate is interim; Stage B is full structural fix.
- **P0-1 single-purpose-merge audit-trail repair** — Stage A merged under bundled commit `fc54e6930f` ("fix(live): restore canonical entry truth gates") rather than dedicated single-purpose PR. Memory anchor: `feedback-p0-live-money-merge-must-be-single-purpose`. Future P0 ships single-purpose.

## Phase 3 readiness gate

| Prerequisite | Status |
|---|---|
| Phase 1 closure tag | landed (`phase1_landed`) |
| Phase 2 tracks T1-T5 + P0-1 on main | confirmed (table above) |
| Per-track tags | 5/5 pushed |
| CI green on main | confirmed |
| W6 verifier evidence | session a516a719d2f5abc5e |
| Phase 3 ultraplan packet | dispatched (planner agent a034de4c06550b492) |

Phase 3 dispatch authorized once this closure tag (`phase2_landed`) lands.

## Process lessons (this phase)

- 3-class phantom-coverage rule (Type-1 field + Type-2 storage + Type-3 line-anchor, all via `git show origin/main`) codified in autopilot §11.1.G9 — recurred 6× across two sessions before adoption.
- Executor PR monitor must self-poll silently to terminal state; ONE merge-ready emission. Re-reading PR endpoints across two agents is duplicate-read waste. Memory: `feedback-executor-pr-monitor-silent-terminal-only`.
- Wave-level critic NOT per-slice (saves opus budget).
- Dispatch briefs cite authority verbatim (memory: `feedback-dispatch-brief-cite-authority-verbatim-not-paraphrase`).
- Bundled merges break P0 audit-trail (memory: `feedback-p0-live-money-merge-must-be-single-purpose`).
