# Object-Meaning Invariance Wave 14 Plan

## Scope

Boundary selected: verified settlement authority -> `strategy_health` realized settlement metrics -> RiskGuard/status/report consumers.

This wave is a read-model authority repair. It does not authorize live unlock, live venue side effects, production DB mutation, schema migration, settlement harvest, redemption, backfill, legacy data relabeling, calibration retrain, risk-policy changes, or report publication.

## Route Evidence

- Root `AGENTS.md`, `src/state/AGENTS.md`, and `src/riskguard/AGENTS.md` were read.
- `python3 scripts/topology_doctor.py --task-boot-profiles` returned `topology check ok`.
- Initial route for `verified settlement authority to strategy_health realized PnL` without a profile phrase returned `profile: generic` and `advisory_only` because `src/state/db.py` is high fanout and file-only evidence is ambiguous.
- Typed route with `object-meaning settlement authority cutover Wave14 verified settlement authority to strategy_health realized PnL and RiskGuard learning/report metrics` selected `object meaning settlement authority cutover` and admitted `src/state/db.py`, `tests/test_db.py`, `tests/test_riskguard.py`, this plan, and `docs/operations/AGENTS.md`. It rejected `architecture/improvement_backlog.yaml`; any backlog entry needs the direct feedback capsule route.
- The downstream contamination sweep found an active RiskGuard bypass in `_current_mode_realized_exits()`. A widened route with `remove outcome_fact realized-PnL fallback bypass` admitted `src/riskguard/riskguard.py` plus the existing Wave14 test and packet surfaces.
- The same route blocked `tests/test_k5_slice_k.py`; the repair preserved the helper's legacy default-call compatibility and added Wave14 relationship tests in `tests/test_riskguard.py`.
- First critic verdict was `REVISE`: (1) RiskGuard could replace the 30d `strategy_health` realized PnL object with unwindowed latest-50 settlement exits, (2) `status_summary` overwrote `strategy_health` settlement fields with learning-surface fields, and (3) missing settlement-authority tables looked like empty truth.
- A status-specific route using `object meaning operator status bankroll semantics` admitted `src/observability/status_summary.py`, `tests/test_pnl_flow_and_audit.py`, and this plan. The settlement-authority route rejected those files, so this wave records a real cross-profile compatibility issue.

## Candidate Boundaries

| Candidate | Live-money relevance | Values crossing | Downstream consumers | Stale/bypass risk | Repair scope |
| --- | --- | --- | --- | --- | --- |
| `outcome_fact` -> `strategy_health` realized settlement metrics | RiskGuard writes `strategy_health` into risk details; status/report surfaces consume realized PnL and win rates | `strategy_key`, `settled_at`, `pnl`, `outcome` | RiskGuard `details_json`, status summary, strategy health snapshots, operator reports | `outcome_fact` has no settlement authority/provenance fields, so unverified rows can become realized economic truth | Safely scoped: change read model to use verified authoritative settlement rows |
| canonical settlement rows -> RiskGuard `recent_exits` fallback and report/replay | RiskGuard can rehydrate realized PnL from `PortfolioState.recent_exits`; diagnostics can also consume recent exits | `pnl`, `outcome`, settlement authority, temperature metric, token identity | RiskGuard details, `profit_validation_replay`, `equity_curve`, diagnostics | `recent_exits` only retains a subset; stale metadata `recent_exits` can bypass corrected `strategy_health` | Active RiskGuard fallback repaired; replay/equity diagnostics deferred |
| canonical settlement rows -> RiskGuard brier/accuracy | Risk level can change behavior | `p_posterior`, `outcome`, `metric_ready` | RiskGuard brier/settlement quality levels | already filters `metric_ready_rows` and degrades malformed rows | Already appears guarded |
| settlement/result -> calibration retrain/learning corpus | Can corrupt future model learning | settlement truth, decision snapshot identity | calibration retrain, backtest economics | route may require F2/calibration profile and operator-gated retrain surfaces | Defer; likely separate high-risk route |

Selected: `outcome_fact` -> `strategy_health` realized settlement metrics, because it is active, protective/report-facing, and safely repairable by consuming already-normalized verified settlement rows.

## Lineage Table

