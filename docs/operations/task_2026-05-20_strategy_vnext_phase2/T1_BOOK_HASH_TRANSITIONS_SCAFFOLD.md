# T1 book_hash_transitions — SCAFFOLD Design Doc

**Created**: 2026-05-20 by executor (sonnet)
**Revised**: 2026-05-20 critic round-1 fixes (SEV-1 writer signature, SEV-1 market_slug, SEV-2 line-anchor scrub)
**Authority**: `PHASE_2_ULTRAPLAN.md` v3.1 §4 (branch `docs/phase2-ultraplan-20260520`, sha `00c2399742`)
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

**Grep-verify results (AUTHOR-side G1/G2/G3, type-3 line-anchor check — critic round-1 scrub)**:

| Check | Path:Line | `sed -n '<line>p'` result | Status |
|---|---|---|---|
| G1-a | `v2_schema.py:406` | `raw_orderbook_hash TEXT,` | PASS |
| G1-b | `v2_schema.py:425` | `ALTER TABLE market_price_history ADD COLUMN raw_orderbook_hash TEXT` | PASS |
| G1-c | `v2_schema.py:211` | `ALTER TABLE ensemble_snapshots_v2 ADD COLUMN raw_orderbook_hash_transition_delta_ms INTEGER` | PASS |
| G2-a | `market_scanner.py:2129` | `raw_orderbook_hash=_sha256_json(raw_orderbook),` (snapshot ctor arg) | PASS |
| G2-b | `market_scanner.py:2159` | `raw_orderbook_hash=_sha256_json(raw_orderbook),` (snapshot attr assign) | PASS |
| G2-c | `market_scanner.py:2169` | `insert_snapshot(conn, snapshot)` | PASS |
| G3-a | `market_scanner.py:2170` | `# PR 6 (2026-05-19): compute raw_orderbook_hash transition delta.` | PASS |
| G3-b | `market_scanner.py:2171` | `_current_hash = snapshot.raw_orderbook_hash` | PASS |
| G3-c | `market_scanner.py:2133` | `event_slug=str(market.get("slug") or ""),` (market_slug source) | PASS |
| G3-d | `db.py:4492` | `market_slug = _forward_clean_str(row["event_slug"])` (confirms market_slug = event_slug) | PASS |

**Checklist: 10/10 PASS**

**Type-3 drift caught**: brief cited 2572/2602/2613-2628; verified actuals are 2129/2159/2170-2186.
Per ULTRAPLAN v3 §14.1 (G9). All secondary citations in this doc use verified actual lines.

---

## §2 Surface (4 artifacts this SCAFFOLD pass)

| Artifact | Path | Type | Status |
|---|---|---|---|
| Writer + reader skeleton | `src/state/book_hash_transitions.py` (new) | `write_transition(market_slug, prev_hash, new_hash, observed_at, delta_ms, cycle_id=None, *, conn)` + `read_transitions_by_market(market_slug, since, *, conn=None)` both `raise NotImplementedError` | SCAFFOLD |
| CREATE TABLE DDL | `src/state/schema/book_hash_transitions_schema.py` (new) | Full 8-column DDL per §4.3 + `ensure_table(conn)` | SCAFFOLD |
| xfail antibody | `tests/test_inv_book_hash_transitions_completeness.py` (new) | `@pytest.mark.xfail(reason="T1 SCAFFOLD — production pass implements")` | SCAFFOLD |
| Manifest stubs | `architecture/db_table_ownership.yaml` + `architecture/source_rationale.yaml` | Entries added | SCAFFOLD |

**NOT in SCAFFOLD** (production pass):
- `src/state/db.py` mutation (NO SCHEMA_VERSION bump, NO wiring of `ensure_table`)
- Producer wiring in `src/data/market_scanner.py`
- Migration script `scripts/migrate_book_hash_transitions_create_2026_05_21.py`

---

## §3 Producer wiring plan (production pass)

**Location**: `src/data/market_scanner.py` — function `capture_executable_market_snapshot`
(defined at line 2012), PR 6 delta block at lines **2170-2186** (verified).

The block at lines 2170-2186 already:
1. `insert_snapshot(conn, snapshot)` at line **2169** — conn is the world connection
2. `_current_hash = snapshot.raw_orderbook_hash` at line **2171**
3. `_now_ts = time.time()` at line **2172** — timestamp for observed_at
4. `_prior = _prev_orderbook_hash_by_market.get(condition_id)` at line **2174**
5. `if _current_hash != _prior_hash:` at line **2177**
6. `_hash_delta_ms = int((_now_ts - _prior_ts) * 1000)` at line **2178**

