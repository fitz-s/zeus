# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.3 (Path D backfill — artifact_json primary, v3)
# SCAFFOLD: backfill pseudocode shell — production body pending T1 production pass

"""
Backfill decision_events from decision_log.artifact_json (primary source).

Path D v3 (natural-key reframe):
- PK: (market_slug, temperature_metric, target_date, observation_time, decision_seq)
- condition_id is nullable enrichment from market_events_v2 (NOT a PK component)
- artifact_json contains the 5 natural-key fields written at decision time

DELETE-by-source THEN INSERT semantic (per ultraplan §4.3, critic round-2 SEV-2):
  NOT INSERT OR IGNORE — IGNORE silently drops corrected rows on re-run after bug fix.
  DELETE scoped to source='phase0_backfill' — never touches source='live_decision' rows.

Cross-DB reads are INDEPENDENT (no new ATTACH path — INV-37 trivially honored):
  - world DB: decision_log (artifact_json + core fields) + decision_events (write target)
  - forecasts DB (independent read conn): ensemble_snapshots_v2 enrichment
  - trade DB (independent read conn): settlement_commands enrichment
  city→market_slug resolved Python-side via market_events_v2

Path F honesty: PR-3/PR-6 fields absent from historical artifact_json → NULL.
Exception: polymarket_end_anchor_source defaults to 'gamma_explicit' (Phase 0
critic B2 verdict — dominant historical case, retroactive labelling).

Backfill flow (per PHASE_1_ULTRAPLAN.md §4.3):
  for chunk in iter_decision_log_ids(world, chunk_size=500):
      1. world.execute: SELECT artifact_json + core fields WHERE id IN chunk
      2. for each row: json.loads(artifact_json) → from_artifact_json() → natural key
         skip on JSONDecodeError or None key
      3. Optional enrichment (independent reads, keyed on natural fields NOT condition_id):
         forecasts: ensemble_snapshots_v2 → first_member_observed_time, run_complete_time,
                    raw_orderbook_hash_transition_delta_ms
         trade: settlement_commands → zeus_submit_intent_time, venue_ack_time,
                clock_skew_estimate_ms_at_submit
         city→market_slug resolved via market_events_v2 (market_slug is canonical)
      4. db_writer_lock(world, BULK):
         for each parsed row:
             # DELETE-by-source (preserves live_decision rows at same natural key)
             DELETE FROM decision_events
              WHERE market_slug=? AND temperature_metric=?
                AND target_date=? AND observation_time=?
                AND source='phase0_backfill'
             # Compute seq based on what remains (typically 0 after DELETE)
             seq = SELECT COALESCE(MAX(decision_seq), -1)+1 WHERE natural-key
             # Compute writer-side hash (Option β — no UDF needed)
             deid = decision_event_id_v1_hash(
                market_slug=..., temperature_metric=...,
                target_date=..., observation_time=..., decision_seq=seq)
             INSERT INTO decision_events (...) VALUES (...)
             source='phase0_backfill', schema_version=12
         conn.commit()

Usage (production pass):
    python scripts/backfill_decision_events_from_artifact_json.py [--dry-run] [--limit N]
"""

from src.contracts.decision_natural_key import (  # noqa: F401 (production use)
    DecisionNaturalKey,
    decision_event_id_v1_hash,
    from_artifact_json,
    from_ensemble_snapshot_row,
)


def main() -> None:
    """SCAFFOLD — production body pending T1 production pass."""
    raise NotImplementedError("SCAFFOLD — pending T1 production")


if __name__ == "__main__":
    main()