| Value | Real object denoted | Origin | Authority/evidence | Unit/side | Time basis | Transform | Persistence | Consumers | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `outcome_fact.pnl` | Realized trade PnL | `src/state/db.py::log_outcome_fact` and settlement writers | canonical table but no settlement authority/source columns | USD PnL | exit/settlement time | summed in `refresh_strategy_health` | `strategy_health.realized_pnl_30d` | RiskGuard/status/report | broken for authority |
| `outcome_fact.outcome` | Win/loss outcome | `log_outcome_fact` | no settlement authority/source columns | binary outcome | settlement time | win count in `refresh_strategy_health` | `strategy_health.win_rate_30d` | RiskGuard/status/report | broken for authority |
| `query_authoritative_settlement_rows().pnl` | Realized PnL with settlement authority classification | `position_events` normalized by `_normalize_position_settlement_event`, legacy fallback from decision_log | VERIFIED settlement truth required for `metric_ready=True`; legacy fallback degraded | USD PnL | settled_at/event time | filter `metric_ready` and 30d window before aggregation | `strategy_health.realized_pnl_30d` after repair | RiskGuard/status/report | intended authority |
| `query_authoritative_settlement_rows().outcome` | Verified outcome for metric/report measurement | same | `metric_ready=True` only when authority=VERIFIED, source in accepted truth sources, temperature_metric high/low, and settlement_value present | binary outcome | settlement time | win count after repair | `strategy_health.win_rate_30d` | RiskGuard/status/report | intended authority |
| `strategy_health.realized_pnl_30d` | Strategy realized PnL over trailing window | `refresh_strategy_health` | should be verified settlement rows only | USD PnL | refresh `as_of`, settlement window | aggregate by strategy | `strategy_health` table | `query_strategy_health_snapshot`, RiskGuard details, status | broken before repair |
| `PortfolioState.recent_exits[].pnl` in RiskGuard tick | Current-mode realized PnL used as fallback for `risk_state.details_json.realized_pnl` | `src/riskguard/riskguard.py::_current_mode_realized_exits` | before repair: `outcome_fact` primary, chronicle fallback | USD PnL | settled/exit time | copied into `portfolio.recent_exits`, summed when `strategy_health` total is 0 | `risk_state.details_json` | status/report/RiskGuard audit | broken bypass before repair |
| `status.strategy.*.settlement_*` | Operator-facing 30d strategy settlement metrics | `src/observability/status_summary.py` from `query_strategy_health_snapshot()` | derived from `strategy_health` | USD/count/rate | status generation over `strategy_health` 30d window | copied into status summary | status JSON | operator reports | broken before repair when overwritten |
| `status.strategy.*.learning_settlement_*` | Operator-facing learning/regime settlement metrics | `query_learning_surface_summary()` | learning surface from metric-ready settlement rows | USD/count/rate | current regime or learning-surface default | namespaced after repair | status JSON | operator reports | explicit transform after repair |

UNKNOWN: `outcome_fact` may still be useful as a diagnostic durable outcome table, but without authority fields it cannot authorize strategy health realized economics.

## Findings

### W14-F1 - S1 Active

Object meaning changed: unproven `outcome_fact` rows become verified realized settlement economics in `strategy_health`.

Boundary: `outcome_fact` -> `refresh_strategy_health()` -> `strategy_health` -> RiskGuard/status/report consumers.

Code path: `src/state/db.py::refresh_strategy_health` queries `outcome_fact` directly, counts settled rows, sums `pnl`, and derives `win_rate_30d` without checking settlement authority, truth source, temperature metric, or `metric_ready`.

Economic impact: RiskGuard details and operator status can report realized PnL/win-rate from rows that lack verified settlement evidence. This can corrupt risk interpretation, strategy health, reports, and future learning/promotion evidence if reused downstream.

Reachability: active. `src/riskguard/riskguard.py::tick` calls `refresh_strategy_health()` and `query_strategy_health_snapshot()`, then writes `total_realized_pnl` and strategy-health metadata into `risk_state.details_json`.

Repair invariant: `strategy_health` realized settlement metrics must aggregate only `query_authoritative_settlement_rows()` rows with `metric_ready=True` inside the 30d window. Legacy/degraded `outcome_fact` rows may remain stored but cannot become realized economics authority.

### W14-F2 - S3 Active Topology Compatibility

The semantic task initially routed to `generic` because high-fanout `src/state/db.py` cannot select a profile from file evidence. Adding the explicit `object-meaning settlement authority cutover` phrase admitted the safe route. This is workable but should be recorded: object-meaning waves need semantic task phrases, not only file paths, when the source file is high fanout.

### W14-F3 - S1 Active

