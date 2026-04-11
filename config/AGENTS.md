# config AGENTS

Runtime parameters — all configuration that controls Zeus behavior at runtime. Changes here affect trading behavior directly.

## File registry

| File | Purpose |
|------|---------|
| `settings.json` | All runtime parameters (single source of truth) — cycle intervals, thresholds, Kelly multipliers, risk limits |
| `cities.json` | 16 cities with coordinates, WU stations, peak hours, temperature units (F/C) |
| `provenance_registry.yaml` | INV-13 constant registration for Kelly cascade — every magic number traced to source |
| `reality_contracts/execution.yaml` | External assumption contract: Polymarket execution behavior |
| `reality_contracts/protocol.yaml` | External assumption contract: Polymarket protocol rules |
| `reality_contracts/economic.yaml` | External assumption contract: economic/market assumptions |
| `reality_contracts/data.yaml` | External assumption contract: data source availability and behavior |

## Rules

- `settings.json` is the single source of truth for runtime config — no other config files
- Reality contracts (INV-11) define what Zeus assumes about external systems — when assumptions break, contracts flag it
- Changes to `provenance_registry.yaml` require tracing to source literature/data
