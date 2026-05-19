<!-- Created: 2026-05-19 -->
<!-- Last reused or audited: 2026-05-19 -->
<!-- Authority basis: PHASE_1_ULTRAPLAN.md §4 -->

# T1 SCAFFOLD — decision_events instrumentation hardening

**Status**: SCAFFOLD (pre-critic, pre-production)
**Author**: sonnet executor, worktree `phase1-t1-decision-events-20260519`
**Entry SHA**: origin/main = `f5f1da3a4b`

---

## 1. Pre-flight admission verdict

`topology_doctor.py --navigation --json` returned:

```
status: advisory_only
profile_id: generic
confidence: 0.0
needs_typed_intent: true
selected_by: weak_term_nonselectable
```

Advisory only — no strong-term profile matched. Out-of-scope file advisory:
- `architecture/source_rationale.yaml` listed as `companion_missing` (not in scope for this dispatch; production pass must include it alongside yaml manifest update).

**Decision**: proceed. The advisory reflects topology_doctor profile gap, not a conflicting ownership claim. `architecture/db_table_ownership.yaml:481` confirms `decision_log` = world-class; `decision_events` follows same pattern.

---

## 2. DB ownership

| Table | DB | schema_class | Version owner | Colocation rationale |
|---|---|---|---|---|
| `decision_events` (new) | **world** | `world_class` | `SCHEMA_VERSION` | Colocates with `decision_log` (world-class, `db_table_ownership.yaml:481`). World-DB authority allows readers to join `decision_events ↔ decision_log` within one connection. |

**INV-37 compliance**: T1 writes only to world DB (single-DB write — INV-37 trivially honored). Cross-DB backfill reads use 3 independent read-only connections (sequential snapshot, fail-open).

**Do NOT create a new ATTACH path for T1.**

---

## 3. Schema (final, with source-contract attribution)

```sql
CREATE TABLE IF NOT EXISTS decision_events (
    -- PK
    decision_group_id   TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    decision_time       TEXT NOT NULL,            -- ISO8601 UTC

    -- Identity (source: ExecutionIntent)
    market_id           TEXT NOT NULL,
    condition_id        TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,

    -- Probability outputs (source: EffectiveKellyContext + decision pipeline)
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,

    -- DecisionSourceContext PR 3 (4 fields; source: observation pipeline)
    forecast_time       TEXT,
    observation_time    TEXT NOT NULL,
    provider_reported_time TEXT,                  -- Path F Optional; None if WU API
    observation_available_at TEXT NOT NULL,
    polymarket_end_anchor_source TEXT NOT NULL,   -- 'gamma_explicit' | 'f1_12z_fallback'

    -- DecisionSourceContext PR 6 (8 fields; mixed sources)
    first_member_observed_time TEXT NOT NULL,     -- source: ensemble_snapshots_v2 (forecasts)
    run_complete_time          TEXT NOT NULL,     -- source: ensemble_snapshots_v2 (forecasts)
    zeus_submit_intent_time    TEXT NOT NULL,     -- source: settlement_commands (trade)
    venue_ack_time             TEXT NOT NULL,     -- source: settlement_commands (trade)
    first_inclusion_block_time TEXT,              -- source: wrap_unwrap_commands (world); Optional
    finality_confirmed_time    TEXT,              -- source: wrap_unwrap_commands (world); Optional
    clock_skew_estimate_ms     INTEGER,           -- source: settlement_commands (trade); Optional
    raw_orderbook_hash_transition_delta_ms INTEGER,  -- source: ensemble_snapshots_v2 (forecasts); Optional

    -- Provenance
    schema_version             INTEGER NOT NULL,  -- 13 for live writes; 12 for backfilled rows
    source                     TEXT NOT NULL,     -- 'phase0_backfill' | 'live_decision'

    PRIMARY KEY (decision_group_id, decision_seq)
);

CREATE INDEX IF NOT EXISTS idx_decision_events_market   ON decision_events(market_id);
CREATE INDEX IF NOT EXISTS idx_decision_events_strategy ON decision_events(strategy_key);
CREATE INDEX IF NOT EXISTS idx_decision_events_time     ON decision_events(decision_time);
```

### 3.1 Source verification

