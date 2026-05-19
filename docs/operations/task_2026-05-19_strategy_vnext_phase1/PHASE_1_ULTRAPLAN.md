# Phase 1 ULTRAPLAN — Strategy vNext (Scope C, revised)

**Created**: 2026-05-19 by orchestrator opus (v0); revised 2026-05-19 same session (v1) after opus critic NEEDS_REVISION verdict.
**Authority basis**:
- `docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §M` (canonical Phase 1 instrumentation list + Phase 2-7 strategy upgrade list)
- Operator directive 2026-05-19 selecting Scope C (decision_events + Day0Nowcast)
- `architecture/db_table_ownership.yaml` schema_version 2 (K1 DB split + audit 2026-05-17)
- `architecture/invariants.yaml` INV-37 (cross-DB ATTACH+SAVEPOINT only via sanctioned paths)

**Entry SHA**: origin/main = `f5f1da3a4b` (Phase 0 handoff `fc7704a9fd` +3 commits: #207 antibody fix, #208 cycle cache fix, #209 reseat REDEEM_NEGRISK_MISROUTED retry allowlist; none affect Phase 1 scope)

---

## §0. Scope C — what's in, what's out, why

**Background**: v4 §M defined Phase 1 as 4 instrumentation items (`decision_events`, `book_hash_transitions`, `NoTradeReason` enum, `freshness_registry`) and Phases 2-7 as the strategy upgrades (`Day0Nowcast`, `MarketAnalysisVNext`, Shoulder, etc.). Operator scope-C decision 2026-05-19: pull `Day0Nowcast` forward to Phase 1 for fastest alpha capture; keep `decision_events` (most foundational instrument); defer everything else.

**In scope (Phase 1)**:
| Track | Item | Source |
|---|---|---|
| T1 | `decision_events` instrumentation hardening | v4 §M Phase 1 — foundation |
| T2 | `Day0Nowcast` + `provider_reported_time` writer | v4 §M Phase 2-7 → operator promoted |

**Deferred (per operator Scope-C 2026-05-19)**:
- `book_hash_transitions` (v4 §M Phase 1)
- `NoTradeReason` enum (v4 §M Phase 1)
- `freshness_registry` (v4 §M Phase 1)
- `MarketAnalysisVNext` (v4 §M Phase 2-7)
- Shoulder / candidate stubs / EvidenceLadder / Phase 3-7
- `market_end_anchor_source()` consumer wiring (Phase 0 left function defined-but-unused; depends on MarketAnalysisVNext)

These deferrals are recorded here, not lost. The next Phase-1.5-or-2 ultraplan picks them up.

---

## §1. Wave order

| Wave | Track | Title | Dependencies |
|---|---|---|---|
| W1 | T1 | decision_events instrumentation hardening | none (foundation) |
| W2 | T2 | Day0Nowcast + provider_reported_time writer | W1 lands (Day0Nowcast writes decision rows into decision_events) |
| W3 | closure | Phase 1 verifier + handoff + tags | T1 + T2 |

W1 → W2 is sequential, not parallel. With Track 3 removed, the original "T2 || T3 parallel" rationale collapses; W2 strictly follows W1.

---

## §2. K1 DB ownership (revised per critic SEV-1 #1)

| Artifact | DB | schema_class | Rationale |
|---|---|---|---|
| `decision_events` (new) | **world** | `world_class` | Colocates with `decision_log` (world-class, `db_table_ownership.yaml:481`). Trade-class `decision_log` ghost (line 1244) is `legacy_archived` slated for 2026-08-09 drop — NOT the live home. World-DB authority lets readers join decision_events ↔ decision_log within one connection. |
| `day0_nowcast_runs` (new) | **forecasts** | `forecast_class` | Nowcast is a forecast variant; persists alongside `ensemble_snapshots_v2` + `observations`. SCHEMA_FORECASTS_VERSION (currently 3 at `src/state/db.py:2437`) bump. |

**INV-37 compliance**: All cross-DB writes via sanctioned ATTACH paths only. T1 writes only to world DB (single-DB write — no cross-DB write surface needed). T2 writes only to forecasts DB. **No new ATTACH path created.** Cross-DB **reads** for T1 backfill (§4.4) use existing sanctioned read paths (`get_forecasts_connection_with_world()` for forecasts+world reads, `get_trade_connection_with_world()` for trade+world reads), in separate transactions, with sequential-snapshot fail-open semantics documented inline.

---

## §3. Per-track workflow contract (carried)

1. SCAFFOLD pass (sonnet executor in worktree): writes `scaffolds/<track>_scaffold.md` + skeleton + `xfail` antibody tests + schema bump prepared.
2. Wave-level opus critic (single dispatch per wave): reviews SCAFFOLD against this ultraplan + AGENTS.md + INV-37 + K1 ownership. NOT per-slice.
3. Production pass (sonnet executor): apply revisions, fill SCAFFOLD bodies, write production tests, run regression.
4. Executor self-manages PR lifecycle (CI watch + thread resolve + merge + tag). Orchestrator does NOT run merge mechanics.

Per-track LOC budget: production ≤ 800 LOC; tests ≤ 350 LOC. Exceeding → orchestrator re-review.

Every new file: 3-line provenance header (Created / Last reused or audited / Authority basis).

---

## §4. Track 1 — decision_events instrumentation hardening

### §4.1 Problem
Phase 0 PRs 3+6 added 12 new fields to `DecisionSourceContext` (24 total). Storage during Phase 0 was distributed scaffolding across `ensemble_snapshots_v2` + `settlement_commands` + `wrap_unwrap_commands` — explicitly Phase-0-compatible. Phase 1 lands the canonical `decision_events` table consolidating the 24-field decision record per `decision_group_id`.

### §4.2 Production surface

| Artifact | Path | Type |
|---|---|---|
| Schema | `src/state/db.py` (world DB init path, `_init_schema_world` or equivalent) | CREATE TABLE + indices |
| Writer | `src/state/decision_events.py` (new) | `write_decision_event(ctx, ekc, intent, *, conn=None) -> None` — connection default = world DB |
| Reader | `src/state/decision_events.py` | `read_decision_event_by_group(decision_group_id) -> DecisionEventRow` (world DB read) |
| Migration script | `scripts/migrate_decision_events_create_2026_05_19.py` | CREATE TABLE + idempotent IF NOT EXISTS |
| Backfill script | `scripts/backfill_decision_events_from_phase0_temp.py` | Reads forecasts.ensemble_snapshots_v2 + trade.settlement_commands + world.wrap_unwrap_commands; writes consolidated rows to world.decision_events |
| Manifest entry | `architecture/db_table_ownership.yaml` | `decision_events` row, db=world, schema_class=world_class |
| Schema bump | SCHEMA_VERSION 12 → 13 + regenerate `tests/state/_schema_pinned_hash.txt` |

### §4.3 Schema (with source-contract attribution, per critic SEV-3)

```sql
CREATE TABLE IF NOT EXISTS decision_events (
    decision_group_id   TEXT NOT NULL,            -- PK component
    decision_seq        INTEGER NOT NULL,         -- PK component (see §4.3.1 derivation)
    decision_time       TEXT NOT NULL,            -- ISO8601 UTC

    -- Identity (from ExecutionIntent layer)
    market_id           TEXT NOT NULL,
    condition_id        TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,

    -- Probability outputs (from EffectiveKellyContext + decision pipeline)
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,

    -- DecisionSourceContext — PR 3 (4 fields)
    forecast_time       TEXT,
    observation_time    TEXT NOT NULL,            -- R-3.1/3.2/3.3 ordering invariants
    provider_reported_time TEXT,                  -- Path F Optional
    observation_available_at TEXT NOT NULL,
    polymarket_end_anchor_source TEXT NOT NULL,   -- 'gamma_explicit' | 'f1_12z_fallback'

    -- DecisionSourceContext — PR 6 (8 fields)
    first_member_observed_time TEXT NOT NULL,
    run_complete_time          TEXT NOT NULL,
    zeus_submit_intent_time    TEXT NOT NULL,
    venue_ack_time             TEXT NOT NULL,
    first_inclusion_block_time TEXT,              -- chain-confirmed, may arrive post-decision (Optional)
    finality_confirmed_time    TEXT,              -- chain-confirmed (Optional)
    clock_skew_estimate_ms     INTEGER,           -- Optional (probe may fail)
    raw_orderbook_hash_transition_delta_ms INTEGER,  -- Optional (first observation = NULL)

    -- Provenance
    schema_version             INTEGER NOT NULL,  -- 13 for live writes; 12 for backfilled rows
    source                     TEXT NOT NULL,     -- 'phase0_backfill' | 'live_decision'

    PRIMARY KEY (decision_group_id, decision_seq)
);

CREATE INDEX IF NOT EXISTS idx_decision_events_market ON decision_events(market_id);
CREATE INDEX IF NOT EXISTS idx_decision_events_strategy ON decision_events(strategy_key);
CREATE INDEX IF NOT EXISTS idx_decision_events_time ON decision_events(decision_time);
```

**§4.3.1 decision_seq derivation**:
- Backfill: monotonic per `decision_group_id`, ordered by `(forecast_time, observation_time)` ASC, starting at 0
- Live writes: incremented atomically at write time; first write for a group_id = 0

Required NOT NULL fields per critic B3 classification (Phase 0 carryover): 7 REQUIRED (decision_group_id, decision_time, market_id, condition_id, outcome, side, strategy_key) + 7 PR-3/PR-6 REQUIRED (observation_time, observation_available_at, polymarket_end_anchor_source, first_member_observed_time, run_complete_time, zeus_submit_intent_time, venue_ack_time). All chain-finality + Optional/Path-F fields nullable.

### §4.4 Backfill plan (revised per critic SEV-1 #2 + #3)

**Cross-DB semantic — sequential snapshot read with fail-open** (NOT atomic across DBs; INV-37 honored by avoiding cross-DB write surface):

```python
# Pseudocode for backfill (chunked by decision_group_id)
for chunk in iter_group_ids(chunk_size=500):
    # Step 1: Read forecasts side (independent read-only conn)
    forecasts_rows = read_from_forecasts(
        table="ensemble_snapshots_v2",
        columns=[
            "decision_group_id", "first_member_observed_time", "run_complete_time",
            "raw_orderbook_hash_transition_delta_ms",  # CORRECTED per critic SEV-1 #3
        ],
        where=f"decision_group_id IN {chunk}",
    )
    # Step 2: Read trade side (independent read-only conn)
    trade_rows = read_from_trade(
        table="settlement_commands",
        columns=[
            "decision_group_id", "polymarket_end_anchor_source",
            "zeus_submit_intent_time", "venue_ack_time", "clock_skew_estimate_ms",
        ],
        where=f"decision_group_id IN {chunk}",
    )
    # Step 3: Read world side (independent read-only conn, or co-conn with write)
    world_rows = read_from_world(
        table="wrap_unwrap_commands",
        columns=[
            "decision_group_id", "first_inclusion_block_time", "finality_confirmed_time",
        ],
        where=f"decision_group_id IN {chunk}",
    )

    # Step 4: Merge by decision_group_id (Python-side; missing → NULL Optional)
    merged = merge_by_group_id(forecasts_rows, trade_rows, world_rows)

    # Step 5: WRITE to world.decision_events under db_writer_lock(BULK)
    with db_writer_lock(world_db, write_class=BULK):
        with get_world_connection() as conn:  # single-DB write — INV-37 trivially honored
            for row in merged:
                conn.execute("INSERT INTO decision_events ...", row)
            conn.commit()
```

**Fail-open semantics**: if a side's data is partially missing for a group_id, the row still writes with Optional fields = NULL. `source='phase0_backfill'` distinguishes vs live writes. Backfill is **idempotent** via `INSERT OR REPLACE INTO decision_events` on PK conflict — safe to re-run after partial failures.

**Source column corrections per critic SEV-1 #3**:
- `raw_orderbook_hash_transition_delta_ms` ← `ensemble_snapshots_v2` (forecasts), verified at `src/state/schema/v2_schema.py:211` + yaml line 175
- `clock_skew_estimate_ms` ← `settlement_commands` (trade), per PR 6 column placement (operator/critic-verify in SCAFFOLD)

### §4.5 Antibody — INV-decision-events-completeness (revised per critic SEV-2 §4.5)

Cross-DB count comparison via **read-only ATTACH** (existing `get_forecasts_connection_with_world()` is the sanctioned read path for forecasts→world):

```python
def test_inv_decision_events_completeness_per_recent_cycle():
    # Use sanctioned ATTACH read: forecasts attached to world
    with get_forecasts_connection_with_world(mode="ro") as conn:
        # Pick a recent live cycle that should have decisions
        result = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM ensemble_snapshots_v2
                 WHERE cycle_id = ? AND decision_group_id IS NOT NULL) AS forecast_count,
                (SELECT COUNT(*) FROM decision_events
                 WHERE cycle_id = ?) AS event_count
        """, (cycle_id, cycle_id)).fetchone()

    n_forecast, n_events = result
    # Non-empty precondition gates the assertion (avoids trivial-pass on empty cycles)
    if n_forecast == 0:
        pytest.skip(f"cycle {cycle_id} has no decision-tagged forecasts; non-degenerate test impossible")
    assert n_events == n_forecast, (
        f"INV-decision-events-completeness violated: "
        f"cycle={cycle_id} forecasts={n_forecast} events={n_events}"
    )
```

Join key: `decision_group_id` (NOT `strategy_key` — strategy_key is not on ensemble_snapshots_v2 directly). Non-empty precondition prevents trivial pass.

### §4.6 Backward compatibility

Phase 0 readers consume `ensemble_snapshots_v2` / `settlement_commands` / `wrap_unwrap_commands` directly. Phase 1 readers prefer `decision_events`. **Dual-source merge** semantics for readers (per critic ambiguity flag):
- Primary lookup: `decision_events` by `decision_group_id`
- Fallback: if `decision_events` row missing (e.g., backfill gap), fall back to Phase 0 temp storage with `source='inferred_legacy'` tag
- Live writes ONLY to `decision_events` post-Phase-1; Phase 0 temp fields STOP being written (no double-writes)

Phase 0 temp fields removal: deferred to Phase 2 — Phase 2 confirms decision_events is healthy + has full backfill coverage + no readers depend on temp fields, then drops the temp columns. Removal date target: post-Phase-1-closure + 30d soak.

### §4.7 Out-of-scope for T1
- Phase 0 temp field removal (Phase 2)
- decision_events as time-series replay store (Phase 2+)
- decision_log → decision_events bridging (Phase 2; both write paths coexist post-T1)

---

## §5. Track 2 — Day0Nowcast + provider_reported_time writer (revised per critic SEV-2 Day0)

### §5.1 Problem (revised — explicit coexistence with existing nowcast)
**Existing state** (verified):
- `src/signal/day0_low_nowcast_signal.py` — `Day0LowNowcastSignal` class consuming `day0_nowcast_context()` from `forecast_uncertainty.py:524`
- `src/signal/day0_router.py` — HIGH/LOW dispatch (NOT forecast/nowcast lane dispatch)
- `src/signal/day0_high_signal.py` — HIGH-side decision signal; **no HIGH-side nowcast equivalent today**
- `src/signal/forecast_uncertainty.py:524-…` — `day0_nowcast_context()` helper, currently called from LOW-side signal + uncertainty estimation

**Phase 1 T2 goal**: extract a unified `Day0Nowcast` model contract that subsumes the existing LOW path and adds a symmetric HIGH-side. Horizon-aware Platt calibration runs as ONE fit with horizon-as-continuous-covariate (NOT 6 separate fits — resolved per critic Ambiguity Risk).

### §5.1.1 Relationship to existing Day0LowNowcastSignal (per critic SEV-2)

| Surface | Action | Migration |
|---|---|---|
| `Day0LowNowcastSignal` class | REFACTOR (subsumed) | Becomes `Day0Nowcast(temperature_metric=LOW, …)` instantiation under the unified contract |
| `day0_nowcast_context()` helper | PRESERVED, called by new contract | Phase 2 may further refactor; T2 leaves it alone |
| `Day0HighSignal` | EXTENDED (gain nowcast lane) | New `Day0HighNowcastSignal` mirror class, OR `Day0Nowcast(temperature_metric=HIGH, …)` mirror path — SCAFFOLD picks one |
| `Day0Router` | UNCHANGED dispatch axis (HIGH/LOW) | Each branch internally invokes the corresponding nowcast contract — NO new "lane" routing |

Router stays HIGH/LOW dispatched. The new contract's HIGH/LOW behavior is parametric, NOT routing-level.

### §5.2 Production surface (revised)

| Artifact | Path | Type |
|---|---|---|
| Unified nowcast contract | `src/signal/day0_nowcast.py` (new) | `Day0Nowcast(temperature_metric, observation, daypart, market) -> P_nowcast` |
| Horizon-aware calibration | `src/calibration/day0_horizon_calibration.py` (new) | Single-fit Platt with `hours_to_close` as continuous covariate (NOT 6 separate fits) |
| HIGH-side wire-up | `src/signal/day0_high_signal.py` (modified) OR `src/signal/day0_high_nowcast_signal.py` (new mirror) | Choice deferred to SCAFFOLD; both paths reviewed by wave critic |
| LOW-side refactor | `src/signal/day0_low_nowcast_signal.py` (modified) | Becomes thin shim calling `Day0Nowcast(temperature_metric=LOW, ...)` |
| Storage | `day0_nowcast_runs` table (forecasts DB, new) | Per-run output + diagnostic |
| provider_reported_time writer | `src/data/observation_client.py` (modified, conditional) | If T2 ingests NOAA/METAR explicitly, populate the field; else leave None (Path F honest semantic) |
| Manifest | `architecture/db_table_ownership.yaml` + `architecture/source_rationale.yaml` |
| Schema bump | SCHEMA_FORECASTS_VERSION 3 → 4 |

### §5.3 Math sketch (revised — single fit, not 6)

Standard Platt (Extended, in repo): `logit(P_cal) = A·logit(P_raw) + B·lead_days + C`

Day0 nowcast (single fit, horizon-as-covariate):
```
logit(P_nowcast) = α·logit(P_now_raw)
                  + β·hours_to_close
                  + γ·daypart_dummy
                  + δ·temperature_metric_indicator
                  + ε
```

`P_now_raw` = empirical climatology over historical same-daypart Day0 observations conditioned on current observed temperature trajectory (last 3 readings).

Forecast-nowcast fusion (when both available for same market):
`P_fused = w · P_nowcast + (1-w) · P_cal` where `w = σ(-(hours_to_close - 3))` (sigmoid centered at 3h; weight → nowcast as horizon ↓).

### §5.4 provider_reported_time wiring
Per Phase 0 Path F resolution: `provider_reported_time: Optional[str] = None` with conditional validator. T2 wires writer ONLY if Day0Nowcast adds an observation source with an explicit provider-reported timestamp:
- WU API exposes only `valid_time_gmt` → leave None
- If T2 adds NOAA/METAR with explicit issue times → populate from those payloads
- Honest Optional semantic: never fabricate

### §5.5 Relationship test — Day0Nowcast coexistence (revised per critic gap)

Two relationship tests in T2:

**§5.5.1**: Nowcast and standard forecast coexist without overwrite
```python
def test_nowcast_and_forecast_coexist_without_overwrite():
    # Same Day0 market, both lanes produce values
    # market_fusion receives both, applies weighted blend per §5.3
    # Neither single-source lane is dropped
```

**§5.5.2**: Day0Nowcast LOW path matches legacy Day0LowNowcastSignal output (no regression during refactor)
```python
def test_day0_nowcast_low_matches_legacy_low_nowcast_signal():
    # Same inputs (observation, daypart, market — LOW)
    # legacy Day0LowNowcastSignal.evaluate() == Day0Nowcast(metric=LOW).evaluate()
    # Strict equality on P_nowcast within float tolerance
```

### §5.6 Antibody — INV-nowcast-horizon-bound
`Day0Nowcast.evaluate(market)` raises `NotApplicableHorizon` if `market.max_hours_to_resolution > 6`. Live mode MUST NOT silently fall back to forecast pipeline output relabeled as nowcast.

```python
def test_day0_nowcast_horizon_bound_enforces_6h_ceiling():
    market_long = make_market(max_hours_to_resolution=8.0)
    with pytest.raises(NotApplicableHorizon):
        Day0Nowcast().evaluate(market_long)
```

---

## §6. Opus budget — Phase 1 fresh ledger (9 dispatches; 1 already used)

| Phase | Dispatch | Spent | Remaining |
|---|---|---|---|
| Plan v0 critic | 1× ultraplan opus critic (verdict NEEDS_REVISION) | 1 | 8 |
| Plan v1 critic (optional) | 1× re-critic if revisions are complex | reserved | — |
| W1 critic | 1× wave critic on T1 SCAFFOLD | — | — |
| W2 critic | 1× wave critic on T2 SCAFFOLD | — | — |
| W3 closure | 1× closure verifier | — | — |
| **Total projected** | 5 dispatches | | 4 reserve |

Reserve covers: NEEDS_REVISION re-critic, cross-track integration check, surprise architectural pivot.

---

## §7. Pre-existing follow-ups carried in (T1/T2 fold-in)

| Item | Track | Plan |
|---|---|---|
| 18 pre-existing `test_runtime_guards.py` failures | Separate thread post-Phase-1 | Tracked TODO |
| `harvester_pnl_resolver.py:73` linter | NOT folded (decision-pipeline-only; off scope) | Defer |
| `test_evaluator_strategy_key_failclosed.py:48` linter | T1 (decision-adjacent) | Fix during T1 production |
| 13 mypy errors `db.py:8685-8904` | T1 (schema-adjacent) | Fix during T1 production |
| `inject_may2021_markets_2026_05_19.py` script | RESOLVED 2026-05-19 via allowlist | Operator decision (delete or wrap) at Phase 1 closure |
| Phase 0 worktrees on disk | Operator-decided cleanup | Not auto-pruned |

---

## §8. Compound Protocol — defaults carried + reinforced for Phase 1

Carried from Phase 0:
- Haiku audits, never executes destructive
- OMC marketplace cache propagation gap: bulk-refresh agents/*.md when SendMessage shows "model may not exist"
- Executor owns PR lifecycle (CI watch + thread resolve + merge + tag)
- Bot review comments → executor reads PR directly, no orchestrator relay
- git-master event-only emits, dedup per signal
- Multi-PR wave → batched PR open after parallel completion
- Subagent reuse via SendMessage is default
- Monitor silence is success
- Path F idiom for unobservable schema fields
- Don't over-write memory
- Wave-level critic, NOT per-slice

Reinforced for Phase 1:
- K1 DB ownership BEFORE schema commit — every new table appears in `architecture/db_table_ownership.yaml` in SAME commit as CREATE TABLE
- 3-line provenance headers mandatory on every new src/scripts/tests file
- Topology-doctor `--navigation` before EVERY src/scripts edit
- Cross-DB writes ONLY through 2 sanctioned ATTACH paths; cross-DB reads via independent connections + sequential semantic + documented fail-open semantic — NEVER invent a new ATTACH path mid-track

---

## §9. Acceptance criteria & tags

| Track | Acceptance | Tag |
|---|---|---|
| T1 | decision_events table exists in **world** DB; backfill produces ≥ Phase-0-temp-storage equivalent rows (count match per cycle); INV-decision-events-completeness antibody PASS; SCHEMA_VERSION 13 + pinned_hash regenerated; manifest updated; PR merged | `phase1_track1_landed` |
| T2 | Day0Nowcast unified contract subsumes Day0LowNowcastSignal (legacy regression test PASS); HIGH-side nowcast wired; horizon-aware Platt fitted (single fit); INV-nowcast-horizon-bound antibody PASS; provider_reported_time wired conditional on observation source; SCHEMA_FORECASTS_VERSION 4; PR merged | `phase1_track2_landed` |
| W3 | Both track tags on remote; Phase 1 → Phase 2 handoff written; closure verifier PASS; deferred items documented for Phase 2 pickup | `phase1_closure_landed` |

---

## §10. Compact-survival next-session brief

"Resume Zeus Phase 1 Scope C. Read `docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md` (v1, revised). Scope C = decision_events (T1) + Day0Nowcast (T2); MarketAnalysisVNext + book_hash_transitions + NoTradeReason + freshness_registry deferred to Phase 2. origin/main = `f5f1da3a4b`. Phase 1 ultraplan v1 addresses critic SEV-1: decision_events lives on **world** DB (not trade); cross-DB backfill uses sequential read + fail-open semantic (no new ATTACH path); `raw_orderbook_hash_transition_delta_ms` ← ensemble_snapshots_v2 (forecasts). Next step: implement in fresh worktree from origin/main; T1 SCAFFOLD via sonnet executor; wave-level opus critic on SCAFFOLD before production; executor self-manages PR lifecycle."

---

— Phase 1 ULTRAPLAN v1 (Scope C, post-critic-revision). Ready for implementation.
