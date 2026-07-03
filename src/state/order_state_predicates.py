# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
#                  (SCH-W1.2-ORDER-STATE, rev 2 post-critic)
"""Derived order-state predicates (Option B — no stored state).

C3 staleness classification, submit-flight delay, and max-rest-age are
continuous RELATIONS over existing truth (venue_commands.q_version, CommandState
dwell time, rest age), not stored state transitions. Storing a classification
that is always recomputable from live truth would create a stale copy of
staleness itself — the poll-era artifact this module avoids (rejected-alternative
record: Option A, the schema packet above).

Every function here is PURE: explicit inputs only, no DB reads, no imports of
DB connections. Nothing in the runtime consumes these yet — W4 wires them to
the cancel path and REST_ELIGIBLE surface. This module exists now so the
vocabulary and its truth table are locked and tested ahead of that wiring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def is_stale_pending_cancel(
    command_q_version: str | None,
    current_family_q_version: str | None,
    order_open: bool,
) -> bool | None:
    """True iff the order's stamped q_version differs from the family's current
    servable q_version AND the order is open.

    Returns ``None`` (INDETERMINATE), never ``True``, when either side of the
    comparison is unknown:
      - ``command_q_version`` is ``None`` (NULL stamp — reconciliation backfill
        or legacy row; governed by ``rest_deadline_exceeded`` instead, not by
        q-staleness);
      - ``current_family_q_version`` is ``None`` (the family has no current
        servable q — readiness BLOCKED). Callers must fail-closed on
        INDETERMINATE: do not churn cancels on a blind family.

    A closed order is never "stale pending cancel" (there is nothing open to
    cancel), independent of q comparison, so ``order_open=False`` always
    returns ``False``.
    """
    if not order_open:
        return False
    if command_q_version is None or current_family_q_version is None:
        return None
    return command_q_version != current_family_q_version


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


def rest_deadline_exceeded(
    *,
    order_open: bool,
    resting_since: datetime | str,
    now: datetime,
    deadline_minutes: float,
) -> bool:
    """True iff an open rest has aged past the deterministic max-rest-age deadline.

    Applies to ALL open rests regardless of q_version — this predicate, not
    q-staleness, is what retires NULL-q rests (critic ruling 2: closes the
    "NULL-q rest-forever" leak that Option A's stale-only classification would
    have left open). ``deadline_minutes`` is caller-supplied so this stays a
    pure function of its inputs; use ``bootstrap_rest_deadline_minutes()`` for
    the current operating value until W0.2 p99 measurements replace it.
    """
    if not order_open:
        return False
    resting_since_dt = _coerce_datetime(resting_since)
    if resting_since_dt is None:
        return False
    return (now - resting_since_dt) >= timedelta(minutes=deadline_minutes)


def bootstrap_rest_deadline_minutes() -> float:
    """The BOOTSTRAP rest_deadline_exceeded deadline (orchestrator ruling
    2026-07-02): until W0.2 p99 measurements accumulate, bootstrap from the
    incumbent operating value — MAKER_REST_ESCALATION_DEADLINE_MINUTES — so no
    new number is invented and the W4 maker_rest_escalation handover is
    behavior-continuous. Replace with the measured-p99 formula once data
    exists (K1-style fitted boundary, not a habit constant).
    """
    from src.strategy.live_inference.mode_consistent_ev import (
        MAKER_REST_ESCALATION_DEADLINE_MINUTES,
    )

    return float(MAKER_REST_ESCALATION_DEADLINE_MINUTES)
