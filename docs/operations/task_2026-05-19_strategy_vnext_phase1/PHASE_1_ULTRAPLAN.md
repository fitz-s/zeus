# Phase 1 ULTRAPLAN — Strategy vNext (Scope C, revised)

**Created**: 2026-05-19 by orchestrator opus (v0); revised same session (v1) after ultraplan critic; revised (v2 — Path D natural-key reframe) after SCAFFOLD critic round 1; revised (v3 — natural-key simplification + writer-side hash + audit-precondition + DELETE-by-source backfill) after SCAFFOLD critic round 2 caught `market_slug` vs `market_id` semantic mismatch (structural pattern: any identifier nominated as join key without cross-table semantic verification fails).
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

## §4. Track 1 — decision_events instrumentation hardening (Path D — natural-key reframe)

### §4.0 Architectural reframe — Path D (post-SCAFFOLD-critic-v1)

**Root design failure** caught at SCAFFOLD critic round 1: ultraplan v1 named `decision_group_id` as the canonical cross-module join key. But `decision_group_id` is a **derived hash** computed at write time from `(strategy_key, market_id, target_date, observation_time_rounded)` (see `src/contracts/decision_group_id.py:50`). The column was never materialized on the 4 source tables (`ensemble_snapshots_v2`, `settlement_commands`, `wrap_unwrap_commands`, `decision_log` — identity buried in `artifact_json`). Materializing the hash cross-table would create 4 drift points + break on hash version changes (`_v1` → `_v2`).

**Path D resolution**: the natural identifying tuple IS the join key. The hash is demoted to a derived audit-only column on `decision_events`.

```
DecisionNaturalKey = (market_id, condition_id, temperature_metric, target_date, observation_time)
```

Properties of Path D vs the rejected paths (A column-add prereq / B artifact_json with hash join / C forward-only):

| Failure mode | A | B | C | **D** |
|---|---|---|---|---|
| Future ALTER forgets `decision_group_id` on a new source table | Recurs | N/A | N/A | **Impossible — no special column to forget** |
| Hash function v1→v2 silently breaks joins | Yes | Yes | N/A | **No — joins on raw fields, not hash** |
| Live writer computes hash from stale inputs | Possible | Possible | Possible | **Impossible — DB computes via TRIGGER** |
| Historical Phase-0 data recoverable | Hard | Hard (JSON parse for hash) | Permanent loss | **Trivial — natural key in artifact_json + market_events_v2** |
| New cross-DB ATTACH path required | Maybe | No | No | **No — independent reads + Python merge** |

This makes the **bug category** impossible going forward (Fitz §1.4): the join key is no longer a thing a writer can forget to populate — it's just the columns the writer already populates to mean anything.

### §4.1 Production surface (Path D)

| Artifact | Path | Type | LOC est. |
|---|---|---|---|
| NewType + helpers | `src/contracts/decision_natural_key.py` (new) | `DecisionNaturalKey` NewType + `from_market_event_row()` + `from_ensemble_snapshot_row()` + `from_artifact_json()` | ~150 |
| Schema | `src/state/db.py` world DB init path | CREATE TABLE with natural-key PK + AFTER INSERT TRIGGER populating `decision_group_id` audit column + 3 indices | — (in db.py) |
| Writer | `src/state/decision_events.py` (new) | `write_decision_event(natural_key, ctx, ekc, intent, *, conn) -> None` — takes typed `DecisionNaturalKey`; conn default = world | ~200 |
| Reader | same module | `read_decision_event_by_natural_key(key)` AND `read_decision_event_by_hash(decision_group_id)` (both indexed) | — (same file) |
| Migration script | `scripts/migrate_decision_events_create_2026_05_19.py` | CREATE TABLE + TRIGGER + indices, idempotent (IF NOT EXISTS) | ~80 |
| Backfill script | `scripts/backfill_decision_events_from_artifact_json.py` | Parse `decision_log.artifact_json` → natural key + 12 old fields; PR-3/6 fields = NULL for historical (Path F honest); optional enrichment from `market_events_v2`/`ensemble_snapshots_v2` | ~200 |
| Antibody | `tests/test_inv_decision_events_completeness.py` | Natural-key completeness: every decision-tagged forecast in `ensemble_snapshots_v2` → at least one `decision_events` row keyed by natural tuple | ~100 |
| Manifest | `architecture/db_table_ownership.yaml` + `architecture/source_rationale.yaml` | `decision_events`: db=world, schema_class=world_class | — |
| Schema bump | SCHEMA_VERSION 12 → 13 + regenerate `tests/state/_schema_pinned_hash.txt` | — |

