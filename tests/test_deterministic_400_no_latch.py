# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: fix_400_unknown_latch_2026-06-16.md — a venue 400 must classify as a
#   deterministic rejection (command_state REJECTED), never SUBMIT_UNKNOWN_SIDE_EFFECT, so it
#   cannot latch the governor unknown_side_effect kill switch (8h global submit block observed
#   live 2026-06-15 off a single 'invalid post-...' 400).
"""RED-on-revert: any Polymarket status_code=400 → deterministic rejection, not unknown."""

from src.execution.executor import _deterministic_submit_rejection_payload


class PolyApiException(Exception):
    """Name matters: the classifier discriminates on type(exc).__name__ == 'PolyApiException'."""


def _poly_400(message: str) -> Exception:
    return PolyApiException(f"PolyApiException status_code=400 body={{'error': '{message}'}}")


def test_invalid_post_400_is_deterministic_rejection_not_unknown():
    # The exact live failure class: 'invalid post-...' 400 fell through the invalid_amount
    # check into SUBMIT_UNKNOWN_SIDE_EFFECT and latched the kill switch for ~8h. It MUST now
    # classify as a clean deterministic rejection.
    exc = _poly_400("invalid post-only order; would cross the book")
    payload = _deterministic_submit_rejection_payload(exc, idempotency_key="idem-post")
    assert payload is not None, (
        "invalid-post 400 fell through to UNKNOWN_SIDE_EFFECT — governor kill-switch "
        "8h-latch regression (revert of the general-400 branch)"
    )
    assert payload["venue_order_created"] is False
    assert payload["proof_class"] == "deterministic_venue_400"
    assert payload["reason"] == "venue_rejected_400"


def test_invalid_amount_400_keeps_its_specific_reason():
    # The specific invalid_amount classifier is checked FIRST; its reason_code (used by the
    # downstream no-verbatim-retry handling) must be preserved, not shadowed by the general 400.
    exc = _poly_400("invalid amounts; maker amount 1000000 taker amount 1500000")
    payload = _deterministic_submit_rejection_payload(exc, idempotency_key="idem-amt")
    assert payload is not None
    assert payload["reason"] == "venue_rejected_invalid_amount_400"


def test_non_poly_ambiguous_error_stays_unknown():
    # A genuinely ambiguous error (no status_code=400, order maybe placed) must NOT be
    # classified deterministic — it stays unknown so the reconcile path verifies via venue read.
    payload = _deterministic_submit_rejection_payload(
        RuntimeError("connection reset mid-submit"), idempotency_key="idem-amb"
    )
    assert payload is None
