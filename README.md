# Zeus

**Quantitative trading engine for weather-settlement prediction markets on Polymarket.**

Zeus converts atmospheric ensemble forecasts into calibrated probabilities, identifies edges against market prices, sizes positions via fractional Kelly, and executes through the Polymarket CLOB — all while enforcing strict contract semantics, source provenance, and dual-track temperature identity end-to-end.

---

## How it works

Zeus trades **discrete settlement contracts** on daily high/low temperatures.

### Strategy of record (2026-06-09)

The live forecast→edge→size path is the **replacement_forecast** chain (authority `docs/authority/replacement_final_form_2026_06_09.md`):

```text
contract semantics
  → source truth (settlement provider, station, observation field)
  → per-model walk-forward empirical-Bayes de-bias (bayes_precision_fusion.eb_bias, λ=n/(n+8))
  → T2 Bayesian precision fusion, Ledoit-Wolf Σ (bayes_precision_fusion.fuse_bayes_precision_posterior)
  → σ_pred = max(1.0°C, √(fused.sd²+σ_resid²))
  → settlement-preimage bin q (emos.bin_probability_settlement, q_shape fused_normal_direct)
  → q_lcb floor (Wilson z=1.645) → edge → BH FDR (per tested-family)
  → fractional Kelly sizing (dynamic cascade multiplier × DDD coverage discount)
  → execution via Polymarket CLOB → monitoring / exit → settlement reconciliation
  → learning (without hindsight leakage)
```

Live entry `src/engine/event_reactor_adapter.py` `_replacement_authority_probability_and_fdr_proof`; q built and persisted by `src/data/replacement_forecast_materializer.py` `_insert_posterior`; the single settlement integrator is `src/calibration/emos.py` `bin_probability_settlement`.

### Baseline (legacy chain — diagnostics only since 2026-06-12)

The legacy 51-ENS chain still runs as an **independent baseline for diagnostics and for strategies genuinely on baseline q**. It no longer caps or vetoes the live replacement q (Wave-2 single-q-authority cut, commit 479cb34446): the former `min(proof.q_lcb_5pct, replacement_hook_result.effective_q_lcb)` join is deleted; the baseline value is carried as `baseline_q_lcb_reference` receipt provenance. Regime law: `docs/authority/regime_unification_2026-06-12.md` (U1):

```text
51 ENS members → analytic_p_raw_vector_from_maxes (closed-form Gaussian-mixture;
  10k-MC p_raw_vector_from_maxes retired) → Extended Platt → P_cal →
  market_fusion.compute_posterior (model_only_v1 — NO market-prior blend live) → bootstrap q_lcb
```

ENS bias correction (`src/calibration/ens_bias_model.py`; flag `settings.bias_correction_enabled`, default `false`) and the Data Density Discount remain baseline-path features. The α-weighted `P_posterior = α·P_cal + (1−α)·P_market` is **spec-only**; the live `model_only_v1` posterior takes no market input.

Everything starts with the **venue contract** — city, local date, temperature metric, unit, bin topology, settlement source, and provider-specific settlement transform. Forecast probability is economically meaningful only after these semantic obligations are pinned.

### Why settlement is discrete

Polymarket weather markets settle on integer temperatures reported by the settlement provider (typically Weather Underground). A real temperature of 74.45°F → sensor reads 74.2°F → METAR rounds → WU displays 74°F. Zeus models this full chain explicitly via Monte Carlo rather than assuming continuous distributions.

Three bin types exist:

| Type | Example | Resolution |
|------|---------|------------|
| `point` | 10°C | Resolves on exactly {10} |
| `finite_range` | 50-51°F | Resolves on {50, 51} |
| `open_shoulder` | 75°F+ | Unbounded — not a symmetric range |

### Calibration (baseline path)

Extended Platt below is the **legacy baseline / LCB-cap** calibration; the live q is built by `emos.bin_probability_settlement` (see Strategy of record above). Raw ensemble probabilities are biased — overconfident at long lead times, underconfident near settlement. Zeus uses Extended Platt scaling with lead-time as an input feature:

```text
P_cal = sigmoid(A·logit(P_raw) + B·lead_days + C)
```

The `B·lead_days` term triples effective training data per bucket vs. simple lead-time bucketing and prevents overtrade of stale forecasts.

