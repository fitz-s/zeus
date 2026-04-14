# config AGENTS

Runtime parameters — all configuration that controls Zeus behavior at runtime. Changes here affect trading behavior directly.

## File registry

| File | Purpose |
|------|---------|
| `settings.json` | Tunable runtime parameters — cycle intervals, thresholds, Kelly multipliers, risk limits |
| `cities.json` | 16 cities with coordinates, WU stations, peak hours, temperature units (F/C) |
| `city_monthly_bounds.json` | Generated monthly physical bounds used by ingestion guard; generated config, not hand-edited |
| `city_correlation_matrix.json` | Generated city correlation matrix for risk/data-rebuild work; generated config, not hand-edited |
| `provenance_registry.yaml` | INV-13 constant registration for Kelly cascade — every magic number traced to source |
| `reality_contracts/execution.yaml` | External assumption contract: Polymarket execution behavior |
| `reality_contracts/protocol.yaml` | External assumption contract: Polymarket protocol rules |
| `reality_contracts/economic.yaml` | External assumption contract: economic/market assumptions |
| `reality_contracts/data.yaml` | External assumption contract: data source availability and behavior |
| `data_availability_exceptions.yaml` | K2 hole_scanner whitelist: per-model retro-start dates, publication lag, onboarding floor, fill policy |

## Rules

- `settings.json` is the source for tunable runtime parameters. Other config files have scoped authority for cities, generated data bounds/correlation, provenance, and reality contracts.
- Reality contracts (INV-11) define what Zeus assumes about external systems — when assumptions break, contracts flag it
- Changes to `provenance_registry.yaml` require tracing to source literature/data