**Total LOC est.**: ~730 production + ~200 tests/backfill = ~930 across **2 PRs** (sequencing in §4.7).

### §4.2 Schema (Path D — v3 with critic round-2 fixes)

```sql
CREATE TABLE IF NOT EXISTS decision_events (
    -- Natural key (PK) — 5 components, condition_id dropped per critic round 2 SEV-1
    -- (market_events_v2.condition_id is NULLABLE for pre-discovery markets;
    --  market_slug is the durable non-null identifier).
    market_slug         TEXT NOT NULL,             -- matches market_events_v2.market_slug (canonical)
    temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
    target_date         TEXT NOT NULL,             -- settlement calendar day (ISO date)
    observation_time    TEXT NOT NULL,             -- ISO8601 UTC (R-3.1/3.2/3.3 ordering)
    decision_seq        INTEGER NOT NULL,          -- intra-natural-key ordering, see §4.2.1

    -- Enrichment-only (nullable; not in PK because market_events_v2 allows NULL pre-discovery)
    condition_id        TEXT,                      -- Polymarket CTF condition id when known

    -- Audit-only derived hash (NOT a PK input; writer-side computed + trigger backstop per §4.2.2)
    -- Namespace: deid_v1_  (DISTINCT from dgid_v1_ used in calibration_pairs_v2 — see §4.2.2)
    decision_event_id   TEXT,                      -- written by writer at INSERT; trigger validates non-null

    decision_time       TEXT NOT NULL,             -- when the decision was made

    -- Identity (from ExecutionIntent layer; complements natural key)
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,                      -- source: cycle_runtime (live); NULL for backfill (not in pre-Phase-1 artifact_json)
    cycle_iteration     INTEGER,                   -- same as cycle_id

    -- Probability outputs (from EffectiveKellyContext + decision pipeline; all live-only, NULL for backfill)
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,

    -- DecisionSourceContext — PR 3 (5 fields; mixed sources)
    forecast_time              TEXT,
    provider_reported_time     TEXT,               -- Path F Optional
    observation_available_at   TEXT NOT NULL,
    polymarket_end_anchor_source TEXT NOT NULL CHECK (
        polymarket_end_anchor_source IN ('gamma_explicit', 'f1_12z_fallback')
    ),

    -- DecisionSourceContext — PR 6 (8 fields)
    first_member_observed_time TEXT NOT NULL,
    run_complete_time          TEXT NOT NULL,
    zeus_submit_intent_time    TEXT NOT NULL,
    venue_ack_time             TEXT NOT NULL,
    first_inclusion_block_time TEXT,               -- Optional (chain confirms post-decision)
    finality_confirmed_time    TEXT,               -- Optional
    clock_skew_estimate_ms_at_submit INTEGER,      -- Optional (source: settlement_commands; correct name verified at db_table_ownership.yaml:801)
    raw_orderbook_hash_transition_delta_ms INTEGER,-- Optional (source: ensemble_snapshots_v2:175; verified)

    -- Provenance
    schema_version             INTEGER NOT NULL CHECK (schema_version IN (12, 13)),
    source                     TEXT NOT NULL CHECK (
        source IN ('phase0_backfill', 'live_decision')
    ),

    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
);

-- AFTER INSERT TRIGGER as backstop: enforces non-null decision_event_id on every row.
-- Writers SHOULD compute the hash and pass it in INSERT (Option β). Trigger only fires
-- if a writer bypasses (NULL value). This makes the invariant "value is non-null
-- post-INSERT" structurally enforced.
CREATE TRIGGER IF NOT EXISTS decision_events_event_id_backstop
AFTER INSERT ON decision_events
FOR EACH ROW
WHEN NEW.decision_event_id IS NULL
BEGIN
    -- Backstop: emit a recognisable sentinel that audit queries will surface
    -- as a "writer bypass" anomaly. Production: replace sentinel with a
    -- registered SQLite UDF (`decision_event_id_v1_hash`) if Phase 2 chooses
    -- to enforce DB-side hash computation.
    UPDATE decision_events
       SET decision_event_id = 'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'
     WHERE market_slug = NEW.market_slug
       AND temperature_metric = NEW.temperature_metric
       AND target_date = NEW.target_date
       AND observation_time = NEW.observation_time
       AND decision_seq = NEW.decision_seq;
END;

-- Indices for the 3 most common access patterns
CREATE INDEX IF NOT EXISTS idx_decision_events_slug_date
    ON decision_events(market_slug, target_date);
CREATE INDEX IF NOT EXISTS idx_decision_events_strategy
    ON decision_events(strategy_key, decision_time);
CREATE INDEX IF NOT EXISTS idx_decision_events_event_id
    ON decision_events(decision_event_id);        -- audit-only lookups
```