Before calibration, raw ensemble member extrema are bias-corrected: an **empirical-Bayes ENS bias model** shrinks the TIGGE structural prior toward live OpenData settled residuals (SNR-gated, so a noisy/uncertain bias is not applied), with a predictive-error layer that also widens the Monte-Carlo draw and transports the 0.5°→0.25° grid-resolution variance. See `src/calibration/ens_bias_model.py`, `src/calibration/ens_error_model.py` (PRs #334/#336). **This step is flag-gated (`settings.bias_correction_enabled`, default `false`) and not yet active in production — activation pending.**

### Edge detection and sizing

- **Model-market fusion** (baseline path; spec-only — live runs `model_only_v1` with NO market blend, `src/strategy/market_fusion.py` `compute_posterior`): `P_posterior = α × P_cal + (1 - α) × P_market`, where α is dynamically computed from calibration maturity, ensemble spread, and lead time (clamped to [0.20, 0.85])
- **Uncertainty**: double-bootstrap propagates ensemble sampling noise, instrument noise (σ ≈ 0.2–0.5°F), and calibration parameter uncertainty
- **Selection**: Benjamini-Hochberg FDR controls false discovery within each tested family
- **Sizing**: fractional Kelly reduced multiplicatively through CI width, lead time, win rate, portfolio heat, and drawdown cascades (fail-closed on NaN)
- **Data Density Discount (DDD)**: when a city's observation coverage is thin or its oracle mismatch rate is high, a two-rail trigger applies a continuous Kelly discount (and hard-halts below an absolute coverage floor); spec `docs/reference/zeus_oracle_density_discount_reference.md`, code `src/oracle/data_density_discount.py`

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
| Local exists, chain snapshot CHAIN_EMPTY (fresh, complete) | VOID |
| Local exists, chain snapshot CHAIN_UNKNOWN (stale / missing API response) | NO-OP — never void on a degraded snapshot |
| Chain exists, NOT local | Emit `ChainOnlyFact` (typed review entry); entry stays blocked, `review_state` escalates UNRESOLVED→EXPIRED at 48h (operator-resolved) |

`CHAIN_EMPTY` vs `CHAIN_UNKNOWN` is the snapshot completeness classifier
(`src/state/chain_state.py.ChainSnapshotCompleteness`). Treating a missing
API response as `CHAIN_EMPTY` would void real live positions on degraded
infra; the void rule applies ONLY to authoritatively empty snapshots.
See `docs/plans/2026-05-27-chain-local-position-model-refactor.md` (PR C0,
Finding 1) for the timestamp-split that keeps the classifier honest.

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

### Runtime entry points

| Entry point | Purpose |
|-------------|--------|
| `src/main.py` | Live daemon |
| `src/engine/cycle_runner.py` | Cycle orchestration |
| `src/engine/evaluator.py` | Candidate → decision pipeline |
| `src/execution/executor.py` | Live order placement |
| `src/engine/monitor_refresh.py` | Position monitoring |
| `src/execution/exit_safety.py` | Exit logic |
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
docs/operations/      Current-fact surfaces and active work packets
docs/to-do-list/      Known gaps, active checklists, and audit queues
config/               Runtime configuration and source/provenance registries
migrations/           SQL migrations defining canonical DB schema
state/                Runtime databases and projections (local, not committed)
```

---

## For agents

This repository is maintained by AI coding agents with a structured change-control layer. 

MUST READ `AGENTS.md` and `workspace_map.md` 
Run `python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>` for a scoped context pack

You may need to install new environment in your virtual machine or test may fail.

### Packet Runtime (`zpkt`)

A unified CLI collapses packet lifecycle, scope tracking, soft-warn enforcement, and closeout into a single surface. One-time setup (`zpkt setup`) installs the in-repo githook path. Daily use:

```bash
python3 scripts/zpkt.py setup                    # one-time per clone
python3 scripts/zpkt.py start <slug>             # new packet + isolated worktree
python3 scripts/zpkt.py status                   # one-call digest (5-min cached)
python3 scripts/zpkt.py scope add <files>        # widen in_scope as you discover
python3 scripts/zpkt.py commit -m "..." [files]  # commit with soft-warn
python3 scripts/zpkt.py close                    # closeout: receipt + status flip
```

Full protocol: [`docs/operations/packet_scope_protocol.md`](docs/operations/packet_scope_protocol.md).