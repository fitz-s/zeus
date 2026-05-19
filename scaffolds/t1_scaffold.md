<!-- Created: 2026-05-19 -->
<!-- Last reused or audited: 2026-05-19 -->
<!-- Authority basis: PHASE_1_ULTRAPLAN.md §4 (Path D natural-key reframe, v3) -->

# T1 SCAFFOLD v3 — decision_events instrumentation hardening (Path D)

**Status**: SCAFFOLD v3 (post-critic-round-2 natural-key simplification + writer-side hash)
**Author**: sonnet executor, worktree `phase1-t1-decision-events-20260519`
**Entry SHA**: origin/main = `f5f1da3a4b`

---

## 1. Architectural reframe (Path D — v3)

`decision_group_id` is a **derived hash**, not a materialised join key.  Treating
it as a PK would create 4 drift points and break on hash version changes.

**Path D v3**: the natural identifying tuple IS the join key.  Hash is audit-only.

```
DecisionNaturalKey = (market_slug, temperature_metric, target_date, observation_time, decision_seq)
```

v3 simplifications vs v2:
- **PK is 5 components** (not 6) — `condition_id` DROPPED from PK.
  `market_events_v2.condition_id` is NULLABLE for pre-discovery markets;
  `market_slug` is the durable non-null identifier.
