# Open Work Items (consolidated 2026-05-01)

Source: distilled from task_2026-04-26 through task_2026-04-30 dirs (archived to _archive/).

---

## CLOB V2 Migration

R3 slices Z0–Z4 and U1 are DONE and in main. U2 is unfrozen and ready to start.

- [ ] R3 U2: start next slice (venue command repo / related) — unfrozen, awaiting next phase boot
- [ ] Live V2 cutover: operator-authorized; requires heartbeat gate + collateral gate + pUSD/FX classification

---

## Backtest First-Principles Review

Planning artifacts done. Implementation slices S1, S2, S4, F11.1–F11.6 landed. `economics.py` tombstoned pending data decisions.

- [ ] Operator decision Q3: Polymarket data ingestion source (subgraph / websocket / both) — gates economics-grade backtest; `economics.py` tombstone refuses to run until `market_events_v2 > 0`
- [ ] Operator decision Q5: empty-provenance WU observations (39,431 rows) — quarantine-all vs. partial oracle_shadow backfill vs. log-replay; gates training readiness
- [ ] LOW-track settlements writer for `market_events_v2`: build parallel writer now or defer until V2 cutover

---

## F11 Forecast Issue Time

All F11 slices done and in main. 23,466 rows backfilled.

- [ ] 1,683 recently-ingested rows still carry `availability_provenance = NULL` — written after the backfill window; next cron tick populates new rows; targeted cleanup script may be needed for the gap

---

## Settlements LOW Backfill

48 LOW rows inserted (4 VERIFIED + 44 QUARANTINED). In main.

- [ ] Daily obs catch-up: 43 QUARANTINED rows can be reactivated once `observations.low_temp` populates 2026-04-20 onward (ingest lag ~9 days as of audit)
- [ ] Live LOW market scanner persistence: `src/data/market_scanner.py` in-memory cache should write to `market_events_v2`; currently 0 rows
- [ ] Investigate Seoul 2026-04-15 1°C drift (obs=9°C, market settled 10°C)

---

## Weighted Platt Precision Weight RFC

DRAFT RFC only — no implementation started. Pending operator decisions.

- [ ] Operator decision 1: approve or reject structural change (replace `training_allowed: bool` with `precision_weight: float`)
- [ ] Operator decision 2: approve weight function family (`D_softfloor` recommended per PoC v4 evidence)
- [ ] Operator decision 3: approve schema migration window (5 phases over ~3 weeks)

---

## Two-System Independence

Phases 1–3 done and in main. Phase 4 explicitly deferred.

- [ ] Phase 4 revisit trigger (within 8 weeks of Phase 3 completion): divergent strategy restart-policy needs → escalate to architect for process split
- [ ] Q3 (schema manifest versioning): pre-commit hook vs. manual bump — operator decision pending
- [ ] Q4 (quarantine routing destination): `data_quarantine` table vs. JSONL at `state/quarantine/` — operator decision pending
- [ ] Q5 (TIGGE / ECMWF fast-path): confirm `ensemble_client.py` remains read-only from trading
- [ ] Backfill orchestration unification: 16 ad-hoc `scripts/backfill_*.py` scripts remain; scope a follow-up package
