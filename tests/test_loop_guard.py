# Lifecycle: created=2026-07-08; last_reviewed=2026-07-08; last_reused=2026-07-08
# Purpose: TDD coverage for scripts/ops/loop_guard.py — the testable core of
#   loop/tick.sh and loop/daily.sh (24/7 improvement loop v2). Covers HALT
#   semantics, the pre/post-tick allowlist diff enforcement (quarantine
#   restore of out-of-scope changes, operator-dirty files never touched),
#   the diff circuit breaker, and the flock-run single-flight lock.
# Reuse: every test builds its own throwaway git repo under tmp_path; no
#   live repo state or DBs are touched.
# Authority basis: docs/operations/current/plans/allday_improvement_loop_design_2026-07-06.md
#   §3 (wrapper mechanism, adopted consult BLOCKER-1/HIGH items).
"""Tests for scripts/ops/loop_guard.py."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.ops import loop_guard


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "loop@test")
    _run_git(repo, "config", "user.name", "loop-test")
    (repo / "docs").mkdir()
    (repo / "src").mkdir()
    (repo / "loop").mkdir()
    (repo / "docs" / "README.md").write_text("hello\n")
    (repo / "src" / "money.py").write_text("x = 1\n")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-qm", "init")


def _allowlist(repo: Path) -> Path:
    p = repo / "loop" / "allowlist_auto.txt"
    p.write_text("loop/**\ndocs/**\ntests/**\narchitecture/*.yaml\n")
    return p


# --------------------------------------------------------------------------
# halt-check
# --------------------------------------------------------------------------
def test_halt_check_ok_when_absent(tmp_path):
    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    assert loop_guard.main(["halt-check", "--loop-dir", str(loop_dir)]) == 0


def test_halt_check_halts_when_present(tmp_path):
    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    (loop_dir / "HALT").write_text("")
    assert loop_guard.main(["halt-check", "--loop-dir", str(loop_dir)]) == 3


# --------------------------------------------------------------------------
# snapshot + enforce: happy path (in-allowlist change survives)
# --------------------------------------------------------------------------
def test_enforce_allows_in_allowlist_change(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist = _allowlist(repo)
    snapshot = repo / "loop" / ".pre_tick_snapshot"
    journal = repo / "loop" / "JOURNAL.md"

    assert loop_guard.main(["snapshot", "--repo-root", str(repo), "--out", str(snapshot)]) == 0

    # Simulate the tick writing an in-scope docs file.
    (repo / "docs" / "new_note.md").write_text("evidence\n")

    rc = loop_guard.main(
        [
            "enforce",
            "--repo-root",
            str(repo),
            "--allowlist",
            str(allowlist),
            "--pre-snapshot",
            str(snapshot),
            "--journal",
            str(journal),
            "--tier",
            "l1",
        ]
    )
    assert rc == 0
    assert (repo / "docs" / "new_note.md").exists()  # not restored
    assert not journal.exists() or "VIOLATION" not in journal.read_text()


# --------------------------------------------------------------------------
# quarantine trigger: out-of-allowlist file is hard-restored + VIOLATION logged
# --------------------------------------------------------------------------
def test_enforce_quarantines_out_of_allowlist_new_file(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist = _allowlist(repo)
    snapshot = repo / "loop" / ".pre_tick_snapshot"
    journal = repo / "loop" / "JOURNAL.md"

    loop_guard.main(["snapshot", "--repo-root", str(repo), "--out", str(snapshot)])

    # Out-of-scope: a brand new file under src/ (money path, PREPARE tier).
    (repo / "src" / "sneaky.py").write_text("evil = True\n")

    rc = loop_guard.main(
        [
            "enforce",
            "--repo-root",
            str(repo),
            "--allowlist",
            str(allowlist),
            "--pre-snapshot",
            str(snapshot),
            "--journal",
            str(journal),
            "--tier",
            "l1",
        ]
    )
    assert rc == 1
    assert not (repo / "src" / "sneaky.py").exists()
    assert "VIOLATION" in journal.read_text()
    assert "src/sneaky.py" in journal.read_text()


def test_enforce_quarantines_out_of_allowlist_modification_and_restores_content(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist = _allowlist(repo)
    snapshot = repo / "loop" / ".pre_tick_snapshot"
    journal = repo / "loop" / "JOURNAL.md"

    loop_guard.main(["snapshot", "--repo-root", str(repo), "--out", str(snapshot)])

    # Out-of-scope: modifying a committed money-path file.
    (repo / "src" / "money.py").write_text("x = 999  # tampered\n")

    rc = loop_guard.main(
        [
            "enforce",
            "--repo-root",
            str(repo),
            "--allowlist",
            str(allowlist),
            "--pre-snapshot",
            str(snapshot),
            "--journal",
            str(journal),
            "--tier",
            "l1",
        ]
    )
    assert rc == 1
    assert (repo / "src" / "money.py").read_text() == "x = 1\n"  # restored to HEAD
    assert "VIOLATION" in journal.read_text()


# --------------------------------------------------------------------------
# operator-dirty files present BEFORE the tick must never be touched
# --------------------------------------------------------------------------
def test_enforce_never_touches_pre_existing_dirty_file(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist = _allowlist(repo)
    snapshot = repo / "loop" / ".pre_tick_snapshot"
    journal = repo / "loop" / "JOURNAL.md"

    # Operator's own uncommitted work, present BEFORE the tick starts.
    (repo / "src" / "money.py").write_text("x = 1  # operator WIP\n")

    loop_guard.main(["snapshot", "--repo-root", str(repo), "--out", str(snapshot)])

    # Tick itself makes an in-scope docs change only.
    (repo / "docs" / "new_note.md").write_text("evidence\n")

    rc = loop_guard.main(
        [
            "enforce",
            "--repo-root",
            str(repo),
            "--allowlist",
            str(allowlist),
            "--pre-snapshot",
            str(snapshot),
            "--journal",
            str(journal),
            "--tier",
            "l1",
        ]
    )
    assert rc == 0
    # Operator's pre-existing dirty file must survive untouched.
    assert (repo / "src" / "money.py").read_text() == "x = 1  # operator WIP\n"
    assert not journal.exists() or "VIOLATION" not in journal.read_text()


def test_enforce_no_new_changes_is_noop(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist = _allowlist(repo)
    snapshot = repo / "loop" / ".pre_tick_snapshot"
    journal = repo / "loop" / "JOURNAL.md"

    loop_guard.main(["snapshot", "--repo-root", str(repo), "--out", str(snapshot)])
    rc = loop_guard.main(
        [
            "enforce",
            "--repo-root",
            str(repo),
            "--allowlist",
            str(allowlist),
            "--pre-snapshot",
            str(snapshot),
            "--journal",
            str(journal),
            "--tier",
            "l1",
        ]
    )
    assert rc == 0
    assert not journal.exists()


# --------------------------------------------------------------------------
# diff circuit breaker: >20 files or >600 lines -> reject everything + ESCALATION
# --------------------------------------------------------------------------
def test_enforce_circuit_breaker_trips_on_file_count(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist = _allowlist(repo)
    snapshot = repo / "loop" / ".pre_tick_snapshot"
    journal = repo / "loop" / "JOURNAL.md"

    loop_guard.main(["snapshot", "--repo-root", str(repo), "--out", str(snapshot)])

    for i in range(25):
        (repo / "docs" / f"gen_{i}.md").write_text("x\n")

    rc = loop_guard.main(
        [
            "enforce",
            "--repo-root",
            str(repo),
            "--allowlist",
            str(allowlist),
            "--pre-snapshot",
            str(snapshot),
            "--journal",
            str(journal),
            "--tier",
            "l1",
        ]
    )
    assert rc == 2
    assert "ESCALATION" in journal.read_text()
    # All 25 new files restored (deleted), even though each individually
    # matches the allowlist glob.
    for i in range(25):
        assert not (repo / "docs" / f"gen_{i}.md").exists()


def test_enforce_circuit_breaker_trips_on_line_count(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist = _allowlist(repo)
    snapshot = repo / "loop" / ".pre_tick_snapshot"
    journal = repo / "loop" / "JOURNAL.md"

    loop_guard.main(["snapshot", "--repo-root", str(repo), "--out", str(snapshot)])

    (repo / "docs" / "huge.md").write_text("\n".join(f"line {i}" for i in range(700)) + "\n")

    rc = loop_guard.main(
        [
            "enforce",
            "--repo-root",
            str(repo),
            "--allowlist",
            str(allowlist),
            "--pre-snapshot",
            str(snapshot),
            "--journal",
            str(journal),
            "--tier",
            "l1",
        ]
    )
    assert rc == 2
    assert "ESCALATION" in journal.read_text()
    assert not (repo / "docs" / "huge.md").exists()


# --------------------------------------------------------------------------
# fallback-entry
# --------------------------------------------------------------------------
def test_fallback_entry_appends_marker(tmp_path):
    journal = tmp_path / "loop" / "JOURNAL.md"
    rc = loop_guard.main(
        ["fallback-entry", "--journal", str(journal), "--tier", "l2", "--reason", "claude exit=1"]
    )
    assert rc == 0
    text = journal.read_text()
    assert "FALLBACK" in text
    assert "claude exit=1" in text
    assert "L2" in text


# --------------------------------------------------------------------------
# allowlist glob matching
# --------------------------------------------------------------------------
def test_path_allowed_matches_nested_paths():
    patterns = loop_guard.load_allowlist_from_lines(
        ["loop/**", "docs/**", "architecture/*.yaml"]
    )
    assert loop_guard.path_allowed("loop/JOURNAL.md", patterns)
    assert loop_guard.path_allowed("loop/prompts/l1.md", patterns)
    assert loop_guard.path_allowed("docs/operations/current/plans/foo.md", patterns)
    assert loop_guard.path_allowed("architecture/topology.yaml", patterns)
    assert not loop_guard.path_allowed("architecture/sub/topology.yaml", patterns)
    assert not loop_guard.path_allowed("src/main.py", patterns)
    assert not loop_guard.path_allowed("config/settings.json", patterns)


# --------------------------------------------------------------------------
# flock-run: single-flight overlap rejection
# --------------------------------------------------------------------------
def test_flock_run_executes_command_when_free(tmp_path):
    lock = tmp_path / "loop.lock"
    marker = tmp_path / "ran"
    rc = loop_guard.main(
        [
            "flock-run",
            "--lock-file",
            str(lock),
            "--",
            sys.executable,
            "-c",
            f"open({str(marker)!r}, 'w').write('1')",
        ]
    )
    assert rc == 0
    assert marker.exists()


def test_flock_run_rejects_concurrent_overlap(tmp_path):
    lock = tmp_path / "loop.lock"
    marker = tmp_path / "ran"

    # Hold the lock in a background subprocess for a short duration.
    holder = subprocess.Popen(
        [
            sys.executable,
            str(Path(loop_guard.__file__)),
            "flock-run",
            "--lock-file",
            str(lock),
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(1.5)",
        ]
    )
    try:
        # Give the holder a moment to actually acquire the lock.
        time.sleep(0.4)
        rc = loop_guard.main(
            [
                "flock-run",
                "--lock-file",
                str(lock),
                "--",
                sys.executable,
                "-c",
                f"open({str(marker)!r}, 'w').write('1')",
            ]
        )
        assert rc == loop_guard.LOCK_BUSY_EXIT_CODE
        assert not marker.exists()  # second invocation never ran the command
    finally:
        holder.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
