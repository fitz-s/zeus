# Object-Meaning Invariance Wave24 Plan

Status: IMPLEMENTED FOR LOCAL SOURCE/TEST SLICE, NOT LIVE UNLOCK, NOT DB MUTATION AUTHORITY
Date: 2026-05-07
Branch: `object-invariance-mainline-2026-05-07`
Base: `origin/main` at `94285969`

## Scope

Repair the remaining PR67 mainline boundary called out as Wave24:
canonical settlement environment authority in `position_events` and settlement
readers. This packet authorizes only local source/test changes after topology
planning-lock; it does not authorize live/prod DB writes, migration execution,
backfill, relabeling, settlement harvest, redemption, report publication, or
promotion of replay/report rows into live truth.

## Current Alignment

Root `AGENTS.md` still defines the money path as:
`contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`.

The PR67 closeout ledger marks Waves 8 and 12-21 as repaired/reviewed and marks
Wave24 as the highest-priority unrepaired mainline wave. Current source/data
facts remain planning surfaces only and are stale-sensitive; they do not by
themselves authorize current settlement source claims or live deployment.

## Topology / Route Evidence

- `--navigation` for read-only mainline continuation returned planning-only
  T3 with missing current-state packet paths and requested docs files marked
  out of scope.
- `--task-boot-profiles` failed because `architecture/task_boot_profiles.yaml`
  references missing `architecture/topology_schema.yaml`.
- `semantic-bootstrap --task-class settlement_semantics` failed because
  `scripts/topology_doctor_context_pack.py` is missing.
- `--navigation --intent create_new` for this packet admitted this `PLAN.md`
  and rejected `docs/operations/AGENTS.md`; registry changes are intentionally
  out of scope for this first slice.

These are topology compatibility findings, not semantic permission to skip
source truth checks. For implementation, run planning-lock against the exact
changed source/test files and this plan.

## Money Path Map For This Wave

Relevant path:
`settlement event producer -> position_events append -> position_current projection -> query_settlement_events -> query_authoritative_settlement_rows -> strategy_health / RiskGuard / status / replay / learning`.

Object classes:

| Object | Current authority surface | Downstream consumers |
|---|---|---|
| Canonical lifecycle event | `position_events` via `src/state/ledger.py::append_many_and_project` | settlement readers, lifecycle queries, status/report/replay helpers |
| Current position projection | `position_current` via same transaction boundary | query joins, RiskGuard, status projections |
| Settlement read model | `src/state/db.py::query_settlement_events`, `query_authoritative_settlement_rows` | `strategy_health`, RiskGuard, diagnostic scripts, replay/report/learning surfaces |
| Legacy env evidence | legacy `decision_log` and legacy schema reconciliation | compatibility fallback only |

Canonical truth hierarchy remains:
canonical DB/event truth outranks derived JSON/report/replay surfaces. Legacy
and diagnostic rows must not become live settlement, report, risk, or learning
authority unless an explicit eligibility transform says so.

## Selected Boundary

Boundary: canonical settlement event environment identity across persistence and
read-model boundaries.

Why live-money-relevant: settlement rows feed realized PnL, strategy health,
RiskGuard details, operator status, replay/report output, and calibration or
learning eligibility. If a non-live or replay settlement event is read as live,
Zeus can corrupt risk posture, performance attribution, and learning evidence.

Material values:

| Value | Meaning | Origin | Downstream | Current status |
|---|---|---|---|---|
| `position_events.event_type='SETTLED'` | settlement lifecycle fact | canonical event append | settlement readers | authority-bearing |
| `position_events.payload_json` | settlement economics/source payload | producer-specific event payload | `_normalize_position_settlement_event` | authority-bearing only if complete and verified |
| `position_events.env` | execution environment / eligibility scope | legacy `ALTER TABLE ... DEFAULT 'live'`, absent from kernel column contract | settlement filtering and compatibility callers | broken/ambiguous |
| `query_authoritative_settlement_rows(env=...)` | requested environment filter | settlement read model | RiskGuard/status/report | broken/ignored for canonical rows |

## Failure Predicate

Evidence from current source:

- `architecture/2026_04_02_architecture_kernel.sql` creates canonical
  `position_events` without `env`.
- `src/state/ledger.py::CANONICAL_POSITION_EVENT_COLUMNS` omits `env`, and
  `append_many_and_project()` inserts a fixed canonical column list.
- `src/state/db.py::init_schema` later tries to `ALTER TABLE position_events
  ADD COLUMN env TEXT NOT NULL DEFAULT 'live'`, which can turn missing
  environment meaning into implicit live meaning on legacy/fresh reconciled DBs.
- `src/state/db.py::query_position_events()` and `query_settlement_events()`
  project canonical rows with `NULL AS env`.
- `query_authoritative_settlement_rows(env=...)` states env is legacy-only and
  calls `query_settlement_events()` without passing or enforcing env.

Classification: S1 unless a live producer can be shown to write only live DBs;
S0 if any replay/shadow/non-live run can append canonical `SETTLED` events into
the same DB read by live risk/status. Current repo evidence is insufficient to
prove that impossible, so repair should fail closed at the boundary.

## Repair Design

Invariant to restore:

Canonical settlement events must carry explicit environment authority from the
producer through `position_events`, and settlement readers must honor requested
environment filters before rows can feed live risk/report/learning semantics.

Minimal durable mechanism:

1. Add `env` to the canonical position event contract and kernel schema with a
   constrained environment vocabulary.
2. Require canonical event producers to supply `env`; default only in a
   narrowly documented legacy reconciliation path, not in new producer payloads.
