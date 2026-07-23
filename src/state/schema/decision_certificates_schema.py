"""Decision certificate ledger schema owner."""

from __future__ import annotations

import sqlite3


CREATE_CERTIFICATES_SQL = """
CREATE TABLE IF NOT EXISTS decision_certificates (
    certificate_id TEXT NOT NULL PRIMARY KEY,
    certificate_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    canonicalization_version TEXT NOT NULL,
    semantic_key TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode = 'LIVE'),
    decision_time TEXT NOT NULL,
    source_available_at TEXT,
    agent_received_at TEXT,
    persisted_at TEXT,
    max_parent_source_available_at TEXT,
    max_parent_agent_received_at TEXT,
    max_parent_persisted_at TEXT,
    authority_id TEXT NOT NULL,
    authority_version TEXT NOT NULL,
    algorithm_id TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    config_hash TEXT,
    model_version_hash TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    certificate_hash TEXT NOT NULL UNIQUE,
    verifier_status TEXT NOT NULL CHECK (
      verifier_status IN ('VERIFIED','REJECTED','SUPERSEDED','REVIEW_REQUIRED')
    ),
    created_at TEXT NOT NULL,
    UNIQUE(certificate_type, semantic_key, mode, decision_time)
)
"""

CREATE_EDGES_SQL = """
CREATE TABLE IF NOT EXISTS decision_certificate_edges (
    child_certificate_id TEXT NOT NULL,
    parent_role TEXT NOT NULL,
    parent_certificate_hash TEXT NOT NULL,
    parent_certificate_type TEXT NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0,1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (child_certificate_id, parent_role, parent_certificate_hash)
)
"""

CREATE_SUPERSESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS decision_certificate_supersessions (
    supersession_id TEXT NOT NULL PRIMARY KEY,
    old_certificate_hash TEXT NOT NULL,
    new_certificate_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

CREATE_FAILURES_SQL = """
CREATE TABLE IF NOT EXISTS decision_compile_failures (
    failure_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    mode TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_detail TEXT,
    parent_hashes_json TEXT,
    created_at TEXT NOT NULL
)
"""

CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_decision_certificates_semantic
    ON decision_certificates(certificate_type, semantic_key, mode, decision_time);
CREATE INDEX IF NOT EXISTS idx_decision_certificates_hash
    ON decision_certificates(certificate_hash);
CREATE INDEX IF NOT EXISTS idx_decision_certificate_edges_parent
    ON decision_certificate_edges(parent_certificate_hash);
CREATE INDEX IF NOT EXISTS idx_decision_compile_failures_event
    ON decision_compile_failures(event_id, decision_time);
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_CERTIFICATES_SQL)
    conn.execute(CREATE_EDGES_SQL)
    conn.execute(CREATE_SUPERSESSIONS_SQL)
    conn.execute(CREATE_FAILURES_SQL)
    conn.executescript(CREATE_INDEXES_SQL)