**§4.2.1 `decision_seq` derivation** + race documentation per critic round 2 SEV-2:
- Live writes: `SELECT COALESCE(MAX(decision_seq), -1) + 1 FROM decision_events WHERE market_slug=? AND temperature_metric=? AND target_date=? AND observation_time=?`, then INSERT — both under the **same** `db_writer_lock(LIVE)` flock.
- **Race-closure rationale (Fitz §3 relationship)**: `db_writer_lock(LIVE)` is per-DB-FILE, not per-key. Concurrent same-key writers are serialized because **all world-DB live writes are mutually exclusive** under the LIVE flock. PK constraint `(market_slug, temperature_metric, target_date, observation_time, decision_seq)` is the **backstop antibody** — any race that escapes the lock surfaces as a PK violation, never as silent duplication.
- Backfill: monotonic per natural-key tuple, ordered by `decision_time` ASC, starting at 0.

**§4.2.2 Hash strategy — Option β + namespace separation (revised per critic round 2 Ambiguity 1)**:

Operator choice from critic-recommended hybrid:
- **Namespace**: `deid_v1_` — DISTINCT from `dgid_v1_` (used by `decision_group_id_v1_hash` in `src/contracts/decision_group_id.py:50` for calibration_pairs_v2). The two hashes have completely different input shapes (calibration: `(market_id, target_date, forecast_available_at, source_id, data_version, bin_index, lead_days_bucket)`; decision events: `(market_slug, temperature_metric, target_date, observation_time, decision_seq)`). Cross-namespace lookups MUST fail explicitly — never silently return a sibling-table hash.
- **Function**: define new `decision_event_id_v1_hash(*, market_slug, temperature_metric, target_date, observation_time, decision_seq) -> str` in `src/contracts/decision_natural_key.py`. Output prefix `deid_v1_`.
- **Option β (writer-side hash)**: writer Python computes `decision_event_id_v1_hash(...)` and passes it as the INSERT value. AFTER INSERT trigger is **backstop only** — fires if a writer bypasses (NULL value), populating a sentinel that audit queries surface as anomaly. Trigger is dormant for compliant writers.
- **Rationale for β over α (orchestrator-revised)**: UDF binding requires every reader/writer/test connection to call `connection.create_function` — easy to forget, every miss is a production footgun. Writer-side + trigger backstop: the invariant "decision_event_id is non-null post-INSERT" is enforced by the trigger; if writers comply, trigger is dormant; if writers slip, sentinel value surfaces in audit.

**Required NOT NULL columns** (18 total — corrected from §4.2 v2 typo "17" per critic round 2 Ambiguity 2): 5 natural-key + decision_seq + decision_time + outcome + side + strategy_key + observation_available_at + polymarket_end_anchor_source + first_member_observed_time + run_complete_time + zeus_submit_intent_time + venue_ack_time + schema_version + source = **18**. Note: `decision_event_id` is nullable on INSERT (writer or trigger populates it pre-commit — effectively non-null post-INSERT).

### §4.3 Backfill plan (Path D — artifact_json primary source)

