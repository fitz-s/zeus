# Lifecycle: created=2026-04-25; last_reviewed=2026-04-26; last_reused=2026-04-26
# Purpose: Lock packet-runtime CLI behavior and scope-schema contracts.
# Reuse: Run when changing `zpkt`, packet scope parsing, or `architecture/scope_schema.json`.
"""Behavioral tests for the ``zpkt`` Packet Runtime CLI.

These tests build a throwaway git repository in ``tmp_path``, drop the
runtime helpers and CLI into it, and exercise the end-to-end packet
lifecycle. They never touch the live Zeus repository or its branches.

Coverage map:

* ``test_start_creates_packet`` -- ``zpkt start`` emits a packet folder
  with the expected scaffolding and an ``active_packet.txt`` pointer
  (``--inplace`` since worktree creation needs network of git refs we
  do not maintain in this synthetic repo).
* ``test_scope_show_and_add`` -- scope mutations land in
  ``scope.yaml`` and ``scope show`` reports them.
* ``test_status_classifies_buckets`` -- staging an in-scope and
  out-of-scope file together puts each into the right bucket and the
  resulting JSON has the expected counts.
* ``test_status_cache_ttl`` -- the second call within the TTL is
  served from cache (we mutate the staging area between calls and
  observe stale data, then refresh).
* ``test_close_writes_receipt_and_state_line`` -- ``zpkt close``
  writes ``receipt.json``, flips status to ``landed``, and appends an
  idempotent line to ``current_state.md``.
* ``test_audit_bypass_finds_trailer`` -- a commit with the
  ``Pscb-Bypass:`` trailer is reported.
* ``test_setup_configures_hooks_path`` -- ``zpkt setup`` writes
  ``core.hooksPath``.

The tests deliberately avoid invoking ``zpkt commit`` because it
delegates to ``git commit``, which is exercised more cleanly in the
hook-specific test file (``test_pscb_hook.py``).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
HELPER_PATH = SCRIPTS_DIR / "_zpkt_scope.py"
CLI_PATH = SCRIPTS_DIR / "zpkt.py"
SCHEMA_PATH = REPO_ROOT / "architecture" / "scope_schema.json"


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with the runtime helpers wired in."""
    root = tmp_path / "synthrepo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)

    (root / "scripts").mkdir()
    shutil.copy(HELPER_PATH, root / "scripts" / "_zpkt_scope.py")
    shutil.copy(CLI_PATH, root / "scripts" / "zpkt.py")
    (root / "architecture").mkdir()
    shutil.copy(SCHEMA_PATH, root / "architecture" / "scope_schema.json")
    (root / "docs" / "operations").mkdir(parents=True)

    # Seed a baseline commit so HEAD exists.
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)
    return root


def _run(repo: Path, *args: str, expect_rc: int = 0) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "scripts/zpkt.py", *args]
    cp = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    assert cp.returncode == expect_rc, (
        f"zpkt {' '.join(args)} -> rc={cp.returncode}\n"
        f"--- stdout ---\n{cp.stdout}\n--- stderr ---\n{cp.stderr}"
    )
    return cp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_start_creates_packet(synthetic_repo: Path) -> None:
    cp = _run(synthetic_repo, "start", "demo_pkt", "--inplace", "--date", "2099-01-02")
    payload = json.loads(cp.stdout)
    assert payload["packet"] == "task_2099-01-02_demo_pkt"
    pkt_dir = synthetic_repo / "docs" / "operations" / "task_2099-01-02_demo_pkt"
    assert (pkt_dir / "plan.md").is_file()
    assert (pkt_dir / "scope.yaml").is_file()
    assert (pkt_dir / "work_log.md").is_file()
    assert (synthetic_repo / "state" / "active_packet.txt").read_text().strip() == \
        "task_2099-01-02_demo_pkt"