- `raw_orderbook_hash_transition_delta_ms` ← `ensemble_snapshots_v2` (forecasts): verified at
  `src/state/schema/v2_schema.py:211` (ALTER TABLE adds the column to ensemble_snapshots_v2) and
  `src/engine/evaluator.py:4052,4062` (reads from ens_result, writes to ensemble_snapshots_v2).
- `clock_skew_estimate_ms` ← `settlement_commands` (trade): per PR 6 placement — VERIFY in production pass
  by grepping `settlement_commands` schema before writing.
- NOT from `wrap_unwrap_commands` — that table owns only chain-finality fields.

### 3.2 decision_seq derivation

- **Backfill**: monotonic per `decision_group_id`, ordered by `(forecast_time, observation_time)` ASC, starting at 0
- **Live writes**: `SELECT COALESCE(MAX(decision_seq), -1) + 1 FROM decision_events WHERE decision_group_id = ?` within the write transaction — production pass implements atomically

### 3.3 NOT NULL classification

REQUIRED (7): `decision_group_id`, `decision_time`, `market_id`, `condition_id`, `outcome`, `side`, `strategy_key`

PR-3/PR-6 REQUIRED (7): `observation_time`, `observation_available_at`, `polymarket_end_anchor_source`, `first_member_observed_time`, `run_complete_time`, `zeus_submit_intent_time`, `venue_ack_time`

Optional (all chain-finality + probabilistic): `first_inclusion_block_time`, `finality_confirmed_time`, `clock_skew_estimate_ms`, `raw_orderbook_hash_transition_delta_ms`, `forecast_time`, `provider_reported_time`, `cycle_id`, `cycle_iteration`, `p_posterior`, `edge`, `target_size_usd`, `target_price`

---

## 4. Writer + reader signatures

```python
# src/state/decision_events.py

def write_decision_event(
    ctx: DecisionSourceContext,
    ekc: EffectiveKellyContext,
    intent: ExecutionIntent,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    """
    Write a single decision_events row for a live decision.
    If conn=None, opens get_world_connection(write_class="live").
    decision_seq is derived atomically via MAX(decision_seq)+1.
    source='live_decision', schema_version=SCHEMA_VERSION.
    """
    ...

def read_decision_event_by_group(
    decision_group_id: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[DecisionEventRow]:
    """
    Read all decision_events rows for a decision_group_id from world DB.
    Returns list ordered by decision_seq ASC.
    Returns [] if not found (caller may fall back to Phase 0 temp storage).
    If conn=None, opens get_world_connection(write_class=None).
    """
    ...
```

---

## 5. Backfill plan

Cross-DB semantic: sequential snapshot reads (3 independent read-only connections), Python merge, single-DB write to world. NOT atomic across DBs. INV-37 honored (no new ATTACH path).

```
for chunk in iter_group_ids(source=world_decision_log, chunk_size=500):
    1. Read forecasts (independent read conn):
       ensemble_snapshots_v2 → first_member_observed_time, run_complete_time,
                                raw_orderbook_hash_transition_delta_ms
       WHERE decision_group_id IN chunk

    2. Read trade (independent read conn):
       settlement_commands → zeus_submit_intent_time, venue_ack_time,
                             clock_skew_estimate_ms, polymarket_end_anchor_source
       WHERE decision_group_id IN chunk

    3. Read world (independent read conn):
       wrap_unwrap_commands → first_inclusion_block_time, finality_confirmed_time
       decision_log         → decision_time, market_id, condition_id, outcome,
                              side, strategy_key, observation_time,
                              observation_available_at, forecast_time
       WHERE decision_group_id IN chunk

    4. Python-merge by decision_group_id
       Missing side → Optional fields = NULL (fail-open, not abort)

    5. Write to world.decision_events
       INSERT OR REPLACE INTO decision_events ...
       schema_version=12, source='phase0_backfill'
       (idempotent on PK conflict — safe to re-run)
```

**Fail-open invariant**: partial-missing group_id still writes. `source='phase0_backfill'` distinguishes from live rows.

---

## 6. Migration script outline

`scripts/migrate_decision_events_create_2026_05_19.py`:

