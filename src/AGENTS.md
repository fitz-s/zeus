# src AGENTS

Zeus source code root. 14 packages organized by zone (K0-K4), plus cross-cutting types and standalone config.

## Navigation

Every package subdirectory has its own `AGENTS.md` with zone-specific rules, domain context, and a complete file registry. **Read the package `AGENTS.md` before editing any file in that package.**

## Zone map

The canonical file-level zone map lives in `architecture/zones.yaml`.
Use this file only as a navigation summary. If it disagrees with `architecture/zones.yaml`, the YAML wins.

`src/state/` should be treated as a mixed navigation cluster when reading docs. Consult `architecture/zones.yaml` for file-level ownership before editing any file there.

## Standalone files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `config.py` | Runtime configuration — settings loader and live-only state paths |
| `main.py` | Live-only daemon entry point |

## Import rules

Imports flow downward only: K4 → K3 → K2 → K1 → K0. Never upward. Enforced by `.importlinter` at repo root.

## Rules

- Read the zone-specific `AGENTS.md` before editing any file
- Classify your change (math / architecture / governance) before starting — see root `AGENTS.md` §5
- A math change BECOMES architecture if it touches lifecycle states, strategy_key grammar, unit semantics, or truth surfaces
