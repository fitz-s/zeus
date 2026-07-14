# Created: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R6 (venue
#   response-contract layer, PREPARE) + docs/rebuild/whole_system_first_principles_2026-07-07.md
#   §2.8 (venue verdict: response-contract layer missing -- 7 double/triple-key
#   .get() guess sites are the root class of the #429 cancel_orders envelope bug).
"""Response-contract layer for the venue/CLOB client boundary (R6-a).

Problem this closes: ``src/venue/polymarket_v2_adapter.py`` used to have
each venue endpoint independently multi-key ``.get()``-guess the raw
SDK/HTTP response shape (``orderID`` vs ``order_id`` vs ``id``; ``status``
vs ``state``; ...). The single-order cancel path in particular tested only
``_nonempty(raw_dict.get("canceled"))`` WITHOUT checking that the mentioned
order id was actually THIS order -- so a live-verified batch-envelope-shaped
response ``{"canceled": ["<some other order>"]}`` reported CANCELED for an
order the venue never confirmed. That is the #429 bug class: the live
2026-07-05 incident fixed this exact false-positive for the BATCH path
(``cancel_batch`` via ``batch_submit.map_cancel_envelope``, exact order-id
membership) but the single-order path (``cancel``) kept the old ad hoc
guess. This module is the single source of truth both paths now share.

Contract for every parser here: given a raw response for a known endpoint,
either

  (a) return a typed, order-attributed result, or
  (b) return an explicitly-typed ambiguous outcome (e.g. ``UNKNOWN`` when a
      recognized envelope shape says nothing about this specific order --
      never a silently-defaulted success), or
  (c) raise ``VenueResponseShapeError`` when the payload matches NONE of
      the known shape variants at all. Callers must NOT catch this to
      synthesize a default value -- it exists so a genuinely unrecognized
      venue response is loud (logged with the raw payload attached), not
      silently folded into a placeholder like ``status="UNKNOWN"``.

Downstream consumption boundary: this module parses raw SDK/HTTP responses
at the venue client. It does not decide what a recovery/reconciliation
layer does with an ``UNKNOWN`` or a raised shape error -- that is R2's
domain (``command_recovery.py`` / ``exchange_reconcile.py``), which
consumes the resulting typed facts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.contracts.canonical_lifecycle import is_cancel_confirmed_status
from src.venue.batch_submit import map_cancel_envelope


class VenueResponseShapeError(RuntimeError):
    """Raised when a raw venue response matches no known contract shape for
    ``endpoint``. Carries the raw payload for postmortem/replay.

    Callers must not catch this to synthesize a default success/failure --
    they must record an ambiguous/unknown-side-effect outcome, the same way
    a network exception from the SDK is already handled at every call site
    (venue_cancel_journal.py, fill_tracker.py, day0_hard_fact_exit.py all
    wrap venue calls in ``except Exception`` and record UNKNOWN/retry,
    never a guessed success).
    """

    def __init__(self, endpoint: str, raw: Any, detail: str) -> None:
        self.endpoint = endpoint
        self.raw = raw
        self.detail = detail
        super().__init__(
            f"venue response shape error at endpoint={endpoint!r}: {detail}; raw={raw!r}"
        )


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes)):
        return bool(value)
    if isinstance(value, dict):
        return bool(value)
    try:
        return bool(list(value))
    except TypeError:
        return bool(value)


def _reason_from(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, dict):
        parts = [f"{key}: {item}" for key, item in value.items()]
        return "; ".join(parts) if parts else fallback
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or fallback
    return str(value) or fallback


def extract_order_id(raw: Any) -> Optional[str]:
    """Cross-SDK-version order-id key normalizer.

    Not itself a "guess site" bug -- venue SDK/API responses genuinely use
    different casings/names for the same field across call surfaces
    (``orderID`` from the two-step order-post path, ``order_id``/``id``
    from others). Centralized here so every parser in this module (and the
    adapter's thin call sites) uses one normalizer.
    """
    if not isinstance(raw, dict):
        return None
    return raw.get("orderID") or raw.get("orderId") or raw.get("order_id") or raw.get("id")


def extract_response_error(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """Cross-SDK-version error code/message key normalizer."""
    if not isinstance(raw, dict):
        return None, None
    code = raw.get("errorCode") or raw.get("error_code") or raw.get("code")
    message = raw.get("errorMessage") or raw.get("error_message") or raw.get("message")
    return (str(code) if code else None, str(message) if message else None)


@dataclass(frozen=True)
class CancelOutcome:
    """Typed, order-attributed result of parsing a raw cancel response."""

    status: str  # "CANCELED" | "NOT_CANCELED" | "UNKNOWN"
    order_id: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None


_CANCEL_LEGACY_KEYS: tuple[str, ...] = (
    "status",
    "state",
    "success",
    "canceled",
    "cancelled",
    "not_canceled",
    "not_cancelled",
    "errorCode",
    "error_code",
    "code",
    "errorMessage",
    "error_message",
    "message",
)


def _looks_like_cancel_envelope(raw_response: dict) -> bool:
    """True only when ``canceled``/``not_canceled`` are ACTUALLY the
    live-verified envelope's container types (list / dict) -- not a
    per-item legacy boolean (``{"canceled": True}``) or string reason
    (``{"not_canceled": "already open elsewhere"}``), which the per-item
    legacy branch below already handles correctly and must keep handling
    unchanged (batch's index/echo_id-mapped per-item dicts use exactly
    these legacy shapes)."""
    canceled_raw = raw_response.get("canceled", raw_response.get("cancelled"))
    not_canceled_raw = raw_response.get("not_canceled", raw_response.get("not_cancelled"))
    return isinstance(canceled_raw, (list, tuple)) or isinstance(not_canceled_raw, dict)


def parse_cancel_outcome(
    order_id: str,
    raw_response: Any,
    *,
    endpoint: str = "cancel",
    check_envelope: bool = False,
) -> CancelOutcome:
    """Cancel-response parser.

    ``check_envelope=True`` (single-order ``cancel()`` only) additionally
    tries the live-verified envelope shape (2026-07-05) first:
    ``{"canceled": [...], "not_canceled": {...}}``, membership EXACT by
    order id (reuses ``batch_submit.map_cancel_envelope``) -- an envelope
    that mentions some OTHER order id and says nothing about ``order_id``
    maps to UNKNOWN, never CANCELED. This is the #429 false-positive this
    module closes for the single-cancel path (``cancel_batch`` already
    applies this same check at the top-level batch response before ever
    reaching this function; ``check_envelope`` defaults to False here so
    batch's per-item legacy dicts -- which reuse "canceled"/"not_canceled"
    keys with different, non-envelope value types -- are never
    double-interpreted as an envelope).

    Fail-closed precedence:

      1. Bare non-empty string response -- legacy shape where the SDK
         echoes the canceled order id directly. Treated as CANCELED.
      2. (``check_envelope=True`` only) live-verified envelope shape,
         exact order-id membership.
      3. Legacy per-item dict shape (status/state/success/canceled/
         not_canceled/error keys).
      4. Anything else -- raise ``VenueResponseShapeError``. No silent
         "UNKNOWN" default for a payload we cannot recognize at all.
    """
    if isinstance(raw_response, str) and raw_response.strip():
        return CancelOutcome(status="CANCELED", order_id=raw_response.strip())

    if not isinstance(raw_response, dict):
        raise VenueResponseShapeError(
            endpoint,
            raw_response,
            "cancel response is neither a non-empty string nor a dict",
        )

    if check_envelope and _looks_like_cancel_envelope(raw_response):
        envelope_items = map_cancel_envelope(raw_response, [order_id])
        if envelope_items is not None:
            item = envelope_items[0]
            if item.source == "unmapped":
                return CancelOutcome(
                    status="UNKNOWN",
                    order_id=order_id,
                    error_message="cancel envelope did not mention this order id",
                )
            raw_item = item.raw_item or {}
            if "not_canceled" in raw_item:
                reason = raw_item.get("not_canceled")
                if isinstance(reason, dict):
                    reason = reason.get(order_id)
                return CancelOutcome(
                    status="NOT_CANCELED",
                    order_id=order_id,
                    error_message=_reason_from(reason, "cancel_not_canceled"),
                )
            return CancelOutcome(status="CANCELED", order_id=order_id)

    if not any(key in raw_response for key in _CANCEL_LEGACY_KEYS):
        raise VenueResponseShapeError(
            endpoint,
            raw_response,
            "cancel response dict has no recognized envelope or legacy keys",
        )

    error_code, error_message = extract_response_error(raw_response)
    not_canceled = raw_response.get("not_canceled", raw_response.get("not_cancelled"))
    if error_code or error_message or _nonempty(not_canceled) or raw_response.get("success") is False:
        return CancelOutcome(
            status="NOT_CANCELED",
            order_id=order_id,
            error_code=error_code,
            error_message=error_message or _reason_from(not_canceled, "cancel_not_canceled"),
        )
    canceled = raw_response.get("canceled", raw_response.get("cancelled"))
    status = str(raw_response.get("status") or raw_response.get("state") or "").upper()
    if _nonempty(canceled) or is_cancel_confirmed_status(status) or raw_response.get("success") is True:
        return CancelOutcome(
            status="CANCELED", order_id=extract_order_id(raw_response) or order_id
        )
    return CancelOutcome(
        status="UNKNOWN",
        order_id=order_id,
        error_message="unrecognized_cancel_response",
    )


@dataclass(frozen=True)
class OrderStatusOutcome:
    """Typed, order-attributed result of parsing a raw order-status
    response item (``get_order`` / ``get_open_orders``)."""

    order_id: str
    status: str


def parse_order_status(
    raw: Any, *, fallback_order_id: str, endpoint: str
) -> OrderStatusOutcome:
    """Order-status parse for ``get_order``/``get_open_orders``.

    Requires a recognized status-bearing key (``status`` or ``state``);
    raises rather than silently defaulting to a placeholder status string
    (previously ``"UNKNOWN"``) when the response item carries neither --
    that silent default is indistinguishable from a venue that genuinely
    reports an unknown-but-real status value.
    """
    if not isinstance(raw, dict):
        raise VenueResponseShapeError(
            endpoint, raw, "order-status response item is not a dict"
        )
    if "status" not in raw and "state" not in raw:
        raise VenueResponseShapeError(
            endpoint,
            raw,
            "order-status response item has neither 'status' nor 'state'",
        )
    order_id = extract_order_id(raw) or fallback_order_id
    status = str(raw.get("status") or raw.get("state") or "UNKNOWN").strip().upper()
    if status.startswith("ORDER_STATUS_"):
        status = status.removeprefix("ORDER_STATUS_")
    status = {
        "CANCELED_MARKET_RESOLVED": "CANCELED",
        "INVALID": "REJECTED",
    }.get(status, status)
    return OrderStatusOutcome(order_id=order_id, status=status)
