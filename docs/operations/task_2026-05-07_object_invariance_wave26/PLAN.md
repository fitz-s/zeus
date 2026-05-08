# Object Invariance Wave 26 - Canonical Position Event Environment Authority

Status: IMPLEMENTED FOR LOCAL SOURCE/TEST SLICE, CRITIC APPROVED, NOT LIVE UNLOCK, NOT DB MUTATION AUTHORITY

Created: 2026-05-07
Last reused or audited: 2026-05-07
Authority basis: root AGENTS.md object-meaning invariance goal; docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md remaining-mainline ledger; src/state/AGENTS.md; src/engine/AGENTS.md

## Scope

Repair one boundary class:

`runtime lifecycle event -> canonical position_events persistence cohort`

This wave does not mutate live/canonical databases, run backfills, relabel
legacy rows, harvest settlement, publish reports, or authorize live unlock. It
only changes source/test enforcement for future canonical event writes.

## Topology Record

- `python3 scripts/topology_doctor.py --navigation --task "create operation planning packet for object-meaning invariance Wave26 canonical position event env authority" --intent create_new --write-intent add --files docs/operations/task_2026-05-07_object_invariance_wave26/PLAN.md docs/operations/AGENTS.md`
  - Result: `scope_expansion_required`.
  - `PLAN.md` admitted; `docs/operations/AGENTS.md` rejected although registry maintenance is required later.
- `python3 scripts/topology_doctor.py --task-boot-profiles`
  - Result: failed before usable boot because `architecture/task_boot_profiles.yaml:agent_runtime` references missing `architecture/topology_schema.yaml`.
- Implementation route attempts:
  - Generic route stayed advisory for the cross-module packet.
  - `object meaning settlement authority cutover` admitted `src/engine/lifecycle_events.py`, `tests/test_db.py`, and this packet, but rejected `src/state/ledger.py`, `src/state/projection.py`, `architecture/2026_04_02_architecture_kernel.sql`, and supporting schema/savepoint tests.
  - Wave26 REVISE route for explicit `Position.env` authority stayed advisory and rejected the core source/test files, even though critic found the same boundary class. Proceeded under the active packet and recorded the mismatch.
  - Wave26 second REVISE route for portfolio-loader env preservation also stayed advisory and rejected `src/state/db.py`, `src/state/portfolio.py`, `tests/test_db.py`, and this packet because high-fanout `src/state/db.py` could not select a profile. Proceeded under the same active boundary packet.
  - Proceeded under the active user instruction to bear topology's current housekeeping/object-boundary limitations, with this packet recording the compatibility conflict.

Topology compatibility finding: plan-packet creation still cannot admit its own
operations registry row in the same route, and semantic boot remains blocked by
the missing topology schema reference.

## Boundary Selection

Candidates:

| Boundary | Live-money relevance | Material values | Bypass/legacy risk | Patch safety |
| --- | --- | --- | --- | --- |
| Monitor/exit probability authority | Can trigger live exits | `fresh_prob`, `fresh_prob_is_fresh`, `current_market_price`, `best_bid`, held-side direction | Existing live path appears guarded by `ExitContext`; legacy API is diagnostic-only | No active failure found; no patch selected |
| Trade fact economics downstream | Can affect exposure, readiness, learning | `state`, `filled_size`, `fill_price` | Wave 25 and critic pass covered known consumers | No active remaining consumer found |
| Canonical position event env authority | Can decide whether lifecycle/settlement rows are live truth or replay/test/shadow evidence | `position_events.env`, event payload `env`, position `env`, reader env filters | Missing event env can still be converted to `live` before persistence for non-SETTLED events; direct insert trigger absent | Safe as future-write fail-closed enforcement; no existing DB mutation |

Selected: canonical position event env authority. It is the earliest shared
cohort boundary after lifecycle transitions and before settlement, portfolio
load, risk, reporting, replay, and learning readers.

## Material Value Lineage

