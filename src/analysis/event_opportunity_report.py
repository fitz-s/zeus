"""EDLI event opportunity report."""

from __future__ import annotations

import sqlite3


def build_event_opportunity_report(conn: sqlite3.Connection) -> dict[str, object]:
    event_counts = dict(
        conn.execute(
            "SELECT event_type, COUNT(*) FROM opportunity_events GROUP BY event_type"
        ).fetchall()
    )
    processing_counts = dict(
        conn.execute(
            "SELECT processing_status, COUNT(*) FROM opportunity_event_processing GROUP BY processing_status"
        ).fetchall()
    )
    regret_by_stage = dict(
        conn.execute(
            "SELECT rejection_stage, COUNT(*) FROM no_trade_regret_events GROUP BY rejection_stage"
        ).fetchall()
    )
    accepted_no_submit = conn.execute(
        """
        WITH accepted AS (
            SELECT receipt.event_id, receipt.final_intent_id
            FROM edli_no_submit_receipts AS receipt
            JOIN decision_certificates AS cert
              ON cert.certificate_type = 'NoSubmitDecisionCertificate'
             AND cert.semantic_key = 'no_submit:' || receipt.event_id || ':' || receipt.final_intent_id
             AND cert.verifier_status = 'VERIFIED'
             AND receipt.final_intent_id = json_extract(cert.payload_json, '$.final_intent_id')
             AND receipt.side_effect_status = json_extract(cert.payload_json, '$.side_effect_status')
             AND receipt.executable_snapshot_id = json_extract(cert.payload_json, '$.executable_snapshot_id')
             AND receipt.projection_hash = json_extract(cert.payload_json, '$.projection_hash')
             AND json_extract(cert.payload_json, '$.proof_accepted') = 1
             AND json_extract(cert.payload_json, '$.submitted') = 0
             AND NOT EXISTS (
                 SELECT 1
                 FROM decision_certificate_supersessions AS supersession
                 WHERE supersession.old_certificate_hash = cert.certificate_hash
             )
            WHERE receipt.side_effect_status = 'NO_SUBMIT'
        )
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT event_id || '|' || final_intent_id) AS decisions
        FROM accepted
        """
    ).fetchone()
    feasibility_count = conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_evidence"
    ).fetchone()[0]
    event_available_after_decision = _event_time_violation_counts(conn, "available_at")
    event_received_after_decision = _event_time_violation_counts(conn, "received_at")
    violations = {
        "event_available_after_decision": event_available_after_decision["events"],
        "event_available_after_decision_rows": event_available_after_decision["rows"],
        "event_received_after_decision": event_received_after_decision["events"],
        "event_received_after_decision_rows": event_received_after_decision["rows"],
        "available_at_violations": event_available_after_decision["events"],
        "parent_source_available_after_decision": _certificate_time_violation_count(conn, "source_available_at"),
        "parent_agent_received_after_decision": _certificate_time_violation_count(conn, "agent_received_at"),
        "parent_persisted_after_decision": _certificate_time_violation_count(conn, "persisted_at"),
        "direct_market_channel_stale_trades": _payload_violation_count(
            conn,
            "certificate_type IN ('ActionableTradeCertificate','ExecutionCommandCertificate')"
            " AND COALESCE(json_extract(payload_json, '$.fill_source'), json_extract(payload_json, '$.evidence_source'), '') = 'market_channel'",
        ),
        "midpoint_cost_uses": _payload_violation_count(
            conn,
            "certificate_type = 'CostModelCertificate'"
            " AND lower(COALESCE(json_extract(payload_json, '$.cost_source'), json_extract(payload_json, '$.execution_price_type'), '')) LIKE '%midpoint%'",
        ),
        "no_complement_cost_uses": _payload_violation_count(
            conn,
            "certificate_type IN ('QuoteFeasibilityCertificate','CostModelCertificate')"
            " AND lower(COALESCE(json_extract(payload_json, '$.cost_source'), json_extract(payload_json, '$.execution_price_type'), '')) LIKE '%complement%'",
        ),
        "last_trade_cost_uses": _payload_violation_count(
            conn,
            "certificate_type IN ('QuoteFeasibilityCertificate','CostModelCertificate')"
            " AND lower(COALESCE(json_extract(payload_json, '$.cost_source'), json_extract(payload_json, '$.quote_source_kind'), '')) LIKE '%last_trade%'",
        ),
        "cost_source_missing": _payload_violation_count(
            conn,
            "certificate_type = 'CostModelCertificate'"
            " AND COALESCE(json_extract(payload_json, '$.cost_source'), '') = ''",
        ),
    }
    return {
        "events_by_type": event_counts,
        "processing_by_status": processing_counts,
        "blocked_by_stage": regret_by_stage,
        "accepted_no_submit_receipts": int(accepted_no_submit[1] or 0),
        "accepted_no_submit_receipt_rows": int(accepted_no_submit[0] or 0),
        "accepted_no_submit_distinct_decisions": int(accepted_no_submit[1] or 0),
        "execution_feasibility_rows": feasibility_count,
        "certificate_time_semantics": _generated_decision_time_semantics(conn),
        "violations": violations,
    }


