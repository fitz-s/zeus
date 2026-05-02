# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M3.yaml
"""M3 user-channel WebSocket submit guard.

The user WebSocket is venue truth for real-time order/trade activity.  A gap is
therefore a fail-closed condition for new submit: Zeus may continue monitor,
exit, and reconciliation work, but it must not submit new venue commands until a
future M5 reconciliation sweep provides recovery evidence.

This module is intentionally tiny and in-memory for M3.  It mirrors the shape
of heartbeat/cutover guards so executor and cycle_runner have one deterministic
read path while the ingestor owns external socket I/O.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Literal, Optional

SubscriptionState = Literal[
    "AUTHED",
    "SUBSCRIBED",
    "DISCONNECTED",
    "AUTH_FAILED",
    "MARKET_MISMATCH",
]

DEFAULT_STALE_AFTER_SECONDS = 30


class WSGapSubmitBlocked(RuntimeError):
    """Raised before submit when user-channel truth is gapped."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WSGapStatus:
    connected: bool = False
    last_message_at: Optional[datetime] = None
    consecutive_gaps: int = 0
    subscription_state: SubscriptionState = "DISCONNECTED"
    gap_reason: str = "not_started"
    m5_reconcile_required: bool = False
    affected_markets: tuple[str, ...] = field(default_factory=tuple)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS

    def is_stale(self, *, now: datetime | None = None) -> bool:
        if not self.m5_reconcile_required and self.gap_reason == "test_clear":
            return False
        if self.last_message_at is None:
            return self.subscription_state in {"DISCONNECTED", "AUTH_FAILED", "MARKET_MISMATCH"}
        now = now or datetime.now(timezone.utc)
        last = self.last_message_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (now - last).total_seconds() > self.stale_after_seconds

    def blocks_market(self, market_id: str | None = None, *, now: datetime | None = None) -> bool:
        if not self.m5_reconcile_required and self.subscription_state in {"AUTHED", "SUBSCRIBED"} and not self.is_stale(now=now):
            return False
        if self.m5_reconcile_required:
            return True
        if self.subscription_state == "MARKET_MISMATCH":
            return True
        if self.affected_markets and market_id:
            return str(market_id) in set(self.affected_markets)
        return True

    def to_summary(self, *, now: datetime | None = None) -> dict:
        stale = self.is_stale(now=now)
        allow_submit = not self.blocks_market(now=now)
        return {
            "connected": self.connected,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "consecutive_gaps": self.consecutive_gaps,
            "subscription_state": self.subscription_state,
            "gap_reason": self.gap_reason,
            "m5_reconcile_required": self.m5_reconcile_required,
            "affected_markets": list(self.affected_markets),
            "updated_at": self.updated_at.isoformat(),
            "stale_after_seconds": self.stale_after_seconds,
            "stale": stale,
            "entry": {"allow_submit": allow_submit},
        }


_status = WSGapStatus(
    connected=False,
    last_message_at=None,
    subscription_state="DISCONNECTED",
    gap_reason="not_configured",
    m5_reconcile_required=True,
    updated_at=_utcnow(),
)


def configure_status(status: WSGapStatus) -> WSGapStatus:
    _assert_test_runtime("configure_status")
    global _status
    _status = status
    return _status


def status() -> WSGapStatus:
    return _status


def summary(*, now: datetime | None = None) -> dict:
    return _materialize_stale_gap(now=now).to_summary(now=now)


def _materialize_stale_gap(*, now: datetime | None = None) -> WSGapStatus:
    current = status()
    if current.is_stale(now=now) and not current.m5_reconcile_required:
        return record_gap(
            "stale_last_message",
            subscription_state="DISCONNECTED",
            observed_at=now,
            stale_after_seconds=current.stale_after_seconds,
        )
    return current


