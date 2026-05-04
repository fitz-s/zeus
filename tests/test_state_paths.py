# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A2 (storage path centralization + atomic write + heartbeat).
"""Storage path centralization regression tests.

A2 (PLAN.md §A2 + D-10) ships ``src/state/paths.py`` as the single locus
for storage-root resolution and atomic JSON writes, replacing scattered
``Path(__file__).resolve().parent.parent.parent / "data"`` patterns and
4+ private ``_atomic_write_json`` copies. These tests pin three contracts
that the rest of the rebuild (A3-A6) will rely on:

1. The default storage root is the repo root when ``ZEUS_STORAGE_ROOT``
   is unset.
2. Setting ``ZEUS_STORAGE_ROOT`` redirects every path builder coherently
   (oracle, snapshot, heartbeat) — no callsite gets stranded on the old
   root.
3. ``write_json_atomic`` is crash-safe — the target is either pre-existing
   content or new content, never partial. ``write_heartbeat`` writes a
   well-formed companion record with ``sha256``/``writer``/``written_at``.

A regression on (2) is the bug class that motivated centralization: the
listener writes to one path while the bridge reads from another, the
oracle file silently goes stale, and the daemon entries sized using the
old multipliers. The "all artifacts redirect together" invariant is the
antibody.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

import src.state.paths as paths_module
from src.state.paths import (
    REPO_ROOT,
    oracle_artifact_heartbeat_path,
    oracle_data_dir,
    oracle_error_rates_path,
    oracle_snapshot_dir,
    storage_root,
    write_heartbeat,
    write_json_atomic,
)


def test_default_storage_root_equals_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZEUS_STORAGE_ROOT", raising=False)
    assert storage_root() == REPO_ROOT


def test_blank_storage_root_falls_back_to_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty / whitespace value must NOT be treated as a valid override —
    a stray ``export ZEUS_STORAGE_ROOT=`` would silently route writes to
    ``Path('').resolve()`` (= cwd) otherwise.
    """
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", "")
    assert storage_root() == REPO_ROOT
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", "   ")
    assert storage_root() == REPO_ROOT


def test_storage_root_override_redirects_all_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ZEUS_STORAGE_ROOT redirects every path builder coherently.
    The bug class A2 closes is one writer routed via the new path while
    a reader stays on the old one — pin "all builders move together".
    """
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    assert storage_root() == tmp_path.resolve()
    assert oracle_data_dir() == tmp_path.resolve() / "data"
    assert oracle_error_rates_path() == tmp_path.resolve() / "data" / "oracle_error_rates.json"
    assert (
        oracle_artifact_heartbeat_path()
        == tmp_path.resolve() / "data" / "oracle_error_rates.heartbeat.json"
    )
    assert oracle_snapshot_dir() == tmp_path.resolve() / "raw" / "oracle_shadow_snapshots"


def test_oracle_penalty_picks_up_storage_root_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``oracle_penalty._load`` must call the path builder on each
    invocation (not capture at import) so an env override propagates
    without re-importing the module.
    """
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    # Write a synthetic oracle file under the override root.
    payload = {"NYC": {"high": {"oracle_error_rate": 0.05, "status": "CAUTION"}}}
    target = tmp_path / "data" / "oracle_error_rates.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload))

    # Force a fresh load of oracle_penalty — its module-level cache may
    # already be populated by other tests.
    import src.strategy.oracle_penalty as op
    importlib.reload(op)
    op.reload()
    info = op.get_oracle_info("NYC", "high")
    assert info.error_rate == pytest.approx(0.05)
    assert info.status.value == "CAUTION"


def test_atomic_write_creates_target_with_payload(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "out.json"  # parent dir must be auto-created
    payload = {"a": 1, "b": [2, 3]}
    meta = write_json_atomic(target, payload)

    assert target.exists()
    assert json.loads(target.read_text()) == payload
    assert meta["bytes"] > 0
    assert len(meta["sha256"]) == 64
    assert meta["writer"]
    assert meta["written_at"].endswith("+00:00") or meta["written_at"].endswith("Z")


def test_atomic_write_overwrites_in_place(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    write_json_atomic(target, {"v": 1})
    sha_a = target.read_bytes()
    write_json_atomic(target, {"v": 2})
    sha_b = target.read_bytes()
    assert sha_a != sha_b
    assert json.loads(target.read_text()) == {"v": 2}


def test_atomic_write_leaves_target_intact_on_serialization_failure(tmp_path: Path) -> None:
    """A crash mid-serialize (uncatchable circular reference) must NOT
    leave a partial file at the target path. The pre-existing version
    must remain readable, and no .tmp file may linger.
    """
    target = tmp_path / "out.json"
    write_json_atomic(target, {"prev": True})

    # Build a circular reference that json.dumps cannot serialize.
    circular: dict = {"key": None}
    circular["key"] = circular

    with pytest.raises((TypeError, ValueError, RecursionError)):
        write_json_atomic(target, circular)

    # Pre-existing target unchanged.
    assert json.loads(target.read_text()) == {"prev": True}
    # No leftover temp file in the parent dir.
    leftover = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftover == [], f"orphan tmp files: {leftover}"


def test_write_json_atomic_uses_caller_supplied_writer_identity(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    meta = write_json_atomic(target, {"v": 1}, writer_identity="testcase")
    assert meta["writer"] == "testcase"


def test_write_heartbeat_records_artifact_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    write_heartbeat(
        "oracle_error_rates",
        {"sha256": "abc", "writer": "test", "bytes": 42, "written_at": "2026-05-04T00:00:00+00:00"},
    )
    hb = oracle_artifact_heartbeat_path()
    assert hb.exists()
    data = json.loads(hb.read_text())
    assert data["artifact"] == "oracle_error_rates"
    assert data["sha256"] == "abc"
    assert data["bytes"] == 42


def test_write_heartbeat_explicit_path_override(tmp_path: Path) -> None:
    """When the caller provides ``heartbeat_path``, it must be used
    verbatim — no fallback to the default oracle heartbeat path.
    """
    custom = tmp_path / "custom" / "hb.json"
    write_heartbeat("widget", {"sha256": "z"}, heartbeat_path=custom)
    assert custom.exists()
    data = json.loads(custom.read_text())
    assert data["artifact"] == "widget"


def test_module_repo_root_resolves_to_zeus_repo() -> None:
    """REPO_ROOT must contain `src/` and `tests/` — sanity check that
    the ``parent.parent.parent`` traversal didn't drift to a wrong dir.
    """
    assert (paths_module.REPO_ROOT / "src").is_dir()
    assert (paths_module.REPO_ROOT / "tests").is_dir()
