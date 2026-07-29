# Docs Index

Router into the tracked `docs/` mesh. See `AGENTS.md` in this directory for
placement rules; this file just says what's where.

## Subroots

| Directory | Purpose |
|-----------|---------|
| `authority/` | Durable architecture and delivery law. |
| `reference/` | Canonical theory, math, and system reference. Entry point: `reference/theory_map.md`. |
| `operations/` | Live control pointer, current-state docs, and active work packets. |
| `review/` | Review process and scope docs. |
| `methodology/` | Cross-cutting methodology (e.g. adversarial debate evaluation). |
| `evidence/` | Internal investigation record — dated probes, audits, and consult reports. Slated to migrate off the default branch; not authority. |
| `rebuild/` | Active rebuild-effort design docs and implementation packets. |
| `lore/` | Recorded topology/authority-drift hypotheses. |
| `architecture/` | Dated system-decomposition design docs. |

## Other tracked files here

- `AGENTS.md` — placement rules and taxonomy for this directory
- `archive_registry.md` — visible historical interface into retired docs
- `polyweather_city_source_overlay_verified.csv` — verified city/source overlay data

## Naming rules

- All `.md` files use `lower_snake_case.md`
- Exceptions: `AGENTS.md`, `README.md`
- Dated or packet-scoped docs use a `YYYY-MM-DD` or `task_YYYY-MM-DD_name`
  prefix within their subroot