| Value | Real object denoted | Origin | Source authority | Evidence class | Unit/side/time | Transform | Persistence | Consumers | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `position.env` | Cohort that created/owns a runtime position object | live materialization, DB projection loader, tests/replay fixtures | Runtime entrypoint or projection source | persistence authority | cohort at lifecycle event time | normalized to enum | event builder payload | canonical lifecycle events | Preserved when explicit; pre-repair default-live omission was broken; post-repair omitted env is `unknown_env` and rejected by canonical builders |
| event payload `env` | Cohort of a specific canonical event | `src/engine/lifecycle_events.py` builders or direct append caller | Event producer | canonical event authority | event occurrence time | normalized by ledger | `position_events.env` | projection, settlement readers, risk, portfolio load, learning/reporting | Broken when missing non-SETTLED env defaults to live |
| `position_events.env` | Cohort filter for canonical event truth | `append_many_and_project()` or direct SQL | DB append boundary | persistence authority | event occurrence time | SQL CHECK / trigger | canonical DB | all downstream canonical readers | Ambiguous if nullable rows can be newly inserted |

## Finding

### W26-F1 - Missing canonical lifecycle event cohort can become live truth

Classification: S1 active source-level persistence failure; S2 for legacy/direct
diagnostic inserts; no existing live DB row rewrite authorized.

Object meaning that changes:

A missing cohort means "the event has not proven whether it is live, test,
replay, backtest, or shadow." The current append path can transform that to
`live` for every non-SETTLED event, making an unknown-cohort lifecycle event
look like live canonical truth.

Boundary:

`lifecycle event builder / direct append caller -> append_many_and_project() ->
position_events.env`.

Code paths:

- `src/state/ledger.py::append_many_and_project` defaults missing non-SETTLED
  `env` to `DEFAULT_POSITION_EVENT_ENV`.
- `src/engine/lifecycle_events.py::_position_env` defaults missing position env
  to the same live value.
- `src/state/portfolio.py::Position.env` defaults omitted constructor env to
  `live`, so builders cannot distinguish explicit live authority from missing
  cohort authority.
- Fresh schema permits nullable `position_events.env`; no direct-insert trigger
  blocks future SQL inserts without env.

Economic impact:

Downstream portfolio, risk, settlement, reporting, replay, and learning readers
filter or reason over `position_events.env`. If the append boundary silently
stamps missing cohort as live, corrected non-live/test/replay lifecycle facts can
enter the live truth cohort without an explicit transition.

Reachability:

Live materialization passes `env=get_mode()` and `Position` currently defaults to
`live`, so normal live runtime events are expected to remain writable. The
danger is the shared append/schema seam: tests, replay, future tools, or direct
SQL can omit env and still create live-looking events unless the boundary fails
closed.

## Repair Design

Invariant restored:

Every new canonical `position_events` row must carry an explicit valid cohort
before persistence. Missing cohort is not live truth, and no source/test/replay
producer may acquire live authority by omission.

Durable mechanisms:

- Make `append_many_and_project()` reject missing `env` for all event types.
- Make lifecycle event builders reject positions that lack a valid `env` instead
  of defaulting to live.
- Make `Position` default missing constructor env to a non-authoritative
  sentinel (`unknown_env`) so omitted cohort authority cannot masquerade as
  explicit live authority before reaching the builder.
- Make portfolio-loader rows preserve explicit canonical event/projection env
  when available, and otherwise carry `unknown_env`; loader rows must not stamp
  `get_mode()` onto a projection that lacks cohort authority.
- Add a direct SQL trigger to the architecture kernel so fresh/existing
  bootstrapped schemas reject future `position_events` inserts with missing env.
- Keep legacy audit compatibility writes independent from new authority rows so
  old `position_events` shapes cannot suppress `rescue_events_v2`.
- Keep legacy existing rows untouched; a physical DB audit/backfill/relabel
  requires a separate operator-approved dry-run and rollback plan.
- Add relationship tests proving missing `env` fails closed at the append and
  direct SQL boundaries.

## Verification Plan

