# Zeus Workspace Map

This is the root visibility and routing guide for zero-context agents. Read it immediately after root `AGENTS.md`.

It answers:

1. What kind of surface am I looking at?
2. Is it default-readable authority/reference/current fact, or evidence/history only?
3. What should I read next?

---

## 1. Default Route

1. Read `AGENTS.md`.
2. Read this map.
3. Read scoped `AGENTS.md` for every subtree you will touch.
4. For runtime or money-path work, read `docs/authority/zeus_current_architecture.md` and the targeted canonical reference.
5. For docs/governance/router/registry work, read `docs/authority/zeus_current_delivery.md`, `docs/authority/ARCHIVAL_RULES.md`, and `architecture/docs_registry.yaml`.
6. Use machine manifests under `architecture/**` before relying on prose.
7. Use evidence/archive/report files only after a narrow task requires history.

`python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>` may be used for route hints. It is not a proof source and must not make evidence/history default-readable.

---

## 2. Visibility Classes

| Class | Meaning | Examples | Default posture |
|---|---|---|---|
| durable authority law | Active law for architecture, delivery, archival hygiene | `docs/authority/zeus_current_architecture.md`, `docs/authority/zeus_current_delivery.md`, `docs/authority/ARCHIVAL_RULES.md` | Read when task class requires it |
| canonical durable reference | Stable explanation of domain/math/strategy/execution/risk/data/failure modes | `docs/reference/zeus_prediction_market_quant_reference.md`, targeted `docs/reference/zeus_*.md` | Read by task route |
| current fact pointer | Present-tense pointer with freshness/expiry | `docs/operations/current_state.md`, `current_data_state.md`, `current_source_validity.md` | Read only when current facts are required |
| machine-checkable manifest | YAML/registry/test topology/law surfaces | `architecture/*.yaml` | Prefer over prose for registered facts |
| runbook | Operator procedure | `docs/runbooks/**` | Read only for operation being performed |
| evidence/report/archive/rebuild | Historical evidence, dated audit, consult, PR review, packet closeout, raw measurement | `docs/evidence/**`, `docs/reports/**`, `docs/archive/**`, `docs/rebuild/**` | Do not default-read; evidence only |
| runtime local state | DBs, logs, state JSON, launchd/process truth | `state/**`, `logs/**`, live launchd/process receipts | Current fact only when freshly inspected |
| derived structural context | Graphs, topology digests, generated maps | `.code-review-graph/**`, topology output | Route aid only, never semantic proof |
| scratch/untracked | Temporary local work | `.omx/**`, `.omc/**`, raw dumps | Not durable authority |

---

## 3. Directory Router

| Path | Role | Next read |
|---|---|---|
| `src/` | Runtime source code | `src/AGENTS.md`, then package `AGENTS.md` |
| `src/contracts/` | Settlement, native-side, typed economic contracts | `src/contracts/AGENTS.md`, market/settlement reference |
| `src/data/` | Forecast/source/materialization and external data boundaries | `src/data/AGENTS.md`, data/replay reference |
| `src/forecast/`, `src/probability/`, `src/calibration/` | Predictive/q/calibration math | scoped AGENTS if present, math/strategy references |
| `src/decision/`, `src/strategy/` | Family decision, payoff, direction, utility/risk strategy | `src/strategy/AGENTS.md`, strategy/risk references |
| `src/engine/`, `src/events/` | Live daemon orchestration and event reactor boundary | `src/engine/AGENTS.md`, `src/events/AGENTS.md` |
| `src/execution/`, `src/venue/` | Execution side effects and venue adapter | `src/execution/AGENTS.md`, `src/venue/AGENTS.md`, execution lifecycle reference |
| `src/state/` | DB/event/projection/lifecycle truth | `src/state/AGENTS.md`, DB ownership manifest |
| `src/riskguard/`, `src/risk_allocator/` | Risk action and capital allocation boundaries | scoped AGENTS, risk strategy reference |
| `src/ingest/` | Split ingest/event-stream daemons | `src/ingest/AGENTS.md` if present, data/replay reference |
| `tests/` | Regression, invariant, and money-path relationship checks | `tests/AGENTS.md`, `architecture/test_topology.yaml` |
| `scripts/` | Operator/ETL/audit/enforcement tools | `scripts/AGENTS.md`, script manifests when present |
| `deploy/launchd/` | Operator-installable launchd artifacts | Treat as deploy artifact; verify live load separately |
| `config/` | Runtime settings and static config | `config/AGENTS.md`; remember current behavior still needs runtime proof |
| `docs/authority/` | Durable law only | `docs/authority/AGENTS.md` |
| `docs/reference/` | Durable concept/reference layer | `docs/reference/AGENTS.md` |
| `docs/reference/modules/` | Module books only | `docs/reference/modules/AGENTS.md` |
| `docs/operations/` | Current pointers, run-state notes, active work homes | `docs/operations/AGENTS.md`; do not recursively default-read packets |
| `docs/runbooks/` | Operator runbooks | `docs/runbooks/AGENTS.md` |
| `docs/evidence/`, `docs/reports/`, `docs/archive/`, `docs/rebuild/` | Evidence/history/consult/rebuild/closed material | Route through registry/archive index; non-default |
| `architecture/` | Machine-checkable law/registries/topology | `architecture/AGENTS.md` |
| `.code-review-graph/` | Derived structural context | graph status command; not semantic truth |
| `state/` | Runtime DBs/local current state | Fresh inspection only; never durable law |
| `raw/` | Raw external captures | Evidence only |

