<!-- Created: 2026-05-19 -->
<!-- Last reused or audited: 2026-05-19 -->
<!-- Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5 -->

# T2 SCAFFOLD — Day0Nowcast unified contract + horizon-aware Platt + xfail antibody

**Status**: SCAFFOLD (pending wave-level opus critic before production pass)
**Author**: sonnet executor, worktree `phase1-t2-day0-nowcast-20260520`
**Entry SHA**: origin/main = `649f73d865` (PR-T1-B merged; T1 complete)

---

## §1. Architectural reframe — unified Day0Nowcast contract

**Problem**: `Day0LowNowcastSignal` is a standalone class with no HIGH-side symmetric.
`Day0HighSignal` has no nowcast lane. Two signal paths duplicating horizon logic with
no shared abstraction.

**Resolution**: `Day0Nowcast(temperature_metric: Literal['high','low'])` is the unified
contract. HIGH/LOW difference is a **parametric constructor argument**, not a routing
dimension. `Day0Router` dispatch axis (HIGH/LOW) is unchanged — each branch internally
invokes `Day0Nowcast` with the corresponding metric.

**HIGH-side wire-up choice (SCAFFOLD picks one per ultraplan §5.2)**: `Day0Nowcast`
parametric. Rationale: creating `Day0HighNowcastSignal` as a 3rd class would add a
shim on top of a shim (HIGH → Day0HighNowcastSignal → Day0Nowcast). The production
pass wires `Day0Nowcast(temperature_metric='high', ...)` directly into `Day0HighSignal`
or the evaluator callsite — no intermediate mirror class.

**Migration plan per ultraplan §5.1.1**:

| Surface | Action |
|---|---|
| `Day0LowNowcastSignal` | REFACTOR → thin shim calling `Day0Nowcast(temperature_metric='low', ...)` |
| `day0_nowcast_context()` | PRESERVED, called by `Day0Nowcast.evaluate()` internally |
| `Day0HighSignal` | EXTENDED — nowcast lane added via `Day0Nowcast(temperature_metric='high', ...)` |
| `Day0Router` | UNCHANGED — dispatch axis stays HIGH/LOW |

`day0_nowcast_context()` at `src/signal/forecast_uncertainty.py:524` is preserved
unmodified in this track. Its signature and return dict are the ground truth for
the blend_weight computation that `Day0Nowcast.evaluate()` will consume.

---

## §2. Schema — `day0_nowcast_runs` table (forecasts DB)

**DB**: forecasts (schema_class=`forecast_class`)
**Schema version**: SCHEMA_FORECASTS_VERSION 3 → 4 (documented here; bump in production pass)

### §2.1 Natural-key PK (mirrors T1 discipline)

```sql
PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, run_seq)
```

`run_seq` replaces `decision_seq` in T1 — same derivation pattern:
- Live writes: `SELECT COALESCE(MAX(run_seq),-1)+1` under forecasts DB writer lock,
  WHERE natural-key matches (same flock serialization as T1)
- Replay: monotonic per natural-key tuple, ordered by `observation_time` ASC, from 0

### §2.2 Audit-only `nowcast_event_id`

Namespace: `nei_v1_` — DISTINCT from:
- T1's `deid_v1_` (decision events)
- calibration's `dgid_v1_` (calibration pairs v2)

**Writer-side hash** (Option β — mirrors T1 choice):
`nowcast_event_id_v1_hash(*, market_slug, temperature_metric, target_date, observation_time, run_seq) -> str`

AFTER INSERT trigger backstop: sentinel `'nei_v1_BACKSTOP_NULL_WRITER_BYPASS'` fires
on NULL (writer bypass anomaly surfaced by audit; dormant for compliant writers).

### §2.3 Full column sketch