Object meaning changed: authority-less `outcome_fact` realized PnL could still re-enter RiskGuard after the `strategy_health` cutover.

Boundary: `outcome_fact` -> `_current_mode_realized_exits()` -> `PortfolioState.recent_exits` -> RiskGuard `risk_state.details_json.realized_pnl`.

Code path: `src/riskguard/riskguard.py::tick` queried authoritative settlement rows, but then called `_current_mode_realized_exits()`, whose primary path read `outcome_fact`. Later, if `strategy_health` summed to zero, RiskGuard summed `portfolio.recent_exits`, so the authority-less PnL could bypass the repaired `strategy_health` semantics.

Economic impact: operator-visible RiskGuard details could report realized PnL from rows without settlement authority even after `strategy_health` was fixed, undermining the repair and any status/report consumers that trust RiskGuard details.

Repair invariant: the active RiskGuard tick path must derive realized exits from the same `query_authoritative_settlement_rows()` result set and must not fall back to `outcome_fact` when settlement rows are degraded or absent.

### W14-F4 - S2 Reduced-Schema Reachable

Object meaning changed: absence of the legacy fallback table was treated as an exceptional DB shape instead of "no settlement authority rows available".

Boundary: `query_authoritative_settlement_rows()` -> `refresh_strategy_health()` under kernel-only policy/test DBs.

Code path: no stage-level settlement rows caused `query_authoritative_settlement_rows()` to unconditionally call `query_legacy_settlement_records()`, which reads `decision_log`. Kernel-only DBs have `position_events`/`position_current` but not always `decision_log`, so RiskGuard strategy-health refresh could throw before producing a non-promoting empty settlement metric set.

Economic impact: not a live unlock risk in normal `init_schema` DBs, but it weakens fail-closed/degraded behavior for protective read models and masked the authority cutover in tests.

Repair invariant: missing settlement authority surfaces may yield no realized settlement metrics, but must not make `refresh_strategy_health()` promote legacy `outcome_fact` or crash before recording open-exposure health.

### W14-F5 - S2 Active Time-Basis Drift

Object meaning changed: settlement event time could be compared as a string instead of a parsed UTC instant.

Boundary: `query_authoritative_settlement_rows().settled_at` -> `refresh_strategy_health()` 30-day realized metric window.

Code path: `refresh_strategy_health()` compared `settled_at` directly with `_shift_iso_timestamp()` output. Zeus settlement rows can carry `Z`, `+00:00`, or SQLite-style naive UTC strings, and the repo already has `_parse_iso_timestamp()` to normalize those forms.

Economic impact: edge-case settlement rows on the cutoff boundary could be omitted from or included in realized PnL depending on timestamp spelling, not actual time.

Repair invariant: strategy-health settlement windows compare parsed UTC instants; unparsable settlement times fail closed by excluding the row.

### W14-F6 - S1 Active

Object meaning changed: RiskGuard could replace the 30-day `strategy_health.realized_pnl_30d` value with an unwindowed `PortfolioState.recent_exits` sum.

Boundary: `strategy_health` -> `risk_state.details_json.realized_pnl`.

Code path: after querying `strategy_health`, `tick()` summed `portfolio.recent_exits` when total realized PnL was zero. After the Wave14 cutover those exits were authority-filtered, but still came from the latest settlement rows without the same 30-day validity interval.

Economic impact: status/report RiskGuard details could show a valid settlement-authority object under the wrong time basis.

Repair invariant: `risk_state.details_json.realized_pnl` is the 30-day strategy-health realized PnL object only. Recent exits are not a fallback for that field.

### W14-F7 - S1 Active

Object meaning changed: `status_summary.strategy.*.settlement_count`, `settlement_pnl`, and `settlement_accuracy` were first populated from `strategy_health` and then overwritten by `query_learning_surface_summary()`.

Boundary: `strategy_health` -> status summary strategy fields -> learning surface merge.

Code path: `status_summary.write_status()` wrote `settlement_*` fields from `strategy_health`, then the learning merge reused the same field names for learning/regime metrics.

Economic impact: operator-visible strategy settlement metrics could silently change window/source from 30-day strategy-health economics to learning-surface/regime metrics.

Repair invariant: status summary preserves `settlement_*` for `strategy_health` 30-day metrics and uses `learning_settlement_*` for learning-surface metrics with explicit source/window metadata.

### W14-F8 - S2 Reduced-Schema Reachable

Object meaning changed: missing settlement-authority tables were indistinguishable from a real empty settlement set.

