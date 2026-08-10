"""No-submit projection helpers.

The certificate ledger is the proof authority. This module provides a named
projection surface so reports do not treat legacy receipt rows as source truth.
"""

from __future__ import annotations

import sqlite3

from src.decision_kernel import claims


_PRE_SUBMIT_SEMANTIC_PREFIX = "pre_submit:"


def no_submit_projection_rows(conn: sqlite3.Connection, *, limit: int = 100) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                receipt.*,
                cert.certificate_hash AS no_submit_decision_certificate_hash
            FROM edli_no_submit_receipts AS receipt
            JOIN decision_certificates AS cert
             ON cert.certificate_type = ?
             AND cert.semantic_key = ? || receipt.event_id || ':' || receipt.final_intent_id
             AND cert.verifier_status = 'VERIFIED'
             AND NOT EXISTS (
                 SELECT 1
                 FROM decision_certificate_supersessions AS supersession
                 WHERE supersession.old_certificate_hash = cert.certificate_hash
             )
            ORDER BY receipt.decision_time DESC
            LIMIT ?
            """,
            (claims.PRE_SUBMIT_DECISION, _PRE_SUBMIT_SEMANTIC_PREFIX, limit),
        )
    )