- `py_compile` changed source/tests.
- Focused tests:
  - canonical append rejects any event missing `env`;
  - direct SQL insert without env fails under the kernel trigger;
  - existing settlement env filters from Wave 24 still isolate live/replay rows;
  - live lifecycle builder events still carry explicit live env.
- Planning-lock and map-maintenance checks after changed files are known.
- Critic review before advancing to the next wave.

## Implemented Repair

- `src/state/ledger.py`
  - `append_many_and_project()` now rejects missing `env` for every canonical
    event type before opening the write savepoint.
  - Removed the non-SETTLED default-to-live path.
- `src/engine/lifecycle_events.py`
  - Canonical event builders now require a position with a valid `env` rather
    than defaulting a missing position cohort to live.
- `src/state/projection.py`
  - Removed the `DEFAULT_POSITION_EVENT_ENV` live default constant from the
    canonical event cohort surface.
- `src/state/portfolio.py`
  - `Position.env` now defaults to `POSITION_ENV_UNKNOWN = 'unknown_env'`
    instead of `live`; runtime/live constructors and tests that intend live
    authority must pass `env='live'` explicitly.
  - JSON/projection fallback loaders preserve missing env as `unknown_env`
    rather than inventing live authority.
- `src/state/db.py`
  - Legacy `log_trade_entry()` and `log_trade_exit()` no longer default an
    arbitrary object with no `env` attribute to live.
  - `query_portfolio_loader_view()` now derives loader env from explicit
    `position_current.env` when such a schema exists, otherwise from the latest
    canonical `position_events.env` for the position, otherwise `unknown_env`.
  - Removed the `env=get_mode()` loader fallback that turned missing projection
    env into live.
- `src/state/chain_reconciliation.py`
  - The legacy `CHAIN_RESCUE_AUDIT` compatibility insert is attempted only when
    the target table has the old `payload` shape.
  - `rescue_events_v2` is written independently so canonical-schema
    incompatibility with the legacy audit row cannot suppress the structured
    authority-bearing rescue row.
- `architecture/2026_04_02_architecture_kernel.sql`
  - Fresh `position_events.env` is `NOT NULL`.
  - Added `trg_position_events_require_env` so future direct SQL inserts on
    bootstrapped/legacy schemas fail when `env` is missing or blank.
- Tests
  - Added append-boundary and direct-SQL relationship tests for missing env.
  - Added a builder-boundary relationship test proving `Position(...)` without
    explicit env fails before canonical event persistence.
  - Added a loader-boundary relationship test proving
    `query_portfolio_loader_view -> _position_from_projection_row -> lifecycle
    builder` preserves missing projection env as `unknown_env` and fails closed.
  - Added a rescue audit compatibility test proving skipped legacy
    `CHAIN_RESCUE_AUDIT` does not suppress `rescue_events_v2`.
  - Updated canonical-event fixtures that intentionally bypass builders to
    carry explicit `env='live'`.
  - Minimal verification-noise repair: RiskGuard test fake DB connectors now
    accept the current `write_class=` kwarg, and direct raw `position_events`
    fixture rows include explicit env.

## Verification Results

- `py_compile` for changed source/tests: pass.
- Focused Wave26 relationship tests:
  - `tests/test_db.py::test_lifecycle_builder_rejects_position_without_explicit_env`
  - `tests/test_db.py::test_portfolio_loader_missing_projection_env_stays_unknown_until_builder_rejects`
  - `tests/test_db.py::test_append_many_and_project_requires_env_for_canonical_position_events`
  - `tests/test_db.py::test_append_many_and_project_rejects_missing_env_for_non_settlement_events`
  - `tests/test_db.py::test_query_authoritative_settlement_rows_filters_canonical_position_events_by_env`
  - `tests/test_b063_rescue_events_v2.py::TestEmitRescueEventIntegration::test_canonical_position_events_schema_does_not_suppress_rescue_v2`
  - `tests/test_append_many_and_project_nested_savepoint.py`
  - `tests/test_architecture_contracts.py::test_position_events_direct_insert_requires_explicit_env`
  - Result after critic REVISE repair: `6 passed` for the direct focused node set and `10 passed` for the original append/schema node set before the REVISE expansion.
