# Created: 2026-09-29
# Last reused/audited: 2026-09-29
# Authority basis: 2026-09-29 14:32-17:03Z geoblock window (13/13 ENTRY POSTs 403)
"""Host-wide venue order-access state: the one owner of the geoblock fact.

A Polymarket geoblock 403 refuses the host's region for ``POST /order``. On
2026-09-29 heartbeat, reads, cancels and trade facts all kept working while
every order POST was refused, so no side-effect-free call observes this fact;
the order POST itself is the only exact probe.

States: OPEN (entries submit); GEOBLOCKED (the venue refused an order POST for
the host's region; entries short-circuit until ``next_probe_at``); PROBING (one
entry POST was admitted as the probe; its outcome re-arms GEOBLOCKED on a 403
or sets OPEN when the venue accepts an order). Exits and cancels never consult
this state. An unreadable state file reads OPEN: the venue answer, not this
file, is the authority.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VENUE_ACCESS_FILENAME = "venue-access.json"
REASON = "VENUE_ACCESS_GEOBLOCKED"
PROBE_BACKOFF_SECONDS = (60, 120, 240, 300)

_LOCK = threading.Lock()


def _path() -> Path:
    from src.config import state_path

    return state_path(VENUE_ACCESS_FILENAME)


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"state": "OPEN"}
    return payload if isinstance(payload, dict) else {"state": "OPEN"}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n")
    tmp.replace(path)


def egress_evidence(host: str = "clob.polymarket.com") -> dict[str, str]:
    """The local route the host's traffic takes, read from the kernel table."""

    try:
        out = subprocess.run(
            ["route", "-n", "get", host],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return {"host": host, "error": f"{type(exc).__name__}: {exc}"}
    evidence = {"host": host}
    for line in out.splitlines():
        key, _, value = line.strip().partition(":")
        if key in {"route to", "gateway", "interface"}:
            evidence[key.replace(" ", "_")] = value.strip()
    return evidence


def _backoff(failures: int) -> timedelta:
    index = min(max(failures, 1), len(PROBE_BACKOFF_SECONDS)) - 1
    return timedelta(seconds=PROBE_BACKOFF_SECONDS[index])


def record_geoblock(
    detail: str, *, now: datetime | None = None, path: Path | None = None
) -> None:
    """The venue refused an order POST for the host's region."""

    at = _now(now)
    target = path or _path()
    with _LOCK:
        prior = _read(target)
        failures = int(prior.get("consecutive_geoblocks") or 0) + 1
        payload = {
            "state": "GEOBLOCKED",
            "since": prior.get("since") if prior.get("state") != "OPEN" else at.isoformat(),
            "last_geoblock_at": at.isoformat(),
            "consecutive_geoblocks": failures,
            "next_probe_at": (at + _backoff(failures)).isoformat(),
            "detail": str(detail)[:300],
            "egress": egress_evidence(),
        }
        _write(target, payload)
    if prior.get("state") in (None, "OPEN"):
        logger.error(
            "VENUE_ACCESS OPEN->GEOBLOCKED: entries short-circuit until %s; egress=%s",
            payload["next_probe_at"],
            payload["egress"],
        )


def record_order_accepted(*, now: datetime | None = None, path: Path | None = None) -> None:
    """The venue accepted an order POST: the host has order access."""

    target = path or _path()
    with _LOCK:
        prior = _read(target)
        if prior.get("state") in (None, "OPEN"):
            return
        at = _now(now)
        payload = {
            "state": "OPEN",
            "opened_at": at.isoformat(),
            "prior_since": prior.get("since"),
            "egress": egress_evidence(),
        }
        _write(target, payload)
    logger.warning(
        "VENUE_ACCESS %s->OPEN after geoblock since %s; egress=%s",
        prior.get("state"),
        prior.get("since"),
        payload["egress"],
    )


def entry_block_reason(*, now: datetime | None = None, path: Path | None = None) -> str | None:
    """Why entries may not submit now, or None. Read-only: never claims a probe."""

    payload = _read(path or _path())
    if payload.get("state") in (None, "OPEN"):
        return None
    try:
        due = datetime.fromisoformat(str(payload.get("next_probe_at")))
    except ValueError:
        return None
    if _now(now) >= due:
        return None
    return f"{REASON}:since={payload.get('since')}:next_probe_at={payload.get('next_probe_at')}"


def claim_entry_submit(*, now: datetime | None = None, path: Path | None = None) -> str | None:
    """Admit an entry POST, or return the block reason.

    While not OPEN, the first admitted POST after ``next_probe_at`` is the probe;
    claiming it pushes ``next_probe_at`` forward so one probe runs per interval.
    """

    target = path or _path()
    at = _now(now)
    with _LOCK:
        payload = _read(target)
        if payload.get("state") in (None, "OPEN"):
            return None
        reason = entry_block_reason(now=at, path=target)
        if reason is not None:
            return reason
        failures = int(payload.get("consecutive_geoblocks") or 1)
        payload.update(
            state="PROBING",
            probe_claimed_at=at.isoformat(),
            next_probe_at=(at + _backoff(failures)).isoformat(),
        )
        _write(target, payload)
    return None


def summary(*, path: Path | None = None) -> dict[str, Any]:
    payload = _read(path or _path())
    payload.setdefault("state", "OPEN")
    payload["entry_block_reason"] = entry_block_reason(path=path)
    return payload
