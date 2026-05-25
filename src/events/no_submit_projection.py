"""No-submit projection helpers.

The certificate ledger is the proof authority. This module provides a named
projection surface so reports do not treat legacy receipt rows as source truth.
"""

from __future__ import annotations

import sqlite3


def no_submit_projection_rows(conn: sqlite3.Connection, *, limit: int = 100) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                receipt.*,
                cert.certificate_hash AS no_submit_decision_certificate_hash
            FROM edli_no_submit_receipts AS receipt
            JOIN decision_certificates AS cert
              ON cert.certificate_type = 'NoSubmitDecisionCertificate'
             AND cert.semantic_key = 'no_submit:' || receipt.event_id || ':' || receipt.final_intent_id
             AND cert.verifier_status = 'VERIFIED'
            ORDER BY receipt.decision_time DESC
            LIMIT ?
            """,
            (limit,),
        )
    )
