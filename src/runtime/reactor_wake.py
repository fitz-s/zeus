"""Best-effort cross-process wake hint for the durable event reactor."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REACTOR_WAKE_FILENAME = "edli-reactor-wake.json"
REACTOR_WAKE_QUEUE_SUFFIX = ".d"


@dataclass(frozen=True)
class ReactorWake:
    wake_id: str
    published_at: str
    source: str
    reason: str
    event_ids: tuple[str, ...] = ()
    forecast_families: tuple[tuple[str, str, str], ...] = ()


def _wake_path(path: Path | None) -> Path:
    if path is not None:
        return Path(path)
    from src.config import state_path

    return state_path(REACTOR_WAKE_FILENAME)


def _wake_queue_dir(path: Path | None) -> Path:
    target = _wake_path(path)
    return target.with_name(f"{target.name}{REACTOR_WAKE_QUEUE_SUFFIX}")


def _clean_forecast_families(
    values: object,
) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    families: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in values:
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            continue
        family = (
            str(raw[0] or "").strip(),
            str(raw[1] or "").strip(),
            str(raw[2] or "").strip(),
        )
        if not all(family) or family in seen:
            continue
        seen.add(family)
        families.append(family)
        if len(families) == 100:
            break
    return tuple(families)


def publish_reactor_wake(
    *,
    source: str,
    reason: str,
    path: Path | None = None,
    wake_id: str | None = None,
    published_at: datetime | None = None,
    event_ids: tuple[str, ...] = (),
    forecast_families: tuple[tuple[str, str, str], ...] = (),
) -> ReactorWake:
    """Atomically publish a non-authoritative wake hint after durable truth commits."""

    clean_source = str(source or "").strip()
    clean_reason = str(reason or "").strip()
    if not clean_source or not clean_reason:
        raise ValueError("reactor wake source and reason are required")
    clean_event_ids = tuple(
        dict.fromkeys(
            event_id
            for raw_event_id in event_ids
            if (event_id := str(raw_event_id or "").strip())
        )
    )[:100]
    clean_forecast_families = _clean_forecast_families(forecast_families)
    wake = ReactorWake(
        wake_id=str(wake_id or uuid.uuid4().hex),
        published_at=(published_at or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .isoformat(),
        source=clean_source,
        reason=clean_reason,
        event_ids=clean_event_ids,
        forecast_families=clean_forecast_families,
    )
    target = _wake_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    queue_dir = _wake_queue_dir(path)
    queue_dir.mkdir(parents=True, exist_ok=True)
    published_us = int(
        datetime.fromisoformat(wake.published_at.replace("Z", "+00:00")).timestamp()
        * 1_000_000
    )
    queue_target = queue_dir / f"{published_us:020d}-{wake.wake_id}.json"
    _atomic_write_wake(queue_target, wake)
    _atomic_write_wake(target, wake)
    return wake


def _atomic_write_wake(target: Path, wake: ReactorWake) -> None:
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


def _read_reactor_wake_path(path: Path) -> ReactorWake | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        wake = ReactorWake(
            wake_id=str(payload["wake_id"]).strip(),
            published_at=str(payload["published_at"]).strip(),
            source=str(payload["source"]).strip(),
            reason=str(payload["reason"]).strip(),
            event_ids=tuple(
                str(event_id or "").strip()
                for event_id in payload.get("event_ids", ())
                if str(event_id or "").strip()
            )[:100],
            forecast_families=_clean_forecast_families(
                payload.get("forecast_families", ())
            ),
        )
    except (FileNotFoundError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not all((wake.wake_id, wake.published_at, wake.source, wake.reason)):
        return None
    return wake


def read_reactor_wake(*, path: Path | None = None) -> ReactorWake | None:
    """Read the oldest queued wake, falling back to the legacy latest-wake file."""

    try:
        queue_files = sorted(_wake_queue_dir(path).glob("*.json"))
    except OSError:
        queue_files = []
    for queue_file in queue_files:
        wake = _read_reactor_wake_path(queue_file)
        if wake is not None:
            return wake
    return _read_reactor_wake_path(_wake_path(path))


def acknowledge_reactor_wake(
    wake: ReactorWake,
    *,
    path: Path | None = None,
) -> bool:
    """Remove one consumed queue entry and its matching legacy fallback."""

    try:
        suffix = f"-{wake.wake_id}.json"
        for queue_file in _wake_queue_dir(path).glob("*.json"):
            if queue_file.name.endswith(suffix):
                queue_file.unlink(missing_ok=True)
        legacy = _wake_path(path)
        latest = _read_reactor_wake_path(legacy)
        if latest is not None and latest.wake_id == wake.wake_id:
            legacy.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def reactor_wake_revision(
    *, path: Path | None = None
) -> tuple[int, int, int] | None:
    """Return a cheap revision for detecting atomic wake-file replacement."""

    try:
        stat = _wake_path(path).stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size
