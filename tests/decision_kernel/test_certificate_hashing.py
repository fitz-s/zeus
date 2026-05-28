# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §6, §9, §13.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.certificate import ParentEdge, build_certificate
from src.decision_kernel.errors import CertificateSemanticDriftError
from src.decision_kernel.ledger import DecisionCertificateLedger


def _base(certificate_type: str, semantic_key: str, payload: dict, parents=()):
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


def test_parent_role_swap_changes_hash():
    parent_a = _base("ForecastAuthorityCertificate", "forecast:a", {"a": 1})
    parent_b = _base("CalibrationCertificate", "cal:b", {"b": 1})
    child_one = _base(
        "BeliefCertificate",
        "belief:event",
        {"q": 0.5},
        (
            ParentEdge("forecast_authority", parent_a.certificate_hash, parent_a.certificate_type),
            ParentEdge("calibration", parent_b.certificate_hash, parent_b.certificate_type),
        ),
    )
    child_two = _base(
        "BeliefCertificate",
        "belief:event",
        {"q": 0.5},
        (
            ParentEdge("calibration", parent_a.certificate_hash, parent_a.certificate_type),
            ParentEdge("forecast_authority", parent_b.certificate_hash, parent_b.certificate_type),
        ),
    )
    assert child_one.certificate_hash != child_two.certificate_hash


def test_same_semantic_key_different_hash_requires_supersession():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = DecisionCertificateLedger(conn)
    cert_one = _base("BeliefCertificate", "belief:event", {"q": 0.5})
    cert_two = _base("BeliefCertificate", "belief:event", {"q": 0.6})
    ledger.insert_idempotent(cert_one)
    assert ledger.insert_idempotent(cert_one) == cert_one.certificate_id
    with pytest.raises(CertificateSemanticDriftError, match="SEMANTIC_DRIFT"):
        ledger.insert_idempotent(cert_two)


def test_hash_canonicalization_decimal_datetime_stable():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    one = stable_hash({"x": Decimal("1.2300"), "t": now})
    two = stable_hash({"t": "2026-05-25T12:00:00Z", "x": Decimal("1.23")})
    assert one == two
