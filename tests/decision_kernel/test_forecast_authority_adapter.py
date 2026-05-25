# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §13.2, §16 A18.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.decision_kernel import claims
from src.decision_kernel.adapters.forecast_authority_adapter import build_forecast_authority_certificate


@dataclass(frozen=True)
class _Bundle:
    snapshot_id: str
    applied_validations: tuple[str, ...]


@dataclass(frozen=True)
class _ReaderResult:
    status: str
    reason_code: str | None
    bundle: _Bundle


def test_uses_canonical_executable_forecast_reader():
    calls = []

    def reader(**kwargs):
        calls.append(kwargs)
        return _ReaderResult("OK", None, _Bundle("snap-1", ("producer_ready",)))

    cert = build_forecast_authority_certificate(
        semantic_key="forecast:event",
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
        reader=reader,
        reader_kwargs={"scope": "scope-1", "now_utc": "decision-time"},
    )

    assert calls == [{"scope": "scope-1", "now_utc": "decision-time"}]
    assert cert.certificate_type == claims.FORECAST_AUTHORITY
    assert cert.payload["reader"] == "canonical_executable_forecast_reader"


def test_reader_reason_code_is_authority():
    def reader(**_kwargs):
        return _ReaderResult("BLOCKED", "COVERAGE_EXPIRED", _Bundle("snap-1", ()))

    cert = build_forecast_authority_certificate(
        semantic_key="forecast:event",
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
        reader=reader,
        reader_kwargs={},
    )

    assert cert.header.verifier_status == "REJECTED"
    assert cert.payload["reader_reason_code"] == "COVERAGE_EXPIRED"


def test_reader_applied_validations_preserved():
    def reader(**_kwargs):
        return _ReaderResult("OK", None, _Bundle("snap-1", ("producer_ready", "entry_ready")))

    cert = build_forecast_authority_certificate(
        semantic_key="forecast:event",
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
        reader=reader,
        reader_kwargs={},
    )

    assert cert.payload["applied_validations"] == ("producer_ready", "entry_ready")
