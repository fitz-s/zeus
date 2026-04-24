# Zeus

**Zeus is an agent-managed quantitative trading system for weather-settlement prediction markets on Polymarket.**

It is not a generic weather dashboard, a public trading bot, a plug-and-play forecasting library, or a cleaned-up academic demo. Zeus is a research and operations system built around a harder problem:

> preserving the causal chain from market contract semantics to source truth, forecast signal, calibrated probability, execution, monitoring, settlement, and learning — while keeping a dual-track (high/low temperature) identity separated end-to-end.

The system trades **discrete settlement contracts**, not continuous temperatures. A correct model of Zeus starts with the venue contract: city, local date, unit, bin topology, shoulder bins, source text, rounding or containment rule, and settlement authority. Forecast accuracy matters only after those semantic obligations are pinned down.

---

## What Zeus is

Zeus is a live-runtime architecture for weather-linked prediction-market contracts. Its core money path is:

```text
contract semantics
  -> source truth
  -> ensemble forecast signal (51 ECMWF ENS members)
  -> Monte Carlo sensor-noise + rounding simulation -> P_raw
  -> Extended Platt calibration (temporal-decay aware) -> P_cal
  -> α-weighted model-market fusion -> P_posterior
  -> double-bootstrap confidence intervals -> edge + p-value
  -> BH FDR filtering (per tested-family, not per-cycle)
  -> fractional Kelly sizing (dynamic cascade multiplier)
  -> execution via Polymarket CLOB
  -> monitoring / exit
  -> settlement truth reconciliation
  -> learning (without hindsight leakage)
```

The system is designed around the fact that most catastrophic failures in this domain are not syntax errors. They are **semantic category errors**:

* using the wrong settlement source;
* treating an airport station as the city settlement station without proof;
* mixing daily-high and daily-low tracks;
* confusing Day0 live monitoring with final settlement truth;
* using historical hourly data as if it were settlement data;
* applying the wrong bin, shoulder, rounding, or containment semantics;
* allowing backtest, shadow, or report surfaces to outrank canonical runtime truth.

Zeus therefore treats **contract semantics, source provenance, lifecycle truth, and risk control** as first-class runtime objects — not advisory documentation, but executable contracts with blocking tests.

---

## Core probability theory

### Why settlement is integer, not continuous

Polymarket weather markets settle on Weather Underground's reported daily high/low. WU reports **whole degrees** (°F or °C). The physical chain is:

```text
atmosphere -> NWP ensemble member -> ASOS sensor (σ ≈ 0.2–0.5°F) -> METAR rounding -> WU integer
```

This means probability mass concentrates at bin boundaries in ways that mean-based continuous models miss entirely. Zeus's Monte Carlo explicitly simulates the full chain from ensemble member through sensor noise to integer display.

### Discrete settlement support (semantic atom)

Discrete settlement support is not an implementation detail — it is an **architectural primitive**. Any work touching uncertainty, calibration, hit-rate analysis, edge math, pricing, or settlement interpretation must treat settlement support as authority before reasoning from continuous physical intuition.

| Concept | Definition |
|---------|------------|
| `bin_contract_kind` | `point` (single integer), `finite_range` (fixed integer set), or `open_shoulder` (unbounded) |
| `bin_settlement_cardinality` | Number of discrete settled values that resolve the bin to YES |
| `settlement_support_geometry` | The exact discrete support implied by the venue contract |

Current market law: Fahrenheit non-shoulder bins are `finite_range` with cardinality 2 (e.g. `50-51°F` resolves on `{50, 51}`). Celsius non-shoulder bins are `point` with cardinality 1. Shoulder bins are `open_shoulder` — they are NOT ordinary finite bins.

### Extended Platt calibration with temporal decay

Raw ensemble probabilities are systematically biased — overconfident at long lead times, underconfident near settlement. Zeus uses an Extended Platt model:

```text
P_cal = sigmoid(A·logit(P_raw) + B·lead_days + C)
```

`lead_days` is an **input feature**, not a bucket dimension. This triples positive training samples per bucket vs. simple lead-time bucketing. Without the `B·lead_days` term, Zeus overtrades stale forecasts.

### Model-market fusion (α-weighted posterior)

