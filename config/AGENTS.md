# config AGENTS

Runtime parameters — all configuration that controls Zeus behavior at runtime. Changes here affect trading behavior directly.

## File registry

| File | Purpose |
|------|---------|
| `settings.json` | Tunable runtime parameters — cycle intervals, thresholds, Kelly multipliers, risk limits |
| `cities.json` | 46 cities: coordinates (= settlement station lat/lon), station id, `settlement_source_type`, timezone, unit, peak hour, cluster; `_source_contract_pending_conversions` blocks config-only source migrations until release evidence exists. **Routine check needed** — see discipline note below |
| `city_monthly_bounds.json` | Generated monthly physical bounds used by ingestion guard; generated config, not hand-edited |
| `city_correlation_matrix.json` | Generated city correlation matrix for risk/data-rebuild work; generated config, not hand-edited |
| `provenance_registry.yaml` | INV-13 constant registration for Kelly cascade — every magic number traced to source |
| `reality_contracts/execution.yaml` | External assumption contract: Polymarket execution behavior |
| `reality_contracts/protocol.yaml` | External assumption contract: Polymarket protocol rules |
| `reality_contracts/economic.yaml` | External assumption contract: economic/market assumptions |
| `reality_contracts/data.yaml` | External assumption contract: data source availability and behavior |
| `data_availability_exceptions.yaml` | K2 hole_scanner whitelist: per-model retro-start dates, publication lag, onboarding floor, fill policy |
| `risk_caps.yaml` | R3 A2 engineering defaults for RiskAllocator/PortfolioGovernor capacity, drawdown, heartbeat/WS-gap, reconciliation, unknown-side-effect, and maker/taker thresholds |
| `settings.example.json` | Template for config/settings.json with operator-specific values marked null; copy to settings.json to configure |
| `source_release_calendar.yaml` | Source release calendar for data-source availability windows and release schedules |

## Rules

- `settings.json` is the source for tunable runtime parameters. Other config files have scoped authority for cities, generated data bounds/correlation, provenance, and reality contracts.
- Reality contracts (INV-11) define what Zeus assumes about external systems — when assumptions break, contracts flag it
- Changes to `provenance_registry.yaml` require tracing to source literature/data
- `risk_caps.yaml` defaults must remain sane when absent; operator tuning is separate from engineering closeout and must not itself authorize live deployment.

## cities.json — routine check discipline (ROUTINE CHECK NEEDED)

Every `cities.json` row must match the **current** Polymarket market description text for that city. Polymarket can change a city's settlement source, station, or unit at any time.

Volatile external city/station evidence lives under `docs/artifacts/polymarket_city_settlement_audit_*.md`. These artifacts explain why the audit cadence exists; they are not current authority.

**Audit cadence**: monthly, plus before any recalibration run or new-city onboarding.

**Single source of truth**: the `description` field of the most recent active Polymarket market for each city. The market wins when any mirror (cities.json, Wunderground page, code constants) disagrees.

**`settlement_source_type` values**: `wu_icao`, `hko`, `noaa`, `cwa_station`. Field semantics live in config/schema code and `cities.json`; do not update this routing file from a dated market snapshot.

**Downstream consequence**: any station-ICAO change invalidates prior `observations` rows for that city (wrong station = wrong temperatures). Delete the stale rows and re-backfill before running calibration.

**Coordinate invariant**: `lat`/`lon` MUST correspond to the same physical station as `wu_station` / `hko_station` / CWA id. Do not use city-center or approximate coordinates — this drives ENS grid-point selection.

**Pending conversion invariant**: a city listed in `_source_contract_pending_conversions` with `status="pending_release"` remains blocked for new entries even if its current market `resolutionSource` matches `cities.json`. Release requires complete `state/source_contract_quarantine.json` transition-history evidence refs for config, source-validity, backfill, settlements, calibration, and verification.

Use generated/audit evidence for dated city exception lists. Do not encode volatile city/station snapshots directly in this routing file.
