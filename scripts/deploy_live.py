#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator big-direction 2026-06-12 ("大方向现在也只是添加几个文件现在做") +
#   incident: a `launchctl kickstart` booted a concurrent agent's mid-edit working tree
#   into live money. This script makes daemon restarts SAFE by refusing to kickstart while
#   the LIVE checkout's runtime surface (src/ config/) is uncommitted or unpushed. It does
#   NOT open any DB. Registered in SQLITE_CONNECT_ALLOWLIST defensively (no connect today).
"""deploy_live — make live daemon restarts safe (deploy/dev split).

The Zeus daemons run from the LIVE main checkout at /Users/leofitz/zeus.
Restarting a daemon boots whatever is on disk THERE — so a kickstart while
that tree has uncommitted or unpushed runtime code ships half-finished work
into live money. This tool gates the restart.

COMMANDS
    deploy_live.py status
        Print HEAD sha, the live branch, whether HEAD is pushed, the dirty
        runtime files (src/ config/), and each daemon's pid + uptime.

    deploy_live.py restart <daemon|all>
        Kickstart one daemon (short label, e.g. "live-trading") or all of
        them. REFUSES when the live checkout's src/ or config/ has
        uncommitted changes, OR when HEAD != origin/<branch> (unpushed) —
        printing exactly what is dirty / unpushed. Pass --allow-dirty to
        bypass with a loud warning listing the dirty surface.

SAFETY
    Read-mostly: the only state-changing action is `launchctl kickstart`,
    and only after the clean-tree gate passes (or --allow-dirty is given).
    `status` never changes anything.

END-STATE (documented, NOT implemented here)
    The durable fix is per-worktree plist deploys: each deployable worktree
    gets its own `com.zeus.<daemon>` plist pointing at that worktree's
    interpreter + src tree, and `deploy_live.py promote <worktree>` atomically
    swaps the live plist symlink + kickstarts. That removes the shared-live-
    checkout hazard entirely (no tree is ever "the live one" mid-edit). It
    requires editing launchd plists, which is operator-level, so it is left
    as the documented end-state rather than built now.

USAGE
    .venv/bin/python scripts/deploy_live.py status
    .venv/bin/python scripts/deploy_live.py restart live-trading
    .venv/bin/python scripts/deploy_live.py restart all --allow-dirty
"""
from __future__ import annotations

import argparse
import subprocess
import sys

# The LIVE checkout the daemons boot from (NOT this worktree).
LIVE_REPO = "/Users/leofitz/zeus"

# launchd GUI domain for the operator user (gui/<uid>).
GUI_DOMAIN = "gui/501"

# Short label -> full launchd label. "all" expands to every entry here.
DAEMONS = {
    "data-ingest": "com.zeus.data-ingest",
    "forecast-live": "com.zeus.forecast-live",
    "riskguard-live": "com.zeus.riskguard-live",
    "live-trading": "com.zeus.live-trading",
    "venue-heartbeat": "com.zeus.venue-heartbeat",
    "heartbeat-sensor": "com.zeus.heartbeat-sensor",
}

# Runtime surface whose dirtiness must block a restart (per the incident).
RUNTIME_PATHSPECS = ["src/", "config/"]


def _git(*args: str, repo: str | None = None) -> subprocess.CompletedProcess:
    # Read LIVE_REPO at call time (not as a default-arg binding) so tests and
    # callers that point the gate at a different checkout are honored.
    return subprocess.run(
        ["git", "-C", repo or LIVE_REPO, *args],
        capture_output=True, text=True, timeout=20.0,
    )


def head_sha(short: bool = True) -> str:
    res = _git("rev-parse", "--short" if short else "HEAD", "HEAD")
    return res.stdout.strip() or "?"


def current_branch() -> str:
    res = _git("rev-parse", "--abbrev-ref", "HEAD")
    return res.stdout.strip() or "?"


def dirty_runtime_files() -> list[str]:
    """Lines from `git status --porcelain -- src/ config/` on the live repo."""
    res = _git("status", "--porcelain", "--", *RUNTIME_PATHSPECS)
    return [ln for ln in res.stdout.splitlines() if ln.strip()]


