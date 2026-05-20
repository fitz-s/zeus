<!-- Created: 2026-05-19 -->
<!-- Last reused or audited: 2026-05-19 -->
<!-- Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5 (Option B pivot, v3) -->

# T2 SCAFFOLD v3.1 — Day0HighNowcastSignal + one-hot daypart + fit_run_id (bin_grid_id deferred)

**Status**: SCAFFOLD v3.1 (bin_grid_id/bin_schema_version deferred to Phase 2; proceeding to production)
**Author**: sonnet executor, worktree `phase1-t2-day0-nowcast-20260520`
**Entry SHA**: origin/main = `649f73d865` (PR-T1-B merged; T1 complete)

---

## §1. Architectural reframe — Option B (post-critic-round-1)

**Root design failure caught**: "unified Day0Nowcast" was thesis-broken. `Day0LowNowcastSignal`
returns `np.ndarray` + `p_bin()` integration — NOT a scalar probability. A 20-LOC shim
was impossible; real migration would touch 150+ LOC across 3+ callers with regression risk.

**Option B resolution**:
- `Day0HighNowcastSignal` (NEW) — mirror class in `src/signal/day0_high_nowcast_signal.py`.
  Same constructor shape as `Day0LowNowcastSignal`, same output interface
  (`settlement_samples() -> np.ndarray`, `p_bin(low, high) -> float`, `p_vector(bins) -> np.ndarray`).
- `Day0LowNowcastSignal` — **UNTOUCHED**. No refactor, no shim, zero regression risk.
- `HorizonPlattFit` (in `src/calibration/day0_horizon_calibration.py`) — the shared
  calibration contract. This is the unification point (Fitz §1.4: make category impossible
  at the CALIBRATION layer, not the signal-class layer).
- `Day0Router.route()` — HIGH branch invokes `Day0HighSignal` (ensemble path, return value)
  AND `Day0HighNowcastSignal` (nowcast path, side-write to `day0_nowcast_runs`) in parallel.
  LOW branch unchanged. Router return type unchanged: `Day0HighSignal | Day0LowNowcastSignal`.

**Router wiring — Option (c): caller-site, NOT Day0Router** (per critic round-2 verdict):
`Day0Router.route()` stays PURE — no DB connection parameter, no side effects.
HIGH-branch nowcast invocation happens at the 2 caller sites that already have
a DB connection in scope:
- `src/engine/evaluator.py:2363` area — after `Day0Router.route()` returns `day0`
- `src/engine/monitor_refresh.py:838` area — after `Day0Router.route()` returns `day0`

Pattern at each caller site:
```python
if temperature_metric.is_high() and hours_remaining <= 6.0:
    try:
        nowcast = Day0HighNowcastSignal(...)
        # write p_vector output to day0_nowcast_runs via day0_nowcast_store.write_nowcast_run()
    except NotApplicableHorizon:
        pass  # guard triggered, skip — no write
```
Router still returns `Day0HighSignal` (ensemble path). Nowcast is a parallel write lane.

**§5.5.2 LOW regression test REMOVED** — no LOW refactor means no regression risk.

---

## §2. DB ownership

| Table | DB | schema_class |
|---|---|---|
| `day0_nowcast_runs` (new) | **forecasts** | `forecast_class` |
| `day0_horizon_platt_fits` (new) | **forecasts** | `forecast_class` |

**INV-37**: T2 writes only to forecasts DB (single-DB write; no new ATTACH path).

---

## §3. Schema — `day0_nowcast_runs` (forecasts DB)

### §3.1 Natural-key PK (mirrors T1 discipline)

```sql
PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, run_seq)
```

`run_seq` derivation mirrors T1 `decision_seq`: `SELECT COALESCE(MAX(run_seq),-1)+1`
under forecasts DB writer lock, WHERE natural-key matches.

`condition_id` omitted from PK — consistent with T1 `decision_events` post SEV-1 #3
resolution: two markets sharing (slug, metric, target_date) but differing condition_id
collapse to one nowcast row. Acceptable: Day0 markets are keyed by slug, not condition.

### §3.2 Audit-only `nowcast_event_id`

Namespace: `nei_v1_` — DISTINCT from T1's `deid_v1_` and calibration's `dgid_v1_`.
Writer-side hash (Option β, mirrors T1). AFTER INSERT trigger backstop:
sentinel `'nei_v1_BACKSTOP_NULL_WRITER_BYPASS'`.

### §3.3 Column sketch (v3.1 — bin_grid_id deferred, fit_run_id, one-hot daypart)

```sql
CREATE TABLE IF NOT EXISTS day0_nowcast_runs (
    -- Natural key (PK)
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    run_seq             INTEGER NOT NULL,

    -- Audit-only
    nowcast_event_id    TEXT,                      -- nei_v1_ prefix; trigger backstop on NULL

    -- HorizonPlattFit reference (FK to day0_horizon_platt_fits)
    fit_run_id          TEXT NOT NULL,             -- references day0_horizon_platt_fits.fit_run_id

    -- Platt model output (per-bin — see §3.4)
    p_nowcast_json      TEXT,                      -- JSON array of per-bin P_nowcast values
    p_now_raw_json      TEXT,                      -- JSON array of per-bin P_now_raw values

    -- Horizon covariates
    hours_remaining     REAL NOT NULL,
    daypart             TEXT NOT NULL CHECK (daypart IN ('pre_sunrise','morning','afternoon','post_peak')),

    -- Provenance
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (3, 4)),
    source              TEXT NOT NULL CHECK (source IN ('live_nowcast', 'replay')),

    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, run_seq)
);
```