Boundary: settlement authority surfaces -> `refresh_strategy_health()` -> RiskGuard details.

Code path: when `position_events` was absent and `decision_log` was absent or empty, `query_authoritative_settlement_rows()` returned `[]`; `refresh_strategy_health()` and RiskGuard reported zero realized settlement economics without an explicit authority-surface degradation.

Economic impact: reduced-schema or broken protective read models could look like true no-settlement state instead of missing authority.

Repair invariant: strategy-health refresh reports `settlement_authority_missing_tables`, uses `refreshed*_degraded` statuses when authority surfaces are missing/degraded, and RiskGuard details set `realized_degraded=True` when that authority gap is present.

## Repair Plan

1. Add a failing relationship test proving `refresh_strategy_health()` ignores authority-less `outcome_fact` rows and aggregates only verified settlement rows from `query_authoritative_settlement_rows()`.
2. Update the existing strategy-health test to seed verified canonical settlement events for realized PnL/win-rate, not bare `outcome_fact` rows.
3. Modify `refresh_strategy_health()` to build settlement metrics from authoritative settlement rows:
   - call `query_authoritative_settlement_rows(conn, limit=None)`;
   - filter `metric_ready=True`, non-null `settled_at`, and `settled_at >= settled_cutoff`;
   - group by settlement row `strategy`;
   - compute count, realized PnL, wins/win rate.
4. Keep `outcome_fact` as optional diagnostic storage; do not mutate schema or relabel rows.
5. Cut the RiskGuard realized-exit bypass by passing the already-loaded authoritative settlement rows into the active tick path and blocking `outcome_fact` fallback for that runtime path.
6. Guard missing `position_events`/`position_current`/`decision_log` surfaces in `query_authoritative_settlement_rows()` so no verified rows means no settlement metrics, not a legacy promotion or exception.
7. Compare settlement-window times via `_parse_iso_timestamp()`, not raw string ordering.
8. Remove the RiskGuard realized-PnL fallback from `recent_exits`; expose `realized_pnl_source=strategy_health.realized_pnl_30d` and `realized_pnl_window_days=30`.
9. Namespace learning-surface settlement fields in `status_summary` as `learning_settlement_*` and preserve `settlement_*` as strategy-health 30-day fields.
10. Run focused tests, py_compile, topology gates, contamination sweep, and critic review before advancing.

## Verification Plan

