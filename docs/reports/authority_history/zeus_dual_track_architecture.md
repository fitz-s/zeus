# Zeus Dual-Track Architecture Law

Status: historical report evidence; superseded by `docs/authority/zeus_current_architecture.md`
Scope: Metric identity, dual-track world truth, World DB v2, death-trap
remediation law, activation gates
Supersedes on disagreement: any prose in `AGENTS.md`, deep-map, or packet plan
that still describes Zeus as a single-track daily-high system.

---

## 0. Historical Position

This file was formerly active dual-track authority. It is retained as
historical evidence only. Current dual-track law lives in
`docs/authority/zeus_current_architecture.md` and machine manifests/tests.

---

## 1. Why this file exists

Zeus has run on an implicit worldview that ``one city + one target_date = one
temperature truth``. That worldview is already insufficient for the existing
code paths that have begun to carry `temperature_metric` and it is incompatible
with live `daily-low` markets. Repairing it is not a patch. It requires
identity-level structural decisions at the data, runtime, governance, and
activation layers simultaneously.

This file is the single constitutional record of those decisions.

---

## 2. Metric identity spine

### 2.1 MetricIdentity as a typed object

Zeus now treats every temperature-market family as carrying an explicit typed
identity:

- `temperature_metric` ∈ {`high`, `low`}
- `physical_quantity` — the upstream forecast quantity name
- `observation_field` ∈ {`high_temp`, `low_temp`}
- `data_version` — canonical product version tag

These four fields are row truth and model truth. Any table or model family
that cannot represent them at row level is structurally incomplete.

### 2.2 Canonical families

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

Both families share one local-calendar-day time geometry. They share nothing
else: not physical quantity, not observation field, not Day0 causality, not
training eligibility, not calibration family, not runtime decision class.

### 2.3 Row-identity invariants

On the same `(city, target_date)`, two distinct legitimate temperature truths
must be representable:

- the daily high settlement truth
- the daily low settlement truth

Any table whose unique key conflates them is an SD-2 violation.

---

## 3. Structural decisions (SD-1 … SD-8)

- **SD-1**: `MetricIdentity` is a first-class typed object. Bare strings for
  `"high"` / `"low"` are only allowed at serialization boundaries.
- **SD-2**: World DB upgrades via v2 table family. The old tables remain
  readable but are no longer write targets. This is a cutover, not a patch.
- **SD-3**: `observations` keeps daily dual fields (`high_temp`, `low_temp`).
  All consumers select via `observation_field`. No consumer defaults to high.
- **SD-4**: Day0 runtime splits into `Day0HighSignal` and
  `Day0LowNowcastSignal`. Shared tools are allowed; shared main class is not.
- **SD-5**: Fallback forecasts (e.g. Open-Meteo) may feed runtime degrade
  paths but are forbidden from canonical training. Eligibility is a DB gate,
  not a comment.
- **SD-6**: Low historical lane and low Day0 runtime ship in separate phases.
  Historical purity and Day0 causality are two different gates.
- **SD-7**: High is re-canonicalized onto
  `tigge_mx2t6_local_calendar_day_max_v1` before low enters the world. High
  and low share geometry, so the geometry must be owned before low lands.
- **SD-8**: Documentation upgrades precede code. Without Phase 0, implicit
  high-only mental models silently recapture the code base.

---

## 4. World DB v2 outline

The v2 table family is authoritative for dual-track writes. The old tables
remain readable for audit and are frozen for new writes after Phase 4 cutover.

| Table | Unique identity | Metric-aware? |
|---|---|---|
| `settlements_v2` | `(city, target_date, temperature_metric)` | yes |
| `market_events_v2` | + `temperature_metric` | yes |
| `ensemble_snapshots_v2` | `(city, target_date, temperature_metric, issue_time, data_version)` | yes |
| `calibration_pairs_v2` | + `temperature_metric, forecast_available_at` | yes |
| `platt_models_v2` | `(temperature_metric, cluster, season, data_version, input_space, is_active)` | yes |
| `observation_instants_v2` | adds `running_min` alongside `running_max` | yes |
| `historical_forecasts_v2` | adds `temperature_metric` + `forecast_value` | yes |
| `day0_metric_fact` | replaces `day0_residual_fact`; keyed on metric | yes |