```text
P_posterior = α × P_cal + (1 - α) × P_market
```

α is dynamically computed per decision from calibration maturity, ensemble spread, and lead time, clamped to `[0.20, 0.85]` — never fully trusting either source. Market price uses VWMP (Volume-Weighted Micro-Price), not raw mid-price.

### Triple-source uncertainty via double-bootstrap

Edge confidence intervals propagate **three independent uncertainty sources**:

1. **Ensemble sampling uncertainty** — which 51 NWP members the model produced
2. **Instrument noise** — ASOS sensor measurement error (Monte Carlo with σ ≈ 0.2–0.5°F)
3. **Calibration parameter uncertainty** — Platt coefficients are estimated, not known

P-values come from bootstrap empirical distribution (`p = mean(bootstrap_edges ≤ 0)`), never from normal approximation — the distributions are non-Gaussian near bin boundaries.

### FDR-controlled edge selection

Benjamini-Hochberg controls the **false discovery rate** within each active tested family (market/snapshot/direction), not the whole cycle as one hypothesis batch. Only edges surviving BH proceed to Kelly sizing.

### Kelly sizing with dynamic cascade

Base fractional Kelly (`f* = (P_posterior - entry_price) / (1 - entry_price)`) is reduced multiplicatively through five risk factors: CI width, lead time, win rate, portfolio heat, and drawdown. The cascade floor is fail-closed: NaN or non-positive multiplier raises `ValueError` rather than producing a fabricated floor.

---

## Four independent strategies

Zeus's edges fall into four categories with fundamentally different alpha profiles. Per-strategy tracking is required because portfolio-level P&L masks which strategies are working and which are being competed away.

| Strategy | Edge source | Alpha decay | Requires full signal chain? |
|----------|------------|-------------|----------------------------|
| **A: Settlement Capture** | Observation speed — temperature already crossed bin post-peak | Very slow | No — observation-based, not predictive |
| **B: Shoulder Bin Sell** | Retail cognitive bias (prospect theory → shoulder overpricing) | Moderate | No — rough climatological estimates suffice |
| **C: Center Bin Buy** | Model accuracy vs. market at estimating most likely bin | Fastest | Yes — full ENS → Platt → bootstrap → FDR |
| **D: Opening Inertia** | New market mispricing (first liquidity provider anchoring) | Fastest | Partial — market scan + model signal |

When a strategy's edge trend is negative and sustained for 30+ days, the correct response is to reduce capital allocation — not to refine the model.

---

## Agent-managed development

Zeus is built and maintained by AI coding agents. This is not a convenience choice — it is a structural necessity. The system has too many interacting semantic surfaces (contract law, source provenance, calibration families, execution lifecycle, risk control, dual-track identity, live/backtest separation) for ad-hoc human edits to be reliably safe.

The repository therefore ships a complete **agent operating system** — not just rules, but executable navigation, routing, and enforcement infrastructure.

### How agents navigate the codebase

A cold-start agent arriving at this repo follows a deterministic boot path:

```text
1. AGENTS.md            → operating law, authority order, durable rules
2. workspace_map.md     → visibility classes, directory router
3. Scoped AGENTS.md     → local router for the directory being touched
4. Machine manifests    → architecture/*.yaml (zones, invariants, topology)
5. Task boot profile    → architecture/task_boot_profiles.yaml classifies the
                          task and loads required proof questions + current fact
                          surfaces before the agent reads any code
6. Topology Doctor      → python scripts/topology_doctor.py --navigation
                          --task "<task>" --files <files>
                          produces a targeted context pack: which files matter,
                          what law applies, what downstream surfaces are affected
7. Code Review Graph    → .code-review-graph/graph.db provides caller/callee
                          maps, blast-radius analysis, impacted tests, and
                          review order — all as derived context, never authority
```

This means an agent does not guess what to read. The topology system **tells it** — and tells it what *not* to read (archives, stale reports, runtime scratch).

### How destructive operations are prevented

The most dangerous agent failure mode is not a syntax error. It is a **locally plausible but globally destructive** semantic change — for example, treating an airport station as the city settlement station, mixing high/low temperature tracks, or promoting backtest output to live authority.

