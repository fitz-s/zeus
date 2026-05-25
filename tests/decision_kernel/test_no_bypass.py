# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §3, §12, §13.
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.decision_kernel import claims
from src.decision_kernel.certificate import build_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.verifier import verify_actionable_trade, verify_execution_command


def _cert(certificate_type: str):
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=f"{certificate_type}:event",
        claim_type=certificate_type,
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload={"ok": True},
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


def test_actionable_trade_requires_fill_feasibility():
    action = _cert(claims.ACTIONABLE_TRADE)
    parents = (
        _cert(claims.NO_SUBMIT_DECISION),
        _cert(claims.EXECUTION_POLICY),
        _cert(claims.BALANCE_ALLOWANCE),
        _cert(claims.VENUE_CONNECTIVITY),
        _cert(claims.PRE_SUBMIT_REVALIDATION),
    )
    with pytest.raises(CertificateVerificationError, match="FillFeasibilityEvidenceCertificate"):
        verify_actionable_trade(action, parents)


def test_execution_command_requires_verified_actionable_trade():
    command = _cert(claims.EXECUTION_COMMAND)
    with pytest.raises(CertificateVerificationError, match="ActionableTradeCertificate"):
        verify_execution_command(command, ())


def test_market_channel_certificate_cannot_be_fill_certificate():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    fill = build_certificate(
        certificate_type=claims.FILL_FEASIBILITY,
        semantic_key="fill-feasibility:bad",
        claim_type="fill_feasibility",
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
    from src.decision_kernel.verifier import assert_market_channel_not_fill

    with pytest.raises(CertificateVerificationError, match="market-channel"):
        assert_market_channel_not_fill(fill)