```python
# Backfill from decision_log.artifact_json (Path F-aware: historical rows NULL for PR 3+6 fields)
for chunk in iter_decision_log_ids(chunk_size=500):
    # Step 1: Read decision_log (world DB — same DB as decision_events; no cross-DB read needed)
    log_rows = world.execute("""
        SELECT id, mode, started_at, completed_at, artifact_json, timestamp
        FROM decision_log
        WHERE id IN (?, ?, ...)
    """, chunk).fetchall()

    # Step 2: Parse artifact_json → natural key + 12 old fields
    parsed = []
    for r in log_rows:
        try:
            j = json.loads(r["artifact_json"])
        except json.JSONDecodeError:
            continue  # fail-open: skip malformed entries
        nk = DecisionNaturalKey.from_artifact_json(j)  # extract 5 natural-key fields
        if nk is None:
            continue  # missing natural-key fields → skip (cannot key without them)
        parsed.append((nk, j, r))

    # Step 3 (optional enrichment): independent reads from forecasts/trade for PR-3+6 fields
    # Historical writes may have populated some; best-effort. Keyed on natural fields, NOT hash.
    forecasts_enrich = forecasts.execute("""
        SELECT city, target_date, temperature_metric,
               first_member_observed_time, run_complete_time,
               raw_orderbook_hash_transition_delta_ms
        FROM ensemble_snapshots_v2
        WHERE (city, target_date, temperature_metric) IN ((?,?,?), ...)
    """, enrichment_keys).fetchall()
    # Similarly trade.settlement_commands → clock_skew_estimate_ms_at_submit etc.
    # Resolve (city, target_date, metric) → market_slug via market_events_v2 (slug is the durable id; condition_id is enrichment, may be NULL)

    # Step 4: Merge per natural key + write to world.decision_events using
    # DELETE-by-source THEN INSERT (NOT INSERT OR IGNORE — per critic round 2 SEV-2)
    # Rationale: IGNORE silently skips corrected rows on re-run after bug fix.
    # DELETE scoped to source='phase0_backfill' never touches source='live_decision' rows.
    with db_writer_lock(world_db, write_class=BULK):
        with get_world_connection() as conn:
            for nk, j, log_row in parsed:
                # 1) Delete prior phase0_backfill rows for THIS natural-key
                #    (preserves any source='live_decision' rows at same key)
                conn.execute("""
                    DELETE FROM decision_events
                     WHERE market_slug=? AND temperature_metric=?
                       AND target_date=? AND observation_time=?
                       AND source='phase0_backfill'
                """, (nk.market_slug, nk.temperature_metric, nk.target_date, nk.observation_time))
                # 2) Compute decision_seq based on what remains
                #    (typically 0 after DELETE, unless live rows occupy slots)
                seq = conn.execute("""
                    SELECT COALESCE(MAX(decision_seq), -1) + 1
                    FROM decision_events
                    WHERE market_slug=? AND temperature_metric=?
                      AND target_date=? AND observation_time=?
                """, (nk.market_slug, nk.temperature_metric, nk.target_date, nk.observation_time)).fetchone()[0]
                # 3) Build row + writer-side hash + INSERT (no OR IGNORE)
                row = build_row(
                    nk, seq, j, enrich_data,
                    source='phase0_backfill', schema_version=12,
                    decision_event_id=decision_event_id_v1_hash(
                        market_slug=nk.market_slug,
                        temperature_metric=nk.temperature_metric,
                        target_date=nk.target_date,
                        observation_time=nk.observation_time,
                        decision_seq=seq,
                    ),
                )
                conn.execute("INSERT INTO decision_events (...) VALUES (...)", row)
            conn.commit()
```

**DELETE-by-source THEN INSERT** (per critic round 2 SEV-2): IGNORE silently dropped corrected rows on bug-fix re-runs. DELETE scoped to `source='phase0_backfill'` makes backfill safely re-runnable while protecting `source='live_decision'` rows.

**Path F honesty**: 12 PR-3+6 fields that did not exist when historical artifact_json was written → NULL. CHECK constraints on `polymarket_end_anchor_source` and `schema_version` apply; backfilled rows default to `polymarket_end_anchor_source='gamma_explicit'` (per Phase 0 critic B2 verdict — same rationale: dominant case, retroactive labeling).

