# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §1, §12, §13.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.decision_kernel.certificate import build_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.verifier import verify_certificate


def _cert(**overrides):
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    params = {
        "certificate_type": "ClockModeCertificate",
        "semantic_key": "clock:test",
        "claim_type": "clock_mode",
        "mode": "NO_SUBMIT",
        "decision_time": now,
        "source_available_at": now,
        "agent_received_at": now,
        "persisted_at": now,
        "payload": {"ok": True},
        "authority_id": "test",
        "authority_version": "v1",
        "algorithm_id": "test",
        "algorithm_version": "v1",
    }
    params.update(overrides)
    return build_certificate(**params)


def test_live_rejects_parent_source_available_after_decision():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    cert = _cert(payload={"_parent_times": {"source_available_at": now + timedelta(seconds=1)}})
    with pytest.raises(CertificateVerificationError, match="source_available_at"):
        verify_certificate(cert)


def test_live_rejects_parent_agent_received_after_decision():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    cert = _cert(payload={"_parent_times": {"agent_received_at": now + timedelta(seconds=1)}})
    with pytest.raises(CertificateVerificationError, match="agent_received_at"):
        verify_certificate(cert)


def test_live_rejects_parent_persisted_after_decision():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    cert = _cert(payload={"_parent_times": {"persisted_at": now + timedelta(seconds=1)}})
    with pytest.raises(CertificateVerificationError, match="persisted_at"):
        verify_certificate(cert)


def test_replay_counterfactual_cannot_be_live():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    cert = _cert(
        mode="REPLAY_COUNTERFACTUAL",
        semantic_key="clock:replay",
        persisted_at=now + timedelta(days=1),
    )
    verify_certificate(cert)
    live_cert = _cert(
        mode="LIVE",
        semantic_key="clock:live",
        persisted_at=now + timedelta(days=1),
    )
    with pytest.raises(CertificateVerificationError, match="persisted_at"):
        verify_certificate(live_cert)
