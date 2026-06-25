# docs/authority AGENTS

This directory contains durable authority law only. It is not a place for packets, consults, reviews, raw evidence, dated audits, current runtime status, or historical notes.

---

## Active Authority Set

| File | Class | Default-read posture |
|---|---|---|
| `zeus_current_architecture.md` | active durable architecture law | runtime/money-path work |
| `zeus_current_delivery.md` | active durable delivery law | docs/governance/router work |
| `zeus_database_runtime_authority.md` | active durable database runtime law | DB/WAL/lock/topology work |
| `zeus_forecast_fusion_authority.md` | active durable forecast-fusion law | forecast source/model/fusion work |
| `zeus_docs_classification_authority.md` | active durable docs-classification law | docs classification/default-read work |
| `zeus_runtime_artifact_authority.md` | active durable runtime artifact placement law | runtime receipt/probe/artifact placement work |
| `zeus_change_control_constitution.md` | durable deep-governance constitution | non-default governance rationale |
| `ARCHIVAL_RULES.md` | durable archive/evidence isolation law | demotion/archive/registry work |

No other file in this directory is active authority unless this table and `architecture/docs_registry.yaml` both say so.

---

## Rules

- Code, manifests, tests, deploy artifacts, DB ownership, and runtime receipts outrank prose.
- Durable law must not contain current operational snapshots.
- Current facts and runtime artifacts belong in operations.
- Reference explains law; it does not create law.
- Demotions out of authority must be recorded in `docs/archive_registry.md`.
- Database runtime changes must update `zeus_database_runtime_authority.md` and `architecture/db_runtime_manifest.yaml`.
- Forecast fusion changes must update `zeus_forecast_fusion_authority.md` and `architecture/forecast_fusion_manifest.yaml`.
- Docs/runtime artifact classification changes must update `zeus_docs_classification_authority.md`, `zeus_runtime_artifact_authority.md`, and the relevant architecture registry.