**Backfill PRECONDITION** (per critic round 2 SEV-2): PR-T1-A must include `scripts/audit_artifact_json_natural_key_coverage_2026_05_19.py`. Audit reads N≥1000 random `decision_log` rows, runs `DecisionNaturalKey.from_artifact_json()`, reports natural-key recovery rate. **Acceptance gate**: ≥80% recovery → backfill viable; <80% → operator decides between continuing Path D with reduced coverage or pivoting to forward-only (Path C). Audit results land in SCAFFOLD-doc updates BEFORE PR-T1-B opens.

**No new ATTACH path**: each DB uses its independent sanctioned read connection; final write is single-DB to world. INV-37 trivially honored.

### §4.4 Antibody — INV-decision-events-completeness (natural-key, no ATTACH)

```python
def test_inv_decision_events_completeness_natural_key():
    """For every decision-tagged forecast in the last 7d, decision_events
    must carry at least one row keyed by the natural tuple.

    Cross-module relationship test (Fitz §3 invariant pattern).
    Independent read connections — INV-37 trivially honored.
    """
    # Per critic round 2 Ambiguity 3: use existing API (no _read_only suffix function).
    # PR-T1-A adds thin wrappers get_*_connection_read_only() = get_*_connection(write_class=None).
    forecasts = get_forecasts_connection_read_only()  # = get_forecasts_connection(write_class=None)
    world = get_world_connection_read_only()           # = get_world_connection(write_class=None)

    candidates = forecasts.execute("""
        SELECT city, target_date, temperature_metric, available_at
        FROM ensemble_snapshots_v2
        WHERE recorded_at >= datetime('now', '-7 days')
          AND causality_status = 'OK'
    """).fetchall()

    # Resolve (city, target_date, metric) → market_slug via market_events_v2.
    # market_slug is the durable non-null identifier. condition_id is nullable
    # (pre-discovery markets), kept here for enrichment lookups but NOT in the
    # join key (per critic round 2 SEV-1 fix: condition_id IS NULL in SQL never
    # matches; building the antibody on market_slug only).
    slug_map = {
        (r['city'], r['target_date'], r['temperature_metric']): r['market_slug']
        for r in forecasts.execute("""
            SELECT city, target_date, temperature_metric, market_slug
            FROM market_events_v2
            WHERE market_slug IS NOT NULL
        """).fetchall()
    }

    misses = []
    for c in candidates:
        key = (c['city'], c['target_date'], c['temperature_metric'])
        if key not in slug_map:
            continue  # no market = no decision possible
        market_slug = slug_map[key]
        n = world.execute("""
            SELECT COUNT(*) FROM decision_events
            WHERE market_slug = ?
              AND temperature_metric = ?
              AND target_date = ?
        """, (market_slug, c['temperature_metric'], c['target_date'])).fetchone()[0]
        if n == 0:
            misses.append((market_slug, c['target_date'], c['temperature_metric']))

    if not candidates:
        pytest.skip("no decision-tagged forecasts in 7d window — non-degenerate test impossible")
    assert not misses, (
        f"INV-decision-events-completeness violated: "
        f"{len(misses)} decision-tagged forecasts have no decision_events row. "
        f"Sample: {misses[:5]}"
    )
```

Properties:
- Tests a **cross-module relationship** (forecasts.ensemble_snapshots_v2 ↔ forecasts.market_events_v2 ↔ world.decision_events) — Fitz §3 pattern
- **Keyed on market_slug** (not market_id — per critic round 2 SEV-1). market_slug is non-null on market_events_v2; condition_id excluded from join (was the failure mode in v2)
- **Independent read connections** — INV-37 trivially honored (no new ATTACH path)
- **Non-empty precondition** prevents trivial-pass
- **Survives hash version changes** — keyed on natural tuple, not on `decision_event_id`
- **Survives source-table additions** — new tables ship with natural-key columns; writers that omit them immediately visible in misses list

### §4.5 Backward compatibility

Phase 0 readers consume `ensemble_snapshots_v2` / `settlement_commands` / `wrap_unwrap_commands` directly. Phase 1 readers prefer `decision_events`. Dual-source merge for readers:
- Primary lookup: `decision_events` by natural key (new canonical)
- Fallback: if `decision_events` row missing (e.g., pre-`decision_log`-retention history), read from Phase 0 temp storage