**NOT NULL columns (9)**: market_slug, temperature_metric, target_date, observation_time,
run_seq, fit_run_id, hours_remaining, daypart, schema_version, source — 10 total.

**bin_grid_id deferred to Phase 2 (MarketAnalysisVNext)**: Day0 markets resolve ≤6h; bin
re-list within that window is vanishingly rare. The propagation path (`bin_grid_id` on
`ensemble_snapshots_v2` and `ContractOutcomeDomain`) does not reach `evaluator.py:2363` or
`monitor_refresh.py:838` — the `bins` list at those sites is built inline from market
`outcomes` (Bin type has no `bin_grid_id` field). Plumbing it requires new architectural
surface not in T2 scope. Scaffold §13 #2 claim "propagated from cycle_runtime.bins" was
a phantom: no such propagation exists. Phase 2 retrofit will add bin_grid_id/bin_schema_version
once the canonical propagation path is designed.

**p_fused NOT stored here** — evaluator computes element-wise fusion per-bin and stores
result in `decision_events.p_posterior`. Nowcast runs table stores only nowcast output.
`blend_weight_w` also not stored (runtime-computed from hours_remaining; derivable).

**schema_version CHECK widened to IN (3, 4)** per Fix 3 — allows both pre-bump and
post-bump rows during migration window (mirrors T1 pattern: `CHECK (schema_version IN (12, 13))`).

### §3.4 Per-bin storage rationale

`p_nowcast_json` stores `np.ndarray` serialized as JSON array — same bin count as the
market's bin layout. Evaluator reads this alongside `p_cal` (also np.ndarray) for fusion.
Alternative (one row per bin) rejected: adds complexity + joins for a small table.

2 indices:
```sql
CREATE INDEX IF NOT EXISTS idx_day0_nowcast_runs_slug_date
    ON day0_nowcast_runs(market_slug, target_date);
CREATE INDEX IF NOT EXISTS idx_day0_nowcast_runs_event_id
    ON day0_nowcast_runs(nowcast_event_id);
```

---

## §4. Schema — `day0_horizon_platt_fits` (forecasts DB)

Coefficients change rarely. Storing per nowcast_run row = waste.
Separate table: one row per fit execution, referenced via `fit_run_id`.

`fit_version` is stable across re-runs of the same algorithm ("hpf_v1").
`fit_run_id` is a per-execution UUID4 (non-deterministic = better: no hash stability issues).

```sql
CREATE TABLE IF NOT EXISTS day0_horizon_platt_fits (
    fit_run_id          TEXT PRIMARY KEY,          -- uuid4 per fit execution
    fit_version         TEXT NOT NULL,             -- semantic version, e.g. "hpf_v1"
    alpha               REAL NOT NULL,
    beta                REAL NOT NULL,
    -- One-hot daypart (pre_sunrise is reference category, no coefficient)
    gamma_morning       REAL NOT NULL,
    gamma_afternoon     REAL NOT NULL,
    gamma_post_peak     REAL NOT NULL,
    delta               REAL NOT NULL,
    epsilon             REAL NOT NULL,
    fit_date            TEXT NOT NULL,
    n_obs               INTEGER NOT NULL,
    sample_period_start TEXT NOT NULL,
    sample_period_end   TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (3, 4)),
    source              TEXT NOT NULL CHECK (source IN ('live_fit', 'replay_fit'))
);
```

---

## §5. Math sketch — single horizon-aware Platt fit (v3: one-hot daypart)

```
logit(P_nowcast) = α · logit(P_now_raw)
                 + β · hours_remaining
                 + γ_morning   · [daypart=='morning']
                 + γ_afternoon · [daypart=='afternoon']
                 + γ_post_peak · [daypart=='post_peak']
                 + δ · temperature_metric_indicator
                 + ε
```

`daypart`: from `Day0ObservationContext.daypart` (`src/contracts/day0_observation_context.py:133`).
4-way: pre_sunrise (reference, no coefficient), morning, afternoon, post_peak.
One-hot avoids false ordinal assumption (pre_sunrise < morning < ... is not meaningful).

`temperature_metric_indicator`: 0=low, 1=high. Single cross-metric fit (coefficients
shared; δ captures the HIGH/LOW mean difference).

Training data: `calibration_pairs_v2` (forecasts DB), filtered to Day0 rows with
`hours_remaining <= 6` and known daypart.

---

## §6. Forecast-nowcast fusion (evaluator-owned)

`p_cal: Optional[np.ndarray]` per `src/engine/evaluator.py:250` — per-bin vector.
`Day0HighNowcastSignal.p_vector(bins)` returns matching `np.ndarray`.

