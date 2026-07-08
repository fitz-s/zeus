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
#   enforce --repo-root DIR --allowlist PATH --pre-snapshot PATH
#            --journal PATH --tier {l1,l2}
#       computes new = current_dirty - pre_snapshot. If new is empty:
#       no-op, exit 0. If len(new) > 20 files or > 600 changed lines: hard-
#       restores ALL of `new`, appends one ESCALATION line, exit 2. Else:
#       hard-restores any path in `new` that does not match a glob in the
#       allowlist file, appends one VIOLATION line naming them, exit 1 if
#       any were restored else exit 0.
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
    """Resolve rel_path under repo_root; refuse anything that escapes it."""
    full = (repo_root / rel_path).resolve()
    try:
        full.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return full


def _delete(repo_root: Path, rel_path: str) -> None:
    full = _safe_target(repo_root, rel_path)
    if full is None:
        return
    if full.is_dir():
        import shutil

        shutil.rmtree(full, ignore_errors=True)
    elif full.exists() or full.is_symlink():
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
    ap.add_argument("--allowlist", required=True)
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

    patterns = load_allowlist(args.allowlist)
    violations = [e for e in new_entries if not path_allowed(e.path, patterns)]
    for e in violations:
        restore_entry(repo_root, e)

    if violations:
        detail = ", ".join(e.path for e in violations)
        append_journal(
            args.journal,
            f"VIOLATION: {args.tier} tick touched path(s) outside "
            f"loop/allowlist_auto.txt: {detail} — hard-restored to HEAD.",
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
