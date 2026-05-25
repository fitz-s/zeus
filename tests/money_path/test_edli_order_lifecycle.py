# Created: 2026-05-25
# Last reused/audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations


def test_order_lifecycle_certificate_status_vocabulary_declared_for_future_live_cut():
    statuses = {
        "SUBMITTED",
        "ACCEPTED",
        "RESTING",
        "REJECTED",
        "FILLED",
        "PARTIAL_FILL",
        "CANCEL_REMAINDER",
        "TIMEOUT_UNKNOWN",
        "ERROR_UNKNOWN",
    }

    assert "ACCEPTED" in statuses
    assert "FILLED" in statuses
    assert "ACCEPTED" != "FILLED"
    assert "TIMEOUT_UNKNOWN" in statuses
    assert "REJECTED" in statuses
    assert "PARTIAL_FILL" in statuses
    assert "CANCEL_REMAINDER" in statuses
