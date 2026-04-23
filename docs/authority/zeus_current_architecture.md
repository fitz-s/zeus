# Zeus Current Architecture Law

Status: active architecture authority
Scope: runtime semantic law, truth ownership, source/data role boundaries, risk/execution semantics, dual-track identity

---

## 1. Purpose

This file answers two questions:

1. How Zeus operates as a live trading machine.
2. Which semantic mistakes can cause real loss or corrupt runtime truth.

If an agent reads only one human authority file before touching runtime,
source, settlement, Day0, calibration, or truth ownership, it should read this
file.

---

## 2. What Zeus Is

Zeus is a **live-only weather settlement-contract trading runtime**.

It is not:

- a general weather research repository
- a generic forecasting dashboard
- a paper-mode simulator
- a system whose primary object is continuous temperature prediction

Zeus trades **discrete settlement contracts**. Its primary chain is:

`contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`

Any reasoning that skips contract semantics or source truth is not valid Zeus
reasoning.

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

Zeus is not a single-track daily-high system.

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
- `quarantined`
- `admin_closed`

Exit intent is not closure. Settlement is not exit. Quarantine is not a normal
holding state. No helper, report, strategy, or LLM-generated patch may invent
phase strings.

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

If exact attribution is missing, fail, quarantine, or mark the record degraded.
Do not invent fallback governance buckets.

---

## 10. Risk, Execution, Backtest, And Shadow Law

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

### 10.2 Live / Backtest / Shadow Boundary

- Live may act.
- Backtest may evaluate.
- Shadow may observe.

Backtest output and shadow metrics cannot promote live behavior by themselves.
Promotion to live requires evidence, operator approval, and a governance
packet. Shadow instrumentation may report additive metrics but may not gate
live entries until promoted.

Replay is diagnostic until it has full market-price linkage, active sizing
parity, and selection-family parity with live control units.

Shadow/backtest promotion protocol:

1. collect at least 30 days of parallel shadow data
2. evaluate with honest replay, including full market-price linkage, active
   sizing parity, and selection-family parity
3. prove live-relevant metric improvement
4. obtain explicit operator approval
5. document rollback in a governance packet
6. keep live cutover reversible for at least 7 days

### 10.3 Authority-Loss Degradation

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
- `acknowledge_quarantine_clear`

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

## 14. What Every Agent Must State Before Editing

For any source, data, settlement, monitoring, calibration, runtime, or truth
ownership task, state:

1. What truth Zeus is trading or protecting in this task.
2. Which feed/surface determines the money path.
3. Which surface is only evidence or diagnostics.
4. Which current-fact surface must be fresh.
5. Which fatal misread is most likely.
6. Where to stop if current facts are stale or missing.

If these cannot be answered, do not implement.

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
