# T1 book_hash_transitions — SCAFFOLD Design Doc

**Created**: 2026-05-20 by executor (sonnet)
**Authority**: `PHASE_2_ULTRAPLAN.md` v3 §4 (branch `docs/phase2-ultraplan-20260520`, sha `99875d4781`)
**Status**: SCAFFOLD — wave-critic reviews before production pass

---

## §1 Problem (per ULTRAPLAN v3 §4.1, grep-verified)

`raw_orderbook_hash` is materialized on `market_price_history.raw_orderbook_hash`
(world DB; `v2_schema.py:406` column definition, `v2_schema.py:425` ALTER).

The delta column `raw_orderbook_hash_transition_delta_ms` was added by Phase 0
PR 6 to `ensemble_snapshots_v2` (forecasts DB; `v2_schema.py:211`) capturing
inter-snapshot cadence. The **actual hash transitions** (prev_hash → new_hash
with timestamp) are NOT persisted as discrete events — only the per-snapshot
delta survives.

Phase 2 T1 lands the transition log so post-hoc analytics can reconstruct
microstructure events.

**Grep-verify results (AUTHOR-side G1 + G9, type-3 line-anchor check)**:

| Check | Claim | Grep result | Status |
|---|---|---|---|
| G1-a | `raw_orderbook_hash TEXT` at `v2_schema.py:406` | CONFIRMED | PASS |
| G1-b | `ALTER TABLE market_price_history ADD COLUMN raw_orderbook_hash TEXT` at `v2_schema.py:425` | CONFIRMED | PASS |
| G1-c | `ALTER TABLE ensemble_snapshots_v2 ADD COLUMN raw_orderbook_hash_transition_delta_ms INTEGER` at `v2_schema.py:211` | CONFIRMED | PASS |
| G9-a | brief cited `market_scanner.py:2572` for `raw_orderbook_hash=_sha256_json(raw_orderbook)` | VERIFIED ACTUAL: line **2129** (snapshot ctor call 1) + **2159** (snapshot ctor call 2) | TYPE-3 DRIFT CAUGHT |
| G9-b | brief cited `market_scanner.py:2613-2628` for PR 6 delta block | VERIFIED ACTUAL: lines **2170-2186** (`_current_hash = snapshot.raw_orderbook_hash` at **2171**) | TYPE-3 DRIFT CAUGHT |

**Note**: brief cited 2572/2602/2613-2628; verified actuals are 2129/2159/2170-2186.
This is exactly the type-3 line-anchor rot described in ULTRAPLAN v3 §14.1 (G9).
SCAFFOLD doc uses verified actual lines throughout.

---

## §2 Surface (4 artifacts this SCAFFOLD pass)

| Artifact | Path | Type | Status |
|---|---|---|---|
| Writer + reader skeleton | `src/state/book_hash_transitions.py` (new) | `write_transition(...)` + `read_transitions_by_market(...)` both `raise NotImplementedError` | SCAFFOLD |
| CREATE TABLE DDL | `src/state/schema/book_hash_transitions_schema.py` (new) | Full 8-column DDL per §4.3 + `ensure_table(conn)` | SCAFFOLD |
| xfail antibody | `tests/test_inv_book_hash_transitions_completeness.py` (new) | `@pytest.mark.xfail(reason="T1 SCAFFOLD — production pass implements")` | SCAFFOLD |
| Manifest stubs | `architecture/db_table_ownership.yaml` + `architecture/source_rationale.yaml` | Entries added | SCAFFOLD |

**NOT in SCAFFOLD** (production pass):
- `src/state/db.py` mutation (NO SCHEMA_VERSION bump, NO wiring of `ensure_table`)
- Producer wiring in `src/data/market_scanner.py`
- Migration script `scripts/migrate_book_hash_transitions_create_2026_05_21.py`

---

## §3 Producer wiring plan (production pass)

**Location**: `src/data/market_scanner.py` PR 6 delta block, lines **2170-2186** (verified).

The block at lines 2170-2186 already:
1. Computes `_current_hash = snapshot.raw_orderbook_hash` (line 2171)
2. Looks up `_prior = _prev_orderbook_hash_by_market.get(condition_id)` (line 2174)
3. Compares `_current_hash != _prior_hash` (line 2177)
4. Computes `_hash_delta_ms` (line 2178)

**T1 hook (production pass)**: when `_current_hash != _prior_hash`, call:

```python
write_transition(
    market_slug=condition_id,   # condition_id serves as market_slug key here
    prev_hash=_prior_hash,
    new_hash=_current_hash,
    observed_at=datetime.utcnow().isoformat(),
    conn=world_conn,            # world connection passed through from caller
)
```

The world connection must be threaded through from the existing `insert_snapshot(conn, snapshot)` call above line 2484. No new connection opens at the write site (INV-37: single-conn path).

Snapshot construction with `raw_orderbook_hash` occurs at lines 2443 (inner path) and 2473 (outer path). Both feed `snapshot.raw_orderbook_hash` which the PR 6 delta block reads at line 2485 — no change to construction sites required.

---

## §4 Production-pass checklist

1. **SCHEMA_VERSION 13 → 14**: increment in `src/state/db.py` (world DB version constant). Verify current value is 13 before incrementing.
2. **Wire `ensure_table(conn)`**: call `ensure_table(world_conn)` from `init_schema(conn)` in `src/state/db.py` world DB init path. Import from `src.state.schema.book_hash_transitions_schema`.
3. **Fill `write_transition` body**: INSERT with all 8 columns; `transition_seq` derived atomically (MAX + 1 under caller-provided conn or separate SELECT FOR UPDATE equivalent).
4. **Fill `read_transitions_by_market` body**: SELECT ordered by `(observed_at, transition_seq) ASC`; `conn=None` path opens `get_world_connection_read_only()`.
5. **Wire producer**: add `write_transition` call at lines 2484-2499 delta block (see §3).
6. **Migration script**: `scripts/migrate_book_hash_transitions_create_2026_05_21.py` — idempotent CREATE TABLE + indexes, targets ZEUS_WORLD_DB_PATH.
7. **Remove xfail**: activate antibody test in `tests/test_inv_book_hash_transitions_completeness.py` (remove `@pytest.mark.xfail` decorator after production pass verifies invariant holds).
8. **Update manifests**: `architecture/db_table_ownership.yaml` entry — update `created_by` from `migration_script_...` to `init_schema` after wiring, update `notes` to remove SCAFFOLD caveat.
9. **Regression suite**: run `python -m pytest tests/` and verify no regressions before PR open.
10. **Batch with W2** (T2 merge constraint): T1 must be merged before T2 per ULTRAPLAN §1 wave order (W1 → W2; SCHEMA_VERSION sequencing).
