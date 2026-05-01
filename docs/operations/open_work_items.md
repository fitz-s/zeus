# Open Work Items (verified 2026-05-01)

Audited against main HEAD by scientist subagent 2026-05-01.
Removed: R3 U2 (done), V2 cutover gate flags (never existed / posture now NORMAL),
F11 availability_provenance (column renamed — issue not reproducible),
LOW QUARANTINED reactivation (settlements table restructured, no status column).

---

## CLOB V2 Migration

- [ ] Live V2 cutover: runtime_posture is NORMAL; remaining gates are operator-paced
  (heartbeat gate + collateral accounting). The gate flags were never added to
  config/settings.json — confirm with operator whether cutover is complete or still
  requires a formal evidence packet.

---

## Backtest First-Principles Review

- [ ] Operator decision Q3: Polymarket data ingestion source (subgraph / websocket /
  both) — `market_events_v2` table exists but has 0 rows; scanner has not populated
  it. Gates economics-grade backtest; `economics.py` tombstone refuses to run until
  `market_events_v2 > 0`
- [ ] Operator decision Q5: empty-provenance WU observations — `observations` table
  has no `provenance` column (renamed to `provenance_metadata`); 16 rows carry
  NULL/empty `provenance_metadata`. Quarantine-all vs. partial oracle_shadow backfill
  vs. log-replay; gates training readiness
- [ ] LOW-track settlements writer for `market_events_v2`: `market_events_v2` = 0
  rows; `src/data/market_scanner.py` in-memory cache still does not write to it

---

## Settlements LOW Backfill

- [ ] Investigate Seoul 2026-04-15 1°C drift — settlements rows present
  (high=21.0, low=9.0) but no `observation_value` column exists to cross-check
  directly; requires fresh obs query

---

## Weighted Platt Precision Weight RFC

DRAFT RFC only — no implementation started. All 3 operator decisions pending.

- [ ] Operator decision 1: approve or reject structural change (replace
  `training_allowed: bool` with `precision_weight: float`)
- [ ] Operator decision 2: approve weight function family (`D_softfloor`
  recommended per PoC v4 evidence)
- [ ] Operator decision 3: approve schema migration window (5 phases over ~3 weeks)

---

## Two-System Independence

Phases 1–3 done and in main (Phase 3 completed 2026-05-01). Phase 4 explicitly deferred.

- [ ] Phase 4 revisit trigger: 8-week window from Phase 3 completion opens now;
  revisit due ~2026-06-26 — divergent strategy restart-policy needs → escalate to
  architect for process split
- [ ] Q3 (schema manifest versioning): pre-commit hook is soft-warn only; does not
  enforce schema manifest versioning — operator decision pending (pre-commit hook vs.
  manual bump)
- [ ] Q4 (quarantine routing destination): neither `data_quarantine` table nor
  `state/quarantine/` directory exists — operator decision pending
- [ ] Q5 (TIGGE / ECMWF fast-path): `ensemble_client` is imported by trading-path
  files (`cycle_runner`, `monitor_refresh`, `evaluator`) for read-only
  `fetch_ensemble` / `validate_ensemble` calls — confirm this is acceptable or
  requires formal read-only attestation
- [ ] Backfill orchestration unification: 18 `scripts/backfill_*.py` scripts present
  (was 16 at last count); scope a follow-up package
