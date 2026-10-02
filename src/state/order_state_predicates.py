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
    held_shares: Decimal,
    open_remaining: Decimal,
    target_holding_shares: Decimal,
    remainder_gain: float,
) -> tuple[str, str]:
    """Dispose one open ENTRY rest as the order it is: KEEP or CANCEL.

    ``h`` held shares (the order's own fills counted once), ``r`` the open
    remainder, ``H*`` the holding the selector would size this order to if it
    placed it fresh now, from the holding before the order's own fills and
    under every limit the selector applies; ``g`` the remainder's own
    common-axis expected growth given ``h``.

    KEEP iff ``g > 0`` and ``h + r <= H*``. The lot floor sizes only a NEW
    order: a remainder below one lot is an order that exists. ``h + r`` is
    the order's full size on top of the prior holding and ``H*`` is computed
    from that prior holding, so the verdict never depends on how much of the
    order has filled. No amend: a CANCEL hands the family to the
    confirmed-cancel redecision, which sizes a fresh order.
    """
    values = (held_shares, open_remaining, target_holding_shares)
    if (
        any(not isinstance(v, Decimal) or not v.is_finite() for v in values)
        or held_shares < 0
        or open_remaining <= 0
        or target_holding_shares < 0
        or not math.isfinite(remainder_gain)
    ):
        raise ValueError("ENTRY_REST_VALUE_INVALID")
    if remainder_gain <= 0:
        return "CANCEL", "CURRENT_MEAN_VALUE_NON_POSITIVE"
    if held_shares + open_remaining > target_holding_shares:
        return "CANCEL", "CURRENT_FRACTIONAL_TARGET_REDUCED"
    return "KEEP", "CURRENT_ENTRY_REST_VALUE_POSITIVE"
