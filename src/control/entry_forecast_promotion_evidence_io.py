# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md Phase B1 atomic JSON I/O + Phase C-flock concurrent-writer protection for EntryForecastPromotionEvidence.
"""Atomic JSON read/write for ``EntryForecastPromotionEvidence``.

Phase C activation status: read-side wired into ``src/engine/evaluator.py``
behind ``ZEUS_ENTRY_FORECAST_ROLLOUT_GATE`` env flag (default OFF).
Write-side remains operator-script-only — no daemon writer in the
default activation path.

The promotion evidence carries the operator approval, G1 attestation,
calibration promotion approval, and canary-success attestation that
``evaluate_entry_forecast_rollout_gate`` requires before authorizing
canary or live entry-forecast orders. Storage on disk so an operator
script can populate it atomically without taking the daemon down.

Concurrent-writer protection: writes hold an exclusive ``fcntl.flock``
on a sidecar lock file at ``<path>.lock`` for the duration of the
write. Atomic JSON via tempfile + ``os.replace`` already prevents the
reader from observing a partial file; the flock prevents two
concurrent writers from racing and clobbering each other's payloads.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
from src.data.live_entry_status import LiveEntryForecastStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMOTION_EVIDENCE_PATH = PROJECT_ROOT / "state" / "entry_forecast_promotion_evidence.json"

PROMOTION_EVIDENCE_SCHEMA_VERSION = 1


class PromotionEvidenceCorruption(ValueError):
    """Raised when the on-disk promotion evidence file fails strict parsing.

    The caller is expected to treat this as ``EVIDENCE_MISSING`` for
    rollout-gate purposes — never as ``EVIDENCE_PRESENT_AND_VALID``.
    """


@contextlib.contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive ``fcntl.flock`` on ``<path>.lock`` for write coordination.

    Uses a sidecar ``.lock`` file rather than locking the target file
    directly because the target is replaced (different inode) on each
    write. Lock-file inode is stable across writes.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(path):
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def _serialize_status(status: LiveEntryForecastStatus) -> dict[str, Any]:
    return status.to_dict()


def _deserialize_status(raw: object) -> LiveEntryForecastStatus:
    if not isinstance(raw, dict):
        raise PromotionEvidenceCorruption(
            "status_snapshot must be a dict; got " + type(raw).__name__
        )
    required = {
        "status",
        "blockers",
        "executable_row_count",
        "producer_readiness_count",
        "producer_live_eligible_count",
    }
    missing = required - set(raw)
    if missing:
        raise PromotionEvidenceCorruption(
            "status_snapshot missing fields: " + ", ".join(sorted(missing))
        )
    if not isinstance(raw["status"], str) or not raw["status"]:
        raise PromotionEvidenceCorruption("status_snapshot.status must be non-empty string")
    blockers = raw["blockers"]
    if not isinstance(blockers, list) or not all(isinstance(b, str) for b in blockers):
        raise PromotionEvidenceCorruption("status_snapshot.blockers must be list[str]")
    for field in ("executable_row_count", "producer_readiness_count", "producer_live_eligible_count"):
        if not isinstance(raw[field], int) or isinstance(raw[field], bool):
            raise PromotionEvidenceCorruption(f"status_snapshot.{field} must be int")
    return LiveEntryForecastStatus(
        status=raw["status"],
        blockers=tuple(blockers),
        executable_row_count=raw["executable_row_count"],
        producer_readiness_count=raw["producer_readiness_count"],
        producer_live_eligible_count=raw["producer_live_eligible_count"],
    )


def write_promotion_evidence(
    evidence: EntryForecastPromotionEvidence,
    *,
    path: Path | None = None,
) -> None:
    target = path or DEFAULT_PROMOTION_EVIDENCE_PATH
    payload = {
        "schema_version": PROMOTION_EVIDENCE_SCHEMA_VERSION,
        "operator_approval_id": evidence.operator_approval_id,
        "g1_evidence_id": evidence.g1_evidence_id,
        "calibration_promotion_approved": evidence.calibration_promotion_approved,
        "canary_success_evidence_id": evidence.canary_success_evidence_id,
        "status_snapshot": _serialize_status(evidence.status_snapshot),
    }
    _atomic_write_json(target, payload)


@functools.lru_cache(maxsize=4)
def _parse_evidence_payload_cached(
    path_str: str,
    _mtime_ns: int,  # cache-key-only; not consumed in body
    _size: int,  # cache-key-only; not consumed in body
    _ino: int,  # cache-key-only; catches atomic-write inode rotation (PR47 codex P2)
    _ctime_ns: int,  # cache-key-only; metadata-change timestamp; defence-in-depth (PR47 codex P2)
) -> EntryForecastPromotionEvidence | None:
    """Strict-parse a promotion-evidence payload.

    Phase C-perf-cache (critic ATTACK 3 follow-up): cache keyed by
    ``(path, mtime_ns, size, ino, ctime_ns)`` so the daemon does not
    re-parse the file 200×/cycle when both
    ``ZEUS_ENTRY_FORECAST_ROLLOUT_GATE`` and
    ``ZEUS_ENTRY_FORECAST_READINESS_WRITER`` are ON.

    Cache key strength (2026-05-04, codex P2 follow-up on PR #47):
    keying on ``(mtime_ns, size)`` alone allowed stale entries to
    survive a same-length rewrite when the filesystem's mtime
    granularity didn't advance within the write window — e.g. an
    operator rewriting evidence with the same byte count back-to-back.
    Inode (``st_ino``) and metadata-change time (``st_ctime_ns``) are
    now part of the key:

    - ``write_promotion_evidence`` always writes via tempfile +
      ``os.replace``. Each write produces a new inode, so any rewrite
      mutates ``st_ino`` regardless of mtime resolution. This is the
      load-bearing invariant.
    - ``st_ctime_ns`` advances on metadata-only updates (touch, chmod,
      attribute change). Defence in depth — even non-overwrite mutations
      that legitimately should invalidate the cache flush it.

    ``functools.lru_cache`` does not cache exceptions, so corruption
    re-runs the parse every call (acceptable: corrupt files are rare
    and re-parsing produces consistent error messages rather than
    caching a stale exception object).

    **stat/read_text race note**: ``read_promotion_evidence`` calls
    ``target.stat()`` and then this function reads ``target.read_text()``
    via separate syscalls. If ``os.replace`` swaps the inode between
    the two calls, the reader sees the NEW content but caches under
    the OLD key. With ``st_ino`` in the key, subsequent readers stat
    the new inode, miss the cache, and re-parse. The "wrong-key" entry
    cannot re-collide; it occupies one of the four LRU slots until
    evicted. No production failure mode.
    """

    target = Path(path_str)
    try:
        raw = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        raise PromotionEvidenceCorruption(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise PromotionEvidenceCorruption("payload root must be object")
    schema = raw.get("schema_version")
    if schema != PROMOTION_EVIDENCE_SCHEMA_VERSION:
        raise PromotionEvidenceCorruption(
            f"unsupported schema_version={schema!r}; "
            f"expected {PROMOTION_EVIDENCE_SCHEMA_VERSION}"
        )
    for nullable in ("operator_approval_id", "g1_evidence_id", "canary_success_evidence_id"):
        value = raw.get(nullable)
        if value is not None and not isinstance(value, str):
            raise PromotionEvidenceCorruption(f"{nullable} must be string or null")
    approved = raw.get("calibration_promotion_approved")
    if not isinstance(approved, bool):
        raise PromotionEvidenceCorruption(
            "calibration_promotion_approved must be bool (no truthy coercion)"
        )
    return EntryForecastPromotionEvidence(
        operator_approval_id=raw.get("operator_approval_id"),
        g1_evidence_id=raw.get("g1_evidence_id"),
        status_snapshot=_deserialize_status(raw.get("status_snapshot")),
        calibration_promotion_approved=approved,
        canary_success_evidence_id=raw.get("canary_success_evidence_id"),
    )


def read_promotion_evidence(
    *,
    path: Path | None = None,
) -> EntryForecastPromotionEvidence | None:
    """Return parsed promotion evidence, or ``None`` if the file is absent.

    Strict parsing: any structural defect raises
    :class:`PromotionEvidenceCorruption`. Callers must treat corruption
    as ``EVIDENCE_MISSING`` for rollout-gate purposes — never silently
    accept a malformed payload as valid evidence.

    Phase C-perf-cache (critic ATTACK 3 follow-up): the parse is cached
    by ``(path, mtime_ns, size, ino, ctime_ns)``. The cache invalidates
    automatically on file change — including same-length rewrites where
    mtime granularity does not advance, because ``os.replace`` rotates
    ``st_ino`` on every atomic write. Successful parses are cached up
    to LRU(maxsize=4); corruption raises and is not cached (re-parses
    on every call).
    """

    target = path or DEFAULT_PROMOTION_EVIDENCE_PATH
    try:
        stat = target.stat()
    except FileNotFoundError:
        return None
    return _parse_evidence_payload_cached(
        str(target),
        stat.st_mtime_ns,
        stat.st_size,
        stat.st_ino,
        stat.st_ctime_ns,
    )


def clear_evidence_read_cache() -> None:
    """Drop the lru_cache backing :func:`read_promotion_evidence`.

    Test fixtures use this between assertions so a cached parse from a
    previous test does not leak into the next.
    """

    _parse_evidence_payload_cached.cache_clear()


def evidence_to_dict(evidence: EntryForecastPromotionEvidence) -> dict[str, Any]:
    """Serialize for log/audit purposes only — not the on-disk format."""

    payload = asdict(evidence)
    payload["status_snapshot"] = evidence.status_snapshot.to_dict()
    return payload
