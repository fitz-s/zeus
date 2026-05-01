# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: live-blockers session 2026-05-01 — harden auto_pause to
#                  prevent permanent lock-out on transient failures.
"""Auto-pause streak counter.

The cycle entry path can raise transient exceptions (API hiccups, brief DB
contention, websocket reconnect timing). A single such failure should NOT
permanently pause entries. This module records a sliding-window streak of
consecutive failures keyed by ``reason_code``; only when the same reason
fires N=3 times within a 5-minute window does the cycle escalate to
``pause_entries``.

State lives in ``state/auto_pause_streak.json`` (atomic write via tmp +
``os.replace``). The file is small and per-process; concurrent writers are
not a concern for the current single-daemon arrangement.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.config import state_path

logger = logging.getLogger(__name__)

STREAK_FILE = "auto_pause_streak.json"
STREAK_THRESHOLD = 3
STREAK_WINDOW_SECONDS = 300  # 5 minutes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _streak_path():
    return state_path(STREAK_FILE)


def _read_raw() -> dict:
    try:
        with open(_streak_path()) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _write_raw(data: dict) -> None:
    path = _streak_path()
    try:
        os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    except OSError:
        # Already exists or unwritable — let the open() below surface the real error.
        pass
    tmp = str(path) + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, str(path))
    except OSError as exc:
        logger.error("auto_pause_streak: failed to persist streak file: %s", exc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_streak() -> dict:
    """Return the current streak record (best-effort)."""
    return _read_raw()


def record_failure(reason_code: str, *, now: Optional[datetime] = None) -> int:
    """Record a failure for ``reason_code`` and return the new streak count.

    If the previous failure was for the same ``reason_code`` AND occurred
    within ``STREAK_WINDOW_SECONDS`` of ``now``, the count increments.
    Otherwise the streak resets to 1.
    """
    current = now or _now()
    raw = _read_raw()
    last_reason = str(raw.get("reason_code") or "")
    last_seen = _parse_iso(raw.get("last_seen_at"))
    count = int(raw.get("count") or 0)
    same_reason = last_reason == reason_code
    in_window = (
        last_seen is not None
        and (current - last_seen) <= timedelta(seconds=STREAK_WINDOW_SECONDS)
    )
    if same_reason and in_window and count > 0:
        count += 1
        first_seen = raw.get("first_seen_at") or current.isoformat()
    else:
        count = 1
        first_seen = current.isoformat()
    payload = {
        "reason_code": reason_code,
        "count": count,
        "first_seen_at": first_seen,
        "last_seen_at": current.isoformat(),
        "threshold": STREAK_THRESHOLD,
        "window_seconds": STREAK_WINDOW_SECONDS,
    }
    _write_raw(payload)
    return count


def clear_streak() -> None:
    """Reset the streak after a successful entry path completion.

    Removes the streak file. If the file does not exist this is a no-op.
    """
    path = _streak_path()
    try:
        os.remove(str(path))
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("auto_pause_streak: failed to clear streak file: %s", exc)


def threshold_reached(count: int) -> bool:
    return count >= STREAK_THRESHOLD
