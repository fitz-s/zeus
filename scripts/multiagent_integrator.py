#!/usr/bin/env python3
# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: first-principles multi-agent worktree design 2026-06-29
#   (docs/operations/current/reports/multi_agent_worktree_orchestration_design_2026-06-29.md),
#   frontier-checked (ChatGPT Pro) against git ff-only / merge-base / status
#   semantics. Supersedes the per-branch live-main merge in agent_worktree_merge.py.
"""Deterministic single-writer integrator for parallel agent worktrees.

THE DESIGN, AS CODE. One constraint generates it: a live daemon runs from the
MAIN checkout, so MAIN must only ever fast-forward, atomically, to a commit whose
COMBINED tree has already passed validation, and exactly one writer may touch MAIN.

This tool is that one writer. It never runs inside an editor; the orchestrator
invokes it once per integration attempt with the editors' frozen tip OIDs. It:

  1. takes a single-writer lock (no two integrations race the MAIN ref);
  2. snapshots main0 = HEAD of the session branch and requires a clean MAIN tree;
  3. builds the COMBINED candidate OFF main, in a throwaway integration worktree,
     by replaying each reviewed exact tip OID in the given dependency order,
     confined to its declared pathspec;
  4. validates the combined candidate THERE (runs --test) — this is where
     A-green+B-green / A+B-red is caught, before MAIN moves;
  5. fast-forwards MAIN to the candidate ONLY IF MAIN still sits at main0 and is
     clean — otherwise it restages or aborts, never forcing a stale write.

Editors are pure producers: they commit in their own worktree and report a tip
OID. They do not run this tool and do not write MAIN. If a non-fast-forward or a
conflict appears, the candidate is blocked (MAIN untouched) and the offending unit
is returned for repair — a conflict means the split/dependency model was wrong.

Exit: 0 = MAIN advanced (or nothing to do). 2 = usage. 3 = blocked, MAIN
unchanged (dirty/moved main, scope violation, replay conflict, validation fail).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from fnmatch import fnmatch
from pathlib import Path


def _git(args: list[str], cwd: str, *, bypass: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if bypass:
        # The maintree_git_state_guard hook blocks state-changing git in the MAIN
        # tree; this sanctioned ff is the one exception (same posture as the
        # legacy agent_worktree_merge.py).
        env["MAINTREE_GIT_BYPASS"] = "1"
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True, text=True, env=env, check=check,
    )


def _out(args: list[str], cwd: str) -> str:
    return _git(args, cwd).stdout.strip()


def _blocked(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"INTEGRATION_BLOCKED: {msg} (MAIN unchanged)", file=sys.stderr)
    sys.exit(3)


class _Lock:
    """Single-writer lock via O_CREAT|O_EXCL on a lockfile in the git common dir."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "_Lock":
        deadline = time.monotonic() + 120
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(self.fd, f"{os.getpid()}\n".encode())
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    _blocked(f"another integrator holds {self.path}")
                time.sleep(0.5)

    def __exit__(self, *exc) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _require_clean(main: str) -> None:
    # The ff hazard for a live daemon is a TRACKED-file modification that the
    # merge would clobber. Untracked agent infra (.claude/worktrees/ — where this
    # tool's own staging worktrees live; gitignored in a real checkout) is not a
    # hazard and must not count as dirty.
    dirty = [
        ln for ln in _out(["status", "--porcelain=v1"], main).splitlines()
        if ln and not ln[3:].lstrip().startswith(".claude/")
    ]
    if dirty:
        _blocked("MAIN working tree is dirty: " + "; ".join(dirty[:5]))