**market_slug resolution**: at the PR 6 delta block, `market_slug` is NOT a local
variable. The correct value is `snapshot.event_slug` (set at line **2133** as
`event_slug=str(market.get("slug") or "")`). This is confirmed by `db.py:4492`:
`market_slug = _forward_clean_str(row["event_slug"])` — the `market_price_history`
writer maps `market_slug ← snapshot.event_slug`. Do NOT use `condition_id`; it is a
distinct column in the schema.

**T1 hook (production pass)**: immediately after line 2178 (`_hash_delta_ms = ...`),
inside the `if _current_hash != _prior_hash:` block:

```python
write_transition(
    market_slug=snapshot.event_slug,      # NOT condition_id
    prev_hash=_prior_hash,
    new_hash=_current_hash,
    observed_at=datetime.fromtimestamp(_now_ts, tz=timezone.utc).isoformat(),
    delta_ms=int((_now_ts - _prior_ts) * 1000),
    cycle_id=None,                        # production pass: pass live cycle_id if in scope
    conn=conn,                            # world conn from insert_snapshot call at line 2169
)
```

**observed_at**: use `datetime.fromtimestamp(_now_ts, tz=timezone.utc).isoformat()`
from the already-captured `_now_ts` (line 2172). Do NOT use `datetime.utcnow()` —
that creates write-lag drift by re-stamping after the hash comparison.

**transition_seq atomicity**: production pass uses SAVEPOINT under the caller-provided
`conn` + `SELECT MAX(transition_seq) + 1 WHERE market_slug = ? AND observed_at = ?`.
No `SELECT FOR UPDATE` (SQLite does not support it); SAVEPOINT is the SQLite-native
mechanism for atomic read-modify-write under a single connection.

No new connection opens at the write site (INV-37: single-conn path; `conn` is the
world connection already passed through `insert_snapshot(conn, snapshot)` at line 2169).

Snapshot construction with `raw_orderbook_hash` occurs at lines **2129** (snapshot ctor
`_snapshot_id()` arg) and **2159** (snapshot attribute assignment). Both feed
`snapshot.raw_orderbook_hash` read at line **2171** — no change to construction sites
required.

---

## §4 Production-pass checklist

1. **SCHEMA_VERSION 13 → 14**: increment in `src/state/db.py` (world DB version constant). Verify current value is 13 before incrementing.
2. **Wire `ensure_table(conn)`**: call `ensure_table(world_conn)` from `init_schema(conn)` in `src/state/db.py` world DB init path. Import from `src.state.schema.book_hash_transitions_schema`.
3. **Fill `write_transition` body**: INSERT with all 8 columns; `transition_seq` derived atomically (MAX + 1 under caller-provided conn or separate SELECT FOR UPDATE equivalent).
4. **Fill `read_transitions_by_market` body**: SELECT ordered by `(observed_at, transition_seq) ASC`; `conn=None` path opens `get_world_connection_read_only()`.
5. **Wire producer**: add `write_transition` call inside the `if _current_hash != _prior_hash:` block at lines **2170-2186** (see §3 for full sample call with correct `market_slug=snapshot.event_slug`, `observed_at` from `_now_ts`, and `delta_ms`).
6. **Migration script**: `scripts/migrate_book_hash_transitions_create_2026_05_21.py` — idempotent CREATE TABLE + indexes, targets ZEUS_WORLD_DB_PATH.
7. **Remove xfail**: activate antibody test in `tests/test_inv_book_hash_transitions_completeness.py` (remove `@pytest.mark.xfail` decorator after production pass verifies invariant holds).
8. **Update manifests**: `architecture/db_table_ownership.yaml` entry — update `created_by` from `migration_script_...` to `init_schema` after wiring, update `notes` to remove SCAFFOLD caveat.
9. **Regression suite**: run `python -m pytest tests/` and verify no regressions before PR open.
10. **Batch with W2** (T2 merge constraint): T1 must be merged before T2 per ULTRAPLAN §1 wave order (W1 → W2; SCHEMA_VERSION sequencing).
11. **Antibody tightening (Phase-3 carryover)**: current `expected = distinct_count - 1` may undercount for A→B→A→B sequences where the same prev/new pair repeats. Production pass should replace with a row-pair join on `market_price_history` ordered by `recorded_at`, verifying each consecutive distinct `(prev_hash, new_hash)` pair has a corresponding `book_hash_transitions` row. Alternatively, count transitions per market-slug in the 24h window and assert `>= distinct_count - 1` (current logic) as a floor rather than exact match. Tightening deferred to Phase 3 per critic round-1 recommendation.
