# Zeus Current Delivery Law

Status: active durable delivery authority  
Scope: authority order, zero-context boot, documentation isolation, packet discipline, demotion/promotion hygiene, validation, and completion evidence  
Freshness model: durable law. This file does not record live bankroll, process status, positions, packet diary, temporary reject counts, or loaded SHA.

---

## 1. Purpose

This file defines how work may land in Zeus without corrupting the live-money trading machine or the agent cognition surface. `docs/authority/zeus_current_architecture.md` defines what Zeus is; this file defines how changes, docs, packets, and evidence are handled.

Correct delivery process never excuses semantic violations. For runtime, strategy, settlement, source, execution, lifecycle, DB, risk, or docs-authority work, ingest the architecture law and relevant code/manifests before relying on any prose plan.

---

## 2. Proof And Authority Order

Use this order for every conflict:

1. executable source, launchd deploy artifacts as installed by operator receipts, migrations/schema, DB/event/projection truth, tests/invariants, and runtime receipts;
2. machine manifests under `architecture/**`;
3. active durable authority files: `docs/authority/zeus_current_architecture.md`, this file, and retained constitutions whose registry class is active authority;
4. canonical durable references under `docs/reference/**`;
5. current-fact operation pointers while fresh and evidence-backed;
6. scoped AGENTS files as routing contracts;
7. evidence, reports, packets, task folders, consults, PR reviews, dated audits, rebuild notes, archives, graph outputs, and chat memory.

If code/manifests/tests and prose disagree, update the prose. If implementation is unclear, mark the ambiguity instead of manufacturing a stable law.

---

## 3. Zero-Context Boot Order

For non-trivial work, the default route is:

1. `AGENTS.md`;
2. `workspace_map.md`;
3. scoped `AGENTS.md` for every directory you will touch;
4. `docs/authority/zeus_current_architecture.md`;
5. this file when the work touches delivery, docs, architecture, governance, demotion, packets, or registries;
6. `docs/reference/AGENTS.md` and the canonical reference named there for the task;
7. `architecture/task_boot_profiles.yaml` and `architecture/fatal_misreads.yaml` for the task class;
8. relevant machine manifests such as `architecture/db_table_ownership.yaml`, `money_path_objects.yaml`, `negative_constraints.yaml`, `runtime_modes.yaml`, `runtime_posture.yaml`, `test_topology.yaml`, `module_manifest.yaml`, and `docs_registry.yaml`;
9. targeted code/tests/config/deploy artifacts;
10. current-fact pointers only when the task needs present operational status and the pointer is fresh enough.

Do not default-read `docs/evidence/**`, `docs/reports/**`, `docs/archive/**`, `docs/rebuild/**`, closed `docs/operations/task_*`, dated consult/review/raw docs, or packet evidence. Read them only by explicit task need and cite them as evidence/history, never present-tense law.

---

## 4. Documentation Layer Law

The docs tree has one job per layer:

- `docs/authority/**`: durable law only. No consult raw, one-off doctrine, dated task packet, PR review, packet diary, live runtime snapshot, or active work log.
- `docs/reference/**`: durable concept/reference/module books. No live bankroll, PID, loaded SHA, current position list, active rejection count, current packet diary, or time-bound operational fact.
- `docs/operations/current_state.md`, `current_data_state.md`, `current_source_validity.md`: current-fact pointers with evidence/freshness/expiry semantics. They do not authorize architecture.
- `docs/operations/current/**`: active work package home only; default route must not recursively read it. Closed/superseded packages move to archive/reports/evidence and become non-default.
- `docs/runbooks/**`: procedural operations. Runbooks must not define architecture.
- `docs/evidence/**`, `docs/reports/**`, `docs/archive/**`, `docs/rebuild/**`: evidence/history. Discoverable, non-default, non-authority.

A historical file in the wrong layer must be demoted. If it contains a surviving rule, promote the rule into active authority or canonical reference first, then archive or report the source.

---

## 5. Change Classes

Classify before editing:

- **Math/strategy**: probability, q, q_lcb/q_ucb, payoff vector, FDR, selection, Kelly, risk, calibration, signal, thresholding.
- **Architecture**: lifecycle grammar, truth ownership, DB split, transaction boundaries, source/settlement semantics, family/bin/native-side law, runtime topology.
- **Execution/venue**: intent, command persistence, pre-submit witness, venue adapter, idempotency, fill truth.
- **Docs/governance**: AGENTS, authority/reference/current-fact isolation, registry/router changes, demotion/promotion.
- **Schema/truth contract**: migrations, DB ownership, supervisor/control-plane contracts, manifest grammar.