def _event_time_violation_counts(conn: sqlite3.Connection, event_time_column: str) -> dict[str, int]:
    if event_time_column not in {"available_at", "received_at"}:
        raise ValueError(f"unsupported event time column: {event_time_column}")
    row = conn.execute(
        f"""
        WITH decision_surface AS (
            SELECT receipt.event_id, receipt.decision_time, 'receipt' AS surface
            FROM edli_no_submit_receipts AS receipt
            WHERE receipt.side_effect_status = 'NO_SUBMIT'
              AND EXISTS (
                  SELECT 1
                  FROM decision_certificates AS cert
                  WHERE cert.certificate_type = 'NoSubmitDecisionCertificate'
                    AND cert.verifier_status = 'VERIFIED'
                    AND cert.semantic_key = 'no_submit:' || receipt.event_id || ':' || receipt.final_intent_id
                    AND NOT EXISTS (
                        SELECT 1
                        FROM decision_certificate_supersessions AS supersession
                        WHERE supersession.old_certificate_hash = cert.certificate_hash
                    )
              )
            UNION ALL
            SELECT event_id, decision_time, 'regret' AS surface
            FROM no_trade_regret_events
            WHERE decision_time IS NOT NULL
            UNION ALL
            SELECT event_id, decision_time, 'compile_failure' AS surface
            FROM decision_compile_failures
            UNION ALL
            SELECT
                json_extract(cert.payload_json, '$.event_id') AS event_id,
                cert.decision_time,
                'verified_no_submit_certificate' AS surface
            FROM decision_certificates AS cert
            WHERE cert.certificate_type = 'NoSubmitDecisionCertificate'
              AND cert.verifier_status = 'VERIFIED'
              AND json_extract(cert.payload_json, '$.event_id') IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM decision_certificate_supersessions AS supersession
                  WHERE supersession.old_certificate_hash = cert.certificate_hash
              )
              AND EXISTS (
                  SELECT 1
                  FROM edli_no_submit_receipts AS receipt
                  WHERE receipt.event_id = json_extract(cert.payload_json, '$.event_id')
                    AND receipt.final_intent_id = json_extract(cert.payload_json, '$.final_intent_id')
                    AND 'no_submit:' || receipt.event_id || ':' || receipt.final_intent_id = cert.semantic_key
              )
        ),
        violations AS (
            SELECT decision.event_id, decision.decision_time, decision.surface
            FROM opportunity_events AS event
            JOIN decision_surface AS decision
              ON decision.event_id = event.event_id
            WHERE event.{event_time_column} > decision.decision_time
        )
        SELECT
            COUNT(*) AS rows_count,
            COUNT(DISTINCT event_id || '|' || decision_time) AS event_decision_count
        FROM violations
        """
    ).fetchone()
    return {"rows": int(row[0] or 0), "events": int(row[1] or 0)}


def _certificate_time_violation_count(conn: sqlite3.Connection, column: str) -> int:
    if column not in {"source_available_at", "agent_received_at", "persisted_at"}:
        raise ValueError(f"unsupported certificate time column: {column}")
    return conn.execute(
        f"""
        SELECT COUNT(*)
        FROM decision_certificates
        WHERE {column} IS NOT NULL
          AND {column} > decision_time
        """
    ).fetchone()[0]


def _generated_decision_time_semantics(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS generated_no_submit_decisions,
            SUM(CASE WHEN created_at > persisted_at THEN 1 ELSE 0 END) AS db_created_after_header_persisted_at
        FROM decision_certificates
        WHERE certificate_type = 'NoSubmitDecisionCertificate'
          AND json_extract(payload_json, '$.generated_at_decision_time') = 1
          AND json_extract(payload_json, '$.header_persisted_at_semantics') = 'decision_kernel_generated_at_decision_time'
        """
    ).fetchone()
    return {
        "generated_no_submit_decisions": int(row[0] or 0),
        "db_created_after_header_persisted_at": int(row[1] or 0),
    }


def _payload_violation_count(conn: sqlite3.Connection, predicate: str) -> int:
    return conn.execute(
        f"""
        SELECT COUNT(*)
        FROM decision_certificates
        WHERE {predicate}
        """
    ).fetchone()[0]
