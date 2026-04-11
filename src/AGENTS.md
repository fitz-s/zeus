# src AGENTS

Zeus source code root. 14 packages organized by zone (K0-K4), plus cross-cutting types and standalone config.

## Navigation

Every package subdirectory has its own `AGENTS.md` with zone-specific rules, domain context, and a complete file registry. **Read the package `AGENTS.md` before editing any file in that package.**

## Zone map

| Zone | Packages | Purpose |
|------|----------|---------|
| K0 (Kernel) | `contracts/`, `state/` | Truth, lifecycle, semantic boundaries |
| K1 (Protective) | `riskguard/`, `control/` | Risk enforcement, control plane |
| K2 (Execution) | `execution/`, `supervisor_api/` | Order execution, Venus contracts |
| K3 (Math/Data) | `engine/`, `signal/`, `calibration/`, `strategy/`, `data/` | Signals, calibration, trading decisions, data |
| K4 (Extension) | `observability/`, `analysis/` | Monitoring, reporting (derived, never canonical) |
| Cross-cutting | `types/` | Unit-safe types (Temperature, market types) |

## Standalone files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `config.py` | Runtime configuration — settings loader, state paths, mode qualification |
| `main.py` | Daemon entry point (paper/live mode) |

## Import rules

Imports flow downward only: K4 → K3 → K2 → K1 → K0. Never upward. Enforced by `.importlinter` at repo root.

## Rules

- Read the zone-specific `AGENTS.md` before editing any file
- Classify your change (math / architecture / governance) before starting — see root `AGENTS.md` §5
- A math change BECOMES architecture if it touches lifecycle states, strategy_key grammar, unit semantics, or truth surfaces
