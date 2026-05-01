# Open Questions for Operator — Two-System Independence Design (v2)

**Status:** v2 after critic-opus APPROVE-WITH-CONDITIONS review. Q1, Q2, Q6 RESOLVED in this revision per critic recommendations + operator authorization to converge ("如果 converge 到一个点就不需要我决定了"). Q3, Q4, Q5, Q7 remain operator-decidable with architect's defaults.
**Companion to:** [`design.md`](design.md) (v2)
**Drafted:** 2026-04-30 (v1) → revised 2026-04-30 (v2)

---

## Q1. Asymmetric restart policy — manual recovery for trading? — **RESOLVED**

**Decision (v2):** YES — manual recovery for trading.
- `com.zeus.live-trading.plist` KeepAlive=`false`.
- External watchdog reads `state/auto_restart_allowed.flag`. Operator manually flips after positions close.
- `com.zeus.data-ingest.plist` KeepAlive=`true` (asymmetric).
- Revisit at Phase 3 once recovery semantics are codified.

**Authority basis:** critic-opus final operator recommendation §"Final recommendation to operator" Q1; architect's original recommendation matched.

**Implementation:** §4.3 of design.md.

---

## Q2. Calibration ownership — **RESOLVED**

**Decision (v2):** ingest-owned.
- Move `scripts/refit_platt_v2.py`, `rebuild_calibration_pairs_canonical.py`, `etl_tigge_direct_calibration.py` into `scripts/ingest/calibration/` in Phase 3.
- Trading still imports `src.calibration.{platt, store, manager, metric_specs}` for read-only consumption.
- Antibody #10 (`tests/test_calibration_consumer_lane.py`) enforces.
- Third-lane deferred to Phase 4 if calibration cadence diverges from ingest cadence.

**Authority basis:** critic-opus final operator recommendation Q2; architect's original recommendation matched.

**Implementation:** §1.2 + §2 row 10 of design.md.

---

## Q3. Schema manifest versioning — who bumps?

**Question:** Schema manifest version (`architecture/world_schema_version.yaml`): does the ingest commit author bump manually, or is there a CI hook?

**Architect's recommendation (default):** pre-commit hook that diffs `init_schema` AST and refuses to commit without manifest bump.

**Trade-off:**
- Manual: error-prone (the whole point of versioning is to catch drift).
- CI hook: adds infra; needs careful AST comparison.

**Decision:** [pending operator] — Phase 2 blocker.

---

## Q4. Quarantine routing destination

**Question:** Quarantined ingest payloads — `data_quarantine` table in `zeus-world.db`, or a JSONL file outside the DB?

**Architect's recommendation (default):** JSONL file at `state/quarantine/<source>/<date>.jsonl` for Phase 2 (write-only, never read by ingest); a quarantine review tool reads them.

**Trade-off:**
- Table: SQL queryable; but if quarantined data IS the cause of DB exception, we may not be able to write it.
- JSONL: safer, but no SQL surface; review tool must be built.

**Decision:** [pending operator] — Phase 2 blocker.

---

## Q5. TIGGE / ECMWF Open Data — fast-path read for trading?

**Question:** Are TIGGE / ECMWF Open Data strictly ingest-owned, or do they need a parallel "fast path" that trading reads directly for nowcasts?

**Architect's recommendation (default):** confirm `ensemble_client.py` stays read-only and only reads `ensemble_snapshots` written by ingest. No fast-path.

**Today:** `ensemble_client.py` is read-only from trading. Confirm no exception exists.

**Decision:** [pending operator] — confirmation, not policy. Phase 2 blocker.

---

## Q6. Harvester ownership — KEY DECISION — **RESOLVED**

**Decision (v2):** Phase-1.5 split (NOT Phase 4).
- `src/ingest/harvester_truth_writer.py` — owns world.settlements writes; runs on ingest scheduler hourly. Wraps `_write_settlement_truth` body. Feature flag preserved.
- `src/execution/harvester_pnl_resolver.py` — what remains of the legacy harvester; reads world.settlements and writes trade.decision_log + settles positions; runs on trading scheduler hourly.
- `src.contracts.world_writer.WorldSettlementWriter` — audited wrapper for the world-side write.
- Antibody #12 (`tests/test_harvester_split_independence.py`) enforces independence.

**Why not "stay in trading until Phase 4":** critic ATTACK 10 made the case — if Phase 4 is deferred, the original 12-day-gap shape recurs for `world.settlements` whenever trading is unloaded. Settlement continuity is a first-class data continuity concern, parallel to the original TIGGE-gap motivation.

**Authority basis:** critic-opus final operator recommendation Q6; operator authorization to converge with critic.

**Implementation:** §5 Phase 1.5 of design.md.

---

## Q7. Backfill operation atomicity

**Question:** Should `python -m scripts.ingest.backfill --since X` be safe to run while the ingest daemon is also running?

**Architect's recommendation (default):** backfill acquires advisory lock at `state/locks/backfill_<table>.lock`; ingest tick checks lock and skips overlapping work for the same table.

**Trade-off:**
- WAL handles concurrent writes at the DB level.
- Logical race: two writers on the same MISSING row in `data_coverage`.
- Lock approach: simple file lock; standard ops pattern. **Already adopted as pattern for HBL-3 dual-run mechanism (§5 Phase 1) — this is the same primitive.**

**Decision:** [pending operator] — non-blocking; default is conservative and consistent with HBL-3.

---

## Decision Block-Map (v2)

- Phase 1 BLOCKED on: **NONE** (Q1, Q2, Q6 RESOLVED; Q7 has a conservative default).
- Phase 1.5 BLOCKED on: **NONE** (Q6 RESOLVED).
- Phase 2 BLOCKED on: Q3 (schema versioning workflow), Q4 (quarantine destination), Q5 (TIGGE fast-path confirmation).
- Phase 3 BLOCKED on: revisit of Q1 (final restart policy after Phase 2 review).
- Phase 4 BLOCKED on: revisit of Q2 (third-lane calibration if cadence diverges); §3.0 row 3 strategy-as-process explicit revisit trigger.

Operator may now greenlight Phase 1 + Phase 1.5 work without further decision; the four remaining questions surface during Phase 2 planning.