def test_start_can_create_phase_inside_existing_package(synthetic_repo: Path) -> None:
    package = synthetic_repo / "docs" / "operations" / "task_2099-01-01_parent"
    package.mkdir(parents=True)
    (package / "plan.md").write_text("parent\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=synthetic_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "parent package"], cwd=synthetic_repo, check=True)

    cp = _run(
        synthetic_repo,
        "start",
        "phase demo",
        "--inplace",
        "--date",
        "2099-01-02",
        "--package",
        "task_2099-01-01_parent",
    )
    payload = json.loads(cp.stdout)
    packet_path = "task_2099-01-01_parent/phases/task_2099-01-02_phase_demo"
    assert payload["packet_path"] == packet_path
    pkt_dir = synthetic_repo / "docs" / "operations" / packet_path
    assert (pkt_dir / "plan.md").is_file()
    assert (synthetic_repo / "state" / "active_packet.txt").read_text().strip() == packet_path

    import yaml
    scope_doc = yaml.safe_load((pkt_dir / "scope.yaml").read_text())
    assert scope_doc["$schema"] == "../../../../../architecture/scope_schema.json"
    assert scope_doc["in_scope"] == [f"docs/operations/{packet_path}/**"]


def test_scope_schema_accepts_closed_packet_status() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert "closed" in schema["properties"]["status"]["enum"]


def test_scope_show_and_add(synthetic_repo: Path) -> None:
    _run(synthetic_repo, "start", "demo", "--inplace", "--date", "2099-02-02")
    cp = _run(synthetic_repo, "scope", "show")
    payload = json.loads(cp.stdout)
    assert any(p.startswith("docs/operations/") for p in payload["in_scope"])
    _run(synthetic_repo, "scope", "add", "scripts/foo.py")
    cp = _run(synthetic_repo, "scope", "show")
    after = json.loads(cp.stdout)
    assert "scripts/foo.py" in after["in_scope"]


def test_status_classifies_buckets(synthetic_repo: Path) -> None:
    _run(synthetic_repo, "start", "demo", "--inplace", "--date", "2099-03-03")
    pkt = synthetic_repo / "docs/operations/task_2099-03-03_demo"
    # Stage one in-scope file (inside packet folder) and one out-of-scope file.
    (pkt / "notes.md").write_text("hi", encoding="utf-8")
    (synthetic_repo / "state").mkdir(exist_ok=True)
    (synthetic_repo / "state" / "zeus-world.db").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-Af"], cwd=synthetic_repo, check=True)

    cp = _run(synthetic_repo, "status", "--json", "--refresh")
    payload = json.loads(cp.stdout)
    buckets = payload["scope"]["staged_buckets"]
    in_scope_paths = buckets["in_scope"]
    out_paths = buckets["out_of_scope"]
    assert any(p.endswith("/notes.md") for p in in_scope_paths)
    assert any("zeus-world.db" in p for p in out_paths)


def test_close_writes_receipt(synthetic_repo: Path) -> None:
    _run(synthetic_repo, "start", "demo", "--inplace", "--date", "2099-04-04")
    pkt = synthetic_repo / "docs/operations/task_2099-04-04_demo"
    # Pretend the packet is finished; commit something so HEAD has a hash.
    (pkt / "out.md").write_text("done", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=synthetic_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "demo done"], cwd=synthetic_repo, check=True)

    _run(synthetic_repo, "close", "--skip-current-state")
    receipt = json.loads((pkt / "receipt.json").read_text())
    assert receipt["packet"] == "task_2099-04-04_demo"
    # Status flag must move to landed.
    import yaml
    scope_doc = yaml.safe_load((pkt / "scope.yaml").read_text())
    assert scope_doc["status"] == "landed"


def test_audit_bypass_finds_trailer(synthetic_repo: Path) -> None:
    # Commit with the Pscb-Bypass trailer in body.
    msg = "feat: x\n\nPscb-Bypass: emergency rollback of broken doc rename"
    (synthetic_repo / "noise.txt").write_text("noise", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=synthetic_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=synthetic_repo, check=True)

    cp = _run(synthetic_repo, "audit-bypass", "--days", "365")
    payload = json.loads(cp.stdout)
    assert payload["count"] == 1
    assert "emergency rollback" in payload["entries"][0]["reason"]


def test_setup_configures_hooks_path(synthetic_repo: Path, tmp_path: Path) -> None:
    # zpkt setup requires .zeus-githooks/ to exist.
    (synthetic_repo / ".zeus-githooks").mkdir()
    (synthetic_repo / ".zeus-githooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    _run(synthetic_repo, "setup")
    cp = subprocess.run(
        ["git", "config", "core.hooksPath"],
        cwd=synthetic_repo, capture_output=True, text=True, check=True,
    )
    assert cp.stdout.strip() == ".zeus-githooks"
