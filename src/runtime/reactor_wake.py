"""Best-effort cross-process wake hint for the durable event reactor."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REACTOR_WAKE_FILENAME = "edli-reactor-wake.json"


@dataclass(frozen=True)
class ReactorWake:
    wake_id: str
    published_at: str
    source: str
    reason: str


def _wake_path(path: Path | None) -> Path:
    if path is not None:
        return Path(path)
    from src.config import state_path

    return state_path(REACTOR_WAKE_FILENAME)


def publish_reactor_wake(
    *,
    source: str,
    reason: str,
    path: Path | None = None,
    wake_id: str | None = None,
    published_at: datetime | None = None,
) -> ReactorWake:
    """Atomically publish a non-authoritative wake hint after durable truth commits."""

    clean_source = str(source or "").strip()
    clean_reason = str(reason or "").strip()
    if not clean_source or not clean_reason:
        raise ValueError("reactor wake source and reason are required")
    wake = ReactorWake(
        wake_id=str(wake_id or uuid.uuid4().hex),
        published_at=(published_at or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .isoformat(),
        source=clean_source,
        reason=clean_reason,
    )
    target = _wake_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.{wake.wake_id}.tmp")
    try:
        temp.write_text(
            json.dumps(wake.__dict__, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return wake


def read_reactor_wake(*, path: Path | None = None) -> ReactorWake | None:
    """Read the latest complete wake hint; malformed or absent hints are ignored."""

    try:
        payload = json.loads(_wake_path(path).read_text(encoding="utf-8"))
        wake = ReactorWake(
            wake_id=str(payload["wake_id"]).strip(),
            published_at=str(payload["published_at"]).strip(),
            source=str(payload["source"]).strip(),
            reason=str(payload["reason"]).strip(),
        )
    except (FileNotFoundError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not all((wake.wake_id, wake.published_at, wake.source, wake.reason)):
        return None
    return wake


def reactor_wake_revision(
    *, path: Path | None = None
) -> tuple[int, int, int] | None:
    """Return a cheap revision for detecting atomic wake-file replacement."""

    try:
        stat = _wake_path(path).stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size