```sql
CREATE TABLE IF NOT EXISTS day0_nowcast_runs (
    -- Natural key (PK)
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
    target_date         TEXT NOT NULL,             -- settlement calendar day (ISO date)
    observation_time    TEXT NOT NULL,             -- ISO8601 UTC
    run_seq             INTEGER NOT NULL,          -- intra-natural-key ordering

    -- Audit-only
    nowcast_event_id    TEXT,                      -- nei_v1_ prefix; writer-side hash + trigger backstop

    -- Platt model output
    p_nowcast           REAL NOT NULL,             -- calibrated nowcast probability
    p_now_raw           REAL,                      -- raw empirical climatology input
    p_fused             REAL,                      -- fusion output: w·P_nowcast + (1-w)·P_cal

    -- Horizon covariates (inputs to single Platt fit)
    hours_to_close      REAL NOT NULL,             -- continuous horizon covariate
    daypart             TEXT NOT NULL,             -- e.g. 'morning', 'afternoon', 'evening'
    blend_weight_w      REAL,                      -- sigmoid(-(hours_to_close - 3))

    -- Platt coefficient snapshot (from HorizonPlattFit)
    platt_alpha         REAL,                      -- logit(P_nowcast) coefficient on logit(P_now_raw)
    platt_beta          REAL,                      -- coefficient on hours_to_close
    platt_gamma         REAL,                      -- coefficient on daypart_dummy
    platt_delta         REAL,                      -- coefficient on temperature_metric_indicator
    platt_epsilon       REAL,                      -- intercept

    -- Provenance
    schema_version      INTEGER NOT NULL CHECK (schema_version = 4),
    source              TEXT NOT NULL CHECK (source IN ('live_nowcast', 'replay')),

    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, run_seq)
);

CREATE TRIGGER IF NOT EXISTS day0_nowcast_runs_event_id_backstop
AFTER INSERT ON day0_nowcast_runs
FOR EACH ROW WHEN NEW.nowcast_event_id IS NULL
BEGIN
    UPDATE day0_nowcast_runs
       SET nowcast_event_id = 'nei_v1_BACKSTOP_NULL_WRITER_BYPASS'
     WHERE market_slug = NEW.market_slug
       AND temperature_metric = NEW.temperature_metric
       AND target_date = NEW.target_date
       AND observation_time = NEW.observation_time
       AND run_seq = NEW.run_seq;
END;

CREATE INDEX IF NOT EXISTS idx_day0_nowcast_runs_slug_date
    ON day0_nowcast_runs(market_slug, target_date);
CREATE INDEX IF NOT EXISTS idx_day0_nowcast_runs_event_id
    ON day0_nowcast_runs(nowcast_event_id);
```

**NOT NULL columns (10)**: market_slug, temperature_metric, target_date, observation_time,
run_seq, p_nowcast, hours_to_close, daypart, schema_version, source.

**INV-37**: T2 writes only to forecasts DB (single-DB write; no new ATTACH path).

---

## §3. Math sketch — single horizon-aware Platt fit

Per ultraplan §5.3. Single fit, NOT 6 separate horizon-bucket fits.

```
logit(P_nowcast) = α · logit(P_now_raw)
                + β · hours_to_close
                + γ · daypart_dummy
                + δ · temperature_metric_indicator
                + ε
```

Where:
- `P_now_raw` = empirical climatology over historical same-daypart Day0 observations
  conditioned on current observed temperature trajectory (last 3 readings)
- `hours_to_close` = continuous covariate — fit captures horizon effect in β rather
  than splitting data into 6 bins (preserves data density; avoids bin boundary artifacts)
- `daypart_dummy` ∈ {morning=0, afternoon=1, evening=2} — categorical encoded
- `temperature_metric_indicator` ∈ {low=0, high=1} — allows single cross-metric fit

`HorizonPlattFit` dataclass carries (α, β, γ, δ, ε) + fit metadata.

---

## §4. Forecast-nowcast fusion

Per ultraplan §5.3:

```
P_fused = w · P_nowcast + (1-w) · P_cal
```

where:
```
w = sigmoid(-(hours_to_close - 3))
```

Semantics: weight → 1 (full nowcast) as `hours_to_close → 0`; weight → 0 (full
calibrated forecast) as `hours_to_close → 6+`. The 3h midpoint is the inflection.

Fusion is computed in `Day0Nowcast.evaluate()` after P_nowcast is produced. P_cal
is passed in from the calling context (evaluator supplies it from the standard forecast
pipeline). If P_cal is None (not yet computed), `p_fused = p_nowcast` (nowcast-only).

---

## §5. INV-nowcast-horizon-bound — antibody design

**Invariant**: `Day0Nowcast.evaluate()` MUST raise `NotApplicableHorizon` when
`market.max_hours_to_resolution > 6`.

**Fail-closed rationale**: markets with >6h horizon should be served by the standard
calibrated forecast pipeline, not the nowcast model. Silent fallback (relabeling
forecast output as nowcast) would corrupt P_fused semantics downstream.

**xfail semantics in SCAFFOLD stub**:
- `Day0Nowcast.evaluate()` currently raises `NotImplementedError` (stub body)
- Test expects `NotApplicableHorizon` — a different exception class
- `pytest.raises(NotApplicableHorizon)` does NOT catch `NotImplementedError`; it
  propagates as an unexpected error → xfail-strict counts the test as expected-fail
- Production pass fills `evaluate()` body; first action is the horizon guard →
  test transitions from xfail to strict-pass on the same commit

**xfail reason in test**: documents that "SCAFFOLD stub raises NotImplementedError,
not NotApplicableHorizon; horizon guard enforcement pending production pass".

---

## §6. Relationship tests

### §6.1 — §5.5.1: nowcast and forecast coexist without overwrite