3. Select `e.env` in `query_position_events()` and `query_settlement_events()`.
4. Add `env` filtering to `query_settlement_events()` and pass it from
   `query_authoritative_settlement_rows()`.
5. Preserve legacy fallback env filtering through `decision_log`.
6. Add relationship tests proving a non-live canonical `SETTLED` event is not
   returned for `env="live"` and cannot feed the authoritative settlement row
   path.

## Implemented Repair

- `src/state/projection.py` now defines the canonical position-event env
  vocabulary and normalization helper.
- `src/state/ledger.py::CANONICAL_POSITION_EVENT_COLUMNS` now includes `env`.
  `append_many_and_project()` normalizes env for every canonical event and
  refuses canonical `SETTLED` events that omit env instead of coercing missing
  settlement scope to live.
- `src/engine/lifecycle_events.py` now includes producer env in every canonical
  lifecycle event builder; `Position.env` remains the producer authority and
  defaults to the repo's current live-only runtime axiom.
- `architecture/2026_04_02_architecture_kernel.sql` now declares `env` on
  `position_events` with the same constrained vocabulary. The column is
  nullable at schema level so legacy/direct rows do not become live by DDL
  default.
- `src/state/db.py::init_schema()` no longer adds
  `position_events.env TEXT NOT NULL DEFAULT 'live'`; it adds nullable env for
  legacy DB convergence. `query_position_events()`, `query_settlement_events()`,
  and `query_authoritative_settlement_rows()` now project and filter canonical
  settlement env.
- `tests/test_db.py` adds relationship coverage that a replay canonical
  `SETTLED` event is invisible to live settlement authority and visible only
  under `env="replay"`, and that a canonical `SETTLED` event missing env fails
  closed before append.
- `tests/test_pnl_flow_and_audit.py` fixture repairs remove baseline noise:
  harvester learning tests now use explicit training-source snapshot lineage,
  a matching `ensemble_snapshots_v2` row for calibration FK authority, a WU
  station id for observation authority, and mock signatures matching current
  harvester/RiskGuard call sites.

## Verification Plan

Required checks for the implementation slice:

- Targeted tests in `tests/test_db.py` or `tests/test_pnl_flow_and_audit.py`
  proving canonical env filtering end-to-end.
- Existing settlement/RiskGuard authority tests that consume
  `query_authoritative_settlement_rows`.
- `py_compile` for touched source/test files.
- `git diff --check`.
- `topology_doctor --planning-lock --changed-files ... --plan-evidence` using
  this plan.
- `topology_doctor --map-maintenance --map-maintenance-mode closeout` for
  touched files.

Verification run:

- PASS: `python -m py_compile src/state/projection.py src/state/ledger.py src/state/db.py src/engine/lifecycle_events.py tests/test_db.py tests/test_pnl_flow_and_audit.py`
- PASS: `git diff --check`
- PASS: `pytest tests/test_append_many_and_project_nested_savepoint.py tests/test_db.py::test_query_p4_fact_smoke_summary_separates_verified_settlement_authority tests/test_db.py::test_query_authoritative_settlement_rows_accepts_env_keyword_for_portfolio_compat tests/test_db.py::test_query_authoritative_settlement_rows_filters_canonical_position_events_by_env tests/test_db.py::test_append_many_and_project_requires_env_for_canonical_settlement_events tests/test_db.py::test_query_authoritative_settlement_rows_requires_verified_settlement_truth tests/test_db.py::test_query_authoritative_settlement_rows_degrades_settled_event_without_truth_authority tests/test_pnl_flow_and_audit.py::test_inv_riskguard_prefers_canonical_position_events_settlement_source tests/test_pnl_flow_and_audit.py::test_inv_harvester_prefers_durable_snapshot_over_open_portfolio -q --tb=short`

Critic verdict:

- APPROVE: native critic reviewed the diff against `origin/main` for
  `position_events` schema, append boundary, lifecycle producers, settlement
  readers, and downstream RiskGuard/portfolio/strategy/decision-chain/edge/
  attribution/harvester consumers. No Critical/Important invariance blockers
  were found. Residual note matches this packet: physical DBs that already
  received historical default-live env DDL still require an operator-approved
  audit/backfill/relabel plan; this source patch intentionally does not relabel
  existing rows.

Downstream sweep:

- `src/riskguard/riskguard.py`, `src/state/portfolio.py`,
  `src/state/strategy_tracker.py`, `src/state/decision_chain.py`,
  `src/state/edge_observation.py`, `src/state/attribution_drift.py`,
  `src/execution/harvester.py`, and `scripts/verify_truth_surfaces.py` consume
  `query_authoritative_settlement_rows()` or `query_settlement_events()`. After
  this repair those default to `get_mode()` / live filtering, and explicit
  non-live reads must request their env.
- Direct non-settlement `position_events` inserts still exist in legacy/audit
  paths and tests. They do not materialize canonical settlement authority
  because `query_settlement_events()` filters only `event_type='SETTLED'` and
  requires matching env.
- Existing physical live DBs that already received prior
  `env TEXT NOT NULL DEFAULT 'live'` DDL may contain rows whose env value was
  defaulted before this repair. This patch intentionally does not relabel or
  backfill those rows; resolving historical contamination requires an operator
  approved dry-run/audit/rollback plan.

## Stop Conditions

Stop for operator decision before:

- executing live/prod DB migrations or backfills;
- silently relabeling existing `position_events.env` values;
- changing runtime mode semantics beyond settlement event eligibility;
- publishing reports or promoting replay rows into learning authority;
- widening into topology redesign or docs registry edits not admitted by route.
