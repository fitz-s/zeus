"""Content identity of one regular file, re-hashed only when its file version moves.

SCOPE: one path. A version is (st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns):
any content write moves mtime/size and ctime too, which no utime() can restore, and
an atomic rename moves the inode, so equal versions mean equal bytes. DRAIN: a changed
version replaces its path's entry. RESET: none needed; entries are facts about
versions. Extracted from openmeteo_model_surface (e8df31c81) so the surface asset
check and the seed input identity share one implementation.
"""

# Created: 2026-10-01
# Last reused/audited: 2026-10-04
# Authority basis: merge-safety round 3 Q1 (seed identity = consumed bytes); e8df31c81.

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat


def file_version(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class UnsafeFile(ValueError):
    """``NOT_REGULAR``: not a regular file. ``SIZE``: over the bound or torn by a writer."""


@dataclass(frozen=True)
class VersionedRead:
    version: tuple[int, ...]
    sha256: str
    # None on a memo hit of a digest-only reader.
    body: bytes | None
    # False when a writer moved the version during the read: these bytes belong to
    # no single version and were not memoized.
    settled: bool


class VersionedFileReader:
    """path -> (version, sha256, body or None); one entry per path.

    ``keep_bodies`` serves callers that consume the bytes (decode); digest-only
    callers keep no bodies. ``max_paths`` bounds a memo whose paths churn: the
    oldest entry is evicted, costing only a re-hash.
    """

    def __init__(self, *, max_bytes: int, keep_bodies: bool, max_paths: int | None = None) -> None:
        self._max_bytes = max_bytes
        self._keep_bodies = keep_bodies
        self._max_paths = max_paths
        self._memo: dict[str, tuple[tuple[int, ...], str, bytes | None]] = {}

    def clear(self) -> None:
        self._memo.clear()

    def read(self, path: Path) -> VersionedRead:
        """Open without following a symlink; OSError (absent, permission, I/O) propagates.

        O_NONBLOCK: a FIFO opens at once instead of waiting for a writer, so it
        is classified NOT_REGULAR by fstat before any read. Blocking mode is
        restored before reading a regular file.
        """
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise UnsafeFile("NOT_REGULAR")
            os.set_blocking(fd, True)
        except BaseException:
            os.close(fd)
            raise
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if info.st_size > self._max_bytes:
                raise UnsafeFile("SIZE")
            version = file_version(info)
            known = self._memo.get(str(path))
            if known is not None and known[0] == version:
                return VersionedRead(version, known[1], known[2], True)
            body = handle.read(self._max_bytes + 1)
            settled = file_version(os.fstat(handle.fileno())) == version
        if len(body) != info.st_size or len(body) > self._max_bytes:
            raise UnsafeFile("SIZE")
        digest = _sha256(body)
        if settled:
            self._memo.pop(str(path), None)
            if self._max_paths is not None and len(self._memo) >= self._max_paths:
                self._memo.pop(next(iter(self._memo)))
            self._memo[str(path)] = (version, digest, body if self._keep_bodies else None)
        return VersionedRead(version, digest, body, settled)