Zeus prevents this through five interlocking layers:

| Layer | Mechanism | What it catches |
|-------|-----------|----------------|
| **Fatal misread antibodies** | `architecture/fatal_misreads.yaml` — machine-readable list of semantic equivalences that look correct but are catastrophically wrong | "WU hourly == settlement" / "airport ASOS == city station" / "Day0 observation == final settlement" |
| **Planning lock** | Changes to `architecture/**`, `src/state/**`, `src/control/**`, `.github/workflows/**`, or cross-zone edits require explicit plan evidence before any code is touched | Prevents impulsive changes to truth-ownership, lifecycle, governance, or CI |
| **Zone boundary enforcement** | Five zones (K0 Frozen Kernel → K4 Experimental) with AST-verified import boundaries; cross-zone imports are machine-detected | Stops K4 experimental code from importing K0 kernel internals |
| **System invariants** | `architecture/invariants.yaml` — 22 machine-enforced rules including "risk must change behavior" (INV-05), "LLM output is never authority" (INV-10), "DB commits before JSON exports" (INV-17) | Catches violations at the contract level, not the bug level |
| **Authority hierarchy** | Executable source > machine manifests > architecture law > current facts > reference > evidence > derived context. An agent memory or chat transcript *never* outranks repo truth | Prevents authority drift from stale context or hallucinated rules |

The enforcement is not advisory. `topology_doctor.py --planning-lock` blocks changes that lack plan evidence. `topology_doctor.py --strict` flags unregistered files, schema violations, and zone coverage gaps. `topology_doctor.py --fatal-misreads` machine-checks that no forbidden semantic shortcut has been introduced.

### Code Review Graph

Zeus integrates a **Code Review Graph** (`.code-review-graph/graph.db`) — a structural dependency graph that provides:

* **Caller/callee maps** — who calls this function, and what does it call?
* **Blast-radius analysis** — if I change this file, what downstream files and tests are affected?
* **Review order** — what should a reviewer read first to understand a change?
* **Impacted test discovery** — which tests exercise the changed code paths?

The graph is **derived context, not authority** — it guides navigation but cannot waive planning locks, override manifests, or prove semantic correctness. When the graph is stale, agents fall back to topology doctor digests, source rationale, and targeted tests. Zeus integrates the official upstream graph tooling rather than inventing custom refresh mechanisms.

### Architecture zone model

All source code lives in one of five zones with enforced import boundaries:

```text
K0  Frozen Kernel   — semantic atoms, lifecycle law, canonical truth model
K1  Governance       — policy, risk actions, overrides, strategy governance
K2  Runtime          — orchestration, execution, reconciliation, projections
K3  Extension        — math, signal, calibration, analysis
K4  Experimental     — disposable experiments, notebooks
```

Cross-zone edits require a governance packet. Zone boundary violations are machine-detected via AST import analysis.

### Key system invariants

From `architecture/invariants.yaml` (selected):

* **INV-01**: Exit is not local close — monitor decisions must not directly imply terminal economic closure
* **INV-05**: Risk must change behavior — advisory-only risk outputs are theater
* **INV-06**: Point-in-time truth beats hindsight truth — learning preserves decision-time truth
* **INV-10**: LLM output is never authority — generated code is valid only after packet, gates, and evidence
* **INV-14**: Dual-track identity spine — every row carries `temperature_metric`, `physical_quantity`, `observation_field`, `data_version`
* **INV-17**: DB commits before JSON exports — on crash, DB wins and JSON rebuilds from projection
* **INV-19**: RED risk cancels and sweeps — not just entry-block, but active position exit

---

## Authority model

Zeus separates **authority**, **current facts**, **reference**, **evidence**, and **derived context**.

At a high level:

1. executable source, tests, DB/event/projection truth;
2. machine manifests under `architecture/**`;
3. durable architecture law under `docs/authority/**`;
4. current-fact surfaces under `docs/operations/**`;
5. durable reference under `docs/reference/**`;
6. reports, artifacts, packets, and historical evidence;
7. derived graph/topology/context caches.

This distinction matters. A report may reveal a failure, but it does not become law. A graph may suggest a blast radius, but it does not prove source semantics. A current-fact document may guide a packet, but it expires. A chat transcript or agent memory never outranks repo truth.

