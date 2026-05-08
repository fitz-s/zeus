# Object Invariance Wave 30 - Position Current Monitor Probability Read-Model Authority

Status: PLANNING-LOCK EVIDENCE FOR LOCAL SOURCE/TEST SLICE, NOT LIVE UNLOCK, NOT VENUE OR DB MUTATION AUTHORITY

Created: 2026-05-08
Last reused or audited: 2026-05-08
Authority basis: root AGENTS.md object-meaning invariance goal; Wave28/Wave29 residual sweep; docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md

## Scope

Repair one bounded boundary class:

`position_current canonical projection -> portfolio loader read model monitor probability fields`

This wave does not mutate live/canonical databases, run migrations, backfill or relabel legacy rows, submit/cancel/redeem venue orders, publish reports, or authorize live unlock. It is source/test enforcement only.

## Phase 0 - Repo-Reconstructed Map

Path for this wave:

`position_current row -> src/state/db.py query_portfolio_loader_view -> PortfolioState load -> monitor/report/risk consumers`

Authority surfaces:

- Canonical projection query: `src/state/db.py::query_portfolio_loader_view`.
- Position object loader: `src/state/portfolio.py::_position_from_projection_row` already preserves raw `last_monitor_prob` and `last_monitor_edge`.
- Riskguard has a separate duplicate row loader and remains out of scope because topology did not admit `src/riskguard/riskguard.py` for this wave.

## Phase 1 - Boundary Selection

Candidates after Wave29:

| Boundary | Live-money relevance | Material values | Bypass/legacy risk | Patch safety |
| --- | --- | --- | --- | --- |
| `position_current` -> portfolio loader view | Can corrupt monitor/report/risk inputs | `last_monitor_prob`, `last_monitor_edge` | `NULL` was coerced to numeric `0.0` in `src/state/db.py` | Safe source/test repair |
| riskguard duplicate loader | Can corrupt risk posture interpretation | same fields | separate `float(row or 0.0)` in `src/riskguard/riskguard.py` | Defer; route blocked this file |
| historical projection rows | Can contain legacy zeros | physical DB rows | requires audit/relabel/backfill | Operator decision required |

Selected: `position_current` -> portfolio loader view because topology admitted `src/state/db.py` and tests, and this removes the central canonical read-model zero-fill.

## Phase 2 - Material Value Lineage

| Value | Real object denoted | Origin | Source authority | Evidence class | Unit/side/time | Transform | Persistence | Downstream consumers | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `position_current.last_monitor_prob` | Last monitor probability if one exists | canonical projection | canonical DB projection | prior monitor evidence | native held-side probability | read from row | `position_current` | portfolio loader | Broken if `NULL` becomes `0.0` |
| `position_current.last_monitor_edge` | Last monitor edge if one exists | canonical projection | canonical DB projection | prior monitor derived evidence | native edge | read from row | `position_current` | portfolio loader | Broken if `NULL` becomes `0.0` |

## Phase 3 - Failure Classification

### W30-F1 - Missing monitor probability/edge collapses to numeric zero in portfolio loader view

Severity: S1. It can corrupt report/replay/risk interpretation by making unknown monitor evidence indistinguishable from a real 0% probability or 0 edge.

Object meaning that changes:

`NULL` means no monitor probability/edge evidence. `0.0` is a real probability/edge value. The loader converted missing evidence into a real value.

Boundary:

`position_current` row -> `query_portfolio_loader_view` position dict.

Code path:

- `src/state/db.py::query_portfolio_loader_view` uses `float(row["last_monitor_prob"] or 0.0)` and `float(row["last_monitor_edge"] or 0.0)`.

Economic impact:

Downstream diagnostics and monitor/report logic can consume fabricated zero-valued evidence as if it came from a monitor cycle.

Reachability:

Active canonical read-model path. Historical rows are not modified by this wave.

## Phase 4 - Repair Design

Invariant restored:

Missing monitor probability/edge remains missing across the canonical projection read boundary. Only finite stored values become numeric read-model values.

Durable mechanism:

- Use a finite-or-None coercion for monitor read-model fields.
- Add relationship tests proving a `NULL` projection remains `None` in loaded position objects and a finite projection still loads as numeric.

## Phase 5 - Verification Plan

