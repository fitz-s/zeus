# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §9, §14 PR-B.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.decision_kernel.certificate import ParentEdge, build_certificate
from src.decision_kernel.ledger import CompileFailure, DecisionCertificateLedger


def _cert(certificate_type: str, semantic_key: str, payload: dict, parents=()):
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type=certificate_type,
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload=payload,
        parent_edges=tuple(parents),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


def test_certificate_ledger_persists_verified_certificate_and_edges():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = DecisionCertificateLedger(conn)
    parent = _cert("ClockModeCertificate", "clock:event", {"mode": "NO_SUBMIT"})
    child = _cert(
        "CausalEventCertificate",
        "event:e1",
        {"event_id": "e1"},
        (ParentEdge("clock_mode", parent.certificate_hash, parent.certificate_type),),
    )

    ledger.persist_all((parent, child))

    assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 2
    edge = conn.execute("SELECT parent_role FROM decision_certificate_edges").fetchone()
    assert edge["parent_role"] == "clock_mode"


def test_compile_failures_persisted():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = DecisionCertificateLedger(conn)
    failure = CompileFailure(
        event_id="event-1",
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
        mode="NO_SUBMIT",
        claim_type="no_submit_dry_run_decision",
        stage="FDR",
        reason_code="TESTING_PROTOCOL_MISSING",
        parent_hashes=("abc",),
    )

    ledger.persist_failures((failure,))
    row = conn.execute("SELECT reason_code, parent_hashes_json FROM decision_compile_failures").fetchone()
    assert row["reason_code"] == "TESTING_PROTOCOL_MISSING"
    assert "abc" in row["parent_hashes_json"]