Live writes ONLY to `decision_events` post-Phase-1; Phase 0 temp INSERT callsites are removed in Phase 2 cleanup (Phase 1 leaves them as fallback paths to avoid disruption).

Phase 0 temp field removal: deferred to Phase 2 (target: Phase-1-closure + 30d soak).

### §4.6 Out-of-scope for T1
- Forward-fit migrations adding `decision_group_id` columns to source tables (Phase 2+; audit convenience only — Path D makes them optional, never required)
- decision_events as time-series replay store (Phase 2+)
- decision_log → decision_events bridging (Phase 2; both write paths coexist post-T1)
- Hash function v2 (Phase 2+; natural-key joins keep working under any hash version)

### §4.7 PR sequencing (v3 — expanded per critic round 2)

| PR | Title | Contents | LOC est. |
|---|---|---|---|
| **PR-T1-A** | foundation: DecisionNaturalKey + decision_events table + audit + read-only helpers | (1) `src/contracts/decision_natural_key.py` — NewType + 3 helpers + `decision_event_id_v1_hash()`. (2) `src/state/decision_events.py` — writer/reader. (3) `src/state/db.py` — SCHEMA_VERSION 12→13 + CREATE TABLE + backstop TRIGGER + indices + new `get_world_connection_read_only()` / `get_forecasts_connection_read_only()` thin wrappers (6 lines each — encode design intent in name per Fitz §1.2). (4) Migration script. (5) `scripts/audit_artifact_json_natural_key_coverage_2026_05_19.py` — backfill precondition. (6) Manifest entries (`db_table_ownership.yaml` + `source_rationale.yaml`). (7) `tests/state/_schema_pinned_hash.txt` regen. | ~700 |
| **PR-T1-B** | backfill + antibody | (1) `scripts/backfill_decision_events_from_artifact_json.py` — DELETE-by-source THEN INSERT semantic. (2) `tests/test_inv_decision_events_completeness.py` — natural-key antibody, **strict-pass** (no longer xfail). (3) 13 mypy errors in `db.py:8685-8904` schema-adjacent cleanup folded in. (4) Run audit script and document recovery rate; gate on ≥80%. | ~300 |

PR-T1-B depends on PR-T1-A merged + audit recovery rate ≥80%. If audit <80%, operator decision required (continue with reduced coverage vs pivot to forward-only Path C).

Single executor handles both via SendMessage continuity.

### §4.8 W3 closure-critic focus areas (operator directive 2026-05-19)

When the Phase 1 closure verifier (W3) reviews T1 implementation, **focus audit on these cross-module relationship invariants** (operator's specific request given the 2 SEV-1 finds during SCAFFOLD critic rounds were both "identifier semantic mismatch across tables"):

1. **`market_slug` semantic identity across all tables** — verify writes never confuse market_slug with market_id (Polymarket numerical id) or condition_id; grep all `decision_events.market_slug` writers and assert each populates from `market_events_v2.market_slug` (or equivalent verified-slug source).
2. **`condition_id` nullability handling** — every reader/joiner that consumes `decision_events.condition_id` must explicitly handle NULL via `IS NULL` checks (NOT `= NULL`); SQL `= NULL` is silent failure.
3. **Hash namespace isolation** — `deid_v1_` prefix never appears in `calibration_pairs_v2.decision_group_id` (which is `dgid_v1_`) and vice versa. Cross-namespace lookups must explicitly fail.
4. **artifact_json coverage rate** — audit script result on full Phase 0 history; ≥80% gate honored, any divergence documented.
5. **decision_seq race closure under db_writer_lock(LIVE)** — confirm the lock serializes all live writes to world DB (per-DB-file lock, NOT per-key); PK violation is the backstop antibody for any race escaping the lock.
6. **Backfill DELETE-by-source isolation** — confirm `DELETE WHERE source='phase0_backfill'` queries never accidentally touch `source='live_decision'` rows even at same natural-key tuple.
7. **`decision_event_id` non-null post-INSERT** — sentinel value `'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'` should appear zero times in production decision_events after a soak period; non-zero count = writer bypass found.

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