---

## 4. Machine Manifests To Prefer Over Prose

| Manifest | Use |
|---|---|
| `architecture/docs_registry.yaml` | Docs classification and default-read registry |
| `architecture/reference_replacement.yaml` | Canonical reference replacement map |
| `architecture/module_manifest.yaml` | Module-book/router/dependency registry |
| `architecture/kernel_manifest.yaml` | Kernel authority and lifecycle surfaces |
| `architecture/invariants.yaml` | Invariant IDs and enforcement intent |
| `architecture/negative_constraints.yaml` | Forbidden patterns and carve-outs |
| `architecture/fatal_misreads.yaml` | Semantic shortcut antibodies |
| `architecture/db_table_ownership.yaml` | Canonical table-to-DB ownership |
| `architecture/runtime_modes.yaml` | Discovery mode grammar and shared runtime path |
| `architecture/runtime_posture.yaml` | Branch posture grammar |
| `architecture/money_path_objects.yaml` | Economic object/state-machine/source registry |
| `architecture/test_topology.yaml` | Test categories and law gates |
| `architecture/source_rationale.yaml` | Per-source/package rationale, hazards, providers |
| `architecture/task_boot_profiles.yaml` | Semantic boot profiles by task class |
| `architecture/city_truth_contract.yaml` | Stable source-role schema, not current per-city truth |
| `architecture/code_review_graph_protocol.yaml` | Graph usage protocol; graph is derived context |

Dated audit/critic/plan Markdown under `architecture/**` is historical unless a manifest routes it as active. Do not add dated review files to the default map.

---

## 5. Do Not Default-Read

- `docs/evidence/**`;
- `docs/reports/**`;
- `docs/archive/**`;
- `docs/rebuild/**`;
- closed `docs/operations/task_*` packages;
- raw consult, PR review, closeout, packet, or dated strategy documents;
- `.code-review-graph/graph.db` as if it were authority;
- `.omx/**`, `.omc/**`, generated dumps, local scratch;
- long module books before scoped routing identifies the module.

Packet docs, ADRs, fix-pack notes, rollback doctrine, and date-scoped boundary notes must not remain in active authority/reference. Promote surviving law into active authority/reference, then archive or report the source.

---

## 6. Maintenance Rule

When adding, renaming, demoting, or deleting a doc-like file:

1. update the owning manifest or registry;
2. update scoped `AGENTS.md` if route instructions changed;
3. update `docs/archive_registry.md` for demotions;
4. update this map only when directory-level structure or visibility class changed;
5. validate with topology/stale-term checks and report unavailable checks honestly.

Suggested command after a material boot-surface rewrite:

`python3 scripts/topology_doctor.py --context-budget --json`