---

## Core design commitments

### 1. Contract-first reasoning

Zeus does not begin with a weather model. It begins with the traded contract: city, local calendar date, temperature metric (high/low), unit (°F/°C), settlement source and station, bin topology, shoulder bins, rounding rule, and authority status. Only after that does forecast probability become economically meaningful.

### 2. Source provenance as runtime truth

Weather APIs are not interchangeable. Zeus tracks source family, station/product, observation field, physical quantity, data version, writer, audit reference, and reconstruction method. Settlement rows are expected to be re-auditable from their provenance sidecar.

### 3. Dual-track high/low identity

Daily high and daily low markets share city/date geometry but not physical quantity, observation field, Day0 causality, calibration family, or replay identity. High and low tracks are separate semantic families throughout the entire pipeline.

### 4. Canonical runtime truth

Derived JSON, CSV, reports, notebooks, and graph outputs are not canonical runtime truth. For live behavior, DB/event/projection truth is the inner authority surface. Chain/CLOB facts outrank local cache:

```text
Chain (Polymarket CLOB) > event log / canonical DB > local cache / projection exports
```

### 5. Fail-closed risk

Risk must change behavior. `GREEN`: normal. `YELLOW`: no new entries. `ORANGE`: no new entries, restricted exits. `RED`: cancel pending orders and sweep active positions. Truth unavailability fails new-entry lanes closed.

### 6. Live / backtest / shadow separation

Live may act. Backtest may evaluate. Shadow may observe. Promotion from backtest/shadow to live requires evidence, explicit approval, rollback planning, and a governance packet.

### 7. Translation loss law

Natural language → code translation has systematic, irreducible information loss. Functions, types, and tests survive sessions at ~100%; design philosophy survives at ~20%. Therefore every session encodes insights as **code structure** (types, tests, contracts), not documentation. `SettlementSemantics.for_city()` and `test_celsius_cities_get_celsius_semantics()` are executable forms of design intent — they enforce correctness without being understood.

### 8. Structural decisions over patches

When facing N surface-level problems, do not write N patches. Find K structural decisions where K << N. The test: does it eliminate a *class* of problems, or just one instance? If one instance, it is a patch. If a class, it is a structural decision.

---

## Data provenance model

Zeus classifies all persistent data into three layers with distinct isolation semantics:

| Layer | What | Isolation rule | Examples |
|-------|------|----------------|----------|
| **World data** | External facts independent of Zeus's decisions | Shared, no mode tag | ENS forecasts, calibration pairs, observations |
| **Decision data** | Records of Zeus's choices and outcomes | Shared + `env` discriminator | `trade_decisions`, `chronicle`, `position_events` |
| **Process state** | Mutable runtime state of a running instance | Physically isolated via `state_path()` | positions, strategy tracker, risk state |

World data can be shared because it is objective. Decision data must be tagged so backtest decisions never contaminate live analytics. Process state must be physically separate because concurrent instances writing the same file corrupt both.

---

## Current status

This branch is an active data-improvement and source-provenance branch. Current work has focused on settlement reconstruction, provenance hardening, high/low identity separation, data-readiness audit surfaces, runtime safety gates, and agent boot surfaces.

Some capabilities are intentionally gated, dormant, or incomplete. For current posture, read `docs/operations/current_data_state.md`, `docs/operations/current_source_validity.md`, and `docs/operations/known_gaps.md`. These are current-fact surfaces that expire and must be refreshed before present-tense claims.

---

## What to review

A useful review should judge Zeus on these questions:

1. Does the system preserve correct contract semantics from market text through probability and settlement?
2. Are source roles separated correctly: settlement, Day0 monitoring, historical hourly, and forecast-skill?
3. Does provenance make settlement and calibration rows re-auditable?
4. Are high and low temperature tracks structurally separated?
5. Does the execution lifecycle distinguish entry, active position, Day0 window, exit intent, economic closure, settlement, void, and quarantine?
6. Do risk states actually constrain behavior?
7. Are live, backtest, and shadow boundaries enforced?
8. Do agent-management rules prevent authority drift, stale-context edits, and local whack-a-mole patches?
9. Are current-fact surfaces clearly separated from durable architecture law?
10. Does the topology system enforce registry parity, zone boundaries, and planning locks?
11. Are known gaps explicit enough to prevent false confidence?

