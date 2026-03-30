# CLAUDE.md — Zeus

## What This Is

Zeus is a Polymarket weather prediction market trading engine. It replaces Rainstorm (retired — data assets inherited, code discarded). Zeus is a **market microstructure exploitation engine**, not a weather forecasting system that happens to trade.

## Edge Thesis (ranked by durability)

1. **Favorite-Longshot Bias** — Retail overpays for low-probability shoulder bins, underpays for high-probability center bins
2. **Opening Price Inertia** — First liquidity provider sets sticky prices; 6-24h post-open has largest model-vs-market gap
3. **Bin Boundary Discretization** — WU settles on integer-rounded °F; continuous models miss probability discontinuities at bin edges

## Architecture Overview

- **Signal**: ECMWF ENS 51-member ensemble → Monte Carlo with instrument noise (σ=0.5°F) → P_raw per bin
- **Cross-check**: GFS 31-member ensemble (conflict detection only, never blended)
- **Calibration**: Platt scaling per bucket (cluster × season × lead_band, 72 buckets), bootstrap parameter uncertainty
- **Edge**: Double-bootstrap CI (σ_ensemble + σ_parameter + σ_instrument); CI_lower > 0 required
- **Sizing**: Quarter-Kelly with dynamic multiplier, portfolio heat / drawdown / correlation constraints
- **Execution**: Limit orders only, VWMP fair value (not mid-price), toxicity-aware cancel

## Data Foundation

Inherited from Rainstorm (SQLite):
- 1,634 settlements, 4,410 IEM ASOS daily, 6,520 NOAA GHCND daily, 105K Meteostat hourly
- 285K token price log rows, 14.9K market events, 53.6K ladder backfill, 71 WU PWS city-days

Settlement authority: Polymarket result > WU PWS > IEM ASOS + offset > Meteostat

## Key Design Decisions

- **VWMP everywhere** — all edge calculations use volume-weighted micro-price, never mid-price
- **WU integer rounding** — always simulate the full settlement chain (atmosphere → NWP → sensor → METAR → WU integer °F)
- **Maturity gates** — calibration bucket with n<15 uses P_raw directly with 3× edge threshold
- **Hierarchical fallback** — city+season+lead → cluster+season+lead → season+lead → global → uncalibrated
- **Model conflict = skip** — ECMWF vs GFS CONFLICT (KL > 0.15) → skip market entirely
- **No re-evaluation after entry** — once ENTERED, only exit triggers are checked (no second-guessing)

## Cities

NYC, Chicago, Seattle, Atlanta, Dallas, Miami, LA, SF (US). London, Paris (Europe).

Clusters: US-Northeast, US-Midwest, US-Southeast, US-SouthCentral, US-Pacific, Europe

## Portfolio Constraints

| Limit | Value |
|-------|-------|
| Max single position | 10% bankroll |
| Max portfolio heat | 50% |
| Max correlated exposure | 25% |
| Max city exposure | 20% |
| Max region exposure | 35% |
| Daily loss halt | 8% |
| Weekly loss halt | 15% |
| Max drawdown halt | 20% |
| Min order | $1.00 |

## Discovery Modes

- **Mode A: Opening Hunt** — every 30 min, scan markets <24h old
- **Mode B: Update Reaction** — 4×/day after ECMWF 00Z/12Z arrival, check exits + scan existing markets
- **Mode C: Day0 Capture** — every 15 min for markets <6h to resolution, observation + residual ENS

## Exit Triggers (exhaustive)

SETTLEMENT, EDGE_REVERSAL (2 consecutive ENS runs), STOP_LOSS (>40% cost basis), RISK_HALT, NWS_EXTREME, EXPIRY_EXIT (<4h + unprofitable), BIMODAL_SHIFT

**NOT triggers**: edge shrinking (still positive), model soft-disagree, price fluctuations within stop, slightly different P on new ENS run.

## Common Commands

