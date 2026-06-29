# Zeus Glossary

Canonical definitions for core terms used throughout the Zeus codebase and docs.
Each definition links to the authoritative source where it is formally defined.
For a system overview see [`README.md`](../../README.md); for operating law see
[`AGENTS.md`](../../AGENTS.md).

Terms marked **[live path]** are on the active trading path. Terms marked
**[diagnostic]** describe the legacy baseline, which runs as a comparison only
and cannot cap or veto the live probability.

---

## Terms

### replacement_forecast chain

The canonical live probability pipeline, also called the *replacement forecast*
or *replacement chain*. It constructs the single authoritative per-bin probability
`q` by: (1) empirical-Bayes de-biasing each ensemble model against its settled
history, (2) Ledoit–Wolf shrinkage of the model covariance, (3) Bayesian
precision-weighted fusion of all de-biased models into a single `(μ*, V*)`, (4)
settlement-exact preimage integration via EMOS to produce per-bin probabilities.
The output is a single `q` per (city, date, metric, bin). There is no multi-regime
fallback and no shadow complement on the live path.

**Authoritative source:** [`docs/authority/replacement_final_form_2026_06_09.md`](../authority/replacement_final_form_2026_06_09.md)  
**Implementation:** `src/forecast/bayes_precision_fusion.py` → `src/forecast/emos.py`  
**Related invariant:** INV (see `architecture/invariants.yaml` for probability-chain entries)

---

### q_lcb

The *conservative lower bound* on a bin's replacement-chain probability. Drawn
by sampling from the parameter posterior of the fused `(μ*, V*)`, renormalizing
each draw to sum to one across bins, then taking the 5th percentile (z = 1.645).
The renormalize-before-quantile step is load-bearing: lower-bounding bins
independently would hollow out the modal bin under draws where the peak shifts one
bin. The selection calibrator may further tighten `q_lcb` based on the realized
settlement hit-rate of the *raw signal bucket* that would admit the trade
(`src/decision/selection_calibrator.py`).

**Authoritative source:** [`docs/authority/replacement_final_form_2026_06_09.md`](../authority/replacement_final_form_2026_06_09.md) §q_lcb; `README.md` §"A lower bound that stays coherent"  
**Implementation:** `src/decision/qlcb_reliability_guard.py`

---

### Day0 / day0_window

Overloaded across two distinct concepts — context determines which applies:

1. **Lifecycle phase (`day0_window`)**: a position state entered once the
   settlement day is underway (the high can only go up). The conditioning
   uses only observations available at decision time; bins entirely below the
   running high carry exactly zero probability, built into the integrator.
   See `src/state/lifecycle_manager.py`.

2. **Low-causality monitoring window**: the period during which live station
   observations are incorporated to condition the forecast. Referenced as
   *Day0 low causality* in INV-16. See `src/contracts/day0_observation_context.py`.

**Authoritative source:** [`docs/authority/zeus_current_architecture.md`](../authority/zeus_current_architecture.md) §4.3; `README.md` §"Causality on the day of settlement"  
**Related invariant:** INV-16 (Day0 low causality)

---

### settlement preimage

The set of continuous temperature values that round to a given market bin label
under the city's rounding rule. Because markets settle on rounded integers,
the probability of a bin equals the mass of the forecast distribution over the
preimage — not over the nominal bin edges. Each settlement convention declares
its preimage once: `wmo_half_up` maps label X to `[X−0.5, X+0.5)`, `oracle_truncate`
(Hong Kong) to `[X, X+1)`, `ceil` to `(X−1, X]`. Open-ended bins integrate a
one-sided tail. See `README.md` §"Settlement-exact probability: integrate the
preimage, not the bin" for the full preimage table.

**Authoritative source:** [`docs/reference/zeus_market_settlement_reference.md`](zeus_market_settlement_reference.md); `src/contracts/settlement_semantics.py`

---

### family (high/low semantic family)

A *family* is the set of all tradeable bin positions for a single (city,
target_date, metric) cell — e.g., all YES/NO positions on Tokyo's daily high for
a given date. High-temperature and low-temperature markets for the same city and
date are **separate families**: different physical quantity, different observation,
different calibration, never sharing state or selection decisions. Within a family,
FDR control and selection operate over the complete set of tested bins, not just
survivors.