- Opens world DB with `get_world_connection(write_class="bulk")`
- Executes `CREATE TABLE IF NOT EXISTS decision_events (...)` per §3
- Executes 3 `CREATE INDEX IF NOT EXISTS` per §3
- Idempotent — safe to re-run; IF NOT EXISTS guards prevent errors
- Does NOT bump SCHEMA_VERSION (that happens in `src/state/db.py` in production pass)
- Logs row to stdout: `"decision_events table created/verified in {db_path}"`

---

## 7. Schema bump path (document only — NOT yet applied)

File to modify: `src/state/db.py`

Change: `SCHEMA_VERSION = 12` → `SCHEMA_VERSION = 13`

Comment update:
```python
SCHEMA_VERSION = 13  # 2026-05-19 T1: decision_events table + indices (world DB)
```

After bump: regenerate `tests/state/_schema_pinned_hash.txt` via:
```bash
python -m pytest tests/state/test_schema_pinned_hash.py --update-hash
# or however the existing pinned hash is generated — check test file
```

Also: add `decision_events` entry to `architecture/db_table_ownership.yaml` (see §8).
Also: add `decision_events` entry to `architecture/source_rationale.yaml` (companion file; production pass).

---

## 8. Manifest entries (document only — NOT yet applied)

### db_table_ownership.yaml (after `decision_log` entry at line 481)

```yaml
  - name: decision_events
    db: world
    schema_class: world_class
    schema_version_owner: SCHEMA_VERSION
    created_by: migrate_decision_events_create_2026_05_19
    pk_col: "[decision_group_id, decision_seq]"
    notes: >
      Canonical consolidated decision record per decision_group_id.
      Phase 1 T1 landing (PHASE_1_ULTRAPLAN.md §4).
      Colocates with decision_log (world-class) for join capability.
      Backfilled from ensemble_snapshots_v2 (forecasts) + settlement_commands (trade)
      + wrap_unwrap_commands (world) via sequential-snapshot fail-open backfill script.
```

---

## 9. Antibody design

### INV-decision-events-completeness

**Invariant**: For any recent live cycle, `COUNT(decision_events WHERE cycle_id=X)` equals `COUNT(ensemble_snapshots_v2 WHERE cycle_id=X AND decision_group_id IS NOT NULL)`.

**Test file**: `tests/test_inv_decision_events_completeness.py`

**xfail-strict reason**: `"SCAFFOLD — antibody pending T1 production"`

**Read path**: `get_forecasts_connection_with_world()` (sanctioned ATTACH: forecasts=MAIN, world=ATTACHED). This is a read-only use of the cross-DB attach path — no write surface.

**Ambiguity flagged for production pass**: `get_forecasts_connection_with_world` signature is `*, write_class: WriteClass | str = "bulk"` — no `mode="ro"` kwarg exists. The §4.5 pseudocode shows `mode="ro"` which is incorrect. Production pass must open this connection with `write_class="bulk"` or open independent read connections. Do NOT add a `mode` kwarg.

---

## 10. Skeleton files checklist

| File | Type | LOC budget |
|---|---|---|
| `src/state/decision_events.py` | writer + reader stubs | ~40 LOC |
| `scripts/migrate_decision_events_create_2026_05_19.py` | migration stub | ~35 LOC |
| `scripts/backfill_decision_events_from_phase0_temp.py` | backfill pseudocode shell | ~50 LOC |
| `tests/test_inv_decision_events_completeness.py` | xfail antibody scaffold | ~35 LOC |

Total skeleton LOC target: ~160 (well within 200 budget).

---

## 11. Ambiguities for critic / production pass

1. **`get_forecasts_connection_with_world(mode="ro")` does not exist** — §4.5 antibody pseudocode uses a non-existent kwarg. Production pass picks correct read path. (See §9 above.)

2. **"24 fields" count in §4.1** refers to DecisionSourceContext fields, not total table columns. §4.3 SQL has 29 columns total (identity + probability + PR3 + PR6 + provenance). The schema follows §4.3 verbatim.

3. **`clock_skew_estimate_ms` source attribution** — ultraplan §4.4 lists it under trade-side `settlement_commands`. Production pass must grep `settlement_commands` schema to confirm before writing.

4. **`topology_doctor` companion advisory** — `architecture/source_rationale.yaml` required alongside yaml manifest update. Production pass must include it.
