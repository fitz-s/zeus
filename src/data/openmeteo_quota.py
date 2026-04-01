"""Shared Open-Meteo free-tier quota tracker."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DAILY_LIMIT = 10_000
WARN_THRESHOLD = 0.80
HARD_THRESHOLD = 0.95
RATE_LIMIT_COOLDOWN_SECONDS = 5 * 60


class OpenMeteoQuotaTracker:
    """Process-local daily quota tracker with UTC midnight reset."""

    def __init__(self) -> None:
        self._count = 0
        self._today = self._utc_today()
        self._blocked_until: datetime | None = None

    @staticmethod
    def _utc_today() -> date:
        return datetime.now(timezone.utc).date()

    def _check_reset(self) -> None:
        today = self._utc_today()
        if today != self._today:
            self._count = 0
            self._today = today

    def can_call(self) -> bool:
        self._check_reset()
        now = datetime.now(timezone.utc)
        if self._blocked_until is not None and now < self._blocked_until:
            logger.warning(
                "Open-Meteo temporarily blocked after 429 until %s",
                self._blocked_until.isoformat(),
            )
            return False
        if self._count >= int(DAILY_LIMIT * HARD_THRESHOLD):
            logger.critical(
                "Open-Meteo quota exhausted: %d/%d calls today (hard block at %d)",
                self._count,
                DAILY_LIMIT,
                int(DAILY_LIMIT * HARD_THRESHOLD),
            )
            return False
        return True

    def note_rate_limited(self, retry_after_seconds: int | float | None = None) -> None:
        cooldown = max(
            RATE_LIMIT_COOLDOWN_SECONDS,
            int(retry_after_seconds or 0),
        )
        self._blocked_until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=cooldown)
        logger.warning(
            "Open-Meteo 429 cooldown engaged for %ds until %s",
            cooldown,
            self._blocked_until.isoformat(),
        )

    def record_call(self, endpoint: str = "") -> None:
        self._check_reset()
        self._count += 1
        usage = self._count / DAILY_LIMIT
        suffix = f" [{endpoint}]" if endpoint else ""
        if usage >= HARD_THRESHOLD:
            logger.critical(
                "Open-Meteo quota CRITICAL%s: %d/%d (%.1f%%)",
                suffix,
                self._count,
                DAILY_LIMIT,
                usage * 100.0,
            )
        elif usage >= WARN_THRESHOLD:
            logger.warning(
                "Open-Meteo quota WARNING%s: %d/%d (%.1f%%)",
                suffix,
                self._count,
                DAILY_LIMIT,
                usage * 100.0,
            )

    def calls_today(self) -> int:
        self._check_reset()
        return self._count

    def calls_remaining(self) -> int:
        self._check_reset()
        hard_cap = int(DAILY_LIMIT * HARD_THRESHOLD)
        return max(0, hard_cap - self._count)

    def cooldown_remaining_seconds(self) -> int:
        if self._blocked_until is None:
            return 0
        remaining = int((self._blocked_until - datetime.now(timezone.utc)).total_seconds())
        return max(0, remaining)


quota_tracker = OpenMeteoQuotaTracker()