In code, `family_key` typically returns the (city, target_date, metric) tuple.
INV-22 pins `make_family_id()` as the canonical constructor. The term is
overloaded in a few contexts:

- *FDR family*: the complete set of bins tested together for Benjamini-Hochberg.
- *City-family key*: the full identity in the event store, sometimes including
  `strategy_key`.
- *Calibration family*: the (city, metric) pair that groups Platt training.

**Authoritative source:** `architecture/invariants.yaml` INV-22; `src/decision/family_decision_engine.py`; `src/calibration/manager.py`

---

### EDLI

*Event-Driven Live Ingest* — the event-sourced opportunity discovery and execution
subsystem. EDLI listens for `ForecastSnapshotReadyTrigger` and market-channel events
and drives decisions reactively rather than on a fixed scheduler cycle. It adds an
online event loop alongside the legacy scheduler cycle (it does not replace the
scheduler; `src/main.py` remains scheduler owner for heartbeat, source health,
harvester, and market discovery fallback).

EDLI has three DB ownership rules: `opportunity_events` / no-trade regret live in
the world DB; executable snapshots and `book_hash_transitions` live in the trades
DB; no cross-DB foreign keys.

**Authoritative source:** `docs/operations/edli_v1/REFERENCE_event_sourced_opportunity.md`; `docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md`  
**Implementation root:** `src/execution/edli_absence_resolver.py`, `src/execution/edli_presence_resolver.py`, `src/execution/edli_resting_absorbed_resolver.py`

---

### substrate

The venue-specific CLOB (central limit order book) state monitor — a live view of
the Polymarket order book for a given market token. `substrate_observer.py` queries
and caches CLOB state (bids, asks, last trade) and makes it available to the
decision and execution layers without holding a world write mutex during the I/O.
"Substrate" is an internal Zeus term; externally it corresponds to the Polymarket
CLOB book state.

**Implementation:** `src/data/substrate_observer.py`

---

### K0 – K3 zones

Four architectural zones classifying invariants and code surfaces by stability and
ownership, used in `architecture/invariants.yaml`:

| Zone | Meaning |
|------|---------|
| `K0_frozen_kernel` | Immutable semantic contracts: settlement semantics, identity types, append-first write order. Changes require operator packet and proof gate. |
| `K1_governance` | Governance and schema surface: DB ownership, migration discipline, change-control gates. |
| `K2_runtime` | Live trading path: probability chain, risk levels, lifecycle state machine, position truth. |
| `K3_extension` | Extension surface: strategies, oracle penalties, scoring models that sit outside the core kernel. |

**Authoritative source:** `architecture/invariants.yaml` (zones field on each invariant entry)

---

### ROI frontier

The selection criterion used to choose one trade among FDR-passing survivors in a
family. Zeus maximizes *guarded edge per dollar at risk* — the lower-quantile
log-growth tiebreak picks the candidate with the best return per unit of capital
committed (capital efficiency over one bloated position). Implemented as
`roi_frontier` in `src/decision/family_decision_engine.py`.

**Implementation:** `src/decision/family_decision_engine.py` lines ~70, 80, 427

---

### selection calibrator

The settlement-graded admission gate that sits at the `q_lcb` seam, upstream of
`edge_lcb > 0` and BH-FDR. It groups every candidate trade into a cell keyed on
the *raw side probability* bucket (the actual admission signal, not the derived
`q_lcb`) and serves a Wilson lower bound on the empirical settlement hit-rate of
that cell. Absent artifact, stale artifact, or fewer than the minimum-N threshold
emits `q_safe = 0` (no trade) — never a fallback to the raw center-bootstrap
`q_lcb`. It is the primary defense against adverse selection of overconfident bins.

**Implementation:** `src/decision/selection_calibrator.py`  
**Related:** `README.md` §"Beating the winner's curse"

---

### fractional Kelly

