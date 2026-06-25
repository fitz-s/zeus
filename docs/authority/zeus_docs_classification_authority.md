# Zeus Docs Classification Authority

Status: active durable authority law  
Scope: documentation class boundaries, default-read eligibility, authority/reference/current/evidence separation  
Machine authority: `architecture/docs_registry.yaml`, `architecture/reference_replacement.yaml`  
Freshness model: durable classification law. Individual current facts expire in their own surfaces.

---

## 1. Core Law

Documentation placement does not decide authority. A file is authority only when its class and registry route say it is authority.

The active classes are:

| Class | Authority? | Default-read? | Allowed content |
|---|---:|---:|---|
| durable authority law | yes | yes when task class requires | stable architecture/delivery/model-selection/docs-classification law |
| machine authority manifest | yes | yes when task class requires | YAML/registry/test topology/contracts that machines can validate |
| canonical reference | no | task-routed only | durable explanation, formulas, examples, rebuild guidance |
| current fact pointer | no durable authority | only when current facts are required | evidence-backed present state with freshness/expiry |
| runbook | no | operation-routed only | procedures, commands, triage flow |
| evidence | no | no | raw receipts, measurements, audits |
| report | no | no | interpreted history, reviews, closeouts, critic/verifier output |
| archive | no | no | cold historical material |
| packet/work diary | no | no except active task scope | plan, work log, packet evidence, closeout material |
| rebuild/consult history | no | no | implementation notes, raw consults, PR reviews, debate logs |

Reference is not authority. Reference can be excellent, complete, and necessary, but it explains law; it does not create law.

---

## 2. Authority Files

Active docs authority files are only:

- `docs/authority/AGENTS.md`
- `docs/authority/zeus_current_architecture.md`
- `docs/authority/zeus_current_delivery.md`
- `docs/authority/zeus_change_control_constitution.md`
- `docs/authority/ARCHIVAL_RULES.md`
- `docs/authority/zeus_forecast_fusion_authority.md`
- `docs/authority/zeus_docs_classification_authority.md`

A new authority file must be registered in `architecture/docs_registry.yaml`, routed by `docs/authority/AGENTS.md`, and justified by code/manifests/tests/runtime evidence.

---

## 3. Reference Files

Canonical reference files live under `docs/reference/**`. They are not authority even when default-routed for cognition.

Reference files must not contain:

- live bankroll;
- process PID;
- loaded SHA;
- active position inventory;
- current rejection counts;
- active packet diary;
- current provider health without freshness/expiry;
- present-tense law not backed by source/manifest/authority.

When a reference needs a current fact, it must point to an operations current pointer instead of embedding it.

---

## 4. Current Fact Pointers

Current facts belong in:

- `docs/operations/current_state.md`
- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`

They must state evidence, checked_at/observed_at, freshness or expiry, refresh owner, and stale behavior.

Current facts do not become durable law by being cited from authority or reference.

---

## 5. Evidence And History

These paths are never default authority/reference:

- `docs/evidence/**`
- `docs/reports/**`
- `docs/archive/**`
- `docs/rebuild/**`
- closed `docs/operations/task_*`
- non-current bodies under `docs/operations/current/**`

Historical material remains discoverable through `docs/archive_registry.md`, not through default boot.

---

## 6. Promotion Rule

To promote historical material:

1. extract the durable rule;
2. prove it against current code/manifests/tests/runtime evidence;
3. rewrite it into an active authority or reference file;
4. register the route;
5. demote the source.

Never promote a raw packet, raw consult, raw report, or dated audit wholesale.
