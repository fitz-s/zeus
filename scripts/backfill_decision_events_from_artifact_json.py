# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.3 (Path D backfill — artifact_json primary)
# SCAFFOLD: backfill pseudocode shell — production body pending T1 production pass

"""
Backfill decision_events from decision_log.artifact_json (primary source).

Path D (natural-key reframe): artifact_json contains the 5 natural-key fields
that were always present at write time.  No join on decision_group_id needed.

Cross-DB reads are INDEPENDENT (no new ATTACH path — INV-37 trivially honored):
  - world DB: decision_log (artifact_json + core fields)
  - forecasts DB (independent read conn): ensemble_snapshots_v2 enrichment
  - trade DB (independent read conn): settlement_commands enrichment
  city→(market_id, condition_id) resolved Python-side via market_events_v2

Write: single-DB to world.decision_events (INSERT OR IGNORE — idempotent, does
NOT overwrite existing live rows).

Path F honesty: PR-3/PR-6 fields absent from historical artifact_json → NULL.
Exception: polymarket_end_anchor_source defaults to 'gamma_explicit' (Phase 0
critic B2 verdict — dominant historical case, retroactive labelling).

Backfill flow (per PHASE_1_ULTRAPLAN.md §4.3):
  for chunk in iter_decision_log_ids(world, chunk_size=500):
      1. world.execute: SELECT artifact_json + core fields WHERE id IN chunk
      2. for each row: json.loads(artifact_json) → from_artifact_json() → natural key
         skip on JSONDecodeError or None key
      3. Optional enrichment (independent reads, keyed on natural fields):
         forecasts: ensemble_snapshots_v2 → first_member_observed_time, run_complete_time,
                    raw_orderbook_hash_transition_delta_ms
         trade: settlement_commands → zeus_submit_intent_time, venue_ack_time,
                clock_skew_estimate_ms_at_submit
         city→(market_id, condition_id) resolved via market_events_v2 Python-side
      4. db_writer_lock(world, BULK) + SAVEPOINT:
         for each parsed row:
             seq = MAX(decision_seq)+1 WHERE natural-key matches
             INSERT OR IGNORE INTO decision_events ... (NOT REPLACE)
             source='phase0_backfill', schema_version=12
         COMMIT

Usage (production pass):
    python scripts/backfill_decision_events_from_artifact_json.py [--dry-run]
"""

from src.contracts.decision_natural_key import (  # noqa: F401 (production use)
    DecisionNaturalKey,
    from_artifact_json,
    from_ensemble_snapshot_row,
)


def main() -> None:
    """SCAFFOLD — production body pending T1 production pass."""
    raise NotImplementedError("SCAFFOLD — pending T1 production")


if __name__ == "__main__":
    main()