- `tests/test_db.py` -> `50 passed, 17 skipped`.
- Canonical ledger/schema affected suites:
  - `tests/test_append_many_and_project_nested_savepoint.py tests/test_architecture_contracts.py tests/test_exit_evidence_audit.py` -> `100 passed, 22 skipped`.
  - `tests/test_live_safety_invariants.py tests/test_chain_reconciliation_corrected_guard.py tests/test_b063_rescue_events_v2.py` -> `140 passed`.
  - `tests/test_live_safety_invariants.py` -> `115 passed`.
  - `tests/test_runtime_guards.py::test_build_exit_context_preserves_missing_best_bid_for_exit_audit tests/test_runtime_guards.py::test_orange_risk_does_not_override_incomplete_exit_context tests/test_runtime_guards.py::test_exit_dual_write_backfills_missing_entry_history_after_day0_only_canonical_event` -> `3 passed`.
  - `tests/test_pnl_flow_and_audit.py::test_inv_riskguard_prefers_canonical_position_events_settlement_source tests/test_pnl_flow_and_audit.py::test_inv_harvester_prefers_durable_snapshot_over_open_portfolio` -> `2 passed`.
  - Combined runtime/PnL/authority rerun after REVISE repairs -> `19 passed`.
  - `tests/test_riskguard.py` -> `57 passed`.
- `tests/test_authority_strict_learning.py` -> `14 passed`.
- Static default-live sweep: no source `env: str = "live"` or `getattr(..., "env", "live")` remains; the only matched source compatibility line uses `unknown_env`.
- Planning-lock with Wave26 plan evidence -> pass.
- Map-maintenance closeout -> pass.
- `git diff --check` -> pass.
- `python3 scripts/topology_doctor.py --task-boot-profiles` -> still fails because `architecture/task_boot_profiles.yaml:agent_runtime` references missing `architecture/topology_schema.yaml`.

## Critic Loop

- Critic pass 1: `REVISE`. Finding: `Position.env` still defaulted omitted
  constructor env to `live`, so lifecycle builders could not distinguish an
  explicit live cohort from missing cohort authority.
- Repair response: introduced `POSITION_ENV_UNKNOWN`, made omitted constructor
  env non-authoritative, added builder-boundary relationship coverage, and
  updated intentional-live test fixtures to pass `env='live'`.
- Critic pass 2: `REVISE`. Finding: `query_portfolio_loader_view()` still
  stamped `env=get_mode()` onto `position_current` rows that lacked explicit
  projection env, so DB loader materialization could turn missing cohort into
  live before a future lifecycle builder.
- Repair response: loader env now comes from explicit projection env if present,
  latest canonical event env if present, else `unknown_env`; added
  loader-to-builder relationship coverage.
- Critic pass 3: `APPROVE`. No blocking findings after the loader repair;
  critic specifically rechecked the prior missing-env-to-live loader shape,
  rescue audit compatibility, downstream settlement/report/learning env filters,
  focused tests, static sweep, `git diff --check`, and planning-lock.

Observed verification noise:

- Pytest emits the current writer-lock informational warning about direct
  `sqlite3.connect()` sites. This is mainline noise and not repaired in this
  wave.

## Downstream Sweep Targets

- monitor/exit paths: no active monitor/exit env bypass selected in this wave.
- settlement paths: `query_settlement_events`/`query_authoritative_settlement_rows`.
- replay/report/learning paths: readers using authoritative settlement rows and
  strategy/edge/learning projections.
- legacy/compatibility/fallback paths: direct SQL inserts and tests that bypass
  builders.

## Residual Risk

This wave does not prove historical live databases are uncontaminated and does
not rewrite any existing row. Legacy rows with `env IS NULL` or default-live
history require a separate operator-approved physical-data audit.
