# Lifecycle: created=2026-07-08; last_reviewed=2026-07-08; last_reused=2026-07-08
# Purpose: TDD coverage for scripts/ops/loop_guard.py — the testable core of
#   loop/tick.sh and loop/daily.sh (24/7 improvement loop v2). Covers HALT
#   semantics, the pre/post-tick allowlist diff enforcement (restore
#   restore of out-of-scope changes, operator-dirty files never touched),
#   the diff circuit breaker, the flock-run single-flight lock, and the
#   guard-escape hardening history:
#     - allowlist self-widening: TWO prior file-based designs (an in-repo
#       frozen copy, then an out-of-repo mktemp copy) were each live-
#       reproduced escapes — any file a tick's Bash tool can locate is a
#       file it can tamper, whether by guessing a documented repo-relative
#       name or by enumerating $TMPDIR (same OS user, no process isolation).
#       The current design reads the allowlist from an IMMUTABLE git object
#       at a pre-captured sha (`git show "$BASE_SHA:loop/allowlist_auto.txt"`)
#       — there is no file to find or tamper at all. See
#       test_enforce_rewriting_live_allowlist_has_no_effect_because_enforce_reads_base_sha
#       and test_enforce_allowlist_tamper_defeated_by_git_ref.
#     - rename laundering: git mv into an allowed dir must check BOTH sides.
#     - symlink-following delete: must lstat/unlink the link, never its
#       resolved target.
#     - DB writes invisible to git status (*.db is globally gitignored):
#       needs a separate mtime/size sentinel that self-halts.
#     - dirty-at-start and DB-sentinel baselines now live only in the
#       wrapper's own shell-variable memory and cross into this CLI via an
#       anonymous stdin pipe (`--pre-snapshot -`), never a file — see
#       test_enforce_accepts_pre_snapshot_via_stdin and
#       test_db_sentinel_check_accepts_pre_snapshot_via_stdin for the real
#       subprocess+pipe proof of that handoff.
# Reuse: every test builds its own throwaway git repo under tmp_path; no
#   live repo state or DBs are touched.
# Authority basis: docs/operations/current/plans/allday_improvement_loop_design_2026-07-06.md
#   §3 (wrapper mechanism, adopted consult BLOCKER-1/HIGH items).
"""Tests for scripts/ops/loop_guard.py."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.ops import loop_guard

DEFAULT_ALLOWLIST = "loop/**\ndocs/**\ntests/**\narchitecture/*.yaml\n"


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
    (repo / "src" / "riskguard.py").write_text("kelly = 0.03125\n")
    (repo / "loop" / "allowlist_auto.txt").write_text(DEFAULT_ALLOWLIST)
    (repo / "loop" / "tick.sh").write_text("#!/usr/bin/env bash\necho tick\n")
    (repo / "loop" / "JOURNAL.md").write_text("# journal\n")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-qm", "init")


def _base_sha(repo: Path) -> str:
    """Simulate tick.sh's `BASE_SHA=$(git rev-parse HEAD)` capture, taken
    BEFORE the "tick" (the test's simulated claude run) makes any change."""
    return _run_git(repo, "rev-parse", "HEAD").stdout.strip()


def _allowlist_ref(repo: Path, sha: str | None = None) -> str:
    """A `git show`-compatible ref for loop/allowlist_auto.txt at `sha`
    (default: current HEAD) — what tick.sh actually passes to
    `enforce --allowlist-git-ref`. Content-addressed and immutable: nothing
    a "tick" does to the WORKING TREE after this sha is captured can change
    what this ref resolves to."""
    return f"{sha or _base_sha(repo)}:loop/allowlist_auto.txt"


def _snapshot(repo: Path) -> Path:
    """Dirty-path pre-snapshot, captured to a tmp file (test/CLI
    convenience — production tick.sh captures stdout into a shell variable
    and never touches disk; see test_enforce_accepts_pre_snapshot_via_stdin
    for that real end-to-end path). Written OUTSIDE the repo (a sibling
    directory), matching what a non-file-based design implies: this data
    must never live somewhere the "tick" under test could plausibly reach."""
    outside = repo.parent / "outside_dirty_paths"
    outside.mkdir(parents=True, exist_ok=True)
    out = outside / "dirty_paths"
    loop_guard.main(["snapshot", "--repo-root", str(repo), "--out", str(out)])
    return out


def _enforce(repo: Path, snapshot: Path, allowlist_ref: str, journal: Path, tier: str = "l1") -> int:
    return loop_guard.main(
        [
            "enforce",
            "--repo-root",
            str(repo),
            "--allowlist-git-ref",
            allowlist_ref,
            "--pre-snapshot",
            str(snapshot),
            "--journal",
            str(journal),
            "--tier",
            tier,
        ]
    )


def _commit_auto(repo: Path, snapshot: Path, allowlist_ref: str, journal: Path, tier: str = "l1") -> int:
    return loop_guard.main(
        [
            "commit-auto",
            "--repo-root",
            str(repo),
            "--allowlist-git-ref",
            allowlist_ref,
            "--pre-snapshot",
            str(snapshot),
            "--tier",
            tier,
            "--journal",
            str(journal),
        ]
    )


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
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    # Simulate the tick writing an in-scope docs file.
    (repo / "docs" / "new_note.md").write_text("evidence\n")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 0
    assert (repo / "docs" / "new_note.md").exists()  # not restored
    assert not journal.exists() or "VIOLATION" not in journal.read_text()


# --------------------------------------------------------------------------
# restore trigger: out-of-allowlist file is hard-restored + VIOLATION logged
# --------------------------------------------------------------------------
def test_enforce_restores_out_of_allowlist_new_file(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    # Out-of-scope: a brand new file under src/ (money path, PREPARE tier).
    (repo / "src" / "sneaky.py").write_text("evil = True\n")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 1
    assert not (repo / "src" / "sneaky.py").exists()
    assert "VIOLATION" in journal.read_text()
    assert "src/sneaky.py" in journal.read_text()


def test_enforce_restores_out_of_allowlist_modification_and_restores_content(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    # Out-of-scope: modifying a committed money-path file.
    (repo / "src" / "money.py").write_text("x = 999  # tampered\n")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 1
    assert (repo / "src" / "money.py").read_text() == "x = 1\n"  # restored to HEAD
    assert "VIOLATION" in journal.read_text()


# --------------------------------------------------------------------------
# operator-dirty files present BEFORE the tick must never be touched
# --------------------------------------------------------------------------
def test_enforce_never_touches_pre_existing_dirty_file(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)

    # Operator's own uncommitted work, present BEFORE the tick starts.
    (repo / "src" / "money.py").write_text("x = 1  # operator WIP\n")

    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    # Tick itself makes an in-scope docs change only.
    (repo / "docs" / "new_note.md").write_text("evidence\n")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 0
    # Operator's pre-existing dirty file must survive untouched.
    assert (repo / "src" / "money.py").read_text() == "x = 1  # operator WIP\n"
    assert not journal.exists() or "VIOLATION" not in journal.read_text()


def test_enforce_no_new_changes_is_noop(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 0
    assert not journal.exists()


# --------------------------------------------------------------------------
# diff circuit breaker: >20 files or >600 lines -> reject everything + ESCALATION
# --------------------------------------------------------------------------
def test_enforce_circuit_breaker_trips_on_file_count(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    for i in range(25):
        (repo / "docs" / f"gen_{i}.md").write_text("x\n")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 2
    assert "ESCALATION" in journal.read_text()
    # All 25 new files restored (deleted), even though each individually
    # matches the allowlist glob.
    for i in range(25):
        assert not (repo / "docs" / f"gen_{i}.md").exists()


def test_enforce_circuit_breaker_trips_on_line_count(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    (repo / "docs" / "huge.md").write_text("\n".join(f"line {i}" for i in range(700)) + "\n")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 2
    assert "ESCALATION" in journal.read_text()
    assert not (repo / "docs" / "huge.md").exists()


# --------------------------------------------------------------------------
# REGRESSION: allowlist self-widening, closed via immutable git object
# --------------------------------------------------------------------------
def test_enforce_rewriting_live_allowlist_has_no_effect_because_enforce_reads_base_sha(tmp_path):
    """The core property the fix depends on, tested in isolation: after
    BASE_SHA is captured, REWRITING the working-tree loop/allowlist_auto.txt
    (no exploit attempt, just the rewrite itself) must not change what
    `load_allowlist_from_git` returns — it is pinned to the immutable git
    object, not the mutable file."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ref = _allowlist_ref(repo)  # captured BEFORE the rewrite below

    (repo / "loop" / "allowlist_auto.txt").write_text(DEFAULT_ALLOWLIST + "src/**\nconfig/**\n")

    patterns = loop_guard.load_allowlist_from_git(repo, ref)
    assert patterns == loop_guard.load_allowlist_from_lines(DEFAULT_ALLOWLIST.splitlines())
    assert "src/**" not in patterns
    assert "config/**" not in patterns


def test_enforce_allowlist_tamper_defeated_by_git_ref(tmp_path):
    """A tick that appends 'src/**' to the live allowlist_auto.txt and then
    edits src/ in the same run must NOT get away with it — enforce loads
    the allowlist from the git object at BASE_SHA, which does not have the
    widened pattern, so the src/ edit is still a violation."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)  # BASE_SHA captured BEFORE the "tick"
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    # The "tick" widens the LIVE allowlist file (in-scope: loop/** covers it)...
    live_allowlist = repo / "loop" / "allowlist_auto.txt"
    live_allowlist.write_text(DEFAULT_ALLOWLIST + "src/**\n")
    # ...then exploits the widened scope in the same run.
    (repo / "src" / "sneaky.py").write_text("evil = True\n")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 1
    # The src/ exploit attempt must be restored regardless of the live
    # (tampered) allowlist content.
    assert not (repo / "src" / "sneaky.py").exists()
    text = journal.read_text()
    assert "VIOLATION" in text
    assert "src/sneaky.py" in text
    # The live allowlist_auto.txt edit itself is ALSO a violation
    # (guard-immutable — see next test) and must be restored to HEAD
    # content, not left widened for the next tick.
    assert live_allowlist.read_text() == DEFAULT_ALLOWLIST


def test_enforce_guard_immutable_files_always_violate_even_if_allowlisted(tmp_path):
    """loop/allowlist_auto.txt, loop/tick.sh, loop/daily.sh, and
    loop/prompts/** are guard-immutable: any tick-made edit to them is a
    VIOLATION even though loop/** is otherwise AUTO-allowed and even though
    the BASE_SHA allowlist (unmodified) would technically permit editing
    files under loop/**. Belt-and-suspenders on top of the git-ref fix."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    (repo / "loop" / "allowlist_auto.txt").write_text(DEFAULT_ALLOWLIST + "config/**\n")
    (repo / "loop" / "tick.sh").write_text("#!/usr/bin/env bash\necho tampered\n")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 1
    assert (repo / "loop" / "allowlist_auto.txt").read_text() == DEFAULT_ALLOWLIST
    assert "echo tick" in (repo / "loop" / "tick.sh").read_text()
    text = journal.read_text()
    assert "VIOLATION" in text
    assert "guard-immutable" in text


def test_enforce_bad_git_ref_fails_closed(tmp_path):
    """A malformed or missing --allowlist-git-ref (bad sha, wrong path)
    must fail CLOSED — zero patterns, so every new-this-tick change becomes
    a violation — never silently allow everything."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    (repo / "docs" / "new_note.md").write_text("evidence\n")

    rc = _enforce(repo, snapshot, "0" * 40 + ":loop/allowlist_auto.txt", journal)
    assert rc == 1
    assert not (repo / "docs" / "new_note.md").exists()
    assert "VIOLATION" in journal.read_text()


# --------------------------------------------------------------------------
# REGRESSION: rename laundering
# --------------------------------------------------------------------------
def test_enforce_rename_laundering_checks_both_sides(tmp_path):
    """`git mv src/riskguard.py docs/stolen.py` must NOT pass just because
    the NEW path (docs/**) is allowed — the ORIGINAL path (src/**) is out
    of scope, so this must be a violation, and BOTH sides must be restored:
    the original comes back, the laundered copy is removed."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    _run_git(repo, "mv", "src/riskguard.py", "docs/stolen.py")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 1
    # Laundered copy removed.
    assert not (repo / "docs" / "stolen.py").exists()
    # Original restored.
    assert (repo / "src" / "riskguard.py").exists()
    assert (repo / "src" / "riskguard.py").read_text() == "kelly = 0.03125\n"
    text = journal.read_text()
    assert "VIOLATION" in text
    assert "riskguard.py" in text and "stolen.py" in text


def test_enforce_rename_allowed_when_both_sides_in_scope(tmp_path):
    """A rename where BOTH sides are allowlisted (e.g. within docs/) must
    survive — the both-sides check must not become a blanket rename-denier."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    _run_git(repo, "mv", "docs/README.md", "docs/README2.md")

    rc = _enforce(repo, snapshot, allowlist_ref, journal)
    assert rc == 0
    assert (repo / "docs" / "README2.md").exists()
    assert not (repo / "docs" / "README.md").exists()
    assert not journal.exists() or "VIOLATION" not in journal.read_text()


# --------------------------------------------------------------------------
# REGRESSION: symlink-following delete wipes the audit trail
# --------------------------------------------------------------------------
def test_enforce_symlink_violation_deletes_the_link_not_the_target(tmp_path):
    """A violating path that is itself a symlink pointing at loop/JOURNAL.md
    must have the LINK deleted, never the resolved target — resolving
    before delete would wipe the journal's audit trail while leaving the
    malicious symlink sitting there untouched."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)

    journal_target = repo / "loop" / "JOURNAL.md"
    original_journal_content = journal_target.read_text()

    snapshot = _snapshot(repo)
    out_journal = repo / "loop" / "journal_out.md"

    evil_link = repo / "evil_link"
    evil_link.symlink_to(journal_target)
    assert evil_link.is_symlink()

    rc = _enforce(repo, snapshot, allowlist_ref, out_journal)
    assert rc == 1
    # The symlink itself is gone (or at least no longer resolves to the
    # journal — either outcome proves the link was targeted, not the file).
    assert not evil_link.exists()  # lexists() would also be False: unlinked
    # The REAL journal content must be untouched — this is the actual
    # regression: a naive resolve()-then-unlink would have wiped this.
    assert journal_target.read_text() == original_journal_content
    text = out_journal.read_text()
    assert "VIOLATION" in text
    assert "evil_link" in text


# --------------------------------------------------------------------------
# REGRESSION: in-memory control-state handoff via stdin (never a file)
# --------------------------------------------------------------------------
def test_enforce_accepts_pre_snapshot_via_stdin(tmp_path):
    """Real subprocess+pipe proof that `--pre-snapshot -` works the way
    tick.sh actually uses it: DIRTY_AT_START captured via stdout into a
    shell-variable equivalent (here, a Python string), then piped into a
    SEPARATE `enforce` subprocess via stdin — never touching disk, never
    passed as a CLI argument (so invisible to `ps`)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ref = _allowlist_ref(repo)
    guard_py = str(Path(loop_guard.__file__))

    # Capture stdout, exactly like tick.sh's `DIRTY_AT_START="$(...)"`.
    snap_proc = subprocess.run(
        [sys.executable, guard_py, "snapshot", "--repo-root", str(repo)],
        capture_output=True, text=True, check=True,
    )
    dirty_at_start = snap_proc.stdout

    # Tick makes an in-scope change and an out-of-scope one.
    (repo / "docs" / "ok.md").write_text("fine\n")
    (repo / "src" / "sneaky.py").write_text("evil = True\n")

    journal = repo / "loop" / "journal_out.md"
    enforce_proc = subprocess.run(
        [
            sys.executable, guard_py, "enforce",
            "--repo-root", str(repo),
            "--allowlist-git-ref", ref,
            "--pre-snapshot", "-",
            "--journal", str(journal),
            "--tier", "l1",
        ],
        input=dirty_at_start,
        capture_output=True, text=True, check=False,
    )
    assert enforce_proc.returncode == 1
    assert (repo / "docs" / "ok.md").exists()
    assert not (repo / "src" / "sneaky.py").exists()
    assert "VIOLATION" in journal.read_text()


def test_db_sentinel_check_accepts_pre_snapshot_via_stdin(tmp_path):
    """Same real subprocess+pipe proof for the DB sentinel baseline
    (DB_SENTINEL_PRE in tick.sh)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = repo / "state"
    state_dir.mkdir()
    (state_dir / "zeus-world.db").write_bytes(b"original")
    guard_py = str(Path(loop_guard.__file__))

    snap_proc = subprocess.run(
        [sys.executable, guard_py, "db-sentinel-snapshot", "--repo-root", str(repo)],
        capture_output=True, text=True, check=True,
    )
    db_sentinel_pre = snap_proc.stdout

    (state_dir / "zeus-world.db").write_bytes(b"tampered, different size")

    journal = repo / "loop" / "journal_out.md"
    loop_dir = repo / "loop"
    check_proc = subprocess.run(
        [
            sys.executable, guard_py, "db-sentinel-check",
            "--repo-root", str(repo),
            "--pre-snapshot", "-",
            "--journal", str(journal),
            "--loop-dir", str(loop_dir),
            "--tier", "l1",
        ],
        input=db_sentinel_pre,
        capture_output=True, text=True, check=False,
    )
    assert check_proc.returncode == 2
    assert (loop_dir / "HALT").exists()
    assert "ESCALATION" in journal.read_text()


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


def test_is_guard_immutable():
    entry_allowlist = loop_guard.DirtyEntry(" M", "loop/allowlist_auto.txt")
    entry_tick = loop_guard.DirtyEntry(" M", "loop/tick.sh")
    entry_daily = loop_guard.DirtyEntry(" M", "loop/daily.sh")
    entry_prompt = loop_guard.DirtyEntry(" M", "loop/prompts/l1.md")
    entry_journal = loop_guard.DirtyEntry(" M", "loop/JOURNAL.md")
    entry_ledger = loop_guard.DirtyEntry(" M", "loop/LEDGER.yaml")

    assert loop_guard.is_guard_immutable(entry_allowlist)
    assert loop_guard.is_guard_immutable(entry_tick)
    assert loop_guard.is_guard_immutable(entry_daily)
    assert loop_guard.is_guard_immutable(entry_prompt)
    assert not loop_guard.is_guard_immutable(entry_journal)
    assert not loop_guard.is_guard_immutable(entry_ledger)

    # Rename INTO a guard-immutable path is also caught (orig side check).
    rename_into = loop_guard.DirtyEntry("R ", "loop/tick.sh", orig_path="loop/tick_backup.sh")
    assert loop_guard.is_guard_immutable(rename_into)


# --------------------------------------------------------------------------
# REGRESSION: DB writes are invisible to git status (structural gap)
# --------------------------------------------------------------------------
def test_db_sentinel_detects_new_db_file_and_self_halts(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = repo / "state"
    state_dir.mkdir()

    pre = repo.parent / "outside_db_sentinel_pre"
    assert loop_guard.main(["db-sentinel-snapshot", "--repo-root", str(repo), "--out", str(pre)]) == 0

    # A tick writes a brand new DB file — invisible to git (globally
    # gitignored *.db), so this must be caught by mtime/size, not git status.
    (state_dir / "zeus-world.db").write_bytes(b"sqlite data")

    journal = repo / "loop" / "journal_out.md"
    loop_dir = repo / "loop"
    halt_path = loop_dir / "HALT"
    assert not halt_path.exists()

    rc = loop_guard.main(
        [
            "db-sentinel-check",
            "--repo-root",
            str(repo),
            "--pre-snapshot",
            str(pre),
            "--journal",
            str(journal),
            "--loop-dir",
            str(loop_dir),
            "--tier",
            "l1",
        ]
    )
    assert rc == 2
    assert halt_path.exists()  # self-halt: loop/HALT touched
    text = journal.read_text()
    assert "ESCALATION" in text
    assert "zeus-world.db" in text


def test_db_sentinel_detects_content_change_on_existing_db(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = repo / "state"
    state_dir.mkdir()
    db = state_dir / "zeus-world.db"
    db.write_bytes(b"original")

    pre = repo.parent / "outside_db_sentinel_pre2"
    loop_guard.main(["db-sentinel-snapshot", "--repo-root", str(repo), "--out", str(pre)])

    # Size delta alone (33 bytes vs 8) is sufficient to detect the change
    # deterministically, independent of filesystem mtime granularity.
    db.write_bytes(b"tampered content, different size")

    journal = repo / "loop" / "journal_out.md"
    loop_dir = repo / "loop"
    rc = loop_guard.main(
        [
            "db-sentinel-check",
            "--repo-root",
            str(repo),
            "--pre-snapshot",
            str(pre),
            "--journal",
            str(journal),
            "--loop-dir",
            str(loop_dir),
            "--tier",
            "l1",
        ]
    )
    assert rc == 2
    assert (loop_dir / "HALT").exists()
    assert "ESCALATION" in journal.read_text()


def test_db_sentinel_no_delta_is_noop(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = repo / "state"
    state_dir.mkdir()
    (state_dir / "zeus-world.db").write_bytes(b"stable")

    pre = repo.parent / "outside_db_sentinel_pre3"
    loop_guard.main(["db-sentinel-snapshot", "--repo-root", str(repo), "--out", str(pre)])

    journal = repo / "loop" / "journal_out.md"
    loop_dir = repo / "loop"
    rc = loop_guard.main(
        [
            "db-sentinel-check",
            "--repo-root",
            str(repo),
            "--pre-snapshot",
            str(pre),
            "--journal",
            str(journal),
            "--loop-dir",
            str(loop_dir),
            "--tier",
            "l1",
        ]
    )
    assert rc == 0
    assert not (loop_dir / "HALT").exists()
    assert not journal.exists()


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


# --------------------------------------------------------------------------
# interval-check: loop/INTERVAL turns the launchd-fixed hourly cadence into
# an operator-adjustable dial
# --------------------------------------------------------------------------
def test_interval_check_first_run_proceeds_and_stamps(tmp_path):
    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    # Missing .last_tick_epoch defaults last=0, so any realistic epoch "now"
    # (far more than one hour past the Unix epoch) naturally clears the
    # default 1h window — use a real-ish epoch, not a tiny test value.
    now = 10_000_000
    rc = loop_guard.main(["interval-check", "--loop-dir", str(loop_dir), "--now", str(now)])
    assert rc == 0
    assert (loop_dir / ".last_tick_epoch").read_text() == str(now)


def test_interval_check_skips_inside_window_without_touching_stamp(tmp_path):
    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    (loop_dir / ".last_tick_epoch").write_text("1000")
    rc = loop_guard.main(["interval-check", "--loop-dir", str(loop_dir), "--now", "1500"])
    assert rc == 4
    assert (loop_dir / ".last_tick_epoch").read_text() == "1000"


def test_interval_check_honors_operator_hours_value(tmp_path):
    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    (loop_dir / "INTERVAL").write_text("3")
    (loop_dir / ".last_tick_epoch").write_text("0")

    almost = 2 * 3600 + 59 * 60  # 2h59m
    rc = loop_guard.main(["interval-check", "--loop-dir", str(loop_dir), "--now", str(almost)])
    assert rc == 4
    assert (loop_dir / ".last_tick_epoch").read_text() == "0"

    exactly = 3 * 3600
    rc2 = loop_guard.main(["interval-check", "--loop-dir", str(loop_dir), "--now", str(exactly)])
    assert rc2 == 0
    assert (loop_dir / ".last_tick_epoch").read_text() == str(exactly)


def test_interval_check_invalid_interval_falls_back_to_one_hour(tmp_path):
    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    (loop_dir / "INTERVAL").write_text("not-a-number")
    (loop_dir / ".last_tick_epoch").write_text("0")

    rc = loop_guard.main(["interval-check", "--loop-dir", str(loop_dir), "--now", "3599"])
    assert rc == 4
    rc2 = loop_guard.main(["interval-check", "--loop-dir", str(loop_dir), "--now", "3600"])
    assert rc2 == 0


# --------------------------------------------------------------------------
# run-queries: query escrow — the sandboxed tick authors SQL, this trusted
# command executes it read-only against the canonical DBs
# --------------------------------------------------------------------------
def _make_db(path: Path, rows: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO t (val) VALUES (?)", (f"v{i}",))
    conn.commit()
    conn.close()


def _write_pending(loop_dir: Path, name: str, sql: str) -> Path:
    pending = loop_dir / "queries" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    p = pending / name
    p.write_text(sql)
    return p


def test_run_queries_executes_select_against_attached_alias_and_writes_result(tmp_path, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    loop_dir = repo / "loop"
    _make_db(repo / "state" / "zeus-forecasts.db", rows=3)
    _write_pending(loop_dir, "probe.sql", "SELECT * FROM forecasts.t ORDER BY 1")

    rc = loop_guard.main(["run-queries", "--repo-root", str(repo), "--loop-dir", str(loop_dir)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["processed"] == ["probe.sql"]
    assert summary["errors"] == []

    result = json.loads((loop_dir / "queries" / "results" / "probe.json").read_text())
    assert result["error"] is None
    assert result["columns"] == ["id", "val"]
    assert result["row_count"] == 3
    assert result["rows"][0][1] == "v0"
    assert result["attached"] == ["forecasts"]


def test_run_queries_write_statement_recorded_as_error_and_db_unchanged(tmp_path, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    loop_dir = repo / "loop"
    db_path = repo / "state" / "zeus-forecasts.db"
    _make_db(db_path, rows=2)
    _write_pending(loop_dir, "bad.sql", "INSERT INTO forecasts.t (val) VALUES ('x')")

    rc = loop_guard.main(["run-queries", "--repo-root", str(repo), "--loop-dir", str(loop_dir)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["processed"] == ["bad.sql"]
    assert summary["errors"] and summary["errors"][0]["file"] == "bad.sql"

    result = json.loads((loop_dir / "queries" / "results" / "bad.json").read_text())
    assert result["error"] is not None

    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    conn.close()
    assert count == 2


def test_run_queries_attach_denied(tmp_path, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    loop_dir = repo / "loop"
    _make_db(repo / "state" / "zeus-forecasts.db", rows=1)
    evil_db = tmp_path / "evil.db"
    _write_pending(loop_dir, "evil.sql", f"ATTACH DATABASE '{evil_db}' AS evil")

    rc = loop_guard.main(["run-queries", "--repo-root", str(repo), "--loop-dir", str(loop_dir)])
    assert rc == 0
    result = json.loads((loop_dir / "queries" / "results" / "evil.json").read_text())
    assert result["error"] is not None
    assert not evil_db.exists()


def test_run_queries_row_cap_truncates(tmp_path, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    loop_dir = repo / "loop"
    _make_db(repo / "state" / "zeus-forecasts.db", rows=5)
    _write_pending(loop_dir, "probe.sql", "SELECT * FROM forecasts.t ORDER BY 1")

    rc = loop_guard.main(
        ["run-queries", "--repo-root", str(repo), "--loop-dir", str(loop_dir), "--max-rows", "3"]
    )
    assert rc == 0
    result = json.loads((loop_dir / "queries" / "results" / "probe.json").read_text())
    assert result["row_count"] == 3
    assert result["truncated"] is True


def test_run_queries_reruns_standing_query_and_overwrites_result(tmp_path, capsys):
    """No idempotent skip: standing orientation probes (e.g. "new
    settlements in last 3 days") must re-run every tick — a stale cached
    result would silently freeze the tick's view of the world. A one-shot
    probe is retired by the tick deleting its own pending/*.sql, not by
    this command caching results."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    loop_dir = repo / "loop"
    db_path = repo / "state" / "zeus-forecasts.db"
    _make_db(db_path, rows=2)
    _write_pending(loop_dir, "probe.sql", "SELECT * FROM forecasts.t ORDER BY 1")

    rc1 = loop_guard.main(["run-queries", "--repo-root", str(repo), "--loop-dir", str(loop_dir)])
    assert rc1 == 0
    result_path = loop_dir / "queries" / "results" / "probe.json"
    result1 = json.loads(result_path.read_text())
    assert result1["row_count"] == 2
    capsys.readouterr()

    # A new row lands between ticks (world moved) — the tick file itself is
    # untouched, but the re-run must reflect the new data, not a cache.
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO t (val) VALUES ('v2')")
    conn.commit()
    conn.close()

    rc2 = loop_guard.main(["run-queries", "--repo-root", str(repo), "--loop-dir", str(loop_dir)])
    assert rc2 == 0
    summary2 = json.loads(capsys.readouterr().out)
    assert summary2["processed"] == ["probe.sql"]
    assert "skipped" not in summary2

    result2 = json.loads(result_path.read_text())
    assert result2["row_count"] == 3


def test_run_queries_prunes_orphaned_results(tmp_path, capsys):
    """A results/*.json with no matching pending/<stem>.sql — its probe was
    retired, or it was planted directly into results/ — must be deleted,
    not left behind: results/ is fully wrapper-regenerated every
    invocation, so nothing a sandboxed tick writes there ever survives."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    loop_dir = repo / "loop"
    results_dir = loop_dir / "queries" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "old.json").write_text("{}\n")

    rc = loop_guard.main(["run-queries", "--repo-root", str(repo), "--loop-dir", str(loop_dir)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["pruned"] == ["old.json"]
    assert not (results_dir / "old.json").exists()


def test_run_queries_missing_canonical_db_is_not_fatal(tmp_path, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    loop_dir = repo / "loop"
    _make_db(repo / "state" / "zeus-forecasts.db", rows=1)
    _write_pending(loop_dir, "probe.sql", "SELECT * FROM forecasts.t")

    rc = loop_guard.main(["run-queries", "--repo-root", str(repo), "--loop-dir", str(loop_dir)])
    assert rc == 0
    result = json.loads((loop_dir / "queries" / "results" / "probe.json").read_text())
    assert result["error"] is None
    assert result["attached"] == ["forecasts"]


# --------------------------------------------------------------------------
# commit-auto: the sandboxed tick has no .git write access, so a trusted
# command commits new-this-tick allowlisted paths after `enforce` has run.
# Scope is the SAME --allowlist-git-ref as `enforce` — one source of truth,
# not a loop/**-only carve-out.
# --------------------------------------------------------------------------
def test_commit_auto_commits_allowlisted_paths_not_just_loop(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    # In-scope (docs/** is allowlisted, same as enforce) and out-of-scope.
    (repo / "docs" / "new_note.md").write_text("evidence\n")
    (repo / "src" / "other.py").write_text("y = 2\n")

    rc = _commit_auto(repo, snapshot, allowlist_ref, journal)
    assert rc == 0

    committed = _run_git(repo, "show", "--stat", "--name-only", "HEAD").stdout
    assert "docs/new_note.md" in committed
    assert "src/other.py" not in committed

    status = _run_git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    assert "src/other.py" in status  # co-tenant file left dirty, untouched


def test_commit_auto_noop_when_nothing_new(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"
    head_before = _base_sha(repo)

    rc = _commit_auto(repo, snapshot, allowlist_ref, journal)
    assert rc == 0
    assert not journal.exists()
    assert _base_sha(repo) == head_before


def test_commit_auto_excludes_guard_immutable_results(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    allowlist_ref = _allowlist_ref(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"

    results_dir = repo / "loop" / "queries" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "x.json").write_text("{}\n")

    rc = _commit_auto(repo, snapshot, allowlist_ref, journal)
    assert rc == 0
    assert not journal.exists()  # nothing eligible -> noop, not a violation report
    status = _run_git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    assert "loop/queries/results/x.json" in status  # left uncommitted


def test_commit_auto_bad_git_ref_fails_closed_commits_nothing(tmp_path):
    """Same fail-closed contract as enforce's --allowlist-git-ref: a bad ref
    yields zero patterns, so nothing new-this-tick can pass entry_allowed,
    and commit-auto must commit nothing rather than fall back to some
    implicit default scope."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    snapshot = _snapshot(repo)
    journal = repo / "loop" / "journal_out.md"
    head_before = _base_sha(repo)

    (repo / "docs" / "new_note.md").write_text("evidence\n")

    rc = _commit_auto(repo, snapshot, "0" * 40 + ":loop/allowlist_auto.txt", journal)
    assert rc == 0
    assert not journal.exists()
    assert _base_sha(repo) == head_before
    status = _run_git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    assert "docs/new_note.md" in status  # left uncommitted


def test_results_dir_is_guard_immutable():
    entry_results = loop_guard.DirtyEntry(" M", "loop/queries/results/x.json")
    entry_pending = loop_guard.DirtyEntry(" M", "loop/queries/pending/x.sql")
    assert loop_guard.is_guard_immutable(entry_results)
    assert not loop_guard.is_guard_immutable(entry_pending)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