def record_message(
    *,
    observed_at: datetime | None = None,
    subscription_state: SubscriptionState = "SUBSCRIBED",
    stale_after_seconds: int | None = None,
) -> WSGapStatus:
    """Mark the user channel as receiving messages.

    This clears transient disconnect/auth state. Historically this function
    deliberately did NOT clear ``m5_reconcile_required`` because true mid-run
    reconnect-after-gap could have missed fills that need REST reconciliation.

    Live-blockers 2026-05-01 fix: that policy combined with the module-init
    value of ``m5_reconcile_required=True`` and the fact that no production
    code path ever sets it to False meant the gate was permanently latched
    closed once a daemon booted — entries were blocked forever even on a
    perfectly healthy SUBSCRIBED stream. The `not_configured` boot state has
    no missed-orders risk because the daemon never had a connection to lose
    messages from, so transitioning OUT of `not_configured` directly into
    SUBSCRIBED safely clears the flag. Genuine mid-run reconnect (where the
    prior gap_reason was a real disconnect) still preserves the flag and
    waits for explicit M5 reconciliation.
    """

    global _status
    now = observed_at or _utcnow()
    prior_m5_required = _status.m5_reconcile_required
    new_m5_required = prior_m5_required
    if (
        prior_m5_required
        and subscription_state == "SUBSCRIBED"
        and _status.gap_reason in {"not_configured", None}
    ):
        new_m5_required = False
    _status = WSGapStatus(
        connected=True,
        last_message_at=now,
        consecutive_gaps=0,
        subscription_state=subscription_state,
        gap_reason="message_received",
        m5_reconcile_required=new_m5_required,
        affected_markets=_status.affected_markets,
        updated_at=now,
        stale_after_seconds=stale_after_seconds or _status.stale_after_seconds,
    )
    return _status


def clear_after_no_local_side_effects(
    *,
    observed_at: datetime | None = None,
    stale_after_seconds: int | None = None,
) -> WSGapStatus:
    """Clear a real reconnect latch after caller-provided empty-journal proof.

    The proof is intentionally outside this module: M3 owns the submit guard,
    not durable exchange reconciliation. Production callers may use this only
    after proving Zeus has no local venue-command/position/finding surface that
    could have missed side effects during the gap. If the channel is not already
    healthy, this remains fail-closed.
    """

    global _status
    now = observed_at or _utcnow()
    current = _status
    if current.subscription_state not in {"AUTHED", "SUBSCRIBED"} or current.is_stale(now=now):
        raise WSGapSubmitBlocked(
            f"cannot clear ws gap without healthy subscription: "
            f"ws_gap={current.subscription_state}:{current.gap_reason}; "
            f"m5_reconcile_required={current.m5_reconcile_required}"
        )
    _status = WSGapStatus(
        connected=True,
        last_message_at=current.last_message_at or now,
        consecutive_gaps=current.consecutive_gaps,
        subscription_state=current.subscription_state,
        gap_reason="message_received_no_local_side_effects",
        m5_reconcile_required=False,
        affected_markets=current.affected_markets,
        updated_at=now,
        stale_after_seconds=stale_after_seconds or current.stale_after_seconds,
    )
    return _status


def record_gap(
    reason: str,
    *,
    subscription_state: SubscriptionState = "DISCONNECTED",
    affected_markets: Iterable[str] | None = None,
    observed_at: datetime | None = None,
    stale_after_seconds: int | None = None,
) -> WSGapStatus:
    global _status
    now = observed_at or _utcnow()
    markets = tuple(str(m) for m in (affected_markets or ()) if str(m))
    _status = WSGapStatus(
        connected=False,
        last_message_at=_status.last_message_at,
        consecutive_gaps=_status.consecutive_gaps + 1,
        subscription_state=subscription_state,
        gap_reason=str(reason),
        m5_reconcile_required=True,
        affected_markets=markets,
        updated_at=now,
        stale_after_seconds=stale_after_seconds or _status.stale_after_seconds,
    )
    return _status


def clear_for_test(*, observed_at: datetime | None = None) -> WSGapStatus:
    """Reset guard state for deterministic unit tests only."""

    _assert_test_runtime("clear_for_test")
    global _status
    now = observed_at or _utcnow()
    _status = WSGapStatus(
        connected=True,
        last_message_at=now,
        consecutive_gaps=0,
        subscription_state="SUBSCRIBED",
        gap_reason="test_clear",
        m5_reconcile_required=False,
        updated_at=now,
    )
    return _status


def _test_runtime_enabled() -> bool:
    return (
        os.environ.get("ZEUS_TESTING") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
        or "pytest" in sys.modules
    )


def _assert_test_runtime(action: str) -> None:
    if not _test_runtime_enabled():
        raise RuntimeError(f"{action} is forbidden outside test runtime")


def assert_ws_allows_submit(market_id: str | None = None) -> None:
    current = _materialize_stale_gap()
    if current.blocks_market(market_id):
        raise WSGapSubmitBlocked(
            f"ws_gap={current.subscription_state}:{current.gap_reason}; "
            f"m5_reconcile_required={current.m5_reconcile_required}"
        )