A less useful review would treat the repo as a turnkey product and judge it mainly on installation smoothness. Zeus is not trying to be a public package.

---

## Recommended reading path

For architecture / professor review:

1. `README.md` — this orientation.
2. `docs/reference/zeus_domain_model.md` — compact domain model with worked examples.
3. `docs/authority/zeus_current_architecture.md` — current runtime semantic law.
4. `docs/authority/zeus_current_delivery.md` — current change-control and agent-delivery law.
5. `architecture/invariants.yaml` — machine-enforced system invariants.
6. `architecture/fatal_misreads.yaml` — forbidden semantic shortcuts.
7. `docs/operations/current_data_state.md` — current data posture, if fresh.
8. `docs/operations/current_source_validity.md` — current source-validity posture, if fresh.
9. `docs/operations/known_gaps.md` — active gap register.
10. Targeted code under `src/` and tests under `tests/`.

For agentic coding work, start with `AGENTS.md` and follow the scoped boot path. Do not treat this README as an authority replacement.

---

## Repository map

```text
src/                  Runtime source code: signal, contracts, execution, state, risk, engine.
tests/                Executable checks and regression guards.
scripts/              Topology doctor, replay parity, provisioning, and maintenance tools.
architecture/         Machine-readable manifests, invariants, zones, task profiles, constraints.
docs/authority/       Durable human-readable architecture and delivery law.
docs/reference/       Durable conceptual and module references (domain model, math spec).
docs/operations/      Current-fact surfaces, active packet pointers, known gaps.
docs/runbooks/        Operator runbooks.
docs/reports/         Diagnostic or historical evidence, not active authority.
config/               Runtime configuration seeds and source/provenance registries.
data/                 Small tracked examples or curated data only; full/private data should not live here.
raw/                  Raw provider captures are local/private by default.
state/                Runtime databases, projections, locks, heartbeats, and telemetry; local/private by default.
migrations/           SQL migrations defining canonical DB schema (kernel authority).
```

---

## Development notes

This repository is Python-based. Tests and static checks are the preferred public reproducibility surface.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/
```

Topology verification (machine-checkable integrity):

```bash
python scripts/topology_doctor.py --strict          # Registry parity, schema, zone coverage
python scripts/topology_doctor.py --source           # Source rationale checks
python scripts/topology_doctor.py --tests            # Test topology audit
python scripts/topology_doctor.py --scripts          # Script manifest audit
python scripts/topology_doctor.py --history-lore     # Antibody reference checks
python scripts/topology_doctor.py --fatal-misreads   # Forbidden shortcut checks
python scripts/topology_doctor.py --planning-lock --changed-files <files...>  # Planning gate
python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory  # Registry sync
```

Some runtime paths require local databases, venue credentials, provider data, or operator configuration that are intentionally not committed. Data-dependent or live-dependent tests should either use fixtures or clearly fail/skip when local state is absent.

---

## Safety and non-goals

Zeus is not financial advice and does not guarantee profitability.

Non-goals:

* making Zeus a one-command public trading bot;
* hiding known gaps to look finished;
* replacing source provenance with generic weather API availability;
* allowing AI agents to override runtime law;
* treating backtest or shadow evidence as live promotion by default;
* optimizing forecast accuracy while ignoring settlement semantics.

---

## Data and public-surface policy

Runtime state, credentials, local telemetry, raw vendor captures, full settlement corpora, and derived review caches are intentionally excluded from the public tree.

Reviewable examples may be committed when they are scrubbed, small enough to inspect, clearly labeled as examples, not required to reconstruct private trading state, and not presented as current source truth unless backed by a fresh current-fact surface.

The absence of raw data or local DB files from the repository is intentional and should not be read as absence of system design.

---

## Review stance

Zeus is best evaluated as an agent-managed quant system under active maturation.

The central question is not whether every subsystem is already final. The central question is whether the system's architecture correctly protects the money path:

```text
market contract -> source truth -> probability -> execution -> settlement -> learning
```

That is the standard this repository is built around.