- `pytest -q -p no:cacheprovider tests/test_riskguard.py -k 'strategy_health or settlement_authority'`
- `pytest -q -p no:cacheprovider tests/test_db.py -k 'authoritative_settlement_rows or strategy_health'`
- `python3 -m py_compile src/state/db.py src/riskguard/riskguard.py tests/test_riskguard.py`
- `python3 scripts/topology_doctor.py --schema`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave14/PLAN.md`
- Static sweep for `strategy_health` realized PnL consumers and any remaining direct `outcome_fact` aggregation into protective/report/learning authority.

## Implementation Notes

- `src/state/db.py::refresh_strategy_health()` now builds settlement metrics from `query_authoritative_settlement_rows(conn, limit=None)`, filters to `metric_ready=True`, applies the 30-day settlement window, and aggregates by strategy.
- `src/state/db.py::refresh_strategy_health()` parses both cutoff and settlement row times through `_parse_iso_timestamp()` before applying the 30-day window.
- `src/state/db.py::refresh_strategy_health()` reports `settlement_authority_missing_tables` and `settlement_degraded_rows`; missing/degraded authority surfaces produce `refreshed_degraded` or `refreshed_empty_degraded`.
- `src/state/db.py::query_authoritative_settlement_rows()` now returns an empty set when the canonical event/projection surface or legacy `decision_log` fallback surface is absent; it does not use `outcome_fact` as a substitute.
- `src/riskguard/riskguard.py::tick()` now passes the authoritative settlement rows into `_current_mode_realized_exits()` and replaces stale metadata `recent_exits` with that result set, even when empty. In this active path, realized exits come only from metric-ready settlement rows; degraded rows return no exits with `realized_degraded=True`.
- `src/riskguard/riskguard.py::tick()` no longer uses recent exits as a realized-PnL fallback. RiskGuard realized PnL is explicitly sourced from `strategy_health.realized_pnl_30d`.
- `src/observability/status_summary.py` preserves `settlement_*` fields from `strategy_health` and writes learning-surface metrics to `learning_settlement_*`.
- `_current_mode_realized_exits()` keeps its old default-call behavior for legacy unit compatibility only. The live tick path no longer uses that default.

## Verification Results

- `python3 -m py_compile src/state/db.py src/riskguard/riskguard.py src/observability/status_summary.py tests/test_riskguard.py tests/test_pnl_flow_and_audit.py` - PASS
- `pytest -q -p no:cacheprovider tests/test_riskguard.py -k 'strategy_health or settlement_authority or realized_exits or metadata_recent_exits'` - PASS, 12 passed
- `pytest -q -p no:cacheprovider tests/test_k5_slice_k.py -k 'realized_exits'` - PASS, 4 passed
- `pytest -q -p no:cacheprovider tests/test_riskguard.py` - PASS, 57 passed
- `pytest -q -p no:cacheprovider tests/test_db.py -k 'strategy_health or authoritative_settlement_rows or query_settlement_events or learning_surface_summary'` - PASS, 4 passed, 9 skipped
- `pytest -q -p no:cacheprovider tests/test_k5_slice_k.py` - PASS, 9 passed
- `pytest -q -p no:cacheprovider tests/test_phase10b_dt_seam_cleanup.py -k 'status or strategy_health or bankroll_semantics'` - PASS, 7 passed
- Process-local dependency-stub run for `tests/test_pnl_flow_and_audit.py -k 'riskguard_does_not_promote_legacy_settlement_pnl or status_summary_does_not_promote_legacy_realized_truth or status_strategy_merges_learning_surface or status_summary or strategy_health'` - PASS, 4 passed. Direct collection of this file still fails in the local environment without `sklearn`; a full process-local stub run remains red on unrelated evaluator/harvester tests.
- `python3 scripts/topology_doctor.py --task-boot-profiles` - PASS
- `python3 scripts/topology_doctor.py --schema` - PASS
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave14/PLAN.md` - PASS
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...` - PASS
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files ...` - PASS
- `git diff --check` - PASS
- Static sweep `rg -n "FROM outcome_fact|_current_mode_realized_exits\\(|realized_pnl_source|learning_settlement_|settlement_authority_missing_tables|refreshed_empty_degraded|refreshed_degraded" src/state/db.py src/riskguard/riskguard.py src/observability/status_summary.py tests/test_riskguard.py tests/test_pnl_flow_and_audit.py ...` - reviewed; active `tick()` path passes `settlement_rows`; remaining `outcome_fact` reads are logging/diagnostic, helper default-call compatibility, or separate learning/replay candidates.

## Critic Review

- First critic verdict: `REVISE`. Findings were unwindowed RiskGuard realized-PnL fallback, status-summary settlement field overwrite by learning metrics, and missing settlement-authority surfaces reported as empty truth.
- Second critic verdict: `APPROVE`. It found no remaining scoped Wave14 findings, confirmed the three REVISE findings were resolved, and explicitly stated the approval is not live unlock approval.

## Contamination Sweep

- Monitor/exit: no direct monitor/exit consumer of `strategy_health.realized_pnl_30d` found; RiskGuard realized-exit fallback was the active bypass and is repaired for `tick()`.
- Settlement: `query_authoritative_settlement_rows()` remains the settlement authority adapter; missing legacy fallback tables now produce no rows rather than a legacy promotion.
- Replay/learning/scripts: `outcome_fact` remains referenced by replay, backtest, diagnostics, and truth-surface scripts. Those are not repaired in this wave because the admitted scope is RiskGuard/read-model settlement authority. They are recorded as candidate future boundaries and must not be treated as corrected learning authority until separately routed.
- Reporting/status: status summary reads `strategy_health` and RiskGuard details; the two active `outcome_fact` routes into those surfaces are repaired.

## Compatibility Notes

- `outcome_fact` currently has no authority columns, so the safe repair is read-side filtering through existing authoritative settlement normalization rather than schema migration.
- If future learning/calibration promotion requires outcome facts, it needs a separate schema/provenance route or must consume `query_authoritative_settlement_rows()` directly.
- Topology admitted the active RiskGuard bypass only after the task phrase explicitly named the bypass. It rejected the legacy K5 test file, so the implementation preserved default-call compatibility while adding Wave14 tests on admitted surfaces.
- Topology rejected `status_summary.py` under the settlement-authority profile even though critic found an active report-facing continuation of the same object. The status repair required a separate operator-status profile whose name is bankroll-specific, not settlement-specific.
