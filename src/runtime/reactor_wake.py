"""Best-effort cross-process wake hint for the durable event reactor."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Collection, Iterator

REACTOR_WAKE_FILENAME = "edli-reactor-wake.json"
REACTOR_WAKE_QUEUE_SUFFIX = ".d"
REACTOR_WAKE_SOCKET_SUFFIX = ".sock"
REACTOR_URGENT_WAKE_SUFFIX = ".urgent"
URGENT_WAKE_REASONS = frozenset(
    {
        "day0_extreme_event_committed",
        "forecast_posterior_advanced",
        "market_price_advanced",
    }
)


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


def _wake_socket_path(path: Path | None) -> Path:
    target = _wake_path(path)
    socket_path = target.with_name(f"{target.name}{REACTOR_WAKE_SOCKET_SUFFIX}")
    if len(os.fsencode(socket_path)) <= 100:
        return socket_path
    digest = hashlib.sha256(os.fsencode(target)).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"zeus-reactor-wake-{digest}.sock"


def _urgent_wake_path(path: Path | None) -> Path:
    target = _wake_path(path)
    return target.with_name(f"{target.name}{REACTOR_URGENT_WAKE_SUFFIX}")


def _notify_reactor_wake(path: Path | None) -> None:
    """Best-effort latency signal; the durable queue remains the authority."""

    notifier: socket.socket | None = None
    try:
        notifier = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        notifier.setblocking(False)
        notifier.sendto(b"\x01", str(_wake_socket_path(path)))
    except OSError:
        pass
    finally:
        if notifier is not None:
            notifier.close()


def _reactor_wake_socket_live(path: Path) -> bool:
    probe: socket.socket | None = None
    try:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        probe.connect(str(path))
        probe.send(b"\x00")
        return True
    except OSError:
        return False
    finally:
        if probe is not None:
            probe.close()


@contextmanager
def reactor_wake_listener_socket(
    *, path: Path | None = None
) -> Iterator[socket.socket | None]:
    """Own the local notifier socket, or yield None when another listener does."""

    target = _wake_socket_path(path)
    listener: socket.socket | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _reactor_wake_socket_live(target):
                yield None
                return
            target.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        listener.bind(str(target))
    except OSError:
        if listener is not None:
            listener.close()
        yield None
        return

    assert listener is not None
    bound_inode: int | None = None
    try:
        bound_inode = target.stat().st_ino
        yield listener
    finally:
        listener.close()
        try:
            if bound_inode is not None and target.stat().st_ino == bound_inode:
                target.unlink(missing_ok=True)
        except OSError:
            pass


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
    queue_target = _wake_queue_target(wake, path=path)
    _atomic_write_wake(queue_target, wake)
    _atomic_write_wake(target, wake)
    if wake.reason in URGENT_WAKE_REASONS:
        _atomic_write_wake(_urgent_wake_path(path), wake)
    _notify_reactor_wake(path)
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


def _wake_queue_target(wake: ReactorWake, *, path: Path | None) -> Path:
    published_us = int(
        datetime.fromisoformat(wake.published_at.replace("Z", "+00:00")).timestamp()
        * 1_000_000
    )
    return _wake_queue_dir(path) / f"{published_us:020d}-{wake.wake_id}.json"


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


def _queued_wakes(path: Path | None) -> list[tuple[Path, ReactorWake]]:
    try:
        queue_files = sorted(_wake_queue_dir(path).glob("*.json"))
    except OSError:
        return []
    return [
        (queue_file, wake)
        for queue_file in queue_files
        if (wake := _read_reactor_wake_path(queue_file)) is not None
    ]


def read_reactor_wake(
    *,
    path: Path | None = None,
    exclude_wake_ids: Collection[str] = (),
) -> ReactorWake | None:
    """Read the queued fact with the shortest alpha clock first.

    Day0 observations and executable-book changes can reverse value in
    milliseconds, while a fresh forecast usually has a longer but still finite
    reaction window. Maintenance and other ordinary hints cannot stand ahead
    of those facts. Forecast hints carry incremental family scopes; selecting
    the newest hint first does not lose older scopes because same-reason wakes
    are coalesced and acknowledgement remains exact.
    """

    excluded = {str(wake_id) for wake_id in exclude_wake_ids}
    queued = [
        item for item in _queued_wakes(path) if item[1].wake_id not in excluded
    ]
    priority_reasons = (
        "day0_extreme_event_committed",
        "market_price_advanced",
    )
    for reason in priority_reasons:
        reason_wakes = reversed(queued) if reason == "day0_extreme_event_committed" else queued
        for _queue_file, wake in reason_wakes:
            if wake.reason == reason:
                return wake
    for _queue_file, wake in reversed(queued):
        if wake.reason == "forecast_posterior_advanced":
            return wake
    for _queue_file, wake in queued:
        return wake
    legacy = _read_reactor_wake_path(_wake_path(path))
    if legacy is not None and legacy.wake_id not in excluded:
        return legacy
    return None


def coalescible_reactor_wakes(
    selected: ReactorWake,
    *,
    path: Path | None = None,
    max_wakes: int = 100,
    max_event_ids: int = 100,
    max_forecast_families: int = 100,
) -> tuple[ReactorWake, ...]:
    """Collect same-reason wake hints that one targeted reactor drain can serve.

    A Day0 commit is one preemptible alpha unit. Combining it with older
    observation wakes can put the newest hard fact behind more event IDs than
    one reactor cycle can process. The durable event queue remains the recovery
    authority, so serve the newest Day0 wake alone and leave older hints queued.
    """

    if selected.reason == "day0_extreme_event_committed":
        return (selected,)

    queued = [wake for _queue_file, wake in _queued_wakes(path)]
    selected_index = next(
        (
            index
            for index, wake in enumerate(queued)
            if wake.wake_id == selected.wake_id
        ),
        None,
    )
    if selected_index is None or max_wakes <= 1:
        return (selected,)

    candidates: list[ReactorWake] = []
    if selected.reason == "forecast_posterior_advanced":
        candidates = [
            wake
            for wake in queued
            if wake.wake_id != selected.wake_id and wake.reason == selected.reason
        ]
    else:
        for wake in queued[selected_index + 1 :]:
            if wake.reason == "forecast_posterior_advanced":
                continue
            if wake.reason != selected.reason:
                break
            candidates.append(wake)

    wakes = [selected]
    wake_ids = {selected.wake_id}
    event_ids = set(selected.event_ids)
    families = set(selected.forecast_families)
    for wake in candidates:
        if len(wakes) >= max(1, int(max_wakes)) or wake.wake_id in wake_ids:
            continue
        next_event_ids = event_ids | set(wake.event_ids)
        next_families = families | set(wake.forecast_families)
        if (
            len(next_event_ids) > max(1, int(max_event_ids))
            or len(next_families) > max(1, int(max_forecast_families))
        ):
            continue
        wakes.append(wake)
        wake_ids.add(wake.wake_id)
        event_ids = next_event_ids
        families = next_families
    return tuple(wakes)


def acknowledge_reactor_wake(
    wake: ReactorWake,
    *,
    path: Path | None = None,
) -> bool:
    """Remove exactly one consumed wake and its matching legacy fallback."""

    return acknowledge_reactor_wakes((wake,), path=path)


def acknowledge_reactor_wakes(
    wakes: tuple[ReactorWake, ...],
    *,
    path: Path | None = None,
) -> bool:
    """Acknowledge one coalesced reactor drain without rescanning the queue."""

    try:
        wake_ids = {wake.wake_id for wake in wakes}
        for wake in wakes:
            _wake_queue_target(wake, path=path).unlink(missing_ok=True)
        legacy = _wake_path(path)
        latest = _read_reactor_wake_path(legacy)
        if latest is not None and latest.wake_id in wake_ids:
            legacy.unlink(missing_ok=True)
    except (OSError, ValueError):
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


def reactor_urgent_wake_revision(
    *, path: Path | None = None
) -> tuple[int, int, int] | None:
    """Return a cheap revision for inputs whose alpha clock can preempt an epoch."""

    try:
        stat = _urgent_wake_path(path).stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def reactor_urgent_wake_reason(*, path: Path | None = None) -> str | None:
    """Return the reason carried by the current urgent-wake marker."""

    wake = _read_reactor_wake_path(_urgent_wake_path(path))
    return wake.reason if wake is not None else None


def reactor_urgent_wake_identity(
    *, path: Path | None = None
) -> tuple[str, str] | None:
    """Return the wake id and reason from one atomic urgent-marker read."""

    wake = _read_reactor_wake_path(_urgent_wake_path(path))
    return (wake.wake_id, wake.reason) if wake is not None else None
