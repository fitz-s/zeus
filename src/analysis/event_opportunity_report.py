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
    accepted_no_submit_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM edli_no_submit_receipts AS receipt
        JOIN decision_certificates AS cert
          ON cert.certificate_type = 'NoSubmitDecisionCertificate'
         AND cert.semantic_key = 'no_submit:' || receipt.event_id || ':' || receipt.final_intent_id
         AND cert.verifier_status = 'VERIFIED'
        WHERE receipt.side_effect_status = 'NO_SUBMIT'
        """
    ).fetchone()[0]
    feasibility_count = conn.execute(
        "SELECT COUNT(*) FROM execution_feasibility_evidence"
    ).fetchone()[0]
    event_available_after_decision = _event_time_violation_count(conn, "available_at")
    event_received_after_decision = _event_time_violation_count(conn, "received_at")
    violations = {
        "event_available_after_decision": event_available_after_decision,
        "event_received_after_decision": event_received_after_decision,
        "available_at_violations": event_available_after_decision,
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
    }
    return {
        "events_by_type": event_counts,
        "processing_by_status": processing_counts,
        "blocked_by_stage": regret_by_stage,
        "accepted_no_submit_receipts": accepted_no_submit_count,
        "execution_feasibility_rows": feasibility_count,
        "violations": violations,
    }


def _event_time_violation_count(conn: sqlite3.Connection, event_time_column: str) -> int:
    if event_time_column not in {"available_at", "received_at"}:
        raise ValueError(f"unsupported event time column: {event_time_column}")
    return conn.execute(
        f"""
        SELECT COUNT(*)
        FROM opportunity_events AS event
        JOIN (
            SELECT event_id, decision_time FROM edli_no_submit_receipts
            UNION ALL
            SELECT event_id, decision_time FROM no_trade_regret_events WHERE decision_time IS NOT NULL
        ) AS decision
          ON decision.event_id = event.event_id
        WHERE event.{event_time_column} > decision.decision_time
        """
    ).fetchone()[0]


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


def _payload_violation_count(conn: sqlite3.Connection, predicate: str) -> int:
    return conn.execute(
        f"""
        SELECT COUNT(*)
        FROM decision_certificates
        WHERE {predicate}
        """
    ).fetchone()[0]