Evaluator fusion (element-wise):
```python
w = sigmoid(-(hours_remaining - 3.0))
p_fused = w * p_nowcast_vec + (1 - w) * p_cal  # np.ndarray element-wise
```

Fusion NOT stored in `day0_nowcast_runs` — evaluator owns it. Result flows into
`decision_events.p_posterior` via the existing evaluator → decision pipeline.

---

## §7. INV-nowcast-horizon-bound antibody

**Guard location**: `Day0HighNowcastSignal.__init__` (fail-fast on construction).
Rationale: callers discover inapplicability before any evaluate call; no deferred
NotImplementedError race.

**Canonical field**: `inputs.hours_remaining` from `Day0SignalInputs` (`day0_router.py:52`).
NOT a phantom `market.max_hours_to_resolution` field.

**Test status**: STRICT PASS in SCAFFOLD (guard fires immediately in __init__).
Three tests in `tests/test_inv_nowcast_horizon_bound.py`:
1. `test_day0_nowcast_horizon_bound_enforces_6h_ceiling`: hours_remaining=8.0 → raises
2. `test_day0_nowcast_horizon_bound_allows_within_ceiling`: hours_remaining=6.0 → OK
3. `test_day0_nowcast_horizon_bound_rejects_negative`: hours_remaining=-0.5 → raises

All 3 pass (verified: `3 passed in 0.74s`).

---

## §8. provider_reported_time wiring — scope decision

**OUT OF SCOPE for T2** (deferred to Phase 2). Rationale:
- WU API exposes only `valid_time_gmt` → no explicit provider-reported time
- NOAA/METAR not added in T2 scope
- Path F honest semantic: `provider_reported_time = None` in all T2 nowcast rows
- T2 marks the field as Optional in any nowcast storage row (NULL allowed)

---

## §9. decision_events ↔ day0_nowcast_runs join surface

Natural-key join: `(market_slug, temperature_metric, target_date, observation_time)`.
`run_seq` and `decision_seq` are intra-key counters — not part of cross-table joins.
This surface documented here for W3 closure-critic validation.

---

## §10. PR plan — PR-T2 (single PR, ~420 LOC)

| Component | Files | LOC est. |
|---|---|---|
| HIGH nowcast signal | `src/signal/day0_high_nowcast_signal.py` (new) | ~160 |
| Calibration | `src/calibration/day0_horizon_calibration.py` (updated) | ~130 |
| Storage writer | `src/state/day0_nowcast_store.py` (new) | ~60 |
| Caller-site wiring | `src/engine/evaluator.py` + `monitor_refresh.py` (modified) | ~40 |
| DB schema | `src/state/db.py` (SCHEMA_FORECASTS_VERSION bump + 2 CREATE TABLEs) | ~70 |
| Migration script | `scripts/migrate_day0_nowcast_create_2026_05_20.py` (new) | ~40 |
| Antibody tests | `tests/test_inv_nowcast_horizon_bound.py` (updated) | ~70 |
| Manifest | `architecture/db_table_ownership.yaml` (2 entries) | ~20 |
| **Total** | | **~590 LOC** |

LOW class untouched. No §6.2 regression test needed.

---

## §11. Manifest entries planned (NOT applied in SCAFFOLD)

`architecture/db_table_ownership.yaml`:
```yaml
day0_nowcast_runs:
  db: forecasts
  schema_class: forecast_class
  pk_col: "[market_slug, temperature_metric, target_date, observation_time, run_seq]"
  writer: src/state/day0_nowcast_store.py
  created: 2026-05-19
day0_horizon_platt_fits:
  db: forecasts
  schema_class: forecast_class
  pk_col: fit_run_id
  writer: src/state/day0_nowcast_store.py
  created: 2026-05-19
```

Both entries land in the same commit as CREATE TABLE (ultraplan §8 K1 discipline).

---

## §12. SCHEMA_FORECASTS_VERSION 3 → 4 (NOT bumped in SCAFFOLD)

`src/state/db.py` production pass bumps to 4 + regenerates pinned hash.
CHECK constraint widened to `IN (3, 4)` during migration window (Fix 3).

---

## §13. Decisions locked in SCAFFOLD v3.1 (no further critic round)

**#1 resolved**: Storage writer = `src/state/day0_nowcast_store.py` (mirrors T1 pattern).
**#2 REVISED (v3.1)**: bin_grid_id/bin_schema_version DEFERRED to Phase 2.
  Original v3 claim ("propagated from cycle_runtime.bins") was a phantom — no propagation
  path exists at evaluator.py:2363 or monitor_refresh.py:838. bins list is built inline
  from market outcomes; Bin type has no bin_grid_id field. NOT NULL count corrected to 10.
**#3 resolved**: Caller-site wiring, not router side-effect (Option c per critic round-2).
**#4 resolved**: One-hot daypart encoding (3 columns: γ_morning, γ_afternoon, γ_post_peak;
  pre_sunrise = reference category).

Proceeding to production pass.
