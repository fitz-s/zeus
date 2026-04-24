# Zeus

**Quantitative trading engine for weather-settlement prediction markets on Polymarket.**

Zeus converts atmospheric ensemble forecasts into calibrated probabilities, identifies edges against market prices, sizes positions via fractional Kelly, and executes through the Polymarket CLOB — all while enforcing strict contract semantics, source provenance, and dual-track temperature identity end-to-end.

---

## How it works

Zeus trades **discrete settlement contracts** on daily high/low temperatures. The core pipeline:

```text
contract semantics
  → source truth (settlement provider, station, observation field)
  → ensemble forecast signal (51 ENS members)
  → Monte Carlo sensor-noise + rounding simulation → P_raw
  → Extended Platt calibration (temporal-decay aware) → P_cal
  → α-weighted model-market fusion → P_posterior
  → double-bootstrap confidence intervals → edge + p-value
  → BH FDR filtering (per tested-family)
  → fractional Kelly sizing (dynamic cascade multiplier)
  → execution via Polymarket CLOB
  → monitoring / exit
  → settlement reconciliation
  → learning (without hindsight leakage)
```

Everything starts with the **venue contract** — city, local date, temperature metric, unit, bin topology, settlement source, and provider-specific settlement transform. Forecast probability is economically meaningful only after these semantic obligations are pinned.

### Why settlement is discrete

Polymarket weather markets settle on integer temperatures reported by the settlement provider (typically Weather Underground). A real temperature of 74.45°F → sensor reads 74.2°F → METAR rounds → WU displays 74°F. Zeus models this full chain explicitly via Monte Carlo rather than assuming continuous distributions.

Three bin types exist:

| Type | Example | Resolution |
|------|---------|------------|
| `point` | 10°C | Resolves on exactly {10} |
| `finite_range` | 50-51°F | Resolves on {50, 51} |
| `open_shoulder` | 75°F+ | Unbounded — not a symmetric range |

### Calibration

Raw ensemble probabilities are biased — overconfident at long lead times, underconfident near settlement. Zeus uses Extended Platt scaling with lead-time as an input feature:

```text
P_cal = sigmoid(A·logit(P_raw) + B·lead_days + C)
```

The `B·lead_days` term triples effective training data per bucket vs. simple lead-time bucketing and prevents overtrade of stale forecasts.

### Edge detection and sizing

- **Model-market fusion**: `P_posterior = α × P_cal + (1 - α) × P_market`, where α is dynamically computed from calibration maturity, ensemble spread, and lead time (clamped to [0.20, 0.85])
- **Uncertainty**: double-bootstrap propagates ensemble sampling noise, instrument noise (σ ≈ 0.2–0.5°F), and calibration parameter uncertainty
- **Selection**: Benjamini-Hochberg FDR controls false discovery within each tested family
- **Sizing**: fractional Kelly reduced multiplicatively through CI width, lead time, win rate, portfolio heat, and drawdown cascades (fail-closed on NaN)

---

## Trading strategies

Four independent strategy families with distinct alpha profiles:

| Strategy | Edge source | Alpha decay |
|----------|------------|-------------|
| **Settlement Capture** | Observed fact post-peak temperature | Very slow |
| **Shoulder Bin Sell** | Retail cognitive bias (prospect theory → shoulder overpricing) | Moderate |
| **Center Bin Buy** | Model accuracy vs. market at estimating most likely bin | Fast |
| **Opening Inertia** | New market mispricing (first LP anchoring) | Fastest |

Per-strategy tracking is required because portfolio-level P&L masks which edges are being competed away.

---

## Risk management

Risk levels change runtime behavior — advisory-only risk is forbidden:

| Level | Behavior |
|-------|----------|
| GREEN | Normal operation |
| YELLOW | No new entries, continue monitoring |
| ORANGE | No new entries, exit at favorable prices |
| RED | Cancel all pending, sweep all active positions |

Overall risk = max of all individual risk signals. Computation error or broken truth input → RED. Fail-closed.

---

## Position lifecycle

```text
pending_entry → active → day0_window → pending_exit → economically_closed → settled
```

Terminal states: `voided`, `quarantined`, `admin_closed`.

Every cycle reconciles local state against on-chain truth:

| Condition | Action |
|-----------|--------|
| Local + chain match | SYNCED |
| Local exists, NOT on chain | VOID immediately |
| Chain exists, NOT local | QUARANTINE 48h |

---

## Data model

All persistent data falls into three layers:

| Layer | What | Isolation |
|-------|------|-----------|
| **World data** | External facts (forecasts, observations) | Shared, no mode tag |
| **Decision data** | Trading choices and outcomes | Shared + `env` discriminator |
| **Process state** | Mutable runtime state | Physically isolated per instance |

High and low temperature markets share city/date geometry but are **separate semantic families** — they do not share physical quantity, observation field, Day0 causality, calibration parameters, or replay identity.

---

## Getting started

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m pytest tests/
```

Some runtime paths require local databases, venue credentials, provider data, or operator configuration that are not committed. Data-dependent tests skip gracefully when local state is absent.

### Runtime entry points

| Entry point | Purpose |
|-------------|--------|
| `src/main.py` | Live daemon |
| `src/engine/cycle_runner.py` | Cycle orchestration |
| `src/engine/evaluator.py` | Candidate → decision pipeline |
| `src/execution/executor.py` | Live order placement |
| `src/engine/monitor_refresh.py` | Position monitoring |
| `src/execution/exit_triggers.py` | Exit logic |
| `src/execution/harvester.py` | Settlement and learning |

### Integrity checks

```bash
python3 scripts/topology_doctor.py --strict          # Registry parity and zone coverage
python3 scripts/topology_doctor.py --source           # Source rationale checks
python3 scripts/topology_doctor.py --tests            # Test topology audit
python3 scripts/topology_doctor.py --fatal-misreads   # Forbidden semantic shortcut checks
```

---

## Repository structure

```text
src/                  Runtime source (signal, contracts, execution, state, risk, engine)
tests/                Executable correctness and regression guards
scripts/              Topology doctor, replay parity, maintenance tools
architecture/         Machine-readable manifests, invariants, zones, task profiles
docs/authority/       Durable architecture and delivery law
docs/reference/       Domain model, math spec, module references
docs/operations/      Current-fact surfaces, active work packets, known gaps
config/               Runtime configuration and source/provenance registries
migrations/           SQL migrations defining canonical DB schema
state/                Runtime databases and projections (local, not committed)
```

---

## For agents

This repository is maintained by AI coding agents with a structured change-control layer. 

MUST READ `AGENTS.md` and `workspace_map.md` 
Run `python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>` for a scoped context pack
