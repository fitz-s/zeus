# Created: 2026-07-02
# Last reused or audited: 2026-10-01
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
#                  (SCH-W1.2-ORDER-STATE, rev 2 post-critic); standing ENTRY keep-by-value
#                  law (operator, 2026-09-30): age and posterior identity are not value.
"""Derived order-state predicates (Option B — no stored state).

Every function here is PURE: explicit inputs only, no DB reads. Order age and
posterior identity are not order value; an open ENTRY rest is disposed only by
``entry_rest_disposition`` over a fresh fractional-Kelly valuation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import math
from typing import Any, Mapping

UTC = timezone.utc

# CommandState values (src/execution/command_bus.py) a command dwells in while
# its submit side effect is in flight, before the venue has acknowledged it.
_IN_FLIGHT_SUBMIT_STATES = frozenset({"SUBMITTING", "POSTING", "SIGNED_PERSISTED"})


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def is_delayed(
    command: Mapping[str, Any],
    *,
    now: datetime,
    submit_flight_sla_seconds: float,
) -> bool:
    """True iff an in-flight command has dwelt in its current submit-path state
    longer than the measured SLA (W0.2 measured submit p99).

    ``command`` is a venue_commands-row-shaped mapping with ``state`` and
    ``updated_at`` keys. ``updated_at`` doubles as the state-entry timestamp:
    venue_command_repo.append_event bumps ``state`` and ``updated_at`` in the
    same atomic UPDATE, so ``updated_at`` is always the time the CURRENT state
    was entered. A terminal command is never delayed.
    """
    state = str(command.get("state") or "").strip().upper()
    if state not in _IN_FLIGHT_SUBMIT_STATES:
        return False
    entered_at = _coerce_datetime(command.get("updated_at"))
    if entered_at is None:
        return False
    dwell_seconds = (now - entered_at).total_seconds()
    return dwell_seconds > submit_flight_sla_seconds


def entry_rest_disposition(
    *,
    open_remaining: Decimal,
    target_remaining: Decimal,
    minimum_order_size: Decimal,
    conditional_gain: float,
) -> tuple[str, str]:
    """Dispose one open ENTRY rest from its current valuation.

    ``target_remaining`` is the selector's fractional-Kelly remaining quantity
    R* at the rest's own limit; ``conditional_gain`` is the expected log-wealth
    gain of the quantity that would keep working. The venue has no amend, so a
    RESIZE is cancel, confirmed terminal reconciliation, then a fresh decision.
    A reduction smaller than one legal lot keeps the incumbent unchanged.
    """
    values = (open_remaining, target_remaining, minimum_order_size)
    if (
        any(not isinstance(v, Decimal) or not v.is_finite() for v in values)
        or open_remaining <= 0
        or target_remaining < 0
        or minimum_order_size <= 0
        or not math.isfinite(conditional_gain)
    ):
        raise ValueError("ENTRY_REST_VALUE_INVALID")
    if target_remaining < minimum_order_size:
        return "CANCEL", "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    if conditional_gain <= 0:
        return "CANCEL", "CURRENT_MEAN_VALUE_NON_POSITIVE"
    if open_remaining - target_remaining >= minimum_order_size:
        return "RESIZE", "CURRENT_FRACTIONAL_TARGET_REDUCED"
    return "KEEP", "CURRENT_ENTRY_REST_VALUE_POSITIVE"
