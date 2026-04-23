# src/state AGENTS

State is Zeus's truth and transition zone. This directory owns canonical DB
truth, append/projection discipline, lifecycle legality, reconciliation, and
portfolio read models.

## Read this before editing

- Module book: `docs/reference/modules/state.md`
- Machine registry: `architecture/module_manifest.yaml`
- Runtime/lifecycle law: `docs/authority/zeus_current_architecture.md`,
  `docs/authority/zeus_current_delivery.md`, `architecture/invariants.yaml`

## Top hazards

- canonical DB/event truth outranks JSON/CSV/status exports
- lifecycle transitions belong to `lifecycle_manager.py`, not ad hoc callers
- append/projection ordering and transaction boundaries are load-bearing
- chain reconciliation must preserve unknown vs known-empty semantics

## Canonical truth surfaces

- `db.py`
- `ledger.py`
- `projection.py`
- `lifecycle_manager.py`
- `chain_reconciliation.py`
- `portfolio.py`

## High-risk files

| File | Role |
|------|------|
| `db.py` | canonical write/query substrate |
| `portfolio.py` | runtime position truth and read model |
| `portfolio_loader_policy.py` | DB-vs-fallback load discipline |
| `lifecycle_manager.py` | sole lifecycle authority |
| `chain_reconciliation.py` | outer-truth convergence |
| `ledger.py` | append-only event spine |
| `projection.py` | deterministic projection fold |
| `decision_chain.py` | point-in-time decision lineage |

## Required tests

- `tests/test_db.py`
- `tests/test_dt1_commit_ordering.py`
- `tests/test_dt1_savepoint_integration.py`
- `tests/test_dt4_chain_three_state.py`
- `tests/test_b070_control_overrides_history_v2.py`
- `tests/test_chronicle_dedup.py`
- `tests/test_cross_module_invariants.py`

## Do not assume

- a derived JSON/status file can stand in for canonical truth
- unknown chain status can be collapsed into empty
- a small schema or lifecycle tweak is not architecture work
- append-only history tables are dead just because a VIEW hides them

## Planning lock

Any schema, truth-owner, projection, reconciliation, or lifecycle write-path
change under `src/state/**` requires an approved packet and planning-lock
evidence.
