# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: user live endpoint asymmetry analysis 2026-05-21; monotonic venue order truth reducer

"""Monotonic reducer for venue order facts, trade facts, and open-order probes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping


TERMINAL_NO_FILL = "TERMINAL_NO_FILL"
TERMINAL_FILLED = "TERMINAL_FILLED"
PARTIAL_WITH_REMAINDER = "PARTIAL_WITH_REMAINDER"
LIVE_RESTING = "LIVE_RESTING"
UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"
REVIEW_REQUIRED = "REVIEW_REQUIRED"

_TERMINAL_STATES = {"MATCHED", "CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED"}
_OPEN_STATES = {"LIVE", "RESTING", "PARTIALLY_MATCHED"}
_UNKNOWN_STATES = {"UNKNOWN", "SUBMIT_UNKNOWN_SIDE_EFFECT"}
_REVIEW_STATES = {"REVIEW_REQUIRED"}


@dataclass(frozen=True)
class CanonicalOrderTruth:
    """Reduced order truth that cannot regress from stronger proof."""

    state: str
    remaining_size: Decimal | None
    matched_size: Decimal
    proof_class: str
    source_state: str = ""


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _row_get(row: Mapping[str, Any], key: str) -> Any:
    getter = getattr(row, "get", None)
    if callable(getter):
        return getter(key)
    try:
        return row[key]
    except Exception:
        return None


def _state(row: Mapping[str, Any]) -> str:
    return str(_row_get(row, "state") or "").strip().upper()


def _zero(value: Decimal | None) -> bool:
    return value is not None and value == Decimal("0")


class VenueOrderTruthReducer:
    """Reduce venue facts into one monotonic order-truth state."""

    @staticmethod
    def reduce(
        *,
        order_facts: Iterable[Mapping[str, Any]] = (),
        trade_filled_size: Decimal | str | int | float | None = None,
        command_size: Decimal | str | int | float | None = None,
        open_order_present: bool | None = None,
        command_state: str | None = None,
    ) -> CanonicalOrderTruth:
        facts = list(order_facts)
        filled_from_trade = _decimal_or_none(trade_filled_size) or Decimal("0")
        command_size_dec = _decimal_or_none(command_size)
        matched_from_orders = Decimal("0")
        terminal_zero_no_fill: Mapping[str, Any] | None = None
        latest_open: Mapping[str, Any] | None = None
        latest_unknown: Mapping[str, Any] | None = None
        latest_review: Mapping[str, Any] | None = None

        for fact in facts:
            state = _state(fact)
            matched = _decimal_or_none(_row_get(fact, "matched_size")) or Decimal("0")
            remaining = _decimal_or_none(_row_get(fact, "remaining_size"))
            matched_from_orders = max(matched_from_orders, matched)
            if state in _TERMINAL_STATES and _zero(remaining) and matched == 0:
                terminal_zero_no_fill = fact
            elif state in _OPEN_STATES:
                latest_open = fact
            elif state in _UNKNOWN_STATES:
                latest_unknown = fact
            elif state in _REVIEW_STATES:
                latest_review = fact

        matched = max(filled_from_trade, matched_from_orders)
        if matched > 0:
            if command_size_dec is not None:
                remaining = max(Decimal("0"), command_size_dec - matched)
                if remaining == 0:
                    return CanonicalOrderTruth("MATCHED", remaining, matched, TERMINAL_FILLED)
                return CanonicalOrderTruth("PARTIALLY_MATCHED", remaining, matched, PARTIAL_WITH_REMAINDER)
            return CanonicalOrderTruth("PARTIALLY_MATCHED", None, matched, PARTIAL_WITH_REMAINDER)

        if terminal_zero_no_fill is not None:
            return CanonicalOrderTruth(
                _state(terminal_zero_no_fill),
                Decimal("0"),
                Decimal("0"),
                TERMINAL_NO_FILL,
                source_state=_state(terminal_zero_no_fill),
            )

        if latest_review is not None or str(command_state or "").upper() == "REVIEW_REQUIRED":
            return CanonicalOrderTruth("REVIEW_REQUIRED", None, Decimal("0"), REVIEW_REQUIRED)

        if latest_unknown is not None or str(command_state or "").upper() in _UNKNOWN_STATES:
            return CanonicalOrderTruth("UNKNOWN", None, Decimal("0"), UNKNOWN_SIDE_EFFECT)

        if latest_open is not None or open_order_present is True:
            state = _state(latest_open) if latest_open is not None else "LIVE"
            remaining = _decimal_or_none(_row_get(latest_open, "remaining_size")) if latest_open is not None else None
            return CanonicalOrderTruth(state, remaining, Decimal("0"), LIVE_RESTING)

        return CanonicalOrderTruth("UNKNOWN", None, Decimal("0"), UNKNOWN_SIDE_EFFECT)
