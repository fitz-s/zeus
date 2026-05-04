# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A2 + D-10 (storage path centralization, ZEUS_STORAGE_ROOT default REPO_ROOT, atomic writer + heartbeat).
"""Central storage path resolution and atomic-write helpers for Zeus.

Background
----------
Zeus has at least 5 independent ``_atomic_write_json`` private helpers
(``src/state/portfolio.py``, ``src/control/cutover_guard.py``,
``src/control/entry_forecast_promotion_evidence_io.py``,
``src/data/station_migration_probe.py``, ``scripts/source_contract_auto_convert.py``)
plus several callsites with hardcoded ``Path(__file__).resolve().parent.parent.parent / "data"``
pattern (e.g. ``src/strategy/oracle_penalty.py``,
``scripts/bridge_oracle_to_calibration.py``,
``scripts/oracle_snapshot_listener.py``). The duplication is a known-bug
class: when the listener writes to one path and the reader looks at another,
the daemon silently sees stale (or empty) data — exactly the failure mode
PR #40's emergency oracle-gate-removal is currently masking.

This module is the single locus for:

- Storage-root resolution via ``ZEUS_STORAGE_ROOT`` env override
  (default = repo root via ``__file__`` traversal).
- Path builders for all storage artifacts (oracle error rates, oracle
  shadow snapshots, heartbeat).
- ``write_json_atomic`` — public atomic JSON writer with checksum +
  writer-identity metadata.
- ``write_heartbeat`` — paired heartbeat record for stale-artifact detection.

Path builders are FUNCTIONS, not module-level constants. Each call re-reads
``ZEUS_STORAGE_ROOT`` from the environment, so tests can flip the env mid-
process (via ``monkeypatch.setenv``) without the import-time-capture trap
that bites global constants.

Migration scope (this packet, A2): oracle_penalty + bridge + listener move
to the path builders. The 5 private ``_atomic_write_json`` copies stay
private for now — migrating them is doc-rot risk we'll batch when each
caller's surface gets touched for substantive reasons (separate from this
storage cutover).
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
"""The repo root, derived from this file's location. Immutable across the
process lifetime; not affected by ``ZEUS_STORAGE_ROOT``."""


_ENV_VAR = "ZEUS_STORAGE_ROOT"


def storage_root() -> Path:
    """Return the active storage root.

    - If ``ZEUS_STORAGE_ROOT`` env is set and non-empty, return its
      resolved absolute path.
    - Otherwise return ``REPO_ROOT``.

    Re-reads the env on every call. Tests use ``monkeypatch.setenv`` to
    redirect storage to an isolated tmpdir.
    """
    override = os.environ.get(_ENV_VAR, "").strip()
    if override:
        return Path(override).resolve()
    return REPO_ROOT


# ── artifact path builders ─────────────────────────────────────────── #


def oracle_data_dir() -> Path:
    return storage_root() / "data"


def oracle_error_rates_path() -> Path:
    """Path to ``oracle_error_rates.json``.

    Single writer: ``scripts/bridge_oracle_to_calibration.py``.
    Single reader: ``src/strategy/oracle_penalty.py``.
    """
    return oracle_data_dir() / "oracle_error_rates.json"


def oracle_artifact_heartbeat_path() -> Path:
    """Path to ``oracle_error_rates.heartbeat.json``.

    Written alongside ``oracle_error_rates.json`` whenever the bridge
    completes an update. The heartbeat carries the artifact's sha256 +
    writer identity + write timestamp, so readers can detect stale
    artifacts independent of the artifact body (which may be byte-equal
    across runs even when nothing was actually re-computed).
    """
    return oracle_data_dir() / "oracle_error_rates.heartbeat.json"


def oracle_snapshot_dir() -> Path:
    """Directory hosting per-(city, date) oracle shadow snapshots.

    Single writer: ``scripts/oracle_snapshot_listener.py``.
    Single reader: ``scripts/bridge_oracle_to_calibration.py``.
    """
    return storage_root() / "raw" / "oracle_shadow_snapshots"


# ── atomic JSON writer ─────────────────────────────────────────────── #


def write_json_atomic(
    path: Path | str,
    payload: Any,
    *,
    writer_identity: Optional[str] = None,
) -> dict[str, Any]:
    """Write ``payload`` as JSON to ``path`` atomically.

    Implementation: serialize → write to ``{path.name}.{rand}.tmp`` in the
    same directory → fsync → ``os.replace`` to target. Same-directory
    rename is the only POSIX-atomic operation; cross-directory rename
    falls back to copy+unlink and is NOT crash-safe.

    On any exception during write/fsync, the tmp file is unlinked. The
    target file is either the pre-existing version (replace not yet
    executed) or the new version (replace done) — never partial.

    Returns metadata::

        {
          "sha256": "<hex>",
          "writer": "<pid@host or caller-supplied>",
          "bytes": <int>,
          "written_at": "<utc-isoformat>",
        }

    The metadata is intended for ``write_heartbeat`` consumption and for
    audit log lines; callers may also discard it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    body_text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    body_bytes = body_text.encode("utf-8")
    sha256 = hashlib.sha256(body_bytes).hexdigest()

    if writer_identity is None:
        writer_identity = f"pid={os.getpid()}@{socket.gethostname()}"

    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_to_clean: Optional[str] = tmp_path_str
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, str(path))
        tmp_to_clean = None
    finally:
        if tmp_to_clean is not None:
            try:
                os.unlink(tmp_to_clean)
            except OSError:
                pass

    return {
        "sha256": sha256,
        "writer": writer_identity,
        "bytes": len(body_bytes),
        "written_at": datetime.now(timezone.utc).isoformat(),
    }


def write_heartbeat(
    artifact_name: str,
    artifact_metadata: dict[str, Any],
    *,
    heartbeat_path: Optional[Path] = None,
) -> None:
    """Write a heartbeat record next to a written artifact.

    The heartbeat is a small JSON blob recording when the artifact was
    last written, by what process, and the sha256 of the body. Readers
    compare ``heartbeat.written_at`` against the artifact mtime (or
    against expected freshness windows) to detect stale writers.

    Default heartbeat path is ``oracle_artifact_heartbeat_path()`` for
    backward-compat (most current callers are oracle bridge); pass an
    explicit ``heartbeat_path`` for other artifacts.
    """
    if heartbeat_path is None:
        heartbeat_path = oracle_artifact_heartbeat_path()
    record = {"artifact": artifact_name, **artifact_metadata}
    write_json_atomic(heartbeat_path, record)
