# docs AGENTS

Documentation root for the tracked docs mesh. This directory routes docs; it does not outrank source code, machine manifests, tests, DB/runtime receipts, or active authority law.

Module book: `docs/reference/modules/docs_system.md`.

---

## System Understanding First

Before changing docs, understand the trading system enough to avoid restoring obsolete lore:

1. `../AGENTS.md`;
2. `../workspace_map.md`;
3. `authority/zeus_current_architecture.md`;
4. `authority/zeus_current_delivery.md`;
5. `reference/zeus_prediction_market_quant_reference.md`;
6. `architecture/docs_registry.yaml` and affected manifests.

---

## Taxonomy

| Subroot | Class | Rule |
|---|---|---|
| `authority/` | durable authority law | no packets, consults, PR reviews, dated one-off doctrine, or current facts |
| `reference/` | canonical durable reference | no present-tense runtime facts or packet evidence |
| `reference/modules/` | module reference | one module/system surface per book; reference only |
| `operations/` | current pointers and active work homes | pointer files only default-readable; task dirs are not default |
| `runbooks/` | procedural | not architecture law |
| `evidence/` | raw evidence/receipts | never default law |
| `reports/` | interpreted history/reviews | never default law |
| `archive/` | cold history | route through archive registry |
| `rebuild/` | rebuild/consult history | never default law |
| `to-do-list/`, `artifacts/` | worklists/evidence artifacts | not authority |

See `authority/ARCHIVAL_RULES.md` for demotion and quarantine rules.

---

## Navigation

Read `README.md` for the active docs index. For historical needs, read `archive_registry.md` before opening archive/report/evidence bodies. Treat those bodies as historical evidence only.

---

## Active File Registry

| Item | Purpose |
|---|---|
| `README.md` | docs index and visibility guide |
| `archive_registry.md` | visible historical interface and demotion registry |
| `authority/AGENTS.md` | authority-directory law |
| `authority/zeus_current_architecture.md` | current architecture law |
| `authority/zeus_current_delivery.md` | current delivery/docs law |
| `authority/ARCHIVAL_RULES.md` | archival/evidence isolation law |
| `reference/AGENTS.md` | reference router |
| `reference/zeus_prediction_market_quant_reference.md` | canonical full money-path reference |
| `reference/zeus_domain_model.md` | domain model |
| `reference/zeus_math_spec.md` | math/q/q_lcb/payoff utility reference |
| `reference/zeus_strategy_spec.md` | strategy/admission/selection reference |
| `reference/zeus_market_settlement_reference.md` | market/settlement/source/bin reference |
| `reference/zeus_execution_lifecycle_reference.md` | execution/lifecycle/exit/settlement reference |
| `reference/zeus_risk_strategy_reference.md` | sizing/risk reference |
| `reference/zeus_data_and_replay_reference.md` | data/replay reference |
| `reference/zeus_failure_modes_reference.md` | failure modes reference |
| `reference/modules/AGENTS.md` | dense module-book router |
| `operations/current_state.md` | current operational pointer; not architecture |
| `operations/current_data_state.md` | current data posture pointer |
| `operations/current_source_validity.md` | current source-validity pointer |
| `runbooks/` | procedures |

Old packet-local CLOB contracts, TIGGE handoffs, dated migration notes, and design audits are evidence/history only unless promoted into active authority/reference and registered.

---

## New City / Source Work

For new city or source-routing tasks, do not rely on this router alone. Read:

- `docs/reference/zeus_market_settlement_reference.md`;
- `docs/reference/zeus_data_and_replay_reference.md`;
- `docs/operations/current_source_validity.md` if current truth is needed;
- `architecture/fatal_misreads.yaml`;
- source/config manifests and current evidence.

Current per-city truth must be fresh/evidence-backed. If not verified, mark unknown and fail closed for live money.

---

## Rules

- New active docs belong in declared tracked subroots, not directly under `docs/` except approved roots such as `README.md`, `AGENTS.md`, and `archive_registry.md`.
- Historical needs route through `archive_registry.md`, not raw archive bodies.
- Do not put current facts, dated audits, or stale support material in `docs/reference/`.
- Do not put packet-scoped docs, ADRs, rollback notes, consult raw, PR reviews, or one-off governance doctrine in `docs/authority/`.
- Generated reports and evidence are evidence only and must not become authority by placement.
- When demoting, update docs registry/router/archive registry in the same patch.
