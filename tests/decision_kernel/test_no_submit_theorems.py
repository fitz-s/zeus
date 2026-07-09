# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §2, §12, §13.
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.decision_kernel import claims
from src.decision_kernel.certificate import build_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.verifier import assert_market_channel_not_fill


def test_no_submit_cannot_have_actionable_trade_score():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    from src.decision_kernel.certificates.no_submit import build_no_submit_decision_certificate

    with pytest.raises(ValueError, match="positive actionable_trade_score"):
        build_no_submit_decision_certificate(
            semantic_key="no_submit:event:intent",
            decision_time=now,
            parent_edges=(),
            parents=(),
            payload={"submitted": False, "proof_accepted": True, "actionable_trade_score": 0.01},
            source_available_at=now,
            agent_received_at=now,
            persisted_at=now,
        )


def test_no_submit_cannot_have_execution_command():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    cert = build_certificate(
        certificate_type=claims.NO_SUBMIT_DECISION,
        semantic_key="no_submit:event:intent",
        claim_type="no_submit_dry_run_decision",
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload={"submitted": False, "proof_accepted": True, "execution_command_id": "cmd-1"},
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    from src.decision_kernel.verifier import verify_no_submit_decision

    with pytest.raises(CertificateVerificationError, match="execution command"):
        verify_no_submit_decision(cert, ())


def test_no_submit_certificate_rejects_proof_accepted_false():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    from src.decision_kernel.certificates.no_submit import build_no_submit_decision_certificate

    with pytest.raises(ValueError, match="proof_accepted=true"):
        build_no_submit_decision_certificate(
            semantic_key="no_submit:event:intent",
            decision_time=now,
            parent_edges=(),
            parents=(),
            payload={"submitted": False, "proof_accepted": False, "actionable_trade_score": 0.0},
            source_available_at=now,
            agent_received_at=now,
            persisted_at=now,
        )


def test_no_submit_certificate_rejects_missing_proof_accepted():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    cert = build_certificate(
        certificate_type=claims.NO_SUBMIT_DECISION,
        semantic_key="no_submit:event:intent",
        claim_type="no_submit_dry_run_decision",
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload={"submitted": False, "actionable_trade_score": 0.0},
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    from src.decision_kernel.verifier import verify_no_submit_decision

    with pytest.raises(CertificateVerificationError, match="proof_accepted=true"):
        verify_no_submit_decision(cert, ())


def test_market_channel_certificate_cannot_be_fill_certificate():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    cert = build_certificate(
        certificate_type=claims.FILL,
        semantic_key="fill:bad",
        claim_type="fill",
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload={"source_kind": claims.PUBLIC_MARKET_CHANNEL_SOURCE},
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    with pytest.raises(CertificateVerificationError, match="market-channel"):
        assert_market_channel_not_fill(cert)
