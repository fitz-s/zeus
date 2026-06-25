# Docs Index

Docs are a layered cognition mesh for a live-money trading system. Placement is not authority by itself; class, registry, freshness, and proof rank decide how a file may be used.

---

## 1. Design Principle

Default-read paths must be safe for zero-context agents. Active authority and canonical reference stay small, current, and code-anchored. Historical reports, evidence, consults, PR reviews, rebuild notes, and closed packet material remain discoverable but non-default.

If a historical file contains surviving law, promote the law into active authority/reference and demote the source.

---

## 2. Three Planes

| Plane | Purpose | Authority? |
|---|---|---:|
| Authority | durable law and machine law | yes |
| Operations | current facts, runtime artifacts, active work, receipts | no durable authority |
| Persistent special | reference, runbooks, reports, evidence, archives, rebuild history | no |

Reference files are not authority. They explain authority, code, and manifests.

---

## 3. Active Authority Set

| File | Purpose |
|---|---|
| `authority/zeus_current_architecture.md` | current durable architecture law |
| `authority/zeus_current_delivery.md` | current durable delivery/change-control law |
| `authority/zeus_database_runtime_authority.md` | DB topology, WAL, busy-timeout, lock, and bulk/live writer law |
| `authority/zeus_forecast_fusion_authority.md` | forecast-fusion model-selection and city-profile law |
| `authority/zeus_docs_classification_authority.md` | docs plane/class/default-read law |
| `authority/zeus_runtime_artifact_authority.md` | runtime artifact placement law |
| `authority/ARCHIVAL_RULES.md` | archive/evidence isolation law |
| `authority/zeus_change_control_constitution.md` | deep governance constitution, non-default |

---

## 4. Tracked Docs Subroots

| Directory | Plane | Purpose | Default-read? |
|---|---|---|---|
| `authority/` | authority | durable docs law | routed active files |
| `reference/` | persistent special | durable explanation | by task route; not authority |
| `operations/` | operations | current pointers, runtime artifacts, active work homes | pointer files only |
| `runbooks/` | persistent special | procedures | only for operation task |
| `evidence/` | persistent special | retained evidence | no |
| `reports/` | persistent special | reports/history | no |
| `archive/` | persistent special | cold history | no |
| `rebuild/` | persistent special | rebuild/consult history | no |

---

## 5. Active Default-Read Set

A zero-context docs/money-path agent may enter these, in order, when relevant:

- `../AGENTS.md`;
- `../workspace_map.md`;
- scoped `AGENTS.md` files;
- `docs/AGENTS.md` for docs work;
- `authority/AGENTS.md`;
- `authority/zeus_current_architecture.md`;
- `authority/zeus_current_delivery.md`;
- `authority/zeus_database_runtime_authority.md` for DB/WAL/lock work;
- `authority/zeus_forecast_fusion_authority.md` for forecast fusion/source/model-selection work;
- `authority/zeus_docs_classification_authority.md` for docs classification/default-read work;
- `authority/zeus_runtime_artifact_authority.md` for runtime artifact placement work;
- `authority/ARCHIVAL_RULES.md` for demotion/archive work;
- `reference/AGENTS.md`;
- `reference/zeus_prediction_market_quant_reference.md`;
- focused canonical references named by `reference/AGENTS.md`;
- `operations/current_state.md`, `operations/current_data_state.md`, `operations/current_source_validity.md` only when current facts are required and freshness is acceptable.

Do not recursively read `operations/current/**`, `operations/task_*`, `evidence/**`, `reports/**`, `archive/**`, or `rebuild/**` by default.

---

## 6. Canonical References

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

---

## 7. Historical Interface

Use `archive_registry.md` first for demoted material. Only open archive/evidence/report/rebuild bodies when the task explicitly needs historical evidence.

Historical material must not be cited as present-tense law.