Every canonical snapshot row must carry at least:

- `training_allowed` (bool, hard gate)
- `causality_status` ∈ {`OK`, `N/A_CAUSAL_DAY_ALREADY_STARTED`,
  `N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON`,
  `REJECTED_BOUNDARY_AMBIGUOUS`}
- `boundary_ambiguous` (bool)
- `ambiguous_member_count`
- `manifest_hash`

### Phase 9C policy clarification (2026-04-19): settlements_v2 writer origin

`settlements_v2` is a **read-side query symmetry surface** — live settlement
truth arrives externally (Polymarket oracle + Weather Underground observations)
and is written by existing legacy paths (`src/execution/harvester.py` for
terminalization, PM oracle sync for market truth). Zeus's Python code does
**not** contain an internal `write_settlement_v2(...)` writer; the v2 table
exists so that a future backfill tool (historical replay symmetry) or an
external publisher (PM oracle v2 integration, if introduced) can populate
rows that dual-track readers query with metric discrimination.

Current state (Phase 9C / Golden Window active): `settlements_v2` has zero
rows. `get_calibrator` and other v2-aware readers gracefully fall back to
legacy for HIGH (backward compat) and fail closed for LOW (no data → no
calibrator → Level 4 uncalibrated → evaluator refuses with
`rejection_reason='no_calibrator'`). This is fail-closed by design.

If and when a live LOW settlement publisher lands, its writer must:
1. Write to `settlements_v2` with explicit `temperature_metric`;
2. Carry provenance fields (`source`, `authority`) per DT#1 / DT#6 §B;
3. NOT mutate legacy `settlements` for LOW rows (NC-11 negative constraint).

Until then, read-side `settlements_v2` queries return empty result sets for
LOW + non-backfilled HIGH; downstream consumers handle gracefully.
- `provenance_json`

Fallback forecast rows are never canonical training evidence. Missing
`issue_time` forces `training_allowed = false`.

---

## 5. Day0 causality law

Daily low Day0 is not a mirror image of daily high Day0.

For positive-offset cities, a Day0 local low may already be partly or fully
historical by ECMWF 00Z. Such slots are not missing coverage; they are:

```
N/A_CAUSAL_DAY_ALREADY_STARTED
```

Runtime code must not route such slots through a historical forecast Platt
lookup. Low Day0 for a non-`OK` causality status must go through the nowcast
path built on `low_so_far`, `current_temp`, `hours_remaining`, and remaining
forecast hours. If any of those inputs is missing, the decision is a clean
reject, not a silent degrade to high path.

---

## 6. Death-trap remediation law

These are six orthogonal runtime-safety issues identified in `death_trap.md`
that must be fixed alongside the dual-track work. This section elevates each
to binding law. Phase ownership is in `plan.md`.

### DT#1 — Truth commit ordering

DB authority writes must complete (COMMIT) before any derived JSON export is
updated. A runtime that writes JSON (e.g. `save_portfolio()`) before
committing the corresponding DB event/projection is a DT#1 violation. On
process death mid-split, the recovery contract is: DB wins, JSON is treated as
a stale export and rebuilt from DB.

Enforcement intent: schema/test contract, plus a semgrep-shaped rule against
JSON-before-commit patterns in cycle-level code.

### DT#2 — Risk force-exit law

INV-05 is extended. `RED` is not permitted to remain entry-block-only while
existing positions sit untreated.

- `RED` must cancel all pending orders AND initiate an exit sweep on active
  positions.
- A "force_exit_review" that only blocks new entries is an advisory-only
  output; per INV-05 it is forbidden.
- The exit sweep runs even if it is more destructive than ORANGE behavior,
  because RED is a truth claim about system integrity, not a throttle.

### DT#3 — FDR family canonicalization

`make_family_id()` must resolve to a single canonical family grammar across
every call site. Either `strategy_key` is always part of the family key or it
is never part of it; per-path drift is forbidden.

Rationale: an unstable family key silently resets the false-discovery budget
and lets the same market be re-tested until a signal happens to cross.

