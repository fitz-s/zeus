# Docs Index

Docs are a layered cognition mesh for a live-money trading system. Placement is not authority by itself; class, registry, freshness, and proof rank decide how a file may be used.

---

## 1. Design Principle

Default-read paths must be safe for zero-context agents. Active authority and canonical reference stay small, current, and code-anchored. Historical reports, evidence, consults, PR reviews, rebuild notes, and closed packet material remain discoverable but non-default.

If a historical file contains surviving law, promote the law into active authority/reference and demote the source.

---

## 2. Tracked Docs Subroots

| Directory | Class | Purpose | Default-read? |
|---|---|---|---|
| `authority/` | durable authority law | current architecture, delivery, governance, archival law | only routed active files |
| `reference/` | canonical durable reference | domain/math/source/strategy/settlement/execution/risk/data/failure/module books | by task route |
| `reference/modules/` | module reference | dense module books | only routed modules |
| `operations/` | current pointers + active work homes | current-state/data/source pointers and active packages | pointer files only by default |
| `runbooks/` | procedure | operator workflows | only for operation task |
| `evidence/` | evidence | raw measurements, audits, receipts | no |
| `reports/` | report/history | reviews, closeouts, authority history, diagnostic reports | no |
| `archive/` | archive | cold historical bodies | no |
| `rebuild/` | rebuild/history | implementation/rebuild notes and consult material | no |
| `to-do-list/` | worklist | checklist/known gaps | no unless task routes there |
| `artifacts/` | artifacts | evidence artifacts/inventories | no |

---

## 3. Active Default-Read Set

A zero-context docs/money-path agent may enter these, in order, when relevant:

- `../AGENTS.md`;
- `../workspace_map.md`;
- scoped `AGENTS.md` files;
- `docs/AGENTS.md` for docs work;
- `authority/AGENTS.md`;
- `authority/zeus_current_architecture.md`;
- `authority/zeus_current_delivery.md`;
- `authority/ARCHIVAL_RULES.md` for demotion/archive work;
- `reference/AGENTS.md`;
- `reference/zeus_prediction_market_quant_reference.md`;
- focused canonical references named by `reference/AGENTS.md`;
- `operations/current_state.md`, `operations/current_data_state.md`, `operations/current_source_validity.md` only when current facts are required and freshness is acceptable.

Do not recursively read `operations/current/**`, `operations/task_*`, `evidence/**`, `reports/**`, `archive/**`, or `rebuild/**` by default.

---

## 4. Canonical References

| File | Purpose |
|---|---|
| `reference/zeus_prediction_market_quant_reference.md` | complete current deploy money-path reference |
| `reference/zeus_domain_model.md` | family/bin/native-side/domain model |
| `reference/zeus_math_spec.md` | q/q_lcb/payoff/utility math |
| `reference/zeus_forecast_source_and_regional_model_reference.md` | forecast source/product identity, regional model inclusion, residual discipline |
| `reference/zeus_strategy_spec.md` | direction law, admission, selection |
| `reference/zeus_market_settlement_reference.md` | market/source/settlement/bin topology |
| `reference/zeus_execution_lifecycle_reference.md` | execution, command, lifecycle, exit, settlement |
| `reference/zeus_risk_strategy_reference.md` | sizing, risk, DATA_DEGRADED |
| `reference/zeus_data_and_replay_reference.md` | DB topology, provenance, replay boundaries |
| `reference/zeus_failure_modes_reference.md` | live-money failure modes |
| `reference/modules/AGENTS.md` | module-book router |

Specialized legacy references may remain for discoverability, but they are non-default unless current code/manifests support the task-specific claim.

---

## 5. Historical Interface

Use `archive_registry.md` first for demoted material. Only open archive/evidence/report/rebuild bodies when the task explicitly needs historical evidence, and label derived claims as historical evidence.

Historical material must not be cited as present-tense law.

---

## 6. Naming Rules

- Use `lower_snake_case.md` for ordinary docs.
- Exceptions: `AGENTS.md`, `README.md`.
- New active packages belong under the operations current work home, not root.
- Closed/superseded packages must be moved, indexed, or clearly non-default.
- Avoid generic top-level names such as `plan.md` or `progress.md` outside active task folders.