- **Column renamed**: `market_id` → `market_slug`; `decision_group_id` → `decision_event_id`
- **Hash namespace**: `deid_v1_` prefix (DISTINCT from calibration's `dgid_v1_`).
  New function `decision_event_id_v1_hash()` in `src/contracts/decision_natural_key.py`.
- **Option β (writer-side hash)**: writer Python computes hash, passes in INSERT.
  Trigger backstop fires ONLY on NULL, setting sentinel `'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'`.
  No SQLite UDF binding required (resolves v2 ambiguity #1).
- **Backfill**: DELETE-by-source THEN INSERT (NOT INSERT OR IGNORE — per critic SEV-2).

See `PHASE_1_ULTRAPLAN.md §4.0` for full Path A/B/C/D comparison matrix.

---

## 2. DB ownership

| Table | DB | schema_class | Rationale |
|---|---|---|---|
| `decision_events` (new) | **world** | `world_class` | Colocates with `decision_log` (world-class, `db_table_ownership.yaml:481`). |

**INV-37**: T1 writes only to world DB (single-DB). Cross-DB backfill uses 3
independent read-only connections — no new ATTACH path.

---

---

## 3. Schema (Path D — v3 full outline)

Full SQL in `scripts/migrate_decision_events_create_2026_05_19.py` (`_CREATE_TABLE`,
`_CREATE_TRIGGER`, `_CREATE_INDICES`). Key structure:

```sql
PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
```

`condition_id` — nullable enrichment column (NOT in PK). `market_slug` is canonical.
`decision_event_id` — nullable on INSERT; writer computes via `decision_event_id_v1_hash()`;
trigger backstop fires on NULL setting sentinel (see §4.2.2 / Option β).

**CHECK constraints**: `temperature_metric IN ('high','low')` /
`polymarket_end_anchor_source IN ('gamma_explicit','f1_12z_fallback')` /
`schema_version IN (12,13)` / `source IN ('phase0_backfill','live_decision')`

**3 indices**: `(market_slug, target_date)` / `(strategy_key, decision_time)` /
`(decision_event_id)` (audit lookups)

### 3.1 Column source attribution

| Column | Source table | DB |
|---|---|---|
| `raw_orderbook_hash_transition_delta_ms` | `ensemble_snapshots_v2` | forecasts |
| `clock_skew_estimate_ms_at_submit` | `settlement_commands` | trade |
| `first_inclusion_block_time`, `finality_confirmed_time` | `wrap_unwrap_commands` | world |
| `provider_reported_time` | Path F Optional — None if WU API | — |
| `cycle_id`, `cycle_iteration` | cycle_runtime (live); NULL for backfill | — |
| probability fields (`p_posterior`, `edge`, etc.) | live-only; NULL for backfill | — |

### 3.2 decision_seq derivation

- **Backfill**: DELETE-by-source THEN monotonic seq per natural-key ordered by `decision_time` ASC, from 0
- **Live writes**: `SELECT COALESCE(MAX(decision_seq),-1)+1` under `db_writer_lock(LIVE)`, WHERE natural-key matches

### 3.3 NOT NULL classification (18 total — corrected from v2 §3.3)

**Natural-key** (4 + seq): `market_slug`, `temperature_metric`, `target_date`,
`observation_time`, `decision_seq`

**Identity/time** (5): `decision_time`, `outcome`, `side`, `strategy_key`, `observation_available_at`

**PR-3** (1): `polymarket_end_anchor_source`

**PR-6** (4): `first_member_observed_time`, `run_complete_time`, `zeus_submit_intent_time`, `venue_ack_time`

**Provenance** (2): `schema_version`, `source`

**Total NOT NULL: 18** (ultraplan §4.2 v3 corrected; `decision_event_id` effective-non-null post-INSERT via trigger)

---

## 4. New contract: DecisionNaturalKey

**File**: `src/contracts/decision_natural_key.py` (new)

```python
DecisionNaturalKey = NewType('DecisionNaturalKey', tuple)
# runtime: tuple[str, str, Literal['high','low'], str, str]
# positional: (market_id, condition_id, temperature_metric, target_date, observation_time)
```

Three helper stubs (`NotImplementedError`):
- `from_market_event_row(row)` — map market_events_v2 row → key
- `from_ensemble_snapshot_row(row)` — includes city→market_id Python-side resolution
- `from_artifact_json(j)` — robust to missing keys in historical artifact_json

---

## 5. Writer + reader signatures

```python
# src/state/decision_events.py

def write_decision_event(
    natural_key: DecisionNaturalKey,
    ctx: DecisionSourceContext,       # from src.contracts.execution_intent
    ekc: EffectiveKellyContext,       # from src.contracts.effective_kelly_context
    intent: ExecutionIntent,          # from src.contracts.execution_intent
    *,
    conn: sqlite3.Connection | None = None,
) -> None: ...

def read_decision_event_by_natural_key(key: DecisionNaturalKey, ...) -> list[DecisionEventRow]: ...
def read_decision_event_by_hash(decision_group_id: str, ...) -> list[DecisionEventRow]: ...
```

All three raise `NotImplementedError` (SCAFFOLD).  `read_decision_event_by_group`
from v1 removed cleanly.

---

## 6. Backfill plan (Path D — artifact_json primary)

**Primary source**: `decision_log.artifact_json` (world DB — same DB as
`decision_events`; no cross-DB read needed for core fields).

**Enrichment** (optional, independent reads keyed on natural fields):
- forecasts: `ensemble_snapshots_v2` → PR-6 timing fields
- trade: `settlement_commands` → PR-6 timing + `clock_skew_estimate_ms_at_submit`
- `city→(market_id, condition_id)` resolved Python-side via `market_events_v2`

**`INSERT OR IGNORE`** (NOT REPLACE) — does not overwrite existing live rows.

**Path F honesty**: historical PR-3/PR-6 fields → NULL.
`polymarket_end_anchor_source` defaults to `'gamma_explicit'` (Phase 0 critic B2).

Renamed file: `scripts/backfill_decision_events_from_artifact_json.py`
(was `backfill_decision_events_from_phase0_temp.py` — `git mv` preserves history)

---

## 7. Migration script outline

`scripts/migrate_decision_events_create_2026_05_19.py`:
- `_CREATE_TABLE` — full 30-column schema string
- `_CREATE_TRIGGER` — AFTER INSERT trigger populating `decision_group_id` via UDF
- `_CREATE_INDICES` — 3 index statements
- `main()` raises `NotImplementedError` (production pass fills body + UDF binding)

**Does NOT bump SCHEMA_VERSION** (production pass owns `src/state/db.py`)

---

## 8. Antibody (natural-key, no ATTACH)

**File**: `tests/test_inv_decision_events_completeness.py`

- `@pytest.mark.xfail(strict=True)` (table not yet created)
- Tests: `forecasts.ensemble_snapshots_v2` (7d window, `causality_status='OK'`)
  → city resolved to `(market_id, condition_id)` via `market_events_v2`
  → `world.decision_events` COUNT ≥ 1 per natural-key tuple
- **Independent read connections** (`get_forecasts_connection_read_only()` +
  `get_world_connection_read_only()`) — INV-37 trivially honored
- `pytest.skip` (not fail) if no candidates in window

---

## 9. Schema bump path (NOT applied — production pass)

`src/state/db.py`: `SCHEMA_VERSION = 12` → `SCHEMA_VERSION = 13`

Regenerate `tests/state/_schema_pinned_hash.txt` after bump.

Manifest entries (same commit as CREATE TABLE):
- `architecture/db_table_ownership.yaml` — `decision_events` after `decision_log` (line 481):
  `db: world, schema_class: world_class, pk_col: "[market_id, condition_id, temperature_metric, target_date, observation_time, decision_seq]"`
- `architecture/source_rationale.yaml` — companion entry (topology_doctor advisory)

---

## 10. PR sequencing

| PR | Title | Contents |
|---|---|---|
| **PR-T1-A** | foundation: DecisionNaturalKey + decision_events table | `src/contracts/decision_natural_key.py` + `src/state/decision_events.py` + migration script + `src/state/db.py` SCHEMA_VERSION bump + manifest entries |
| **PR-T1-B** | backfill + antibody | `scripts/backfill_decision_events_from_artifact_json.py` + `tests/test_inv_decision_events_completeness.py` (promote from xfail to strict-pass) |

PR-T1-B depends on PR-T1-A merged.

---

## 11. Ambiguities for wave-critic round 2

**#1 CRITICAL — hash UDF 4-arg mismatch**: `PHASE_1_ULTRAPLAN.md §4.2.2` says the
trigger calls `decision_group_id_v1(strategy_key, market_id, target_date, observation_time)`
(4 args).  But `src/contracts/decision_group_id.py:50` (`decision_group_id_v1_hash`)
takes **7 named kwargs** (`market_id, target_date, forecast_available_at, source_id,
data_version, bin_index, lead_days_bucket`) and has a completely different canonical
form.  These signatures are irreconcilable without a decision:
- (a) New wrapper mapping 4 trigger args → 7-kwarg hash with fixed defaults for
  `forecast_available_at, source_id, data_version, bin_index, lead_days_bucket`
- (b) The trigger uses a different, simpler hash function defined fresh for
  `decision_events` (not the calibration-pair `decision_group_id_v1_hash`)
- (c) Python UDF dropped; trigger left as `NULL` placeholder; hash computed by
  writer Python code and passed in directly (Option β from ultraplan)
**Migration script `_CREATE_TRIGGER` currently uses Option α stub with 4-arg call
— needs wave-critic decision before production pass.**

**#2 NOT NULL count discrepancy**: my recount = **18** NOT NULL columns; ultraplan
§4.2 states 17.  Recount: 6 natural-key + decision_time + outcome + side +
strategy_key + observation_available_at + polymarket_end_anchor_source +
first_member_observed_time + run_complete_time + zeus_submit_intent_time +
venue_ack_time + schema_version + source = 18.  Wave-critic should confirm
which column was omitted from the ultraplan count.

**#3 `get_world_connection_read_only` existence**: antibody uses
`get_world_connection_read_only()`.  Production pass must verify this function
exists in `src/state/db.py` or use the correct read-only equivalent.
