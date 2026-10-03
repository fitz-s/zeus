# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: docs/operations/current/plans/canonical_execution_lease.md (Part B).
"""Identity-keyed execution lease for replacement-forecast materialization.

One lease per request identity key: ``inflight/leases/<sha256(key)>.lease``,
owned by ``flock(LOCK_EX | LOCK_NB)`` on an open file description (OFD). It is
the only exclusion primitive between materialization owners. A lock belongs to
the OFD, so every descriptor duplicated from it (``SCM_RIGHTS`` to the executing
worker) holds the same lock, and the kernel drops it only when the last such
descriptor closes or its last holder dies. A free lease is therefore a fact (no
owner) and a held one a fact (a live owner); neither is inferred from a clock.

Only an exclusive acquirer may unlink a lease path (``sweep``): a holder whose
OFD was shared cannot know the sharer has closed. An acquirer that locked an
inode the path no longer names lost a race with a sweeper and retries on the
current path. Any other open failure (permission, I/O) is UNKNOWN to the caller
and propagates as ``OSError``; it is never reinterpreted as free.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

LEASE_PROTOCOL = "lease-v1"
LEASE_DIR_NAME = "leases"


class LeaseState(str, Enum):
    """What one observation of a claim's leases found."""

    HELD = "HELD"  # a live owner holds at least one of them
    ACQUIRED_FOR_RECOVERY = "ACQUIRED_FOR_RECOVERY"  # all were free; the observer now holds them
    UNKNOWN = "UNKNOWN"  # a lease-v1 claim whose lease state cannot be read; never reclaimed by time
    LEGACY = "LEGACY"  # a claim without the lease protocol (pre-migration)


@dataclass(frozen=True)
class HeldLease:
    path: Path
    fd: int


def lease_name(key: Sequence[object]) -> str:
    """The fixed lease filename of one identity key."""

    canonical = json.dumps(list(key), separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest() + ".lease"


def _acquire(path: Path) -> HeldLease | None:
    """Hold ``path``, or None when another owner holds it; OSError is UNKNOWN."""

    while True:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        except FileNotFoundError:
            continue  # a sweeper removed the empty directory between mkdir and open
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.path.samestat(os.fstat(fd), os.stat(path)):
                return HeldLease(path, fd)
        except BlockingIOError:
            os.close(fd)
            return None
        except FileNotFoundError:
            pass  # swept after our open: retry on the current path
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)


def acquire_all(paths: Iterable[Path]) -> list[HeldLease] | None:
    """Hold every lease or none, in sorted order; any failure releases all.

    None: another owner holds one. ``OSError``: one is UNKNOWN.
    """

    held: list[HeldLease] = []
    try:
        for path in sorted({Path(p) for p in paths}):
            lease = _acquire(path)
            if lease is None:
                release(held)
                return None
            held.append(lease)
    except BaseException:
        release(held)
        raise
    return held


def observe(paths: Iterable[Path]) -> tuple[LeaseState, list[HeldLease]]:
    """HELD, UNKNOWN, or ACQUIRED_FOR_RECOVERY with every lease now held.

    UNKNOWN dominates HELD: one unreadable lease makes the whole claim unknown.
    """

    held: list[HeldLease] = []
    blocked = False
    try:
        for path in sorted({Path(p) for p in paths}):
            lease = _acquire(path)
            if lease is None:
                blocked = True
            else:
                held.append(lease)
    except OSError:
        release(held)
        return LeaseState.UNKNOWN, []
    except BaseException:
        release(held)
        raise
    if blocked:
        release(held)
        return LeaseState.HELD, []
    return LeaseState.ACQUIRED_FOR_RECOVERY, held


def release(leases: Iterable[HeldLease]) -> None:
    """Close every descriptor (never skipped), then sweep the freed paths."""

    paths: list[Path] = []
    for lease in leases:
        paths.append(lease.path)
        try:
            os.close(lease.fd)
        except OSError:
            pass
    sweep(paths)


def sweep(paths: Iterable[Path]) -> None:
    """Unlink each lease path no descriptor anywhere holds; drop an empty directory."""

    parents: set[Path] = set()
    for path in sorted({Path(p) for p in paths}):
        parents.add(path.parent)
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            continue  # absent, or unreadable (UNKNOWN stays on disk)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.path.samestat(os.fstat(fd), os.stat(path)):
                os.unlink(path)
        except OSError:
            pass  # held elsewhere, or already gone
        finally:
            os.close(fd)
    for parent in parents:
        try:
            parent.rmdir()  # only an empty lease directory goes
        except OSError:
            pass