The Kelly criterion multiplied down by an independent cascade of factors —
confidence-interval width, lead time, win rate, portfolio heat, drawdown, and
data-density — that compound geometrically and fail closed: any NaN or malformed
input yields no trade. "Full Kelly" is the theoretical optimal, but it maximizes
expected log-growth only in the limit and is reckless under parameter uncertainty;
the multiplier cascade produces a practical fraction (typically 20–50% of full
Kelly). Per-city asymmetric loss preferences enter as additional Kelly multipliers,
not as DDD floor overrides (see `zeus_kelly_asymmetric_loss_handoff.md`).

**Authoritative source:** [`docs/reference/zeus_kelly_asymmetric_loss_handoff.md`](zeus_kelly_asymmetric_loss_handoff.md); [`docs/authority/exit_portfolio_execution_authority_2026-06-13.md`](../authority/exit_portfolio_execution_authority_2026-06-13.md)  
**Implementation:** `src/strategy/kelly.py`  
**Related:** `README.md` §"Fractional Kelly, multiplied down"

---

### FDR (false discovery rate control)

Benjamini-Hochberg procedure applied across the *complete* tested family (~200 bins
per cycle), not only survivors. Each bin's p-value is ranked; the rejection
threshold is set at `(k/m) × α` where k is rank and m is total tests. Scanning
many bins per cycle means some will look like edges by chance; per-test significance
would flood the book with false positives. BH-FDR caps the expected fraction of
false positives among all declared edges.

**Implementation:** `src/strategy/fdr_filter.py`; `README.md` §"False-discovery control"

---

## Naming & synonyms

The following synonym sets reflect historical naming drift. The **canonical** term
is the one to use in new code and docs.

| Canonical term | Aliases / variants | Notes |
|----------------|--------------------|-------|
| `replacement_forecast` | `replacement_0_1` (legacy SQL suffix), `bayes_precision_fusion` (function name in `src/forecast/`), `fused-q` (prose shorthand), `q_construction` (authority doc §1e), `T2 Bayesian fusion` (prose) | All refer to the live q-construction path. Grep for `bayes_precision_fusion` to find the implementation; `replacement_forecast` to find authority doc references. |
| `shadow` (observe-only mode) | `diagnostic` (diagnostic-only baseline), `legacy` (legacy-only, diagnostic-only) | In flag names and NC-10, "shadow" means observe-without-acting. "Diagnostic" in `zeus_strategy_spec.md` and `zeus_math_spec.md` §0 means the Monte Carlo / Platt / α-weighted comparison baseline, not the live path. Do not conflate. |
| `exact` bin (code enum) | `point` (prose/docs), single-value settlement | Rounding-direction enum value in `settlement_semantics.py`. Docs call it a "point" bin; code uses `exact`. |
| `ceiling` bin (code enum) | `open-high`, `open_shoulder` (colloquial docs), `75°F or higher` form | Open-ended upper bound. Docs use geometric names; code uses rounding-direction names. |
| `floor` bin (code enum) | `open-low`, `open_shoulder` (colloquial docs), `30°C or below` form | Open-ended lower bound. Same prose/code split as `ceiling`. |
| `ghost` / `unsubmitted` | `abandoned` (root descriptor) | A position that materialized without a submitted order. `tests/execution/test_abandoned_unsubmitted_ghost_reconcile.py` uses all three; `src/execution/command_recovery.py` favors `ghost`. Canonical resolution: `abandoned` for the documented state in authority docs, `ghost` acceptable in code comments. |
| `day0_window` (lifecycle state) | `same-day` (prose), `intra-day` (monitoring window), `nowcast` (forecast method for Day0 truth) | See Day0 entry above. INV-16 "Day0 low causality" and the `day0_window` lifecycle phase are distinct concepts. |
| `family` | `family_key` (function name), `city-family key` (event store identity), `strategy-inclusive family` (INV-22 context) | Bare "family" is context-dependent; prefer the explicit tuple `(city, target_date, metric)` in new code to avoid ambiguity with FDR family vs calibration family. |
| `runtime_posture` | `regime` (probability chain selection sense), `mode` | `regime` in `regime_unification_2026-06-12.md` means probability chain authority; `runtime_posture` in `src/control/` means operational state (NORMAL/DEGRADED/HALTED). Do not use `regime` for the operational mode. |
| `substrate` | `book`, `venue` (loose usage) | Zeus-internal term for the CLOB order-book state monitor. `venue` refers to the Polymarket protocol/adapter, not the book state. |
