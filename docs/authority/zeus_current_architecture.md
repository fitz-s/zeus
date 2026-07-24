# Zeus Current Architecture Law

Status: active architecture authority
Scope: runtime semantic law, truth ownership, source/data role boundaries, risk/execution semantics, dual-track identity

---

## 1. Purpose

This file defines the **physical and semantic laws of the Zeus trading machine**. It covers settlement contract geometry, truth plane separation, data feed roles, dual-track identity, lifecycle grammar, risk actuation, and execution boundaries.

These laws are not advisory. Violation of any law in this document — passing untyped floats through pricing, collapsing discrete geometry into continuous math, misidentifying truth planes — will silently corrupt the money path.

This document supersedes all delivery protocols, governance documentation, and generated mesh graphs.

---

## 2. What Zeus Is

Zeus is a **live weather settlement-contract trading runtime**.

### Typed Boundaries & Contract Execution
Code correctly written in native Python types can still catastrophically fail if the financial/semantic properties are not preserved. Thus, Zeus uses strict bounding contracts:

1. **`ExecutionPrice` Boundary (DT#5 / INV-21)**: No pricing logic (such as `kelly_size`) may accept bare floats. `ExecutionPrice.assert_kelly_safe()` is unconditionally executed to prove a price is physically tradable before sizing.
2. **`SettlementSemantics` as a Semantic Atom**: Settlement data is integer-bound based on physical venue behavior (e.g., WU Fahrenheit uses `wmo_half_up` asymmetry, HKO uses `oracle_truncate`). Continuous-temperature intuition violates the discrete geometry of the tradable bins.
3. **`TemperatureDelta` Unit Defense**: Alpha weight scaling and edge decay (`SPREAD_TIGHT`, `SPREAD_WIDE`) rely on `TemperatureDelta` to prevent mixing °C and °F scalar limits.
4. **Risk Enforces Action (INV-05)**: `RED` risk demands active programmatic sweep: it cancels all pending orders and issues exit/sweep for all active positions.

Zeus trades **discrete settlement contracts**. Its primary chain is:
`contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`

Any reasoning that bypasses contract semantics or abstracts away discrete settlement sources is invalid.

---

## 3. Money Path

Zeus makes or loses money through this path:

1. Understand the venue's discrete market contract: city, local date, unit,
   bin topology, shoulder semantics, and rounding.
2. Bind the correct source, provider, station/product, date range, unit, and
   track.
3. Build raw probability from the correct forecast family.
4. Calibrate against the correct observation family and settlement outcome.
5. Compare posterior probability to executable market context.
6. Size, enter, monitor, exit, and settle under lifecycle and risk law.
7. Feed settlement truth back into learning without hindsight leakage.

The most expensive failures are usually semantic category errors, not syntax
errors:

- wrong source role
- wrong station/product/date mapping
- wrong settlement/bin/rounding semantics
- high/low track mixing
- Day0, historical hourly, settlement, and forecast-skill feed role collapse
- stale current facts treated as current truth
- graph/topology context mistaken for semantic proof

---

## 4. Truth Planes

### 4.1 Venue / Contract Truth

Defines what is being traded:

- market city and local date
- unit
- bin topology
- point / finite range / shoulder shape
- rounding and containment semantics
- settlement support geometry

Contract truth decides what the position is economically about.

### 4.2 Settlement Truth

Defines which daily observation source resolves the contract. Settlement truth
is not inferred from endpoint availability, nearby airport station, or a
generic weather-data source family.

### 4.3 Day0 Monitoring Truth

Defines what live same-day observation stream approximates settlement risk
while the market is still tradable. Day0 truth may differ from final daily
settlement truth.

### 4.4 Historical Hourly Truth

Defines which hourly/sub-hourly historical rows can support diurnal,
persistence, nowcast, and training features. Hourly aggregation must preserve
the extrema required by the target metric.

### 4.5 Forecast-Skill Truth

Defines which forecast/observation pairing supports calibration, bias, and
skill learning. Forecast-skill truth is not settlement truth by default.

### 4.6 Runtime / Ledger Truth

Defines canonical DB, event, lifecycle, position, risk, and projection truth.
Derived JSON, CSV, reports, notebooks, and status files are not equivalent to
canonical DB/event truth.

---

## 5. Truth Ownership Matrix

| Surface | Role | May be relied on by | Must never become |
|---|---|---|---|
| Contract semantics | top market truth | settlement, signal, calibration, review | continuous-temperature intuition |
| DB / events / projections | canonical runtime truth | runtime, supervisor, audit | derived JSON/status replacement |
| `config/cities.json` | runtime config seed | source code, audits | current source validity by itself |
| `docs/operations/current_source_validity.md` | audit-bound current routing fact | planning, source packets | durable law |
| `docs/operations/current_data_state.md` | audit-bound current data posture | planning, data packets | rebuild approval |
| reports/artifacts | evidence | audits, diagnosis | active authority |
| `.code-review-graph/graph.db` | structural context | review, blast radius | semantic truth |

---

## 6. Feed-Role Matrix

| Feed / surface | Durable role | Primary consumers | Forbidden inference |
|---|---|---|---|
| TIGGE ECMWF ENS | signal/probability | signal, calibration inputs | settlement source |
| WU / HKO daily | settlement truth | settlement, training outcomes | Day0 live monitor truth |
| WU / Ogimet / HKO current/hourly | monitor or historical-hourly depending on route | Day0, diurnal, persistence | venue settlement without proof |
| Forecast skill surfaces | forecast-vs-outcome analysis | calibration, bias, replay | runtime signal identity |
| Market book / VWMP | executable market context | posterior fusion, executor | physical weather truth |
| Chain / CLOB facts | live economic truth | reconciliation, PnL, lifecycle | source validity |
| Current-fact docs | audit-bound planning truth | packet planning | durable law |
| Graph / topology | structure and routing | review, code discovery | source/date semantics |

---

## 7. Dual-Track Law

Zeus is dual-track.

High and low temperature tracks may share local-calendar-day geometry. They do
not share:

- physical quantity
- observation field
- Day0 causality
- calibration family
- settlement rebuild identity
- replay bin lookup identity

Every high/low task must state:

1. Which track is changing.
2. Which surfaces are shared.
3. Which surfaces are explicitly not shared.

### 7.1 Metric Identity Spine

Every temperature-market row and model family carries explicit identity:

- `temperature_metric` in {`high`, `low`}
- `physical_quantity`
- `observation_field` in {`high_temp`, `low_temp`}
- `data_version`

Canonical families:

```
HIGH_LOCALDAY_MAX
  temperature_metric = "high"
  physical_quantity  = "mx2t6_local_calendar_day_max"
  observation_field  = "high_temp"
  data_version       = "tigge_mx2t6_local_calendar_day_max_v1"

LOW_LOCALDAY_MIN
  temperature_metric = "low"
  physical_quantity  = "mn2t6_local_calendar_day_min"
  observation_field  = "low_temp"
  data_version       = "tigge_mn2t6_local_calendar_day_min_v1"
```

On the same `(city, target_date)`, Zeus must be able to represent both the
daily high settlement truth and the daily low settlement truth. Any table,
model, replay key, or calibration family that conflates those rows is
structurally incomplete.

### 7.2 Durable Dual-Track Decisions

- `MetricIdentity` is first-class. Bare `"high"` and `"low"` strings are
  serialization details, not internal law.
- Metric-aware v2 tables are the long-term write family; old tables may remain
  readable for compatibility but are not low-track write targets.
- `observations` carries high and low daily fields; consumers select via
  `observation_field`, never by implicit high default.
- Day0 high and Day0 low are separate runtime classes and causality families.
- Fallback forecasts may support runtime degradation but are not canonical
  training evidence.
- Low historical lane and low Day0 runtime are separate gates.
- High is re-canonicalized onto
  `tigge_mx2t6_local_calendar_day_max_v1` before low enters live authority.

Machine manifests and tests are the durable enforcement layer for this law.
The former long-form dual-track authority document is demoted only after this
core law and the relevant manifests carry its load-bearing rules.

---

## 8. Runtime Truth And Lifecycle Law

### 8.1 Canonical Truth

The repo-owned DB/event layer is Zeus's canonical inner truth surface. JSON and
CSV exports are derived.

Chain/CLOB facts outrank local cache:

`Chain (Polymarket CLOB) > event log / canonical DB > local cache / projection exports`

Reconciliation states must distinguish:

- synced local + chain truth
- known empty chain truth
- unknown/stale chain truth

Void decisions require known absence, not unknown chain status.

### 8.2 Lifecycle Grammar

Legal lifecycle phases are bounded and enum-backed:

`pending_entry -> active -> day0_window -> pending_exit -> economically_closed -> settled`

Terminal phases:

- `voided`
- `settled`
- `admin_closed`

`quarantined` is retired from `LifecyclePhase` entirely (T5,
`docs/rebuild/quarantine_excision_2026-07-11.md`) — no writer mints it. A
confirmed-fill/chain-absence dispute keeps the position's TRUE phase
(`active`/`pending_exit`) and the dispute lives in a typed `ReviewWorkItem`
(`src/contracts/review_work_item.py`), never in a lifecycle phase.
Chain-only unknown assets are not Position rows and carry no lifecycle
phase — they are typed `ChainOnlyFact` records
(`src/state/chain_reconciliation.py`) read directly by the risk view, with a
family-scoped entry block and worst-case exposure counted into risk caps.
See `docs/reference/zeus_execution_lifecycle_reference.md` §1.1-1.2, §2.2
Rule 3.

Exit intent is not closure. Settlement is not exit. No helper, report,
strategy, or LLM-generated patch may invent phase strings.

### 8.3 Append-First Authority

Canonical lifecycle truth is append-first:

1. append domain event
2. fold deterministic projection
3. keep event append and projection update in one transaction boundary where
   that write path is used

DB commits must precede derived JSON export writes.

---

## 9. Governance Identity

`strategy_key` is the sole governance key.

It controls:

- attribution that affects behavior
- risk policy resolution
- learning/performance slicing
- operator controls that target a strategy

`edge_source`, `discovery_mode`, `entry_method`, scheduler mode, and similar
fields are metadata. They must not compete as governance centers.

If exact attribution is missing, fail or mark the record degraded —
DATA_DEGRADED is the only non-failing lane for missing truth input.
Do not invent fallback governance buckets.

---

## 10. Risk, Execution, And Backtest Law

### 10.1 Risk Must Act

Risk levels must change behavior:

| Level | Required behavior |
|---|---|
| `GREEN` | normal operation |
| `YELLOW` | no new entries |
| `ORANGE` | no new entries; exit only at favorable prices |
| `RED` | cancel pending orders and sweep active positions |

Overall risk is fail-closed: the max active level wins, and computation error
or broken truth input must not silently downgrade risk.

### 10.2 Absolute Live Order Price Domain

Every proposed or submitted BUY or SELL price must be finite and inside the
inclusive absolute range `[0.05, 0.95]`. This applies symmetrically to every
entry, reduce-only exit, single-order, and batch order. Anything below `0.05`
or above `0.95` is rejected at command persistence, by the canonical envelope,
and again by an independent final SDK-boundary guard. Current tick alignment,
minimum size, identity, tradeability, fees, depth, robust delta-log-wealth/EV,
Kelly, strategy, risk, lifecycle, and current-state proof cannot waive the band.
The official restart preflight must reject a live-trading boot if the active
config is not exactly `0.05/0.95` or any independent guard accepts a forbidden
sample (INV-43).

Price alone does not authorize an entry: the global auction separately requires a strict
current-evidence robust win majority, positive robust EV and delta log wealth,
and only the remaining shares below the cumulative Fractional Kelly final-
holding target. These economic conditions apply symmetrically to YES and NO.

### 10.3 Same-Family Portfolio Capital Objective

Mutual exclusivity defines payoff geometry, not a one-token position limit.
Zeus may hold or buy multiple outcome tokens inside one
`(city, target_date, temperature_metric)` family when the marginal order
improves the full current portfolio objective. The auction must project all
same-family YES/NO holdings and unresolved entry commitments onto the
exhaustive outcome set, then require positive robust delta log wealth and EV
after fees, depth, affordability, and the cumulative Fractional Kelly target.
The command journal enforces executable-truth and risk contracts; it must not
categorically reject a sibling token merely because another family token is
open (INV-45).

### 10.4 Live / Backtest Boundary — NO PARALLEL_OBSERVE_ONLY LANE

- Live may act.
- Backtest may evaluate (walk-forward, settlement-truth graded, no
  market-price replay).

There is NO parallel observe-only lane (operator law 2026-06-13 "不准再parallel observe-only了",
reaffirmed 2026-07-19 "parallel observe-only不应该存在…从源头上就不存在"). A change is
proven by mathematics/statistics on already-settled history plus invariant
tests, then goes LIVE DIRECT — or it is not made. Time-boxed parallel
observation ("collect N days of parallel runtime data", "keep cutover reversible for
N days") is a forbidden proposal shape: correctness is proven by evidence
that already exists, never by waiting. Promotion to live requires that
proof and explicit operator approval; rollback is a git revert, not a
standing observation window.

Replay is evidence check until it has full market-price linkage, active sizing
parity, and selection-family parity with live control units.

### 10.5 Authority-Loss Degradation

When DB truth is unavailable or degraded, new-entry paths must fail closed, but
monitor/exit/reconciliation lanes should keep operating in read-only or
best-known-state mode where legal. Fail-closed does not mean blinding the
monitor/exit lane.

---

## 11. External Boundary

Zeus owns inner runtime truth and repo law.

Venus may read derived status and typed contracts and may issue narrow ingress
commands through typed repo surfaces. OpenClaw may host workspace/runtime
support, memory injection, notifications, and optional gateway routing.

Neither Venus nor OpenClaw may:

- redefine repo authority order
- bypass packet/gate rules
- treat workspace memory as canonical repo truth
- directly mutate DB truth or architectural law
- silently widen command vocabulary or live-control authority

Allowed autonomous control commands in the current phase:

- `request_status`
- `pause_entries` with evidence and TTL
- `tighten_risk` with evidence and TTL

Advisory-only commands:

- `set_strategy_gate`
- `resolve_review_item`

Human-gated commands:

- `resume`
- any command vocabulary expansion
- any risk re-enable after a safety-triggered pause
- any permanent strategy-gating policy change

---

## 12. Current Durable Prohibitions

Forbidden equivalences:

- API returns data == settlement-correct source
- airport station == city settlement station
- settlement daily source == Day0 live monitor source
- Day0 live monitor source == historical hourly source
- historical hourly source == forecast-skill source
- reference doc == authority
- current-fact doc == durable law
- graph output == semantic proof

Forbidden workflow shortcuts:

- entering code before answering semantic proof questions
- using packet closeout as current semantics
- using reports/artifacts to patch authority gaps
- using stale current facts as current truth
- mixing high/low rows in calibration, Platt fitting, replay bin lookup, or
  settlement rebuild identity

---

## 13. Catastrophic Failure Classes

1. wrong provider / station / product
2. wrong local-date mapping
3. wrong rounding / bin topology / shoulder semantics
4. wrong high/low track identity
5. Day0 vs settlement role collapse
6. historical hourly vs forecast-skill role collapse
7. lifecycle / exit / settlement conflation
8. authority rank inversion
9. stale current facts treated as current truth
10. graph/topology treated as system understanding

---

## 14. Operational Checklist For Pipeline-Impacting Work

For any work touching source, data, settlement, monitoring, calibration,
runtime, or truth ownership, the following must be established:

1. What truth Zeus is trading or protecting in this task.
2. Which feed/surface determines the money path.
3. Which surface is only evidence or evidence checks.
4. Which current-fact surface must be fresh.
5. Which fatal misread is most likely.
6. Where to stop if current facts are stale or missing.

If these cannot be established, do not implement.

---

## 15. Machine-Checkable Sources

This file is human law. Machine-checkable authority includes:

- `architecture/invariants.yaml`
- `architecture/negative_constraints.yaml`
- `architecture/zones.yaml`
- `architecture/source_rationale.yaml`
- `architecture/task_boot_profiles.yaml`
- `architecture/fatal_misreads.yaml`
- `architecture/city_truth_contract.yaml`
- `architecture/code_review_graph_protocol.yaml`

---

## 16. Relationship To Other Files

- `docs/authority/zeus_current_delivery.md` defines how Zeus may be changed.
- `docs/authority/zeus_change_control_constitution.md` is deep, non-default
  anti-entropy governance.
- `docs/reference/zeus_domain_model.md` is fast domain orientation, not
  authority.
- `docs/operations/current_state.md` names the active packet.
- Current-fact surfaces are audit-bound and expiry-bound planning facts.
