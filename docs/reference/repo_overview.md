# Zeus: Technical Orientation

> For domain model and WHY explanations, see `docs/reference/zeus_domain_model.md`.
> For operating rules and invariants, see root `AGENTS.md`.

## Stack

- **Language**: Python 3.10+
- **Database**: SQLite (append-only event store pattern)
- **Data sources**: ECMWF ENS (51-member ensemble), ASOS/METAR real-time observations, Weather Underground (settlement source)
- **Market**: Polymarket CLOB (weather prediction markets)
- **Infrastructure**: Import linter (zone boundaries), work packets (change control)

## Runtime modes

Zeus runs in two modes, controlled by `settings.json`:

| Mode | Database | Positions | Purpose |
|------|----------|-----------|---------|
| `paper` | `zeus-paper.db` | `positions-paper.json` | Backtesting, development, validation |
| `live` | `zeus-live.db` | `positions-live.json` | Real money trading on Polymarket |

Shared world data (ensemble forecasts, observations) lives in `zeus-shared.db`.

## Key entry points

| Entry point | Purpose |
|-------------|---------|
| `src/engine/cycle_runner.py` | Main daemon — runs one forecast→trade cycle |
| `src/riskguard/` | Independent risk process (separate from main daemon) |
| `scripts/healthcheck.py` | Daemon alive/dead check (Venus cron target) |
| `scripts/baseline_experiment.py` | Legacy experiment / baseline utility — not a current program-phase gate |

## Configuration

- `config/settings.json` — all runtime parameters (single source of truth)
- `config/cities.json` — 16 cities with coordinates, stations, peak hours, units

## Dependencies

See `requirements.txt`. Key libraries:
- `scipy` — Platt calibration optimization
- `numpy` — Monte Carlo simulation, statistical calculations
- `sqlite3` — built-in, event store
- ECMWF open data API for ensemble forecasts

## Logs

`logs/zeus-paper.log`, `logs/zeus-paper.err` — rotated by launchd.

## External boundaries

- Zeus is one component in the OpenClaw/Venus workspace
- Venus manages agent identity and deployment
- OpenClaw provides central configuration (`~/.openclaw/openclaw.json`)
- Zeus exposes typed contracts outward but external tools must not mutate repo truth
