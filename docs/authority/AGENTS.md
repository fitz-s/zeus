# docs/authority AGENTS

This directory contains durable authority law only. It is not a place for packets, consults, PR reviews, raw evidence, dated audits, current runtime status, or historical governance notes.

---

## Active Authority Set

| File | Class | Default-read posture |
|---|---|---|
| `zeus_current_architecture.md` | active durable architecture law | runtime/money-path work |
| `zeus_current_delivery.md` | active durable delivery law | docs/governance/router work |
| `zeus_database_runtime_authority.md` | active durable database runtime law | DB/WAL/lock/topology work |
| `zeus_forecast_fusion_authority.md` | active durable forecast-fusion law | forecast source/model/fusion work |
| `zeus_docs_classification_authority.md` | active durable docs-classification law | docs classification/default-read work |
| `zeus_change_control_constitution.md` | durable deep-governance constitution | non-default governance rationale |
| `ARCHIVAL_RULES.md` | durable archive/evidence isolation law | demotion/archive/registry work |

No other file in this directory is active authority unless this table and `architecture/docs_registry.yaml` both say so.

---

## Required Posture

- Code, manifests, tests, deploy artifacts, DB ownership, and runtime receipts outrank prose.
- Durable law must not contain current operational snapshots.
- Current facts belong under operations current pointers with evidence and expiry.
- Historical material belongs in reports, evidence, or archive and is not default boot.
- Reference explains law; it does not create law.
- If old prose contains surviving law, promote the law first, then demote the source.
- If behavior cannot be proven, write `unknown` or `unresolved implementation ambiguity`.

---

## Maintenance Rules

- Database runtime law changes must update both `zeus_database_runtime_authority.md` and `architecture/db_runtime_manifest.yaml`.
- Forecast-fusion law changes must update both `zeus_forecast_fusion_authority.md` and `architecture/forecast_fusion_manifest.yaml`.
- Docs-classification law changes must update both `zeus_docs_classification_authority.md`, `architecture/docs_plane_manifest.yaml`, and `architecture/docs_registry.yaml`.
- Demotions out of authority must be recorded in `docs/archive_registry.md`.
- Do not let runbooks, operations current-state files, evidence, reference docs, reports, or archives authorize architecture.