If a change touches live-money side effects, lifecycle truth, `strategy_key`, settlement source, q authority, DB ownership, risk semantics, authority order, or default boot path, treat it as architecture/governance even if the diff is small.

---

## 6. Planning Lock And Packet Discipline

Plan before touching:

- `docs/authority/**`;
- `architecture/**`;
- AGENTS files or workspace routing;
- schema/migrations/DB ownership;
- `src/state/**`, `src/execution/**`, `src/engine/event_reactor_adapter.py`, q-kernel/decision/risk code, or venue adapter;
- cross-zone work;
- demotion/promotion or registry updates;
- more than four files.

A packet is execution evidence, not durable law. A packet can guide a task while active, but it must not remain in the default authority/reference route. Closing a packet requires targeted evidence, affected-surface checks, explicit residual risks, and router/registry cleanup.

Do not create new root-level plan, handoff, scratch, or one-off status files. Use the existing operations/evidence/report/archive homes and registry classes.

---

## 7. Current-Fact Rules

Current facts must name:

- observed fact;
- evidence source/receipt;
- observed_at or checked_at;
- freshness or expiry rule;
- owner/path to refresh;
- what to do when stale.

If freshness cannot be proven, write `unknown` and fail closed where the money path needs the fact. Current facts cannot be backfilled into durable authority to avoid expiry.

Forbidden current facts in durable authority/reference: live bankroll, PID, loaded SHA, active position inventory, active packet diary, temporary rejection count, process loaded/unloaded status, current launchctl output, and current venue balances.

---

## 8. Demotion And Promotion

Promote a rule only when it is durable, future-applicable, no longer packet/date scoped, and backed by code, tests, manifests, DB truth, deploy artifacts, or operator receipts.

Demote a file when it is:

- dated strategy-of-record doc superseded by current code;
- consult raw, PR review, debate transcript, packet evidence, task log, rebuild diary, or work diary;
- an active operational fact without freshness semantics;
- a reference file carrying present-tense runtime state;
- a historical law absorbed into current authority/reference.

Demotion actions must update `docs/archive_registry.md` or the repo's active archive/report index and must remove default-read router references.

---

## 9. Validation Law

A change is not complete until validation is attempted and results are separated into:

- changed-surface failures caused by the patch;
- repo-wide pre-existing drift;
- unavailable validation due missing checkout/tooling/credential.

Minimum docs/architecture validation surface:

- `python3 scripts/topology_doctor.py --strict`;
- `python3 scripts/topology_doctor.py --source`;
- `python3 scripts/topology_doctor.py --tests`;
- `python3 scripts/topology_doctor.py --fatal-misreads`;
- docs registry/topology modes shown by `python3 scripts/topology_doctor.py --help`;
- stale-term searches against active default-read authority/reference/router files.

When local execution is unavailable, record the exact reason and run the strongest available static validation through the connector/search surface. Do not claim a command passed unless it actually ran.

---

## 10. Completion Protocol

Before claiming complete:

1. list active default-read surface after the change;
2. list files rewritten;
3. list files demoted/archived/quarantined/deleted;
4. list registry/router updates;
5. list stale claims removed and their new truth/action;
6. summarize current deploy money path with code anchors;
7. report validation commands and results honestly;
8. name unresolved implementation ambiguities;
9. give one tiny next follow-up only if needed.

Do not hide failures. Do not call evidence/history present-tense law. Do not leave obsolete docs reachable through AGENTS, README, docs registry, reference replacement, module manifest, task boot profiles, or default current routes.

---

## 11. Relationship To Other Files

- `docs/authority/zeus_current_architecture.md`: semantic/runtime law.
- `docs/authority/zeus_change_control_constitution.md`: retained anti-entropy constitution; non-default unless a governance task requires deep rationale.
- `docs/authority/ARCHIVAL_RULES.md`: retained archival hygiene law when present.
- `docs/reference/zeus_prediction_market_quant_reference.md`: canonical durable reference for the live money path.
- `docs/operations/current_state.md`: active packet/current-state pointer, not architecture.
- `architecture/docs_registry.yaml`: machine-readable classification and default-read control.