```python
def test_nowcast_and_forecast_coexist_without_overwrite():
    # SCAFFOLD: xfail(strict=True) — Day0Nowcast.evaluate() not yet implemented
    # Market with both lanes populated; assert P_fused is blend, not replacement
    # Neither P_nowcast nor P_cal is dropped
```

### §6.2 — §5.5.2: LOW path regression guard

PRODUCTION test (not a SCAFFOLD stub). Written alongside the LOW shim refactor.
Same inputs → same P_nowcast within float tolerance (1e-9) across the module
boundary (legacy `Day0LowNowcastSignal` ↔ `Day0Nowcast(metric='low')`).
Gates the shim refactor — must pass before PR-T2 merges.

---

## §7. PR plan — PR-T2 (single PR, ~600 LOC)

| Component | Files | LOC est. |
|---|---|---|
| Unified contract | `src/signal/day0_nowcast.py` (new) | ~150 |
| Calibration model | `src/calibration/day0_horizon_calibration.py` (new) | ~120 |
| Storage writer | `src/state/day0_nowcast_store.py` (new) | ~120 |
| LOW shim refactor | `src/signal/day0_low_nowcast_signal.py` (modified) | ~20 |
| HIGH wire-up | `src/signal/day0_high_signal.py` (modified) | ~30 |
| DB schema | `src/state/db.py` (SCHEMA_FORECASTS_VERSION bump + CREATE TABLE) | ~80 |
| Migration script | `scripts/migrate_day0_nowcast_runs_2026_05_19.py` (new) | ~50 |
| Antibody | `tests/test_inv_nowcast_horizon_bound.py` (new) | ~50 |
| Relationship test §6.2 | `tests/test_day0_nowcast_low_regression.py` (new) | ~50 |
| **Total** | | **~670 LOC** |

Production + test LOC within the ultraplan §3 budget (≤800 production, ≤350 tests).

---

## §8. Manifest entries planned (NOT applied in SCAFFOLD)

`architecture/db_table_ownership.yaml`:
```yaml
day0_nowcast_runs:
  db: forecasts
  schema_class: forecast_class
  pk_col: "[market_slug, temperature_metric, target_date, observation_time, run_seq]"
  writer: src/state/day0_nowcast_store.py
  created: 2026-05-19
```

`architecture/source_rationale.yaml`: companion entry linking T2 nowcast source
to `day0_nowcast_context()` helper (preserved) + HorizonPlattFit calibration artifacts.

Both land in the same commit as CREATE TABLE (per ultraplan §8 K1 discipline).

---

## §9. SCHEMA_FORECASTS_VERSION 3 → 4 (NOT bumped in SCAFFOLD)

Location: `src/state/db.py` — `SCHEMA_FORECASTS_VERSION` constant (currently 3 at
line ~2437 per ultraplan §2 reference).

Production pass bumps to 4 and regenerates `tests/state/_schema_pinned_hash.txt`
(or equivalent forecasts-schema pinned hash if separate from world schema hash).

---

## §10. Ambiguities for wave-level opus critic

**#1 — storage writer placement**: ultraplan §5.2 lists `day0_nowcast_runs` as a
new table but does not name the writer module. T1 pattern uses `src/state/decision_events.py`.
Proposal: `src/state/day0_nowcast_store.py`. Critic should confirm this fits the
K1 topology (forecasts DB writer in `src/state/` is the T1 precedent).

**#2 — P_cal source in evaluate()**: `Day0Nowcast.evaluate()` needs the calibrated
forecast probability for fusion. The caller must supply it (evaluator has it). But the
signature in the stub just has `market` + `observation` + `daypart`. Production pass
needs explicit `p_cal: float | None` kwarg or a shared context object. Critic should
confirm the interface shape before production pass locks it.

**#3 — `hours_to_close` vs `max_hours_to_resolution`**: the antibody uses
`market.max_hours_to_resolution > 6`. The Platt covariate uses `hours_to_close`
(runtime-computed from actual close time). These should align but may differ in edge
cases (e.g., market extended). Critic should verify the correct field name on the
market object (check `src/contracts/day0_observation_context.py:90` `.daypart` and
surrounding fields).

**#4 — `p_fused` storage**: §4 fusion produces `p_fused` for the decision pipeline.
Does `day0_nowcast_runs` store only the nowcast output (P_nowcast) or also P_fused?
Current schema sketch includes both. If the decision layer owns fusion (not the nowcast
model), P_fused should NOT be stored in nowcast_runs — it belongs in decision_events
as a derived field. Critic should adjudicate storage ownership.

**#5 — `run_seq` vs `decision_seq` naming consistency**: T1 uses `decision_seq`;
T2 proposes `run_seq`. Both are intra-natural-key monotonic counters. Critic should
confirm `run_seq` is the right name for the nowcast context (not `nowcast_seq`).