def _parse_unit(spec: str) -> tuple[str, str, list[str]]:
    # name:tip_oid[:glob,glob,...]
    parts = spec.split(":")
    if len(parts) < 2:
        _blocked(f"bad --unit {spec!r}; expected name:tip_oid[:pathspec]")
    name, oid = parts[0], parts[1]
    globs = [g for g in (parts[2].split(",") if len(parts) > 2 and parts[2] else []) if g]
    return name, oid, globs


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic single-writer worktree integrator.")
    ap.add_argument("--run", required=True, help="run id (names the staging ref/worktree)")
    ap.add_argument("--unit", action="append", default=[], required=True,
                    help="name:tip_oid[:glob,glob] — reviewed frozen tip, in dependency order; repeatable")
    ap.add_argument("--test", required=True, help="validation command, run in the combined candidate worktree")
    ap.add_argument("--main", default=None, help="MAIN checkout path (default: repo root of this script)")
    ap.add_argument("--keep-staging", action="store_true", help="keep the integration worktree/branch on success")
    args = ap.parse_args()

    main_tree = str(Path(args.main).resolve()) if args.main else str(Path(__file__).resolve().parents[1])
    units = [_parse_unit(u) for u in args.unit]

    common = _out(["rev-parse", "--git-common-dir"], main_tree)
    common_dir = (Path(main_tree) / common).resolve() if not Path(common).is_absolute() else Path(common)
    lockfile = common_dir / "multiagent-integrator.lock"

    stage_branch = f"integration/{args.run}"
    stage_wt = Path(main_tree) / ".claude" / "worktrees" / f"_integration-{args.run}"

    with _Lock(lockfile):
        # 1. snapshot main0, require clean.
        _require_clean(main_tree)
        main0 = _out(["rev-parse", "HEAD"], main_tree)

        # 2. fresh staging worktree at main0 (linked worktree => exempt from maintree guards).
        if stage_wt.exists():
            _git(["worktree", "remove", "--force", str(stage_wt)], main_tree, check=False)
        _git(["branch", "-D", stage_branch], main_tree, check=False)
        try:
            _git(["worktree", "add", "-b", stage_branch, str(stage_wt), main0], main_tree)
        except subprocess.CalledProcessError as e:
            _blocked(f"could not create staging worktree: {e.stderr.strip()}")
        wt = str(stage_wt)

        try:
            # 3. replay each reviewed tip, in dependency order, path-confined.
            for name, oid, globs in units:
                if _git(["cat-file", "-e", f"{oid}^{{commit}}"], main_tree, check=False).returncode != 0:
                    _blocked(f"unit {name}: tip {oid} is not a commit in this repo")
                base = _out(["merge-base", main0, oid], main_tree)
                if globs:
                    changed = [p for p in _out(["diff", "--name-only", base, oid], main_tree).splitlines() if p]
                    stray = [p for p in changed if not any(fnmatch(p, g) for g in globs)]
                    if stray:
                        _blocked(f"unit {name}: changes outside its pathspec: {stray[:5]}")
                # replay base..oid onto the candidate; no source branch is moved.
                r = _git(["cherry-pick", "--allow-empty", f"{base}..{oid}"], wt, check=False)
                if r.returncode != 0:
                    _git(["cherry-pick", "--abort"], wt, check=False)
                    _blocked(f"unit {name}: replay conflict — split/dependency model was wrong\n{r.stderr.strip()}")

            # 4. validate the COMBINED candidate, OFF main.
            v = subprocess.run(args.test, shell=True, cwd=wt)
            if v.returncode != 0:
                _blocked(f"combined validation failed (exit {v.returncode}); staging kept at {stage_branch}")

            # 5. final conditional ff: MAIN must still be at main0 and clean.
            _require_clean(main_tree)
            if _out(["rev-parse", "HEAD"], main_tree) != main0:
                _blocked(f"MAIN moved during staging (was {main0[:12]}); discard candidate and restage")
            cand = _out(["rev-parse", "HEAD"], wt)
            if _git(["merge-base", "--is-ancestor", main0, cand], main_tree, check=False).returncode != 0:
                _blocked("candidate is not a fast-forward of main0")
            ff = _git(["merge", "--ff-only", cand], main_tree, bypass=True, check=False)
            if ff.returncode != 0:
                _blocked(f"final --ff-only refused: {ff.stderr.strip()}")

            print(f"INTEGRATED: MAIN {main0[:12]} -> {cand[:12]} "
                  f"({len(units)} unit(s): {', '.join(n for n, _, _ in units)})")
        finally:
            if not args.keep_staging:
                _git(["worktree", "remove", "--force", str(stage_wt)], main_tree, check=False)
                _git(["branch", "-D", stage_branch], main_tree, check=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
