# Zeus: Technical Orientation

> For domain model and WHY explanations, see `docs/reference/zeus_domain_model.md`.
> For operating rules and invariants, see root `AGENTS.md`.

## Stack

- **Language**: Python 3.10+
- **Database**: SQLite (append-only event store pattern)
- **Data sources**: ECMWF ENS (51-member ensemble), ASOS/METAR real-time observations, Weather Underground (settlement source)
- **Market**: Polymarket CLOB (weather prediction markets)
- **Infrastructure**: Import linter (zone boundaries), work packets (change control)

## Runtime contexts

Zeus is live-only. Paper mode was decommissioned in Phase 1; backtest and
shadow are diagnostic contexts, not peer runtime modes.

| Context | Authority | Purpose |
|---------|-----------|---------|
| `live` | `state/zeus_trades.db` + `state/zeus-world.db` | Real money trading on Polymarket |
| `backtest` | `state/zeus_backtest.db` | Derived diagnostics and replay reports only |
| `shadow` | DB-backed observation/instrumentation facts | Observe and compare without gating live execution |

JSON status/position files are derived operator surfaces and must not be read
back as runtime authority.

## Key entry points

| Entry point | Purpose |
|-------------|---------|
| `src/engine/cycle_runner.py` | Main daemon — runs one forecast→trade cycle |
| `src/riskguard/` | Independent risk process (separate from main daemon) |
| `scripts/healthcheck.py` | Daemon alive/dead check (Venus cron target) |
| `scripts/baseline_experiment.py` | Legacy experiment / baseline utility — not a current program-phase gate |

## Configuration

- `config/settings.json` — tunable runtime parameters
- `config/cities.json` — 16 cities with coordinates, stations, peak hours, units
- `config/reality_contracts/*.yaml` — scoped external assumption contracts

## Dependencies

See `requirements.txt`. Key libraries:
- `scipy` — Platt calibration optimization
- `numpy` — Monte Carlo simulation, statistical calculations
- `sqlite3` — built-in, event store
- ECMWF open data API for ensemble forecasts

## Logs

Runtime logs are derived operational evidence; DB truth remains authoritative.

## External boundaries

- Zeus is one component in the OpenClaw/Venus workspace
- Venus manages agent identity and deployment
- OpenClaw provides central configuration (`~/.openclaw/openclaw.json`)
- Zeus exposes typed contracts outward but external tools must not mutate repo truth