def unpushed_state(branch: str) -> tuple[bool, str]:
    """(is_unpushed, detail). True when HEAD != origin/<branch> or no upstream."""
    local = _git("rev-parse", "HEAD").stdout.strip()
    remote_res = _git("rev-parse", f"origin/{branch}")
    if remote_res.returncode != 0:
        return True, f"no origin/{branch} ref (never pushed)"
    remote = remote_res.stdout.strip()
    if local != remote:
        # Count how far ahead/behind for a clearer message.
        counts = _git("rev-list", "--left-right", "--count", f"origin/{branch}...HEAD")
        ahead_behind = counts.stdout.strip().replace("\t", " ")
        return True, f"HEAD {local[:9]} != origin/{branch} {remote[:9]} (behind/ahead: {ahead_behind})"
    return False, f"HEAD == origin/{branch} ({remote[:9]})"


def daemon_pid_uptime(label: str) -> tuple[str, str]:
    """(pid, status) for a launchd label, or ('-', '-') if not loaded."""
    res = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=8.0)
    for ln in res.stdout.splitlines():
        if label in ln:
            parts = ln.split("\t") if "\t" in ln else ln.split()
            if len(parts) >= 3:
                return parts[0], parts[1]
    return "-", "-"


def cmd_status(_args: argparse.Namespace) -> int:
    branch = current_branch()
    unpushed, push_detail = unpushed_state(branch)
    dirty = dirty_runtime_files()
    print(f"deploy_live status  (live checkout: {LIVE_REPO})")
    print("=" * 64)
    print(f"branch     : {branch}")
    print(f"HEAD       : {head_sha()}")
    print(f"push state : {'UNPUSHED — ' if unpushed else 'clean — '}{push_detail}")
    if dirty:
        print(f"dirty src/config ({len(dirty)} entries):")
        for ln in dirty:
            print(f"   {ln}")
    else:
        print("dirty src/config : (clean)")
    print("daemons:")
    for short, label in DAEMONS.items():
        pid, status = daemon_pid_uptime(label)
        print(f"   {short:<16} pid={pid:<8} last-status={status}")
    return 0


def _gate(allow_dirty: bool) -> tuple[bool, list[str]]:
    """Return (ok_to_restart, blockers). ok=False means refuse."""
    branch = current_branch()
    blockers: list[str] = []
    dirty = dirty_runtime_files()
    if dirty:
        blockers.append(f"{len(dirty)} uncommitted runtime file(s) in src/ config/:")
        blockers.extend(f"   {ln}" for ln in dirty)
    unpushed, push_detail = unpushed_state(branch)
    if unpushed:
        blockers.append(f"unpushed: {push_detail}")
    if blockers and not allow_dirty:
        return False, blockers
    return True, blockers


def cmd_restart(args: argparse.Namespace) -> int:
    target = args.daemon
    if target == "all":
        labels = list(DAEMONS.values())
    elif target in DAEMONS:
        labels = [DAEMONS[target]]
    else:
        print(f"unknown daemon '{target}'. known: {', '.join(DAEMONS)}, or 'all'", file=sys.stderr)
        return 2

    ok, blockers = _gate(args.allow_dirty)
    if not ok:
        print("REFUSING to restart — live runtime surface is not deploy-clean:")
        for b in blockers:
            print(f"  {b}")
        print("\nCommit + push the runtime changes, or pass --allow-dirty to override.")
        return 1
    if blockers and args.allow_dirty:
        print("!" * 64)
        print("WARNING --allow-dirty: restarting with a DIRTY / UNPUSHED live tree.")
        print("This boots uncommitted runtime code into LIVE money. Blockers:")
        for b in blockers:
            print(f"  {b}")
        print("!" * 64)

    rc_all = 0
    for label in labels:
        kick = subprocess.run(
            ["launchctl", "kickstart", "-k", f"{GUI_DOMAIN}/{label}"],
            capture_output=True, text=True, timeout=20.0,
        )
        if kick.returncode == 0:
            print(f"kickstarted {label}")
        else:
            rc_all = 1
            print(f"FAILED kickstart {label}: rc={kick.returncode} {kick.stderr.strip()}",
                  file=sys.stderr)
    return rc_all


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Make live daemon restarts safe (deploy/dev split).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="show HEAD/dirty/push state + daemon pids")
    p_status.set_defaults(func=cmd_status)

    p_restart = sub.add_parser("restart", help="kickstart a daemon (gated on clean live tree)")
    p_restart.add_argument("daemon", help="short daemon label or 'all'")
    p_restart.add_argument("--allow-dirty", action="store_true",
                           help="bypass the clean-tree gate (loud warning)")
    p_restart.set_defaults(func=cmd_restart)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