Enforcement intent: single choke-point helper; test that asserts every call
site delegates to it.

### DT#4 — Chain-truth three-state law

Chain reconciliation must model three states, not two:

- `CHAIN_SYNCED` — positions match a fresh chain truth.
- `CHAIN_EMPTY` — positions are known absent (chain truth is fresh AND empty).
- `CHAIN_UNKNOWN` — chain truth is not available (API incomplete, stale, or
  otherwise un-authoritative).

`CHAIN_UNKNOWN` is a first-class state. Void decisions require
`CHAIN_EMPTY`, never `CHAIN_UNKNOWN`. A stale guard is a partial
implementation of this law; the state machine must be made explicit.

### DT#5 — Kelly executable-price law

A new invariant, `INV-21`, is introduced for this law. It is **not** the same
as the existing aspirational `INV-13` (cascade-constants registration in
`zeus_current_architecture.md` §9); both remain in scope and are
complementary. Sizing correctness requires both: registered multipliers
(INV-13) and a real executable price distribution (INV-21).

Sizing inputs at the Kelly boundary must be an executable price distribution,
not a single static `entry_price`. At minimum, sizing must incorporate:

- best bid / ask and top-of-book size
- fill probability at intended order size
- queue-priority and adverse-selection hazards

Bare `entry_price` into `kelly_size()` at cross-layer seams is a DT#5 / INV-21
violation. A `dict(best_bid=..., best_ask=...)` without fill-probability or
adverse-selection semantics does not satisfy INV-21.

### DT#6 — Graceful degradation law

When `load_portfolio()` (or any other authority-loss path) detects that DB
truth is not authoritative, the process must not raise `RuntimeError` that
kills the entire cycle.

The legal behavior is:

- disable new-entry paths
- keep monitor / exit / reconciliation paths running in read-only or best-known
  state mode
- surface the degraded state explicitly to operator

Fail-closed is mandatory for new risk; it is not permission to blind the
monitor/exit lane.

#### Interpretation B (adopted 2026-04-18, Phase 9A)

"Read-only" means **no NEW canonical-state entries** (position creation, new
risk policy changes, new strategy enablement); it does NOT mean "no JSON cache
refresh at all". In particular:

- `save_portfolio()` / `save_tracker()` MAY proceed in degraded mode. Callers
  **SHOULD** restrict saves to external-authority-derived updates (CLOB API
  reconciliation, chain sync with on-chain truth, order fill/cancel events)
  so that persistence reflects truth that survives the local DB degradation.
  **There is no runtime enforcement** of this constraint as of Phase 9A — the
  convention is caller-side discipline. A future phase (P9B/P9C or later)
  may add a `source: Literal[...]` parameter to `save_portfolio()` to lock
  this at the seam; logged as design debt per critic-carol P9A MINOR-2.
- `PortfolioState.authority` is a runtime signal derived at load time from DB
  availability — it is NOT serialized into positions-cache.json. A degraded
  save followed by a recovered-DB load yields `authority="canonical_db"`
  naturally (the signal is re-derived from fresh DB state).
- The JSON file's `_truth_authority` annotation (via `annotate_truth_payload`
  + `_TRUTH_AUTHORITY_MAP` at `src/state/portfolio.py:47-51`) records the
  runtime authority at write time; readers inspect this tag for provenance
  audit. As of 2026-04-18, `_TRUTH_AUTHORITY_MAP["degraded"] = "VERIFIED"` —
  review periodically whether that mapping still reflects intent.
- `riskguard.tick_with_portfolio()` (built Phase 6, wired into cycle_runner
  Phase 8) is **advisory** — it computes `RiskLevel.DATA_DEGRADED` but does
  NOT persist to `risk_state.db`. The returned level is authoritative for the
  current cycle's `summary["risk_level"]`, but downstream readers of
  `risk_state.db` (e.g. `status_summary.py::_get_risk_level`) continue to see
  whatever the last full riskguard tick wrote. This inter-cycle drift is
  accepted as design: the next full riskguard tick resolves it. Future `tick_*`
  variants should either persist or document ephemeral explicitly.
