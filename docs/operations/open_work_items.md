# Open Work Items (updated 2026-05-01)

---

## Two-System Independence

- [ ] Phase 4 revisit: due ~2026-06-26 (8 weeks from Phase 3 completion 2026-05-01) —
  divergent strategy restart-policy needs → escalate to architect for process split

---

## Backfill Orchestration

- [ ] 18 `scripts/backfill_*.py` scripts remain ad-hoc; scope a unification package
  when convenient (low priority, no live trading impact)

---

## Closed this session (2026-05-01)

- CLOB V2 cutover: posture NORMAL, Zeus live — done
- Q3 Polymarket ingestion: `market_scanner.py` now persists to `market_events_v2`
  on every scan cycle via `_persist_market_events_to_db()` — no separate ingest
  source needed; Gamma API is the local data pull
- Q5 empty-provenance observations: all 16 NULL `provenance_metadata` rows already
  `authority=QUARANTINED` from prior cleanup
- LOW-track settlements writer: wired with market_scanner persistence above
- Seoul 2026-04-15 drift: already `QUARANTINED` with `obs_outside_winning_bin` —
  correctly handled, nothing further needed
- Q4 quarantine routing: JSONL at `state/quarantine/` (dir created 2026-05-01)
- Q3 schema manifest versioning: soft-warn pre-commit is sufficient, no hard
  enforcement needed
- Q5 TIGGE read-only: `ensemble_client` imports in trading path are read-only
  `fetch/validate` calls — confirmed acceptable
- Weighted Platt RFC: operator deferred indefinitely
- Two-System Q3/Q4/Q5: resolved above