- Focused loader relationship tests.
- Existing db/runtime projection tests around `position_current`.
- Compile touched files.
- Planning-lock and map-maintenance closeout.

## Implemented Repair

- `src/state/db.py`
  - `query_portfolio_loader_view` now preserves missing `last_monitor_prob` and `last_monitor_edge` as `None` instead of coercing `NULL`/missing/non-finite values to numeric `0.0`.
  - Added finite-or-None coercion for these monitor evidence fields.
- `tests/test_runtime_guards.py`
  - Existing `position_current` loader relationship fixture now asserts `NULL` monitor probability/edge remain `None` on loaded `Position`.
  - Updated local monkeypatch fixtures to accept `write_class` keyword noise from current DB connection API.
  - Minimized unrelated fixture noise in the same admitted file: transfer evidence fixture now supplies time-blocked OOS cohort identity, empty canonical portfolio expectation matches current loader semantics, and `position_events` fixture includes required `env`.

## Verification Results

- `python3 scripts/topology_doctor.py --navigation --task "pricing semantics authority cutover: position_current read model must preserve missing last_monitor_prob and last_monitor_edge instead of coercing to zero" --intent modify_existing --write-intent edit --files src/state/db.py tests/test_db.py tests/test_runtime_guards.py` -> admitted.
- `python3 scripts/topology_doctor.py --navigation --task "create operation planning packet for object meaning invariance wave30 position_current monitor probability read model authority" --intent create_new --write-intent add --files docs/operations/task_2026-05-08_object_invariance_wave30/PLAN.md` -> admitted.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py tests/test_runtime_guards.py tests/test_db.py docs/operations/task_2026-05-08_object_invariance_wave30/PLAN.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave30/PLAN.md` -> pass.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py tests/test_runtime_guards.py tests/test_db.py docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave30/PLAN.md docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave30/PLAN.md` -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files src/state/db.py tests/test_runtime_guards.py tests/test_db.py docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave30/PLAN.md docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/state/db.py tests/test_runtime_guards.py tests/test_db.py` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_runtime_guards.py::test_load_portfolio_prefers_position_current_when_projection_exists tests/test_runtime_guards.py::test_load_portfolio_reads_token_identity_from_position_current tests/test_runtime_guards.py::test_load_portfolio_treats_empty_projection_as_canonical_empty tests/test_runtime_guards.py::test_load_portfolio_treats_empty_projection_as_canonical_despite_legacy_json -q --tb=short` -> `4 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_runtime_guards.py::test_transfer_sigma_uses_fully_scoped_live_evidence tests/test_runtime_guards.py::test_load_portfolio_backfills_strategy_key_from_legacy_strategy tests/test_runtime_guards.py::test_load_portfolio_reads_recent_exits_from_authoritative_settlement_rows tests/test_runtime_guards.py::test_load_portfolio_prefers_position_current_when_projection_exists -q --tb=short` -> `4 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_runtime_guards.py -q --tb=short` -> `232 passed, 2 skipped`.

## Downstream Sweep

- Central canonical portfolio loader view no longer fabricates monitor probability/edge zeros for missing evidence.
- Existing `src/state/portfolio.py::_position_from_projection_row` already forwards row values without zero-filling these fields.
- Residual: `src/riskguard/riskguard.py` has a separate duplicate loader that still coerces missing monitor fields to `0.0`; topology did not admit that file in this wave.
- Historical physical rows and decision artifacts were not audited/backfilled/relabelled.

## Critic Loop

- Bundled Wave29/Wave30 critic verdict: APPROVE.
  - Confirmed `query_portfolio_loader_view()` preserves missing/non-finite monitor evidence as `None`.
  - Confirmed `src/state/portfolio.py::_position_from_projection_row` forwards those values without re-zeroing.
  - Confirmed tests prove a producer/read-model boundary relationship (`NULL -> None`) rather than a function-only snapshot.
  - Residuals outside this wave: riskguard duplicate loader, historical physical rows/artifacts, already-numeric legacy/default `0.0` values, and `MonitorResult` type annotation cleanup.

## Stop Conditions

Stop and request operator decision if repair requires:

- DB migration/backfill/relabel;
- touching riskguard duplicate loader without admitted route;
- changing portfolio dataclass defaults globally;
- publishing reports or promoting replay/diagnostic rows into authority.
