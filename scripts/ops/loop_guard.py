#!/usr/bin/env python3
# Created: 2026-07-08
# Last reused/audited: 2026-07-08
# Authority: docs/operations/current/plans/allday_improvement_loop_design_2026-07-06.md
#   §3 (three-tier wrapper mechanism, adopted consult BLOCKER-1/HIGH items) +
#   docs/rebuild/EXECUTION_MASTER_2026-07-07.md §C (deploy operator-only,
#   never touch dirty files, never stash).
#
# WHAT: the testable core of loop/tick.sh and loop/daily.sh. Pure Python so
#   the safety-critical logic (allowlist enforcement, quarantine restore,
#   diff circuit breaker, HALT check, non-blocking single-flight lock) has
#   unit test coverage instead of living only in shell. The wrapper scripts
#   stay thin: they call this CLI and act on its exit code.
#
# WHO WRITES loop/JOURNAL.md THROUGH THIS FILE: the `enforce` and
#   `fallback-entry` subcommands append mechanical VIOLATION/ESCALATION/
#   FALLBACK lines. The substantive "did work" journal entry for a normal
#   tick is written by the invoked `claude -p` run itself (per
#   loop/prompts/l1.md and loop/prompts/l2.md REPORT contract), not by this
#   script.
#
# WHAT BREAKS IF THIS FILE SILENTLY STOPS WORKING: tick.sh/daily.sh call
#   every subcommand with `set -e`-adjacent guards, so a crash here either
#   aborts the tick (halt-check/snapshot) or is swallowed with `|| true`
#   (enforce, so a bug in the guard never blocks a legitimate journal
#   append) — see the wrapper scripts for exactly which calls are best-effort.
#   A real regression here would silently stop enforcing the AUTO allowlist,
#   which is why tests/test_loop_guard.py exists.
#
# SUBCOMMANDS:
#   halt-check --loop-dir DIR
#       exit 0 if loop/HALT does not exist (ok to proceed), exit 3 if it does.
#   snapshot --repo-root DIR --out PATH
#       writes the current set of dirty/untracked paths (git status
#       --porcelain) to PATH, one per line. Call BEFORE invoking claude so
#       the post-run enforce only ever evaluates paths that changed DURING
#       this tick — operator-dirty files present before the tick started
#       are never touched, restored, or reported.
#   enforce --repo-root DIR --allowlist-snapshot PATH --pre-snapshot PATH
#            --journal PATH --tier {l1,l2}
#       computes new = current_dirty - pre_snapshot. If new is empty:
#       no-op, exit 0. If len(new) > 20 files or > 600 changed lines: hard-
#       restores ALL of `new`, appends one ESCALATION line, exit 2. Else:
#       hard-restores any entry that (a) touches a guard-immutable path
#       (loop/allowlist_auto.txt, loop/tick.sh, loop/daily.sh,
#       loop/prompts/** — checked unconditionally, before the allowlist, so
#       a tick can never widen its own scope by editing these) or (b) has
#       any side (both old and new path, for a rename/copy — checking only
#       the new path would let a tick launder a file out of scope by
#       renaming it into an allowed dir) that fails the FROZEN
#       --allowlist-snapshot. Appends one VIOLATION line naming them, exit 1
#       if any were restored else exit 0. --allowlist-snapshot MUST be a
#       copy taken before claude ran — see that flag's --help text.
#   db-sentinel-snapshot --repo-root DIR --out PATH
#       records (mtime, size) for every state/**/*.db* file (recursive) plus
#       repo-root *.db* decoys, via lstat (symlink-safe). Call BEFORE
#       invoking claude, alongside `snapshot`.
#   db-sentinel-check --repo-root DIR --pre-snapshot PATH --journal PATH
#            --loop-dir DIR --tier {l1,l2}
#       re-scans and compares against the pre-snapshot. *.db/*.db-wal/
#       *.db-shm/*.db-journal are globally gitignored, so `enforce` above
#       (which is entirely git-status-driven) is structurally blind to any
#       DB write — this is the separate mechanical backstop for that. Any
#       delta (added/removed/changed) appends one ESCALATION line AND
#       writes loop/HALT (self-halt — a DB write cannot be hard-restored
#       byte-for-byte the way a tracked file can, so the safe response is
#       to stop, not repair). Never opens or diffs DB content.
#   fallback-entry --journal PATH --tier {l1,l2} --reason TEXT
#       appends a mechanical FALLBACK journal line — used when the invoked
#       claude run exited non-zero, so the journal never silently stops
#       growing even on a hard crash.
#   flock-run --lock-file PATH -- <command...>
#       acquires a non-blocking exclusive lock on PATH (Python fcntl.flock,
#       not the `flock` CLI — not reliably present on stock macOS) and runs
#       <command...> for its duration. If the lock is already held, prints
#       LOCK_BUSY to stderr and exits 75 (EX_TEMPFAIL) WITHOUT running the
#       command — this is the single-flight overlap guard.
"""Testable core for loop/tick.sh and loop/daily.sh (24/7 improvement loop v2)."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - posix-only tool, documented assumption
    fcntl = None  # type: ignore[assignment]

CIRCUIT_BREAKER_MAX_FILES = 20
CIRCUIT_BREAKER_MAX_LINES = 600
LOCK_BUSY_EXIT_CODE = 75


# ---------------------------------------------------------------------------
# git status parsing + hard restore
# ---------------------------------------------------------------------------
@dataclass
class DirtyEntry:
    code: str  # 2-char git porcelain status code, e.g. " M", "??", "A ", "R "
    path: str  # current (new, for renames) path, relative to repo root
    orig_path: str | None = None  # original path for renames/copies


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def parse_status(repo_root: Path) -> list[DirtyEntry]:
    out = _git(repo_root, "status", "--porcelain=v1", "--untracked-files=all").stdout
    entries: list[DirtyEntry] = []
    for line in out.splitlines():
        if not line:
            continue
        code = line[:2]
        rest = line[3:]
        if code[0] in ("R", "C") and " -> " in rest:
            old, new = rest.split(" -> ", 1)
            entries.append(DirtyEntry(code, new, old))
        else:
            entries.append(DirtyEntry(code, rest))
    return entries


def dirty_paths(repo_root: Path) -> set[str]:
    return {e.path for e in parse_status(repo_root)}


def _safe_target(repo_root: Path, rel_path: str) -> Path | None:
    """Resolve rel_path under repo_root WITHOUT following a symlink at the
    final path component.

    A violating path can itself be a symlink (e.g. planted by a tick
    pointing at loop/JOURNAL.md). `Path.resolve()` on the FULL path follows
    that symlink to its target — deleting the resolved path then destroys
    the target's content while the symlink itself survives untouched
    (audit-trail wipe). Only the parent directory chain is resolved here
    (to catch '..' traversal); the leaf component is left exactly as named
    so callers can lstat/unlink the link itself, never its target.
    """
    repo_real = repo_root.resolve()
    full = repo_root / rel_path
    try:
        parent_real = full.parent.resolve()
    except OSError:
        return None
    try:
        parent_real.relative_to(repo_real)
    except ValueError:
        return None
    return parent_real / full.name


def _delete(repo_root: Path, rel_path: str) -> None:
    full = _safe_target(repo_root, rel_path)
    if full is None:
        return
    # Check symlink-ness FIRST via os.path.islink (lstat-based, does not
    # follow) — Path.is_dir()/.exists() both stat() (follow symlinks) and
    # would misclassify a symlink pointing at a directory/file elsewhere.
    if os.path.islink(full):
        os.unlink(full)  # removes the link itself, never the target
    elif full.is_dir():
        import shutil

        shutil.rmtree(full, ignore_errors=True)
    elif full.exists():
        full.unlink()
    # Defensive: drop any lingering index entry (no-op if nothing staged).
    _git(repo_root, "restore", "--staged", "--", rel_path)


def restore_entry(repo_root: Path, entry: DirtyEntry) -> None:
    """Hard-restore a single out-of-scope change to its pre-tick state."""
    idx, wt = entry.code[0], entry.code[1]
    if entry.code == "??":
        _delete(repo_root, entry.path)
        return
    if idx == "A":
        # Staged add with no HEAD blob (possibly further worktree-modified
        # too) — unstage then delete, there is nothing to restore TO.
        _git(repo_root, "restore", "--staged", "--", entry.path)
        _delete(repo_root, entry.path)
        return
    if idx in ("R", "C"):
        # Staged rename/copy: bring the original path back from HEAD, drop
        # the new path.
        if entry.orig_path:
            _git(repo_root, "restore", "--staged", "--worktree", "--source=HEAD", "--", entry.orig_path)
        if entry.path != entry.orig_path:
            _delete(repo_root, entry.path)
        return
    # Default: tracked modify/delete (staged and/or worktree) -> hard reset
    # both index and worktree to HEAD's version.
    _git(repo_root, "restore", "--staged", "--worktree", "--source=HEAD", "--", entry.path)


def new_file_line_count(repo_root: Path, entries: list[DirtyEntry]) -> int:
    total = 0
    for e in entries:
        if e.code == "??":
            full = _safe_target(repo_root, e.path)
            if full and full.is_file():
                try:
                    with open(full, "rb") as fh:
                        total += sum(1 for _ in fh)
                except OSError:
                    pass
            continue
        out = _git(repo_root, "diff", "--numstat", "HEAD", "--", e.path).stdout.strip()
        if not out:
            continue
        # numstat prints "added\tremoved\tpath" (added/removed may be '-' for binary)
        parts = out.split("\t")
        if len(parts) < 2:
            continue
        added = int(parts[0]) if parts[0].isdigit() else 0
        removed = int(parts[1]) if parts[1].isdigit() else 0
        total += added + removed
    return total


# ---------------------------------------------------------------------------
# allowlist
# ---------------------------------------------------------------------------
def load_allowlist_from_lines(lines: list[str]) -> list[str]:
    patterns = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def load_allowlist(path: str | Path) -> list[str]:
    return load_allowlist_from_lines(Path(path).read_text(encoding="utf-8").splitlines())


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a path glob into a regex.

    Deliberately NOT stdlib fnmatch: fnmatch's '*' matches '/' too, which
    would make `architecture/*.yaml` (registry files only, non-recursive —
    design doc §3 AUTO row) wrongly match `architecture/sub/deep.yaml`.
    Here `**` matches any depth (including zero extra segments) and a bare
    `*`/`?` matches within a single path segment only.
    """
    i, n, out = 0, len(pattern), []
    while i < n:
        if pattern[i : i + 2] == "**":
            out.append(r".*")
            i += 2
        elif pattern[i] == "*":
            out.append(r"[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def path_allowed(path: str, patterns: list[str]) -> bool:
    posix = path.replace(os.sep, "/")
    return any(_glob_to_regex(pat).match(posix) for pat in patterns)


# Files that govern the guard mechanism itself. Even though these sit under
# loop/** (which is otherwise AUTO-allowed), a tick must NEVER be able to
# widen its own scope by editing them — self-widening the allowlist mid-tick,
# or editing the wrapper scripts / prompts that decide what runs next tick,
# is always a violation regardless of what the (possibly-tampered) allowlist
# says. This is why `cmd_enforce` checks this list BEFORE consulting the
# frozen allowlist snapshot at all.
GUARD_IMMUTABLE_PATTERNS = [
    "loop/allowlist_auto.txt",
    "loop/tick.sh",
    "loop/daily.sh",
    "loop/prompts/**",
]


def _entry_paths(e: DirtyEntry) -> list[str]:
    """All path(s) a dirty entry touches — both sides for a rename/copy.

    Checking only the new path lets a tick launder an out-of-scope file by
    renaming it into an allowed directory (e.g. `git mv src/riskguard.py
    docs/stolen.py` — the new path matches docs/** while the money-path
    original silently vanishes). Both sides must be considered together.
    """
    if e.orig_path and e.orig_path != e.path:
        return [e.path, e.orig_path]
    return [e.path]


def is_guard_immutable(e: DirtyEntry) -> bool:
    return any(path_allowed(p, GUARD_IMMUTABLE_PATTERNS) for p in _entry_paths(e))


def entry_allowed(e: DirtyEntry, patterns: list[str]) -> bool:
    """An entry is allowed only if EVERY path it touches is allowlisted —
    both sides of a rename must independently pass (see _entry_paths)."""
    return all(path_allowed(p, patterns) for p in _entry_paths(e))


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------
def append_journal(journal_path: str | Path, text: str) -> None:
    p = Path(journal_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(text.rstrip("\n") + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# DB mtime/size sentinel
# ---------------------------------------------------------------------------
# *.db, *.db-wal, *.db-shm, *.db-journal are ALL globally gitignored
# (.gitignore lines ~79-90) — git status never sees a write to any of them,
# so the allowlist/quarantine mechanism above is structurally blind to the
# one class of file the prompts most emphasize never touching. This sentinel
# is a separate, git-independent check: record (mtime, size) for every DB
# file under state/ (recursive) plus repo-root decoy *.db* files (the same
# scope db_hygiene.sh treats as suspect) before invoking claude, and diff
# after. Any delta — added, removed, or mtime/size changed — self-halts the
# loop (touches loop/HALT) rather than merely logging, because unlike a
# docs/tests file a DB write cannot be hard-restored byte-for-byte the way
# git restore can undo a tracked file; the safe response is to stop and let
# the operator look, not attempt a repair. Deliberately mtime/size only —
# never opens or diffs DB content.
DB_SENTINEL_GLOBS = ("*.db", "*.db-wal", "*.db-shm", "*.db-journal")


def _db_sentinel_paths(repo_root: Path) -> list[Path]:
    paths: set[Path] = set()
    state_dir = repo_root / "state"
    if state_dir.is_dir():
        for pat in DB_SENTINEL_GLOBS:
            paths.update(state_dir.rglob(pat))
    # Repo-root decoys (db_hygiene.sh precedent: K1 canon puts every live DB
    # under state/, so a *.db* file sitting at the repo root is suspect).
    for pat in DB_SENTINEL_GLOBS:
        paths.update(repo_root.glob(pat))
    return sorted(paths)


def _db_sentinel_state(repo_root: Path) -> dict[str, list[int]]:
    state: dict[str, list[int]] = {}
    for p in _db_sentinel_paths(repo_root):
        try:
            st = os.lstat(p)  # lstat: never follow a symlink to its target
        except OSError:
            continue
        rel = str(p.relative_to(repo_root))
        state[rel] = [int(st.st_mtime), int(st.st_size)]
    return state


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------
def cmd_halt_check(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="loop_guard.py halt-check")
    ap.add_argument("--loop-dir", required=True)
    args = ap.parse_args(argv)
    halt = Path(args.loop_dir) / "HALT"
    if halt.exists():
        print("HALTED")
        return 3
    print("OK")
    return 0


def cmd_snapshot(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="loop_guard.py snapshot")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    paths = sorted(dirty_paths(repo_root))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(paths) + ("\n" if paths else ""), encoding="utf-8")
    return 0


def cmd_enforce(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="loop_guard.py enforce")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument(
        "--allowlist-snapshot",
        required=True,
        help=(
            "Path to a FROZEN copy of loop/allowlist_auto.txt taken BEFORE "
            "claude was invoked (tick.sh: `cp` before the run). Never pass "
            "the live loop/allowlist_auto.txt here — a tick could append "
            "'src/**' to the live file (loop/** is otherwise AUTO-allowed) "
            "and then edit src/ in the same tick; reading the live file at "
            "enforce time would see both as allowed with zero trace."
        ),
    )
    ap.add_argument("--pre-snapshot", required=True)
    ap.add_argument("--journal", required=True)
    ap.add_argument("--tier", required=True, choices=["l1", "l2"])
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    pre_path = Path(args.pre_snapshot)
    pre = set()
    if pre_path.exists():
        pre = {line for line in pre_path.read_text(encoding="utf-8").splitlines() if line}

    entries = parse_status(repo_root)
    new_entries = [e for e in entries if e.path not in pre]

    if not new_entries:
        print(json.dumps({"new_files": [], "violations": [], "escalated": False}))
        return 0

    file_count = len(new_entries)
    line_count = new_file_line_count(repo_root, new_entries)

    if file_count > CIRCUIT_BREAKER_MAX_FILES or line_count > CIRCUIT_BREAKER_MAX_LINES:
        for e in new_entries:
            restore_entry(repo_root, e)
        append_journal(
            args.journal,
            f"ESCALATION: {args.tier} tick diff circuit-breaker tripped "
            f"({file_count} files, {line_count} lines; limit "
            f"{CIRCUIT_BREAKER_MAX_FILES} files / {CIRCUIT_BREAKER_MAX_LINES} lines) "
            f"— all {file_count} new-this-tick change(s) hard-restored to HEAD.",
        )
        print(
            json.dumps(
                {
                    "new_files": [e.path for e in new_entries],
                    "violations": [],
                    "escalated": True,
                    "file_count": file_count,
                    "line_count": line_count,
                }
            )
        )
        return 2

    # Load the FROZEN pre-tick allowlist snapshot — never the live file (see
    # --allowlist-snapshot help above; this is the fix for the self-widening
    # escape). A missing/unparseable snapshot fails CLOSED here: an empty
    # pattern list allows nothing, so every new entry becomes a violation
    # and gets restored — safer than silently allowing everything.
    try:
        patterns = load_allowlist(args.allowlist_snapshot)
    except OSError:
        patterns = []

    violations = []
    for e in new_entries:
        if is_guard_immutable(e) or not entry_allowed(e, patterns):
            violations.append(e)
    for e in violations:
        restore_entry(repo_root, e)

    if violations:
        detail_parts = []
        for e in violations:
            tag = " [guard-immutable]" if is_guard_immutable(e) else ""
            if e.orig_path and e.orig_path != e.path:
                detail_parts.append(f"{e.orig_path} -> {e.path}{tag}")
            else:
                detail_parts.append(f"{e.path}{tag}")
        detail = ", ".join(detail_parts)
        append_journal(
            args.journal,
            f"VIOLATION: {args.tier} tick touched path(s) outside "
            f"loop/allowlist_auto.txt (or the guard's own immutable files): "
            f"{detail} — hard-restored to HEAD.",
        )

    print(
        json.dumps(
            {
                "new_files": [e.path for e in new_entries],
                "violations": [e.path for e in violations],
                "escalated": False,
                "file_count": file_count,
                "line_count": line_count,
            }
        )
    )
    return 1 if violations else 0


def cmd_db_sentinel_snapshot(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="loop_guard.py db-sentinel-snapshot")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    state = _db_sentinel_state(repo_root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    return 0


def cmd_db_sentinel_check(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="loop_guard.py db-sentinel-check")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--pre-snapshot", required=True)
    ap.add_argument("--journal", required=True)
    ap.add_argument("--loop-dir", required=True)
    ap.add_argument("--tier", required=True, choices=["l1", "l2"])
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    pre_path = Path(args.pre_snapshot)
    pre: dict[str, list[int]] = {}
    if pre_path.exists():
        try:
            pre = json.loads(pre_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pre = {}

    cur = _db_sentinel_state(repo_root)

    if cur == pre:
        print(json.dumps({"delta": False}))
        return 0

    added = sorted(set(cur) - set(pre))
    removed = sorted(set(pre) - set(cur))
    changed = sorted(k for k in (set(cur) & set(pre)) if cur[k] != pre[k])

    parts = []
    if added:
        parts.append(f"added={added}")
    if removed:
        parts.append(f"removed={removed}")
    if changed:
        parts.append(f"changed={changed}")
    detail = "; ".join(parts)

    append_journal(
        args.journal,
        f"ESCALATION: {args.tier} tick touched a state/**.db* file — DB "
        f"writes are outside git's visibility (*.db/*.db-wal/*.db-shm/"
        f"*.db-journal are globally gitignored), so this sentinel is the "
        f"only mechanical backstop for it: {detail}. Loop self-halted "
        f"(loop/HALT written) — operator must investigate before the next "
        f"tick runs.",
    )
    halt_path = Path(args.loop_dir) / "HALT"
    halt_path.parent.mkdir(parents=True, exist_ok=True)
    halt_path.write_text(
        f"AUTO-HALT ({_now_iso()}): {args.tier} tick DB-sentinel delta detected — {detail}\n",
        encoding="utf-8",
    )

    print(json.dumps({"delta": True, "added": added, "removed": removed, "changed": changed}))
    return 2


def cmd_fallback_entry(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="loop_guard.py fallback-entry")
    ap.add_argument("--journal", required=True)
    ap.add_argument("--tier", required=True, choices=["l1", "l2"])
    ap.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    append_journal(
        args.journal,
        f"## {_now_iso()} {args.tier.upper()} tick — FALLBACK (no journal entry from the run)\n"
        f"reason: {args.reason}",
    )
    return 0


def cmd_flock_run(argv: list[str]) -> int:
    if fcntl is None:  # pragma: no cover
        print("flock-run: fcntl unavailable on this platform", file=sys.stderr)
        return 70
    if "--" not in argv:
        print("usage: loop_guard.py flock-run --lock-file PATH -- <command...>", file=sys.stderr)
        return 64
    sep = argv.index("--")
    head, command = argv[:sep], argv[sep + 1 :]
    ap = argparse.ArgumentParser(prog="loop_guard.py flock-run")
    ap.add_argument("--lock-file", required=True)
    args = ap.parse_args(head)
    if not command:
        print("flock-run: empty command after --", file=sys.stderr)
        return 64

    lock_path = Path(args.lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        print("LOCK_BUSY: another tick is already running", file=sys.stderr)
        return LOCK_BUSY_EXIT_CODE
    try:
        proc = subprocess.run(command)
        return proc.returncode
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


COMMANDS = {
    "halt-check": cmd_halt_check,
    "snapshot": cmd_snapshot,
    "enforce": cmd_enforce,
    "db-sentinel-snapshot": cmd_db_sentinel_snapshot,
    "db-sentinel-check": cmd_db_sentinel_check,
    "fallback-entry": cmd_fallback_entry,
    "flock-run": cmd_flock_run,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: loop_guard.py {{{','.join(COMMANDS)}}} ...", file=sys.stderr)
        return 64
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main())
