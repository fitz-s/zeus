import sqlite3
from types import SimpleNamespace

from src.execution.executor import _entry_actionable_certificate_component


def _conn_with_world_cert_table() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute(
        """
        CREATE TABLE world.decision_certificates (
            certificate_hash TEXT NOT NULL,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL
        )
        """
    )
    return conn


def test_entry_actionable_certificate_guard_requires_live_verified_row():
    conn = _conn_with_world_cert_table()
    intent = SimpleNamespace(actionable_certificate_hash="h1")

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is False
    assert component["reason"] == "actionable_certificate_not_persisted_live_verified"


def test_entry_actionable_certificate_guard_allows_persisted_live_verified_row():
    conn = _conn_with_world_cert_table()
    conn.execute(
        """
        INSERT INTO world.decision_certificates (
            certificate_hash, certificate_type, mode, verifier_status
        ) VALUES (?, 'ActionableTradeCertificate', 'LIVE', 'VERIFIED')
        """,
        ("h1",),
    )
    intent = SimpleNamespace(actionable_certificate_hash="h1")

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is True
    assert component["details"]["certificate_schema"] == "world"