```bash
cd workspace-venus/zeus
source .venv/bin/activate          # once venv exists
python -m pytest tests/            # run all tests
python -m pytest tests/test_X.py   # single file
```

## Conventions

- **State writes**: atomic — write tmp, then `os.replace()` to target
- **Time**: Chicago local time primary, ET secondary. Never expose UTC to users.
- **Language**: English
- **Spec**: `project level docs/ZEUS_SPEC.md` is the authoritative design document. Code that contradicts it requires explicit justification.
- **Four research documents** (quant research, architecture blueprint, market microstructure, statistical methodology) in `project level docs/` are design authority — all decisions trace to them.

## Testing

- Tests in `tests/` mirror `src/` structure
- Run from project root with venv activated
- Use real data fixtures where possible, mock only external API calls

---

## Claude Behavioral Rules

### MUST DO

1. **Spec is law.** Every implementation decision must trace to `ZEUS_SPEC.md` or the four research documents. If you can't cite which section justifies a choice, stop and ask.
2. **Test before commit.** Run `python -m pytest tests/` and confirm green before any commit. No exceptions.
3. **Atomic state writes.** All file-based state (positions, trades, calibration) must use write-tmp-then-replace. Never write directly to the target file.
4. **VWMP, not mid-price.** Every place that references "market price" must use volume-weighted micro-price. If you see `(bid + ask) / 2` anywhere, fix it immediately.
5. **WU integer rounding in the signal chain.** Every probability calculation must simulate the full settlement chain including integer rounding. `np.round().astype(int)` is mandatory before bin assignment.
6. **Double-bootstrap for edge CI.** Edge confidence intervals must propagate all three σ sources (ensemble, Platt parameters, instrument noise). Single-layer bootstrap is a bug.
7. **Commit often, commit small.** Each logical unit of work gets its own commit. Never bundle unrelated changes.
8. **Read before writing.** Always read a file before modifying it. Understand the existing code before changing it.
9. **Inherit data, not code.** Zeus inherits Rainstorm's *data assets* (SQLite tables). Never copy Rainstorm code — write fresh implementations from the spec.
10. **Log decisions.** When making a non-obvious architectural choice, add a brief code comment citing the spec section (e.g., `# ZEUS_SPEC §3.1: Platt with strong regularization when n < 50`).

### MUST NOT

1. **Never use mid-price.** Not for edge, not for sizing, not for display. VWMP only.
2. **Never blend GFS into probability.** GFS is cross-check only. If KL > 0.15 → skip. Never average ECMWF and GFS vectors.
3. **Never re-evaluate entry decisions.** Once a position is ENTERED, only exit triggers apply. Don't add "should we still hold?" logic to the monitor loop.
4. **Never use market orders.** Limit orders only. No exceptions.
5. **Never skip maturity gates.** If a calibration bucket has n < 15, use P_raw with 3× threshold. Don't "just use Platt anyway."
6. **Never store secrets in code.** API keys, wallet keys, and credentials go through macOS Keychain (`bin/keychain_resolver.py`). Never hardcode, never commit.
7. **Never expose UTC to users.** All user-facing times are Chicago local or ET. Internal storage can use UTC but display must not.
8. **Never add features not in the spec.** If the spec doesn't describe it, don't build it. Ask first.
9. **Never silently swallow errors in the trading path.** Signal generation, calibration, edge calculation, and order execution must fail loud. Use exceptions, not silent fallbacks.
10. **Never commit with failing tests.** If tests fail, fix them or fix the code. Don't skip tests, don't mark them xfail to work around failures.

### WHEN UNCERTAIN

- If the spec is ambiguous on a point, **ask before implementing**. Don't guess.
- If two spec sections appear to conflict, cite both and ask for resolution.
- If a Rainstorm pattern seems relevant but isn't in the Zeus spec, it's intentionally excluded. Don't port it.
- If performance and correctness conflict, choose correctness. Optimize later with evidence.