- Operator visibility is guaranteed via three-signal redundancy on every
  degraded cycle:
  1. `summary["portfolio_degraded"] = True`
  2. `summary["entries_blocked_reason"] = "risk_level=DATA_DEGRADED"`
     (Phase 9A R-BT; pre-P9A this field was silently `None`)
  3. `summary["risk_level"] = "DATA_DEGRADED"`

Antibodies protecting this law (tests/test_phase8_shadow_code.py):

- R-BQ.1 (structural, P9A-hardened): ANY RuntimeError escaping the DT#6 branch
  is a violation — structural immunity, not text-match
- R-BQ.2: `riskguard.tick_with_portfolio` called exactly once with the
  degraded PortfolioState
- R-BS: `save_portfolio(authority="degraded")` preserves positions+bankroll
  faithfully and the truth-payload annotation seam is exercised
- R-BT: `summary["entries_blocked_reason"]` populated with degraded reason code

### DT#7 — Boundary-day settlement policy (market-reality)

Near the integer settlement boundary (where station drift, DST boundary,
source revision, or observation lag can flip the outcome), the system must:

- reduce leverage on boundary-candidate positions
- isolate oracle penalty for the affected city
- refuse to treat boundary-ambiguous forecasts as confirmatory signal

This is a policy law, not a code invariant alone. It lives here so risk and
Day0 phases both point at the same doctrine.

---

## 7. Activation gate grammar

Gates A through F govern the sequence from schema to real-money activation.
They are authoritative for Phase 2 through Phase 9.

| Gate | Passes when |
|---|---|
| A — schema | sandbox DB holds same-city-same-date high + low rows across every v2 table |
| B — observation | every main provider yields `low_so_far`; evaluator stops rejecting on the field |
| C — high-v2 parity | high canonical cutover onto `tigge_mx2t6_local_calendar_day_max_v1` is explainable against the prior live run |
| D — low historical purity | `training_allowed` + `causality_status` enforced at ingest; boundary quarantine reports clean |
| E — low shadow | shadow trace complete; `N/A_CAUSAL_DAY_ALREADY_STARTED` routes to nowcast, not historical Platt |
| F — low activation | all prior gates hold; rollback rehearsed; risk layer distinguishes metric families; death-trap DT#1–#7 remediations in place |

Skipping a gate is a change-control violation, not a speed-up.

---

## 8. Forbidden moves specific to this refactor

- Opening `daily-low` live before Gate F.
- Writing a `daily-low` row on the old `settlements` (non-v2) table.
- Letting a fallback forecast row enter a v2 `calibration_pairs` or Platt fit.
- Routing a `causality_status != 'OK'` Day0 slot to a historical Platt lookup.
- Mixing high and low rows in any single Platt model, bin lookup, or
  calibration family.
- Keeping `day0_signal.py` as a monolithic class once Phase 6 opens.
- Patching DT#1–#6 symptoms (e.g. reordering two lines) without establishing
  the corresponding state machine or helper.

---

## 9. What this document deliberately does not settle

- The exact schema SQL for v2 tables. That lives in Phase 2's migration file.
- The exact Platt refit math. That lives in Phase 7's evidence bundle.
- The exact activation city set. That lives in Phase 9's operator checklist.

Phase plan files add detail within these bounds; they may not loosen the law
here.

---

## 10. Relationship to other authority files

- `AGENTS.md` (root): carries a short summary pointing here. Full law stays
  here.
- `zeus_current_architecture.md`: carries §13–§22 (identity, fallback,
  causality, death-trap remediation); this file is the integrated longform.
- `architecture/invariants.yaml` and `architecture/negative_constraints.yaml`:
  carry machine-checkable INV-14 … INV-22 and NC-11 … NC-15 (landed in
  Phase 0b). INV-21 encodes DT#5 Kelly executable-price; INV-22 encodes DT#3
  FDR family canonicalization. The pre-existing aspirational INV-13
  (cascade-constants registration) in `zeus_current_architecture.md` §9
  remains a distinct law and is not overwritten.
- `docs/operations/data_rebuild_plan.md`: inherits the canonical product and
  separation rules from §2.2 and §4.
- `docs/operations/task_2026-04-16_dual_track_metric_spine/plan.md`: the
  phase-level execution plan; must stay compatible with this file.
